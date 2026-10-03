#!/usr/bin/env python3
"""Regression checks for the shader plane loops and the deband Bayer matrix.

BUG-013: the deband `bayer8` table must equal the canonical recursive 8x8
Bayer matrix (M_2n = [[4M, 4M+2], [4M+3, 4M+1]]).
BUG-007/008: the per-plane loops of deband and borderfix must `continue` past
a plane that does not contain `pos`, never `return` (a later full-size plane
such as yuva420p alpha would otherwise stay unwritten).
BUG-025: borderfix clamp bounds must be order-safe (lo <= hi).
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "ffmpeg-patches" / "files" / "vulkan"
REFERENCE = ROOT / "libpelorus" / "shaders"


def canonical_bayer8() -> list[list[int]]:
    m = [[0]]
    while len(m) < 8:
        top = [[4 * v for v in row] + [4 * v + 2 for v in row] for row in m]
        bot = [[4 * v + 3 for v in row] + [4 * v + 1 for v in row] for row in m]
        m = top + bot
    return m  # m[y][x]


# The exact per-level update the shaders must contain (whitespace-normalised).
CANON_EXPR = "(v << 2) | ((((x >> b) & 1) ^ ((y >> b) & 1)) << 1) | ((y >> b) & 1)"


def canon_table() -> list[list[int]]:
    """Run CANON_EXPR (mirrored in Python) over the 8x8 grid."""
    table = []
    for y in range(8):
        row = []
        for x in range(8):
            v = 0
            for b in range(3):
                v = (v << 2) | ((((x >> b) & 1) ^ ((y >> b) & 1)) << 1) | ((y >> b) & 1)
            row.append(v)
        table.append(row)
    return table


def shader_bayer8_expr(text: str) -> str:
    """Return the whitespace-normalised bayer8() loop update of a shader."""
    match = re.search(
        r"for \(int b = 0; b < 3; b\+\+\) \{\s*v = (.*?);\s*\}", text, re.S
    )
    if match is None:
        raise ValueError("bayer8 loop not found")
    return " ".join(match.group(1).split())


def main() -> int:
    failures: list[str] = []
    if canon_table() != canonical_bayer8():
        failures.append("CANON_EXPR does not generate the canonical Bayer matrix")
    for path in (
        SHIPPED / "pelorus_deband.comp.glsl",
        REFERENCE / "pelorus_deband.comp",
    ):
        if shader_bayer8_expr(path.read_text()) != CANON_EXPR:
            failures.append(f"{path.name}: bayer8 is not the canonical Bayer update")

    for name in ("pelorus_deband", "pelorus_borderfix"):
        text = (SHIPPED / f"{name}.comp.glsl").read_text()
        loop = re.search(
            r"for \(uint i = 0; i < planes; i\+\+\) \{(.*?)\n    \}\n", text, re.S
        )
        if loop is None:
            failures.append(f"{name}: plane loop not found")
        elif re.search(r"\breturn\s*;", loop.group(1)):
            failures.append(f"{name}: plane loop uses return (must continue)")

    # clamp(v, min(lo, hi), hi): lo is capped at the very same hi.
    clamp_re = re.compile(r"clamp\(\s*[\w.]+,\s*min\(\s*[\w.]+,\s*([\w.]+)\s*\),\s*([\w.]+)\s*\)")
    for path in (
        SHIPPED / "pelorus_borderfix.comp.glsl",
        REFERENCE / "pelorus_borderfix.comp",
    ):
        text = path.read_text()
        found = clamp_re.findall(text)
        if len(found) != 2 or any(a != b for a, b in found):
            failures.append(f"{path.name}: clamp() bounds not order-safe")

    for msg in failures:
        print("FAIL:", msg)
    if not failures:
        print("OK: bayer8 canonical, plane loops continue, clamp bounds order-safe")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
