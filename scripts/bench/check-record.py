#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Noisy-source benchmark result record validator (ADR-0223, #131).

Enforces source-referenced benchmarking rules for competitive Pelorus claims:
- Scoring reference must be 'noisy-source' (synthetic-clean reference rejected).
- Four mandatory arms: nvenc-tuned, nvenc-tflevel, nvenc-uhq, pelorus-denoise+nvenc-tuned.
- Device identity explicitly named per arm.
- Minimum 4 rate points per arm (required for cubic Bjøntegaard BD-rate fit).
- Distinct bitrates across arms at each rung (equal bitrates unusable for BD-rate).
- Primary quality metric is SSIMULACRA2.

Usage:
  python3 -I scripts/bench/check-record.py RECORD.json
  python3 -I scripts/bench/check-record.py --self-test
"""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_DEFAULT_SCHEMA = HERE / "record.schema.json"
_DEFAULT_FIXTURE = HERE / "fixtures" / "record-four-arm.json"

SCHEMA_PATH = Path(os.environ.get("PELORUS_BENCH_SCHEMA", _DEFAULT_SCHEMA))
FIXTURE_PATH = Path(os.environ.get("PELORUS_BENCH_FIXTURE", _DEFAULT_FIXTURE))

MANDATORY_ARMS = (
    "nvenc-tuned",
    "nvenc-tflevel",
    "nvenc-uhq",
    "pelorus-denoise+nvenc-tuned",
)


# ------------------------------------------------------------- schema walker

def _load_json_shape():
    """Import the shared JSON shape validator from tools/tester by file path."""
    path = Path(__file__).resolve().parents[2] / "tools/tester/pelorus_json_shape.py"
    spec = importlib.util.spec_from_file_location("pelorus_json_shape", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load pelorus_json_shape from %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_JSON_SHAPE = _load_json_shape()
validate_schema = _JSON_SHAPE.validate_schema


# ----------------------------------------------------------- domain rules

def _check_rule_reference(record, errs):
    ref = record.get("reference")
    if ref != "noisy-source":
        errs.append(
            "rule:reference: reference must be 'noisy-source' (source-referenced scoring, "
            "ADR-0223); got %r" % (ref,)
        )


def _check_rule_arm_set(arms, errs):
    for required_arm in MANDATORY_ARMS:
        if required_arm not in arms:
            errs.append("rule:arm_set: missing mandatory baseline arm %r" % (required_arm,))


def _check_rule_devices(arms, errs):
    for arm_name, arm in arms.items():
        if not isinstance(arm, dict):
            continue
        dev = arm.get("device")
        if not isinstance(dev, str) or not dev.strip():
            errs.append("rule:device: arm %r missing device name" % (arm_name,))


def _check_rule_rate_points(arms, errs):
    for arm_name, arm in arms.items():
        if not isinstance(arm, dict):
            continue
        points = arm.get("points")
        if not isinstance(points, list):
            errs.append("rule:points: arm %r points must be a list" % (arm_name,))
            continue
        if len(points) < 4:
            errs.append(
                "rule:rate_points: arm %r has %d rate points, minimum is 4 (unusable for BD-rate)"
                % (arm_name, len(points))
            )
        # Check monotonic bitrates within arm
        bitrates = []
        for p_idx, pt in enumerate(points):
            if isinstance(pt, dict) and "bitrate_kbps" in pt:
                br = pt["bitrate_kbps"]
                if not isinstance(br, (int, float)) or br <= 0:
                    errs.append(
                        "rule:bitrate_positive: arm %r point %d bitrate %r <= 0"
                        % (arm_name, p_idx, br)
                    )
                else:
                    bitrates.append(br)
        if len(bitrates) == len(points) and len(set(bitrates)) != len(bitrates):
            errs.append(
                "rule:monotonic_bitrate: arm %r has duplicate bitrates across rate points"
                % (arm_name,)
            )


def _check_rule_distinct_rung_bitrates(arms, errs):
    # Collect points per arm
    arm_points = {}
    for arm_name in MANDATORY_ARMS:
        if arm_name in arms and isinstance(arms[arm_name], dict):
            pts = arms[arm_name].get("points")
            if isinstance(pts, list):
                arm_points[arm_name] = pts

    if len(arm_points) == len(MANDATORY_ARMS):
        min_len = min(len(pts) for pts in arm_points.values())
        for rung_idx in range(min_len):
            rung_bitrates = {}
            for arm_name, pts in arm_points.items():
                pt = pts[rung_idx]
                if isinstance(pt, dict) and "bitrate_kbps" in pt:
                    br = pt["bitrate_kbps"]
                    if br in rung_bitrates:
                        errs.append(
                            "rule:distinct_rung_bitrates: arms %r and %r have identical bitrate "
                            "%s kbps at rung %d (unusable for BD-rate)"
                            % (rung_bitrates[br], arm_name, br, rung_idx)
                        )
                    else:
                        rung_bitrates[br] = arm_name


def check_rules(record):
    """Enforce the ADR-0223 / #131 benchmarking domain rules."""
    errs = []

    # Rule 1: Reference must be 'noisy-source'
    _check_rule_reference(record, errs)

    # Rule 2: Arm set — all 4 arms required
    arms = record.get("arms")
    if not isinstance(arms, dict):
        errs.append("rule:arms: 'arms' must be an object")
        return errs

    _check_rule_arm_set(arms, errs)

    # Rule 3: Device per arm
    _check_rule_devices(arms, errs)

    # Rule 4: Minimum 4 rate points per arm
    _check_rule_rate_points(arms, errs)

    # Rule 5: Distinct bitrates across arms at each rung
    _check_rule_distinct_rung_bitrates(arms, errs)

    return errs


