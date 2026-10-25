#!/usr/bin/env python3
"""ADR-0163 regression: the dehalo shader must not rewrite line-art.

Two halves, both driven by the shader text (default: the shipped
ffmpeg-patches/files/vulkan/pelorus_dehalo.comp.glsl; pass another path to
check a different revision):

1. Structural: the shader source must carry the corrected expressions
   (edge step scale 0.25, both-polarity edge-mask ring scan, close-edge
   exclusion, DeHalo_alpha MaskedMerge(halos, clp, so) mix + Repair clamp).
2. Behavioural: a CPU model of the gate and pull, with each of those pieces
   selected from what the shader text contains, runs on a synthetic image of a
   1-px stroke (symmetric core, zero Sobel) and a Lanczos-ringed line. Stroke
   and line pixels must stay within one code and flat pixels must not change.

The pre-ADR-0163 shader fails both halves (stroke 30 -> 159 in the model).
"""

from __future__ import annotations

import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "ffmpeg-patches" / "files" / "vulkan" / "pelorus_dehalo.comp.glsl"
LIBREF = ROOT / "libpelorus" / "shaders" / "pelorus_dehalo.comp"

# AVOption defaults of pelorus_dehalo_vulkan.
BLUR, DARK, BRIGHT, LOW, HIGH, EDGE, RING = 2, 1.0, 1.0, 0.0625, 0.5, 0.08, 2.0
EPS = 0.0039
MAX_R = 8


def features(text: str) -> dict[str, bool]:
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    return {
        "edge step scale 0.25 (Sobel / 4)": bool(
            re.search(r"return\s+0\.25\s*\*\s*mag\s*;", code)
        ),
        "ring scan tests the edge mask in both polarities": bool(
            re.search(r"(el|er)\s*=\s*\1\s*\|\|\s*edge_step\(", code)
            and re.search(r"edge_step\([^;]*[-]\s*d\b", code)
            and re.search(r"edge_step\([^;]*[+]\s*(ivec2\(\s*)?d\b", code)
        ),
        "close-edge exclusion (line on both sides of an axis)": bool(
            re.search(r"\(el\s*&&\s*er\)", code) and re.search(r"\(eu\s*&&\s*ed\)", code)
        ),
        "so mix is MaskedMerge(halos, clp, so)": bool(
            re.search(r"lets\s*=\s*mix\(\s*hc\s*,\s*cg\[g\]\s*,\s*so\s*\)", code)
        ),
        "Repair clamp of the source into the lets range": bool(
            re.search(r"clamp\(\s*c\s*,\s*lo\s*,\s*hi\s*\)", code)
        ),
    }


def feathered(text: str) -> bool:
    """#201: the gate is evaluated at the eight neighbours and the flank pixel
    moves toward the range of their pulls with weight min(1, 2 n / 9). Pixels
    in the close-edge exclusion zone and strict local extrema (a thin line
    core, an isolated texture dot) keep the source."""
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    return bool(
        re.search(r"min\(\s*1\.0\s*,\s*2\.0\s*\*\s*float\(\s*n\s*\)\s*/\s*9\.0\s*\)", code)
        and re.search(r"mix\(\s*c\s*,\s*clamp\(\s*c\s*,\s*lo\s*,\s*hi\s*\)\s*,\s*m\s*\)", code)
        and re.search(r"c\s*>\s*nhi\s*\|\|\s*c\s*<\s*nlo|c\s*<=\s*nhi\s*&&\s*c\s*>=\s*nlo", code)
        and re.search(r"(cls|gate_class\([^;]*\))\s*==\s*2", code)
    )


def box(img, x, r):
    n = len(img)
    return sum(img[min(max(x + d, 0), n - 1)] for d in range(-r, r + 1)) / (2 * r + 1)


def px(img, x):
    return img[min(max(x, 0), len(img) - 1)]


def sobel_mag(img, x):
    # Rows are identical, so gy = 0 and gx = 4 * the central difference.
    gx = sum(w * px(img, x + 1) - w * px(img, x - 1) for w in (1, 2, 1))
    return abs(gx)


def gate_class(img, x):
    """ADR-0163 ring gate at column x (rows identical, so only the row axis):
    0 = no halo band, 1 = halo band, 2 = close-edge exclusion zone."""
    step = lambda i: 0.25 * sobel_mag(img, min(max(i, 0), len(img) - 1))
    rr = max(1, min(MAX_R, int(RING + 0.5)))
    if step(x) > EDGE:
        return 0
    left = any(step(x - d) > EDGE for d in range(1, rr + 1))
    right = any(step(x + d) > EDGE for d in range(1, rr + 1))
    if left and right:
        return 2
    return 1 if left or right else 0


def gate(img, x):
    return gate_class(img, x) == 1


