#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check co-change invariants across commits and pull requests.

Enforces documentation and report co-change rules from tools/AGENTS.md:
- tester-usage-guide: tester stage/fixture changes require docs/usage/tester.md
- tester-report-schema: report schema changes require pelorus_tester_report.py
  and docs/development/tester.md

Exemptions can be declared per commit using the trailer:
    Co-change-exempt: <rule_id> <reason>

Usage:
    python3 -I scripts/check-cochange.py
    python3 -I scripts/check-cochange.py --base origin/master --head HEAD
    python3 -I scripts/check-cochange.py --require-base --base origin/main
    python3 -I scripts/check-cochange.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys

DESCRIPTION = "Check co-change invariants across commits and pull requests."

Rule = tuple[str, tuple[str, ...], tuple[str, ...]]

RULES: tuple[Rule, ...] = (
    (
        "tester-usage-guide",
        (
            "tools/tester/pelorus_tester_stages.py",
            "tools/tester/pelorus_tester_fixtures.py",
            "tools/tester/fixtures.lock.json",
        ),
        ("docs/usage/tester.md",),
    ),
    (
        "tester-report-schema",
        ("tools/tester/report.schema.json",),
        (
            "tools/tester/pelorus_tester_report.py",
            "docs/development/tester.md",
        ),
    ),
)

SelfTestCase = tuple[str, set[str], list[str], int]

SELF_TEST_CASES: list[SelfTestCase] = [
    (
        "stages_changed_guide_changed",
        {"tools/tester/pelorus_tester_stages.py", "docs/usage/tester.md"},
        [],
        0,
    ),
    (
        "stages_changed_guide_not",
        {"tools/tester/pelorus_tester_stages.py"},
        [],
        1,
    ),
    (
        "fixtures_lock_changed_alone",
        {"tools/tester/fixtures.lock.json"},
        [],
        1,
    ),
    (
        "stages_changed_valid_trailer",
        {"tools/tester/pelorus_tester_stages.py"},
        ["refactor: stages update\n\nCo-change-exempt: tester-usage-guide refactor only"],
        0,
    ),
    (
        "trailer_with_empty_reason",
        {"tools/tester/pelorus_tester_stages.py"},
        ["refactor: stages update\n\nCo-change-exempt: tester-usage-guide"],
        1,
    ),
    (
        "trailer_naming_unknown_rule",
        {"tools/tester/pelorus_tester_stages.py"},
        ["refactor: stages update\n\nCo-change-exempt: unknown-rule refactor only"],
        2,
    ),
    (
        "unknown_rule_trailer_alone",
        set(),
        ["chore: something\n\nCo-change-exempt: unknown-rule refactor only"],
        1,
    ),
    (
        "schema_changed_program_and_dev_guide_changed",
        {
            "tools/tester/report.schema.json",
            "tools/tester/pelorus_tester_report.py",
            "docs/development/tester.md",
        },
        [],
        0,
    ),
    (
        "schema_changed_only_program",
        {
            "tools/tester/report.schema.json",
            "tools/tester/pelorus_tester_report.py",
        },
        [],
        1,
    ),
    (
        "schema_changed_neither",
        {"tools/tester/report.schema.json"},
        [],
        2,
    ),
    (
        "unrelated_file_only",
        {"docs/principles.md"},
        [],
        0,
    ),
    (
        "trailer_text_mid_line",
        {"tools/tester/pelorus_tester_stages.py"},
        ["note: Co-change-exempt: tester-usage-guide refactor only"],
        1,
    ),
]


def parse_trailers(
    messages: list[str],
    known_rules: set[str],
) -> tuple[set[str], list[str]]:
    """Parse co-change exemption trailers and unknown-rule violations from messages."""
    exemptions: set[str] = set()
    violations: list[str] = []
    for message in messages[:10000]:
        for line in message.splitlines()[:5000]:
            match = re.match(r"^Co-change-exempt:\s+(\S+)(?:\s+(.*))?$", line)
            if not match:
                continue
            rule_id = match.group(1)
            reason = (match.group(2) or "").strip()
            if rule_id not in known_rules:
                violations.append(f"unknown co-change rule {rule_id} in trailer")
            elif reason:
                exemptions.add(rule_id)
    return exemptions, violations


def evaluate(changed: set[str], messages: list[str]) -> list[str]:
    """Evaluate changed files and commit messages against co-change rules."""
    known_rules = {rule[0] for rule in RULES}
    exemptions, violations = parse_trailers(messages, known_rules)

    for rule_id, triggers, required in RULES:
        if rule_id in exemptions:
            continue
        matched_trigger = next((t for t in triggers if t in changed), None)
        if matched_trigger is not None:
            for req in required:
                if req not in changed:
                    violations.append(
                        f"{rule_id}: {matched_trigger} changed without {req}"
                    )

    return violations


def check_base_ref(base: str, repo_root: pathlib.Path) -> bool:
    """Check whether the git base ref exists as a commit."""
    proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc.returncode == 0


def get_changed_files(base: str, head: str, repo_root: pathlib.Path) -> set[str] | None:
    """Retrieve set of changed files between base and head via git diff."""
    proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        ["git", "diff", "--name-only", "-z", f"{base}...{head}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        return None
    return {f for f in proc.stdout.split("\0") if f}


def get_commit_messages(base: str, head: str, repo_root: pathlib.Path) -> list[str] | None:
    """Retrieve commit messages between base and head via git log."""
    proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        ["git", "log", "--format=%B%x00", f"{base}..{head}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        return None
    return [m for m in proc.stdout.split("\0") if m]


def run_self_test() -> int:
    """Run self-test suite covering planted test cases."""
    failures = 0
    for name, changed, messages, expected in SELF_TEST_CASES:
        violations = evaluate(changed, messages)
        if len(violations) != expected:
            print(
                f"FAIL self-test '{name}': expected {expected} violations, got {len(violations)}: {violations}",
                file=sys.stderr,
            )
            failures += 1

    if failures:
        print(f"co-change self-test: {failures} failures", file=sys.stderr)
        return 1

    print(f"co-change self-test: {len(SELF_TEST_CASES)} cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run co-change check or self-test."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--base",
        default="origin/master",
        help="Base commit or reference for comparison (default: origin/master)",
    )
    parser.add_argument(
        "--head",
        default="HEAD",
        help="Head commit or reference for comparison (default: HEAD)",
    )
    parser.add_argument(
        "--require-base",
        action="store_true",
        help="Exit with error code 2 if base ref is missing",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test on planted cases and exit",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo_root = pathlib.Path(__file__).resolve().parent.parent

    if shutil.which("git") is None:
        print("co-change: git not on PATH", file=sys.stderr)
        return 2

    if not check_base_ref(args.base, repo_root):
        if args.require_base:
            print(f"co-change: required base ref '{args.base}' not found", file=sys.stderr)
            return 2
        print(f"co-change: skipped: base ref {args.base} not found")
        return 0

    changed = get_changed_files(args.base, args.head, repo_root)
    if changed is None:
        return 2

    messages = get_commit_messages(args.base, args.head, repo_root)
    if messages is None:
        return 2

    violations = evaluate(changed, messages)
    if violations:
        for violation in violations:
            print(violation)
        print("exempt a rule with a commit trailer: Co-change-exempt: <rule_id> <reason>")
        return 1

    print(f"co-change: {len(changed)} files checked, 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
