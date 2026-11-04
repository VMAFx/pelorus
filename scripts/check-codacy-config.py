#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check .codacy.yml exclusion entries against exception and category rules.

Rules enforced:
  1. Text starts with leading '---\\n'.
  2. Exclusion item path contains no glob characters (* ? [ {).
  3. Exclusion item path names a regular file in the tree (CI: a tracked file).
  4. Preceding comment block contains an exception declaration with valid ISO dates.
  5. Category interval limits (expires - entered):
     - category A: any interval (expires >= entered)
     - category B: interval <= 3 days
     - category C: interval <= 10 days
     - category D: interval <= 120 days
  6. Today <= expires (not expired).

Exit status:
  - 0: all exclusions conform
  - 1: one or more rule violations found
  - 2: usage or file read error
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date
from pathlib import Path
import re
import sys

MAX_LINES = 10000
MAX_COMMENTS = 100
MAX_FINDINGS = 1000
MAX_CASES = 100
FIXED_SELF_TEST_TODAY = "2026-11-04"
FIXED_TRACKED_SET = frozenset({"a.py", "b.py"})
GLOB_CHARS = frozenset("*?[{")
INTERVAL_LIMITS = {
    "B": 3,
    "C": 10,
    "D": 120,
}
EXCEPTION_PATTERN = re.compile(
    r"exception: category ([ABCD]), entered (\d{4}-\d{2}-\d{2}), expires (\d{4}-\d{2}-\d{2})"
)

VALID_TEMPLATE = """---
engines:
  bandit:
    exclude_paths:
      # exception: category B, entered 2026-11-04, expires 2026-11-07
      - "a.py"
"""

SELF_TEST_CASES: list[tuple[str, str, str | None, date | None]] = [
    ("valid entry", VALID_TEMPLATE, None, None),
    ("missing ---", VALID_TEMPLATE.removeprefix("---\n"), "---", None),
    (
        'glob "tools/*.py"',
        VALID_TEMPLATE.replace('"a.py"', '"tools/*.py"'),
        "glob",
        None,
    ),
    (
        'untracked "c.py"',
        VALID_TEMPLATE.replace('"a.py"', '"c.py"'),
        "untracked",
        None,
    ),
    (
        "missing exception line",
        VALID_TEMPLATE.replace(
            "# exception: category B, entered 2026-11-04, expires 2026-11-07",
            "# note without exception",
        ),
        "missing exception",
        None,
    ),
    (
        "category B interval 4 days",
        VALID_TEMPLATE.replace("expires 2026-11-07", "expires 2026-11-08"),
        "category B",
        None,
    ),
    (
        "expired (today 2026-11-08 vs expires 2026-11-07)",
        VALID_TEMPLATE,
        "expired",
        date(2026, 11, 8),
    ),
    (
        "flow list",
        VALID_TEMPLATE.replace('    exclude_paths:\n', '    exclude_paths: ["c.py"]\n'),
        "unrecognised line",
        None,
    ),
    (
        "top-level exclude_paths",
        VALID_TEMPLATE + 'exclude_paths:\n  - "c.py"\n',
        "top-level exclude_paths",
        None,
    ),
    (
        "flow engines mapping",
        '---\nengines: {bandit: {exclude_paths: ["c.py"]}}\n',
        "block mapping only",
        None,
    ),
    (
        "file over MAX_LINES",
        VALID_TEMPLATE + "#\n" * MAX_LINES,
        "longer than",
        None,
    ),
    (
        'bad date "2026-13-01"',
        VALID_TEMPLATE.replace("expires 2026-11-07", "expires 2026-13-01"),
        "2026-13-01",
        None,
    ),
]


def strip_quotes(val: str) -> str:
    """Strip matching leading and trailing single or double quotes."""
    s = val.strip()
    if len(s) >= 2 and (
        (s.startswith('"') and s.endswith('"'))
        or (s.startswith("'") and s.endswith("'"))
    ):
        return s[1:-1]
    return s


def parse_iso_date(val: str) -> date | None:
    """Parse YYYY-MM-DD ISO date string, or return None."""
    s = strip_quotes(val)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def is_tracked(
    path: str,
    root: Path,
    tracked_fn: Callable[[str], bool] | None = None,
) -> bool:
    """Check that path names a regular file in the tree, or ask an injected callable.

    CI checks out tracked files only, so there a path git does not track is missing.
    No git call: the check runs no subprocess.
    """
    if tracked_fn is not None:
        return tracked_fn(path)
    target = (root / path).resolve()
    return target.is_relative_to(root.resolve()) and target.is_file()


def check_item_path(
    path: str,
    line_idx: int,
    root: Path,
    tracked_fn: Callable[[str], bool] | None,
) -> list[str]:
    """Check item path for glob characters and git tracking."""
    if any(ch in path for ch in GLOB_CHARS):
        return [f".codacy.yml:{line_idx}: item path must not contain glob characters: {path}"]
    if not is_tracked(path, root=root, tracked_fn=tracked_fn):
        return [f".codacy.yml:{line_idx}: item path is untracked or missing: {path}"]
    return []


def check_exception_block(
    comments: list[tuple[int, str]],
    item_line: int,
    today: date,
) -> list[str]:
    """Validate exception declaration comments directly preceding an item."""
    matching: tuple[int, str, str, str] | None = None
    for c_idx, c_text in comments[:MAX_COMMENTS]:
        m = EXCEPTION_PATTERN.search(c_text)
        if m:
            matching = (c_idx, m.group(1), m.group(2), m.group(3))
            break

    if matching is None:
        return [f".codacy.yml:{item_line}: missing exception line in preceding comment block"]

    exc_line, cat, entered_raw, expires_raw = matching
    entered = parse_iso_date(entered_raw)
    expires = parse_iso_date(expires_raw)
    findings: list[str] = []

    if entered is None:
        findings.append(f".codacy.yml:{exc_line}: invalid ISO date: {entered_raw!r}")
    if expires is None:
        findings.append(f".codacy.yml:{exc_line}: invalid ISO date: {expires_raw!r}")
    if entered is None or expires is None:
        return findings

    if expires < entered:
        findings.append(f".codacy.yml:{exc_line}: expires ({expires}) is before entered ({entered})")
    else:
        limit = INTERVAL_LIMITS.get(cat)
        interval = (expires - entered).days
        if limit is not None and interval > limit:
            findings.append(
                f".codacy.yml:{exc_line}: category {cat} interval exceeds {limit} days: {interval}d > {limit}d"
            )

    if today > expires:
        findings.append(f".codacy.yml:{exc_line}: exception expired: {expires} is before {today}")

    return findings


def check_exclude_line(
    line: str,
    line_idx: int,
    comments: list[tuple[int, str]],
    today: date,
    root: Path,
    tracked_fn: Callable[[str], bool] | None,
) -> list[str]:
    """Check one line inside an exclude_paths block; collects comments in place."""
    if line.startswith("      #"):
        if len(comments) < MAX_COMMENTS:
            comments.append((line_idx, line))
        return []
    findings: list[str] = []
    if line.startswith("      -"):
        path = strip_quotes(line.strip().removeprefix("-").strip())
        findings.extend(check_item_path(path, line_idx, root, tracked_fn))
        findings.extend(check_exception_block(comments, line_idx, today))
    elif line.strip():
        findings.append(f".codacy.yml:{line_idx}: unrecognised line: {line.strip()}")
    comments.clear()
    return findings


def check_text(
    text: str,
    today: date,
    root: Path,
    tracked_fn: Callable[[str], bool] | None = None,
) -> list[str]:
    """Parse and check .codacy.yml text against exclusion and exception rules."""
    findings: list[str] = []
    if not text.startswith("---\n"):
        findings.append(".codacy.yml:1: file must start with '---\\n'")

    all_lines = text.splitlines()
    if len(all_lines) > MAX_LINES:
        findings.append(f".codacy.yml:{MAX_LINES + 1}: file longer than {MAX_LINES} lines; not checked")
        return findings
    lines = all_lines
    in_engines = False
    in_exclude_paths = False
    comments: list[tuple[int, str]] = []

    for line_idx, line in enumerate(lines, start=1):
        if len(findings) >= MAX_FINDINGS:
            break
        if line.startswith("exclude_paths:"):
            findings.append(f".codacy.yml:{line_idx}: top-level exclude_paths not allowed; exclude per engine")
            continue
        if not in_engines:
            if line.rstrip() == "engines:":
                in_engines = True
            elif line.startswith("engines:"):
                findings.append(f".codacy.yml:{line_idx}: unrecognised line (block mapping only): {line.strip()}")
            continue

        if line and not line.startswith(" ") and not line.startswith("\t") and not line.startswith("#"):
            in_engines = in_exclude_paths = False
            comments.clear()
            continue

        if line.startswith("    exclude_paths:"):
            if line.rstrip() != "    exclude_paths:":
                findings.append(f".codacy.yml:{line_idx}: unrecognised line (block list only): {line.strip()}")
            in_exclude_paths = True
            comments.clear()
            continue

        if (line.startswith("  ") and not line.startswith("   ")) or (
            line.startswith("    ") and not line.startswith("     ")
        ):
            in_exclude_paths = False
            comments.clear()
            continue

        if not in_exclude_paths:
            continue

        findings.extend(check_exclude_line(line, line_idx, comments, today, root, tracked_fn))

    return findings[:MAX_FINDINGS]


def is_in_fixed_set(path: str) -> bool:
    """Predicate for fixed tracked set in self-test."""
    return path in FIXED_TRACKED_SET


def self_test(root_dir: Path) -> int:
    """Run in-memory self-test cases against fixed date and tracked set."""
    ref_date = date.fromisoformat(FIXED_SELF_TEST_TODAY)

    for name, text, expected_sub, case_today in SELF_TEST_CASES[:MAX_CASES]:
        t = case_today if case_today is not None else ref_date
        findings = check_text(text, t, root=root_dir, tracked_fn=is_in_fixed_set)
        if expected_sub is None:
            if findings:
                print(f"self-test FAIL: {name}: unexpected findings {findings}")
                return 1
        elif not any(expected_sub in f for f in findings):
            print(f"self-test FAIL: {name}: expected {expected_sub!r} in {findings}")
            return 1

    print("self-test: ok")
    return 0


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Verify .codacy.yml exclusion entries against exception and category rules."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root directory containing .codacy.yml",
    )
    parser.add_argument(
        "--today",
        type=str,
        default=None,
        help="Reference date YYYY-MM-DD (default: system current date)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-tests against in-memory cases",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.root)

    if args.today is not None:
        ref_date = parse_iso_date(args.today)
        if ref_date is None:
            sys.stderr.write(f"--today: invalid ISO date (YYYY-MM-DD): {args.today!r}\n")
            return 2
    else:
        ref_date = date.today()

    target = args.root / ".codacy.yml"
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        sys.stderr.write(f"{target}: {err}\n")
        return 2

    findings = check_text(text, ref_date, root=args.root)
    for finding in findings[:MAX_FINDINGS]:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
