#!/usr/bin/env python3
"""shw-2 step 33: locate the aa fast=1 vs fast=0 residual on the UHD 770 and
compare each differing sample with the Arc B580's fast=0/fast=1 output for the
same input. yuv420p only. usage: 33-aa-residual-locate.py DIR W H FRAMES"""
import sys
from pathlib import Path

d, W, H, F = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
names = ["uhd-f0", "uhd-f1", "b580-f0", "b580-f1", "b580lin-f0", "b580lin-f1"]
raw = {n: (d / f"{n}.raw").read_bytes() for n in names if (d / f"{n}.raw").exists()}
cw, ch = W // 2, H // 2
planes = [("Y", W, H), ("U", cw, ch), ("V", cw, ch)]
fsz = W * H + 2 * cw * ch
for n in raw:
    assert len(raw[n]) == fsz * F, (n, len(raw[n]))


def pairs(a, b):
    out = []
    for f in range(F):
        off = f * fsz
        for pn, pw, ph in planes:
            for i in range(pw * ph):
                if raw[a][off + i] != raw[b][off + i]:
                    out.append((f, pn, i % pw, i // pw, off + i))
            off += pw * ph
    return out


print("differences uhd-f0 vs uhd-f1 (frame plane x y | values per run):")
for f, pn, x, y, k in pairs("uhd-f0", "uhd-f1"):
    vals = " ".join(f"{n}={raw[n][k]}" for n in raw)
    print(f"  f{f} {pn} x={x} y={y} (x%32={x % 32}) | {vals}")
for a, b in [("uhd-f0", "b580-f0"), ("uhd-f1", "b580-f1"), ("uhd-f0", "b580lin-f0"),
             ("uhd-f1", "b580lin-f1"), ("b580-f0", "b580-f1"), ("b580lin-f0", "b580lin-f1")]:
    if a in raw and b in raw:
        p = pairs(a, b)
        print(f"{a} vs {b}: {len(p)} differing samples"
              + (f"; rows {sorted({q[3] for q in p})[:8]}" if p else ""))
