#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Pelorus tester stage runners (ADR-0173, #227).

Loaded by `pelorus_tester_report.py`; not run on its own. Each runner takes the
report context and the stage spec and returns an outcome tuple
`(status, reason, log, legs)` where status is `pass`, `fail`, `not_run`,
`no_device` or `incomplete`, and legs is None or one record per encoder tried
(`pass`, `fail` or `not_run` with a reason; a QSV bitrate-control leg also names
its `rate_control`). Pass rules live in small pure functions
(`steering_verdict`, `graph_problems`, `blob_problems`, `brc_verdict`, ...) so
`self_test` can plant a failure for each rule without hardware.

Environment (read from `ctx["env"]`): `FFMPEG_BIN` (default `ffmpeg`),
`VULKAN_DEVICE` (FFmpeg Vulkan device index), `PELORUS_VALIDATE` (`auto`,
`1`, `0`; `1` fails closed when the validation layer is absent),
`PELORUS_TESTER_CACHE` (fixture cache), `PELORUS_FORMAT_MATRIX` (script path
override), `VMAF_BIN` (bench scoring).
"""

import datetime
import hashlib
import importlib.util
import re
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

RULES = (
    "zc_hwdownload", "zc_hwupload", "zc_scale", "zc_graph_nonempty",
    "validate_fail_closed", "validation_absent_not_run", "encoder_absent_not_run",
    "vuid_unlisted", "vuid_expiry", "vuid_reference", "vuid_gate_every_stage",
    "steering_effect", "steering_decode", "steering_control", "steering_selfreport",
    "steering_baseline_defect",
    "sd_present", "sd_structure", "sd_decode_tap", "sd_pts", "sd_carrier_masking",
    "brc_bitrate", "brc_decode", "brc_extbrc_engaged", "brc_no_huc", "brc_hw_refused",
)

ENC_TIMEOUT_S = 180
MATRIX_TIMEOUT_S = 1800
BENCH_TIMEOUT_S = 1800
MAX_DEVICES = 8
MAX_SEI_NALS = 4096
MAX_SEI_MESSAGES = 64
STEER_FRAMES = (8, 16)
SIDEDATA_FRAMES = 4
VK_LAYER = "VK_LAYER_KHRONOS_validation"
VUID_RE = re.compile(r"VUID-[A-Za-z0-9_.-]+")
ALLOWLIST_PATH = REPO_ROOT / "ffmpeg-patches" / "test" / "vulkan-vuid-allowlist.txt"
ALLOWLIST_MAX_LINES = 500
REF_RE = re.compile(r"^(#\d+|VMAFx/[A-Za-z0-9_.-]+#\d+|https://\S+)$")
PELORUS_UUID = bytes.fromhex("e1d7c4a26b934f089a550f3c2db17e64")
PELORUS_MAGIC = b"PELOR1\0\0"
SEC_BANDING, SEC_VARIANCE = 1, 2
REQUIRED_SECTIONS = SEC_BANDING | SEC_VARIANCE
HEADER_FMT = "<8sHHIIHHQ"
HEADER_BYTES = 48
DIR_FMT = "<IIII"
FILTER_RE = re.compile(r"^Filter '([^']+)' formats:\s*$", re.M)
INSERT_RE = re.compile(r"auto-inserting filter '([^']+)' between")
TRANSFER_TYPES = ("hwdownload", "hwupload", "hwupload_cuda")
NATIVE_FILTERS = ("pelorus_deband_vulkan", "pelorus_denoise_vulkan", "pelorus_mc_vulkan")

CODECS = {"h264": "h264", "hevc": "hevc", "av1": "obu"}
# Steering and zero-copy streams go into bitexact Matroska: the container keeps
# the AV1 sequence header (a raw OBU stream does not) and is reproducible.
MUX_ARGS = ["-fflags", "+bitexact", "-f", "matroska"]
# An AV1 encoder that fails its first encode with one of these has no AV1
# encode on this GPU or driver (NVENC before Ada, QSV before Arc).
NO_AV1_RE = re.compile(r"(?i)(no capable devices|codec not supported|unsupported|"
                       r"not supported|no usable encoding profile)")
STEER_OPTION = "pelorus_roi"
NOEFFECT_RE = re.compile(r"(continuing without ROI bias"
                         r"|regions asking for a lower QP[^\n]*clamped to \d+"
                         r"|pelorus_roi: [^\n]*\bdisabl(?:ed|ing)\b[^\n]*)")
# The steered encodes get ROI side data; the unsteered control gets none, because
# FFmpeg's stock QSV path applies ROI rectangles whatever -pelorus_roi says
# (h264_qsv on an Arc A380: research 0229).
FILTER_CHAIN = "pelorus_analyze_vulkan=roi=1"
CONTROL_CHAIN = "pelorus_analyze_vulkan=roi=0"
# Intel media driver packages; a QSV leg that fails with only the free one
# installed (a host run or a custom image: the tester-intel image ships the
# non-free one) says it may need the non-free one (ADR-0180); HEVC with the
# free driver failed on an A380 under xe without HuC (research 0229).
MEDIA_DRIVER_FREE = "intel-media-va-driver"
MEDIA_DRIVER_NONFREE = "intel-media-va-driver-non-free"
# kind: vulkan = frames stay in VRAM to the encoder; hw = hwdownload to a
# hardware encoder; sw = hwdownload to a software encoder.
ENCODERS = (
    {"name": "h264_vulkan", "codec": "h264", "kind": "vulkan", "pix": "nv12", "args": ["-qp", "30"]},
    {"name": "hevc_vulkan", "codec": "hevc", "kind": "vulkan", "pix": "nv12", "args": ["-qp", "30"]},
    {"name": "av1_vulkan", "codec": "av1", "kind": "vulkan", "pix": "nv12", "args": ["-qp", "30"]},
    {"name": "h264_nvenc", "codec": "h264", "kind": "hw", "pix": "nv12",
     "args": ["-rc", "constqp", "-qp", "30"]},
    {"name": "hevc_nvenc", "codec": "hevc", "kind": "hw", "pix": "nv12",
     "args": ["-rc", "constqp", "-qp", "30"]},
    {"name": "av1_nvenc", "codec": "av1", "kind": "hw", "pix": "nv12",
     "args": ["-rc", "constqp", "-qp", "80"]},
    # QSV: -q:v selects CQP (the dense ROI path needs it); patch 0005 steers
    # h264_qsv and hevc_qsv, so av1_qsv shows as a not_run leg after its probe.
    {"name": "h264_qsv", "codec": "h264", "kind": "hw", "pix": "nv12", "args": ["-q:v", "30"]},
    {"name": "hevc_qsv", "codec": "hevc", "kind": "hw", "pix": "nv12", "args": ["-q:v", "30"]},
    {"name": "av1_qsv", "codec": "av1", "kind": "hw", "pix": "nv12", "args": ["-q:v", "30"]},
    {"name": "libsvtav1", "codec": "av1", "kind": "sw", "pix": "yuv420p",
     "args": ["-crf", "30", "-svtav1-params", "aq-mode=0"]},
    {"name": "libaom-av1", "codec": "av1", "kind": "sw", "pix": "yuv420p",
     "args": ["-crf", "30", "-cpu-used", "8"]},
)
# Encoders that can write the Pelorus blob as user-data-unregistered SEI with
# -udu_sei 1: NVENC (stock), QSV (patch 0019), Vulkan Video (patch 0020).
SEI_CARRIERS = (
    {"name": "hevc_nvenc", "codec": "hevc", "kind": "hw", "args": []},
    {"name": "h264_nvenc", "codec": "h264", "kind": "hw", "args": []},
    {"name": "hevc_qsv", "codec": "hevc", "kind": "hw", "args": []},
    {"name": "h264_qsv", "codec": "h264", "kind": "hw", "args": []},
    {"name": "hevc_vulkan", "codec": "hevc", "kind": "vulkan", "args": ["-qp", "30"]},
    {"name": "h264_vulkan", "codec": "h264", "kind": "vulkan", "args": ["-qp", "30"]},
)
# QSV bitrate-control legs of the steering stage (ADR-0180 decision 8): CBR at
# BRC_TARGET with a one-second buffer over the synth-motion fixture played
# BRC_LOOPS + 1 times (400 frames, 16 s). A leg passes when every frame decodes and
# the bitrate is within BRC_TOLERANCE of the target. cbr_extbrc asks oneVPL for its
# software bitrate control (-extbrc 1: the runtime picks each frame's QP and drives
# the driver in constant QP); cbr_hw leaves bitrate control to the hardware.
BRC_FIXTURE = "synth-motion"
BRC_TARGET = 1000000
BRC_TOLERANCE = 0.15
BRC_LOOPS = 7
BRC_RATE = ["-b:v", "1000k", "-maxrate", "1000k", "-bufsize", "1000k"]
BRC_CHAIN = "format=nv12,hwupload,pelorus_deband_vulkan,hwdownload,format=nv12"
EXTBRC_RE = re.compile(r"\bExtBRC: (\w+)")
# How the encoder reports a hardware bitrate-control encode that produced no frame
# on DG2 under xe, which loads no HuC firmware: the runtime's GPU Hang (-21) or
# device failed (-17), or an empty bitstream (ADR-0180 decision 8).
NO_HUC_RE = re.compile(r"Invalid FrameType:0\b|GPU Hang \(-21\)|device failed \(-17\)")
NO_HW_BRC_RE = re.compile(r"Selected ratecontrol mode is unsupported")
PROBLEM_RE = re.compile(r"(?i)\b(?:error|invalid|fail\w*|unsupported|hang)\b")
NO_HUC_REASON = ("hardware bitrate control needs HuC firmware, and the xe kernel driver loads "
                 "none on DG2 (Arc A-series): constant QP encodes, CBR does not")
# oneVPL honours ExtBRC for AV1 only through its EncTools look-ahead (LookAheadDepth > 0,
# vpl-gpu-rt av1ehw_base_enctools_com.h IsSwEncToolsImplicit, 25.1.4 and main); FFmpeg's
# av1_qsv sets no look-ahead by default.
AV1_EXTBRC_REASON = ("oneVPL applies -extbrc to AV1 only through its look-ahead tools, which "
                     "av1_qsv does not enable by default; on an A380 under xe the AV1 encode "
                     "with -extbrc 1 failed like hardware bitrate control")
DG2_PCI_PREFIX = "0x56"
MAX_RENDER_NODES = 64


def outcome(status, reason, log="", legs=None):
    return status, reason, log, legs


def load_sibling(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ pure rules

def validation_decision(mode, layer, need_layer, disabled=frozenset()):
    """Return (status or None, reason). None means the stage may go on."""
    if mode not in ("auto", "1", "0"):
        return "fail", "PELORUS_VALIDATE must be auto, 1 or 0, got %r" % mode
    if mode == "1" and not layer:
        if "validate_fail_closed" in disabled:
            return "not_run", "validation layer absent"
        return "fail", "PELORUS_VALIDATE=1 but %s is unavailable" % VK_LAYER
    if mode == "auto" and need_layer and not layer:
        if "validation_absent_not_run" in disabled:
            return None, ""
        return "not_run", ("%s is not installed; the matrix needs it (install it, or "
                           "set PELORUS_VALIDATE=0 to run without validation)" % VK_LAYER)
    return None, ""


def parse_allowlist(text, today, disabled=frozenset()):
    """Entries of the shared VUID allow-list that may match today.

    Line: `<VUID without prefix> | <reference> | <YYYY-MM-DD expiry>`. An entry with
    no reference, a malformed expiry or a past expiry never matches."""
    entries = []
    for line in text.splitlines()[:ALLOWLIST_MAX_LINES]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not parts[0]:
            continue
        vuid, ref, expiry = parts
        if "vuid_reference" not in disabled and not REF_RE.match(ref):
            continue
        try:
            until = datetime.date.fromisoformat(expiry)
        except ValueError:
            continue
        if until < today and "vuid_expiry" not in disabled:
            continue
        entries.append({"vuid": vuid, "line": " | ".join(parts)})
    return entries


def vuid_gate(text, entries, disabled=frozenset()):
    """Split the VUIDs in a log into (unlisted, listed entry lines)."""
    by_name = {e["vuid"]: e["line"] for e in entries}
    unlisted, listed = [], []
    for found in sorted(set(VUID_RE.findall(text))):
        entry = by_name.get(found[len("VUID-"):])
        if entry is None and "vuid_unlisted" not in disabled:
            unlisted.append(found)
        elif entry is not None:
            listed.append(entry)
    return unlisted, listed


def load_allowlist(ctx):
    """Allow-list entries valid today; unreadable file means no entries (fail closed)."""
    if "allowlist" not in ctx:
        path = Path(ctx["env"].get("PELORUS_VUID_ALLOWLIST") or ALLOWLIST_PATH)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        today = ctx.get("today") or datetime.date.today()
        ctx["allowlist"] = parse_allowlist(text, today, ctx["disabled"])
    return ctx["allowlist"]


def filter_type(name):
    """Filter type behind an instance name: Parsed_<type>_<n>, auto_<type>_<n>."""
    m = re.match(r"^(?:Parsed|auto)_(.+)_\d+$", name)
    return m.group(1) if m else name


def graph_filter_types(log):
    """Types of every filter in an ffmpeg -loglevel debug graph dump."""
    names = FILTER_RE.findall(log) + INSERT_RE.findall(log)
    return [filter_type(n) for n in names]


def graph_problems(log, sw_source, sw_encoder, expect=(), disabled=frozenset()):
    """Stage 7 pass rule: no unexpected transfer or scale in the filter graph.

    A software source may add one hwupload, a software encoder one hwdownload;
    anything beyond that, and any scale, is a host round trip."""
    types = graph_filter_types(log)
    if not types and "zc_graph_nonempty" not in disabled:
        return ["no filter graph in the debug log; the assertion has nothing to check"]
    errs = ["graph lacks expected filter %s" % e for e in expect
            if types and e not in types and "zc_graph_nonempty" not in disabled]
    limits = {"hwdownload": ("zc_hwdownload", int(sw_encoder)),
              "hwupload": ("zc_hwupload", int(sw_source)),
              "hwupload_cuda": ("zc_hwupload", 0), "scale": ("zc_scale", 0)}
    for ftype, (rule, allowed) in limits.items():
        count = types.count(ftype)
        if count > allowed and rule not in disabled:
            errs.append("graph has %d %s (allowed %d)" % (count, ftype, allowed))
    return errs


def frames_from_size(size, width, height):
    """Frame count of a raw yuv420p dump; -1 when the size is not whole frames."""
    frame = width * height * 3 // 2
    return size // frame if frame and size % frame == 0 else -1


def steering_verdict(h, counts, notes="", disabled=frozenset(), errs=None):
    """Return (status, reason): pass, fail, or not_run for an inconclusive control.

    `h` maps none8, none8b, roi8, none16, roi16 to bitstream digests; `counts`
    maps the same keys (except none8b) to decoded frame numbers; `notes` is the
    steered encodes' log, searched for the encoder's own statement that it
    ignores the steering data (named in the reason, never silent); `errs` maps a
    key to the decoder's error line. An unsteered baseline that does not decode
    is an encoder or driver defect, a named not_run; a steered stream that does
    not decode is a failure."""
    errs = errs or {}
    want = {"none8": 8, "roi8": 8, "none16": 16, "roi16": 16}
    broken = [k for k in ("none8", "none16") if counts.get(k) != want[k]]
    if broken and "steering_baseline_defect" not in disabled:
        return "not_run", ("baseline output does not decode (encoder/driver defect): %s decoded "
                        "%s frames, expected %d; %s" % (broken[0], counts.get(broken[0]),
                                                       want[broken[0]], errs.get(broken[0], "no decoder output")))
    for key, n in want.items():
        if counts.get(key) != n and "steering_decode" not in disabled:
            return "fail", "%s decoded %s frames, expected %d" % (key, counts.get(key), n)
    if h["none8"] != h["none8b"] and "steering_control" not in disabled:
        return "not_run", "baseline encode is not deterministic; steering test inconclusive"
    same = [k for k in ("8", "16") if h["roi" + k] == h["none" + k]]
    said = NOEFFECT_RE.search(notes)
    if same and said and "steering_selfreport" not in disabled:
        return "not_run", "encoder reports it ignores the steering data: " + said.group(0)
    if same and "steering_effect" not in disabled:
        return "fail", "steering did not change the %s-frame bitstream" % "/".join(same)
    return "pass", "steering changes the bitstream at 8 and 16 frames; both decode"


def aggregate_legs(results, disabled=frozenset()):
    """results: list of (encoder, codec, state, note[, rate_control]). Return an outcome with one leg each.

    A bitrate-control leg (rate_control set) can fail the stage but never pass it alone."""
    parts = {"pass": [], "fail": [], "not_run": []}
    legs = []
    for name, codec, state, note, *rate in results:
        label = " ".join([name] + rate[:1])
        parts[state].append("%s (%s)" % (label, note) if note else label)
        legs.append({"encoder": name, "codec": codec, "status": state,
                     "reason": "" if state == "pass" else (note or "no reason given")[:400]})
        if rate:
            legs[-1]["rate_control"] = rate[0]
    text = "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in parts.items() if v)
    if parts["fail"]:
        return outcome("fail", text, legs=legs)
    if not any(l["status"] == "pass" and "rate_control" not in l for l in legs):
        if "encoder_absent_not_run" in disabled:
            return outcome("pass", text, legs=legs)
        return outcome("not_run", "no usable encoder; " + text, legs=legs)
    return outcome("pass", text, legs=legs)


def brc_verdict(size, decoded, expected, fps, disabled=frozenset()):
    """Pass rule of a bitrate-control leg: every frame decodes and the bitrate of
    `size` bytes over `expected` frames is within BRC_TOLERANCE of BRC_TARGET."""
    if decoded != expected and "brc_decode" not in disabled:
        return "fail", "decoded %s of %d frames" % (decoded, expected)
    rate = size * 8.0 * fps / expected if expected else 0.0
    off = rate / BRC_TARGET - 1.0
    text = "%.0f kb/s, %+.1f%% off the %d kb/s target" % (rate / 1000, off * 100, BRC_TARGET // 1000)
    if abs(off) > BRC_TOLERANCE and "brc_bitrate" not in disabled:
        return "fail", "%s (allowed: within %d%%)" % (text, round(BRC_TOLERANCE * 100))
    return "pass", "%s; %d frames decode" % (text, expected)


def extbrc_problems(log, disabled=frozenset()):
    """An -extbrc 1 encode must report the runtime's software bitrate control as on."""
    seen = EXTBRC_RE.search(log)
    if "brc_extbrc_engaged" in disabled or (seen and seen.group(1) == "ON"):
        return []
    return ["-extbrc 1 was set but the encoder reports %s" % (
        seen.group(0) if seen else "no ExtBRC state")]


