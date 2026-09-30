#!/usr/bin/env python3
"""shw-3 halfpsnr.py -- per-frame ROI placement check from half-frame PSNR.

Usage: halfpsnr.py <none_top.log> <none_bot.log> <roi_top.log> <roi_bot.log> [alt|top]

Each .log is an FFmpeg psnr stats_file for the top or bottom region of the
baseline (no ROI) and the ROI encode.  For every frame the ROI's quality cost
shows up as a PSNR drop versus the baseline in the half the ROI (a positive
delta-QP) covered.  The frame is "as expected" when the expected half dropped
more than the other half, and "swapped" otherwise.  Prints one summary line and
writes per-frame rows to stdout after it.
"""
import re
import sys


def load(path):
    vals = {}
    for line in open(path):
        m = re.search(r"n:(\d+) .*psnr_y:([0-9.inf]+)", line)
        if m:
            v = m.group(2)
            vals[int(m.group(1))] = 99.0 if v == "inf" else float(v)
    return vals


nt, nb, rt, rb = (load(p) for p in sys.argv[1:5])
pattern = sys.argv[5] if len(sys.argv) > 5 else "alt"
rows, swapped, weak, n = [], 0, 0, 0
margins = []
for f in sorted(rt):
    if f not in nt or f not in nb or f not in rb:
        continue
    n += 1
    dt, db = nt[f] - rt[f], nb[f] - rb[f]
    exp_top = pattern == "top" or (f - 1) % 2 == 0  # psnr n: is 1-based
    de, do = (dt, db) if exp_top else (db, dt)
    margin = de - do
    margins.append(margin)
    status = "ok" if margin > 0 else "SWAPPED"
    if margin <= 0:
        swapped += 1
    elif margin < 0.5:
        weak += 1
    rows.append(f"{f},{'top' if exp_top else 'bot'},{dt:.3f},{db:.3f},{margin:.3f},{status}")
margins.sort()
med = margins[len(margins) // 2] if margins else float("nan")
print(f"SUMMARY frames={n} swapped={swapped} weak(<0.5dB)={weak} "
      f"min_margin={margins[0] if margins else float('nan'):.3f} median_margin={med:.3f}")
print("frame,expected_half,drop_top_dB,drop_bot_dB,margin_dB,status")
print("\n".join(rows))
