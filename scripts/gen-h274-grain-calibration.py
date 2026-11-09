#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
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
at the commit pinned in build-config.env, so nothing is vendored. The shared
FFmpeg fix series applied on that commit (FFMPEG_SERIES_*, ADR-0185) does not
change libavcodec/h274.c, so the pinned file is the one a build compiles.

    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py
    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py --check
    FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py --write
    scripts/gen-h274-grain-calibration.py --self-test

--check compares the regenerated table with the one in the filter source and
exits 1 on drift. --write updates the table in the filter source (or stats table)
in-place when drift or grid changes occur, and is a no-op when within tolerance.
--self-test needs no numpy or FFmpeg checkout and runs in the fast suite;
generating, checking or writing the tables needs numpy and an FFmpeg checkout.
Rerun it after an FFmpeg bump or a shared series bump that touches
libavcodec/h274.c.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _fileutil import atomic_write_text

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILTER = ROOT / "ffmpeg-patches" / "files" / "vf_pelorus_grain_estimate_vulkan.c"
BUILD_CONFIG = ROOT / "build-config.env"
# HISS-02: deadline for the `git show` read of an FFmpeg source file; within
# the 30 s Meson default of the gen-h274-grain-calibration-self-test test.
GIT_TIMEOUT_S = 30

WIDTH, HEIGHT, LEVEL = 1920, 1088, 128
LOG2_SCALE = 2
SCALES = (16, 32)
SEEDS = (0, 77, 155)
CUTOFFS = range(6, 15)
RES_CLAMP = 0.08
DEBLOCK = (64, 71, 77, 84, 90, 96, 103, 109, 116, 122, 128, 128, 128)
RHO_TOL = 0.002
GAIN_TOL = 0.0005
STATS_TABLE = ROOT / "libpelorus" / "src" / "grain_convert_tables.c"
STAT_CUTOFFS = range(2, 15)
STAT_SCALE = 64
STAT_SIZE = 512


def ffmpeg_h274_source(repo: str) -> str:
    return ffmpeg_source(repo, "libavcodec/h274.c")


