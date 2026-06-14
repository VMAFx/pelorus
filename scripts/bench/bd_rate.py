#!/usr/bin/env python3
# bd_rate.py — Bjøntegaard delta-rate / delta-quality between two RD curves.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# BD-rate = average % bitrate change at equal quality (negative = Pelorus needs
# fewer bits for the same quality = a win). BD-quality = average quality change
# at equal bitrate (positive = higher quality at the same bitrate = a win).
# Standard Bjøntegaard metric (VCEG-M33) via piecewise-cubic interpolation over
# log10(bitrate), integrated on the overlapping quality range.
#
#   bd_rate.py baseline.json pelorus.json
#     where each JSON is [{"bitrate_kbps": float, "score": float}, ...]
#     (>= 4 points recommended; falls back to a cubic polyfit if fewer).

import json
import sys

try:
    import numpy as np
except ImportError:
    sys.exit("bd_rate.py needs numpy (pip install numpy)")


def _bd(rate_a, q_a, rate_b, q_b, mode):
    """mode='rate' -> avg % bitrate delta at equal quality; 'quality' -> avg
    quality delta at equal rate. a=baseline, b=pelorus."""
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
            return np.polyval(P, hi) - np.polyval(P, lo)
        # too few points: trapezoid on the clamped range
        xs = np.linspace(lo, hi, 200)
        ys = np.interp(xs, x, y)
        return np.trapz(ys, xs)

    # b = pelorus, a = baseline. Both modes report the change of pelorus
    # relative to baseline: rate -> negative when pelorus needs fewer bits;
    # quality -> positive when pelorus is higher quality at equal bitrate.
    avg = (integ(xb, yb) - integ(xa, ya)) / (hi - lo)
    if mode == "rate":
        return (10.0 ** avg - 1.0) * 100.0   # % bitrate change
    return avg                                # quality units


def bd_rate(baseline, pelorus):
    ra = [p["bitrate_kbps"] for p in baseline]; qa = [p["score"] for p in baseline]
    rb = [p["bitrate_kbps"] for p in pelorus];  qb = [p["score"] for p in pelorus]
    return {
        "bd_rate_pct": _bd(ra, qa, rb, qb, "rate"),
        "bd_quality": _bd(ra, qa, rb, qb, "quality"),
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: bd_rate.py baseline.json pelorus.json")
    base = json.load(open(sys.argv[1]))
    pel = json.load(open(sys.argv[2]))
    r = bd_rate(base, pel)
    print(json.dumps(r, indent=2))
    bd = r["bd_rate_pct"]
    if bd == bd:  # not NaN
        verdict = "WIN" if bd < 0 else "loss"
        print(f"\nBD-rate: {bd:+.2f}%  ({verdict}: "
              f"{'fewer' if bd < 0 else 'more'} bits for equal quality)")
        print(f"BD-quality: {r['bd_quality']:+.3f} at equal bitrate")