def hw_brc_failure(line, host, disabled=frozenset()):
    """(status, reason) of a hardware-CBR encode that failed while constant QP encodes.

    `host` is intel_render_host(): the no-HuC signature is a named not_run only on a
    DG2 bound to xe (or, unconfirmed, when the kernel driver cannot be read); a driver
    that refuses the mode is a named not_run; anything else is a fail."""
    if NO_HUC_RE.search(line) and "brc_no_huc" not in disabled:
        if host == "xe-dg2":
            return "not_run", "%s: %s" % (NO_HUC_REASON, line)
        if host == "unknown":
            return "not_run", ("hardware CBR fails as on DG2 under xe without HuC firmware, but "
                               "the GPU's kernel driver could not be read, so that cause is not "
                               "confirmed: " + line)
    if NO_HW_BRC_RE.search(line) and "brc_hw_refused" not in disabled:
        return "not_run", "the media driver offers no hardware bitrate control here: " + line
    return "fail", "hardware CBR fails while constant QP encodes: " + line


# ------------------------------------------------------------- SEI parsing

def split_nals(data):
    """Annex B NAL units (without start codes); bounded by MAX_SEI_NALS * 64."""
    marks = [m.start() for m in re.finditer(b"\x00\x00\x01", data)]
    nals = []
    for i, pos in enumerate(marks[:MAX_SEI_NALS * 64]):
        end = marks[i + 1] if i + 1 < len(marks) else len(data)
        nals.append(data[pos + 3:end].rstrip(b"\x00"))
    return nals


def nal_kind(nal, codec):
    """Return ('sei'|'vcl'|'other', payload offset)."""
    if not nal:
        return "other", 0
    if codec == "hevc":
        t = (nal[0] >> 1) & 0x3F
        return ("sei" if t in (39, 40) else "vcl" if t < 32 else "other"), 2
    t = nal[0] & 0x1F
    return ("sei" if t == 6 else "vcl" if t in (1, 5) else "other"), 1


