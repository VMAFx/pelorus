#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regenerate or check the grain estimator's AV1 calibration table.

The grain estimator (vf_pelorus_grain_estimate_vulkan) maps its measurement to
AV1 film grain parameters that a decoder synthesizes. This
tool ports the luma path of FFmpeg's synthesizer (libavcodec/aom_film_grain.c
and aom_film_grain_template.c) bit for bit: the Gaussian grain template, the
lag-1 auto-regressive filter, and the 32x32 block placement with overlap. For
each symmetric lag-1 model (ar_coeffs_y = {0, a, 0, a}: the up and left taps,
ar_coeff_shift 6) it renders grain at a flat scaling function on a flat 8-bit
frame and measures it with the estimator's arithmetic (3x3 box high-pass,
residual clamp, edge gate open). It reports, per a, the residual lag-1
correlation (rho_r) and the residual RMS in 8-bit code values per unit of the
scaling function (gain_r).

A second table, pel_aom_gate_calib, holds the same statistics measured through
each gate wider than the default (gate k x EDGE, ADR-0230), with the shader's
gate arithmetic: a pixel counts when its 3x3 range is within the gate, a
lag-1 pair when both windows are. A wider gate is chosen for heavy grain, and
it still drops the largest excursions, which lowers the residual RMS and
shifts the residual lag-1 correlation by an amount that depends on a. Each row
is (gate, a, q, rho_r, gain_r) with q the gated residual RMS over the gate, the
quantity a band measures; the scales span the range of q the host meets at
that gate. q, not the kept fraction, keys the rows: the kept fraction reaches 1
while the residual clamp still lowers the RMS.

gaussian_sequence is read from FFmpeg's source at the commit pinned in
build-config.env, so nothing is vendored. The shared FFmpeg fix series applied
on that commit (FFMPEG_SERIES_*, ADR-0185) does not change the AV1 grain files,
so the pinned files are the ones a build compiles.

    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-av1-grain-calibration.py
    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-av1-grain-calibration.py --check

--check compares the regenerated tables with the ones in the filter source and
exits 1 on drift. This is a development tool: it needs numpy and an FFmpeg
checkout, so it is not part of the fast suite. Rerun it after an FFmpeg bump
or a shared series bump that touches libavcodec/aom_film_grain*.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("gen_h274", HERE / "gen-h274-grain-calibration.py")
assert _SPEC is not None and _SPEC.loader is not None
h274 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h274)

WIDTH, HEIGHT, LEVEL = 1280, 736, 128
GRAIN_W, GRAIN_H, BLOCK = 82, 73, 32
AR_SHIFT = 6
AR_GRID = range(0, 29, 4)
SCALES = (16, 24, 32)
SEEDS = (7391, 0x1234, 0xBEEF)
OVERLAP_W = ((27, 17), (17, 27))
EDGE = 0.06  # the filter's default `edge`
# Gates above the default, (gate index, step k); match pel_grain_gate_step.
WIDE_GATES = ((1, 2.0), (2, 4.0))
# Scaling values at gate step 2; gate step k uses them times k / 2, so the kept
# fraction runs from about 1 down to about 0.5 at every gate.
GATE_SCALES = (32, 40, 48, 56, 64, 72, 80)


def round2(x, shift: int):
    return (x + ((1 << shift) >> 1)) >> shift


