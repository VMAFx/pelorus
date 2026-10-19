#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regenerate or check the grain estimator's gate-chain curve tables.

The grain estimator (vf_pelorus_grain_estimate_vulkan) measures each luma
band at a chain of edge gates kT (k = 1, 2, 4; T = the default edge gate).
Heavy grain makes a gate drop flat pixels whose grain excursion is large, so
the band reads weak; texture is dropped for the same reason. For white
Gaussian grain, the kept fraction f (gated pixels / band pixels) and the gated
RMS over kT lie on one curve per gate: both depend only on sigma / kT, apart
from the residual clamp. Texture lies well below that curve, because its pixels
fail the gate by structure, not by the grain's tail. The host moves a band from
gate kT to the next gate only when its (f, rms / kT) pair at kT lies on that
gate's curve (ADR-0229, ADR-0230).

This tool renders white Gaussian grain on a flat 8-bit field with the
estimator's arithmetic (3x3 box high-pass, residual clamp, edge gate kT at the
filter's default T) and prints one curve per gate below the widest as
(f, rms / kT) rows, sorted by f.

    scripts/gen-grain-gate-curve.py
    scripts/gen-grain-gate-curve.py --check

--check compares the regenerated tables and the gate steps with the filter
source and exits 1 on drift. This is a development tool: it needs numpy, so it is not
part of the fast suite.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_SOURCE = ROOT / "ffmpeg-patches" / "files" / "vf_pelorus_grain_estimate_vulkan.c"
EDGE = 0.06  # the filter's default `edge`
RES_CLAMP = 0.08
LEVEL = 128  # 8-bit field level
SIZE = 512
SEED = 20261019
# Gate steps k (gate = k x EDGE); matches pel_grain_gate_step in the filter.
GATE_STEPS = (1.0, 2.0, 4.0)
# Grain standard deviations in 8-bit code values at gate 1 x EDGE, scaled by k
# for gate k: below about 3k the gate keeps more than 0.95 of the band and the
# host never moves past it.
SIGMAS = (3.0, 3.25, 3.5, 3.75, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0)
TABLE = re.compile(r"pel_gate_curve\[[^\]]*\]\s*=\s*\{(.*?)\};", re.S)
STEPS = re.compile(r"pel_grain_gate_step\[[^\]]*\]\s*=\s*\{([^}]*)\};")
ROW = re.compile(r"\{\s*([0-9.]+)f,\s*([0-9.]+)f\s*\}")
TOLERANCE = 0.0015


def measure(field: np.ndarray, gate: float) -> tuple[float, float]:
    h, w = field.shape
    pad = np.pad(field, 1, mode="edge")
    win = [pad[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)]
    mean = sum(win) / 9.0
    flat = (np.max(win, axis=0) - np.min(win, axis=0)) <= gate
    resid = np.clip(field - mean, -RES_CLAMP, RES_CLAMP)
    rms = float(np.sqrt(np.mean(resid[flat] ** 2)))
    return float(flat.mean()), rms / gate


def curves() -> list[list[tuple[float, float]]]:
    """One curve per gate below the widest: the widest gate is never left."""
    rng = np.random.default_rng(SEED)
    tables = []
    for k in GATE_STEPS[:-1]:
        rows = []
        for sigma in SIGMAS:
            codes = np.clip(np.round(LEVEL + rng.normal(0.0, sigma * k, (SIZE, SIZE))), 0, 255)
            rows.append(measure(codes / 255.0, k * EDGE))
        tables.append(sorted(rows))
    return tables


def render(tables: list[list[tuple[float, float]]]) -> str:
    """The tables flat, gate by gate, as the filter stores them."""
    cells = [f"{{{f:.4f}f, {q:.4f}f}}," for rows in tables for f, q in rows]
    lines = []
    for i in range(0, len(cells), 4):
        lines.append("    " + " ".join(cells[i:i + 4]))
    return "\n".join(lines)


def check(tables: list[list[tuple[float, float]]]) -> int:
    text = C_SOURCE.read_text(encoding="utf-8")
    steps = STEPS.search(text)
    if steps is None:
        print(f"{C_SOURCE}: pel_grain_gate_step not found", file=sys.stderr)
        return 1
    have_steps = tuple(float(v) for v in steps.group(1).split(","))
    if have_steps != GATE_STEPS:
        print(f"pel_grain_gate_step {have_steps} differs from {GATE_STEPS}", file=sys.stderr)
        return 1
    match = TABLE.search(text)
    if match is None:
        print(f"{C_SOURCE}: pel_gate_curve table not found", file=sys.stderr)
        return 1
    have = [(float(a), float(b)) for a, b in ROW.findall(match.group(1))]
    rows = [row for table in tables for row in table]
    if len(have) != len(rows):
        print(f"pel_gate_curve has {len(have)} rows, regenerated {len(rows)}", file=sys.stderr)
        return 1
    bad = [(h, r) for h, r in zip(have, rows)
           if abs(h[0] - r[0]) > TOLERANCE or abs(h[1] - r[1]) > TOLERANCE]
    for h, r in bad:
        print(f"drift: source {h} regenerated ({r[0]:.4f}, {r[1]:.4f})", file=sys.stderr)
    if not bad:
        print(f"pel_gate_curve matches ({len(tables)} gates, {len(rows)} rows)")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="grain estimator gate-chain curve tables")
    parser.add_argument("--check", action="store_true", help="compare with the filter source")
    args = parser.parse_args()
    tables = curves()
    if args.check:
        return check(tables)
    print(render(tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
