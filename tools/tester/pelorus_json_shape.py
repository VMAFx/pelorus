#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""JSON Schema subset validator for Pelorus (ADR-0173, #496).

Validates a JSON instance against a JSON Schema subset using bounded,
non-recursive stack iteration (HISS-01, HISS-02). Standard library only;
run it as `python3 -I`.

Supported schema keywords:
  - type (string or list of strings: object, array, string, number, integer, boolean, null)
  - const
  - enum
  - pattern
  - minLength, maxLength
  - minimum, maximum
  - minItems
  - required
  - properties
  - additionalProperties (boolean False)
  - items
  - $ref (local pointer: #/path/to/definition)

Usage:
  python3 -I tools/tester/pelorus_json_shape.py --self-test
"""

import argparse
import json
import re
import sys

MAX_NODES = 200000
MAX_DEPTH = 64


def type_ok(value: object, name: str) -> bool:
    """Check if value matches the JSON schema type name."""
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py_types = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }
    expected = py_types.get(name)
    return isinstance(value, expected) if expected is not None else False


def resolve_ref(root_schema: dict, ref: str) -> dict | None:
    """Resolve an internal #/path/to/target JSON pointer."""
    if not isinstance(ref, str):
        return None
    if ref == "#":
        return root_schema
    if not ref.startswith("#/"):
        return None
    cur: object = root_schema
    for part in ref[2:].split("/"):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur if isinstance(cur, dict) else None


def _check_type(value: object, sch: dict, path: str, errs: list[str]) -> bool:
    """Check type constraint; returns True if type matches or type keyword absent."""
    if "type" not in sch:
        return True
    raw = sch["type"]
    names = raw if isinstance(raw, list) else [raw]
    if not any(type_ok(value, n) for n in names):
        errs.append("%s: expected type %s" % (path, "|".join(names)))
        return False
    return True


def _check_string(value: str, sch: dict, path: str, errs: list[str]) -> None:
    """Check string constraints (pattern, minLength, maxLength)."""
    if "pattern" in sch and not re.search(sch["pattern"], value):
        errs.append("%s: does not match %s" % (path, sch["pattern"]))
    if "minLength" in sch and len(value) < sch["minLength"]:
        errs.append(
            "%s: length %d below minLength %d" % (path, len(value), sch["minLength"])
        )
    if "maxLength" in sch and len(value) > sch["maxLength"]:
        errs.append("%s: longer than %d" % (path, sch["maxLength"]))


def _check_number(value: int | float, sch: dict, path: str, errs: list[str]) -> None:
    """Check number constraints (minimum, maximum)."""
    if "minimum" in sch and value < sch["minimum"]:
        errs.append("%s: below minimum %s" % (path, sch["minimum"]))
    if "maximum" in sch and value > sch["maximum"]:
        errs.append("%s: value %r exceeds maximum %r" % (path, value, sch["maximum"]))


def check_scalar(value: object, sch: dict, path: str, errs: list[str]) -> bool:
    """Check scalar constraints on value against schema sch."""
    if not _check_type(value, sch, path, errs):
        return False
    if "const" in sch and value != sch["const"]:
        errs.append("%s: must equal %r" % (path, sch["const"]))
    if "enum" in sch and value not in sch["enum"]:
        errs.append("%s: %r not in enum" % (path, value))
    if isinstance(value, str):
        _check_string(value, sch, path, errs)
    elif type_ok(value, "number"):
        _check_number(value, sch, path, errs)
    return True


def _expand_dict(
    value: dict,
    sch: dict,
    path: str,
    depth: int,
    errs: list[str],
    stack: list[tuple[object, dict, str, int]],
) -> None:
    """Expand dict properties and check required and additionalProperties."""
    for req in sch.get("required", []):
        if req not in value:
            errs.append("%s: missing required '%s'" % (path, req))
    props = sch.get("properties")
    if isinstance(props, dict):
        for key, val in value.items():
            if key in props:
                stack.append((val, props[key], "%s.%s" % (path, key), depth + 1))
            elif sch.get("additionalProperties") is False:
                errs.append("%s: unexpected property '%s'" % (path, key))
    elif sch.get("additionalProperties") is False:
        for key in sorted(value.keys()):
            errs.append("%s: unexpected property '%s'" % (path, key))


def _expand_list(
    value: list,
    sch: dict,
    path: str,
    depth: int,
    errs: list[str],
    stack: list[tuple[object, dict, str, int]],
) -> None:
    """Expand list items and check minItems."""
    if "minItems" in sch and len(value) < sch["minItems"]:
        errs.append("%s: fewer than %d items" % (path, sch["minItems"]))
    if "items" in sch:
        item_sch = sch["items"]
        for idx, val in enumerate(value):
            stack.append((val, item_sch, "%s[%d]" % (path, idx), depth + 1))


def expand_node(
    value: object,
    sch: dict,
    path: str,
    depth: int,
    errs: list[str],
    stack: list[tuple[object, dict, str, int]],
    root_schema: dict,
) -> None:
    """Expand container or reference nodes onto the traversal stack."""
    if "$ref" in sch:
        target = resolve_ref(root_schema, sch["$ref"])
        if target is None:
            errs.append("%s: unresolved $ref %s" % (path, sch["$ref"]))
            return
        stack.append((value, target, path, depth))
        return

    if isinstance(value, dict):
        _expand_dict(value, sch, path, depth, errs, stack)
    elif isinstance(value, list):
        _expand_list(value, sch, path, depth, errs, stack)


def validate_schema(
    instance: object,
    schema: dict,
    *,
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> list[str]:
    """Check instance against the JSON Schema subset using bounded iteration."""
    errs: list[str] = []
    stack: list[tuple[object, dict, str, int]] = [(instance, schema, "$", 0)]
    for _ in range(max_nodes):
        if not stack:
            return errs
        value, sch, path, depth = stack.pop()
        if depth > max_depth:
            errs.append("%s: schema walk exceeded maximum depth %d" % (path, max_depth))
            continue
        if not isinstance(sch, dict):
            continue
        check_scalar(value, sch, path, errs)
        expand_node(value, sch, path, depth, errs, stack, schema)
    return errs + ["schema walk exceeded %d nodes" % max_nodes]


# ------------------------------------------------------------- self-test


def _test_types(failures: list[str]) -> None:
    cases = [
        (42, {"type": "integer"}, True),
        ("hello", {"type": "string"}, True),
        ([1, 2], {"type": "array"}, True),
        ({"a": 1}, {"type": "object"}, True),
        (True, {"type": "boolean"}, True),
        (None, {"type": "null"}, True),
        (3.14, {"type": "number"}, True),
        ("x", {"type": ["string", "null"]}, True),
        (None, {"type": ["string", "null"]}, True),
        ("not-int", {"type": "integer"}, False),
        (42, {"type": "string"}, False),
        (None, {"type": "integer"}, False),
    ]
    for val, sch, want_pass in cases:
        errs = validate_schema(val, sch)
        if bool(errs) == want_pass:
            failures.append(
                "type check failed for %r with %r: errs=%r" % (val, sch, errs)
            )


def _test_bool_int(failures: list[str]) -> None:
    bool_cases = [
        (True, {"type": "integer"}),
        (False, {"type": "integer"}),
        (True, {"type": "number"}),
        (False, {"type": "number"}),
    ]
    for val, sch in bool_cases:
        errs = validate_schema(val, sch)
        if not errs or not any("expected type" in e for e in errs):
            failures.append("bool %r was accepted as %s: %r" % (val, sch["type"], errs))

    if validate_schema(0, {"type": "integer"}):
        failures.append("integer 0 rejected as integer")
    if validate_schema(1, {"type": "number"}):
        failures.append("integer 1 rejected as number")


def _test_scalar_bounds(failures: list[str]) -> None:
    cases = [
        ("exact", {"const": "exact"}, True),
        ("wrong", {"const": "exact"}, False),
        ("alpha", {"enum": ["alpha", "beta"]}, True),
        ("gamma", {"enum": ["alpha", "beta"]}, False),
        ("only", {"enum": ["only"]}, True),
        ("abc12", {"pattern": "^[a-z]+[0-9]+$"}, True),
        ("12abc", {"pattern": "^[a-z]+[0-9]+$"}, False),
        ("abc", {"minLength": 3}, True),
        ("abcd", {"minLength": 3}, True),
        ("ab", {"minLength": 3}, False),
        ("12345", {"maxLength": 5}, True),
        ("1234", {"maxLength": 5}, True),
        ("123456", {"maxLength": 5}, False),
        (10, {"minimum": 10}, True),
        (11, {"minimum": 10}, True),
        (9, {"minimum": 10}, False),
        (20, {"maximum": 20}, True),
        (19, {"maximum": 20}, True),
        (21, {"maximum": 20}, False),
    ]
    for val, sch, want_pass in cases:
        errs = validate_schema(val, sch)
        if bool(errs) == want_pass:
            failures.append(
                "scalar check failed for %r with %r: errs=%r" % (val, sch, errs)
            )


def _test_objects(failures: list[str]) -> None:
    req_sch = {"type": "object", "required": ["id", "kind"]}
    if validate_schema({"id": "x", "kind": "y"}, req_sch):
        failures.append("valid required properties rejected")
    if not validate_schema({"id": "x"}, req_sch):
        failures.append("missing required property accepted")
    if validate_schema({}, {"type": "object", "required": []}):
        failures.append("empty required list rejected")

    add_sch = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "additionalProperties": False,
    }
    if validate_schema({"a": 1}, add_sch):
        failures.append("valid property rejected with additionalProperties false")
    if not validate_schema({"a": 1, "b": 2}, add_sch):
        failures.append("extra property accepted with additionalProperties false")
    if validate_schema(
        {"a": 1, "b": 2}, {"type": "object", "properties": {"a": {"type": "integer"}}}
    ):
        failures.append("extra property rejected when additionalProperties allowed")


def _test_arrays(failures: list[str]) -> None:
    items_sch = {"type": "array", "minItems": 2, "items": {"type": "integer"}}
    if validate_schema([1, 2], items_sch):
        failures.append("boundary minItems 2 rejected for len 2")
    if validate_schema([1, 2, 3], items_sch):
        failures.append("minItems 2 rejected for len 3")
    if not validate_schema([1], items_sch):
        failures.append("boundary failure minItems 2 accepted for len 1")
    if validate_schema([], {"type": "array", "minItems": 0}):
        failures.append("minItems 0 rejected for empty list")


def _test_refs_and_caps(failures: list[str]) -> None:
    schema = {
        "$defs": {
            "point": {
                "type": "object",
                "required": ["x"],
                "properties": {"x": {"type": "integer"}},
            }
        },
        "type": "array",
        "items": {"$ref": "#/$defs/point"},
    }
    if validate_schema([{"x": 1}, {"x": 2}], schema):
        failures.append("valid $ref rejected")
    if not validate_schema([{"x": "bad"}], schema):
        failures.append("invalid $ref target value accepted")
    if not validate_schema([{"x": 1}], {"items": {"$ref": "#/$defs/missing"}}):
        failures.append("unresolved $ref accepted")

    tree: dict = {"v": 0}
    cur = tree
    for i in range(1, 5):
        cur["next"] = {"v": i}
        cur = cur["next"]
    rec_sch = {"type": "object", "properties": {"next": {"$ref": "#"}}}
    if validate_schema(tree, rec_sch, max_depth=5):
        failures.append("recursive depth within cap rejected")
    deep_errs = validate_schema(tree, rec_sch, max_depth=3)
    if not deep_errs or not any("exceeded maximum depth" in e for e in deep_errs):
        failures.append("depth cap hit not reported: %r" % deep_errs)

    node_errs = validate_schema(
        [1, 2, 3, 4, 5], {"items": {"type": "integer"}}, max_nodes=3
    )
    if not node_errs or not any("exceeded 3 nodes" in e for e in node_errs):
        failures.append("node budget exceeded not reported: %r" % node_errs)


def self_test() -> int:
    """Run self-test battery; returns 0 on success, 1 on failure."""
    failures: list[str] = []
    _test_types(failures)
    _test_bool_int(failures)
    _test_scalar_bounds(failures)
    _test_objects(failures)
    _test_arrays(failures)
    _test_refs_and_caps(failures)

    if failures:
        for f in failures:
            sys.stderr.write("FAIL: %s\n" % f)
        return 1
    print("self-test: ok")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--self-test", action="store_true", help="run self-test suite")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
