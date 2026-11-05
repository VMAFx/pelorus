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
    scripts/gen-grain-gate-curve.py --write
    scripts/gen-grain-gate-curve.py --self-test

--check compares the regenerated tables and the gate steps with the filter
source and exits 1 on drift. --write updates the table in the filter source
in-place when drift or grid changes occur, and is a no-op when within tolerance.
This is a development tool: it needs numpy, so it is not part of the fast suite.
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


def check(tables: list[list[tuple[float, float]]], source_path: pathlib.Path = C_SOURCE) -> int:
    text = source_path.read_text(encoding="utf-8")
    steps = STEPS.search(text)
    if steps is None:
        print(f"{source_path}: pel_grain_gate_step not found", file=sys.stderr)
        return 1
    have_steps = tuple(float(v) for v in steps.group(1).split(","))
    if have_steps != GATE_STEPS:
        print(f"pel_grain_gate_step {have_steps} differs from {GATE_STEPS}", file=sys.stderr)
        return 1
    match = TABLE.search(text)
    if match is None:
        print(f"{source_path}: pel_gate_curve table not found", file=sys.stderr)
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


def write_table(tables: list[list[tuple[float, float]]], source_path: pathlib.Path = C_SOURCE) -> int:
    text = source_path.read_text(encoding="utf-8")
    match = TABLE.search(text)
    if match is None:
        print(f"{source_path}: pel_gate_curve table not found", file=sys.stderr)
        return 1
    have = [(float(a), float(b)) for a, b in ROW.findall(match.group(1))]
    rows = [row for table in tables for row in table]
    grid_changed = len(have) != len(rows)
    if grid_changed:
        print(f"row count changed: {len(have)} -> {len(rows)}")
    bad = []
    if not grid_changed:
        bad = [(h, r) for h, r in zip(have, rows)
               if abs(h[0] - r[0]) > TOLERANCE or abs(h[1] - r[1]) > TOLERANCE]
    if not grid_changed and not bad:
        print("tables match, nothing written")
        return 0
    span_start, span_end = match.span(1)
    new_body = "\n" + render(tables) + "\n"
    new_text = text[:span_start] + new_body + text[span_end:]
    source_path.write_text(new_text, encoding="utf-8")
    return check(tables, source_path=source_path)


def _test_perturb_restore(tables: list[list[tuple[float, float]]], tmp_c: pathlib.Path, orig_bytes: bytes) -> bool:
    perturbed_text = tmp_c.read_text(encoding="utf-8").replace("{0.0029f, 0.3009f}", "{0.0029f, 0.4500f}", 1)
    tmp_c.write_text(perturbed_text, encoding="utf-8")
    if tmp_c.read_bytes() == orig_bytes:
        return False
    if write_table(tables, source_path=tmp_c) != 0 or tmp_c.read_bytes() != orig_bytes:
        return False
    return check(tables, source_path=tmp_c) == 0


def _test_tolerance_no_write(tables: list[list[tuple[float, float]]], tmp_c: pathlib.Path, orig_bytes: bytes) -> bool:
    perturbed = tmp_c.read_text(encoding="utf-8").replace("{0.0029f, 0.3009f}", "{0.0029f, 0.3010f}", 1)
    tmp_c.write_text(perturbed, encoding="utf-8")
    before = tmp_c.read_bytes()
    if before == orig_bytes:
        return False
    return write_table(tables, source_path=tmp_c) == 0 and tmp_c.read_bytes() == before


def self_test() -> int:
    import tempfile
    tables = curves()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_c = pathlib.Path(tmp_dir) / C_SOURCE.name
        tmp_c.write_text(C_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
        orig_bytes = tmp_c.read_bytes()

        if write_table(tables, source_path=tmp_c) != 0 or tmp_c.read_bytes() != orig_bytes:
            print("self-test failure: unchanged source was modified", file=sys.stderr)
            return 1
        if not _test_perturb_restore(tables, tmp_c, orig_bytes):
            print("self-test failure: write failed to restore perturbed row", file=sys.stderr)
            return 1
        if not _test_tolerance_no_write(tables, tmp_c, orig_bytes):
            print("self-test failure: write modified file on inside-tolerance drift", file=sys.stderr)
            return 1
        try:
            build_parser().parse_args(["--write", "--check"])
            print("self-test failure: --write with --check was not refused", file=sys.stderr)
            return 1
        except SystemExit:
            pass

    print("gen-grain-gate-curve: all self-test cases passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="grain estimator gate-chain curve tables")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="compare with the filter source")
    group.add_argument("--write", action="store_true", help="update the filter source table on drift")
    parser.add_argument("--self-test", action="store_true", help="run self-test with planted cases")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    tables = curves()
    if args.check:
        return check(tables)
    if args.write:
        return write_table(tables)
    print(render(tables))
    return 0


if __name__ == "__main__":
    sys.exit(main())
