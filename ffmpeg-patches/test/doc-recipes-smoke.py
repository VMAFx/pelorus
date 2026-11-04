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

import argparse
import importlib
import os
import re
import shlex
import shutil
import subprocess  # nosec B404 - fixed ffmpeg argv, no shell, explicit deadline
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

# Reuse check-doc-recipes parsing helpers (HISS-19)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
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


class RunResult(NamedTuple):
    """Exit code and output of one ffmpeg run."""

    returncode: int
    stdout: str
    stderr: str


def run_ffmpeg(argv, timeout_s, env=None, merge_stderr=True):
    """Run one ffmpeg command without a shell. Return a RunResult.

    argv[0] must name an existing file. A run past timeout_s returns exit code
    124 with the deadline in its output, so every caller treats it as a failure.
    """
    if not Path(argv[0]).is_file():
        raise FileNotFoundError(f"ffmpeg binary not found: {argv[0]}")
    try:
        proc = subprocess.run(  # nosec B603 - argv[0] is a checked file; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - list argv from the tracked docs, no shell, explicit deadline
            argv,  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - argv is a recipe from the tracked docs, split without a shell; argv[0] is the operator's FFMPEG_BIN
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        msg = f"timed out after {timeout_s} s"
        return RunResult(124, msg, "" if merge_stderr else msg)
    return RunResult(proc.returncode, proc.stdout, proc.stderr or "")


def resolve_ffmpeg_bin(value):
    """Resolve FFMPEG_BIN (a path or a bare name on PATH). Return a Path or None."""
    path = Path(value)
    if path.is_absolute():
        return path if path.is_file() else None
    resolved = shutil.which(value)
    return Path(resolved) if resolved else None


_doc = importlib.import_module("doc-recipes-doc")
DOC_PATH = _doc.DOC_PATH
SPEC = _doc.SPEC
check_doc = _doc.check_doc
collect_marked_recipes = _doc.collect_marked_recipes


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
    if proc.returncode != 0:
        tail = proc.stdout.strip()[-200:]
        return None, f"FAIL: ffmpeg -encoders exited {proc.returncode}: {tail}"
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
    # a reused output root keeps the last run's clip; the decode check
    # must only ever see what this run wrote
    out_clip.unlink(missing_ok=True)
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
    if clips is None and err.startswith("FAIL: "):
        print(err)
        return 1
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for doc recipes smoke."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-doc", action="store_true", help="Check document recipe markup")
    parser.add_argument("--self-test", action="store_true", help="Run self-test suite")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for doc recipes smoke."""
    args = parse_args(argv)
    if args.check_doc:
        text = DOC_PATH.read_text(encoding="utf-8")
        return check_doc(str(DOC_PATH), text)
    if args.self_test:
        selftest = importlib.import_module("doc-recipes-self-test")
        return selftest.self_test(sys.modules[__name__])

    ffmpeg_bin = os.environ.get("FFMPEG_BIN")
    if not ffmpeg_bin:
        print("SKIP: FFMPEG_BIN unset: doc recipes smoke needs a patched ffmpeg binary")
        return 77

    resolved = resolve_ffmpeg_bin(ffmpeg_bin)
    if resolved is None:
        print(f"FAIL: FFMPEG_BIN not found as a file or on PATH: {ffmpeg_bin}")
        return 1
    out_root = os.environ.get("OUTPUT_ROOT")
    recipes_filter = os.environ.get("RECIPES")
    return run_smoke(resolved, output_root=out_root, recipes_filter=recipes_filter)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
