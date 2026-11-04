#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check ADR status compliance and render docs/adr/README.md index table.

Rules:
1. An ADR whose Status is Proposed carries the header line
   `- **Implementation**: pending (#<issue>)`. A Proposed ADR without it describes
   merged work that was never flipped to Accepted. Accepted, Rejected, Deprecated
   and Superseded ADRs are ignored. A missing or unknown Status fails loudly.
2. The index table in docs/adr/README.md must match the rendered table generated
   from the ADR files (link, title from H1, status from header).
3. --write renders the index in docs/adr/README.md, preserving text outside the
   table, and then checks every ADR status.
4. --self-test runs planted cases verifying check and write operations.

Usage:
    python3 -I scripts/adr/check-status.py [--root DIR] [--write]
    python3 -I scripts/adr/check-status.py --self-test
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

STATUS_RE = re.compile(r"^- \*\*Status\*\*:\s*([A-Za-z]+)")
STATUS_LINE_RE = re.compile(r"^- \*\*Status\*\*:\s*(.*)")
IMPL_RE = re.compile(r"^- \*\*Implementation\*\*: pending \(#[0-9]+\)\s*$")
H1_RE = re.compile(r"^#\s+(?:ADR-[0-9]{4}:\s*)?(.*)")
KNOWN = {"Proposed", "Accepted", "Rejected", "Deprecated", "Superseded"}
HEADER_LINES = 15


def check_file(path: Path) -> tuple[int, str] | None:
    """Return None when the ADR passes, else a (line, message) pair."""
    lines = path.read_text(encoding="utf-8").splitlines()[:HEADER_LINES]
    status = None
    status_no = 1
    for no, line in enumerate(lines, 1):
        m = STATUS_RE.match(line)
        if m:
            status, status_no = m.group(1), no
            break
    if status is None:
        return (1, "malformed: no '- **Status**:' header line")
    if status not in KNOWN:
        return (status_no, f"malformed: unknown status '{status}'")
    if status != "Proposed":
        return None
    if any(IMPL_RE.match(line) for line in lines):
        return None
    return (
        status_no,
        "Proposed without '- **Implementation**: pending (#N)'; flip to Accepted if its PR merged",
    )


def extract_title(path: Path) -> str:
    """Extract ADR title from its H1 heading."""
    lines = path.read_text(encoding="utf-8").splitlines()[:HEADER_LINES]
    for line in lines:
        if line.startswith("# "):
            m = H1_RE.match(line)
            if m and m.group(1).strip():
                return m.group(1).strip()
    return path.stem


def extract_status(path: Path) -> str:
    """Extract and normalize status from ADR header line."""
    lines = path.read_text(encoding="utf-8").splitlines()[:HEADER_LINES]
    for line in lines:
        m = STATUS_LINE_RE.match(line)
        if m:
            raw = m.group(1).strip()
            # Normalize [ADR-NNNN] to [NNNN]
            s = re.sub(r"\[ADR-([0-9]{4})\]", r"[\1]", raw)
            # Strip trailing parenthetical comments not part of a markdown link
            s = re.sub(r"(?<!\])\s*\([^)]*\)\s*$", "", s).strip()
            # Strip dash annotations (em-dash, en-dash, hyphen)
            s = re.split(r"\s+[\u2014\u2013-]\s+", s)[0]
            if s.startswith("Superseded by") or ";" in s:
                s = re.sub(r"(?<!\])\s*\([^)]*\)\s*$", "", s).strip()
                return s
            m2 = re.match(r"^([A-Za-z]+)", s)
            if m2:
                return m2.group(1)
            return s
    return "Unknown"


