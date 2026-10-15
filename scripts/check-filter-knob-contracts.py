#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Verify per-filter control-plane knob JSON contracts against C AVOption tables.

Parses AVOption tables in ``ffmpeg-patches/files/`` (including the shared
``PEL_VK_POOL_OPTIONS`` macro in ``pelorus_vulkan_pool.h`` and named C
constants across the patch stack and ``libpelorus/include/pelorus/``), checks
each filter's contract under ``docs/api/knobs/<filter>.json``, and verifies
that ``pelorus_deband_vulkan`` stays in lockstep with the authoritative frozen
table in ``docs/api/control-plane.md``.

Exit codes:
  0 — all contracts and self-tests pass
  1 — contract drift, missing/stale contract file, or self-test failure
  2 — unreadable inputs, unparseable C table, or unresolved named constant
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
STABILITY = "pre-1.0"
VALID_EFFECT_CLASSES = ("strength", "shape", "topology", "reporting")
ROOT_KEYS = (
    "schema_version",
    "stability",
    "filter",
    "kind",
    "source",
    "options",
)
OPTION_KEYS = (
    "name",
    "type",
    "min",
    "max",
    "default",
    "unit",
    "constants",
    "effect_class",
    "tunable",
    "help",
)
CONST_KEYS = ("name", "value", "help")

BUILTIN_CONSTANTS: dict[str, int | float] = {
    "INT_MAX": 2147483647,
    "INT_MIN": -2147483648,
    "UINT_MAX": 4294967295,
    "INT64_MAX": 9223372036854775807,
    "INT64_MIN": -9223372036854775808,
    "FLT_MAX": 3.4028234663852886e38,
    "DBL_MAX": 1.7976931348623157e308,
}

EFFECT_CLASS_MAP: dict[str, dict[str, str]] = {
    "pelorus_aa_vulkan": {
        "blur": "strength",
        "depth": "strength",
        "thresh": "strength",
        "darkstr": "strength",
        "edge": "strength",
        "planes": "topology",
        "fast": "topology",
        "tiling": "topology",
        "drm_modifiers": "topology",
    },
    "pelorus_analyze_vulkan": {
        "flat": "strength",
        "roi": "reporting",
        "roi_strength": "strength",
        "grad_lo": "strength",
        "grad_hi": "strength",
        "cell": "shape",
        "maps": "reporting",
    },
    "pelorus_borderfix_vulkan": {
        "left": "shape",
        "right": "shape",
        "top": "shape",
        "bottom": "shape",
        "planes": "topology",
        "tiling": "topology",
        "drm_modifiers": "topology",
    },
    "pelorus_deband_vulkan": {
        "range": "strength",
        "thry": "strength",
        "thrc": "strength",
        "grainy": "strength",
        "grainc": "strength",
        "softness": "strength",
        "detail": "strength",
        "sample": "shape",
        "blur": "shape",
        "dither": "strength",
        "dynamic": "strength",
        "protect": "strength",
        "planes": "topology",
        "meta": "reporting",
        "tiling": "topology",
        "drm_modifiers": "topology",
    },
    "pelorus_deblock_vulkan": {
        "bsize": "shape",
        "edge": "shape",
        "thr": "strength",
        "str": "strength",
        "planes": "topology",
        "tiling": "topology",
        "drm_modifiers": "topology",
    },
    "pelorus_dehalo_vulkan": {
        "blur": "strength",
        "darkstr": "strength",
        "brightstr": "strength",
        "lowsens": "strength",
        "highsens": "strength",
        "edge": "strength",
        "ring": "strength",
        "planes": "topology",
        "tile": "topology",
        "tiling": "topology",
        "drm_modifiers": "topology",
    },
    "pelorus_denoise_vulkan": {
        "sigma": "strength",
        "sigmac": "strength",
        "sigmat": "strength",
        "strength": "strength",
        "strengthc": "strength",
        "blend": "strength",
        "tdecay": "strength",
        "tcut": "strength",
        "patch": "shape",
        "prev": "shape",
        "protect": "strength",
        "planes": "topology",
        "meta": "reporting",
        "mc": "topology",
        "tile": "topology",
        "lookahead": "topology",
        "tiling": "topology",
        "drm_modifiers": "topology",
    },
    "pelorus_grain_estimate_vulkan": {
        "edge": "strength",
        "strength": "strength",
        "model": "shape",
        "native": "reporting",
    },
    "pelorus_mc_vulkan": {
        "bsize": "shape",
        "search": "shape",
        "meta": "reporting",
    },
    "pelorus_scenecut": {
        "force_idr": "topology",
    },
    "pelorus_fgs": {
        "model_id": "shape",
        "blending_mode": "shape",
        "log2_scale": "strength",
        "components": "topology",
        "intensity_low": "shape",
        "intensity_high": "shape",
        "intensity_low_c": "shape",
        "intensity_high_c": "shape",
        "scale_y": "strength",
        "scale_c": "strength",
        "cutoff_h": "strength",
        "cutoff_v": "strength",
        "persistence": "topology",
        "skip_existing": "topology",
    },
}

