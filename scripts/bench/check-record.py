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

import json
import os
import re
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

def _type_ok(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }[name]
    return isinstance(value, py)


def _resolve_ref(root_schema, ref):
    if not ref.startswith("#/"):
        return None
    parts = ref[2:].split("/")
    cur = root_schema
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def check_scalar(value, sch, path, errs):
    if "type" in sch:
        names = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_type_ok(value, n) for n in names):
            errs.append("%s: expected type %s" % (path, "|".join(names)))
            return
    if "const" in sch and value != sch["const"]:
        errs.append("%s: must equal %r (got %r)" % (path, sch["const"], value))
    if "enum" in sch and value not in sch["enum"]:
        errs.append("%s: %r not in enum %r" % (path, value, sch["enum"]))
    if isinstance(value, str):
        if "pattern" in sch and not re.search(sch["pattern"], value):
            errs.append("%s: does not match pattern %s" % (path, sch["pattern"]))
        if "minLength" in sch and len(value) < sch["minLength"]:
            errs.append("%s: length %d below minLength %d" % (path, len(value), sch["minLength"]))
        if "maxLength" in sch and len(value) > sch["maxLength"]:
            errs.append("%s: length %d exceeds maxLength %d" % (path, len(value), sch["maxLength"]))
    if _type_ok(value, "number"):
        if "minimum" in sch and value < sch["minimum"]:
            errs.append("%s: value %r below minimum %r" % (path, value, sch["minimum"]))
        if "maximum" in sch and value > sch["maximum"]:
            errs.append("%s: value %r exceeds maximum %r" % (path, value, sch["maximum"]))


def expand_node(value, sch, path, errs, stack, root_schema):
    if "$ref" in sch:
        target = _resolve_ref(root_schema, sch["$ref"])
        if target is None:
            errs.append("%s: unresolved $ref %s" % (path, sch["$ref"]))
            return
        stack.append((value, target, path))
        return

    if isinstance(value, dict) and "properties" in sch:
        for req in sch.get("required", []):
            if req not in value:
                errs.append("%s: missing required property %r" % (path, req))
        for key, val in value.items():
            if key in sch["properties"]:
                stack.append((val, sch["properties"][key], path + "." + key))
            elif sch.get("additionalProperties") is False:
                errs.append("%s: unexpected property %r" % (path, key))

    if isinstance(value, list) and "items" in sch:
        if "minItems" in sch and len(value) < sch["minItems"]:
            errs.append("%s: fewer than %d items (got %d)" % (path, sch["minItems"], len(value)))
        for idx, val in enumerate(value):
            stack.append((val, sch["items"], "%s[%d]" % (path, idx)))


def validate_schema(instance, schema):
    """Validate JSON instance against the JSON Schema subset used in record.schema.json."""
    errs = []
    stack = [(instance, schema, "$")]
    max_nodes = 50000
    for _ in range(max_nodes):
        if not stack:
            return errs
        value, sch, path = stack.pop()
        check_scalar(value, sch, path, errs)
        expand_node(value, sch, path, errs, stack, schema)
    return errs + ["schema walk exceeded node budget (%d)" % max_nodes]


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

    total = len(SELF_TEST_CASES)
    print("\nself-test summary: %d/%d checks passed" % (passed_count, total))
    return 0 if passed_count == total else 1


def main(argv):
    if len(argv) == 2 and argv[1] == "--self-test":
        return run_self_test()

    if len(argv) != 2:
        sys.stderr.write("usage: check-record.py RECORD.json | --self-test\n")
        return 2

    path = Path(argv[1])
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
    sys.exit(main(sys.argv))
