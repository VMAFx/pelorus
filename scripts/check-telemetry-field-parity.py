#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Telemetry field-name parity between Pelorus and VMAFx (issue #220).

The Pelorus registry libpelorus/schema/telemetry-fields.json is the normative
list of PEL_SEC_ENC_TELEMETRY field names (ADR-0174 decision 10). VMAFx's
stream-metadata field list (VMAFx/vmafx#2271) must spell, type and unit every
shared field the same way. The rules, from docs/api/encoder-telemetry.md
("Field-name parity"):

  1. each file parses, carries its schema, has a field and no duplicate key;
  2. every Pelorus shared key is a VMAFx shared field with the same json type
     and unit, or is listed in VMAFx's not_carried;
  3. every VMAFx shared field is a Pelorus shared field, same type and unit;
  4. a VMAFx-only key never reuses a registry key;
  5. every registry key with a bit has PEL_TLM_F_<KEY> at that bit in
     interop.h, and interop.h defines no other PEL_TLM_F_*.

Exit status: 0 all rules pass; 1 a rule fails (each difference printed with
both names); 2 a file is missing, empty or malformed (never a pass); 77 the
registry and rule 5 pass but no VMAFx field list is pinned yet, which Meson
reports as SKIP with the reason printed.

    python3 -I scripts/check-telemetry-field-parity.py [--root DIR] [--vmafx FILE]
    python3 -I scripts/check-telemetry-field-parity.py --self-test
"""

import json
import re
import sys
import tempfile
from pathlib import Path

REGISTRY = Path("libpelorus/schema/telemetry-fields.json")
HEADER = Path("libpelorus/include/pelorus/interop.h")
VMAFX_SNAPSHOT = Path("libpelorus/test/fixtures/vmafx-stream-metadata-fields.json")
PELORUS_SCHEMA = "pelorus/telemetry-fields/1"
VMAFX_SCHEMA = "vmafx/stream-metadata-fields/1"
SKIP_REASON = "VMAFx field list not pinned (VMAFx/vmafx#2271)"
SOURCE_RE = re.compile(r"^VMAFx/vmafx@[0-9a-f]{40}:\S+$")
DEFINE_RE = re.compile(r"^#define\s+PEL_TLM_F_([A-Z0-9_]+)\s+(.*)$")
BIT_RE = re.compile(r"^\(UINT64_C\(1\)\s*<<\s*([0-9]+)u?\)$")
EXIT_SKIP = 77


class Malformed(Exception):
    """A missing, empty or malformed input: exit 2, never a pass."""


def load_fields(path, schema, side_values):
    """Rule 1: parse a field list and return ({key: field}, document)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise Malformed("%s: %s" % (path, err)) from err
    if not isinstance(doc, dict) or doc.get("schema") != schema:
        raise Malformed("%s: schema is not %s" % (path, schema))
    fields = doc.get("fields")
    if not isinstance(fields, list) or not fields:
        raise Malformed("%s: no fields" % path)
    out = {}
    for field in fields:
        key = field.get("key") if isinstance(field, dict) else None
        if not isinstance(key, str) or not key:
            raise Malformed("%s: a field without a key" % path)
        if key in out:
            raise Malformed("%s: duplicate key %s" % (path, key))
        if field.get(side_values[0]) not in side_values[1]:
            raise Malformed("%s: %s has %s %r" % (path, key, side_values[0],
                                                  field.get(side_values[0])))
        out[key] = field
    return out, doc


def load_registry(path):
    return load_fields(path, PELORUS_SCHEMA, ("decoder", ("shared", "encoder_only")))[0]


def load_vmafx(path):
    fields, doc = load_fields(path, VMAFX_SCHEMA, ("pelorus", ("shared", "vmafx_only")))
    if not SOURCE_RE.match(str(doc.get("source", ""))):
        raise Malformed("%s: source must name VMAFx/vmafx@<40-hex sha>:<path>" % path)
    not_carried = doc.get("not_carried", [])
    if not isinstance(not_carried, list) or not all(isinstance(k, str) for k in not_carried):
        raise Malformed("%s: not_carried must be a list of keys" % path)
    return fields, set(not_carried)


def header_bits(path):
    """{KEY: bit} of every PEL_TLM_F_* define in interop.h."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as err:
        raise Malformed("%s: %s" % (path, err)) from err
    bits = {}
    for line in lines:
        m = DEFINE_RE.match(line.strip())
        if not m:
            continue
        b = BIT_RE.match(m.group(2).strip())
        if not b:
            raise Malformed("%s: PEL_TLM_F_%s is not (UINT64_C(1) << nu)" % (path, m.group(1)))
        bits[m.group(1)] = int(b.group(1))
    if not bits:
        raise Malformed("%s: no PEL_TLM_F_* define" % path)
    return bits


def check_header(registry, bits):
    """Rule 5: registry bits and interop.h defines agree both ways."""
    errors = []
    want = {k.upper(): f["bit"] for k, f in registry.items() if f.get("bit") is not None}
    for key, bit in sorted(want.items()):
        if key not in bits:
            errors.append("missing in interop.h: PEL_TLM_F_%s" % key)
        elif bits[key] != bit:
            errors.append("bit differs: PEL_TLM_F_%s is %d in interop.h, %d in the registry"
                          % (key, bits[key], bit))
    for key in sorted(set(bits) - set(want)):
        errors.append("missing in registry: PEL_TLM_F_%s" % key)
    return errors


def same_shape(a, b):
    return a.get("json") == b.get("json") and a.get("unit") == b.get("unit")


def check_vmafx(registry, vmafx, not_carried):
    """Rules 2 to 4 between the registry and the pinned VMAFx field list."""
    errors = []
    for key, field in sorted(registry.items()):
        if field["decoder"] != "shared" or key in not_carried:
            continue
        other = vmafx.get(key)
        if other is None or other["pelorus"] != "shared":
            errors.append("missing in vmafx: %s" % key)
        elif not same_shape(field, other):
            errors.append("type/unit differs: %s" % key)
    for key, field in sorted(vmafx.items()):
        mine = registry.get(key)
        if field["pelorus"] == "vmafx_only":
            if mine is not None:
                errors.append("vmafx-only key reuses a pelorus name: %s" % key)
        elif mine is None or mine["decoder"] != "shared":
            errors.append("missing in pelorus: %s" % key)
        elif not same_shape(field, mine):
            errors.append("type/unit differs: %s" % key)
    return errors


def run(root, vmafx_path):
    """Return the exit status; prints every difference."""
    try:
        registry = load_registry(root / REGISTRY)
        errors = check_header(registry, header_bits(root / HEADER))
        snapshot = vmafx_path if vmafx_path is not None else root / VMAFX_SNAPSHOT
        if errors or (vmafx_path is None and not snapshot.exists()):
            for line in errors:
                print("telemetry-field-parity: %s" % line)
            if errors:
                return 1
            print("telemetry-field-parity: registry and interop.h agree (%d fields)"
                  % len(registry))
            print("SKIP: %s" % SKIP_REASON)
            return EXIT_SKIP
        vmafx, not_carried = load_vmafx(snapshot)
    except Malformed as err:
        print("telemetry-field-parity: malformed input: %s" % err)
        return 2
    errors = check_vmafx(registry, vmafx, not_carried)
    for line in errors:
        print("telemetry-field-parity: %s" % line)
    if not errors:
        print("telemetry-field-parity: pelorus and vmafx agree (%d shared fields)"
              % sum(1 for f in registry.values() if f["decoder"] == "shared"))
    return 1 if errors else 0


# ---- self-test --------------------------------------------------------------

def _field(key, bit, side_key, side, json_type="integer", unit="frame"):
    out = {"key": key, "json": json_type, "unit": unit, side_key: side}
    if side_key == "decoder":
        out["bit"] = bit
    return out


def _fixture_registry():
    return {"schema": PELORUS_SCHEMA, "fields": [
        _field("display_index", 0, "decoder", "shared"),
        _field("frame_bytes", 1, "decoder", "shared", unit="byte"),
        _field("psnr_y", 2, "decoder", "encoder_only", "number", "dB"),
        _field("codec", None, "decoder", "shared", "string", "none")]}


def _fixture_vmafx():
    return {"schema": VMAFX_SCHEMA, "source": "VMAFx/vmafx@%s:docs/fields.json" % ("a" * 40),
            "not_carried": ["codec"], "fields": [
                _field("display_index", None, "pelorus", "shared"),
                _field("frame_bytes", None, "pelorus", "shared", unit="byte"),
                _field("frames_since_key", None, "pelorus", "vmafx_only")]}


FIXTURE_HEADER = ("#define PEL_TLM_F_DISPLAY_INDEX (UINT64_C(1) << 0)\n"
                  "#define PEL_TLM_F_FRAME_BYTES (UINT64_C(1) << 1)\n"
                  "#define PEL_TLM_F_PSNR_Y (UINT64_C(1) << 2)\n")


def _rename(doc, old, new):
    for field in doc["fields"]:
        if field["key"] == old:
            field["key"] = new
    return doc


def _drop(doc, key):
    doc["fields"] = [f for f in doc["fields"] if f["key"] != key]
    return doc


def _set(doc, key, member, value):
    for field in doc["fields"]:
        if field["key"] == key:
            field[member] = value
    return doc


def _self_test_cases():
    """(name, registry, vmafx, header, expected exit). Each rule has a planted failure."""
    reg, vmx, hdr = _fixture_registry, _fixture_vmafx, FIXTURE_HEADER
    return [
        ("matching pair", reg(), vmx(), hdr, 0),
        ("no vmafx list pinned", reg(), None, hdr, EXIT_SKIP),
        ("renamed in pelorus", _rename(reg(), "frame_bytes", "frame_size"), vmx(),
         hdr.replace("FRAME_BYTES", "FRAME_SIZE"), 1),
        ("renamed in vmafx", reg(), _rename(vmx(), "frame_bytes", "frame_size"), hdr, 1),
        ("pelorus-only shared key", reg(), _drop(vmx(), "display_index"), hdr, 1),
        ("vmafx-only shared key", reg(),
         _set(vmx(), "frames_since_key", "pelorus", "shared"), hdr, 1),
        ("vmafx-only key reuses a name", reg(),
         _set(vmx(), "frame_bytes", "pelorus", "vmafx_only"), hdr, 1),
        ("unit differs", reg(), _set(vmx(), "frame_bytes", "unit", "bit"), hdr, 1),
        ("bit differs", reg(), vmx(), hdr.replace("<< 2", "<< 3"), 1),
        ("define without registry key", reg(), vmx(),
         hdr + "#define PEL_TLM_F_EXTRA (UINT64_C(1) << 9)\n", 1),
        ("empty registry", {"schema": PELORUS_SCHEMA, "fields": []}, vmx(), hdr, 2),
        ("malformed json", "{", vmx(), hdr, 2),
        ("empty file", "", vmx(), hdr, 2),
        ("duplicate key", _set(reg(), "psnr_y", "key", "frame_bytes"), vmx(), hdr, 2),
        ("unpinned vmafx source", reg(), dict(vmx(), source="master"), hdr, 2),
    ]


def _write_case(tmp, registry, vmafx, header):
    root = Path(tmp)
    for rel, body in ((REGISTRY, registry), (HEADER, header)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body if isinstance(body, str) else json.dumps(body),
                                encoding="utf-8")
    if vmafx is None:
        return None
    path = root / "vmafx.json"
    path.write_text(json.dumps(vmafx), encoding="utf-8")
    return path


def self_test():
    failed = 0
    for name, registry, vmafx, header, want in _self_test_cases():
        with tempfile.TemporaryDirectory() as tmp:
            vmafx_path = _write_case(tmp, registry, vmafx, header)
            got = run(Path(tmp), vmafx_path)
        if got != want:
            print("self-test FAIL %s: want exit %d, got %d" % (name, want, got))
            failed = 1
    print("self-test %s" % ("FAILED" if failed else "passed"))
    return failed


def main(argv):
    if "--self-test" in argv:
        return self_test()
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else Path(".")
    vmafx = Path(argv[argv.index("--vmafx") + 1]) if "--vmafx" in argv else None
    return run(root, vmafx)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