TYPE_MAP: dict[str, str] = {
    "AV_OPT_TYPE_INT": "int",
    "AV_OPT_TYPE_INT64": "int64",
    "AV_OPT_TYPE_DOUBLE": "double",
    "AV_OPT_TYPE_FLOAT": "float",
    "AV_OPT_TYPE_BOOL": "bool",
    "AV_OPT_TYPE_STRING": "string",
    "AV_OPT_TYPE_FLAGS": "flags",
}

DEFINE_RE = re.compile(
    r"^\s*#\s*define\s+([A-Z_][A-Z0-9_]*)[ \t]+([^\n/]+)", re.MULTILINE
)
INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
ENUM_BLOCK_RE = re.compile(
    r"\benum(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*\{([^}]*)\}", re.DOTALL
)
SHIFT_RE = re.compile(r"^\(?\s*(\d+)[uUlL]*\s*<<\s*(\d+)[uUlL]*\s*\)?$")
STRING_LIT_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
FILTER_NAME_RE = re.compile(
    r"const\s+(FFFilter|FFBitStreamFilter)\s+\w+\s*=\s*\{.*?"
    r"\.p\.name\s*=\s*\"([^\"]+)\"",
    re.DOTALL,
)
OPTIONS_HEADER_RE = re.compile(
    r"static\s+const\s+AVOption\s+\w+_options\[\]\s*=\s*"
)
POOL_MACRO_RE = re.compile(
    r"^[ \t]*#\s*define\s+PEL_VK_POOL_OPTIONS\([^)]*\)"
    r"((?:[^\n]*\\[ \t]*\n)*[^\n]*)",
    re.MULTILINE,
)


class GateError(Exception):
    """Raised when inputs are missing, malformed, or have unresolved symbols."""


