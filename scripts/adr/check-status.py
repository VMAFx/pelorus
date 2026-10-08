#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Fail on an ADR that is Proposed without a pending-implementation line.

Rule: an ADR whose Status is Proposed carries the header line
`- **Implementation**: pending (#<issue>)`. A Proposed ADR without it describes
merged work that was never flipped to Accepted. Accepted, Rejected, Deprecated
and Superseded ADRs are ignored. A missing or unknown Status fails loudly.

    python3 -I scripts/adr/check-status.py [--root DIR] | --self-test
"""

import re
import sys
import tempfile
from pathlib import Path

STATUS_RE = re.compile(r"^- \*\*Status\*\*:\s*([A-Za-z]+)")
IMPL_RE = re.compile(r"^- \*\*Implementation\*\*: pending \(#[0-9]+\)\s*$")
KNOWN = {"Proposed", "Accepted", "Rejected", "Deprecated", "Superseded"}
HEADER_LINES = 12


def check_file(path):
    """Return None when the ADR passes, else a (line, message) pair."""
    lines = path.read_text(encoding="utf-8").splitlines()[:HEADER_LINES]
    status = None
    for no, line in enumerate(lines, 1):
        m = STATUS_RE.match(line)
        if m:
            status, status_no = m.group(1), no
            break
    if status is None:
        return (1, "malformed: no '- **Status**:' header line")
    if status not in KNOWN:
        return (status_no, "malformed: unknown status '%s'" % status)
    if status != "Proposed":
        return None
    if any(IMPL_RE.match(line) for line in lines):
        return None
    return (status_no, "Proposed without '- **Implementation**: pending (#N)'; "
            "flip to Accepted if its PR merged")


def scan(root):
    offenders = []
    for path in sorted(root.glob("[0-9][0-9][0-9][0-9]-*.md")):
        if path.name.startswith("0000-"):
            continue
        res = check_file(path)
        if res:
            offenders.append((path, res[0], res[1]))
    return offenders


def report(offenders):
    for path, line, msg in offenders:
        print("ADR-%s %s:%d %s" % (path.name[:4], path, line, msg))
    return 1 if offenders else 0


def self_test():
    cases = {
        "0001-a.md": ("- **Status**: Proposed\n", 1),
        "0002-b.md": ("- **Status**: Proposed (2026) - x\n"
                      "- **Implementation**: pending (#112)\n", 0),
        "0003-c.md": ("- **Status**: Accepted; amended by x\n", 0),
        "0004-d.md": ("- **Status**: Maybe\n", 1),
        "0005-e.md": ("# no status line\n", 1),
    }
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, (body, want) in cases.items():
            d = Path(tmp) / name[:4]
            d.mkdir()
            (d / name).write_text("# ADR\n\n" + body, encoding="utf-8")
            got = 1 if scan(d) else 0
            if got != want:
                print("self-test FAIL %s: want exit %d, got %d" % (name, want, got))
                failed = 1
    print("self-test %s" % ("FAILED" if failed else "passed"))
    return failed


def main(argv):
    if "--self-test" in argv:
        return self_test()
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else Path("docs/adr")
    if not root.is_dir():
        print("check-status: %s is not a directory" % root)
        return 2
    return report(scan(root))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
