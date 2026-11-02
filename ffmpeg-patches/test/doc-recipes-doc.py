# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Doc side of the zero-copy recipe smoke: the SPEC table and --check-doc.

Imported by doc-recipes-smoke.py; it parses the marked recipes in
docs/usage/ffmpeg.md and refuses a marker, fence or section that drifts.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
_cdr = __import__("importlib").import_module("check-doc-recipes")

split_blocks = _cdr.split_blocks
parse_block = _cdr.parse_block
strip_comment = _cdr.strip_comment
is_ffmpeg_start = _cdr.is_ffmpeg_start

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