def strip_c_comments(text: str) -> str:
    """Remove C block and line comments while preserving newlines."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text)


def parse_scalar_token(
    raw: str, known: dict[str, int | float]
) -> int | float | None:
    """Parse a simple C scalar token or constant reference without eval."""
    tok = raw.strip()
    m_shift = SHIFT_RE.match(tok)
    if m_shift:
        return int(m_shift.group(1)) << int(m_shift.group(2))
    if tok.startswith("(") and tok.endswith(")"):
        tok = tok[1:-1].strip()
    if tok in known:
        return known[tok]
    m_int = re.match(r"^(-?(?:0x[0-9a-fA-F]+|\d+))[uUlL]*$", tok)
    if m_int:
        return int(m_int.group(1), 0)
    m_flt = re.match(
        r"^(-?(?:\d+\.\d*|\.\d+|\d+e[+-]?\d+|\d+\.\d*e[+-]?\d+))[fFlL]?$",
        tok,
        re.IGNORECASE,
    )
    if m_flt:
        return float(m_flt.group(1))
    return None


def _register_constant(
    name: str,
    val: int | float,
    origin: str,
    constants: dict[str, int | float],
    origins: dict[str, str],
) -> None:
    """Store a constant or raise GateError on a conflicting duplicate value."""
    if name in constants:
        prev = constants[name]
        if prev != val or isinstance(prev, float) != isinstance(val, float):
            raise GateError(
                f"conflicting constant {name!r}: {prev!r} ({origins[name]}) "
                f"vs {val!r} ({origin})"
            )
        return
    constants[name] = val
    origins[name] = origin


def _scan_enum_items(
    body: str,
    origin: str,
    constants: dict[str, int | float],
    origins: dict[str, str],
) -> None:
    """Register integer constants from a single C `enum { ... }` block."""
    next_val = 0
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            ename, expr = item.split("=", 1)
            parsed = parse_scalar_token(expr, constants)
            if parsed is not None and isinstance(parsed, int):
                next_val = parsed
            item_name = ename.strip()
        else:
            item_name = item
        _register_constant(item_name, next_val, origin, constants, origins)
        next_val += 1


def _scan_file_constants(
    cleaned: str,
    origin: str,
    constants: dict[str, int | float],
    origins: dict[str, str],
    deferred: list[tuple[str, str, str]],
) -> None:
    """Populate `constants` from `#define` and `enum` blocks in `cleaned` C source."""
    active = re.sub(
        r"^\s*#\s*else\b.*?^\s*#\s*endif\b",
        "",
        cleaned,
        flags=re.DOTALL | re.MULTILINE,
    )
    folded = re.sub(r"\\[ \t]*\n", " ", active)
    for match in DEFINE_RE.finditer(folded):
        name, val_str = match.group(1), match.group(2).strip()
        val = parse_scalar_token(val_str, constants)
        if val is not None:
            _register_constant(name, val, origin, constants, origins)
        elif re.match(r"^[A-Z_][A-Z0-9_]*$", val_str):
            deferred.append((name, val_str, origin))
    for match in ENUM_BLOCK_RE.finditer(folded):
        _scan_enum_items(match.group(1), origin, constants, origins)


def _resolve_included_headers(root: Path, c_file: Path) -> list[Path]:
    """Return shared repository headers transitively included by `c_file`."""
    search_dirs = (root / "ffmpeg-patches/files", root / "libpelorus/include")
    queue: list[Path] = [c_file]
    seen: set[Path] = {c_file.resolve()}
    headers: list[Path] = []
    while queue:
        cur = queue.pop(0)
        cleaned = strip_c_comments(cur.read_text(encoding="utf-8"))
        for inc in INCLUDE_RE.findall(cleaned):
            for base_dir in search_dirs:
                cand = base_dir / inc
                if cand.is_file() and cand.resolve() not in seen:
                    seen.add(cand.resolve())
                    headers.append(cand)
                    queue.append(cand)
                    break
    return headers


def collect_file_constants(root: Path, c_file: Path) -> dict[str, int | float]:
    """Collect constants for `c_file` (source file first, then included headers)."""
    constants: dict[str, int | float] = dict(BUILTIN_CONSTANTS)
    origins: dict[str, str] = {k: "<builtin>" for k in BUILTIN_CONSTANTS}
    deferred: list[tuple[str, str, str]] = []
    for path in [c_file, *_resolve_included_headers(root, c_file)]:
        origin = path.relative_to(root).as_posix()
        cleaned = strip_c_comments(path.read_text(encoding="utf-8"))
        _scan_file_constants(cleaned, origin, constants, origins, deferred)
    for name, ref_name, origin in deferred:
        val = parse_scalar_token(ref_name, constants)
        if val is not None:
            _register_constant(name, val, origin, constants, origins)
    return constants


def resolve_number(
    expr: str,
    constants: dict[str, int | float],
    want_float: bool,
    context: str,
) -> int | float:
    """Resolve a C constant or numeric literal, failing by name if unknown."""
    val = parse_scalar_token(expr, constants)
    if val is None:
        raise GateError(f"unresolved constant {expr.strip()!r} in {context}")
    return float(val) if want_float else int(val)


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split C initializer text on top-level separator outside braces/parens/strings."""
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    in_str = False
    escape = False
    for ch in text:
        if escape:
            cur.append(ch)
            escape = False
            continue
        if ch == "\\" and in_str:
            cur.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            cur.append(ch)
            continue
        if not in_str:
            if ch in "{(":
                depth += 1
            elif ch in "})":
                depth = max(0, depth - 1)
            elif ch == sep and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def extract_brace_entries(body: str) -> list[str]:
    """Return top-level `{ ... }` entry bodies from C source text."""
    entries: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for idx, ch in enumerate(body):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = idx + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                entries.append(body[start:idx].strip())
                start = -1
    return entries


def parse_c_string(expr: str, context: str) -> str | None:
    """Join adjacent C string literals or return None for NULL."""
    stripped = expr.strip()
    if stripped == "NULL":
        return None
    pieces = STRING_LIT_RE.findall(stripped)
    if not pieces:
        raise GateError(f"expected string literal or NULL in {context}: {stripped!r}")
    return "".join(pieces)


def parse_default_val(
    expr: str,
    opt_type: str,
    constants: dict[str, int | float],
    context: str,
) -> int | float | str | None:
    """Parse `{.i64 = ...}`, `{.dbl = ...}`, or `{.str = ...}` default initializer."""
    s = expr.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    m = re.match(r"^\.(i64|dbl|str)\s*=\s*(.+)$", s, re.DOTALL)
    if not m:
        raise GateError(f"unrecognised default initializer {expr!r} in {context}")
    field, rhs = m.group(1), m.group(2).strip()
    if opt_type == "string" or field == "str":
        return parse_c_string(rhs, context)
    want_float = opt_type in ("double", "float")
    return resolve_number(rhs, constants, want_float, context)


def parse_unit_field(fields: list[str], context: str) -> str | None:
    """Extract `.unit = "..."` or positional unit string from trailing fields."""
    if len(fields) < 9:
        return None
    raw = fields[8].strip()
    m = re.match(r"^\.unit\s*=\s*(.+)$", raw)
    target = m.group(1).strip() if m else raw
    return parse_c_string(target, context)