def sei_varint(rbsp, pos):
    """0xFF-extended SEI value at pos: (value, next pos), or (None, end)."""
    val = 0
    for i in range(pos, len(rbsp)):
        val += rbsp[i]
        if rbsp[i] != 255:
            return val, i + 1
    return None, len(rbsp)


def sei_messages(rbsp):
    """Yield (payload type, payload bytes) of one SEI RBSP; bounded."""
    pos = 0
    for _ in range(MAX_SEI_MESSAGES):
        if pos >= len(rbsp) - 1:
            return
        ptype, pos = sei_varint(rbsp, pos)
        size, pos = sei_varint(rbsp, pos)
        if ptype is None or size is None:
            return
        yield ptype, rbsp[pos:pos + size]
        pos += size


def pelorus_blobs_per_frame(data, codec):
    """List, per coded picture, of the Pelorus blobs in its SEI NAL units."""
    frames, pending = [], []
    for nal in split_nals(data)[:MAX_SEI_NALS]:
        kind, skip = nal_kind(nal, codec)
        if kind == "sei":
            rbsp = nal[skip:].replace(b"\x00\x00\x03", b"\x00\x00")
            pending.extend(pl[16:] for t, pl in sei_messages(rbsp)
                           if t == 5 and pl[:16] == PELORUS_UUID)
        elif kind == "vcl":
            frames.append(pending)
            pending = []
    return frames


def header_errors(fields, length):
    magic, major, _minor, total, _mask, _count, hsize, _pts = fields
    errs = []
    if magic != PELORUS_MAGIC or major != 1 or hsize != HEADER_BYTES:
        errs.append("header magic/abi/size wrong (abi_major %d)" % major)
    if total != length:
        errs.append("total_size %d != blob length %d" % (total, length))
    return errs


def directory_errors(blob, fields):
    _magic, _major, _minor, total, mask, count, _hsize, _pts = fields
    ids, errs, dir_end = 0, [], HEADER_BYTES + 16 * count
    for i in range(min(count, 32)):
        raw = blob[HEADER_BYTES + 16 * i:HEADER_BYTES + 16 * i + 16]
        if len(raw) < 16:
            return errs + ["section directory truncated"]
        sid, off, size, _sm = struct.unpack(DIR_FMT, raw)
        ids |= sid
        if off < dir_end or off + size > total:
            errs.append("section %#x lies outside the blob" % sid)
    if ids != mask:
        errs.append("section_mask %#x != directory ids %#x" % (mask, ids))
    if (mask & REQUIRED_SECTIONS) != REQUIRED_SECTIONS:
        errs.append("banding and variance sections missing")
    return errs


def blob_problems(blob, disabled=frozenset()):
    """Structural check of one PelorusSideData blob; returns (problems, pts)."""
    if len(blob) < HEADER_BYTES:
        return ["blob shorter than the %d-byte header" % HEADER_BYTES], -1
    fields = struct.unpack(HEADER_FMT, blob[:struct.calcsize(HEADER_FMT)])
    errs = header_errors(fields, len(blob)) + directory_errors(blob, fields)
    return ([] if "sd_structure" in disabled else errs), fields[7]


def sidedata_problems(frames, n_expected, disabled=frozenset()):
    """Stage 6 pass rule over the blobs read back from the encoded bitstream."""
    present = [f for f in frames if f]
    if len(frames) != n_expected or len(present) != n_expected:
        if "sd_present" not in disabled:
            return ["%d of %d coded pictures carry a Pelorus blob (%d pictures)"
                    % (len(present), n_expected, len(frames))]
    errs, pts = [], []
    for idx, blobs in enumerate(frames):
        for blob in blobs[:8]:
            bad, p = blob_problems(blob, disabled)
            errs.extend("picture %d: %s" % (idx, b) for b in bad)
            pts.append(p)
    if len(set(pts)) != len(pts) and "sd_pts" not in disabled:
        errs.append("frame_pts echoes are not distinct: desynchronised side data")
    return errs


def decode_tap_problems(showinfo_text, n_expected, disabled=frozenset()):
    """Decoder must hand the blob back as frame side data on every picture."""
    if "sd_decode_tap" in disabled:
        return []
    frames = len(re.findall(r"\bn:\s*\d+\s+pts:", showinfo_text))
    uuids = showinfo_text.count("UUID=e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64")
    if frames != n_expected or uuids < n_expected:
        return ["decode tap saw %d frames with %d Pelorus blobs, expected %d each"
                % (frames, uuids, n_expected)]
    return []


# ---------------------------------------------------------------- helpers

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def first_line(text):
    lines = [l for l in text.splitlines() if l.strip() and "MESA:" not in l]
    return (lines[-1] if lines else "no output")[:200]


def encoder_error_line(text, name):
    """The first two lines FFmpeg logged for this encoder, else the last line of the log."""
    own = [l.split("]", 1)[-1].strip() for l in text.splitlines()[:2000] if "[%s @" % name in l]
    return ("%s: %s" % (name, "; ".join(own[:2])) if own else first_line(text))[:200]


def encoder_problem_line(text, name):
    """First line the encoder logged that names a problem; for a verbose log, where
    the encoder's first lines are its settings."""
    tag = "[%s @" % name
    own = [l.split("]", 1)[-1].strip() for l in text.splitlines()[:5000]
           if tag in l and PROBLEM_RE.search(l.split("]", 1)[-1])]
    return ("%s: %s" % (name, own[0]))[:200] if own else first_line(text)


def intel_render_host(dev_root="/dev", sys_root="/sys"):
    """'xe-dg2' when an Intel DG2 render node (PCI id 0x56xx, linux
    include/drm/intel/pciids.h) under dev_root/dri is bound to xe; 'unknown' when no
    Intel render node can be read from sys_root; else 'other'."""
    try:
        nodes = sorted(p.name for p in Path(dev_root, "dri").iterdir() if p.name.startswith("renderD"))
    except OSError:
        return "unknown"
    seen = False
    for node in nodes[:MAX_RENDER_NODES]:
        base = Path(sys_root, "class", "drm", node, "device")
        try:
            vendor = (base / "vendor").read_text(encoding="ascii").strip()
            device = (base / "device").read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if vendor != "0x8086":
            continue
        seen = True
        if device.startswith(DG2_PCI_PREFIX) and (base / "driver").resolve().name == "xe":
            return "xe-dg2"
    return "other" if seen else "unknown"


def unusable(enc, text, why):
    """not_run leg for an encoder whose first encode failed; names a missing AV1 encoder.

    Only the encoder's own lines decide "no AV1 encode": another component's
    "not supported" (a VA-API probe, a filter) is no statement about AV1."""
    line = encoder_error_line(text, enc["name"]) if text else why
    if enc["codec"] == "av1" and NO_AV1_RE.search(line):
        return "not_run", "no AV1 encode on this GPU or driver: " + line
    return "not_run", "not usable on this host: " + line


def installed_media_drivers(ctx):
    """{package: version} of the Intel media drivers dpkg lists as installed; cached.

    Empty outside a Debian image (no dpkg-query): then no leg names a driver."""
    if "media_drivers" not in ctx:
        code, text, _ = ctx["run"](["dpkg-query", "-W", "-f", "${Package} ${Version} ${db:Status-Abbrev}\n",
                                    MEDIA_DRIVER_FREE, MEDIA_DRIVER_NONFREE], 30, ctx["env"])
        found = {}
        for line in text.splitlines()[:16] if code is not None else []:
            parts = line.split()
            if len(parts) >= 3 and parts[0] in (MEDIA_DRIVER_FREE, MEDIA_DRIVER_NONFREE) and parts[2] == "ii":
                found[parts[0]] = parts[1]
        ctx["media_drivers"] = found
    return ctx["media_drivers"]


def free_driver_leg(ctx, enc, state, note):
    """A QSV leg that cannot encode with only the free media driver names the driver it lacks."""
    drivers = installed_media_drivers(ctx) if enc["name"].endswith("_qsv") else {}
    if (state != "not_run" or note.startswith("no AV1 encode") or MEDIA_DRIVER_FREE not in drivers
            or MEDIA_DRIVER_NONFREE in drivers):
        return state, note
    return "not_run", ("%s did not encode with the free %s %s; it may need %s, which is not "
                       "installed (the tester-intel image ships it, ADR-0180; HEVC with the "
                       "free driver failed on an A380 under xe without HuC): %s" % (
                           enc["name"], MEDIA_DRIVER_FREE, drivers[MEDIA_DRIVER_FREE],
                           MEDIA_DRIVER_NONFREE, note))[:400]


def encoder_has_option(ctx, name, option):
    """Whether `ffmpeg -h encoder=<name>` lists -<option>; cached per encoder."""
    helps = ctx["ff"].setdefault("help", {})
    if name not in helps:
        code, text, _ = ctx["run"]([ctx["ff"]["bin"], "-hide_banner", "-h", "encoder=" + name],
                                   60, ctx["env"])
        helps[name] = text if code == 0 else ""
    return re.search(r"^\s*-%s\b" % re.escape(option), helps[name], re.M) is not None


def ffmpeg_info(ctx):
    """Cached (binary path or None, filter names, encoder names)."""
    if "ff" in ctx:
        return ctx["ff"]
    binary = shutil.which(ctx["env"].get("FFMPEG_BIN", "ffmpeg"))
    info = {"bin": binary, "filters": set(), "encoders": set()}
    for flag, key in (("-filters", "filters"), ("-encoders", "encoders")):
        if binary:
            code, text, _ = ctx["run"]([binary, "-hide_banner", flag], 60, ctx["env"])
            rows = [l.split() for l in text.splitlines()[:5000]] if code == 0 else []
            info[key] = {r[1] for r in rows if len(r) > 1}
    ctx["ff"] = info
    return info


def validation_layer(ctx):
    if "layer" not in ctx:
        code, text, _ = ctx["run"](["vulkaninfo"], 120, ctx["env"])
        ctx["layer"] = code == 0 and VK_LAYER in text
    return ctx["layer"]


