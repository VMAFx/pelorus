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
Commit = tuple[set[str], list[str]]
SelfTestCase = tuple[str, set[str], list[Commit], int]
TrailerParserCase = tuple[str, str, list[str]]

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
        [
            (
                {"tools/tester/pelorus_tester_stages.py"},
                ["tester-usage-guide refactor only"],
            )
        ],
        0,
    ),
    (
        "trailer_with_empty_reason",
        {"tools/tester/pelorus_tester_stages.py"},
        [
            (
                {"tools/tester/pelorus_tester_stages.py"},
                ["tester-usage-guide"],
            )
        ],
        2,
    ),
    (
        "trailer_naming_unknown_rule",
        {"tools/tester/pelorus_tester_stages.py"},
        [
            (
                {"tools/tester/pelorus_tester_stages.py"},
                ["unknown-rule refactor only"],
            )
        ],
        2,
    ),
    (
        "unknown_rule_trailer_alone",
        set(),
        [(set(), ["unknown-rule refactor only"])],
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
        [({"tools/tester/pelorus_tester_stages.py"}, [])],
        1,
    ),
    (
        "empty_reason_guide_also_changed",
        {"tools/tester/pelorus_tester_stages.py", "docs/usage/tester.md"},
        [
            (
                {
                    "tools/tester/pelorus_tester_stages.py",
                    "docs/usage/tester.md",
                },
                ["tester-usage-guide"],
            )
        ],
        1,
    ),
    (
        "empty_reason_no_trigger_changed",
        set(),
        [(set(), ["tester-usage-guide"])],
        1,
    ),
    (
        "two_commits_one_missing_trailer",
        {"tools/tester/pelorus_tester_stages.py"},
        [
            (
                {"tools/tester/pelorus_tester_stages.py"},
                ["tester-usage-guide refactor only"],
            ),
            ({"tools/tester/pelorus_tester_stages.py"}, []),
        ],
        1,
    ),
    (
        "two_commits_both_valid_trailer",
        {"tools/tester/pelorus_tester_stages.py"},
        [
            (
                {"tools/tester/pelorus_tester_stages.py"},
                ["tester-usage-guide refactor A"],
            ),
            (
                {"tools/tester/pelorus_tester_stages.py"},
                ["tester-usage-guide refactor B"],
            ),
        ],
        0,
    ),
    (
        "trailer_in_non_trigger_commit",
        {"tools/tester/pelorus_tester_stages.py"},
        [
            (
                {"docs/principles.md"},
                ["tester-usage-guide refactor only"],
            ),
            ({"tools/tester/pelorus_tester_stages.py"}, []),
        ],
        1,
    ),
]

TRAILER_PARSER_CASES: list[TrailerParserCase] = [
    (
        "trailer_in_final_block",
        "feat: test subject\n\nSome body text.\n\nCo-change-exempt: tester-usage-guide refactor only\n",
        ["tester-usage-guide refactor only"],
    ),
    (
        "same_line_in_body_paragraph",
        "feat: test subject\n\nCo-change-exempt: tester-usage-guide refactor only\n\nFollowed by non-trailer paragraph.\n",
        [],
    ),
    (
        "mid_line_text",
        "feat: test subject\n\nSome note: Co-change-exempt: tester-usage-guide refactor only\n",
        [],
    ),
]


def run_git(
    args: list[str],
    repo_root: pathlib.Path,
    stdin: str | None = None,
    timeout: float = 60.0,
    quiet_rc: int | None = None,
) -> str | None:
    """Execute a git command and return its stdout, or None on failure."""
    try:
        proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
            input=stdin,
        )
    except subprocess.TimeoutExpired:
        print(
            f"co-change: git {args[0]} timed out after {timeout} s",
            file=sys.stderr,
        )
        return None
    except UnicodeDecodeError as exc:
        print(
            f"co-change: git {args[0]} output is not UTF-8: {exc}",
            file=sys.stderr,
        )
        return None
    if proc.returncode != 0:
        if quiet_rc is not None and proc.returncode == quiet_rc:
            return ""
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        return None
    return proc.stdout