def render_readme(root: Path) -> str:
    """Render docs/adr/README.md preserving text outside the index table."""
    index = root / "README.md"
    preamble: list[str] = []
    trailer: list[str] = []

    if index.is_file():
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        idx = 0
        while idx < len(lines):
            if lines[idx].startswith("| ADR | Title | Status |"):
                break
            preamble.append(lines[idx])
            idx += 1
        if idx == len(lines):
            raise ValueError(f"{index}: no ADR index table header '| ADR | Title | Status |'")
        while idx < len(lines):
            if not lines[idx].startswith("|"):
                break
            idx += 1
        trailer = lines[idx:]
    else:
        preamble = [
            "# Architecture Decision Records\n\n",
        ]

    rows = ["| ADR | Title | Status |", "|---|---|---|"]
    for path in sorted(root.glob("[0-9][0-9][0-9][0-9]-*.md")):
        if path.name.startswith("0000-"):
            continue
        num = path.name[:4]
        title = extract_title(path)
        status = extract_status(path)
        rows.append(f"| [{num}]({path.name}) | {title} | {status} |")
    table_text = "\n".join(rows) + "\n"
    return "".join(preamble) + table_text + "".join(trailer)


def write_readme(root: Path) -> bool:
    """Write rendered README.md if content differs; return True if written."""
    index = root / "README.md"
    rendered = render_readme(root)
    if not index.is_file() or index.read_text(encoding="utf-8") != rendered:
        index.write_text(rendered, encoding="utf-8")
        return True
    return False


def scan_index(root: Path) -> list[tuple[Path, int, str]]:
    """Return index rows whose content differs from the rendered table."""
    index = root / "README.md"
    if not index.is_file():
        return [(index, 1, "docs/adr/README.md missing")]

    current = index.read_text(encoding="utf-8")
    try:
        expected = render_readme(root)
    except ValueError as exc:
        return [(index, 1, str(exc))]
    if current == expected:
        return []

    c_lines = current.splitlines()
    e_lines = expected.splitlines()
    offenders: list[tuple[Path, int, str]] = []
    for line_no, (c, e) in enumerate(zip(c_lines, e_lines), 1):
        if c != e:
            offenders.append(
                (
                    index,
                    line_no,
                    f"index row differs from rendered ADR table (got {c!r}, want {e!r})",
                )
            )
    if len(c_lines) != len(e_lines):
        offenders.append(
            (
                index,
                min(len(c_lines), len(e_lines)) + 1,
                f"index line count differs ({len(c_lines)} vs {len(e_lines)}); run scripts/adr/check-status.py --write",
            )
        )
    return offenders


def scan(root: Path) -> list[tuple[Path, int, str]]:
    """Scan root for ADR status issues and index drift."""
    offenders = scan_index(root)
    for path in sorted(root.glob("[0-9][0-9][0-9][0-9]-*.md")):
        if path.name.startswith("0000-"):
            continue
        res = check_file(path)
        if res:
            offenders.append((path, res[0], res[1]))
    return offenders


def report(offenders: list[tuple[Path, int, str]]) -> int:
    """Print offenders and return exit code."""
    for path, line, msg in offenders:
        label = "index" if path.name == "README.md" else "ADR-" + path.name[:4]
        print(f"{label} {path}:{line} {msg}")
    return 1 if offenders else 0


_ROW_HEAD = "# ADRs\n\n| ADR | Title | Status |\n|---|---|---|\n"


def _plant(tmppath: Path, name: str, adrs: dict[str, str], readme: str | None) -> Path:
    """Create an ADR tree; without readme text, render the index with --write."""
    root = tmppath / name
    root.mkdir()
    for fname, body in adrs.items():
        (root / fname).write_text(body, encoding="utf-8")
    if readme is None:
        write_readme(root)
    else:
        (root / "README.md").write_text(readme, encoding="utf-8")
    return root


def _case_missing_row(tmppath: Path) -> str | None:
    """A missing index row is refused, and --write restores it."""
    root = _plant(
        tmppath,
        "missing_row",
        {
            "0001-first.md": "# ADR-0001: First\n\n- **Status**: Accepted\n",
            "0002-second.md": "# ADR-0002: Second\n\n- **Status**: Accepted\n",
        },
        _ROW_HEAD + "| [0001](0001-first.md) | First | Accepted |\n",
    )
    if not scan(root):
        return "missing row: scan accepted partial index"
    print("self-test PASS: planted missing row in index refused")
    write_readme(root)
    if scan(root):
        return "restore row: scan rejected restored index"
    print("self-test PASS: --write restored missing index row")
    return None


