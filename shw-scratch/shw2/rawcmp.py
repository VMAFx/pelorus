#!/usr/bin/env python3
"""shw-2 raw-frame comparator: per-plane diff stats for yuv420p/nv12/10-12-bit/rgba.
usage: rawcmp.py FMT W H A.raw B.raw [frames]"""
import array, math, sys

def planes(fmt, w, h):
    cw, ch = (w + 1) // 2, (h + 1) // 2
    return {
        "yuv420p": [("Y", w, h, 1, 1, 0), ("U", cw, ch, 1, 1, 0), ("V", cw, ch, 1, 1, 0)],
        "yuv444p": [("Y", w, h, 1, 1, 0), ("U", w, h, 1, 1, 0), ("V", w, h, 1, 1, 0)],
        "yuv420p10le": [("Y", w, h, 2, 1, 0), ("U", cw, ch, 2, 1, 0), ("V", cw, ch, 2, 1, 0)],
        "yuv420p12le": [("Y", w, h, 2, 1, 0), ("U", cw, ch, 2, 1, 0), ("V", cw, ch, 2, 1, 0)],
        "yuv444p10le": [("Y", w, h, 2, 1, 0), ("U", w, h, 2, 1, 0), ("V", w, h, 2, 1, 0)],
        "nv12": [("Y", w, h, 1, 1, 0), ("UV", cw, ch, 1, 2, 0)],
        "p010le": [("Y", w, h, 2, 1, 6), ("UV", cw, ch, 2, 2, 6)],
        "p012le": [("Y", w, h, 2, 1, 4), ("UV", cw, ch, 2, 2, 4)],
        "rgba": [("RGBA", w, h, 1, 4, 0)],
    }[fmt]

def load(path, bps):
    raw = open(path, "rb").read()
    if bps == 1:
        return raw
    a = array.array("H"); a.frombytes(raw[: len(raw) // 2 * 2]); return a

def main():
    fmt, w, h, pa, pb = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
    frames = int(sys.argv[6]) if len(sys.argv) > 6 else 1
    pl = planes(fmt, w, h)
    bps = pl[0][3]
    A, B = load(pa, bps), load(pb, bps)
    per_frame = sum(pw * ph * comps for _, pw, ph, _, comps, _ in pl)
    if len(A) != len(B) or len(A) != per_frame * frames:
        print(f"SIZE a={len(A)} b={len(B)} expected={per_frame*frames} samples"); return 2
    rc = 0
    for f in range(frames):
        off = f * per_frame
        for name, pw, ph, _, comps, shift in pl:
            n = pw * ph * comps
            a, b = A[off:off + n], B[off:off + n]
            off += n
            diffs = [abs((x >> shift) - (y >> shift)) for x, y in zip(a, b)]
            nd = sum(1 for d in diffs if d)
            mx = max(diffs) if diffs else 0
            peak = ((1 << (8 * bps)) - 1) >> shift if bps == 2 else 255
            if fmt in ("yuv420p10le", "yuv444p10le"): peak = 1023
            if fmt == "yuv420p12le": peak = 4095
            mse = sum(d * d for d in diffs) / max(1, len(diffs))
            psnr = "inf" if mse == 0 else f"{10 * math.log10(peak * peak / mse):.2f}"
            rows = sorted({i // (pw * comps) for i, d in enumerate(diffs) if d})
            rowinfo = f" rows={rows[0]}..{rows[-1]}({len(rows)})" if rows else ""
            print(f"frame{f} {name}: differing={nd}/{n} max={mx} psnr={psnr}{rowinfo}")
            rc |= bool(nd)
    return rc

sys.exit(main())