def gpu_prologue(ctx, need_layer=False):
    """Common checks; returns an outcome to stop with, or None to go on."""
    mode = ctx["env"].get("PELORUS_VALIDATE", "auto")
    layer = validation_layer(ctx) if mode != "0" else False
    status, reason = validation_decision(mode, layer, need_layer, ctx["disabled"])
    if status:
        return outcome(status, reason)
    info = ffmpeg_info(ctx)
    if not info["bin"]:
        return outcome("not_run", "ffmpeg not found (set FFMPEG_BIN)")
    if "pelorus_analyze_vulkan" not in info["filters"]:
        return outcome("not_run", "this FFmpeg has no Pelorus filters; patched build required")
    return None


def device_indices(ctx):
    forced = ctx["env"].get("VULKAN_DEVICE", "")
    if forced.isdigit():
        return [int(forced)]
    hw = [i for i, d in enumerate(ctx["devices"]) if ctx["is_hardware"](d)]
    return hw[:MAX_DEVICES]


def ffrun(ctx, argv, timeout_s=ENC_TIMEOUT_S):
    """Run FFmpeg. With PELORUS_VALIDATE=1 the layer is on and every VUID is gated."""
    env = dict(ctx["env"])
    validating = env.get("PELORUS_VALIDATE") == "1"
    if validating:
        env["VK_INSTANCE_LAYERS"] = VK_LAYER
    code, text, err = ctx["run"]([ffmpeg_info(ctx)["bin"]] + argv, timeout_s, env)
    if validating:
        bad, ok = vuid_gate(text, load_allowlist(ctx), ctx["disabled"])
        ctx.setdefault("vuids", set()).update(bad)
        ctx.setdefault("vuid_hits", set()).update(ok)
    return code, text, err


def finish(ctx, result):
    """Gate Vulkan validation messages: an unlisted VUID fails the stage.

    A listed VUID passes through the shared allow-list and the reason names the
    entry (VUID, reference, expiry)."""
    bad, hits = ctx.pop("vuids", set()), ctx.pop("vuid_hits", set())
    status, reason, log, legs = result
    if bad and status != "no_device" and "vuid_gate_every_stage" not in ctx["disabled"]:
        return outcome("fail", ("%s; unlisted Vulkan validation VUIDs: %s" % (
            reason, ", ".join(sorted(bad)[:4])))[:900], log, legs)
    if hits:
        reason = "%s; allow-listed VUIDs: %s" % (reason, "; ".join(sorted(hits)[:3]))
    return outcome(status, reason[:900], log, legs)


def fixture_for(ctx, name):
    """(path, dims, outcome to stop with)."""
    fx = ctx["fixtures"]
    entries, err = fx.load_lock()
    entry = next((e for e in entries if e.get("name") == name), None)
    if err or entry is None:
        return None, None, outcome("incomplete", err or "fixture %s not in the lock" % name)
    cache = Path(ctx["env"].get("PELORUS_TESTER_CACHE") or fx.default_cache())
    run = lambda argv, t: ctx["run"](argv, t, ctx["env"])
    path, errs, absent = fx.ensure_offline(entry, cache, ffmpeg_info(ctx)["bin"], run)
    if path is None:
        return None, None, outcome("not_run" if absent else "fail", "; ".join(errs))
    return str(path), entry, None


def input_args(entry, path):
    return ["-f", "rawvideo", "-pix_fmt", entry["pixfmt"], "-s",
            "%dx%d" % (entry["width"], entry["height"]), "-framerate",
            str(entry["fps"]), "-i", path]


def chain_for(enc, steered=True):
    head = "format=%s,hwupload,%s" % (enc["pix"], FILTER_CHAIN if steered else CONTROL_CHAIN)
    if enc["kind"] == "vulkan":
        return head
    return "%s,hwdownload,format=%s" % (head, enc["pix"])


def encode(ctx, enc, dev, entry, src, frames, steered, out):
    """One encode; returns (ok, error line, log text)."""
    argv = ["-hide_banner", "-loglevel", "verbose" if steered else "error", "-y",
            "-init_hw_device", "vulkan=vk:%d" % dev, "-filter_hw_device", "vk"]
    argv += input_args(entry, src) + ["-frames:v", str(frames), "-vf", chain_for(enc, steered)]
    argv += ["-c:v", enc["name"]] + enc["args"]
    argv += ["-pelorus_roi", "1"] if steered else []
    argv += MUX_ARGS + [str(out)]
    code, text, err = ffrun(ctx, argv)
    ok = code == 0 and Path(out).is_file() and Path(out).stat().st_size > 0
    return ok, err or first_line(text), text


def decoded_frames(ctx, stream, entry, out_raw):
    """Decode a stream to raw yuv420p; returns (frame count or -1, decoder error line)."""
    argv = ["-hide_banner", "-loglevel", "error", "-y", "-i", str(stream), "-f",
            "rawvideo", "-pix_fmt", "yuv420p", str(out_raw)]
    code, text, err = ffrun(ctx, argv)
    failed = [l.strip() for l in text.splitlines() if re.search(r"(?i)fail|corrupt|error|invalid", l)]
    line = (failed[0] if failed else first_line(text))[:160]
    if code != 0 or not Path(out_raw).is_file():
        return -1, err or line
    count = frames_from_size(Path(out_raw).stat().st_size, entry["width"], entry["height"])
    return count, line if count < 0 or failed else ""


def stage_dir(ctx, sid):
    d = Path(ctx["work"]) / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------ format matrix

def run_format_matrix(ctx, spec):
    stop = gpu_prologue(ctx, need_layer=True)
    if stop:
        return stop
    script = Path(ctx["env"].get("PELORUS_FORMAT_MATRIX") or
                  REPO_ROOT / "ffmpeg-patches" / "test" / "vulkan-format-matrix.sh")
    bash = shutil.which("bash")
    if not script.is_file() or not bash:
        return outcome("not_run", "format matrix script or bash not found: %s" % script.name)
    env = dict(ctx["env"], FFMPEG_BIN=ffmpeg_info(ctx)["bin"],
               OUTPUT_ROOT=str(stage_dir(ctx, "format_matrix")))
    devs = device_indices(ctx)
    if devs and "VULKAN_DEVICE" not in env:
        env["VULKAN_DEVICE"] = str(devs[0])
    code, text, err = ctx["run"]([bash, str(script)], MATRIX_TIMEOUT_S, env)
    if err:
        return outcome("incomplete", err, text)
    if code == 77:
        return outcome("no_device", "matrix script found no usable Vulkan device; " +
                       ctx.get("no_device_reason", "start the container with a GPU device"), text)
    mode = env.get("PELORUS_VALIDATE", "auto")
    note = "validation off (PELORUS_VALIDATE=0)" if mode == "0" else "validation layer on"
    if code != 0:
        return outcome("fail", "format matrix exit code %s" % code, text)
    return outcome("pass", "format matrix passed on device %s; %s" % (
        env.get("VULKAN_DEVICE", "default"), note), text)


# ----------------------------------------------------------- steering smoke

def steer_one(ctx, enc, devs, entry, src, work):
    """Run the five encodes of one encoder; returns (state, note)."""
    dev, outs, logs, why, text = None, {}, [], "no device", ""
    plan = (("none8", 8, False), ("none8b", 8, False), ("roi8", 8, True),
            ("none16", 16, False), ("roi16", 16, True))
    for dev_try in (devs if enc["kind"] == "vulkan" else devs[:1]):
        ok, why, text = encode(ctx, enc, dev_try, entry, src, 8, False, work / "probe.mkv")
        if ok:
            dev = dev_try
            break
    if dev is None:
        return free_driver_leg(ctx, enc, *unusable(enc, text, why))
    if not encoder_has_option(ctx, enc["name"], STEER_OPTION):
        return "not_run", ("%s encodes on device %d but has no -%s option in this FFmpeg; "
                           "the patch stack does not steer it" % (enc["name"], dev, STEER_OPTION))
    for key, frames, steered in plan:
        outs[key] = work / ("%s-%s.mkv" % (enc["name"], key))
        ok, why, text = encode(ctx, enc, dev, entry, src, frames, steered, outs[key])
        logs.append(text if steered else "")
        if not ok:
            return "fail", "%s encode failed: %s" % (key, why)
    return steer_judge(ctx, outs, "\n".join(logs), entry, work, dev)


def steer_judge(ctx, outs, notes, entry, work, dev):
    try:
        hashes = {k: digest(p) for k, p in outs.items()}
    except OSError as exc:
        return "fail", "cannot read an encoder output: %s" % exc.strerror
    decoded = {k: decoded_frames(ctx, p, entry, work / (k + ".raw"))
               for k, p in outs.items() if k != "none8b"}
    counts = {k: n for k, (n, _) in decoded.items()}
    errs = {k: e for k, (_, e) in decoded.items()}
    state, why = steering_verdict(hashes, counts, notes, ctx["disabled"], errs)
    return state, "%s on device %d" % (why, dev) if state == "pass" else why


def run_steering(ctx, spec):
    stop = gpu_prologue(ctx)
    if stop:
        return stop
    src, entry, stop = fixture_for(ctx, "synth-banding")
    if stop:
        return stop
    devs, work, results = device_indices(ctx), stage_dir(ctx, "steering_smoke"), []
    have = ffmpeg_info(ctx)["encoders"]
    for enc in ENCODERS:
        if enc["name"] not in have:
            results.append((enc["name"], enc["codec"], "not_run", "not built into this FFmpeg"))
        elif devs:
            results.append((enc["name"], enc["codec"]) + steer_one(ctx, enc, devs, entry, src, work))
    if devs:
        results += brc_legs(ctx, devs[0], work)
    return finish(ctx, aggregate_legs(results, ctx["disabled"]))


# ------------------------------------------------- QSV bitrate-control legs

def brc_legs(ctx, dev, work):
    """cbr_extbrc and cbr_hw legs for each QSV encoder this FFmpeg has (ADR-0180 decision 8)."""
    have = ffmpeg_info(ctx)["encoders"]
    qsv = [e for e in ENCODERS if e["name"].endswith("_qsv") and e["name"] in have]
    if not qsv:
        return []
    src, entry, stop = fixture_for(ctx, BRC_FIXTURE)
    results = []
    for enc in qsv:
        for mode, leg in (("cbr_extbrc", extbrc_leg), ("cbr_hw", hw_brc_leg)):
            if stop:
                state = "fail" if stop[0] == "fail" else "not_run"
                results.append((enc["name"], enc["codec"], state, stop[1], mode))
            else:
                results.append((enc["name"], enc["codec"]) + leg(ctx, enc, dev, entry, src, work) + (mode,))
    return results


