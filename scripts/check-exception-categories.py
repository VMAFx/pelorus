#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check .standards.yaml exception entries for category, interval and cap rules.

Fleet rules require every exception entry to name its category in its reason:
  - category A: interval stated in entry (no upper bound)
  - category B: interval <= 3 days
  - category C: interval <= 10 days
  - category D: interval <= 120 days
  - engine maximum: every expiry <= 90 days from reference date (unless category A)
  - expires: valid ISO date (YYYY-MM-DD)

Exit status: 0 if all exceptions conform; 1 on any rule violation or error.

    python3 -I scripts/check-exception-categories.py
    python3 -I scripts/check-exception-categories.py --today 2026-10-18
    python3 -I scripts/check-exception-categories.py --self-test
"""

import argparse
from datetime import date, timedelta
from pathlib import Path
import re
import sys

MAX_LINES = 10000
MAX_ENTRIES = 1000
MAX_FINDINGS = 1000
FIXED_SELF_TEST_TODAY = "2026-10-18"


def strip_quotes(val: str) -> str:
    """Strip matching leading and trailing single or double quotes."""
    s = val.strip()
    if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or
                        (s.startswith("'") and s.endswith("'"))):
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


def parse_exceptions(text: str) -> list[dict[str, str]]:
    """Parse exceptions block from .standards.yaml text without YAML library."""
    lines = text.splitlines()[:MAX_LINES]
    in_exceptions = False
    entries: list[dict[str, str]] = []
    curr: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if stripped == "exceptions:":
            in_exceptions = True
            continue
        if not in_exceptions:
            continue
        if line and not line.startswith(" ") and not line.startswith("\t"):
            break
        if stripped.startswith("- rule:"):
            if curr:
                entries.append(curr)
                if len(entries) >= MAX_ENTRIES:
                    break
            curr = {"rule": strip_quotes(stripped.split(":", 1)[1])}
        elif curr and ":" in stripped:
            key, val = stripped.split(":", 1)
            curr[key.strip()] = strip_quotes(val)

    if curr and len(entries) < MAX_ENTRIES:
        entries.append(curr)
    return entries


def check_entry(entry: dict[str, str], today: date) -> list[tuple[str, str]]:
    """Check a single exception entry against category, interval, and cap rules."""
    target = entry.get("path") or entry.get("tool") or entry.get("rule") or "<unknown>"
    findings: list[tuple[str, str]] = []
    reason = entry.get("reason", "")
    expires_raw = entry.get("expires", "")

    cat_match = re.search(r"category ([ABCD])\b", reason)
    cat = cat_match.group(1) if cat_match else None
    if not cat:
        findings.append((target, f"reason lacks category [ABCD]: {reason!r}"))

    exp_date = parse_iso_date(expires_raw)
    if exp_date is None:
        findings.append((target, f"expires is not a valid ISO date: {expires_raw!r}"))
        return findings

    delta = (exp_date - today).days
    if cat == "B" and delta > 3:
        findings.append((target, f"category B expiry exceeds 3 days: {expires_raw} ({delta}d > 3d)"))
    elif cat == "C" and delta > 10:
        findings.append((target, f"category C expiry exceeds 10 days: {expires_raw} ({delta}d > 10d)"))
    elif cat == "D" and delta > 120:
        findings.append((target, f"category D expiry exceeds 120 days: {expires_raw} ({delta}d > 120d)"))

    if cat != "A" and delta > 90:
        findings.append((target, f"expiry exceeds 90-day engine cap: {expires_raw} ({delta}d > 90d)"))

    return findings


def check_text(text: str, today: date) -> list[tuple[str, str]]:
    """Parse and check all exception entries in YAML text."""
    entries = parse_exceptions(text)
    if not entries:
        return [(".standards.yaml", "no exceptions found in .standards.yaml")]
    findings: list[tuple[str, str]] = []
    for entry in entries[:MAX_ENTRIES]:
        for finding in check_entry(entry, today):
            findings.append(finding)
            if len(findings) >= MAX_FINDINGS:
                return findings
    return findings


def self_test(root_dir: Path) -> int:
    """Run self-test on real .standards.yaml and in-memory mutations."""
    standards_path = root_dir / ".standards.yaml"
    try:
        real_text = standards_path.read_text(encoding="utf-8")
    except OSError as err:
        print(f"self-test FAIL: cannot read {standards_path}: {err}")
        return 1

    ref_date = date.fromisoformat(FIXED_SELF_TEST_TODAY)
    failures = 0

    real_findings = check_text(real_text, ref_date)
    if real_findings:
        print(f"self-test FAIL: real file failed with fixed date: {real_findings!r}")
        failures += 1

    # Mutation 1: drop "category D" from one reason
    mut1 = real_text.replace("category D, capped at the engine maximum of 90 days: ", "", 1)
    if not check_text(mut1, ref_date):
        print("self-test FAIL: drop category D was not rejected")
        failures += 1

    # Mutation 2: category B with expiry 30 days out
    exp_30 = (ref_date + timedelta(days=30)).isoformat()
    mut2 = real_text.replace("category D", "category B", 1).replace(
        'expires: "2027-01-13"', f'expires: "{exp_30}"', 1
    )
    if not check_text(mut2, ref_date):
        print("self-test FAIL: category B with 30-day expiry was not rejected")
        failures += 1

    # Mutation 3: expiry 2027-06-01 with category D
    mut3 = real_text.replace('expires: "2027-01-13"', 'expires: "2027-06-01"', 1)
    if not check_text(mut3, ref_date):
        print("self-test FAIL: category D with expiry 2027-06-01 was not rejected")
        failures += 1

    # Mutation 4: expires "soon"
    mut4 = real_text.replace('expires: "2027-01-13"', 'expires: "soon"', 1)
    if not check_text(mut4, ref_date):
        print("self-test FAIL: expires soon was not rejected")
        failures += 1

    print(f"self-test: 5 cases, {failures} failures")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Verify category declarations and expiry dates in .standards.yaml."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root directory containing .standards.yaml",
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
        help="Run self-tests against real file and in-memory mutations",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test(args.root)

    ref_date = parse_iso_date(args.today) if args.today else date.today()
    if ref_date is None:
        print(f"--today: invalid ISO date (YYYY-MM-DD): {args.today!r}")
        return 1

    standards_path = args.root / ".standards.yaml"
    try:
        text = standards_path.read_text(encoding="utf-8")
    except OSError as err:
        print(f"{standards_path}: {err}")
        return 1

    findings = check_text(text, ref_date)
    for target, msg in findings[:MAX_FINDINGS]:
        print(f"{target}: {msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
