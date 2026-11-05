#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regression test for scripts/bench/run-bench.py synth_source pixfmt emission.

Verifies that synth_source() writes raw YUV clips with the requested pixel format
(yuv420p and yuv420p10le) for both banding and noise synthetic generators,
preventing 8-bit bytes from being ingested as 10-bit samples (issue #300).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH_SCRIPT = ROOT / "scripts" / "bench" / "run-bench.py"
RUN_TIMEOUT_S = 30.0

CASES: list[tuple[str, str, int]] = [
    ("banding", "yuv420p", 1),
    ("banding", "yuv420p10le", 2),
    ("noise", "yuv420p10le", 2),
]


def load_bench_module(script_path: pathlib.Path) -> Any:
    """Load run-bench.py via importlib."""
    spec = importlib.util.spec_from_file_location("run_bench", str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install_timeout_guard(mod: Any) -> None:
    """Ensure every subprocess call made by mod.run has a timeout."""
    orig_run = mod.run

    def safe_run(cmd: list[str], **kw: Any) -> Any:
        kw.setdefault("timeout", RUN_TIMEOUT_S)
        return orig_run(cmd, **kw)

    mod.run = safe_run


def make_defect_run(orig_run: Any) -> Any:
    """Wrap run() to strip format=<pixfmt> filter from ffmpeg -vf arguments."""

    def defect_run(cmd: list[str], **kw: Any) -> Any:
        new_cmd = list(cmd)
        if "-vf" in new_cmd:
            idx = new_cmd.index("-vf")
            vf_val = new_cmd[idx + 1]
            parts = [
                p for p in vf_val.split(",") if not p.startswith("format=")
            ]
            new_cmd[idx + 1] = ",".join(parts) or "null"
        kw.setdefault("timeout", RUN_TIMEOUT_S)
        return orig_run(new_cmd, **kw)

    return defect_run


def verify_case(
    mod: Any,
    ffmpeg_path: str,
    out_dir: str,
    synth: str,
    pixfmt: str,
    bytes_per_sample: int,
) -> None:
    """Run one synthetic source case and verify size and sample layout."""
    width, height, frames, fps = 64, 32, 3, 25
    expected_size = int(frames * width * height * 1.5 * bytes_per_sample)
    args = argparse.Namespace(
        out=out_dir,
        synth=synth,
        width=width,
        height=height,
        fps=fps,
        frames=frames,
        pixfmt=pixfmt,
        ffmpeg=ffmpeg_path,
    )
    src_path = mod.synth_source(args)
    actual_size = os.path.getsize(src_path)
    if actual_size != expected_size:
        raise AssertionError(
            f"{synth}+{pixfmt}: size {actual_size} != expected {expected_size}"
        )
    if bytes_per_sample == 2:
        with open(src_path, "rb") as f:
            data = f.read()
        max_high = max(data[1::2])
        if max_high > 3:
            raise AssertionError(
                f"{synth}+{pixfmt}: sample high byte > 3 (max={max_high})"
            )


def run_test_suite(mod: Any, ffmpeg_path: str) -> None:
    """Execute all test cases in a clean temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for synth, pixfmt, bps in CASES:
            case_dir = pathlib.Path(tmpdir) / f"{synth}_{pixfmt}"
            case_dir.mkdir(parents=True, exist_ok=True)
            verify_case(mod, ffmpeg_path, str(case_dir), synth, pixfmt, bps)


def run_self_test(mod: Any, ffmpeg_path: str) -> int:
    """Verify that dropping format filter triggers test failure on 10-bit cases."""
    orig_run = mod.run
    mod.run = make_defect_run(orig_run)
    ten_bit_cases = [c for c in CASES if c[2] == 2]
    caught = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for synth, pixfmt, bps in ten_bit_cases:
                case_dir = pathlib.Path(tmpdir) / f"defect_{synth}_{pixfmt}"
                case_dir.mkdir(parents=True, exist_ok=True)
                try:
                    verify_case(
                        mod, ffmpeg_path, str(case_dir), synth, pixfmt, bps
                    )
                except AssertionError as exc:
                    caught += 1
                    print(
                        f"self-test: caught defect for {synth}+{pixfmt}: {exc}"
                    )
                else:
                    print(
                        f"self-test FAIL: {synth}+{pixfmt} passed without format!",
                        file=sys.stderr,
                    )
                    return 1
    finally:
        mod.run = orig_run

    if caught != len(ten_bit_cases):
        print(
            f"self-test FAIL: caught {caught}/{len(ten_bit_cases)} defects",
            file=sys.stderr,
        )
        return 1

    print(f"self-test OK: caught {caught} planted defects")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test on planted defect",
    )
    parser.add_argument(
        "--ffmpeg",
        default=None,
        help="Path to ffmpeg binary (default: discover via PATH)",
    )
    parser.add_argument(
        "--script",
        type=pathlib.Path,
        default=BENCH_SCRIPT,
        help="Path to run-bench.py",
    )
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg_path:
        print("SKIP: ffmpeg not found in PATH and --ffmpeg not provided")
        return 77

    mod = load_bench_module(args.script)
    install_timeout_guard(mod)

    if args.self_test:
        return run_self_test(mod, ffmpeg_path)

    run_test_suite(mod, ffmpeg_path)
    print("PASS: synth_source pixfmt verified across all cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
