#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Execute documented zero-copy FFmpeg recipes verbatim on hardware.

Reads docs/usage/ffmpeg.md, parses recipes marked with <!-- recipe-run: <id> -->
in the sections linked by the zero-copy boundary table, substitutes runtime
paths, and verifies hardware pipeline assertions:
  1. Exit code 0.
  2. No hwdownload in log (VRAM-to-VRAM or map zero-copy).
  3. No auto-inserted scale filter that touches hardware frames (a CPU
     conversion the recipe's own format= requests before hwupload is allowed;
     an auto scale whose formats the log does not show fails closed).
  4. Every SPEC expected log needle is present.
  5. Decoded output has exactly 120 frames.

Static checking and self-test:
  doc-recipes-smoke.py --check-doc
  doc-recipes-smoke.py --self-test
"""

import contextlib
import importlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# Reuse check-doc-recipes parsing helpers (HISS-19)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
_cdr = importlib.import_module("check-doc-recipes")

split_blocks = _cdr.split_blocks
parse_block = _cdr.parse_block
strip_comment = _cdr.strip_comment
is_ffmpeg_start = _cdr.is_ffmpeg_start


# Every ffmpeg call carries a deadline, so a hung driver or encoder fails the
# run instead of blocking the lane (HISS-02).
PROBE_TIMEOUT_S = 60
RUN_TIMEOUT_S = 600


def ffmpeg_argv(ffmpeg_bin):
    """Command prefix that starts ffmpeg_bin.

    A .py file (the self-test stub) runs through this interpreter, because
    Windows cannot start a script by its shebang (WinError 193).
    """
    if str(ffmpeg_bin).endswith(".py"):
        return [sys.executable, str(ffmpeg_bin)]
    return [str(ffmpeg_bin)]


def run_ffmpeg(argv, timeout_s, env=None, merge_stderr=True):
    """Run one ffmpeg command without a shell. Return a CompletedProcess.

    argv[0] must name an existing file. A run past timeout_s returns exit code
    124 with the deadline in its output, so every caller treats it as a failure.
    """
    if not Path(argv[0]).is_file():
        raise FileNotFoundError(f"ffmpeg binary not found: {argv[0]}")
    try:
        return subprocess.run(
            argv,
            check=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        msg = f"timed out after {timeout_s} s"
        return subprocess.CompletedProcess(argv, 124, msg, "" if merge_stderr else msg)


DOC_PATH = REPO_ROOT / "docs" / "usage" / "ffmpeg.md"

MARKER_RE = re.compile(r"<!--\s*recipe-run:\s*([^\s>]+)(?:\s+(.*?))?\s*-->")

SPEC = {
    "nvenc-nvdec-derived": {
        "section": "nvenc-nvdec--vulkan-filters--nvenc",
        "device_class": "cuda",
        "input_kind": "8bit",
        "filters_default": "pelorus_deband_vulkan",
        "expected_encoder": "hevc_nvenc",
        "needles": [
            "pixfmt:cuda",
            "Transferred CUDA image to Vulkan!",
            "Transferred Vulkan image to CUDA!",
            "Using input frames context (format cuda) with hevc_nvenc encoder",
        ],
        "variants": None,
    },
    "nvenc-swdec-separate": {
        "section": "nvenc-nvdec--vulkan-filters--nvenc",
        "device_class": "cuda",
        "input_kind": "8bit",
        "filters_default": "pelorus_deband_vulkan",
        "expected_encoder": "hevc_nvenc",
        "needles": [
            "Using input frames context (format cuda) with hevc_nvenc encoder",
        ],
        "variants": None,
    },
    "nvenc-swdec-p010": {
        "section": "nvenc-nvdec--vulkan-filters--nvenc",
        "device_class": "cuda",
        "input_kind": "10bit",
        "filters_default": "pelorus_deband_vulkan",
        "expected_encoder": "hevc_nvenc",
        "needles": [
            "Using input frames context (format cuda) with hevc_nvenc encoder",
        ],
        "variants": None,
    },
    "vulkan-hevc": {
        "section": "full-zero-copy-vulkan-decode--filters--vulkan-hw-encode",
        "device_class": "vulkan-hevc-encode",
        "input_kind": "8bit",
        "filters_default": "pelorus_deband_vulkan=range=15:thry=0.012",
        "expected_encoder": "hevc_vulkan",
        "needles": [
            "Using input frames context (format vulkan) with hevc_vulkan encoder",
        ],
        "variants": None,
    },
    "vaapi-h264": {
        "section": "zero-copy-into-vaapi-and-qsv-encoders-linux",
        "device_class": "vaapi",
        "input_kind": "8bit",
        "filters_default": "pelorus_deband_vulkan=tiling=drm",
        "expected_encoder": "h264_vaapi",
        "needles": [
            "tiling=drm",
            "Using input frames context (format vaapi) with h264_vaapi encoder",
        ],
        "variants": [
            {
                "name": "intel",
                "driver": "iHD",
                "vendor": "0x8086",
                "node_env": "INTEL_RENDER_NODE",
            },
            {
                "name": "amd",
                "driver": "radeonsi",
                "vendor": "0x1002",
                "node_env": "AMD_RENDER_NODE",
            },
        ],
    },
    "qsv-hevc": {
        "section": "zero-copy-into-vaapi-and-qsv-encoders-linux",
        "device_class": "qsv",
        "input_kind": "8bit",
        "filters_default": "pelorus_deband_vulkan=tiling=drm",
        "expected_encoder": "hevc_qsv",
        "needles": [
            "tiling=drm",
            "Using input frames context (format qsv) with hevc_qsv encoder",
        ],
        "variants": [
            {
                "name": "intel",
                "driver": "iHD",
                "vendor": "0x8086",
                "node_env": "INTEL_RENDER_NODE",
            },
        ],
    },
}


def heading_to_anchor(title):
    title = title.replace("`", "")
    title = "".join(c for c in title if c.isascii())
    cleaned = "".join(
        c if (c.isalnum() or c in (" ", "-", "_")) else "" for c in title.lower()
    )
    return cleaned.replace(" ", "-")


def extract_boundary_anchors(text):
    """Return anchor set linked by the Section column in the zero-copy boundary table."""
    anchors = set()
    in_table = False
    for line in text.splitlines():
        if "| Encoder |" in line:
            in_table = True
            continue
        if in_table:
            if not line.strip().startswith("|"):
                break
            m = re.search(r"\[.*?\]\(#([^)]+)\)", line)
            if m:
                anchors.add(m.group(1))
    return anchors


def collect_marked_recipes(text):
    """Return dict of {recipe_id: (marker_line_no, fence_line_no, command_str, nos)}."""
    lines = text.splitlines()
    recipes = {}
    for idx, line in enumerate(lines):
        m = MARKER_RE.match(line.strip())
        if not m:
            continue
        raw_id = m.group(1)
        if raw_id.startswith("none:") or raw_id == "none":
            continue
        # Find next non-blank line
        k = idx + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k >= len(lines) or not lines[k].startswith("```"):
            continue
        end = k + 1
        while end < len(lines) and not lines[end].startswith("```"):
            end += 1
        _comments, commands = parse_block(lines, k + 1, end)
        if commands:
            _first_no, cmd, nos = commands[0]
            # keep the first: check_doc refuses a duplicate id
            recipes.setdefault(raw_id, (idx + 1, k + 1, cmd, nos))
    return recipes


def _fence_end(lines, start):
    """Index of the closing fence line for a fence opened before `start`."""
    end = start
    while end < len(lines) and not lines[end].startswith("```"):
        end += 1
    return end


def _marker_findings(path, lines, i, marker_id, seen_markers, anchor=None):
    """Findings for one recipe-run marker at line index i under section anchor."""
    findings = []
    k = i + 1
    while k < len(lines) and not lines[k].strip():
        k += 1
    if k >= len(lines) or not lines[k].startswith("```"):
        findings.append(
            (path, i + 1, "recipe-run marker is not followed by a code fence")
        )
    elif not parse_block(lines, k + 1, _fence_end(lines, k + 1))[1]:
        findings.append(
            (path, i + 1, "recipe-run marker fence contains no ffmpeg command")
        )
    if marker_id != "none" and not marker_id.startswith("none:"):
        if marker_id in seen_markers:
            findings.append((path, i + 1, f"duplicate recipe-run id '{marker_id}'"))
        if marker_id in SPEC:
            seen_markers.add(marker_id)
            if anchor != SPEC[marker_id]["section"]:
                msg = (
                    f"recipe-run id '{marker_id}' sits in section '{anchor}', "
                    f"its boundary-table section is '{SPEC[marker_id]['section']}'"
                )
                findings.append((path, i + 1, msg))
        else:
            findings.append((path, i + 1, f"unknown recipe-run id '{marker_id}'"))
    return findings


def _unmarked_fence_finding(path, lines, fence_start, anchor):
    """Finding when an ffmpeg fence in a boundary section lacks a marker, else None."""
    if not parse_block(lines, fence_start + 1, _fence_end(lines, fence_start + 1))[1]:
        return None
    prev = fence_start - 1
    while prev >= 0 and not lines[prev].strip():
        prev -= 1
    if prev >= 0 and MARKER_RE.match(lines[prev].strip()):
        return None
    msg = f"ffmpeg fence in boundary section '{anchor}' has no preceding recipe-run marker"
    return (path, fence_start + 1, msg)


def check_doc(path, text):
    """Static gate for docs/usage/ffmpeg.md: exit 0 clean, 1 findings."""
    lines = text.splitlines()
    findings = []
    boundary_anchors = extract_boundary_anchors(text)
    cur_anchor = None
    in_fence = False
    seen_markers = set()
    for i, line in enumerate(lines):
        if line.startswith("## ") and not in_fence:
            cur_anchor = heading_to_anchor(line[3:].strip())
            continue
        m = MARKER_RE.match(line.strip())
        if m:
            findings += _marker_findings(
                path, lines, i, m.group(1), seen_markers, cur_anchor
            )
        # Track fence state so a closing fence is never read as an opening one
        opening = False
        if line.startswith("```"):
            in_fence = not in_fence
            opening = in_fence
        if opening and cur_anchor in boundary_anchors:
            finding = _unmarked_fence_finding(path, lines, i, cur_anchor)
            if finding:
                findings.append(finding)
    for spec_id in sorted(SPEC):
        if SPEC[spec_id]["section"] not in boundary_anchors:
            msg = f"SPEC id '{spec_id}' section is not linked by the boundary table"
            findings.append((path, 1, msg))
        if spec_id not in seen_markers:
            findings.append((path, 1, f"SPEC id '{spec_id}' has no marker in doc"))
    for p, line_no, msg in findings:
        print(f"{p}:{line_no}: [check-doc] {msg}")
    if findings:
        print(f"doc-recipes-smoke --check-doc: {len(findings)} finding(s)")
        return 1
    print("doc-recipes-smoke --check-doc: ok")
    return 0


def prepare_argv_and_env(
    cmd_str,
    spec,
    ffmpeg_bin,
    clip8_path,
    clip10_path,
    out_path,
    chosen_node=None,
    variant_driver=None,
):
    """Shlex-split, strip comments, extract VAR=val, apply substitutions."""
    words = shlex.split(cmd_str)
    child_env = os.environ.copy()
    prefix = ffmpeg_argv(ffmpeg_bin)

    # Move leading VAR=value into child env
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        k, v = words.pop(0).split("=", 1)
        child_env[k] = v

    if variant_driver:
        child_env["LIBVA_DRIVER_NAME"] = variant_driver

    input_clip = clip10_path if spec["input_kind"] == "10bit" else clip8_path

    new_words = []
    for idx, w in enumerate(words):
        if idx == 0 and w == "ffmpeg":
            new_words.extend(prefix)
            continue
        if w == "input.mkv" or w == "input-10bit.mkv":
            new_words.append(str(input_clip))
            continue
        if w == "out.mkv":
            new_words.append(str(out_path))
            continue
        if "<filters>" in w:
            w = w.replace("<filters>", spec["filters_default"])
        if chosen_node and "/dev/dri/renderD130" in w:
            w = w.replace("/dev/dri/renderD130", chosen_node)
        new_words.append(w)

    # Insert -v verbose if not present
    if "-v" not in new_words and "-loglevel" not in new_words:
        new_words[len(prefix) : len(prefix)] = ["-v", "verbose"]

    return new_words, child_env


# Hardware encoders refuse tiny frames (hevc_vulkan on an RTX 4090 rejects 64x64
# with EINVAL), so every probe encodes one frame at a size each encoder takes.
PROBE_SOURCE = "testsrc2=size=256x256"


def _probe_device_args(device_class, chosen_node):
    """Device arguments and encode tail for one probe. Return (args, error)."""
    if (
        device_class in ("vaapi", "qsv")
        and chosen_node
        and not Path(chosen_node).exists()
    ):
        return None, f"render node {chosen_node} does not exist"
    va = f"vaapi=va:{chosen_node}" if chosen_node else "vaapi=va"
    table = {
        "cuda": (["-init_hw_device", "cuda=cu:0"], []),
        "vulkan-hevc-encode": (
            ["-init_hw_device", "vulkan=vk:0", "-filter_hw_device", "vk"],
            ["-vf", "format=nv12,hwupload", "-c:v", "hevc_vulkan"],
        ),
        "vaapi": (["-init_hw_device", va], []),
        "qsv": (
            [
                "-init_hw_device",
                va,
                "-init_hw_device",
                "qsv=qs@va",
                "-filter_hw_device",
                "qs",
            ],
            ["-vf", "format=nv12,hwupload=extra_hw_frames=16", "-c:v", "hevc_qsv"],
        ),
    }
    if device_class not in table:
        return None, f"unknown device class {device_class}"
    dev, tail = table[device_class]
    return dev + ["-f", "lavfi", "-i", PROBE_SOURCE, "-frames:v", "1"] + tail, None


def probe_device(ffmpeg_bin, device_class, chosen_node=None, variant_driver=None):
    """Probe hardware device readiness. Return (ok, reason)."""
    args, error = _probe_device_args(device_class, chosen_node)
    if error:
        return False, error
    env = os.environ.copy()
    if variant_driver:
        env["LIBVA_DRIVER_NAME"] = variant_driver
    cmd = ffmpeg_argv(ffmpeg_bin) + ["-nostdin", "-hide_banner", "-loglevel", "error"]
    cmd += args + ["-f", "null", "-"]
    proc = run_ffmpeg(cmd, PROBE_TIMEOUT_S, env=env)
    if proc.returncode != 0:
        lines = [
            ln
            for ln in proc.stdout.strip().splitlines()
            if ln.strip() and not ln.startswith("MESA: warning")
        ]
        return False, lines[-1] if lines else f"exit {proc.returncode}"
    return True, "ok"


def discover_render_node(vendor, drm_root=Path("/sys/class/drm")):
    """Return the first /dev/dri/renderD* whose PCI vendor id is `vendor`, else None.

    Render node numbers follow probe order and change between boots, so the
    runner reads the vendor from sysfs instead of trusting a fixed number.
    """
    if not drm_root.is_dir():
        return None
    for entry in sorted(drm_root.glob("renderD*")):
        try:
            found = (entry / "device" / "vendor").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if found == vendor:
            return f"/dev/dri/{entry.name}"
    return None


SOFTWARE_CLIP_ATTEMPTS = (
    (
        "libx265",
        {
            "8bit": ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-crf", "20"],
            "10bit": ["-c:v", "libx265", "-pix_fmt", "yuv420p10le"]
            + ["-profile:v", "main10", "-crf", "20"],
        },
        {},
    ),
    ("libx264", {"8bit": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20"]}, {}),
    (
        "hevc_nvenc",
        {
            "8bit": ["-c:v", "hevc_nvenc", "-pix_fmt", "yuv420p"],
            "10bit": [
                "-c:v",
                "hevc_nvenc",
                "-pix_fmt",
                "p010le",
                "-profile:v",
                "main10",
            ],
        },
        {},
    ),
)


def clip_encoder_attempts():
    """Ordered (name, encoder args per bit depth, env) attempts for the input clips.

    Software encoders first; then NVENC; then VAAPI on the Intel and the AMD node
    with the driver set per node, so a busy or absent device falls through to the
    next one instead of ending the run.
    """
    attempts = list(SOFTWARE_CLIP_ATTEMPTS)
    for node_env, vendor, driver in (
        ("INTEL_RENDER_NODE", "0x8086", "iHD"),
        ("AMD_RENDER_NODE", "0x1002", "radeonsi"),
    ):
        node = os.environ.get(node_env) or discover_render_node(vendor)
        if node is None:
            continue
        dev = ["-init_hw_device", f"vaapi=va:{node}", "-filter_hw_device", "va"]
        args = {
            "8bit": dev + ["-vf", "format=nv12,hwupload", "-c:v", "hevc_vaapi"],
            "10bit": dev
            + [
                "-vf",
                "format=p010le,hwupload",
                "-c:v",
                "hevc_vaapi",
                "-profile:v",
                "main10",
            ],
        }
        attempts.append(("hevc_vaapi", args, {"LIBVA_DRIVER_NAME": driver}))
    return attempts


def encode_clip(ffmpeg_bin, encoders_text, kind, clip):
    """Encode one clip with the first attempt that succeeds. Return (encoder, errors)."""
    errors = []
    for name, args, extra_env in clip_encoder_attempts():
        if kind not in args or not re.search(r"\b" + name + r"\b", encoders_text):
            continue
        cmd = ffmpeg_argv(ffmpeg_bin) + [
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=24",
            "-frames:v",
            "120",
        ]
        cmd += args[kind] + [str(clip)]
        env = os.environ.copy()
        env.update(extra_env)
        proc = run_ffmpeg(cmd, RUN_TIMEOUT_S, env=env, merge_stderr=False)
        if proc.returncode == 0:
            print(f"input clip {clip.name}: {name} {extra_env or ''}".rstrip())
            return name, errors
        errors.append(f"{name}: {proc.stderr.strip()[-200:]}")
    return None, errors


HW_PIXFMTS = frozenset(
    {
        "cuda",
        "vulkan",
        "vaapi",
        "qsv",
        "drm_prime",
        "d3d11",
        "d3d12",
        "dxva2_vld",
        "videotoolbox_vld",
        "opencl",
        "mediacodec",
    }
)
AUTO_SCALE_CONVERSION = re.compile(
    r"\[(auto_scale_\d+) @ [^\]]+\] .*?\bfmt:(\S+).*?->.*?\bfmt:(\S+)"
)
AUTO_SCALE_INSERT = re.compile(r"auto[-_ ]?inserting filter '(auto_scale_\d+)'")


def hardware_auto_scale(log_text):
    """Return a finding when an auto-inserted scale touches hardware frames, else None.

    FFmpeg logs each auto scale as `fmt:<in> ... -> ... fmt:<out>`. A conversion
    between two software formats is the CPU step a software-decode recipe asks
    for with format= before hwupload; one with a hardware format on either side
    means frames left or entered VRAM through the scaler. Every inserted scale
    needs its own conversion line; one without it fails closed.
    """
    conversions = AUTO_SCALE_CONVERSION.findall(log_text)
    for _name, src, dst in conversions:
        if src in HW_PIXFMTS or dst in HW_PIXFMTS:
            return f"auto scale on hardware frames ({src} -> {dst})"
    parsed = {name for name, _src, _dst in conversions}
    unparsed = sorted(set(AUTO_SCALE_INSERT.findall(log_text)) - parsed)
    if unparsed:
        return f"auto scale inserted but its formats are not in the log ({', '.join(unparsed)})"
    if not conversions and re.search(r"auto[-_ ]?inserting", log_text, re.IGNORECASE):
        return "auto scale inserted but its formats are not in the log"
    return None


CLIP_NAMES = {"8bit": "input.mkv", "10bit": "input-10bit.mkv"}


def generate_clips(ffmpeg_bin, tmp_dir, kinds):
    """Generate a 120-frame 1280x720 clip per input kind. Return ({kind: path}, reason)."""
    proc = run_ffmpeg(ffmpeg_argv(ffmpeg_bin) + ["-encoders"], PROBE_TIMEOUT_S)
    clips = {}
    for kind in sorted(kinds):
        clip = tmp_dir / CLIP_NAMES[kind]
        chosen, errors = encode_clip(ffmpeg_bin, proc.stdout, kind, clip)
        if chosen is None:
            detail = "; ".join(errors) if errors else "no suitable encoder found"
            return None, f"failed to encode {kind} input clip: {detail}"
        clips[kind] = clip
    return clips, "ok"


def run_smoke(ffmpeg_bin, output_root=None, recipes_filter=None):
    """Execute smoke test against ffmpeg_bin. Return exit code 0, 1, or 77."""
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    marked = collect_marked_recipes(doc_text)

    if recipes_filter:
        selected_ids = [rid.strip() for rid in recipes_filter.split(",") if rid.strip()]
    else:
        selected_ids = list(SPEC.keys())

    with tempfile.TemporaryDirectory(prefix="pelorus-doc-recipes-clips.") as clip_dir:
        return _run_smoke_in(
            ffmpeg_bin, Path(clip_dir), marked, selected_ids, output_root
        )


def _decoded_frames(ffmpeg_bin, out_clip):
    """Decode out_clip to null. Return (rc, last frame= count or -1)."""
    dec_cmd = ffmpeg_argv(ffmpeg_bin) + [
        "-nostdin",
        "-hide_banner",
        "-i",
        str(out_clip),
    ]
    dec_cmd += ["-f", "null", "-"]
    proc = run_ffmpeg(dec_cmd, RUN_TIMEOUT_S)
    matches = re.findall(r"frame=\s*(\d+)", proc.stdout)
    return proc.returncode, int(matches[-1]) if matches else -1


def _run_failure(proc, spec, ffmpeg_bin, out_clip):
    """Return why one recipe run failed its checks, else None."""
    if proc.returncode != 0:
        return f"exit code {proc.returncode}"
    if re.search(r"\bhwdownload\b", proc.stdout):
        return "forbidden 'hwdownload' in log"
    scale_finding = hardware_auto_scale(proc.stdout)
    if scale_finding:
        return scale_finding
    missing = [n for n in spec["needles"] if n not in proc.stdout]
    if missing:
        return f"missing expected needle(s) {missing}"
    dec_rc, frame_count = _decoded_frames(ffmpeg_bin, out_clip)
    if dec_rc != 0:
        return f"output decode exit code {dec_rc}"
    if frame_count != 120:
        return f"output decoded to {frame_count} frames, expected 120"
    return None


def _run_variant(ctx, recipe_id, var):
    """Run one recipe variant. Return ("pass"|"fail"|"skip", run_tag, message)."""
    spec = SPEC[recipe_id]
    run_tag = f"{recipe_id}:{var['name']}" if var["name"] != "default" else recipe_id
    chosen_node = None
    if var["vendor"]:
        chosen_node = os.environ.get(var["node_env"]) or discover_render_node(
            var["vendor"]
        )
        if chosen_node is None:
            return "skip", run_tag, f"no render node with PCI vendor {var['vendor']}"
    ok, probe_reason = probe_device(
        ctx["ffmpeg_bin"], spec["device_class"], chosen_node, var["driver"]
    )
    if not ok:
        return "skip", run_tag, probe_reason
    stem = run_tag.replace(":", "_")
    out_clip = ctx["out_dir"] / f"{stem}.mkv"
    log_path = ctx["out_dir"] / f"{stem}.log"
    argv, child_env = prepare_argv_and_env(
        ctx["marked"][recipe_id][2],
        spec,
        ctx["ffmpeg_bin"],
        ctx["clips"].get("8bit"),
        ctx["clips"].get("10bit"),
        out_clip,
        chosen_node=chosen_node,
        variant_driver=var["driver"],
    )
    print(f"[{run_tag}] argv: " + " ".join(shlex.quote(a) for a in argv))
    proc = run_ffmpeg(argv, RUN_TIMEOUT_S, env=child_env)
    log_path.write_text(proc.stdout, encoding="utf-8")
    failure = _run_failure(proc, spec, ctx["ffmpeg_bin"], out_clip)
    if failure:
        return "fail", run_tag, f"{failure}, log: {log_path}"
    return "pass", run_tag, ""


def _run_smoke_in(ffmpeg_bin, tmp_dir, marked, selected_ids, output_root):
    """Body of run_smoke; tmp_dir is removed by the caller."""
    if output_root is None:
        out_dir = Path(tempfile.mkdtemp(prefix="pelorus-doc-recipes-out."))
    else:
        out_dir = Path(output_root)
        out_dir.mkdir(parents=True, exist_ok=True)
    for recipe_id in selected_ids:
        if recipe_id not in SPEC:
            print(f"FAIL: unknown recipe id '{recipe_id}' in filter")
            return 1
        if recipe_id not in marked:
            print(f"FAIL: recipe id '{recipe_id}' marked in SPEC but not found in doc")
            return 1
    kinds = {SPEC[recipe_id]["input_kind"] for recipe_id in selected_ids}
    clips, err = generate_clips(ffmpeg_bin, tmp_dir, kinds)
    if clips is None:
        print(f"SKIP: no recipe device: cannot generate input clips: {err}")
        return 77
    ctx = {"ffmpeg_bin": ffmpeg_bin, "out_dir": out_dir, "marked": marked}
    ctx["clips"] = clips
    default = [{"name": "default", "driver": None, "vendor": None, "node_env": None}]
    results = []
    for recipe_id in selected_ids:
        for var in SPEC[recipe_id]["variants"] or default:
            status, run_tag, msg = _run_variant(ctx, recipe_id, var)
            print(f"{status.upper()}: {run_tag}" + (f": {msg}" if msg else ""))
            results.append((status, run_tag, msg))
    failed = [tag for status, tag, _ in results if status == "fail"]
    if failed:
        print(
            f"doc-recipes-smoke: FAIL ({len(failed)} failed runs: {', '.join(failed)})"
        )
        return 1
    skips = [f"{tag}: {msg}" for status, tag, msg in results if status == "skip"]
    if len(skips) == len(results):
        print(
            f"SKIP: no recipe device: all {len(results)} variants skipped ({'; '.join(skips)})"
        )
        return 77
    print("doc-recipes-smoke: all active runs passed")
    return 0


# --- Self-test ---------------------------------------------------------------

STUB_FFMPEG = Path(__file__).resolve().with_name("doc-recipes-stub-ffmpeg.py")


def _quiet(fn, *args, **kwargs):
    """Call fn with stdout discarded; return its result."""
    with (
        open(os.devnull, "w", encoding="utf-8") as devnull,
        contextlib.redirect_stdout(devnull),
    ):
        return fn(*args, **kwargs)


def _quiet_deadline(fn, *args, **kwargs):
    """_quiet with a 3 s recipe deadline, so the hang plant ends fast."""
    global RUN_TIMEOUT_S
    saved, RUN_TIMEOUT_S = RUN_TIMEOUT_S, 3
    try:
        return _quiet(fn, *args, **kwargs)
    finally:
        RUN_TIMEOUT_S = saved


def _self_test_doc(clean_doc):
    """Doc plants for --check-doc. Return the failure count."""
    failures = 0
    print("--- self-test: clean doc check ---")
    if check_doc(str(DOC_PATH), clean_doc) != 0:
        print("self-test FAIL: clean doc failed check-doc")
        failures += 1
    print("--- self-test: doc plants ---")
    derived = "<!-- recipe-run: nvenc-nvdec-derived -->"
    vulkan = "<!-- recipe-run: vulkan-hevc -->"
    fence = "```sh\nffmpeg -i input.mkv out.mkv\n```\n"
    plants_doc = [
        ("fence without marker", clean_doc.replace(derived + "\n\n", "")),
        ("marker without fence", clean_doc + f"\n{derived}\n\nNo fence here.\n"),
        (
            "id without SPEC",
            clean_doc.replace(derived, "<!-- recipe-run: unknown-recipe -->"),
        ),
        (
            "duplicate id",
            clean_doc.replace(derived, f"{derived}\n\n{fence}\n{derived}", 1),
        ),
        (
            "id outside its section",
            clean_doc.replace(vulkan + "\n\n", "<!-- recipe-run: none: moved -->\n\n")
            + f"\n## Notes on Vulkan\n\n{vulkan}\n\n{fence}",
        ),
        (
            "SPEC without marker",
            clean_doc.replace(
                "<!-- recipe-run: vulkan-hevc -->\n\n",
                "<!-- recipe-run: none: skip -->\n\n",
            ),
        ),
    ]
    for name, ptext in plants_doc:
        rc = _quiet(check_doc, "test.md", ptext)
        if rc != 1:
            print(
                f"self-test FAIL: doc plant '{name}' was not refused by check-doc (rc {rc}, want 1)"
            )
            failures += 1
        else:
            print(f"self-test: doc plant '{name}' correctly refused (rc {rc})")
    return failures


def _self_test_exec():
    """Execution plants through a stub ffmpeg. Return the failure count."""
    failures = 0
    print("--- self-test: execution plants ---")
    with tempfile.TemporaryDirectory(prefix="pelorus-stub-bin.") as stub_dir:
        stub_path = STUB_FFMPEG
        exec_plants = [
            ("rc!=0", "rc"),
            ("hwdownload in log", "hwdownload"),
            ("auto scale", "autoscale"),
            ("auto scale without formats", "autoscale_unparsed"),
            ("second auto scale without formats", "autoscale_mixed"),
            ("hung recipe run", "hang"),
            ("missing needle", "missing_needle"),
            ("119 frames", "119frames"),
        ]
        for name, plant_env in exec_plants:
            os.environ["STUB_PLANT"] = plant_env
            out = Path(stub_dir) / f"out-{plant_env}"
            rc = _quiet_deadline(
                run_smoke,
                stub_path,
                output_root=out,
                recipes_filter="nvenc-nvdec-derived",
            )
            # a refusal is rc 1; a 77 means every variant skipped and the
            # plant never reached the check it targets
            if rc != 1:
                print(
                    f"self-test FAIL: execution plant '{name}' was not refused (rc {rc}, want 1)"
                )
                failures += 1
            else:
                print(
                    f"self-test: execution plant '{name}' correctly refused (rc {rc})"
                )
        print("--- self-test: 8-bit recipe without a 10-bit clip ---")
        os.environ["STUB_PLANT"] = "no10bit"
        rc = _quiet(
            run_smoke,
            stub_path,
            output_root=Path(stub_dir) / "out-no10bit",
            recipes_filter="nvenc-nvdec-derived",
        )
        if rc != 0:
            print(
                f"self-test FAIL: 8-bit recipe did not run without a 10-bit clip (rc {rc})"
            )
            failures += 1
        else:
            print("self-test: 8-bit recipe ran without a 10-bit clip")
        print("--- self-test: clean stub run ---")
        os.environ["STUB_PLANT"] = "clean"
        rc = _quiet(run_smoke, stub_path, output_root=Path(stub_dir) / "out-clean")
        if rc != 0:
            print(f"self-test FAIL: clean stub run failed with rc {rc}")
            failures += 1
        else:
            print("self-test: clean stub run passed")
    return failures


def self_test():
    """Plant every defect class and require each refused; the clean cases must pass."""
    failures = _self_test_doc(DOC_PATH.read_text(encoding="utf-8"))
    failures += _self_test_exec()
    if failures:
        print(f"doc-recipes-smoke self-test: {failures} failure(s)")
        return 1
    print("doc-recipes-smoke self-test: ok")
    return 0


def main(argv):
    args = list(argv)
    if "--check-doc" in args:
        text = DOC_PATH.read_text(encoding="utf-8")
        return check_doc(str(DOC_PATH), text)
    if "--self-test" in args:
        return self_test()

    ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    if not ffmpeg_bin:
        print("SKIP: FFMPEG_BIN unset: doc recipes smoke needs a patched ffmpeg binary")
        return 77

    out_root = os.environ.get("OUTPUT_ROOT")
    recipes_filter = os.environ.get("RECIPES")
    return run_smoke(
        Path(ffmpeg_bin), output_root=out_root, recipes_filter=recipes_filter
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