def feather(img, x):
    """#201 flank pixel: neighbours (clamped to the row) in a 3x3 window of
    identical rows are the columns x-1, x, x+1, three rows each minus the centre."""
    if gate_class(img, x) == 2:
        return img[x]
    cols = [min(max(x + d, 0), len(img) - 1) for d in (-1, 0, 1)]
    n = sum((3 if d != 0 else 2) * gate(img, q) for d, q in zip((-1, 0, 1), cols))
    # The two vertical neighbours equal the centre, so the strict-extremum
    # guard never fires on identical rows; the format matrix covers it in 2-D.
    if n == 0:
        return img[x]
    pulls = [pull(img, q, True) for q in cols]
    m = min(1.0, 2.0 * n / 9.0)
    target = min(max(img[x], min(pulls)), max(pulls))
    return min(max(img[x] + (target - img[x]) * m, 0.0), 1.0)


def model(img, new: bool, feathered_mask: bool = False):
    """One image row (all rows identical); returns the filtered row."""
    scale = 0.25 if new else 1.0
    step = lambda x: scale * sobel_mag(img, x)
    rr = max(1, min(MAX_R, int(RING + 0.5)))
    out = []
    for x in range(len(img)):
        c = img[x]
        if new:
            if not gate(img, x):
                out.append(feather(img, x) if feathered_mask else c)
                continue
        else:
            # Old gate: raw Sobel vs edge, one-sided scan on the pixel value,
            # vertical neighbours equal the centre so only the row axis counts.
            on_line = step(x) > EDGE
            near = any(
                abs(max(px(img, x + d), px(img, x - d), c) - c) > EDGE
                for d in range(1, rr + 1)
            )
            if on_line or not near:
                out.append(c)
                continue
        out.append(pull(img, x, new))
    return out


def pull(img, x, new: bool):
    c = img[x]
    h = lambda i: box(img, i, BLUR)
    if not new:
        are = max(px(img, x + d) for d in (-1, 0, 1)) - min(px(img, x + d) for d in (-1, 0, 1))
        hs = [h(x), h(x - 1), h(x + 1)]
        ugly = max(hs) - min(hs)
        so = min(max(((are - ugly) / (are + EPS) - LOW) * (1 + HIGH), 0.0), 1.0)
        lets = c + (h(x) - c) * so
        strength = BRIGHT if lets < c else DARK
        return min(max(c - (c - lets) * strength, 0.0), 1.0)
    lo, hi = math.inf, -math.inf
    for _ in (-1, 0, 1):  # rows are identical: only dx varies the window
        for dx in (-1, 0, 1):
            g = x + dx
            win = [px(img, g + b) for b in (-1, 0, 1)]
            are = max(win) - min(win)
            hs = [h(g), h(g - 1), h(g + 1)]  # vertical neighbours equal h(g)
            ugly = max(hs) - min(hs)
            so = min(max(((are - ugly) / (are + EPS) - LOW) * (1 + HIGH), 0.0), 1.0)
            lets = h(g) + (px(img, g) - h(g)) * so
            lo, hi = min(lo, lets), max(hi, lets)
    remove = min(max(c, lo), hi)
    strength = BRIGHT if remove < c else DARK
    return min(max(c - (c - remove) * strength, 0.0), 1.0)


def codes(row):
    return [round(v * 255) for v in row]


def main() -> int:
    paths = [pathlib.Path(a) for a in sys.argv[1:]] or [SHIPPED, LIBREF]
    failed = False
    for path in paths:
        text = path.read_text(encoding="utf-8")
        feat = features(text)
        for name, ok in feat.items():
            if not ok:
                print(f"FAIL: {path.name}: missing {name}")
                failed = True
    if failed:
        return 1

    text = paths[0].read_text(encoding="utf-8")
    new = all(features(text).values())
    feath = feathered(text)
    if not feath:
        print(f"FAIL: {paths[0].name}: missing the feathered flank mask (#201)")
        return 1
    fill, width = 191, 96
    row = [fill] * width
    row[20] = 30
    ringed = [189, 195, 196, 181, 148, 105, 78, 104, 166, 203, 198, 188, 190]
    row[44:44 + len(ringed)] = ringed
    src = [v / 255 for v in row]
    got = codes(model(src, new, feath))
    line_art = [20] + list(range(47, 53))
    flat = list(range(0, 16)) + list(range(62, width))
    for x in line_art:
        if abs(got[x] - row[x]) > 1:
            print(f"FAIL: model rewrote line-art at x={x}: {row[x]} -> {got[x]}")
            failed = True
    for x in flat:
        if got[x] != row[x]:
            print(f"FAIL: model changed a flat pixel at x={x}: {row[x]} -> {got[x]}")
            failed = True
    # #201: the steep flank next to the line core (x=53, 203 above the 191
    # fill, own step above `edge`) is outside the ADR-0163 band; the feathered
    # mask must pull at least half of its overshoot back.
    if (got[53] - fill) * 2 > row[53] - fill:
        print(f"FAIL: model left the steep-flank overshoot at x=53: {row[53]} -> {got[53]}")
        failed = True
    if failed:
        return 1
    print("PASS: dehalo shader keeps line-art and flats unchanged and pulls the steep flank")
    return 0


if __name__ == "__main__":
    sys.exit(main())
