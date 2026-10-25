#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Write a copy of a header with one known line planted wrong.

The fast suite proves that ffmpeg-patches/test/sei_fit_test.c catches a drift
of the SEI fit rules Pelorus keeps in ffmpeg-patches/files/pelorus_sei_fit.h
(ADR-0236): Meson writes the header with one rule changed into the build
directory and builds the test against it, and each such test must fail.

It refuses (exit 1), never guesses, when the source cannot be read or the
planted line does not occur exactly once.

Exit status: 0 written; 1 refused; 2 usage error.

    python3 -I scripts/plant-header.py SRC OUT NAME
    python3 -I scripts/plant-header.py --self-test
"""

import os
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 1 << 20
# One rule each of pelorus_sei_fit.h, rewritten so the header no longer
# matches what sei_fit_test.c pins.
PLANTS = {
    "reserve": ("#define PEL_SEI_HEVC_RESERVE 256u\n",
                "#define PEL_SEI_HEVC_RESERVE 255u\n"),
    "payload": ("#define PEL_SEI_HEVC_PAYLOAD_LIMIT 512u\n",
                "#define PEL_SEI_HEVC_PAYLOAD_LIMIT 513u\n"),
    "epb": ("        if (zeros >= 2u && p[i] <= 3u) {\n",
            "        if (zeros >= 2u && p[i] <= 2u) {\n"),
    "truncated": ("    return n + 3u + epb > 4u * (pmax + 5u) / 3u;\n",
                  "    return n + 3u + epb > 4u * (pmax + 6u) / 3u;\n"),
    "charge": ("    if (n <= room->payload)\n",
               "    if (n < room->payload)\n"),
}


class Refused(Exception):
    pass


def planted(text, name):
    old, new = PLANTS[name]
    if text.count(old) != 1:
        raise Refused("plant %s: %r occurs %d times, want 1"
                      % (name, old.strip(), text.count(old)))
    text = text.replace(old, new)
    guard = "AVCODEC_PELORUS_SEI_FIT_H"
    old_guard_block = "#ifndef " + guard + "\n#define " + guard + "\n"
    if old_guard_block in text:
        planted_guard = "AVCODEC_PELORUS_SEI_FIT_%s_H" % name.upper()
        new_guard_block = (
            "#ifndef " + planted_guard + "\n#define " + planted_guard + "\n"
            "#ifndef " + guard + "\n#define " + guard + "\n#endif\n"
        )
        text = text.replace(old_guard_block, new_guard_block)
        text = text.replace(
            "#endif /* " + guard + " */\n",
            "#endif /* " + planted_guard + " */\n",
        )
    return text


def run(src, out, name):
    try:
        if src.stat().st_size > MAX_BYTES:
            raise Refused("%s is larger than %d bytes" % (src, MAX_BYTES))
        text = planted(src.read_text(encoding="utf-8"), name)
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        os.replace(tmp, out)
    except (Refused, OSError, UnicodeDecodeError) as err:
        print("plant-header: refused: %s" % err, file=sys.stderr)
        return 1
    return 0


GOOD = "/* x */\n" + "".join(old for old, _ in PLANTS.values()) + "int y;\n"
GUARD_GOOD = (
    "#ifndef AVCODEC_PELORUS_SEI_FIT_H\n"
    "#define AVCODEC_PELORUS_SEI_FIT_H\n"
    + GOOD
    + "#endif /* AVCODEC_PELORUS_SEI_FIT_H */\n"
)
GUARD_WANT = (
    "#ifndef AVCODEC_PELORUS_SEI_FIT_RESERVE_H\n"
    "#define AVCODEC_PELORUS_SEI_FIT_RESERVE_H\n"
    "#ifndef AVCODEC_PELORUS_SEI_FIT_H\n"
    "#define AVCODEC_PELORUS_SEI_FIT_H\n"
    "#endif\n"
    + GOOD.replace("256u", "255u")
    + "#endif /* AVCODEC_PELORUS_SEI_FIT_RESERVE_H */\n"
)
SELF_TEST_CASES = [
    # (label, source text or None for a missing file, plant, expected text or None)
    ("reserve rewrites one line", GOOD, "reserve", GOOD.replace("256u", "255u")),
    ("payload rewrites one line", GOOD, "payload", GOOD.replace("512u", "513u")),
    ("epb rewrites one line", GOOD, "epb", GOOD.replace("<= 3u", "<= 2u")),
    ("truncated rewrites one line", GOOD, "truncated", GOOD.replace("+ 5u)", "+ 6u)")),
    ("charge rewrites one line", GOOD, "charge", GOOD.replace("n <= room", "n < room")),
    ("guard rewritten", GUARD_GOOD, "reserve", GUARD_WANT),
    ("line absent", "int y;\n", "reserve", None),
    ("line twice", GOOD + PLANTS["reserve"][0], "reserve", None),
    ("source missing", None, "reserve", None),
]


def self_case(root, text, name):
    src = root / "in.h"
    out = root / "out.h"
    if text is not None:
        src.write_text(text, encoding="utf-8", newline="")
    if run(src, out, name) != 0:
        return None
    return out.read_text(encoding="utf-8")


def self_test():
    """Plant each line and require exactly that rewrite; refusals must refuse."""
    failures = 0
    for label, text, name, want in SELF_TEST_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            got = self_case(Path(tmp), text, name)
        if got != want:
            print("self-test FAIL: %s: got %r, want %r" % (label, got, want))
            failures += 1
    print("self-test: %d cases, %d failures" % (len(SELF_TEST_CASES), failures))
    return 1 if failures else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 3 or argv[2] not in PLANTS:
        print((__doc__ or "").strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    return run(Path(argv[0]), Path(argv[1]), argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
