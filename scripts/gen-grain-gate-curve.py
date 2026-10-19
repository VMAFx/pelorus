#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regenerate or check the grain estimator's dual-gate curve table.

The grain estimator (vf_pelorus_grain_estimate_vulkan) measures each luma
band at the default edge gate T and at a wide gate 2T. Heavy grain makes the
default gate drop flat pixels whose grain excursion is large, so the band reads
weak; texture is dropped for the same reason. For white Gaussian grain, the
kept fraction f (gated pixels / band pixels) and the gated RMS over T lie on
one curve: both depend only on sigma / T. Texture lies well below that curve,
because its pixels fail the gate by structure, not by the grain's tail. The
host takes the wide-gate statistics of a band only when its (f, rms / T) pair
lies on the curve (ADR-0229).

This tool renders white Gaussian grain on a flat 8-bit field with the
estimator's arithmetic (3x3 box high-pass, residual clamp, edge gate at the
filter's default) and prints the curve as (f, rms / T) rows, sorted by f.

    scripts/gen-grain-gate-curve.py
    scripts/gen-grain-gate-curve.py --check

--check compares the regenerated table with the one in the filter source and
exits 1 on drift. This is a development tool: it needs numpy, so it is not
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
# Grain standard deviations in 8-bit code values: below about 3 the default
# gate keeps more than 0.95 of the band and the host never widens it.
SIGMAS = (3.0, 3.25, 3.5, 3.75, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0, 9.0, 10.0, 12.0, 14.0)
TABLE = re.compile(r"pel_gate_curve\[\]\s*=\s*\{(.*?)\};", re.S)
ROW = re.compile(r"\{\s*([0-9.]+)f,\s*([0-9.]+)f\s*\}")
TOLERANCE = 0.0015


def measure(field: np.ndarray) -> tuple[float, float]:
    h, w = field.shape
    pad = np.pad(field, 1, mode="edge")
    win = [pad[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)]
    mean = sum(win) / 9.0
    flat = (np.max(win, axis=0) - np.min(win, axis=0)) <= EDGE
    resid = np.clip(field - mean, -RES_CLAMP, RES_CLAMP)
    rms = float(np.sqrt(np.mean(resid[flat] ** 2)))
    return float(flat.mean()), rms / EDGE


def curve() -> list[tuple[float, float]]:
    rng = np.random.default_rng(SEED)
    rows = []
    for sigma in SIGMAS:
        codes = np.clip(np.round(LEVEL + rng.normal(0.0, sigma, (SIZE, SIZE))), 0, 255)
        rows.append(measure(codes / 255.0))
    return sorted(rows)


def render(rows: list[tuple[float, float]]) -> str:
    cells = [f"{{{f:.4f}f, {q:.4f}f}}," for f, q in rows]
    lines = []
    for i in range(0, len(cells), 4):
        lines.append("    " + " ".join(cells[i:i + 4]))
    return "\n".join(lines)


def check(rows: list[tuple[float, float]]) -> int:
    match = TABLE.search(C_SOURCE.read_text(encoding="utf-8"))
    if match is None:
        print(f"{C_SOURCE}: pel_gate_curve table not found", file=sys.stderr)
        return 1
    have = [(float(a), float(b)) for a, b in ROW.findall(match.group(1))]
    if len(have) != len(rows):
        print(f"pel_gate_curve has {len(have)} rows, regenerated {len(rows)}", file=sys.stderr)
        return 1
    bad = [(h, r) for h, r in zip(have, rows)
           if abs(h[0] - r[0]) > TOLERANCE or abs(h[1] - r[1]) > TOLERANCE]
    for h, r in bad:
        print(f"drift: source {h} regenerated ({r[0]:.4f}, {r[1]:.4f})", file=sys.stderr)
    if not bad:
        print(f"pel_gate_curve matches ({len(rows)} rows)")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="grain estimator dual-gate curve table")
    parser.add_argument("--check", action="store_true", help="compare with the filter source")
    args = parser.parse_args()
    rows = curve()
    if args.check:
        return check(rows)
    print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