def brc_encode(ctx, enc, dev, entry, src, out, rate_args):
    """One encode of the looped bitrate fixture through pelorus_deband_vulkan; (ok, why, log)."""
    frames = entry["frames"] * (BRC_LOOPS + 1)
    argv = ["-hide_banner", "-loglevel", "verbose", "-y", "-init_hw_device", "vulkan=vk:%d" % dev,
            "-filter_hw_device", "vk", "-stream_loop", str(BRC_LOOPS)] + input_args(entry, src)
    argv += ["-frames:v", str(frames), "-vf", BRC_CHAIN, "-c:v", enc["name"]] + rate_args
    argv += ["-f", CODECS[enc["codec"]], str(out)]
    code, text, err = ffrun(ctx, argv)
    ok = code == 0 and Path(out).is_file() and Path(out).stat().st_size > 0
    return ok, err or (encoder_problem_line(text, enc["name"]) if text else "no output"), text


def decoded_count(ctx, stream):
    """Frames FFmpeg decodes from `stream` (framecrc lines): (count or -1, first error line)."""
    code, text, err = ffrun(ctx, ["-hide_banner", "-loglevel", "error", "-i", str(stream),
                                  "-f", "framecrc", "-"])
    lines = text.splitlines()[:100000]
    count = sum(1 for l in lines if re.match(r"^0,\s*-?\d+,", l))
    errors = [l.strip() for l in lines if l.strip() and not l.startswith(("0,", "#"))]
    if code != 0:
        return -1, (err or (errors[0] if errors else first_line(text)))[:160]
    return count, (errors[0] if errors else "")[:160]


def brc_judge(ctx, entry, out):
    """Bitrate and decode verdict of an encoded bitrate-control stream."""
    decoded, why = decoded_count(ctx, out)
    state, note = brc_verdict(Path(out).stat().st_size, decoded, entry["frames"] * (BRC_LOOPS + 1),
                              entry["fps"], ctx["disabled"])
    return state, ("%s; decoder: %s" % (note, why) if why and state == "fail" else note)


def brc_baseline(ctx, enc, dev, entry, src, work):
    """The encoder's constant-QP encode of the same input, (ok, why, log); cached per encoder."""
    cache = ctx.setdefault("brc_baseline", {})
    if enc["name"] not in cache:
        out = work / ("brc-%s-cqp.%s" % (enc["name"], CODECS[enc["codec"]]))
        cache[enc["name"]] = brc_encode(ctx, enc, dev, entry, src, out, enc["args"])
    return cache[enc["name"]]


def extbrc_leg(ctx, enc, dev, entry, src, work):
    """CBR with -extbrc 1: oneVPL's software bitrate control."""
    if enc["codec"] == "av1":
        return "not_run", AV1_EXTBRC_REASON
    if not encoder_has_option(ctx, enc["name"], "extbrc"):
        return "not_run", "%s has no -extbrc option in this FFmpeg" % enc["name"]
    out = work / ("brc-%s-extbrc.%s" % (enc["name"], CODECS[enc["codec"]]))
    ok, why, text = brc_encode(ctx, enc, dev, entry, src, out, BRC_RATE + ["-extbrc", "1"])
    if not ok:
        base_ok, base_why, _ = brc_baseline(ctx, enc, dev, entry, src, work)
        if not base_ok:
            return free_driver_leg(ctx, enc, *unusable(enc, "", base_why))
        return "fail", "CBR with -extbrc 1 fails while constant QP encodes: " + why
    errs = extbrc_problems(text, ctx["disabled"])
    return ("fail", errs[0]) if errs else brc_judge(ctx, entry, out)


def hw_brc_leg(ctx, enc, dev, entry, src, work):
    """CBR with the hardware's own bitrate control."""
    out = work / ("brc-%s-hw.%s" % (enc["name"], CODECS[enc["codec"]]))
    ok, why, _ = brc_encode(ctx, enc, dev, entry, src, out, BRC_RATE)
    if ok:
        return brc_judge(ctx, entry, out)
    base_ok, base_why, _ = brc_baseline(ctx, enc, dev, entry, src, work)
    if not base_ok:
        return free_driver_leg(ctx, enc, *unusable(enc, "", base_why))
    if "intel_host" not in ctx:
        ctx["intel_host"] = intel_render_host()
    return hw_brc_failure(why, ctx["intel_host"], ctx["disabled"])


# ------------------------------------------------------ side-data round trip

def run_sidedata(ctx, spec):
    stop = gpu_prologue(ctx)
    if stop:
        return stop
    src, entry, stop = fixture_for(ctx, "synth-banding")
    if stop:
        return stop
    work, devs = stage_dir(ctx, "sidedata_roundtrip"), device_indices(ctx)
    have, results = ffmpeg_info(ctx)["encoders"], []
    for car in SEI_CARRIERS:
        if car["name"] not in have:
            results.append((car["name"], car["codec"], "not_run", "not built into this FFmpeg"))
        elif devs:
            results.append((car["name"], car["codec"]) + carrier_roundtrip(ctx, car, devs, entry, src, work))
    status, reason, log, legs = aggregate_legs(results, ctx["disabled"])
    if status == "not_run":
        reason = "no encoder carries SEI unregistered here; " + reason
    return finish(ctx, outcome(status, reason, log, legs))


def carrier_encode(ctx, car, devs, entry, src, out, sidedata=True):
    """Encode on the first device that works; returns (dev, why).

    With sidedata the analyze filter runs and -udu_sei 1 is set; without, the
    baseline keeps the same input, deband and encoder but carries neither."""
    head = "format=nv12,hwupload," + ("pelorus_analyze_vulkan," if sidedata else "") \
        + "pelorus_deband_vulkan"
    chain = head if car["kind"] == "vulkan" else head + ",hwdownload,format=nv12"
    why = "no device"
    for dev in (devs if car["kind"] == "vulkan" else devs[:1]):
        argv = ["-hide_banner", "-loglevel", "error", "-y", "-init_hw_device",
                "vulkan=vk:%d" % dev, "-filter_hw_device", "vk"]
        argv += input_args(entry, src) + ["-frames:v", str(SIDEDATA_FRAMES), "-vf", chain]
        argv += ["-c:v", car["name"]] + car["args"] + (["-udu_sei", "1"] if sidedata else [])
        argv += ["-f", car["codec"], str(out)]
        code, text, err = ffrun(ctx, argv)
        if code == 0 and Path(out).is_file() and Path(out).stat().st_size > 0:
            return dev, ""
        why = (encoder_error_line(text, car["name"]) if text else err)[:160] or first_line(text)[:160]
    return None, why


def carrier_failure(ctx, car, devs, entry, src, work, why):
    """Classify a failed side-data encode against a baseline encode of the same input.

    not_run only when the baseline fails too (the host cannot encode); a baseline
    that encodes makes the side data the cause, which is a fail (ADR-0173)."""
    if "sd_carrier_masking" in ctx["disabled"]:
        return free_driver_leg(ctx, car, "not_run", "cannot encode on this host: " + why)
    base = work / ("sd-base-%s.%s" % (car["name"], car["codec"]))
    dev, base_why = carrier_encode(ctx, car, devs, entry, src, base, sidedata=False)
    if dev is None:
        return free_driver_leg(ctx, car, "not_run", "cannot encode on this host: " + base_why)
    return "fail", ("%s encodes without the side data but fails with it (device %d): %s" % (
        car["name"], dev, why))[:400]


def carrier_roundtrip(ctx, car, devs, entry, src, work):
    """Encode, read the blobs back from the stream, then through the decode tap."""
    out = work / ("sd-%s.%s" % (car["name"], car["codec"]))
    dev, why = carrier_encode(ctx, car, devs, entry, src, out)
    if dev is None:
        return carrier_failure(ctx, car, devs, entry, src, work, why)
    frames = pelorus_blobs_per_frame(out.read_bytes(), car["codec"])
    errs = sidedata_problems(frames, SIDEDATA_FRAMES, ctx["disabled"])
    code, tap, err = ffrun(ctx, ["-hide_banner", "-loglevel", "info", "-i", str(out),
                                 "-vf", "showinfo", "-f", "null", "-"])
    errs += decode_tap_problems(tap, SIDEDATA_FRAMES, ctx["disabled"]) if code == 0 \
        else ["decode of the encoded stream failed: " + (err or first_line(tap))]
    if errs:
        return "fail", "; ".join(errs[:3])
    return "pass", "%d frames, blob in stream and decode tap, device %d" % (SIDEDATA_FRAMES, dev)


# --------------------------------------------------------------- zero-copy

def find_native_pair(ctx, entry, src, work):
    """First (device, encoder, decoded-ok stream) where Vulkan encodes and decodes."""
    have = ffmpeg_info(ctx)["encoders"]
    for dev in device_indices(ctx):
        for enc in ENCODERS:
            if enc["kind"] != "vulkan" or enc["name"] not in have:
                continue
            stream = work / ("native-%s-%d.mkv" % (enc["name"], dev))
            ok, _, _ = encode(ctx, enc, dev, entry, src, 4, False, stream)
            if ok and native_run(ctx, enc, dev, stream, work, 1)[0] == 0:
                return dev, enc, stream
    return None


def native_run(ctx, enc, dev, stream, work, frames):
    """Vulkan decode -> Pelorus filters -> Vulkan encode with a debug graph dump."""
    argv = ["-hide_banner", "-loglevel", "debug", "-y", "-init_hw_device",
            "vulkan=vk:%d" % dev, "-filter_hw_device", "vk", "-hwaccel", "vulkan",
            "-hwaccel_device", "vk", "-hwaccel_output_format", "vulkan", "-i", str(stream),
            "-frames:v", str(frames), "-vf", ",".join(NATIVE_FILTERS), "-c:v", enc["name"]]
    argv += enc["args"] + MUX_ARGS + [str(work / "native-out.mkv")]
    code, text, _ = ffrun(ctx, argv)
    return code, text