class AV1Luma:
    """Luma path of FFmpeg's AV1 film grain synthesis, 8-bit, grain_scale_shift 0."""

    def __init__(self, source: str) -> None:
        self.gauss = np.array(h274.parse_table(source, "gaussian_sequence"), dtype=np.int64)
        if self.gauss.size != 2048:
            raise SystemExit("aom_film_grain.c: unexpected gaussian_sequence size")

    @staticmethod
    def random(bits: int, state: int) -> tuple[int, int]:
        bit = (state ^ (state >> 1) ^ (state >> 3) ^ (state >> 12)) & 1
        state = (state >> 1) | (bit << 15)
        return (state >> (16 - bits)) & ((1 << bits) - 1), state

    def template(self, seed: int, a: int) -> np.ndarray:
        """generate_grain_y_c() for ar_coeff_lag 1, coefficients {0, a, 0, a}."""
        buf = np.zeros((GRAIN_H, GRAIN_W), dtype=np.int64)
        for y in range(GRAIN_H):
            for x in range(GRAIN_W):
                value, seed = self.random(11, seed)
                buf[y, x] = round2(int(self.gauss[value]), 4)
        g = buf.tolist()
        for y in range(3, GRAIN_H):
            row, up = g[y], g[y - 1]
            for x in range(3, GRAIN_W - 3):
                s = a * up[x] + a * row[x - 1]
                row[x] = min(max(row[x] + round2(s, AR_SHIFT), -128), 127)
        return np.array(g, dtype=np.int64)

    def field(self, seed: int, a: int, width: int, height: int) -> np.ndarray:
        """Luma grain per pixel as fgy_32x32xn_c() places it, overlap_flag 1."""
        lut = self.template(seed, a)
        out = np.zeros((height, width), dtype=np.int64)

        def sample(rv: int, bx: int, by: int, ys, xs):
            offx = 3 + 2 * (3 + (rv >> 4))
            offy = 3 + 2 * (3 + (rv & 0xF))
            return lut[offy + by * BLOCK + ys, offx + bx * BLOCK + xs]

        def blend(old, new, w):
            return np.clip(round2(old * w[0] + new * w[1], 5), -128, 127)

        for row in range(0, (height + BLOCK - 1) // BLOCK):
            bh = min(height - row * BLOCK, BLOCK)
            rows = 1 + (row > 0)
            seeds = []
            for i in range(rows):
                s = seed ^ ((((row - i) * 37 + 178) & 0xFF) << 8)
                seeds.append(s ^ (((row - i) * 173 + 105) & 0xFF))
            offs = [[0, 0], [0, 0]]  # offs[col][row]
            for bx in range(0, width, BLOCK):
                bw = min(BLOCK, width - bx)
                ystart = min(2, bh) if row else 0
                xstart = min(2, bw) if bx else 0
                if bx:
                    for i in range(rows):
                        offs[1][i] = offs[0][i]
                for i in range(rows):
                    offs[0][i], seeds[i] = self.random(8, seeds[i])
                ys, xs = np.mgrid[0:bh, 0:bw]
                grain = sample(offs[0][0], 0, 0, ys, xs)
                blk = grain.copy()
                for x in range(xstart):
                    col = slice(ystart, bh)
                    old = sample(offs[1][0], 1, 0, ys[col, x], x)
                    blk[col, x] = blend(old, grain[col, x], OVERLAP_W[x])
                for y in range(ystart):
                    cols = slice(xstart, bw)
                    old = sample(offs[0][1], 0, 1, y, xs[y, cols])
                    blk[y, cols] = blend(old, grain[y, cols], OVERLAP_W[y])
                    for x in range(xstart):
                        top = sample(offs[0][1], 0, 1, y, x)
                        top = blend(sample(offs[1][1], 1, 1, y, x), top, OVERLAP_W[x])
                        cur = blend(sample(offs[1][0], 1, 0, y, x), grain[y, x], OVERLAP_W[x])
                        blk[y, x] = blend(top, cur, OVERLAP_W[y])
                out[row * BLOCK : row * BLOCK + bh, bx : bx + bw] = blk
        return out


def render(field: np.ndarray, scale: int) -> np.ndarray:
    """add_noise_y on a flat frame: scaling_shift 8, full output range."""
    return np.clip(LEVEL + round2(scale * field, 8), 0, 255)


def gated_sums(luma8: np.ndarray, gate: float) -> np.ndarray:
    """Shader arithmetic at one gate: kept count, total, residual square sum,
    lag-1 pair count and product sum (right neighbour clamped at the edge)."""
    img = luma8.astype(np.float64) / 255.0
    h, w = img.shape
    pad = np.pad(img, ((1, 1), (1, 2)), mode="edge")
    win = [pad[dy : dy + h, dx : dx + w + 1] for dy in range(3) for dx in range(3)]
    rng = np.max(win, axis=0) - np.min(win, axis=0)
    resid = np.clip(pad[1 : 1 + h, 1 : 2 + w] - sum(win) / 9.0, -h274.RES_CLAMP, h274.RES_CLAMP)
    resid[:, w], rng[:, w] = resid[:, w - 1], rng[:, w - 1]
    flat = rng[:, :w] <= gate
    pair = np.maximum(rng[:, :w], rng[:, 1:]) <= gate
    prod = resid[:, :w] * resid[:, 1:]
    return np.array(
        [flat.sum(), flat.size, (resid[:, :w][flat] ** 2).sum(), pair.sum(), prod[pair].sum()]
    )


def gate_rows(field_sets: dict) -> list[tuple[int, int, float, float, float]]:
    rows = []
    for g, k in WIDE_GATES:
        for a in AR_GRID:
            block = []
            for base in GATE_SCALES:
                scale = int(base * k / 2)
                s = sum(gated_sums(render(f, scale), k * EDGE) for f in field_sets[a])
                var = s[2] / s[0]
                rho = s[4] / s[3] / var
                q = float(np.sqrt(var)) / (k * EDGE)
                block.append((g, a, q, float(rho), float(np.sqrt(var)) * 255.0 / scale))
            rows.extend(sorted(block, key=lambda r: r[2]))
    return rows


def calibrate(model: AV1Luma) -> tuple[list[tuple[int, float, float]], list]:
    rows, fields = [], {}
    for a in AR_GRID:
        gains, rhos = [], []
        fields[a] = [model.field(seed, a, WIDTH, HEIGHT) for seed in SEEDS]
        for field in fields[a]:
            for scale in SCALES:
                rms, rho = h274.estimator_stats(render(field, scale))
                gains.append(rms / scale)
                rhos.append(rho)
        rows.append((a, float(np.mean(rhos)), float(np.mean(gains))))
    return rows, gate_rows(fields)


def format_gate_rows(rows: list) -> list[str]:
    cells = [f"{{{g}, {a:2d}, {f:.4f}f, {r:+.4f}f, {gn:.5f}f}},".replace("+", "") for g, a, f, r, gn in rows]
    return ["    " + " ".join(cells[i : i + 2]) for i in range(0, len(cells), 2)]


def parse_gate_table() -> list[tuple[int, int, float, float, float]]:
    text = h274.FILTER.read_text()
    match = re.search(r"pel_aom_gate_calib\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"{h274.FILTER.name}: pel_aom_gate_calib table not found")
    return [
        (int(g), int(a), float(f), float(r), float(gn))
        for g, a, f, r, gn in re.findall(
            r"\{\s*(\d+),\s*(\d+),\s*([0-9.]+)f,\s*(-?[0-9.]+)f,\s*([0-9.]+)f\s*\}",
            match.group(1),
        )
    ]


def check_gate_rows(rows: list) -> int:
    committed = parse_gate_table()
    if [(g, a) for g, a, *_ in committed] != [(g, a) for g, a, *_ in rows]:
        print("gate calibration grid drifted", file=sys.stderr)
        return 1
    drift = [
        (old, new)
        for old, new in zip(committed, rows)
        if abs(old[2] - new[2]) > h274.RHO_TOL
        or abs(old[3] - new[3]) > h274.RHO_TOL
        or abs(old[4] - new[4]) > h274.GAIN_TOL
    ]
    for old, new in drift:
        print(f"gate {old[0]} a {old[1]}: {old[2:]} -> {tuple(round(v, 5) for v in new[2:])}", file=sys.stderr)
    return 1 if drift else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="compare with the filter table")
    parser.add_argument("--ffmpeg-repo", default=os.environ.get("FFMPEG_REPO"))
    args = parser.parse_args()
    if not args.ffmpeg_repo:
        print("set FFMPEG_REPO or pass --ffmpeg-repo", file=sys.stderr)
        return 2

    model = AV1Luma(h274.ffmpeg_source(args.ffmpeg_repo, "libavcodec/aom_film_grain.c"))
    rows, gated = calibrate(model)
    for a, rho, gain in rows:
        print(f"    {{{a:2d}, {rho:+.4f}f, {gain:.5f}f}},".replace("+", " "))
    print("\n".join(format_gate_rows(gated)))
    if not args.check:
        return 0

    committed = h274.parse_filter_table("pel_aom_calib")
    if [a for a, _, _ in committed] != [a for a, _, _ in rows]:
        print("calibration AR grid drifted", file=sys.stderr)
        return 1
    drift = [
        (a, r0, r1, g0, g1)
        for (a, r0, g0), (_, r1, g1) in zip(committed, rows)
        if abs(r0 - r1) > h274.RHO_TOL or abs(g0 - g1) > h274.GAIN_TOL
    ]
    for a, r0, r1, g0, g1 in drift:
        print(f"a {a}: rho {r0:+.4f} -> {r1:+.4f}, gain {g0:.5f} -> {g1:.5f}", file=sys.stderr)
    if drift or check_gate_rows(gated):
        return 1
    print("AV1 calibration tables match the pinned FFmpeg synthesizer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
