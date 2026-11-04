#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Query and validate exception entries declared in .standards.yaml.

Reuses the parser and date validation logic from scripts/check-exception-categories.py
via dynamic import to preserve a single implementation (HISS-19).
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sys
from typing import Callable, TextIO


def _load_parser() -> tuple[Callable[[str], list[dict[str, str]]], Callable[[str], date | None]]:
    """Dynamically load parse_exceptions and parse_iso_date from sibling script."""
    sibling = Path(__file__).resolve().parent / "check-exception-categories.py"
    spec = importlib.util.spec_from_file_location("check_exception_categories", sibling)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {sibling}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_exceptions, mod.parse_iso_date


_PARSE_EXCEPTIONS, _PARSE_ISO_DATE = _load_parser()
parse_exceptions = _PARSE_EXCEPTIONS
parse_iso_date = _PARSE_ISO_DATE


def load_entries(root: Path) -> list[dict[str, str]]:
    """Read exception entries from root/.standards.yaml (missing -> [])."""
    standards_path = root / ".standards.yaml"
    try:
        text = standards_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _PARSE_EXCEPTIONS(text)


def find_live_exception(
    entries: list[dict[str, str]],
    rule: str,
    rel_path: str,
    today: date,
) -> dict[str, str] | None:
    """Return the first matching live exception entry, or None."""
    target_path = Path(rel_path).as_posix()
    for entry in entries:
        if entry.get("rule") != rule:
            continue
        entry_path = entry.get("path")
        if entry_path is None:
            continue
        if Path(entry_path).as_posix() != target_path:
            continue
        exp_raw = entry.get("expires", "")
        exp_date = _PARSE_ISO_DATE(exp_raw)
        if exp_date is not None and exp_date >= today:
            return entry
    return None


def live_exception(
    entries: list[dict[str, str]],
    rule: str,
    rel_path: str,
    today: date,
) -> bool:
    """True when an entry has that rule and that path and expires >= today."""
    return find_live_exception(entries, rule, rel_path, today) is not None


def filter_excused(
    findings: list[str],
    entries: list[dict[str, str]],
    rule: str,
    today: date,
    out: TextIO = sys.stdout,
) -> list[str]:
    """Drop the findings ("<rel>:<line>: ...") of each file a live entry for rule excuses."""
    by_file: dict[str, list[str]] = {}
    for finding in findings:
        by_file.setdefault(finding.split(":", 1)[0], []).append(finding)
    retained: list[str] = []
    for rel, file_findings in by_file.items():
        match = find_live_exception(entries, rule, rel, today)
        if match is None:
            retained.extend(file_findings)
            continue
        print(f"{rel}: excused by .standards.yaml exception {rule} until {match.get('expires', '')}", file=out)
    return retained


def self_test(root_dir: Path) -> int:
    """Verify standards exception parsing and live_exception querying."""
    failures = 0
    ref_today = date(2026, 11, 4)
    yesterday = (ref_today - timedelta(days=1)).isoformat()
    future = (ref_today + timedelta(days=3)).isoformat()
    fixture_path = "tools/example.py"

    test_entries = [
        {"rule": "HISS-10", "path": fixture_path, "expires": future},
        {"rule": "HISS-07", "path": "other/file.py", "expires": yesterday},
        {"rule": "HISS-08", "path": fixture_path, "expires": "invalid-date"},
    ]

    # Matching rule, path, future expiry
    if not live_exception(test_entries, "HISS-10", fixture_path, ref_today):
        print("FAIL self-test: matching live entry not recognized", file=sys.stderr)
        failures += 1

    # Matching rule, path, expired
    if live_exception(test_entries, "HISS-07", "other/file.py", ref_today):
        print("FAIL self-test: expired entry was accepted as live", file=sys.stderr)
        failures += 1

    # Wrong rule
    if live_exception(test_entries, "HISS-07", fixture_path, ref_today):
        print("FAIL self-test: mismatched rule was accepted", file=sys.stderr)
        failures += 1

    # Wrong path
    if live_exception(test_entries, "HISS-10", "nonexistent.py", ref_today):
        print("FAIL self-test: mismatched path was accepted", file=sys.stderr)
        failures += 1

    # Invalid date
    if live_exception(test_entries, "HISS-08", fixture_path, ref_today):
        print("FAIL self-test: invalid date entry was accepted", file=sys.stderr)
        failures += 1

    # load_entries on non-existent path
    if load_entries(root_dir / "nonexistent-dir-for-test"):
        print("FAIL self-test: non-existent directory returned entries", file=sys.stderr)
        failures += 1

    # load_entries on real root
    real_entries = load_entries(root_dir)
    if not real_entries:
        print("FAIL self-test: real .standards.yaml returned no entries", file=sys.stderr)
        failures += 1

    if failures:
        print(f"standards_exceptions self-test: {failures} failures", file=sys.stderr)
        return 1

    print("standards_exceptions self-test: 7 cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root directory containing .standards.yaml",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-tests against in-memory fixture entries",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.root)

    entries = load_entries(args.root)
    print(f"Loaded {len(entries)} exception entry/entries from {args.root / '.standards.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