def ffmpeg_source(repo: str, path: str) -> str:
    """One FFmpeg source file at the commit pinned in build-config.env."""
    match = re.search(r"^FFMPEG_COMMIT=([0-9a-f]{40})$", BUILD_CONFIG.read_text(), re.M)
    if match is None:
        raise SystemExit("build-config.env: FFMPEG_COMMIT missing")
    return subprocess.run(
        ["git", "-C", repo, "show", f"{match.group(1)}:{path}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    ).stdout


def parse_table(text: str, name: str) -> list[int]:
    match = re.search(r"static const \w+ " + name + r"\[[^=]*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"FFmpeg source: table {name} not found")
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    return [int(n, 0) for n in re.findall(r"-?(?:0x[0-9A-Fa-f]+|\d+)", body)]


class H274Model0:
    """Luma path of ff_h274_apply_film_grain() for one interval, model 0."""

    def __init__(self, source: str) -> None:
        import numpy as np

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
        import numpy as np

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
        import numpy as np

        grain = self.grain(scale, cutoff, cutoff, seed, WIDTH, HEIGHT)
        return np.clip(LEVEL + grain, 0, 255)

    def grain(self, scale: int, cut_h: int, cut_v: int, seed: int, width: int, height: int):
        """Luma grain field for one cutoff pair, before it is added to the image."""
        import numpy as np

        def int8(a: np.ndarray) -> np.ndarray:
            return ((a + 128) & 0xFF) - 128

        db = self.pattern(cut_h - 2, cut_v - 2)
        shift = LOG2_SCALE + 6
        grain = np.zeros((height, width), dtype=np.int64)
        state = self.seeds[seed % 256]
        for y in range(0, height, 16):
            for x in range(0, width, 16):
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
        return grain


def estimator_stats(luma8: np.ndarray) -> tuple[float, float]:
    """Residual RMS (8-bit codes) and residual lag-1 correlation, edge gate open."""
    import numpy as np

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


def grain_stats(model: H274Model0) -> list[tuple[int, int, float, float, float]]:
    """Raw model-0 grain per cutoff pair: RMS per unit scale and lag-1 correlations.

    These are the statistics libpelorus's AV1 <-> H.274 conversion compares
    (ADR-0205). Grain is measured before it is added to the image, so no
    clipping and no high-pass filter applies.
    """
    import numpy as np

    rows = []
    for cut_v in STAT_CUTOFFS:
        for cut_h in STAT_CUTOFFS:
            gains, rho_h, rho_v = [], [], []
            for seed in SEEDS:
                g = model.grain(STAT_SCALE, cut_h, cut_v, seed, STAT_SIZE, STAT_SIZE).astype(
                    np.float64
                )
                g -= g.mean()
                var = float((g * g).mean())
                gains.append(np.sqrt(var) / STAT_SCALE)
                rho_h.append(float((g[:, :-1] * g[:, 1:]).mean()) / var)
                rho_v.append(float((g[:-1, :] * g[1:, :]).mean()) / var)
            rows.append(
                (cut_h, cut_v, float(np.mean(gains)), float(np.mean(rho_h)), float(np.mean(rho_v)))
            )
    return rows


def format_stats(rows: list[tuple[int, int, float, float, float]]) -> list[str]:
    return [
        f"    {{{h:2d}, {v:2d}, {g:.5f}f, {rh:+.4f}f, {rv:+.4f}f}},".replace("+", " ")
        for h, v, g, rh, rv in rows
    ]


def parse_stats_table(source_path: pathlib.Path = STATS_TABLE) -> list[tuple[int, int, float, float, float]]:
    text = source_path.read_text(encoding="utf-8")
    match = re.search(r"pel_h274_grain_stats\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"{source_path.name}: pel_h274_grain_stats table not found")
    return [
        (int(h), int(v), float(g), float(rh), float(rv))
        for h, v, g, rh, rv in re.findall(
            r"\{\s*(\d+),\s*(\d+),\s*([0-9.]+)f,\s*(-?[0-9.]+)f,\s*(-?[0-9.]+)f\s*\}",
            match.group(1),
        )
    ]


def _check_stats_drift(
    rows: list[tuple[int, int, float, float, float]], source_path: pathlib.Path = STATS_TABLE
) -> tuple[int, bool, bool]:
    committed = parse_stats_table(source_path=source_path)
    grid_changed = [(h, v) for h, v, *_ in committed] != [(h, v) for h, v, *_ in rows]
    if grid_changed:
        print(f"grain statistics cutoff pairs drifted: {len(committed)} -> {len(rows)}", file=sys.stderr)
        return 1, True, True
    drift = [
        (new, old)
        for new, old in zip(rows, committed)
        if abs(new[2] - old[2]) > GAIN_TOL
        or abs(new[3] - old[3]) > RHO_TOL
        or abs(new[4] - old[4]) > RHO_TOL
    ]
    for new, old in drift:
        print(f"cutoff {new[0]},{new[1]}: {old[2:]} -> {new[2:]}", file=sys.stderr)
    return (1 if drift else 0), bool(drift), False


def check_stats(rows: list[tuple[int, int, float, float, float]], source_path: pathlib.Path = STATS_TABLE) -> int:
    rc, _, _ = _check_stats_drift(rows, source_path=source_path)
    if rc == 0:
        print("H.274 grain statistics table matches the pinned FFmpeg synthesizer")
    return rc


def write_stats(rows: list[tuple[int, int, float, float, float]], source_path: pathlib.Path = STATS_TABLE) -> int:
    _, drift, grid = _check_stats_drift(rows, source_path=source_path)
    if not (drift or grid):
        print("tables match, nothing written")
        return 0
    text = source_path.read_text(encoding="utf-8")
    m = re.search(r"pel_h274_grain_stats\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if m is None:
        raise SystemExit(f"{source_path.name}: pel_h274_grain_stats table not found")
    new_body = "\n" + "\n".join(format_stats(rows))
    text = text[:m.start(1)] + new_body + text[m.end(1):]
    atomic_write_text(source_path, text)
    return check_stats(rows, source_path=source_path)


def calibrate(model: H274Model0) -> list[tuple[int, float, float]]:
    import numpy as np

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


def parse_filter_table(table: str = "pel_h274_calib", source_path: pathlib.Path = FILTER) -> list[tuple[int, float, float]]:
    """Read the committed `table` rows from the grain filter source."""
    text = source_path.read_text(encoding="utf-8")
    match = re.search(table + r"\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if match is None:
        raise SystemExit(f"{source_path.name}: {table} table not found")
    return [
        (int(c), float(r), float(g))
        for c, r, g in re.findall(
            r"\{\s*(\d+),\s*(-?[0-9.]+)f,\s*([0-9.]+)f\s*\}", match.group(1)
        )
    ]


def format_h274_rows(rows: list[tuple[int, float, float]]) -> str:
    cells = [f"{{{c}, {r:+.4f}f, {g:.5f}f}},".replace("+", "") for c, r, g in rows]
    lines = []
    for i in range(0, len(cells), 3):
        chunk = cells[i : i + 3]
        if len(chunk) == 3:
            s1 = "  " if len(chunk[0]) < 24 else " "
            s2 = "  " if len(chunk[1]) < 24 else " "
            lines.append("    " + chunk[0] + s1 + chunk[1] + s2 + chunk[2])
        elif len(chunk) == 2:
            s1 = "  " if len(chunk[0]) < 24 else " "
            lines.append("    " + chunk[0] + s1 + chunk[1])
        else:
            lines.append("    " + chunk[0])
    return "\n".join(lines)


def _check_filter_drift(
    rows: list[tuple[int, float, float]], source_path: pathlib.Path = FILTER
) -> tuple[int, bool, bool]:
    committed = parse_filter_table("pel_h274_calib", source_path=source_path)
    grid_changed = [c for c, _, _ in committed] != [c for c, _, _ in rows]
    if grid_changed:
        print(f"calibration cutoffs drifted: {len(committed)} -> {len(rows)}", file=sys.stderr)
        return 1, True, True
    drift = [
        (c, r0, r1, g0, g1)
        for (c, r0, g0), (_, r1, g1) in zip(committed, rows)
        if abs(r0 - r1) > RHO_TOL or abs(g0 - g1) > GAIN_TOL
    ]
    for c, r0, r1, g0, g1 in drift:
        print(f"cutoff {c}: rho {r0:+.4f} -> {r1:+.4f}, gain {g0:.5f} -> {g1:.5f}", file=sys.stderr)
    return (1 if drift else 0), bool(drift), False


def check_filter(rows: list[tuple[int, float, float]], source_path: pathlib.Path = FILTER) -> int:
    rc, _, _ = _check_filter_drift(rows, source_path=source_path)
    if rc == 0:
        print("H.274 calibration table matches the pinned FFmpeg synthesizer")
    return rc


def write_filter(rows: list[tuple[int, float, float]], source_path: pathlib.Path = FILTER) -> int:
    _, drift, grid = _check_filter_drift(rows, source_path=source_path)
    if not (drift or grid):
        print("tables match, nothing written")
        return 0
    text = source_path.read_text(encoding="utf-8")
    m = re.search(r"pel_h274_calib\[\]\s*=\s*\{(.*?)\n\};", text, re.S)
    if m is None:
        raise SystemExit(f"{source_path.name}: pel_h274_calib table not found")
    new_body = "\n" + format_h274_rows(rows)
    text = text[:m.start(1)] + new_body + text[m.end(1):]
    atomic_write_text(source_path, text)
    return check_filter(rows, source_path=source_path)


def _perturb(text: str, row: tuple, delta: float) -> str | None:
    """Shift the last value of a filter (3-cell) or statistics (5-cell) row."""
    moved = (*row[:-1], row[-1] + delta)
    if len(row) == 3:
        orig, perturbed = (format_h274_rows([r]).strip().rstrip(",") for r in (row, moved))
    else:
        orig, perturbed = (format_stats([r])[0].strip().rstrip(",") for r in (row, moved))
    if orig not in text:
        print(f"self-test failure: row literal {orig} not found in source", file=sys.stderr)
        return None
    return text.replace(orig, perturbed, 1)


def _test_h274_perturb_restore(rows: list, tmp_c: pathlib.Path, orig_bytes: bytes) -> bool:
    perturbed = _perturb(tmp_c.read_text(encoding="utf-8"), rows[0], 0.15)
    if perturbed is None:
        return False
    tmp_c.write_text(perturbed, encoding="utf-8")
    if tmp_c.read_bytes() == orig_bytes:
        return False
    if write_filter(rows, source_path=tmp_c) != 0 or tmp_c.read_bytes() != orig_bytes:
        return False
    return check_filter(rows, source_path=tmp_c) == 0


def _test_h274_tolerance_no_write(rows: list, tmp_c: pathlib.Path, orig_bytes: bytes) -> bool:
    perturbed = _perturb(tmp_c.read_text(encoding="utf-8"), rows[0], GAIN_TOL / 3)
    if perturbed is None:
        return False
    tmp_c.write_text(perturbed, encoding="utf-8")
    before = tmp_c.read_bytes()
    if before == orig_bytes:
        return False
    return write_filter(rows, source_path=tmp_c) == 0 and tmp_c.read_bytes() == before


def _test_stats_perturb_restore(stats_rows: list, tmp_s: pathlib.Path, orig_s_bytes: bytes) -> bool:
    perturbed = _perturb(tmp_s.read_text(encoding="utf-8"), stats_rows[0], 0.1)
    if perturbed is None:
        return False
    tmp_s.write_text(perturbed, encoding="utf-8")
    if tmp_s.read_bytes() == orig_s_bytes:
        return False
    if write_stats(stats_rows, source_path=tmp_s) != 0 or tmp_s.read_bytes() != orig_s_bytes:
        return False
    return check_stats(stats_rows, source_path=tmp_s) == 0


def self_test() -> int:
    import tempfile
    rows = parse_filter_table("pel_h274_calib", source_path=FILTER)
    stats_rows = parse_stats_table(source_path=STATS_TABLE)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_c = pathlib.Path(tmp_dir) / FILTER.name
        tmp_c.write_text(FILTER.read_text(encoding="utf-8"), encoding="utf-8")
        orig_bytes = tmp_c.read_bytes()

        if write_filter(rows, source_path=tmp_c) != 0 or tmp_c.read_bytes() != orig_bytes:
            print("self-test failure: unchanged filter source was modified", file=sys.stderr)
            return 1
        if not _test_h274_perturb_restore(rows, tmp_c, orig_bytes):
            print("self-test failure: write failed to restore perturbed filter row", file=sys.stderr)
            return 1
        if not _test_h274_tolerance_no_write(rows, tmp_c, orig_bytes):
            print("self-test failure: write modified file on inside-tolerance drift", file=sys.stderr)
            return 1

        tmp_s = pathlib.Path(tmp_dir) / STATS_TABLE.name
        tmp_s.write_text(STATS_TABLE.read_text(encoding="utf-8"), encoding="utf-8")
        orig_s_bytes = tmp_s.read_bytes()
        if write_stats(stats_rows, source_path=tmp_s) != 0 or tmp_s.read_bytes() != orig_s_bytes:
            print("self-test failure: unchanged stats source was modified", file=sys.stderr)
            return 1
        if not _test_stats_perturb_restore(stats_rows, tmp_s, orig_s_bytes):
            print("self-test failure: write failed to restore perturbed stats row", file=sys.stderr)
            return 1

        try:
            build_parser().parse_args(["--write", "--check"])
            print("self-test failure: --write with --check was not refused", file=sys.stderr)
            return 1
        except SystemExit:  # argparse refused the pair: the expected outcome
            pass

    print("gen-h274-grain-calibration: all self-test cases passed (precomputed rows injected)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="compare with the filter table")
    group.add_argument("--write", action="store_true", help="update filter table on drift")
    parser.add_argument(
        "--stats", action="store_true", help="raw grain statistics table for libpelorus instead"
    )
    parser.add_argument("--self-test", action="store_true", help="run self-test with planted cases")
    parser.add_argument("--ffmpeg-repo", default=os.environ.get("FFMPEG_REPO"))
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.ffmpeg_repo:
        print("set FFMPEG_REPO or pass --ffmpeg-repo", file=sys.stderr)
        return 2

    model = H274Model0(ffmpeg_h274_source(args.ffmpeg_repo))
    if args.stats:
        stats = grain_stats(model)
        if args.write:
            return write_stats(stats)
        if args.check:
            return check_stats(stats)
        print("\n".join(format_stats(stats)))
        return 0

    rows = calibrate(model)
    if args.write:
        return write_filter(rows)
    if args.check:
        return check_filter(rows)
    for cutoff, rho, gain in rows:
        print(f"    {{{cutoff:2d}, {rho:+.4f}f, {gain:.5f}f}},".replace("+", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
