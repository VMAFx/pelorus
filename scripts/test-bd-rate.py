#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Regression test for scripts/bench/bd_rate.py monotonic guard.

A non-monotonic curve (score dip or duplicate bitrate), a curve with fewer than
2 points, or disjoint curves without overlap must fail closed with an error
message and NaN values rather than returning a meaningless BD metric.
When numpy is present, known BD reference values (identical curves -> 0.0%,
2x bitrate shift -> +100%) are verified; when numpy is absent, numerical checks
are skipped without failing the guard tests.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = pathlib.Path(
    os.environ.get("BD_RATE_SCRIPT", ROOT / "scripts" / "bench" / "bd_rate.py")
)


def load_module(path: pathlib.Path) -> Any:
    """Load bd_rate module dynamically from the target path."""
    spec = importlib.util.spec_from_file_location("bd_rate_mod", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _test_positive_control(mod: Any, has_numpy: bool) -> list[str]:
    """Test valid monotonic curves pass the guard."""
    failures: list[str] = []
    base_monotonic = [
        {"bitrate_kbps": 1000.0, "score": 80.0},
        {"bitrate_kbps": 2000.0, "score": 85.0},
        {"bitrate_kbps": 3000.0, "score": 90.0},
        {"bitrate_kbps": 4000.0, "score": 95.0},
    ]
    pel_monotonic = [
        {"bitrate_kbps": 1000.0, "score": 82.0},
        {"bitrate_kbps": 2000.0, "score": 87.0},
        {"bitrate_kbps": 3000.0, "score": 92.0},
        {"bitrate_kbps": 4000.0, "score": 97.0},
    ]
    if has_numpy:
        res = mod.bd_rate(base_monotonic, pel_monotonic)
        if "error" in res:
            failures.append(f"positive control failed: unexpected error {res['error']}")
        if math.isnan(res.get("bd_rate_pct", float("nan"))):
            failures.append("positive control failed: bd_rate_pct is NaN")
    else:
        try:
            mod.bd_rate(base_monotonic, pel_monotonic)
            failures.append("positive control without numpy expected ImportError, none raised")
        except ImportError as exc:
            if "bd_rate.py needs numpy" not in str(exc):
                failures.append(f"positive control ImportError missing context: {exc}")
    return failures


def _test_planted_dip(mod: Any) -> list[str]:
    """Test planted non-monotonic score dip in baseline or pelorus."""
    failures: list[str] = []
    base_good = [
        {"bitrate_kbps": 1000.0, "score": 80.0},
        {"bitrate_kbps": 2000.0, "score": 85.0},
        {"bitrate_kbps": 3000.0, "score": 90.0},
    ]
    pel_good = [
        {"bitrate_kbps": 1000.0, "score": 82.0},
        {"bitrate_kbps": 2000.0, "score": 87.0},
        {"bitrate_kbps": 3000.0, "score": 92.0},
    ]
    base_dip = [
        {"bitrate_kbps": 1000.0, "score": 85.0},
        {"bitrate_kbps": 2000.0, "score": 80.0},
        {"bitrate_kbps": 3000.0, "score": 90.0},
    ]
    pel_dip = [
        {"bitrate_kbps": 1000.0, "score": 82.0},
        {"bitrate_kbps": 2000.0, "score": 80.0},
        {"bitrate_kbps": 3000.0, "score": 92.0},
    ]
    res_b = mod.bd_rate(base_dip, pel_good)
    if "error" not in res_b:
        failures.append("planted score dip: no 'error' key returned")
    elif "baseline curve not monotonic" not in res_b["error"]:
        failures.append(f"planted score dip: expected baseline error, got {res_b['error']}")
    if not math.isnan(res_b.get("bd_rate_pct", 0.0)):
        failures.append(f"planted score dip: expected NaN bd_rate_pct, got {res_b.get('bd_rate_pct')}")

    res_p = mod.bd_rate(base_good, pel_dip)
    if "error" not in res_p:
        failures.append("planted pelorus dip: no 'error' key returned")
    elif "pelorus curve not monotonic" not in res_p["error"]:
        failures.append(f"planted pelorus dip: expected pelorus error, got {res_p['error']}")
    return failures


def _test_invalid_curves(mod: Any) -> list[str]:
    """Test duplicate bitrates, single point, and disjoint curves."""
    failures: list[str] = []
    pel_good = [
        {"bitrate_kbps": 1000.0, "score": 82.0},
        {"bitrate_kbps": 2000.0, "score": 87.0},
    ]
    base_dup = [
        {"bitrate_kbps": 1000.0, "score": 80.0},
        {"bitrate_kbps": 1000.0, "score": 85.0},
    ]
    res_dup = mod.bd_rate(base_dup, pel_good)
    if "error" not in res_dup:
        failures.append("duplicate bitrate: no 'error' key returned")
    elif "baseline curve not monotonic" not in res_dup["error"]:
        failures.append(f"duplicate bitrate: expected baseline error, got {res_dup['error']}")
    if not math.isnan(res_dup.get("bd_rate_pct", 0.0)):
        failures.append(f"duplicate bitrate: expected NaN bd_rate_pct, got {res_dup.get('bd_rate_pct')}")

    single_pt = [{"bitrate_kbps": 1000.0, "score": 80.0}]
    res_single = mod.bd_rate(single_pt, pel_good)
    if "error" not in res_single:
        failures.append("single point: no 'error' key returned")
    if not math.isnan(res_single.get("bd_rate_pct", 0.0)):
        failures.append(f"single point: expected NaN bd_rate_pct, got {res_single.get('bd_rate_pct')}")

    curve_low = [{"bitrate_kbps": 100.0, "score": 20.0}, {"bitrate_kbps": 200.0, "score": 30.0}]
    curve_high = [{"bitrate_kbps": 1000.0, "score": 80.0}, {"bitrate_kbps": 2000.0, "score": 85.0}]
    res_no_ov = mod.bd_rate(curve_low, curve_high)
    if "error" not in res_no_ov:
        failures.append("no-overlap: no 'error' key returned")
    if not math.isnan(res_no_ov.get("bd_rate_pct", 0.0)):
        failures.append(f"no-overlap: expected NaN bd_rate_pct, got {res_no_ov.get('bd_rate_pct')}")
    return failures


def test_guard(mod: Any, has_numpy: bool) -> list[str]:
    """Test monotonic guard and overlap edge cases."""
    failures: list[str] = []
    failures.extend(_test_positive_control(mod, has_numpy))
    failures.extend(_test_planted_dip(mod))
    failures.extend(_test_invalid_curves(mod))
    return failures


def test_numerical(mod: Any) -> list[str]:
    """Test known BD-rate values when numpy is available."""
    failures: list[str] = []
    base_monotonic = [
        {"bitrate_kbps": 1000.0, "score": 80.0},
        {"bitrate_kbps": 2000.0, "score": 85.0},
        {"bitrate_kbps": 4000.0, "score": 90.0},
        {"bitrate_kbps": 8000.0, "score": 95.0},
    ]

    # Identical curves: 0.0% BD-rate, 0.0 BD-quality
    res_ident = mod.bd_rate(base_monotonic, base_monotonic)
    if "error" in res_ident:
        failures.append(f"identical curves: unexpected error {res_ident['error']}")
    elif abs(res_ident["bd_rate_pct"] - 0.0) >= 1e-6:
        failures.append(f"identical curves: expected 0.0%, got {res_ident['bd_rate_pct']}")
    elif abs(res_ident["bd_quality"] - 0.0) >= 1e-6:
        failures.append(f"identical curves: expected 0.0 quality, got {res_ident['bd_quality']}")

    # 2x-rate shift: +100% within 1e-6
    curve_2x = [
        {"bitrate_kbps": p["bitrate_kbps"] * 2.0, "score": p["score"]}
        for p in base_monotonic
    ]
    res_2x = mod.bd_rate(base_monotonic, curve_2x)
    if "error" in res_2x:
        failures.append(f"2x-rate shift: unexpected error {res_2x['error']}")
    elif abs(res_2x["bd_rate_pct"] - 100.0) >= 1e-6:
        failures.append(f"2x-rate shift: expected +100.0%, got {res_2x['bd_rate_pct']}")

    return failures


def test_cli(script_path: pathlib.Path) -> list[str]:
    """Test CLI exit codes and stderr reporting on invalid input."""
    failures: list[str] = []
    base_dip = [
        {"bitrate_kbps": 1000.0, "score": 85.0},
        {"bitrate_kbps": 2000.0, "score": 80.0},
    ]
    pel_good = [
        {"bitrate_kbps": 1000.0, "score": 82.0},
        {"bitrate_kbps": 2000.0, "score": 87.0},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p_base = pathlib.Path(tmp) / "base.json"
        p_pel = pathlib.Path(tmp) / "pel.json"
        p_base.write_text(json.dumps(base_dip), encoding="utf-8")
        p_pel.write_text(json.dumps(pel_good), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-I", str(script_path), str(p_base), str(p_pel)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 1:
            failures.append(f"CLI non-monotonic: expected exit code 1, got {proc.returncode}")
        if "not monotonic" not in proc.stderr:
            failures.append(f"CLI non-monotonic: expected error on stderr, got: {proc.stderr!r}")
    return failures


def main() -> int:
    try:
        import numpy  # noqa: F401
        has_numpy = True
    except ImportError:
        has_numpy = False

    try:
        mod = load_module(SCRIPT)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not load {SCRIPT}: {exc}", file=sys.stderr)
        return 1

    failures = test_guard(mod, has_numpy)

    if has_numpy:
        failures.extend(test_numerical(mod))
    else:
        print("SKIP: numpy not available, skipping numerical BD assertions")

    failures.extend(test_cli(SCRIPT))

    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)

    if not failures:
        print("test-bd-rate.py: monotonic guard, single point, duplicate bitrate, "
              "no-overlap, and CLI exit-1 all verified")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
