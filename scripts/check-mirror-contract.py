#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Fail when the mirror contract page or a mirrored file breaks the contract.

Rules:
  1. every non-comment line of libpelorus/mirror-paths.txt appears, verbatim,
     in docs/api/mirror-contract.md; an empty or missing list fails;
  2. every listed file keeps the layout the VMAFx renderer
     (scripts/sync-pelorus-interop.sh, VMAFx/vmafx#2649) accepts: a source
     opens with "/**", its first " */" line is followed by a blank line, that
     comment holds exactly one SPDX line naming EUPL-1.2, and the body has at
     most one intra-Pelorus include; the fixture holds that one SPDX line
     before its first intra-Pelorus include.

    python3 -I scripts/check-mirror-contract.py [--list F] [--page F] [--root D]
    python3 -I scripts/check-mirror-contract.py --self-test
"""

import sys
import tempfile
from pathlib import Path

LIST = Path("libpelorus/mirror-paths.txt")
PAGE = Path("docs/api/mirror-contract.md")
FIXTURE = "libpelorus/test/interop_test.c"
LICENSE = "EUPL-1.2"
# Split so REUSE does not take the joined tag for the licence of this script.
TAG = "SPDX-" + "License-Identifier:"
SPDX_LINE = " * %s %s" % (TAG, LICENSE)
INCLUDE = '#include "pelorus/'


def read_paths(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def source_layout(lines):
    """Return the renderer refusal for a mirrored source, or None."""
    if not lines or lines[0] != "/**" or " */" not in lines:
        return "no leading /** ... */ licence comment"
    end = lines.index(" */")
    if end + 1 >= len(lines) or lines[end + 1] != "":
        return "licence comment is not followed by a blank line"
    if [x for x in lines[:end] if TAG in x] != [SPDX_LINE]:
        return "licence comment does not hold exactly one %r" % SPDX_LINE
    if sum(x.startswith(INCLUDE) for x in lines[end + 2:]) > 1:
        return "more than one intra-Pelorus include to rewrite"
    return None


def fixture_layout(lines):
    """Return the renderer refusal for the shared fixture, or None."""
    first = next((i for i, x in enumerate(lines) if x.startswith(INCLUDE)), -1)
    if first < 0:
        return "no intra-Pelorus include"
    if [x for x in lines[:first] if TAG in x] != [SPDX_LINE]:
        return "prefix does not hold exactly one %r" % SPDX_LINE
    return None


def layout_errors(root, paths):
    errors = []
    for rel in paths:
        lines = (root / rel).read_text(encoding="utf-8").splitlines()
        why = fixture_layout(lines) if rel == FIXTURE else source_layout(lines)
        if why:
            errors.append("%s: VMAFx renderer refuses it: %s" % (rel, why))
    return errors


def run(list_path, page_path, root=Path(".")):
    try:
        paths = read_paths(list_path)
        page = page_path.read_text(encoding="utf-8")
        errors = layout_errors(root, paths)
    except OSError as exc:
        print("check-mirror-contract: %s" % exc)
        return 2
    if not paths:
        print("check-mirror-contract: %s lists no files" % list_path)
        return 1
    for p in paths:
        if p not in page:
            errors.append("%s: does not name mirrored file %s" % (page_path, p))
    for e in errors:
        print(e)
    return 1 if errors else 0


GOOD_SOURCE = "/**\n *\n%s\n */\n\n%sx.h\"\nint x;\n" % (SPDX_LINE, INCLUDE)
GOOD_FIXTURE = "/**\n%s\n */\n\n%sa.h\"\n%sb.h\"\n" % (SPDX_LINE, INCLUDE, INCLUDE)
SOURCE_DEFECTS = [
    ("no blank after header", GOOD_SOURCE.replace(" */\n\n", " */\nint y;\n")),
    ("other licence", GOOD_SOURCE.replace(LICENSE, "MIT")),
    ("two SPDX lines", GOOD_SOURCE.replace(SPDX_LINE, SPDX_LINE + "\n" + SPDX_LINE)),
    ("no SPDX line", GOOD_SOURCE.replace(SPDX_LINE + "\n", "")),
    ("17-line header", "/*\n" + " * x\n" * 15 + " */\n\n" + INCLUDE + 'x.h"\n'),
    ("second include", GOOD_SOURCE + INCLUDE + 'y.h"\n'),
]


def self_case(tmp, label, src, fixture, page, want):
    root = Path(tmp)
    (root / "libpelorus/src").mkdir(parents=True, exist_ok=True)
    (root / "libpelorus/test").mkdir(parents=True, exist_ok=True)
    (root / "libpelorus/src/a.c").write_text(src, encoding="utf-8")
    (root / FIXTURE).write_text(fixture, encoding="utf-8")
    lst, pg = root / "list.txt", root / "page.md"
    lst.write_text("# c\nlibpelorus/src/a.c\n%s\n" % FIXTURE, encoding="utf-8")
    pg.write_text(page, encoding="utf-8")
    if run(lst, pg, root) == want:
        return 0
    print("self-test FAIL %s: want exit %d" % (label, want))
    return 1


def self_test():
    page = "`libpelorus/src/a.c` and `%s`\n" % FIXTURE
    cases = [
        ("complete", GOOD_SOURCE, GOOD_FIXTURE, page, 0),
        ("removed name", GOOD_SOURCE, GOOD_FIXTURE, page.replace("src/a.c", "a"), 1),
        ("empty page", GOOD_SOURCE, GOOD_FIXTURE, "", 1),
        ("fixture other licence", GOOD_SOURCE, GOOD_FIXTURE.replace(LICENSE, "MIT"), page, 1),
        ("fixture no include", GOOD_SOURCE, "/**\n%s\n */\n" % SPDX_LINE, page, 1),
    ]
    cases += [(lab, src, GOOD_FIXTURE, page, 1) for lab, src in SOURCE_DEFECTS]
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for label, src, fixture, pg, want in cases:
            failed |= self_case(tmp, label, src, fixture, pg, want)
        lst, pg = Path(tmp) / "list.txt", Path(tmp) / "page.md"
        lst.write_text("# only a comment\n", encoding="utf-8")
        if run(lst, pg, Path(tmp)) != 1:
            print("self-test FAIL empty list: want exit 1")
            failed = 1
        if run(Path(tmp) / "nope.txt", pg, Path(tmp)) != 2:
            print("self-test FAIL missing list: want exit 2")
            failed = 1
    print("self-test %s" % ("FAILED" if failed else "passed"))
    return failed


def arg(argv, flag, default):
    return Path(argv[argv.index(flag) + 1]) if flag in argv else default


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return run(arg(argv, "--list", LIST), arg(argv, "--page", PAGE),
               arg(argv, "--root", Path(".")))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