def sw_leg(ctx, dev, entry, src, work):
    """Software source -> hwupload -> Pelorus filters -> hwdownload -> software encoder."""
    have = ffmpeg_info(ctx)["encoders"]
    enc = next((e for e in ENCODERS if e["kind"] == "sw" and e["name"] in have), None)
    if enc is None:
        return "skip", "no software encoder built in"
    chain = "format=yuv420p,hwupload,%s,hwdownload,format=yuv420p" % ",".join(NATIVE_FILTERS)
    argv = ["-hide_banner", "-loglevel", "debug", "-y", "-init_hw_device",
            "vulkan=vk:%d" % dev, "-filter_hw_device", "vk"]
    argv += input_args(entry, src) + ["-frames:v", "2", "-vf", chain, "-c:v", enc["name"]]
    argv += enc["args"] + MUX_ARGS + [str(work / "sw-out.mkv")]
    code, text, err = ffrun(ctx, argv)
    if code != 0:
        return "fail", "software-encoder leg did not run: " + (err or first_line(text))
    errs = graph_problems(text, True, True, NATIVE_FILTERS, ctx["disabled"])
    return ("fail", "; ".join(errs)) if errs else ("pass", "one hwupload, one hwdownload only")


def run_zero_copy(ctx, spec):
    stop = gpu_prologue(ctx)
    if stop:
        return stop
    src, entry, stop = fixture_for(ctx, "synth-banding")
    if stop:
        return stop
    work, devs = stage_dir(ctx, "zero_copy_chain"), device_indices(ctx)
    if not devs:
        return outcome("no_device", "no hardware Vulkan device index known; " +
                       ctx.get("no_device_reason", "start the container with a GPU device"))
    sw_state, sw_why = sw_leg(ctx, devs[0], entry, src, work)
    pair = find_native_pair(ctx, entry, src, work)
    if pair is None:
        status = "fail" if sw_state == "fail" else "not_run"
        return finish(ctx, outcome(status, "native leg (Vulkan decode -> Pelorus -> Vulkan "
                                   "encode) not run: no device with both; software-encoder "
                                   "leg %s: %s" % (sw_state, sw_why)))
    dev, enc, stream = pair
    code, text = native_run(ctx, enc, dev, stream, work, 4)
    errs = graph_problems(text, False, False, NATIVE_FILTERS, ctx["disabled"]) if code == 0 \
        else ["native leg exit code %s" % code]
    if errs or sw_state == "fail":
        return finish(ctx, outcome("fail", "native: %s; software-encoder leg %s: %s" % (
            "; ".join(errs) or "ok", sw_state, sw_why)))
    return finish(ctx, outcome("pass", "native %s on device %d has no hwupload, hwdownload "
                               "or scale; software-encoder leg %s" % (enc["name"], dev, sw_state)))


# ------------------------------------------------------------------- bench

def run_bench(ctx, spec):
    stop = gpu_prologue(ctx)
    if stop:
        return stop
    script = REPO_ROOT / "scripts" / "bench" / "run-bench.py"
    vmaf = shutil.which(ctx["env"].get("VMAF_BIN", "vmaf"))
    have = ffmpeg_info(ctx)["encoders"]
    enc = next((e for e in ("hevc_nvenc", "av1_nvenc") if e in have), None)
    missing = [n for n, ok in (("run-bench.py", script.is_file()), ("vmaf binary", vmaf),
                               ("hevc_nvenc or av1_nvenc", enc)) if not ok]
    if missing:
        return outcome("not_run", "bench needs: " + ", ".join(missing))
    src, entry, stop = fixture_for(ctx, "synth-banding")
    if stop:
        return stop
    work, devs = stage_dir(ctx, "bench"), device_indices(ctx)
    argv = [sys.executable, "-I", str(script), "--ffmpeg", ffmpeg_info(ctx)["bin"],
            "--vmaf", vmaf, "--src", src, "--width", str(entry["width"]), "--height",
            str(entry["height"]), "--pixfmt", entry["pixfmt"], "--frames", str(entry["frames"]),
            "--fps", str(entry["fps"]), "--encoder", enc, "--preset", "p5", "--filter",
            "pelorus_deband_vulkan=range=15:thry=0.012", "--device", "vk:%d" % (devs or [0])[0],
            "--cq", "28", "34", "40", "46", "--out", str(work)]
    code, text, err = ctx["run"](argv, BENCH_TIMEOUT_S, ctx["env"])
    result = work / "result.json"
    if err or code != 0 or not result.is_file():
        return outcome("fail", "non-gating: bench did not finish: %s" % (err or "exit %s" % code), text)
    return outcome("pass", "non-gating performance data for %s, 4-point ladder; "
                   "see log tail" % enc, text)


RUNNERS = {
    "format_matrix": run_format_matrix,
    "steering_smoke": run_steering,
    "sidedata_roundtrip": run_sidedata,
    "zero_copy_chain": run_zero_copy,
    "bench": run_bench,
}


# --------------------------------------------------------------- self-test

CLEAN_GRAPH = """\
Filter 'Parsed_pelorus_deband_vulkan_0' formats:
Filter 'Parsed_pelorus_denoise_vulkan_1' formats:
Filter 'Parsed_pelorus_mc_vulkan_2' formats:
Filter 'graph -1 input from stream 0:0' formats:
Filter 'out_#0:0' formats:
Filter 'format' formats:
"""
PLANTED_DOWNLOAD = CLEAN_GRAPH + "Filter 'Parsed_hwdownload_3' formats:\n"
PLANTED_UPLOAD = CLEAN_GRAPH + "Filter 'Parsed_hwupload_3' formats:\n"
# Captured from a real run: hwdownload, format, hwupload between two Pelorus filters.
PLANTED_PAIR = CLEAN_GRAPH.replace(
    "Filter 'Parsed_pelorus_denoise_vulkan_1' formats:",
    "Filter 'Parsed_hwdownload_1' formats:\nFilter 'Parsed_format_2' formats:\n"
    "Filter 'Parsed_hwupload_3' formats:\nFilter 'Parsed_pelorus_denoise_vulkan_4' formats:")
PLANTED_SCALE = CLEAN_GRAPH + (
    "[format @ 0x1] auto-inserting filter 'auto_scale_1' between the filter "
    "'Parsed_pelorus_mc_vulkan_2' and the filter 'format'\n")
SW_GRAPH = ("Filter 'Parsed_format_0' formats:\nFilter 'Parsed_hwupload_1' formats:\n"
            + CLEAN_GRAPH + "Filter 'Parsed_hwdownload_3' formats:\n")


def pack_blob(mask=3, pts=0, total=None, major=1):
    secs = ((1, 80, 8, 3), (2, 88, 8, 3))
    size = 96 if total is None else total
    head = struct.pack(HEADER_FMT, PELORUS_MAGIC, major, 3, size, mask, len(secs),
                       HEADER_BYTES, pts)
    dirs = b"".join(struct.pack(DIR_FMT, *sec) for sec in secs)
    return head + bytes(16) + dirs + bytes(16)


def stage_ctx(env, run):
    return {"env": env, "run": run, "disabled": frozenset(), "devices": [],
            "is_hardware": lambda d: True}


def no_layer_run(argv, timeout_s, env=None):
    return (1, "", "command not found: " + argv[0]) if argv[0] == "vulkaninfo" else (0, "", "")


def self_test_graph(expect, disabled):
    def ok(log, sw=(False, False)):
        return not graph_problems(log, sw[0], sw[1], NATIVE_FILTERS, disabled)

    expect("zc_clean_graph_passes", ok(CLEAN_GRAPH))
    expect("zc_sw_legs_pass", ok(SW_GRAPH, (True, True)))
    expect("zc_rejects_hwdownload", not ok(PLANTED_DOWNLOAD))
    expect("zc_rejects_hwupload", not ok(PLANTED_UPLOAD))
    expect("zc_rejects_midchain_pair", not ok(PLANTED_PAIR))
    expect("zc_rejects_second_download_in_sw_leg", not ok(
        SW_GRAPH + "Filter 'Parsed_hwdownload_9' formats:\n", (True, True)))
    expect("zc_rejects_scale", not ok(PLANTED_SCALE))
    expect("zc_rejects_empty_graph", not ok(""))
    expect("zc_rejects_missing_filters", not ok("Filter 'Parsed_format_0' formats:\n"))


def self_test_steering(expect, disabled):
    base = {"none8": "a", "none8b": "a", "roi8": "b", "none16": "c", "roi16": "d"}
    good = {"none8": 8, "roi8": 8, "none16": 16, "roi16": 16}
    expect("steering_good_passes", steering_verdict(base, good, "", disabled)[0] == "pass")
    flat = dict(base, roi16="c")
    expect("steering_rejects_no_effect", steering_verdict(flat, good, "", disabled)[0] == "fail")
    short = dict(good, roi16=15)
    expect("steering_rejects_short_decode", steering_verdict(base, short, "", disabled)[0] == "fail")
    noisy = dict(base, none8b="z")
    expect("steering_flags_nondeterministic", steering_verdict(noisy, good, "", disabled)[0] == "not_run")
    said = "Pelorus ROI: AOME_SET_ROI_MAP failed (res=8); continuing without ROI bias."
    expect("steering_selfreport_not_run", steering_verdict(flat, good, said, disabled)[0] == "not_run")
    expect("steering_silent_noop_fails", steering_verdict(flat, good, "all fine", disabled)[0] == "fail")
    passthrough = ("[h264_vulkan @ 0x1] pelorus_roi: device does not enable "
                   "VK_KHR_video_encode_quantization_map; QP-map steering disabled (pass-through).")
    got = steering_verdict(flat, good, passthrough, disabled)
    expect("steering_passthrough_selfreport_not_run", got[0] == "not_run" and "pass-through" in got[1])
    broken = dict(good, none8=-1, none16=-1)
    got = steering_verdict(base, broken, "", disabled, {"none8": "Corrupt frame detected"})
    expect("steering_broken_baseline_is_named_not_run", got[0] == "not_run"
           and "baseline output does not decode (encoder/driver defect)" in got[1]
           and "Corrupt frame detected" in got[1])
    steered_only = dict(good, roi8=-1)
    expect("steering_broken_steered_still_fails",
           steering_verdict(base, steered_only, "", disabled)[0] == "fail")
    expect("encoders_none_is_not_run", aggregate_legs(
        [("x", "h264", "not_run", "absent")], disabled)[0] == "not_run")
    expect("encoders_failure_fails", aggregate_legs(
        [("x", "h264", "pass", ""), ("y", "hevc", "fail", "bad")], disabled)[0] == "fail")


