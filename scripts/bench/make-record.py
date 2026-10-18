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
  python3 -I scripts/bench/make-record.py --self-test
"""

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CANNED = HERE / "fixtures" / "canned-run-bench.json"

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


def run_self_test():
    """Run self-test: canned result -> check-record accepts; drop one arm -> rejected."""
    validate_record = load_validator()

    canned_path = DEFAULT_CANNED
    if not canned_path.is_file():
        sys.stderr.write("make-record self-test FAIL: canned fixture not found: %s\n" % canned_path)
        return 1

    try:
        canned_data = json.loads(canned_path.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.stderr.write("make-record self-test FAIL: cannot load canned fixture: %s\n" % exc)
        return 1

    results = _run_emitter_cases(canned_data, validate_record)
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
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()

    if not args.from_result:
        ap.error("must supply --from-result RESULT.json or --self-test")

    in_path = Path(args.from_result)
    if not in_path.is_file():
        sys.stderr.write(f"make-record: file not found: {in_path}\n")
        return 1

    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except Exception as exc:
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


if __name__ == "__main__":
    sys.exit(main())