def parse_pool_macro_entries(root: Path) -> str:
    """Read `PEL_VK_POOL_OPTIONS` body from `pelorus_vulkan_pool.h`."""
    header = root / "ffmpeg-patches/files/pelorus_vulkan_pool.h"
    if not header.is_file():
        raise GateError(f"missing pool header: {header}")
    cleaned = strip_c_comments(header.read_text(encoding="utf-8"))
    m = POOL_MACRO_RE.search(cleaned)
    if not m:
        raise GateError("could not find PEL_VK_POOL_OPTIONS in pelorus_vulkan_pool.h")
    return re.sub(r"\\[ \t]*\n", "\n", m.group(1))


def _build_option_dict(
    fields: list[str],
    name: str,
    help_text: str,
    c_type: str,
    unit: str | None,
    source_rel: str,
    constants: dict[str, int | float],
    effect_map: dict[str, str],
) -> dict[str, Any]:
    """Build a single non-const option dictionary from parsed AVOption fields."""
    ctx = f"{source_rel}:{name}"
    if c_type not in TYPE_MAP:
        raise GateError(f"unsupported AVOption type {c_type!r} in {ctx}")
    opt_type = TYPE_MAP[c_type]
    want_float = opt_type in ("double", "float")
    default_val = parse_default_val(fields[4], opt_type, constants, f"{ctx}:default")
    min_raw = resolve_number(fields[5], constants, want_float, f"{ctx}:min")
    max_raw = resolve_number(fields[6], constants, want_float, f"{ctx}:max")
    min_val = None if opt_type == "string" and min_raw == 0 else min_raw
    max_val = None if opt_type == "string" and max_raw == 0 else max_raw
    effect_class = effect_map.get(name)
    return {
        "name": name,
        "type": opt_type,
        "min": min_val,
        "max": max_val,
        "default": default_val,
        "unit": unit,
        "constants": [],
        "effect_class": effect_class,
        "tunable": effect_class == "strength",
        "help": help_text,
    }