def self_test_sidedata(expect, disabled):
    good = [[pack_blob(pts=i)] for i in range(4)]
    expect("sd_good_passes", not sidedata_problems(good, 4, disabled))
    expect("sd_rejects_missing_blob", bool(sidedata_problems(good[:3] + [[]], 4, disabled)))
    expect("sd_rejects_bad_total", bool(sidedata_problems(
        [[pack_blob(pts=i, total=99)] for i in range(4)], 4, disabled)))
    expect("sd_rejects_bad_abi", bool(sidedata_problems(
        [[pack_blob(pts=i, major=2)] for i in range(4)], 4, disabled)))
    expect("sd_rejects_dup_pts", bool(sidedata_problems(
        [[pack_blob(pts=1)] for _ in range(4)], 4, disabled)))
    tap = "\n".join("[Parsed_showinfo_0 @ 0x1] n:   %d pts:  %d side data - H.26[45] User "
                    "Data Unregistered SEI message: UUID=e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64"
                    % (i, i) for i in range(4))
    expect("sd_tap_good_passes", not decode_tap_problems(tap, 4, disabled))
    expect("sd_tap_rejects_stripped", bool(decode_tap_problems(
        tap.replace("e1d7c4a2", "00000000"), 4, disabled)))
    sei = bytes([0x4E, 0x01, 5, 16 + 4]) + PELORUS_UUID + b"\xaa" * 4 + b"\x80"
    stream = b"\x00\x00\x01" + sei + b"\x00\x00\x01\x26\x01\xff"
    got = pelorus_blobs_per_frame(stream, "hevc")
    expect("sd_parser_reads_hevc_sei", got == [[b"\xaa" * 4]])


ALLOW_TEXT = """\
# comment
VkListed-thing-00001 | #214 | 2027-03-31
VkExpired-thing-00002 | #214 | 2026-01-01
VkNoRef-thing-00003 |  | 2027-03-31
VkBadRef-thing-00004 | maybe | 2027-03-31
"""
TODAY = datetime.date(2026, 10, 9)


def fake_gate_ctx(disabled, text):
    """A context whose FFmpeg 'prints' text; PELORUS_VALIDATE=1 with the layer on."""
    ctx = stage_ctx({"PELORUS_VALIDATE": "1"}, lambda argv, t, env=None: (0, text, ""))
    ctx.update(disabled=disabled, today=TODAY, allowlist=parse_allowlist(ALLOW_TEXT, TODAY, disabled))
    ctx["ff"] = {"bin": "ffmpeg", "filters": set(), "encoders": set()}
    return ctx


def self_test_vuids(expect, disabled):
    entries = parse_allowlist(ALLOW_TEXT, TODAY, disabled)
    log = "VUID-VkListed-thing-00001 VUID-VkExpired-thing-00002 VUID-VkNoRef-thing-00003 " \
          "VUID-VkBadRef-thing-00004 VUID-vkUnknown-x-00005"
    bad, hit = vuid_gate(log, entries, disabled)
    expect("vuid_listed_entry_named", hit == ["VkListed-thing-00001 | #214 | 2027-03-31"])
    expect("vuid_unknown_is_unlisted", "VUID-vkUnknown-x-00005" in bad)
    expect("vuid_expired_entry_is_unlisted", "VUID-VkExpired-thing-00002" in bad)
    expect("vuid_entry_without_reference_is_unlisted", "VUID-VkNoRef-thing-00003" in bad)
    expect("vuid_bad_reference_is_unlisted", "VUID-VkBadRef-thing-00004" in bad)
    ctx = fake_gate_ctx(disabled, "VUID-vkUnknown-x-00005")
    ffrun(ctx, [])
    expect("stage_fails_on_unlisted_vuid", finish(ctx, outcome("pass", "ok"))[0] == "fail")
    ctx = fake_gate_ctx(disabled, "VUID-VkListed-thing-00001")
    ffrun(ctx, [])
    done = finish(ctx, outcome("pass", "ok"))
    expect("stage_passes_listed_vuid_and_names_it",
           done[0] == "pass" and "VkListed-thing-00001 | #214 | 2027-03-31" in done[1])
    expect("shared_allowlist_file_parses", bool(parse_allowlist(
        ALLOWLIST_PATH.read_text(encoding="utf-8"), datetime.date.today(), disabled)))


def self_test_gates(expect, disabled):
    expect("validate_1_absent_fails_closed", validation_decision(
        "1", False, False, disabled)[0] == "fail")
    expect("validate_auto_absent_not_run", validation_decision(
        "auto", False, True, disabled)[0] == "not_run")
    expect("validate_0_runs", validation_decision("0", False, True, disabled)[0] is None)
    ctx = stage_ctx({"PELORUS_VALIDATE": "1", "FFMPEG_BIN": "/nonexistent"}, no_layer_run)
    ctx["disabled"] = disabled
    expect("stage_validate_1_fails_closed", run_format_matrix(ctx, {})[0] == "fail")
    ctx = stage_ctx({"PELORUS_VALIDATE": "auto", "FFMPEG_BIN": "/nonexistent"}, no_layer_run)
    ctx["disabled"] = disabled
    expect("stage_no_layer_not_run", run_format_matrix(ctx, {})[0] == "not_run")
    ctx = stage_ctx({"PELORUS_VALIDATE": "0", "FFMPEG_BIN": "/nonexistent"}, no_layer_run)
    expect("stage_no_ffmpeg_not_run", run_steering(ctx, {})[0] == "not_run")
    self_test_vuids(expect, disabled)


NO_AV1_LOG = ("[av1_nvenc @ 0x1] Codec not supported\n"
              "[av1_nvenc @ 0x1] No capable devices found\n"
              "[vost#0:0/av1_nvenc @ 0x2] Error while opening encoder\nConversion failed!\n")


def fake_encode_ctx(disabled, work):
    """FFmpeg stand-in: av1_nvenc fails as on a GPU without AV1 NVENC; the rest encode."""
    def run(argv, timeout_s, env=None):
        if "av1_nvenc" in argv:
            return 1, NO_AV1_LOG, ""
        Path(argv[-1]).write_bytes(b"\x00" * 64)
        return 0, "", ""
    ctx = stage_ctx({"PELORUS_VALIDATE": "0"}, run)
    ctx.update(disabled=disabled, work=str(work))
    ctx["ff"] = {"bin": "ffmpeg", "filters": {"pelorus_analyze_vulkan"},
                 "encoders": {"av1_nvenc", "av1_qsv"}, "help": {"av1_qsv": "no steering here"}}
    return ctx


def fake_qsv_ctx(work, dpkg_text):
    """FFmpeg stand-in where hevc_qsv fails as with the free driver on an A380 under xe without HuC."""
    def run(argv, timeout_s, env=None):
        if argv[0] == "dpkg-query":
            return 1, dpkg_text, ""
        if "hevc_qsv" in argv:
            return 183, "[hevc_qsv @ 0x1] Invalid FrameType:0.\n", ""
        Path(argv[-1]).write_bytes(b"\x00" * 64)
        return 0, "", ""
    ctx = stage_ctx({"PELORUS_VALIDATE": "0"}, run)
    ctx.update(work=str(work))
    ctx["ff"] = {"bin": "ffmpeg", "filters": {"pelorus_analyze_vulkan"}, "encoders": {"hevc_qsv"},
                 "help": {}}
    return ctx


def self_test_free_driver(expect, disabled, entry):
    """hevc_qsv with only the free media driver names the non-free one; with it, it does not."""
    import tempfile
    enc = next(e for e in ENCODERS if e["name"] == "hevc_qsv")
    free = "intel-media-va-driver 25.2.3+dfsg1-1 ii \ndpkg-query: no packages found matching x\n"
    both = free + "intel-media-va-driver-non-free 25.2.3+ds1-1 ii \n"
    with tempfile.TemporaryDirectory(prefix="pelorus-qsv-") as tmp:
        got = steer_one(fake_qsv_ctx(tmp, free), enc, [0], entry, "src", Path(tmp))
        expect("free_driver_leg_names_nonfree", got[0] == "not_run"
               and "may need intel-media-va-driver-non-free" in got[1]
               and "Invalid FrameType:0" in got[1])
        got = steer_one(fake_qsv_ctx(tmp, both), enc, [0], entry, "src", Path(tmp))
        expect("nonfree_installed_no_driver_claim", got[0] == "not_run"
               and "intel-media-va-driver-non-free" not in got[1])
        got = steer_one(fake_qsv_ctx(tmp, ""), enc, [0], entry, "src", Path(tmp))
        expect("no_dpkg_no_driver_claim", got[0] == "not_run" and "free" not in got[1])


def self_test_legs(expect, disabled):
    """AV1 without hardware support, and an encoder without the steering option, are named not_run legs."""
    import tempfile
    entry = {"pixfmt": "yuv420p", "width": 16, "height": 16, "fps": 24}
    with tempfile.TemporaryDirectory(prefix="pelorus-legs-") as tmp:
        ctx = fake_encode_ctx(disabled, tmp)
        enc = {e["name"]: e for e in ENCODERS}
        got = steer_one(ctx, enc["av1_nvenc"], [0], entry, "src", Path(tmp))
        expect("av1_unsupported_leg_not_run", got[0] == "not_run" and "no AV1 encode" in got[1]
               and "No capable devices found" in got[1])
        got = steer_one(ctx, enc["av1_qsv"], [0], entry, "src", Path(tmp))
        expect("missing_steering_option_leg_not_run", got[0] == "not_run" and "-pelorus_roi" in got[1])
    vaapi = ("[AVHWDeviceContext @ 0x1] VAAPI profile not supported\n"
             "[av1_qsv @ 0x2] Failed to create a VAAPI device.\nConversion failed!\n")
    got = unusable({"name": "av1_qsv", "codec": "av1"}, vaapi, "Conversion failed!")
    expect("other_component_unsupported_is_not_av1", got == (
        "not_run", "not usable on this host: av1_qsv: Failed to create a VAAPI device."))
    expect("control_arm_has_no_roi", all(
        "roi=0" in chain_for(e, False) and "roi=1" in chain_for(e, True) for e in ENCODERS))
    self_test_free_driver(expect, disabled, entry)
    status, _, _, legs = aggregate_legs([("hevc_nvenc", "hevc", "pass", ""),
                                         ("av1_nvenc", "av1", "not_run", "no AV1 encode")], disabled)
    expect("legs_recorded", status == "pass" and [l["status"] for l in legs] == ["pass", "not_run"])


