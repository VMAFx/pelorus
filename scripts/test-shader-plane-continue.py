#!/usr/bin/env python3
"""Regression check for BUG-028: deblock and aa plane loops must `continue`.

A plane whose size excludes `pos` (subsampled chroma) must be skipped with
`continue`; `return` ends the invocation and leaves a later full-size plane
(yuva420p alpha) unwritten. aa has one plane loop per `fast` variant; the
fast=1 loop also hosts the workgroup barriers, so it must not return either.

Usage: test-shader-plane-continue.py [shader-dir]
"""

from __future__ import annotations

import pathlib
import re
import sys

DEFAULT = pathlib.Path(__file__).resolve().parent.parent / "ffmpeg-patches" / "files" / "vulkan"
LOOP = re.compile(r"for \(uint i = 0; i < planes; i\+\+\) \{")


def plane_loop_bodies(text: str) -> list[str]:
    bodies = []
    for m in LOOP.finditer(text):
        depth, end = 1, m.end()
        while depth and end < len(text):
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        bodies.append(text[m.end() : end])
    return bodies


def main() -> int:
    shipped = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    failures: list[str] = []
    for name, expected in (("pelorus_deblock", 1), ("pelorus_aa", 2)):
        bodies = plane_loop_bodies((shipped / f"{name}.comp.glsl").read_text())
        if len(bodies) != expected:
            failures.append(f"{name}: expected {expected} plane loop(s), found {len(bodies)}")
        for body in bodies:
            if re.search(r"\breturn\s*;", body):
                failures.append(f"{name}: plane loop uses return (must continue)")
    for msg in failures:
        print("FAIL:", msg)
    if not failures:
        print("OK: deblock/aa plane loops continue past excluded planes")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
