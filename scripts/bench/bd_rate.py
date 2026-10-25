#!/usr/bin/env python3
# bd_rate.py — Bjøntegaard delta-rate / delta-quality between two RD curves.
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# BD-rate = average % bitrate change at equal quality (negative = Pelorus needs
# fewer bits for the same quality = a win). BD-quality = average quality change
# at equal bitrate (positive = higher quality at the same bitrate = a win).
# Standard Bjøntegaard metric (VCEG-M33): cubic polynomial fit over
# log10(bitrate) for >= 4 points, linear interpolation + trapezoid for 2-3
# points, integrated on the overlapping range.
#
#   bd_rate.py baseline.json pelorus.json
#     where each JSON is [{"bitrate_kbps": float, "score": float}, ...]

import json
import math
import sys


def _validate_curve(name, curve):
    """Verify curve sorted by bitrate has >= 2 points with strictly increasing
    bitrate and strictly increasing score."""
    if len(curve) < 2:
        return f"{name} curve not monotonic: requires at least 2 points (got {len(curve)})"
    sorted_pts = sorted(curve, key=lambda p: p["bitrate_kbps"])
    for i in range(len(sorted_pts) - 1):
        p0 = sorted_pts[i]
        p1 = sorted_pts[i + 1]
        if p1["bitrate_kbps"] <= p0["bitrate_kbps"] or p1["score"] <= p0["score"]:
            return (
                f"{name} curve not monotonic: "
                f"({p0['bitrate_kbps']}, {p0['score']}) and "
                f"({p1['bitrate_kbps']}, {p1['score']})"
            )
    return None


def _bd(rate_a, q_a, rate_b, q_b, mode):
    """mode='rate' -> avg % bitrate delta at equal quality; 'quality' -> avg
    quality delta at equal rate. a=baseline, b=pelorus."""
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("bd_rate.py needs numpy (pip install numpy)") from exc

    la, lb = np.log10(rate_a), np.log10(rate_b)
    if mode == "rate":
        x_a, y_a, x_b, y_b = q_a, la, q_b, lb       # integrate lograte over quality
    else:
        x_a, y_a, x_b, y_b = la, q_a, lb, q_b        # integrate quality over lograte

    # sort by x
    a = sorted(zip(x_a, y_a)); b = sorted(zip(x_b, y_b))
    xa, ya = map(np.array, zip(*a)); xb, yb = map(np.array, zip(*b))

    lo = max(xa.min(), xb.min()); hi = min(xa.max(), xb.max())
    if hi <= lo:
        return float("nan")  # no overlap

    def integ(x, y):
        if len(x) >= 4:
            p = np.polyfit(x, y, 3)
            P = np.polyint(p)
            return float(np.polyval(P, hi) - np.polyval(P, lo))
        # too few points: trapezoid on the clamped range
        xs = np.linspace(lo, hi, 200)
        ys = np.interp(xs, x, y)
        # np.trapz was removed in NumPy 2.0; trapezoid is the same integral.
        trapezoid = getattr(np, "trapezoid", None) or np.trapz
        return float(trapezoid(ys, xs))

    # b = pelorus, a = baseline. Both modes report the change of pelorus
    # relative to baseline: rate -> negative when pelorus needs fewer bits;
    # quality -> positive when pelorus is higher quality at equal bitrate.
    avg = (integ(xb, yb) - integ(xa, ya)) / (hi - lo)
    if mode == "rate":
        return float((10.0 ** avg - 1.0) * 100.0)   # % bitrate change
    return float(avg)                                # quality units


def bd_rate(baseline, pelorus):
    err = _validate_curve("baseline", baseline)
    if err is not None:
        return {"bd_rate_pct": float("nan"), "bd_quality": float("nan"), "error": err}
    err = _validate_curve("pelorus", pelorus)
    if err is not None:
        return {"bd_rate_pct": float("nan"), "bd_quality": float("nan"), "error": err}

    base_sorted = sorted(baseline, key=lambda p: p["bitrate_kbps"])
    pel_sorted = sorted(pelorus, key=lambda p: p["bitrate_kbps"])

    lo_q = max(base_sorted[0]["score"], pel_sorted[0]["score"])
    hi_q = min(base_sorted[-1]["score"], pel_sorted[-1]["score"])
    lo_r = max(base_sorted[0]["bitrate_kbps"], pel_sorted[0]["bitrate_kbps"])
    hi_r = min(base_sorted[-1]["bitrate_kbps"], pel_sorted[-1]["bitrate_kbps"])
    if hi_q <= lo_q or hi_r <= lo_r:
        return {
            "bd_rate_pct": float("nan"),
            "bd_quality": float("nan"),
            "error": "no overlap between baseline and pelorus curves",
        }

    ra = [p["bitrate_kbps"] for p in base_sorted]; qa = [p["score"] for p in base_sorted]
    rb = [p["bitrate_kbps"] for p in pel_sorted];  qb = [p["score"] for p in pel_sorted]
    res = {
        "bd_rate_pct": _bd(ra, qa, rb, qb, "rate"),
        "bd_quality": _bd(ra, qa, rb, qb, "quality"),
    }
    if math.isnan(res["bd_rate_pct"]) or math.isnan(res["bd_quality"]):
        res["error"] = "no overlap between baseline and pelorus curves"
    return res


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: bd_rate.py baseline.json pelorus.json")
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            base = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"failed to load baseline {sys.argv[1]}: {exc}")
    try:
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            pel = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"failed to load pelorus {sys.argv[2]}: {exc}")

    r = bd_rate(base, pel)
    if "error" in r:
        print(r["error"], file=sys.stderr)
        sys.exit(1)

    print(json.dumps(r, indent=2))
    bd = r["bd_rate_pct"]
    if not math.isnan(bd):
        verdict = "WIN" if bd < 0 else "loss"
        print(f"\nBD-rate: {bd:+.2f}%  ({verdict}: "
              f"{'fewer' if bd < 0 else 'more'} bits for equal quality)")
        print(f"BD-quality: {r['bd_quality']:+.3f} at equal bitrate")