def self_test_carrier(expect, disabled):
    """A carrier whose baseline encodes but whose side-data encode fails is a fail, never not_run."""
    import tempfile
    entry = {"pixfmt": "yuv420p", "width": 16, "height": 16, "fps": 24}
    car = {"name": "hevc_nvenc", "codec": "hevc", "kind": "hw", "args": ["-qp", "30"]}
    enomem = "[hevc_nvenc @ 0x1] Cannot allocate memory\nConversion failed!\n"

    def stub(fail_when):
        def run(argv, timeout_s, env=None):
            if fail_when(argv):
                return 1, enomem, ""
            Path(argv[-1]).write_bytes(b"\x00" * 64)
            return 0, "", ""
        return run

    with tempfile.TemporaryDirectory(prefix="pelorus-carrier-") as tmp:
        ctx = fake_encode_ctx(disabled, tmp)
        ctx["env"] = {"PELORUS_VALIDATE": "0"}
        ctx["run"] = stub(lambda a: "-udu_sei" in a)
        got = carrier_roundtrip(ctx, car, [0], entry, "src", Path(tmp))
        expect("sd_carrier_sidedata_only_failure_fails", got[0] == "fail" and "Cannot allocate" in got[1])
        status, reason, _, legs = aggregate_legs([(car["name"], car["codec"]) + got], disabled)
        expect("sd_carrier_failure_fails_stage", status == "fail" and legs[0]["status"] == "fail")
        ctx["run"] = stub(lambda a: True)
        got = carrier_roundtrip(ctx, car, [0], entry, "src", Path(tmp))
        expect("sd_carrier_baseline_failure_not_run", got[0] == "not_run" and "Cannot allocate" in got[1])


def self_test_brc_rules(expect, disabled):
    """Bitrate-control pass rules: on target, the +-15 % boundary, decode, ExtBRC engagement."""
    full = lambda rate: int(rate * 16 / 8)   # bytes for `rate` bit/s over 400 frames at 25 fps
    expect("brc_on_target_passes", brc_verdict(full(1040000), 400, 400, 25, disabled)[0] == "pass")
    expect("brc_boundary_14_passes", brc_verdict(full(1140000), 400, 400, 25, disabled)[0] == "pass")
    over = brc_verdict(full(1160000), 400, 400, 25, disabled)
    expect("brc_rejects_16_over", over[0] == "fail" and "+16.0% off the 1000 kb/s target" in over[1])
    expect("brc_rejects_16_under", brc_verdict(full(840000), 400, 400, 25, disabled)[0] == "fail")
    expect("brc_rejects_short_decode", brc_verdict(full(1000000), 399, 400, 25, disabled)[0] == "fail")
    expect("brc_extbrc_on_passes", not extbrc_problems("[h264_qsv @ 0x1] MBBRC: OFF; ExtBRC: ON", disabled))
    expect("brc_rejects_extbrc_off", bool(extbrc_problems("[h264_qsv @ 0x1] ExtBRC: OFF", disabled)))
    expect("brc_rejects_extbrc_absent", bool(extbrc_problems("", disabled)))


def self_test_brc_failures(expect, disabled):
    """A failed hardware-CBR encode: named not_run for no HuC on DG2/xe and for a refusal, else fail."""
    hang = "hevc_qsv: Invalid FrameType:0."
    got = hw_brc_failure(hang, "xe-dg2", disabled)
    expect("brc_no_huc_named_not_run", got[0] == "not_run" and NO_HUC_REASON in got[1] and hang in got[1])
    for line in ("h264_qsv: Error during encoding: GPU Hang (-21)",
                 "av1_qsv: Error during encoding: device failed (-17)"):
        expect("brc_no_huc_status_" + line[:4], hw_brc_failure(line, "xe-dg2", disabled)[0] == "not_run")
    expect("brc_signature_elsewhere_fails", hw_brc_failure(hang, "other", disabled)[0] == "fail")
    got = hw_brc_failure(hang, "unknown", disabled)
    expect("brc_signature_unknown_host_unconfirmed", got[0] == "not_run" and "not confirmed" in got[1])
    got = hw_brc_failure("hevc_qsv: Selected ratecontrol mode is unsupported", "other", disabled)
    expect("brc_refusal_named_not_run", got[0] == "not_run" and "no hardware bitrate control" in got[1])
    expect("brc_other_failure_fails", hw_brc_failure(
        "hevc_qsv: Error during encoding: unknown error (-1)", "xe-dg2", disabled)[0] == "fail")


def self_test_render_host(expect):
    """intel_render_host reads the kernel driver and PCI id of the Intel render nodes."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pelorus-sys-") as tmp:
        def plant(name, vendor, device, driver):
            Path(tmp, "dev", "dri").mkdir(parents=True, exist_ok=True)
            Path(tmp, "dev", "dri", name).write_text("")
            base = Path(tmp, "sys", "class", "drm", name, "device")
            base.mkdir(parents=True, exist_ok=True)
            (base / "vendor").write_text(vendor + "\n")
            (base / "device").write_text(device + "\n")
            Path(tmp, "drivers", driver).mkdir(parents=True, exist_ok=True)
            link = base / "driver"
            if link.is_symlink():
                link.unlink()
            link.symlink_to(Path(tmp, "drivers", driver))
        host = lambda: intel_render_host(str(Path(tmp, "dev")), str(Path(tmp, "sys")))
        expect("render_host_no_dri_unknown", host() == "unknown")
        plant("renderD128", "0x10de", "0x2684", "nvidia")
        expect("render_host_no_intel_unknown", host() == "unknown")
        plant("renderD130", "0x8086", "0x56a5", "i915")
        expect("render_host_dg2_i915_other", host() == "other")
        plant("renderD130", "0x8086", "0xe20b", "xe")
        expect("render_host_bmg_xe_other", host() == "other")
        plant("renderD130", "0x8086", "0x56A5", "xe")
        expect("render_host_dg2_xe", host() == "xe-dg2")


def fake_brc_ctx(work, disabled, extbrc_rate, extbrc_state="ON"):
    """FFmpeg stand-in for hevc_qsv on a DG2 under xe: CQP and -extbrc 1 encode (the latter at
    `extbrc_rate` bit/s), hardware CBR fails as without HuC, decodes yield every frame."""
    def run(argv, timeout_s, env=None):
        if "framecrc" in argv:
            return 0, "".join("0, %d, %d, 1, 384, 0x0\n" % (i, i) for i in range(16)), ""
        if "-b:v" in argv and "-extbrc" not in argv:
            return 183, "[hevc_qsv @ 0x1] ExtBRC: OFF\n[hevc_qsv @ 0x1] Invalid FrameType:0.\n", ""
        size = int(extbrc_rate * 0.64 / 8) if "-extbrc" in argv else 64
        Path(argv[-1]).write_bytes(b"\x00" * size)
        return 0, "[hevc_qsv @ 0x1] BitrateLimit: unknown; MBBRC: OFF; ExtBRC: %s\n" % extbrc_state, ""
    ctx = stage_ctx({"PELORUS_VALIDATE": "0"}, run)
    ctx.update(disabled=disabled, work=str(work), intel_host="xe-dg2")
    ctx["ff"] = {"bin": "ffmpeg", "filters": {"pelorus_analyze_vulkan"}, "encoders": {"hevc_qsv"},
                 "help": {"hevc_qsv": "  -extbrc            <int>        E..V....... Extended bitrate control"}}
    return ctx


def self_test_brc_legs(expect, disabled):
    """End to end through the leg runners with a stand-in FFmpeg."""
    import tempfile
    entry = {"pixfmt": "yuv420p", "width": 16, "height": 16, "fps": 25, "frames": 2}
    enc = {e["name"]: e for e in ENCODERS}
    with tempfile.TemporaryDirectory(prefix="pelorus-brc-") as tmp:
        args = (enc["hevc_qsv"], 0, entry, "src", Path(tmp))
        good = extbrc_leg(fake_brc_ctx(tmp, disabled, 1030000), *args)
        expect("brc_leg_extbrc_on_target_passes", good[0] == "pass" and "+3.0%" in good[1])
        off = extbrc_leg(fake_brc_ctx(tmp, disabled, 1400000), *args)
        expect("brc_leg_extbrc_off_target_fails", off[0] == "fail" and "+40.0%" in off[1])
        dropped = extbrc_leg(fake_brc_ctx(tmp, disabled, 1000000, "OFF"), *args)
        expect("brc_leg_extbrc_not_engaged_fails", dropped[0] == "fail" and "ExtBRC: OFF" in dropped[1])
        hw = hw_brc_leg(fake_brc_ctx(tmp, disabled, 1000000), *args)
        expect("brc_leg_hw_no_huc_named_not_run", hw[0] == "not_run" and NO_HUC_REASON in hw[1]
               and "Invalid FrameType:0" in hw[1])
        av1 = extbrc_leg(fake_brc_ctx(tmp, disabled, 1000000), enc["av1_qsv"], *args[1:])
        expect("brc_leg_av1_extbrc_named_not_run", av1 == ("not_run", AV1_EXTBRC_REASON))
    status, _, _, legs = aggregate_legs([("hevc_qsv", "hevc", "pass", ""),
                                         ("hevc_qsv", "hevc") + off + ("cbr_extbrc",),
                                         ("hevc_qsv", "hevc") + hw + ("cbr_hw",)], disabled)
    expect("brc_failed_leg_fails_stage", status == "fail"
           and [l.get("rate_control") for l in legs] == [None, "cbr_extbrc", "cbr_hw"])
    status = aggregate_legs([("hevc_qsv", "hevc", "not_run", "no -pelorus_roi"),
                             ("hevc_qsv", "hevc") + good + ("cbr_extbrc",)], disabled)[0]
    expect("brc_pass_alone_is_not_a_steering_pass", status == "not_run")


def self_test(disabled=frozenset()):
    """Return failing check names; every rule has a planted bad case."""
    failures = []

    def expect(name, cond):
        if not cond:
            failures.append(name)

    self_test_graph(expect, disabled)
    self_test_steering(expect, disabled)
    self_test_sidedata(expect, disabled)
    self_test_gates(expect, disabled)
    self_test_legs(expect, disabled)
    self_test_carrier(expect, disabled)
    self_test_brc_rules(expect, disabled)
    self_test_brc_failures(expect, disabled)
    self_test_render_host(expect)
    self_test_brc_legs(expect, disabled)
    return failures