# (directory, ADR text, index text or None, scan must refuse, label)
_STATUS_CASES = (
    (
        "wrong_index_status",
        "- **Status**: Accepted\n",
        _ROW_HEAD + "| [0001](0001-first.md) | First | Proposed |\n",
        True,
        "wrong status in index",
    ),
    ("wrong_adr_status", "- **Status**: Maybe\n", None, True, "unknown ADR status"),
    ("proposed_no_impl", "- **Status**: Proposed\n", None, True, "Proposed without implementation line"),
    (
        "proposed_impl",
        "- **Status**: Proposed\n- **Implementation**: pending (#123)\n",
        None,
        False,
        "Proposed with implementation line",
    ),
)


def _case_statuses(tmppath: Path) -> str | None:
    """Each planted status case is refused or accepted as declared."""
    for name, status, readme, refuse, label in _STATUS_CASES:
        adr = {"0001-first.md": "# ADR-0001: First\n\n" + status}
        refused = bool(scan(_plant(tmppath, name, adr, readme)))
        if refused != refuse:
            return f"{label}: scan {'refused' if refused else 'accepted'} it"
        print(f"self-test PASS: {label} {'refused' if refuse else 'accepted'}")
    return None


def _case_bad_header(tmppath: Path) -> str | None:
    """README without exact ADR table header is refused by --write and left unchanged."""
    root = tmppath / "bad_header"
    root.mkdir()
    readme = root / "README.md"
    original = (
        "# Architecture Decision Records\n\n"
        "Preamble text.\n\n"
        "| ADR | Name | Status |\n"
        "|---|---|---|\n"
    )
    readme.write_text(original, encoding="utf-8")
    (root / "0001-first.md").write_text("# ADR-0001: First\n\n- **Status**: Accepted\n", encoding="utf-8")
    rc = main(["--root", str(root), "--write"])
    if rc == 0:
        return "bad header: --write returned 0"
    if readme.read_text(encoding="utf-8") != original:
        return "bad header: --write modified README.md"
    print("self-test PASS: bad header refused by --write and README unchanged")
    return None


def _case_write_checks(tmppath: Path) -> str | None:
    """--write renders the index and still refuses a Proposed ADR without its Implementation line."""
    root = tmppath / "write_checks"
    root.mkdir()
    (root / "README.md").write_text("# ADRs\n\n| ADR | Title | Status |\n|---|---|---|\n", encoding="utf-8")
    (root / "0001-first.md").write_text("# ADR-0001: First\n\n- **Status**: Proposed\n", encoding="utf-8")
    if main(["--root", str(root), "--write"]) == 0:
        return "write checks: --write accepted a Proposed ADR without an Implementation line"
    print("self-test PASS: --write still refuses a Proposed ADR without an Implementation line")
    return None


def _case_argparse(_tmppath: Path) -> str | None:
    """A flag without its value exits with status 2."""
    try:
        parse_args(["--root"])
    except SystemExit as exc:
        if exc.code != 2:
            return f"missing --root value: want exit 2, got {exc.code}"
        print("self-test PASS: missing --root value exited with code 2")
        return None
    return "missing --root value: did not exit"


def self_test() -> int:
    """Execute planted tests for status checking and index rendering."""
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for case in (_case_missing_row, _case_statuses, _case_bad_header, _case_write_checks, _case_argparse):
            failure = case(Path(tmp))
            if failure:
                print(f"self-test FAIL {failure}", file=sys.stderr)
                failed = 1
    print("self-test %s" % ("FAILED" if failed else "passed"))
    return failed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for check-status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run self-test suite")
    parser.add_argument(
        "--write",
        action="store_true",
        help="render the index and then check every status",
    )
    parser.add_argument("--root", type=Path, default=Path("docs/adr"), help="root directory of ADRs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for checking or writing ADR status."""
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.root.is_dir():
        print(f"check-status: {args.root} is not a directory", file=sys.stderr)
        return 2

    if args.write:
        try:
            updated = write_readme(args.root)
            if updated:
                print(f"updated {args.root / 'README.md'}")
        except ValueError as exc:
            print(f"check-status: {exc}", file=sys.stderr)
            return 1

    return report(scan(args.root))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