def validate_record(record_data, schema=None):
    """Run full schema and domain validation on loaded JSON record."""
    if schema is None:
        if not SCHEMA_PATH.is_file():
            return ["schema file not found: %s" % SCHEMA_PATH]
        try:
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            return ["cannot parse schema %s: %s" % (SCHEMA_PATH, exc)]

    schema_errs = validate_schema(record_data, schema)
    domain_errs = check_rules(record_data)
    return schema_errs + domain_errs


# ------------------------------------------------------------- self-test

SELF_TEST_CASES = (
    # (name, mutation_kind, expect_pass, match_error)
    ("positive_fixture_accepted", None, True, None),
    ("negative_missing_arm_tflevel", "del_arm_tflevel", False, "rule:arm_set"),
    ("negative_missing_arm_uhq", "del_arm_uhq", False, "rule:arm_set"),
    ("negative_missing_device", "del_device", False, "rule:device"),
    ("negative_empty_device", "empty_device", False, "rule:device"),
    ("boundary_one_rate_point", "one_rate_point", False, "rule:rate_points"),
    ("boundary_three_rate_points", "three_rate_points", False, "rule:rate_points"),
    ("boundary_equal_bitrates_across_arms", "equal_bitrates", False, "rule:distinct_rung_bitrates"),
    ("negative_reference_synthetic_clean", "synth_clean", False, "rule:reference"),
    ("negative_reference_missing", "del_reference", False, "reference"),
)


def _mutate_fixture(fixture_raw, kind):
    """Apply a named mutation to raw fixture JSON string."""
    mut = json.loads(fixture_raw)
    if kind == "del_arm_tflevel":
        del mut["arms"]["nvenc-tflevel"]
    elif kind == "del_arm_uhq":
        del mut["arms"]["nvenc-uhq"]
    elif kind == "del_device":
        del mut["arms"]["nvenc-tuned"]["device"]
    elif kind == "empty_device":
        mut["arms"]["nvenc-tuned"]["device"] = "   "
    elif kind == "one_rate_point":
        mut["arms"]["nvenc-tuned"]["points"] = [mut["arms"]["nvenc-tuned"]["points"][0]]
    elif kind == "three_rate_points":
        mut["arms"]["nvenc-tuned"]["points"] = mut["arms"]["nvenc-tuned"]["points"][:3]
    elif kind == "equal_bitrates":
        same_br = mut["arms"]["nvenc-tuned"]["points"][0]["bitrate_kbps"]
        mut["arms"]["nvenc-tflevel"]["points"][0]["bitrate_kbps"] = same_br
    elif kind == "synth_clean":
        mut["reference"] = "synthetic-clean"
    elif kind == "del_reference":
        del mut["reference"]
    return mut


def _run_single_test(name, data, expect_pass, match_error, schema):
    """Run one self-test case and report PASS/FAIL."""
    errs = validate_record(data, schema)
    passed = (len(errs) == 0) if expect_pass else (len(errs) > 0)
    matched = True
    if match_error and not expect_pass:
        matched = any(match_error in e for e in errs)
    ok = passed and matched
    status_tag = "[PASS]" if ok else "[FAIL]"
    if not ok:
        print("%s %s (expect_pass=%s, got_errs=%r)" % (status_tag, name, expect_pass, errs))
    else:
        print("%s %s" % (status_tag, name))
    return ok


def run_self_test():
    """Run the self-test mutation battery. Plants defects and checks rejection."""
    if not FIXTURE_PATH.is_file():
        sys.stderr.write("self-test FAIL: fixture not found at %s\n" % FIXTURE_PATH)
        return 1

    try:
        fixture_raw = FIXTURE_PATH.read_text(encoding="utf-8")
        fixture_data = json.loads(fixture_raw)
    except Exception as exc:
        sys.stderr.write("self-test FAIL: cannot load fixture: %s\n" % exc)
        return 1

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    passed_count = 0
    for name, kind, expect_pass, match_error in SELF_TEST_CASES:
        data = fixture_data if kind is None else _mutate_fixture(fixture_raw, kind)
        if _run_single_test(name, data, expect_pass, match_error, schema):
            passed_count += 1

    # Negative case: usage error when record missing and not self-test
    if main([]) != 2:
        print("[FAIL] negative_missing_record_argument")
    else:
        print("[PASS] negative_missing_record_argument")
        passed_count += 1
    total = len(SELF_TEST_CASES) + 1
    print("\nself-test summary: %d/%d checks passed" % (passed_count, total))
    return 0 if passed_count == total else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for check-record."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run self-test suite")
    parser.add_argument("record", nargs="?", type=Path, help="path to benchmark record JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for checking benchmark record."""
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()

    if not args.record:
        sys.stderr.write("usage: check-record.py RECORD.json | --self-test\n")
        return 2

    path = args.record
    if not path.is_file():
        sys.stderr.write("check-record: file not found: %s\n" % path)
        return 1

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.stderr.write("check-record: JSON parse error in %s: %s\n" % (path, exc))
        return 1

    errs = validate_record(data)
    if errs:
        sys.stderr.write("check-record: %d validation error(s) in %s:\n" % (len(errs), path))
        for e in errs:
            sys.stderr.write("  - %s\n" % e)
        return 1

    print("check-record: %s is valid" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
