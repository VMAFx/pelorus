#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Fail when the mirror contract page omits a file VMAFx mirrors.

Rule: every non-comment line of libpelorus/mirror-paths.txt must appear,
verbatim, in docs/api/mirror-contract.md. An empty or missing list fails.

    python3 -I scripts/check-mirror-contract.py [--list F] [--page F] | --self-test
"""

import sys
import tempfile
from pathlib import Path

LIST = Path("libpelorus/mirror-paths.txt")
PAGE = Path("docs/api/mirror-contract.md")


def read_paths(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def missing(list_path, page_path):
    """Return (paths, missing); raise OSError when an input cannot be read."""
    paths = read_paths(list_path)
    page = page_path.read_text(encoding="utf-8")
    return paths, [p for p in paths if p not in page]


def run(list_path, page_path):
    try:
        paths, gone = missing(list_path, page_path)
    except OSError as exc:
        print("check-mirror-contract: %s" % exc)
        return 2
    if not paths:
        print("check-mirror-contract: %s lists no files" % list_path)
        return 1
    for p in gone:
        print("%s: does not name mirrored file %s" % (page_path, p))
    return 1 if gone else 0


def self_test():
    paths = ["libpelorus/src/a.c", "libpelorus/src/b.c"]
    page = "`libpelorus/src/a.c` and `libpelorus/src/b.c`\n"
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        lst, pg = Path(tmp) / "list.txt", Path(tmp) / "page.md"
        lst.write_text("# c\n" + "\n".join(paths) + "\n", encoding="utf-8")
        cases = [
            ("complete page", page, 0),
            ("removed name", page.replace("libpelorus/src/b.c", "b"), 1),
            ("empty page", "", 1),
        ]
        for label, body, want in cases:
            pg.write_text(body, encoding="utf-8")
            if run(lst, pg) != want:
                print("self-test FAIL %s: want exit %d" % (label, want))
                failed = 1
        lst.write_text("# only a comment\n", encoding="utf-8")
        pg.write_text(page, encoding="utf-8")
        if run(lst, pg) != 1:
            print("self-test FAIL empty list: want exit 1")
            failed = 1
        if run(Path(tmp) / "nope.txt", pg) != 2:
            print("self-test FAIL missing list: want exit 2")
            failed = 1
    print("self-test %s" % ("FAILED" if failed else "passed"))
    return failed


def main(argv):
    if "--self-test" in argv:
        return self_test()
    lst = Path(argv[argv.index("--list") + 1]) if "--list" in argv else LIST
    page = Path(argv[argv.index("--page") + 1]) if "--page" in argv else PAGE
    return run(lst, page)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
