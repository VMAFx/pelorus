#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Noisy-source benchmark result record emitter (ADR-0223, #131).

Emits benchmark result records conforming to scripts/bench/record.schema.json:
- Four mandatory arms: nvenc-tuned, nvenc-tflevel, nvenc-uhq, pelorus-denoise+nvenc-tuned
- Device identity from --device-name or nvidia-smi query (never invented)
- >= 4 CQ points per arm
- SSIMULACRA2 primary quality metric + VMAF secondary per rate point
- Reference is 'noisy-source'

Usage:
  python3 -I scripts/bench/make-record.py --from-result RESULT.json --out RECORD.json
  python3 -I scripts/bench/make-record.py --render-md RECORD.json
  python3 -I scripts/bench/make-record.py --render-md RECORD.json --write-md DOC --block NAME
  python3 -I scripts/bench/make-record.py --render-md RECORD.json --check-md DOC --block NAME
  python3 -I scripts/bench/make-record.py --self-test
"""

import argparse
import contextlib
import importlib.util
import io
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CANNED = HERE / "fixtures" / "canned-run-bench.json"
DEFAULT_FIXTURE = HERE / "fixtures" / "record-four-arm.json"
BLOCK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\Z")
SAFE_ARM_NAME_RE = re.compile(r"^[A-Za-z0-9_.+-]+\Z")

MANDATORY_ARMS = (
    "nvenc-tuned",
    "nvenc-tflevel",
    "nvenc-uhq",
    "pelorus-denoise+nvenc-tuned",
)

ARM_PARAMETERS = {
    "nvenc-tuned": {
        "preset": "p7",
        "tune": "hq",
        "spatial-aq": 1,
        "temporal-aq": 1,
        "aq-strength": 8,
        "rc-lookahead": 32,
        "multipass": "fullres",
        "b_ref_mode": "middle",
    },
    "nvenc-tflevel": {
        "preset": "p7",
        "tune": "hq",
        "tf_level": 4,
        "spatial-aq": 1,
        "temporal-aq": 1,
        "aq-strength": 8,
        "rc-lookahead": 32,
        "multipass": "fullres",
        "b_ref_mode": "middle",
    },
    "nvenc-uhq": {
        "preset": "p7",
        "tune": "uhq",
        "spatial-aq": 1,
        "temporal-aq": 1,
        "aq-strength": 8,
        "rc-lookahead": 32,
        "multipass": "fullres",
        "b_ref_mode": "middle",
    },
    "pelorus-denoise+nvenc-tuned": {
        "preset": "p7",
        "tune": "hq",
        "spatial-aq": 1,
        "temporal-aq": 1,
        "aq-strength": 8,
        "rc-lookahead": 32,
        "multipass": "fullres",
        "b_ref_mode": "middle",
    },
}


def _md_text(value):
    """Sanitize and escape plain text for Markdown table cells and headers."""
    if value is None:
        return ""
    text = "".join(c if c.isprintable() else " " for c in str(value))
    text = text.replace("&", "&amp;")
    text = text.replace("\\", "\\\\")
    text = text.replace("|", "\\|")
    text = text.replace("`", "\\`")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("(", "\\(")
    text = text.replace(")", "\\)")
    text = text.replace("!", "\\!")
    return text


def load_validator():
    """Dynamically import validate_record from check-record.py in isolated mode."""
    check_record_path = HERE / "check-record.py"
    if not check_record_path.is_file():
        raise RuntimeError("cannot find %s" % check_record_path)
    spec = importlib.util.spec_from_file_location("check_record", check_record_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot create module spec for check-record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.validate_record


def resolve_device_name(device_name_arg=None):
    """Query device name from nvidia-smi if not supplied; never invent a name."""
    if device_name_arg and device_name_arg.strip():
        return device_name_arg.strip()
    message = (
        "device name could not be queried via nvidia-smi and no --device-name was supplied; "
        "device identity must not be invented (ADR-0223, #131)"
    )
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(message) from exc
    lines = proc.stdout.strip().splitlines()
    if lines and lines[0].strip():
        return lines[0].strip()
    raise RuntimeError(message)


def make_record(result_data, device=None, clip=None, encoder=None, filter_name=None, note=None):
    """Construct a four-arm record conforming to record.schema.json from run-bench result data."""
    clip_name = clip or result_data.get("clip") or "unnamed_noisy_clip"
    dev_name = device or result_data.get("device")
    if not dev_name:
        dev_name = resolve_device_name()

    enc = encoder or result_data.get("encoder") or "hevc_nvenc"
    flt = filter_name or result_data.get("filter") or "pelorus_denoise_vulkan"

    rows = result_data.get("rows", [])
    arm_points = {arm: [] for arm in MANDATORY_ARMS}

    for r in rows:
        v = r.get("variant")
        if v in arm_points:
            pt = {
                "cq": int(r["cq"]),
                "bitrate_kbps": float(r["bitrate_kbps"]),
                "ssimulacra2": float(r["ssimulacra2"]),
            }
            if "vmaf" in r and r["vmaf"] is not None and not (isinstance(r["vmaf"], float) and math.isnan(r["vmaf"])):
                pt["vmaf"] = float(r["vmaf"])
            if "cambi" in r and r["cambi"] is not None and not (isinstance(r["cambi"], float) and math.isnan(r["cambi"])):
                pt["cambi"] = float(r["cambi"])
            arm_points[v].append(pt)

    arms_obj = {}
    for arm_name in MANDATORY_ARMS:
        pts = sorted(arm_points[arm_name], key=lambda p: p["cq"])
        arm_entry = {
            "device": dev_name,
            "encoder": enc,
            "parameters": dict(ARM_PARAMETERS[arm_name]),
            "points": pts,
        }
        if arm_name == "pelorus-denoise+nvenc-tuned" and flt:
            arm_entry["filter"] = flt
        arms_obj[arm_name] = arm_entry

    rec = {
        "schema_version": 1,
        "clip": clip_name,
        "reference": "noisy-source",
        "arms": arms_obj,
    }
    if note:
        rec["note"] = note
    elif "note" in result_data:
        rec["note"] = result_data["note"]

    return rec


def _format_point_row(arm_name, pt):
    """Format one rate point into a Markdown table row."""
    cq_str = str(pt["cq"]) if "cq" in pt else "n/a"
    br_str = f"{pt['bitrate_kbps']:.1f}" if "bitrate_kbps" in pt else "n/a"
    s2_str = f"{pt['ssimulacra2']:.2f}" if "ssimulacra2" in pt else "n/a"
    vmaf_str = f"{pt['vmaf']:.2f}" if pt.get("vmaf") is not None else "n/a"
    cambi_str = f"{pt['cambi']:.2f}" if pt.get("cambi") is not None else "n/a"
    arm_str = f"`{arm_name}`" if SAFE_ARM_NAME_RE.match(arm_name) else _md_text(arm_name)
    return f"| {arm_str} | {cq_str} | {br_str} | {s2_str} | {vmaf_str} | {cambi_str} |"


def render_record_md(record, validator=None, errors_out=None):
    """Render a validated benchmark record to a deterministic Markdown table block."""
    if not isinstance(record, dict):
        if errors_out is not None:
            errors_out.append("record must be a JSON object")
        return None
    if validator is None:
        validator = load_validator()
    errs = validator(record)
    if errs:
        if errors_out is not None:
            errors_out.extend(errs)
        return None

    clip = _md_text(record.get("clip", ""))
    ref = _md_text(record.get("reference", ""))
    arms = record.get("arms", {})
    dev = ""
    for name in MANDATORY_ARMS:
        if name in arms and isinstance(arms[name], dict) and arms[name].get("device"):
            dev = _md_text(arms[name]["device"])
            break

    fixt = ", fixture" if record.get("is_fixture") else ""
    lines = [
        f"Record: clip {clip}, reference {ref}, device {dev}{fixt}",
        "",
        "| arm | CQ | bitrate (kbps) | SSIMULACRA2 | VMAF | CAMBI |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    ordered_arms = [a for a in MANDATORY_ARMS if a in arms]
    ordered_arms.extend(sorted(a for a in arms if a not in MANDATORY_ARMS))

    for arm_name in ordered_arms:
        pts = sorted(arms[arm_name].get("points", []), key=lambda p: p.get("cq", 0))
        for pt in pts:
            lines.append(_format_point_row(arm_name, pt))

    return "\n".join(lines) + "\n"


def _find_block_bounds(doc_lines, block_name):
    """Find line indices of begin and end markers for block_name."""
    begin_marker = f"<!-- make-record:{block_name} begin -->"
    end_marker = f"<!-- make-record:{block_name} end -->"
    full_text = "".join(doc_lines)
    if full_text.count(begin_marker) != 1 or full_text.count(end_marker) != 1:
        return None
    begin_indices = [i for i, line in enumerate(doc_lines) if line.strip() == begin_marker]
    end_indices = [i for i, line in enumerate(doc_lines) if line.strip() == end_marker]
    if len(begin_indices) != 1 or len(end_indices) != 1 or end_indices[0] <= begin_indices[0]:
        return None
    return begin_indices[0], end_indices[0]


def _load_block(p, block_name):
    """Read p and locate block_name; print the reason and return None on any marker or name error."""
    if not BLOCK_NAME_RE.match(block_name):
        sys.stderr.write(f"make-record: invalid block name {block_name!r}\n")
        return None
    try:
        lines = p.read_bytes().decode("utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"make-record: cannot read {p}: {exc}\n")
        return None
    bounds = _find_block_bounds(lines, block_name)
    if bounds is None:
        sys.stderr.write(f"make-record: marker error in {p} for block {block_name}\n")
        return None
    return lines, bounds


def replace_doc_block(doc_path, block_name, rendered_content):
    """Replace content between markers in doc_path with rendered_content atomically."""
    p = Path(doc_path)
    loaded = _load_block(p, block_name)
    if loaded is None:
        return 2
    lines, (begin_idx, end_idx) = loaded
    begin_line = lines[begin_idx]
    doc_newline = "\r\n" if begin_line.endswith("\r\n") else "\n"
    if not begin_line.endswith(("\r\n", "\n")):
        begin_line += doc_newline
    rendered_lines = rendered_content.splitlines()
    content = doc_newline.join(rendered_lines) + doc_newline
    new_text = "".join(lines[:begin_idx]) + begin_line + content + "".join(lines[end_idx:])

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False, encoding="utf-8", newline="") as tf:
            tmp_path = Path(tf.name)
            tf.write(new_text)
        os.chmod(tmp_path, stat.S_IMODE(p.stat().st_mode))
        os.replace(tmp_path, p)
    except OSError as exc:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        sys.stderr.write(f"make-record: atomic write failed: {exc}\n")
        return 2
    return 0


def check_doc_block(doc_path, block_name, rendered_content, record_path):
    """Check if content between markers in doc_path equals rendered_content."""
    p = Path(doc_path)
    loaded = _load_block(p, block_name)
    if loaded is None:
        return 2
    lines, (begin_idx, end_idx) = loaded
    begin_line = lines[begin_idx]
    doc_newline = "\r\n" if begin_line.endswith("\r\n") else "\n"
    rendered_lines = rendered_content.splitlines()
    expected = doc_newline.join(rendered_lines) + doc_newline
    current = "".join(lines[begin_idx + 1 : end_idx])
    if current == expected:
        return 0
    print(f"{doc_path}: block {block_name} drifted from {record_path}")
    return 1


def _handle_render_cli(args):
    """Handle --render-md, --write-md, and --check-md commands."""
    if not args.render_md:
        sys.stderr.write("make-record: --write-md and --check-md require --render-md RECORD.json\n")
        return 2
    if args.write_md and args.check_md:
        sys.stderr.write("make-record: cannot combine --write-md and --check-md\n")
        return 2
    if (args.write_md or args.check_md) and not args.block:
        sys.stderr.write("make-record: --write-md and --check-md require --block NAME\n")
        return 2
    if args.block and not (args.write_md or args.check_md):
        sys.stderr.write("make-record: --block requires --write-md or --check-md\n")
        return 2

    rec_path = Path(args.render_md)
    if not rec_path.is_file():
        sys.stderr.write(f"make-record: file not found: {rec_path}\n")
        return 1
    try:
        rec_data = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"make-record: JSON parse error in {rec_path}: {exc}\n")
        return 1

    errs = []
    rendered = render_record_md(rec_data, errors_out=errs)
    if rendered is None:
        sys.stderr.write(f"make-record: {len(errs)} validation error(s) in {rec_path}:\n")
        for e in errs:
            sys.stderr.write(f"  - {e}\n")
        return 1

    if args.write_md:
        return replace_doc_block(args.write_md, args.block, rendered)
    if args.check_md:
        return check_doc_block(args.check_md, args.block, rendered, args.render_md)
    sys.stdout.write(rendered)
    return 0


def _handle_from_result_cli(args):
    """Handle --from-result to construct and emit a record.json."""
    in_path = Path(args.from_result)
    if not in_path.is_file():
        sys.stderr.write(f"make-record: file not found: {in_path}\n")
        return 1
    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"make-record: JSON parse error in {in_path}: {exc}\n")
        return 1

    rec = make_record(
        data,
        device=args.device_name,
        clip=args.clip,
        encoder=args.encoder,
        filter_name=args.filter,
        note=args.note,
    )
    validate_record = load_validator()
    errs = validate_record(rec)
    if errs:
        sys.stderr.write(f"make-record: {len(errs)} validation error(s) in emitted record:\n")
        for e in errs:
            sys.stderr.write(f"  - {e}\n")
        return 1

    out_json = json.dumps(rec, indent=2) + "\n"
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_json, encoding="utf-8")
        print(f"make-record: wrote valid record to {out_path}")
    else:
        sys.stdout.write(out_json)
    return 0


def _evaluate_check(name, ok, detail=""):
    """Format and print check status, returning whether it passed."""
    status = "[PASS]" if ok else "[FAIL]"
    msg = f"{status} {name}"
    if detail and not ok:
        msg += f" ({detail})"
    print(msg)
    return ok


def _run_emitter_cases(canned_data, validate_record):
    """Execute positive and negative test cases against make_record and validator."""
    results = []

    # 1. Positive test: emitter output on canned run-bench result.json -> check-record accepts
    rec = make_record(canned_data)
    errs = validate_record(rec)
    results.append(_evaluate_check("canned_run_bench_emits_valid_record", len(errs) == 0, repr(errs)))

    # 2. Negative test: drop one arm from emitted record -> rejected
    mut1 = json.loads(json.dumps(rec))
    del mut1["arms"]["nvenc-uhq"]
    errs_drop_arm = validate_record(mut1)
    results.append(
        _evaluate_check(
            "drop_emitted_arm_rejected",
            any("rule:arm_set" in e for e in errs_drop_arm),
            repr(errs_drop_arm),
        )
    )

    # 3. Negative test: drop one arm from input result rows -> rejected
    canned_missing_arm = json.loads(json.dumps(canned_data))
    canned_missing_arm["rows"] = [
        r for r in canned_missing_arm["rows"] if r.get("variant") != "nvenc-tflevel"
    ]
    rec_missing_arm = make_record(canned_missing_arm)
    errs_missing_in_data = validate_record(rec_missing_arm)
    results.append(
        _evaluate_check(
            "drop_input_arm_rejected",
            any("rule:rate_points" in e or "rule:arm_set" in e for e in errs_missing_in_data),
            repr(errs_missing_in_data),
        )
    )

    # 4. Negative test: drop device -> rejected
    mut2 = json.loads(json.dumps(rec))
    del mut2["arms"]["nvenc-tuned"]["device"]
    errs_drop_dev = validate_record(mut2)
    results.append(
        _evaluate_check(
            "drop_device_rejected",
            any("rule:device" in e for e in errs_drop_dev),
            repr(errs_drop_dev),
        )
    )

    return results


def _run_render_cases(fixture_data, validate_record):
    """Execute test cases for Markdown rendering of benchmark records."""
    results = []

    # 1. render of fixture: 1 header line + 16 data rows, first row arm `nvenc-tuned` cq 22, rendering twice is byte-identical
    r1 = render_record_md(fixture_data, validator=validate_record)
    r2 = render_record_md(fixture_data, validator=validate_record)
    lines = r1.splitlines() if r1 else []
    headers = [l for l in lines if l.startswith("| arm |")]
    table_rows = [l for l in lines if l.startswith("| `")]
    first_ok = len(table_rows) > 0 and "| `nvenc-tuned` | 22 |" in table_rows[0]
    results.append(
        _evaluate_check(
            "render_fixture_table_structure_and_determinism",
            r1 is not None and r1 == r2 and len(headers) == 1 and len(table_rows) == 16 and first_ok,
        )
    )

    # 2. record with points given in descending cq renders ascending
    rec_desc = json.loads(json.dumps(fixture_data))
    for arm in rec_desc["arms"].values():
        arm["points"] = list(reversed(arm["points"]))
    r_desc = render_record_md(rec_desc, validator=validate_record)
    desc_rows = [l for l in r_desc.splitlines() if l.startswith("| `")] if r_desc else []
    cqs = [int(r.split("|")[2].strip()) for r in desc_rows]
    cqs_asc = len(cqs) == 16 and all(cqs[i] < cqs[i + 1] for i in range(15) if (i + 1) % 4 != 0)
    results.append(_evaluate_check("render_descending_points_sorted_ascending", cqs_asc))

    # 3. point without cambi -> n/a cell
    rec_no_cambi = json.loads(json.dumps(fixture_data))
    for arm in rec_no_cambi["arms"].values():
        for pt in arm["points"]:
            pt.pop("cambi", None)
    r_no_cambi = render_record_md(rec_no_cambi, validator=validate_record)
    rows_no_cambi = [l for l in r_no_cambi.splitlines() if l.startswith("| `")] if r_no_cambi else []
    all_na = len(rows_no_cambi) == 16 and all(r.endswith("| n/a |") for r in rows_no_cambi)
    results.append(_evaluate_check("render_missing_cambi_shows_na", all_na))

    # 4. invalid record (drop an arm) -> render returns failure, no output
    rec_invalid = json.loads(json.dumps(fixture_data))
    del rec_invalid["arms"]["nvenc-uhq"]
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        r_invalid = render_record_md(rec_invalid, validator=validate_record)
    invalid_ok = r_invalid is None and buf_out.getvalue() == "" and buf_err.getvalue() == ""
    results.append(_evaluate_check("render_invalid_record_fails_no_output", invalid_ok))

    return results


def _run_doc_block_cases(fixture_data, rendered_md):
    """Execute test cases for write-md and check-md doc block management."""
    results = []
    before = "# Title\n\nIntroductory section.\n\n"
    after = "\n\nTrailing notes.\n"
    blk = "bench-summary"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        doc = tmp / "test-doc.md"
        rec = tmp / "record.json"
        rec.write_text(json.dumps(fixture_data), encoding="utf-8")
        orig_content = f"{before}<!-- make-record:{blk} begin -->\nold\n<!-- make-record:{blk} end -->{after}"
        doc.write_text(orig_content, encoding="utf-8")

        # 1. write-md -> surrounding byte-identical, check-md then 0
        w_code = replace_doc_block(doc, blk, rendered_md)
        doc_text = doc.read_text(encoding="utf-8")
        surrounding_ok = (
            w_code == 0
            and doc_text.startswith(before + f"<!-- make-record:{blk} begin -->\n")
            and doc_text.endswith(f"<!-- make-record:{blk} end -->{after}")
        )
        c_code = check_doc_block(doc, blk, rendered_md, rec)
        results.append(_evaluate_check("write_md_surrounding_clean_and_check_passes", surrounding_ok and c_code == 0))

        # 2. check-md after editing one number inside block -> 1
        doc.write_text(doc_text.replace("2450.0", "9999.0", 1), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            drift_code = check_doc_block(doc, blk, rendered_md, rec)
        results.append(_evaluate_check("check_md_detects_drift", drift_code == 1))

        # 3. doc missing end marker -> write-md 2, file byte-identical
        missing_end = f"{before}<!-- make-record:{blk} begin -->\nold\n"
        doc.write_text(missing_end, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            m_code = replace_doc_block(doc, blk, rendered_md)
        results.append(_evaluate_check("write_md_missing_end_marker_fails", m_code == 2 and doc.read_text(encoding="utf-8") == missing_end))

        # 4. duplicate begin marker -> 2
        dup_begin = f"{before}<!-- make-record:{blk} begin -->\n<!-- make-record:{blk} begin -->\n<!-- make-record:{blk} end -->{after}"
        doc.write_text(dup_begin, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            d_code = replace_doc_block(doc, blk, rendered_md)
        results.append(_evaluate_check("write_md_duplicate_begin_marker_fails", d_code == 2))

        # 5. bad block name ../x -> 2
        with contextlib.redirect_stderr(io.StringIO()):
            bad_code = replace_doc_block(doc, "../x", rendered_md)
        results.append(_evaluate_check("write_md_bad_block_name_fails", bad_code == 2))

    return results


def _run_marker_order_case(rendered_md):
    """Planted case: an end marker before its begin marker is refused and the file stays unchanged."""
    blk = "bench-summary"
    text = f"<!-- make-record:{blk} end -->\nold\n<!-- make-record:{blk} begin -->\n"
    with tempfile.TemporaryDirectory() as tmpdir:
        doc = Path(tmpdir) / "test-doc.md"
        doc.write_text(text, encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            code = replace_doc_block(doc, blk, rendered_md)
        unchanged = doc.read_text(encoding="utf-8") == text
    return [_evaluate_check("write_md_end_before_begin_fails", code == 2 and unchanged)]


def _run_escaping_cases():
    """Planted cases: cell escaping preserves columns and escapes HTML markers."""
    malicious = "a|b\n`<!-- make-record:x end -->"
    rec = {
        "schema_version": 1,
        "clip": malicious,
        "reference": "noisy-source",
        "arms": {
            malicious: {
                "device": malicious,
                "points": [{"cq": 22, "bitrate_kbps": 1000.0, "ssimulacra2": 70.0}],
            }
        },
    }
    rendered = render_record_md(rec, validator=lambda _: [])
    if rendered is None:
        return [_evaluate_check("cell_escaping_preserves_columns", False, "render is None")]
    tbl_lines = [l for l in rendered.splitlines() if l.startswith("|")]
    col_counts = [len(re.findall(r"(?<!\\)\|", l)) for l in tbl_lines]
    cols_ok = len(tbl_lines) >= 3 and len(set(col_counts)) == 1
    no_marker_ok = "<!--" not in rendered

    rec2 = dict(rec, clip="![x](https://example.invalid/image)", reference="[y](https://example.invalid/)")
    r2 = render_record_md(rec2, validator=lambda _: [])
    link_ok = bool(r2 and not re.search(r"(?<!\\)\]\(", r2) and not re.search(r"(?<!\\)!\[", r2))

    return [
        _evaluate_check("cell_escaping_preserves_columns", cols_ok, f"cols: {col_counts}"),
        _evaluate_check("header_marker_injection_escaped", no_marker_ok, "found <!--"),
        _evaluate_check("link_image_syntax_escaped", link_ok),
    ]


def _run_non_object_case():
    """Planted case: non-object record returns validation error without traceback."""
    errs = []
    try:
        r = render_record_md([], errors_out=errs)
        ok = r is None and "record must be a JSON object" in errs
    except Exception as exc:
        ok = False
        errs.append(str(exc))
    return [_evaluate_check("render_non_object_record_rejected", ok, repr(errs))]


def _run_crlf_case(rendered_md):
    """Planted case: CRLF doc preserves line endings, prefix/suffix bytes, and check-md passes."""
    blk = "bench-summary"
    prefix = b"# Intro CRLF\r\n\r\nParagraph before block.\r\n\r\n"
    marker_begin = f"<!-- make-record:{blk} begin -->\r\n".encode("utf-8")
    old_content = b"old content line 1\r\nold content line 2\r\n"
    marker_end = f"<!-- make-record:{blk} end -->\r\n".encode("utf-8")
    suffix = b"\r\nTrailing CRLF text.\r\n"
    initial_bytes = prefix + marker_begin + old_content + marker_end + suffix

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        doc = tmp / "crlf-doc.md"
        rec = tmp / "record.json"
        rec.write_text("{}", encoding="utf-8")
        doc.write_bytes(initial_bytes)

        w_code = replace_doc_block(doc, blk, rendered_md)
        out_bytes = doc.read_bytes()
        prefix_ok = out_bytes.startswith(prefix + marker_begin)
        suffix_ok = out_bytes.endswith(marker_end + suffix)
        middle = out_bytes[len(prefix + marker_begin) : -len(marker_end + suffix)]
        crlf_lines_ok = b"\r\n" in middle and not any(
            line.endswith(b"\n") and not line.endswith(b"\r\n")
            for line in middle.splitlines(keepends=True)
        )
        c_code = check_doc_block(doc, blk, rendered_md, rec)
        ok = w_code == 0 and prefix_ok and suffix_ok and crlf_lines_ok and c_code == 0
    return [_evaluate_check("write_md_crlf_preserved_and_check_passes", ok)]


def _run_permissions_case(rendered_md):
    """Planted case: doc file permissions are preserved across write_doc_block."""
    if os.name == "nt":
        print("[SKIP] write_md_permissions_preserved (Windows nt)")
        return [_evaluate_check("write_md_permissions_preserved", True)]

    blk = "bench-summary"
    content = f"<!-- make-record:{blk} begin -->\nold\n<!-- make-record:{blk} end -->\n"
    with tempfile.TemporaryDirectory() as tmpdir:
        doc = Path(tmpdir) / "perm-doc.md"
        doc.write_text(content, encoding="utf-8")
        # owner-only mode that differs from the 0o600 temp-file default, so the case fails if the mode copy is lost.
        os.chmod(doc, 0o700)
        w_code = replace_doc_block(doc, blk, rendered_md)
        current_mode = stat.S_IMODE(doc.stat().st_mode)
        ok = w_code == 0 and current_mode == 0o700
    return [_evaluate_check("write_md_permissions_preserved", ok, f"mode: {oct(current_mode)}")]


def run_self_test():
    """Run self-tests: emitter validation, Markdown rendering, and doc block check/write."""
    validate_record = load_validator()

    canned_path = DEFAULT_CANNED
    fixture_path = DEFAULT_FIXTURE
    if not canned_path.is_file():
        sys.stderr.write("make-record self-test FAIL: canned fixture not found: %s\n" % canned_path)
        return 1
    if not fixture_path.is_file():
        sys.stderr.write("make-record self-test FAIL: four-arm fixture not found: %s\n" % fixture_path)
        return 1

    try:
        canned_data = json.loads(canned_path.read_text(encoding="utf-8"))
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        sys.stderr.write("make-record self-test FAIL: cannot load fixture: %s\n" % exc)
        return 1

    results = []
    results.extend(_run_emitter_cases(canned_data, validate_record))
    results.extend(_run_render_cases(fixture_data, validate_record))
    results.extend(_run_escaping_cases())
    results.extend(_run_non_object_case())

    rendered_md = render_record_md(fixture_data, validator=validate_record)
    results.extend(_run_doc_block_cases(fixture_data, rendered_md))
    results.extend(_run_marker_order_case(rendered_md))
    results.extend(_run_crlf_case(rendered_md))
    results.extend(_run_permissions_case(rendered_md))

    passed_count = sum(1 for ok in results if ok)
    total = len(results)
    print(f"\nmake-record self-test summary: {passed_count}/{total} checks passed")
    return 0 if passed_count == total else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Emit ADR-0223 benchmark records.")
    ap.add_argument("--self-test", action="store_true", help="Run emitter validation self-tests")
    ap.add_argument("--from-result", help="Path to run-bench result.json")
    ap.add_argument("--out", "-o", help="Path to output record.json")
    ap.add_argument("--clip", help="Clip identifier name")
    ap.add_argument("--device-name", help="Device/GPU name (never invented)")
    ap.add_argument("--encoder", default="hevc_nvenc", help="Encoder name")
    ap.add_argument("--filter", default="pelorus_denoise_vulkan", help="Filter name")
    ap.add_argument("--note", help="Optional context or test note")
    ap.add_argument("--render-md", help="Path to record.json to render as Markdown table")
    ap.add_argument("--write-md", help="Path to documentation Markdown file to update")
    ap.add_argument("--check-md", help="Path to documentation Markdown file to check for drift")
    ap.add_argument("--block", help="Name of block to update/check between markers")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if args.render_md or args.write_md or args.check_md:
        return _handle_render_cli(args)
    if args.from_result:
        return _handle_from_result_cli(args)

    ap.error("must supply --from-result RESULT.json, --render-md RECORD.json, or --self-test")
    return 2


if __name__ == "__main__":
    sys.exit(main())
