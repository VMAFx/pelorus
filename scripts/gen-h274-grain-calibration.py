#!/usr/bin/env python3
"""Regenerate or check the grain estimator's H.274 model-0 calibration table.

The grain estimator (vf_pelorus_grain_estimate_vulkan) maps its measurement to
the H.274 frequency-filtering model that FFmpeg's SMPTE RDD 5 synthesizer
(libavcodec/h274.c) reproduces (ADR-0161). This tool ports that synthesizer's
luma path bit for bit, renders model-0 grain on a flat 8-bit frame for each
cutoff (cutoff_h == cutoff_v) at log2_scale_factor 2, and measures it with the
estimator's arithmetic (3x3 box high-pass, residual clamp, edge gate open).
It reports, per cutoff, the residual lag-1 correlation (rho_r) and the
residual RMS in 8-bit code values per unit of comp_model_value[0] (gain_r).

The grain tables (Gaussian_LUT, Seed_LUT, R64T) are read from FFmpeg's source
at the commit pinned in build-config.env, so nothing is vendored.

    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py
    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py --check

--check compares the regenerated table with the one in the filter source and
exits 1 on drift. This is a development tool: it needs numpy and an FFmpeg
checkout, so it is not part of the fast suite. Rerun it after an FFmpeg bump
that touches libavcodec/h274.c.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILTER = ROOT / "ffmpeg-patches" / "files" / "vf_pelorus_grain_estimate_vulkan.c"
BUILD_CONFIG = ROOT / "build-config.env"

WIDTH, HEIGHT, LEVEL = 1920, 1088, 128
LOG2_SCALE = 2
SCALES = (16, 32)
SEEDS = (0, 77, 155)
CUTOFFS = range(6, 15)
RES_CLAMP = 0.08
DEBLOCK = (64, 71, 77, 84, 90, 96, 103, 109, 116, 122, 128, 128, 128)
RHO_TOL = 0.002
GAIN_TOL = 0.0005


def ffmpeg_h274_source(repo: str) -> str:
    match = re.search(r"^FFMPEG_COMMIT=([0-9a-f]{40})$", BUILD_CONFIG.read_text(), re.M)
    if match is None:
        raise SystemExit("build-config.env: FFMPEG_COMMIT missing")
    return subprocess.run(
        ["git", "-C", repo, "show", f"{match.group(1)}:libavcodec/h274.c"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def parse_table(text: str, name: str) -> list[int]:
    match = re.search(r"static const \w+ " + name + r"\[[^=]*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"h274.c: table {name} not found")
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    return [int(n, 0) for n in re.findall(r"-?(?:0x[0-9A-Fa-f]+|\d+)", body)]


class H274Model0:
    """Luma path of ff_h274_apply_film_grain() for one interval, model 0."""

    def __init__(self, source: str) -> None:
        self.gauss = np.array(parse_table(source, "Gaussian_LUT"), dtype=np.int64)
        self.seeds = parse_table(source, "Seed_LUT")
        self.r64t = np.array(parse_table(source, "R64T"), dtype=np.int64).reshape(64, 64)
        if self.gauss.size != 2052 or len(self.seeds) != 256:
            raise SystemExit("h274.c: unexpected grain table sizes")
        self.db: dict[tuple[int, int], np.ndarray] = {}

    @staticmethod
    def prng_shift(x: int) -> int:
        feedback = 1 ^ (x >> 2) ^ (x >> 30)
        return ((x << 1) | (feedback & 1)) & 0xFFFFFFFF

    def pattern(self, h: int, v: int) -> np.ndarray:
        if (h, v) in self.db:
            return self.db[(h, v)]
        freq_h = ((h + 3) << 2) - 1
        freq_v = ((v + 3) << 2) - 1
        seed = self.seeds[h + v * 13]
        coeff = np.zeros((64, 64), dtype=np.int64)
        for row in range(freq_v + 1):
            for col in range(0, freq_h + 1, 4):
                off = seed % 2048
                coeff[row, col : col + 4] = self.gauss[off : off + 4]
                seed = self.prng_shift(seed)
        coeff[0, 0] = 0
        tmp = np.zeros((64, 64), dtype=np.int64)
        tmp[:, : freq_v + 1] = (
            self.r64t[:, : freq_h + 1] @ coeff[: freq_v + 1, : freq_h + 1].T + 128
        ) >> 8
        out = np.clip((self.r64t[:, : freq_v + 1] @ tmp[:, : freq_v + 1].T + 128) >> 8, -127, 127)
        for y in range(0, 64, 8):
            out[y, :] = (out[y, :] * DEBLOCK[v]) >> 7
            out[y + 7, :] = (out[y + 7, :] * DEBLOCK[v]) >> 7
        self.db[(h, v)] = out
        return out

    def render(self, scale: int, cutoff: int, seed: int) -> np.ndarray:
        def int8(a: np.ndarray) -> np.ndarray:
            return ((a + 128) & 0xFF) - 128

        db = self.pattern(cutoff - 2, cutoff - 2)
        shift = LOG2_SCALE + 6
        grain = np.zeros((HEIGHT, WIDTH), dtype=np.int64)
        state = self.seeds[seed % 256]
        for y in range(0, HEIGHT, 16):
            for x in range(0, WIDTH, 16):
                x_off = ((state >> 16) % 52) & 0xFFFC
                y_off = ((state & 0xFFFF) % 56) & 0xFFF8
                signed = -scale if state & 1 else scale
                state = self.prng_shift(state)
                for yy in (0, 8):
                    for xx in (0, 8):
                        blk = db[y_off + yy : y_off + yy + 8, x_off + xx : x_off + xx + 8]
                        rows = slice(y + yy, y + yy + 8)
                        col = x + xx
                        grain[rows, col : col + 8] = int8((signed * blk) >> shift)
                        if col > 0:
                            l1 = grain[rows, col - 2].copy()
                            l0 = grain[rows, col - 1].copy()
                            r0 = grain[rows, col].copy()
                            r1 = grain[rows, col + 1].copy()
                            grain[rows, col] = int8((l0 + r0 * 2 + r1) >> 2)
                            grain[rows, col - 1] = int8((r0 + l0 * 2 + l1) >> 2)
        return np.clip(LEVEL + grain, 0, 255)


def estimator_stats(luma8: np.ndarray) -> tuple[float, float]:
    """Residual RMS (8-bit codes) and residual lag-1 correlation, edge gate open."""
    img = luma8.astype(np.float64) / 255.0
    h, w = img.shape
    padded = np.pad(img, ((1, 1), (1, 2)), mode="edge")
    acc = np.zeros((h, w + 1))
    for dy in range(3):
        for dx in range(3):
            acc += padded[dy : dy + h, dx : dx + w + 1]
    resid = np.clip(padded[1 : 1 + h, 1 : 2 + w] - acc / 9.0, -RES_CLAMP, RES_CLAMP)
    resid[:, w] = resid[:, w - 1]
    var = float((resid[:, :w] ** 2).mean())
    lag = float((resid[:, :w] * resid[:, 1:]).mean()) / var
    return float(np.sqrt(var)) * 255.0, lag


def calibrate(model: H274Model0) -> list[tuple[int, float, float]]:
    rows = []
    for cutoff in CUTOFFS:
        gains, rhos = [], []
        for scale in SCALES:
            for seed in SEEDS:
                rms, rho = estimator_stats(model.render(scale, cutoff, seed))
                gains.append(rms / scale)
                rhos.append(rho)
        rows.append((cutoff, float(np.mean(rhos)), float(np.mean(gains))))
    return rows


def parse_filter_table() -> list[tuple[int, float, float]]:
    text = FILTER.read_text()
    match = re.search(r"pel_h274_calib\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"{FILTER.name}: pel_h274_calib table not found")
    return [
        (int(c), float(r), float(g))
        for c, r, g in re.findall(
            r"\{\s*(\d+),\s*(-?[0-9.]+)f,\s*([0-9.]+)f\s*\}", match.group(1)
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="compare with the filter table")
    parser.add_argument("--ffmpeg-repo", default=os.environ.get("FFMPEG_REPO"))
    args = parser.parse_args()
    if not args.ffmpeg_repo:
        print("set FFMPEG_REPO or pass --ffmpeg-repo", file=sys.stderr)
        return 2

    rows = calibrate(H274Model0(ffmpeg_h274_source(args.ffmpeg_repo)))
    for cutoff, rho, gain in rows:
        print(f"    {{{cutoff:2d}, {rho:+.4f}f, {gain:.5f}f}},".replace("+", " "))
    if not args.check:
        return 0

    committed = parse_filter_table()
    if [c for c, _, _ in committed] != [c for c, _, _ in rows]:
        print("calibration cutoffs drifted", file=sys.stderr)
        return 1
    drift = [
        (c, r0, r1, g0, g1)
        for (c, r0, g0), (_, r1, g1) in zip(committed, rows)
        if abs(r0 - r1) > RHO_TOL or abs(g0 - g1) > GAIN_TOL
    ]
    for c, r0, r1, g0, g1 in drift:
        print(f"cutoff {c}: rho {r0:+.4f} -> {r1:+.4f}, gain {g0:.5f} -> {g1:.5f}", file=sys.stderr)
    if drift:
        return 1
    print("H.274 calibration table matches the pinned FFmpeg synthesizer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