def check_base_ref(base: str, repo_root: pathlib.Path) -> bool | None:
    """Return True if base ref exists as a commit, False if missing, or None on tool error."""
    out = run_git(
        ["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        repo_root,
        quiet_rc=1,
    )
    if out is None:
        return None
    if out == "":
        return False
    return True


def get_changed_files(
    base: str, head: str, repo_root: pathlib.Path
) -> set[str] | None:
    """Retrieve set of changed files between base and head via git diff."""
    stdout = run_git(
        ["diff", "--name-only", "-z", "--no-renames", f"{base}...{head}"],
        repo_root,
    )
    if stdout is None:
        return None
    return {f for f in stdout.split("\0") if f}


def parse_exempt_trailers(
    message: str, repo_root: pathlib.Path
) -> list[str] | None:
    """Return the Co-change-exempt values from the message's trailer block, or None if git fails."""
    stdout = run_git(["interpret-trailers", "--parse"], repo_root, stdin=message)
    if stdout is None:
        return None
    values: list[str] = []
    for line in stdout.splitlines()[:5000]:
        match = re.match(r"^Co-change-exempt:\s*(.*)$", line)
        if match:
            values.append(match.group(1))
    return values


def get_commits(
    base: str, head: str, repo_root: pathlib.Path
) -> list[Commit] | None:
    """Retrieve commits between base and head with touched files and trailers."""
    rev_list_stdout = run_git(
        ["rev-list", "--no-merges", f"{base}..{head}"], repo_root
    )
    if rev_list_stdout is None:
        return None
    shas = [sha.strip() for sha in rev_list_stdout.splitlines() if sha.strip()]
    commits: list[Commit] = []
    for sha in shas[:2000]:
        diff_stdout = run_git(
            [
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                "--no-renames",
                sha,
            ],
            repo_root,
        )
        if diff_stdout is None:
            return None
        files = {f for f in diff_stdout.split("\0") if f}

        msg_stdout = run_git(["log", "-1", "--format=%B", sha], repo_root)
        if msg_stdout is None:
            return None
        values = parse_exempt_trailers(msg_stdout, repo_root)
        if values is None:
            return None
        commits.append((files, values))
    return commits


def has_valid_exemption(
    trailers: list[str], rule_id: str, known_rules: set[str]
) -> bool:
    """Check whether trailers contain a valid exemption for a rule."""
    for val in trailers[:1000]:
        parts = val.strip().split(maxsplit=1)
        if (
            len(parts) > 1
            and parts[0] == rule_id
            and parts[0] in known_rules
            and parts[1].strip()
        ):
            return True
    return False


def evaluate(changed: set[str], commits: list[Commit]) -> list[str]:
    """Evaluate changed files and commits against co-change rules."""
    known_rules = {rule[0] for rule in RULES}
    violations: list[str] = []

    for _commit_files, trailers in commits[:2000]:
        for val in trailers[:1000]:
            parts = val.strip().split(maxsplit=1)
            rule_id = parts[0] if parts else ""
            reason = parts[1].strip() if len(parts) > 1 else ""
            if rule_id not in known_rules:
                violations.append(f"unknown co-change rule {rule_id} in trailer")
            elif not reason:
                violations.append(f"co-change trailer for {rule_id} has no reason")

    for rule_id, triggers, required in RULES[:100]:
        matched_trigger = next((t for t in triggers if t in changed), None)
        if matched_trigger is None:
            continue
        missing = [req for req in required[:100] if req not in changed]
        if not missing:
            continue

        touching_commits = [
            c for c in commits[:2000] if any(t in c[0] for t in triggers)
        ]
        is_exempt = bool(touching_commits) and all(
            has_valid_exemption(c[1], rule_id, known_rules)
            for c in touching_commits[:2000]
        )
        if not is_exempt:
            for req in missing[:100]:
                violations.append(
                    f"{rule_id}: {matched_trigger} changed without {req}"
                )

    return violations


def self_test_run_git(repo_root: pathlib.Path) -> int:
    """Run self-test cases verifying run_git and base ref checking."""
    failures = 0
    if (
        run_git(
            ["-c", "alias.pelsleep=!sleep 5", "pelsleep"],
            repo_root,
            timeout=0.5,
        )
        is not None
    ):
        print("FAIL self-test run_git 'timeout'", file=sys.stderr)
        failures += 1

    if (
        run_git(
            ["-c", "alias.pelbytes=!printf '\\377\\376'", "pelbytes"],
            repo_root,
        )
        is not None
    ):
        print("FAIL self-test run_git 'decode'", file=sys.stderr)
        failures += 1

    if (
        run_git(
            ["-c", "alias.pelutf8=!printf '\\303\\244'", "pelutf8"],
            repo_root,
        )
        != "\u00e4"
    ):
        print("FAIL self-test run_git 'utf8'", file=sys.stderr)
        failures += 1

    if check_base_ref("refs/heads/pelorus-no-such-ref-xyz", repo_root) is not False:
        print("FAIL self-test check_base_ref 'missing'", file=sys.stderr)
        failures += 1

    if check_base_ref("HEAD", repo_root) is not True:
        print("FAIL self-test check_base_ref 'HEAD'", file=sys.stderr)
        failures += 1

    return failures


def run_self_test(repo_root: pathlib.Path) -> int:
    """Run self-test suite covering evaluate logic and trailer parsing."""
    failures = 0
    for name, changed, commits, expected in SELF_TEST_CASES[:100]:
        violations = evaluate(changed, commits)
        if len(violations) != expected:
            print(
                f"FAIL self-test evaluate '{name}': expected {expected} violations, got {len(violations)}: {violations}",
                file=sys.stderr,
            )
            failures += 1

    for name, message, expected_values in TRAILER_PARSER_CASES[:100]:
        actual_values = parse_exempt_trailers(message, repo_root)
        if actual_values is None:
            print(
                f"FAIL self-test parse_exempt_trailers '{name}': git tool error",
                file=sys.stderr,
            )
            return 2
        if actual_values != expected_values:
            print(
                f"FAIL self-test parse_exempt_trailers '{name}': expected {expected_values}, got {actual_values}",
                file=sys.stderr,
            )
            failures += 1

    failures += self_test_run_git(repo_root)

    if failures:
        print(f"co-change self-test: {failures} failures", file=sys.stderr)
        return 1

    total_cases = len(SELF_TEST_CASES) + len(TRAILER_PARSER_CASES) + 5
    print(f"co-change self-test: {total_cases} cases ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the check and its self-test."""
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run co-change check or self-test."""
    args = build_parser().parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent

    if shutil.which("git") is None:
        print("co-change: git not on PATH", file=sys.stderr)
        return 2

    if args.self_test:
        return run_self_test(repo_root)

    found = check_base_ref(args.base, repo_root)
    if found is None:
        return 2

    if not found:
        if args.require_base:
            print(
                f"co-change: required base ref '{args.base}' not found",
                file=sys.stderr,
            )
            return 2
        print(f"co-change: skipped: base ref {args.base} not found")
        return 0

    changed = get_changed_files(args.base, args.head, repo_root)
    if changed is None:
        return 2

    commits = get_commits(args.base, args.head, repo_root)
    if commits is None:
        return 2

    violations = evaluate(changed, commits)
    if violations:
        for violation in violations[:1000]:
            print(violation)
        print("exempt a rule with a commit trailer: Co-change-exempt: <rule_id> <reason>")
        return 1

    print(f"co-change: {len(changed)} files checked, 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