def parse_avoptions_from_body(
    array_body: str,
    source_rel: str,
    constants: dict[str, int | float],
    effect_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Parse all non-NULL AVOption entries and attach AV_OPT_TYPE_CONST rows."""
    options: list[dict[str, Any]] = []
    by_unit: dict[str, dict[str, Any]] = {}

    for entry in extract_brace_entries(array_body):
        fields = split_top_level(entry)
        if not fields or fields[0].strip() == "NULL":
            continue
        if len(fields) < 8:
            raise GateError(f"short AVOption row in {source_rel}: {entry!r}")
        name = parse_c_string(fields[0], f"{source_rel}:name")
        if name is None:
            continue
        ctx = f"{source_rel}:{name}"
        help_text = parse_c_string(fields[1], f"{ctx}:help") or ""
        c_type = fields[3].strip()
        unit = parse_unit_field(fields, ctx)

        if c_type == "AV_OPT_TYPE_CONST":
            val = parse_default_val(fields[4], "int", constants, f"{ctx}:default")
            if not unit or unit not in by_unit:
                raise GateError(
                    f"orphan AV_OPT_TYPE_CONST {name!r} (unit={unit!r}) in {source_rel}"
                )
            by_unit[unit]["constants"].append(
                {"name": name, "value": int(val), "help": help_text}
            )
            if by_unit[unit]["type"] == "int":
                by_unit[unit]["type"] = "const-enum"
            continue

        opt_dict = _build_option_dict(
            fields,
            name,
            help_text,
            c_type,
            unit,
            source_rel,
            constants,
            effect_map,
        )
        options.append(opt_dict)
        if unit:
            by_unit[unit] = opt_dict

    return options


def build_contracts_from_sources(root: Path) -> dict[str, dict[str, Any]]:
    """Scan `ffmpeg-patches/files/*.c` and build expected contract objects."""
    files_dir = root / "ffmpeg-patches/files"
    if not files_dir.is_dir():
        raise GateError(f"missing source directory: {files_dir}")
    pool_macro_body = parse_pool_macro_entries(root)
    contracts: dict[str, dict[str, Any]] = {}

    for c_file in sorted(files_dir.glob("*.c")):
        cleaned = strip_c_comments(c_file.read_text(encoding="utf-8"))
        m_hdr = OPTIONS_HEADER_RE.search(cleaned)
        if not m_hdr:
            continue
        outer = extract_brace_entries(cleaned[m_hdr.end() :])
        if not outer:
            raise GateError(f"malformed AVOption array in {c_file.name}")
        m_name = FILTER_NAME_RE.search(cleaned)
        if not m_name:
            raise GateError(f"AVOption table without FFFilter name in {c_file.name}")
        kind_struct, filter_name = m_name.group(1), m_name.group(2)
        kind = "bsf" if kind_struct == "FFBitStreamFilter" else "vf"
        source_rel = c_file.relative_to(root).as_posix()
        constants = collect_file_constants(root, c_file)
        body = re.sub(
            r"PEL_VK_POOL_OPTIONS\([^)]*\)", pool_macro_body, outer[0]
        )
        effect_map = EFFECT_CLASS_MAP.get(filter_name, {})
        options = parse_avoptions_from_body(
            body, source_rel, constants, effect_map
        )
        contracts[filter_name] = {
            "schema_version": SCHEMA_VERSION,
            "stability": STABILITY,
            "filter": filter_name,
            "kind": kind,
            "source": source_rel,
            "options": options,
        }

    if not contracts:
        raise GateError("no filter AVOption tables found in ffmpeg-patches/files")
    return contracts


def _collect_unclassified_options(exp: dict[str, Any]) -> list[str]:
    """Return error strings for any options in `exp` lacking an `effect_class`."""
    filter_name = exp["filter"]
    source_rel = exp["source"]
    return [
        f"{filter_name}.{opt['name']}: unclassified option in {source_rel}"
        for opt in exp["options"]
        if opt["effect_class"] is None
    ]


def compare_option(
    filter_name: str, exp: dict[str, Any], act: dict[str, Any]
) -> list[str]:
    """Compare one expected option dict against actual contract JSON."""
    errs: list[str] = []
    opt_name = exp["name"]
    if tuple(act.keys()) != OPTION_KEYS:
        errs.append(
            f"{filter_name}.{opt_name}.keys: expected {list(OPTION_KEYS)}, "
            f"got {list(act.keys())}"
        )
    for field in OPTION_KEYS:
        if field not in act:
            continue
        if exp["effect_class"] is None and field in ("effect_class", "tunable"):
            continue
        if act[field] != exp[field]:
            errs.append(
                f"{filter_name}.{opt_name}.{field}: expected {exp[field]!r}, "
                f"got {act[field]!r}"
            )
    if act.get("effect_class") not in VALID_EFFECT_CLASSES:
        errs.append(
            f"{filter_name}.{opt_name}.effect_class: invalid {act.get('effect_class')!r}"
        )
    if act.get("tunable") != (act.get("effect_class") == "strength"):
        errs.append(
            f"{filter_name}.{opt_name}.tunable: must equal (effect_class == 'strength')"
        )
    return errs


def compare_contract(
    filter_name: str, exp: dict[str, Any], act: dict[str, Any]
) -> list[str]:
    """Compare one filter's expected contract against on-disk JSON."""
    errs: list[str] = []
    if tuple(act.keys()) != ROOT_KEYS:
        errs.append(
            f"{filter_name}.root_keys: expected {list(ROOT_KEYS)}, got {list(act.keys())}"
        )
    for top_key in ("schema_version", "stability", "filter", "kind", "source"):
        if act.get(top_key) != exp[top_key]:
            errs.append(
                f"{filter_name}.{top_key}: expected {exp[top_key]!r}, "
                f"got {act.get(top_key)!r}"
            )
    act_opts = act.get("options")
    if not isinstance(act_opts, list):
        errs.append(f"{filter_name}.options: expected list")
        return errs

    exp_by_name = {o["name"]: o for o in exp["options"]}
    act_by_name = {o.get("name"): o for o in act_opts if isinstance(o, dict)}
    for name in exp_by_name:
        if name not in act_by_name:
            errs.append(f"{filter_name}.{name}: missing option in contract")
    for name in act_by_name:
        if name not in exp_by_name:
            errs.append(f"{filter_name}.{name}: stale option not in C AVOption table")
    for name, exp_opt in exp_by_name.items():
        if name in act_by_name:
            errs.extend(compare_option(filter_name, exp_opt, act_by_name[name]))
    return errs


def _parse_control_plane_tables(
    text: str,
) -> tuple[dict[str, tuple[str, str, str, str]], set[str]]:
    """Extract frozen rows and excluded option names from `control-plane.md`."""
    m_frozen = re.search(
        r"## Frozen knobs[^\n]*\n(.*?)(?:\n## |\Z)", text, re.DOTALL
    )
    m_excl = re.search(
        r"## Out-of-contract options[^\n]*\n(.*?)(?:\n## |\Z)", text, re.DOTALL
    )
    if not m_frozen or not m_excl:
        raise GateError("control-plane.md missing Frozen or Out-of-contract section")

    frozen_rows: dict[str, tuple[str, str, str, str]] = {}
    for line in m_frozen.group(1).splitlines():
        m = re.match(
            r"^\|\s*`([a-z0-9_]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*"
            r"([^|]+?)\s*\|\s*([^|]+?)\s*\|",
            line.strip(),
        )
        if m:
            frozen_rows[m.group(1)] = (
                m.group(2).strip(),
                m.group(3).strip(),
                m.group(4).strip(),
                m.group(5).strip().split()[0],
            )

    excluded_names = {
        m.group(1)
        for line in m_excl.group(1).splitlines()
        if (m := re.match(r"^\|\s*`([a-z0-9_]+)`", line.strip()))
    }
    return frozen_rows, excluded_names


def verify_deband_control_plane_doc(
    root: Path, deband_contract: dict[str, Any]
) -> list[str]:
    """Assert `docs/api/control-plane.md` frozen/excluded tables match deband."""
    doc_path = root / "docs/api/control-plane.md"
    if not doc_path.is_file():
        raise GateError(f"missing {doc_path}")
    frozen_rows, excluded_names = _parse_control_plane_tables(
        doc_path.read_text(encoding="utf-8")
    )
    errs: list[str] = []
    opts = {o["name"]: o for o in deband_contract["options"]}
    strength_names = {o["name"] for o in deband_contract["options"] if o["tunable"]}
    if set(frozen_rows.keys()) != strength_names:
        errs.append(
            "control-plane.md frozen set mismatch: "
            f"doc={sorted(frozen_rows.keys())} vs contract={sorted(strength_names)}"
        )
    non_strength = set(opts.keys()) - strength_names
    if excluded_names != non_strength:
        errs.append(
            "control-plane.md excluded set mismatch: "
            f"doc={sorted(excluded_names)} vs contract={sorted(non_strength)}"
        )

    doc_type_map = {"int (enum)": "const-enum"}
    for name, (dtype, dmin, dmax, ddef) in frozen_rows.items():
        if name not in opts:
            continue
        opt = opts[name]
        norm_type = doc_type_map.get(dtype, dtype)
        if norm_type != opt["type"]:
            errs.append(
                f"control-plane.md.{name}.type: doc={norm_type!r}, contract={opt['type']!r}"
            )
        conv = float if opt["type"] in ("double", "float") else int
        for label, raw_val, key in (("min", dmin, "min"), ("max", dmax, "max"), ("default", ddef, "default")):
            if conv(raw_val) != opt[key]:
                errs.append(
                    f"control-plane.md.{name}.{label}: doc={conv(raw_val)!r}, contract={opt[key]!r}"
                )
    return errs


def write_contracts(root: Path, contracts: dict[str, dict[str, Any]]) -> None:
    """Write `<root>/docs/api/knobs/<filter>.json` files with stable key order."""
    knobs_dir = root / "docs/api/knobs"
    knobs_dir.mkdir(parents=True, exist_ok=True)
    for filter_name, contract in sorted(contracts.items()):
        out_path = knobs_dir / f"{filter_name}.json"
        out_path.write_text(
            json.dumps(contract, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )


def check_all(
    root: Path, expected: dict[str, dict[str, Any]] | None = None
) -> list[str]:
    """Check all filter contracts in `<root>/docs/api/knobs/` against C sources."""
    if expected is None:
        expected = build_contracts_from_sources(root)
    knobs_dir = root / "docs/api/knobs"
    errs: list[str] = []

    existing_files = (
        {p.stem: p for p in sorted(knobs_dir.glob("*.json"))}
        if knobs_dir.is_dir()
        else {}
    )
    for filter_name in sorted(expected):
        if filter_name not in existing_files:
            errs.append(
                f"{filter_name}: missing contract file docs/api/knobs/{filter_name}.json"
            )
    for stem in sorted(existing_files):
        if stem not in expected:
            errs.append(
                f"{stem}: stale contract file docs/api/knobs/{stem}.json "
                "(filter no longer exists)"
            )

    for filter_name, exp in sorted(expected.items()):
        errs.extend(_collect_unclassified_options(exp))
        if filter_name not in existing_files:
            continue
        try:
            act = json.loads(existing_files[filter_name].read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GateError(f"invalid JSON in {existing_files[filter_name]}: {exc}") from exc
        errs.extend(compare_contract(filter_name, exp, act))

    if "pelorus_deband_vulkan" in expected:
        errs.extend(
            verify_deband_control_plane_doc(root, expected["pelorus_deband_vulkan"])
        )
    return errs


def make_sandbox_from_repo(root: Path, tmp: Path) -> Path:
    """Create a temporary sandbox with C sources, headers, control-plane doc, and contracts."""
    for rel_dir in (
        "ffmpeg-patches/files",
        "libpelorus/include/pelorus",
        "docs/api",
    ):
        src = root / rel_dir
        dst = tmp / rel_dir
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    contracts = build_contracts_from_sources(tmp)
    write_contracts(tmp, contracts)
    return tmp


def _mutate_json(path: Path, fn: Any) -> None:
    """Load JSON at `path`, mutate it in-place with `fn`, and write it back."""
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _replace_in_file(path: Path, old: str, new: str) -> None:
    """Replace the first occurrence of `old` with `new` in `path`."""
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise GateError(f"self-test target {old!r} not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _st_stability_drift(sandbox: Path) -> None:
    def _set_stable(data: dict[str, Any]) -> None:
        data["stability"] = "stable"

    _mutate_json(
        sandbox / "docs/api/knobs/pelorus_deband_vulkan.json", _set_stable
    )


def _st_json_range_change(sandbox: Path) -> None:
    def _set_max(data: dict[str, Any]) -> None:
        data["options"][0]["max"] = 63

    _mutate_json(sandbox / "docs/api/knobs/pelorus_deband_vulkan.json", _set_max)


def _st_json_default_change(sandbox: Path) -> None:
    def _set_default(data: dict[str, Any]) -> None:
        data["options"][0]["default"] = 0.09

    _mutate_json(sandbox / "docs/api/knobs/pelorus_denoise_vulkan.json", _set_default)


def _st_json_removed_option(sandbox: Path) -> None:
    def _add_ghost(data: dict[str, Any]) -> None:
        ghost = copy.deepcopy(data["options"][0])
        ghost["name"] = "ghost"
        data["options"].append(ghost)

    _mutate_json(sandbox / "docs/api/knobs/pelorus_deblock_vulkan.json", _add_ghost)


def _st_json_new_option(sandbox: Path) -> None:
    def _drop_first(data: dict[str, Any]) -> None:
        data["options"].pop(0)

    _mutate_json(sandbox / "docs/api/knobs/pelorus_deblock_vulkan.json", _drop_first)


def _st_missing_contract(sandbox: Path) -> None:
    (sandbox / "docs/api/knobs/pelorus_mc_vulkan.json").unlink()


def _st_stale_contract(sandbox: Path) -> None:
    shutil.copyfile(
        sandbox / "docs/api/knobs/pelorus_mc_vulkan.json",
        sandbox / "docs/api/knobs/pelorus_removed_vulkan.json",
    )


def _st_json_enum_constant_change(sandbox: Path) -> None:
    def _change_enum_val(data: dict[str, Any]) -> None:
        dither = next(o for o in data["options"] if o["name"] == "dither")
        dither["constants"][2]["value"] = 9

    _mutate_json(
        sandbox / "docs/api/knobs/pelorus_deband_vulkan.json", _change_enum_val
    )


def _st_control_plane_doc_drift(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "docs/api/control-plane.md",
        "| `range` | int | 1 | 31 | 15 |",
        "| `range` | int | 1 | 63 | 15 |",
    )


def _st_unresolved_constant(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "ffmpeg-patches/files/vf_pelorus_scenecut.c",
        "0, 1, FLAGS",
        "0, PEL_UNKNOWN_LIMIT, FLAGS",
    )


def _st_c_range_change(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "ffmpeg-patches/files/vf_pelorus_deband_vulkan.c",
        "{.i64 = 15},\n     1,\n     31,",
        "{.i64 = 15},\n     1,\n     63,",
    )


def _st_c_default_change(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "ffmpeg-patches/files/vf_pelorus_denoise_vulkan.c",
        "{.dbl = 0.03},",
        "{.dbl = 0.09},",
    )


def _st_c_unclassified_option(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "ffmpeg-patches/files/vf_pelorus_scenecut.c",
        "    { NULL }",
        '    { "mystery_knob", "planted", 0, AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 1, FLAGS },\n'
        "    { NULL }",
    )


def _st_c_new_filter_without_contract(sandbox: Path) -> None:
    new_filter_c = sandbox / "ffmpeg-patches/files/vf_pelorus_newtest_vulkan.c"
    new_filter_c.write_text(
        "static const AVOption pelorus_newtest_vulkan_options[] = {\n"
        '    {"strength", "planted", 0, AV_OPT_TYPE_DOUBLE, {.dbl = 0.5}, 0.0, 1.0, 0},\n'
        "    {NULL}};\n"
        "const FFFilter ff_vf_pelorus_newtest_vulkan = {\n"
        '    .p.name = "pelorus_newtest_vulkan",\n'
        "};\n",
        encoding="utf-8",
    )


def _st_c_flags_constant_drift(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "ffmpeg-patches/files/h265_pelorus_fgs_bsf.c",
        "#define PEL_FGS_COMP_CR (1 << 2)",
        "#define PEL_FGS_COMP_CR (1 << 3)",
    )


def _st_c_enum_constant_drift(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "libpelorus/include/pelorus/interop.h",
        "PEL_GRAIN_H274 = 2",
        "PEL_GRAIN_H274 = 3",
    )


def _st_conflicting_constant(sandbox: Path) -> None:
    _replace_in_file(
        sandbox / "ffmpeg-patches/files/h265_pelorus_fgs_bsf.c",
        "#define PEL_FGS_MODEL_FREQ 0",
        "#define PEL_FGS_MODEL_FREQ 0\n#define PEL_FGS_MODEL_FREQ 99",
    )


def _build_self_test_mutations() -> list[tuple[str, Any, str, bool]]:
    """Return `(case_name, mutator, expected_substring, expect_gate_error)` tuples."""
    return [
        ("stability_drift", _st_stability_drift, ".stability", False),
        ("range_change", _st_json_range_change, "pelorus_deband_vulkan.range.max", False),
        ("default_change", _st_json_default_change, "pelorus_denoise_vulkan.sigma.default", False),
        ("removed_option", _st_json_removed_option, "pelorus_deblock_vulkan.ghost: stale option", False),
        ("new_option", _st_json_new_option, "pelorus_deblock_vulkan.bsize: missing option", False),
        ("missing_contract", _st_missing_contract, "pelorus_mc_vulkan: missing contract file", False),
        ("stale_contract", _st_stale_contract, "pelorus_removed_vulkan: stale contract file", False),
        ("enum_constant_change", _st_json_enum_constant_change, "pelorus_deband_vulkan.dither.constants", False),
        ("control_plane_doc_drift", _st_control_plane_doc_drift, "control-plane.md.range.max", False),
        ("unresolved_constant", _st_unresolved_constant, "unresolved constant 'PEL_UNKNOWN_LIMIT'", True),
        ("c_range_change", _st_c_range_change, "pelorus_deband_vulkan.range.max", False),
        ("c_default_change", _st_c_default_change, "pelorus_denoise_vulkan.sigma.default", False),
        ("c_unclassified_option", _st_c_unclassified_option, "pelorus_scenecut.mystery_knob: unclassified option", False),
        (
            "c_new_filter_without_contract",
            _st_c_new_filter_without_contract,
            "pelorus_newtest_vulkan: missing contract file docs/api/knobs/pelorus_newtest_vulkan.json",
            False,
        ),
        ("c_flags_constant_drift", _st_c_flags_constant_drift, "pelorus_fgs.components.constants", False),
        ("c_enum_constant_drift", _st_c_enum_constant_drift, "pelorus_grain_estimate_vulkan.model.constants", False),
        ("conflicting_constant", _st_conflicting_constant, "conflicting constant 'PEL_FGS_MODEL_FREQ'", True),
    ]


def _run_single_self_test_case(
    base: Path,
    tmp_root: Path,
    name: str,
    mutate_fn: Any,
    expect_substring: str,
    expect_gate_error: bool,
) -> bool:
    """Execute one planted-defect self-test case and return True if it behaved as expected."""
    case_dir = tmp_root / name
    shutil.copytree(base, case_dir)
    mutate_fn(case_dir)
    if expect_gate_error:
        try:
            check_all(case_dir)
        except GateError as exc:
            if expect_substring in str(exc):
                return True
            print(
                f"SELF-TEST FAIL [{name}]: GateError {exc!r} missing {expect_substring!r}",
                file=sys.stderr,
            )
            return False
        print(
            f"SELF-TEST FAIL [{name}]: expected GateError, got pass/drift",
            file=sys.stderr,
        )
        return False

    errs = check_all(case_dir)
    if not errs or not any(expect_substring in e for e in errs):
        print(
            f"SELF-TEST FAIL [{name}]: expected {expect_substring!r} in {errs!r}",
            file=sys.stderr,
        )
        return False
    return True


def run_self_test(root: Path) -> int:
    """Run planted-defect self-tests verifying positive, negative, and boundary cases."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_root = Path(tmp_str)
        base = make_sandbox_from_repo(root, tmp_root / "clean")
        clean_errs = check_all(base)
        if clean_errs:
            print(
                f"SELF-TEST FAIL: clean sandbox failed: {clean_errs}",
                file=sys.stderr,
            )
            return 1

        cases = _build_self_test_mutations()
        for name, mutate_fn, expect_substring, expect_gate_error in cases:
            if not _run_single_self_test_case(
                base, tmp_root, name, mutate_fn, expect_substring, expect_gate_error
            ):
                return 1

    print(f"check-filter-knob-contracts: self-test passed ({len(cases) + 1} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument("--write", action="store_true", help="regenerate contract JSONs")
    parser.add_argument("--self-test", action="store_true", help="run planted-defect suite")
    args = parser.parse_args(argv)

    try:
        if args.self_test:
            return run_self_test(args.root)
        expected = build_contracts_from_sources(args.root)
        if args.write:
            unclassified = [
                err
                for exp in expected.values()
                for err in _collect_unclassified_options(exp)
            ]
            if unclassified:
                for err in unclassified:
                    print(f"FAIL: {err}", file=sys.stderr)
                return 1
            write_contracts(args.root, expected)
            errs = check_all(args.root, expected)
            if errs:
                for err in errs:
                    print(f"FAIL: {err}", file=sys.stderr)
                return 1
            print(
                f"check-filter-knob-contracts: wrote {len(expected)} contracts "
                "to docs/api/knobs/"
            )
            return 0

        errs = check_all(args.root, expected)
    except GateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errs:
        for err in errs:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    print(
        f"check-filter-knob-contracts: all {len(expected)} filter knob contracts "
        "match AVOption tables"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
