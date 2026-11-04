#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that Python except clauses with bare pass include explanatory comments.

CodeQL rule py/empty-except requires that an except handler whose body consists solely
of a pass statement includes an explanatory comment documenting why ignoring the exception
is correct. This checker AST-parses Python files and ensures that every such ExceptHandler
has at least one comment between the handler start and the pass statement.

Usage:
    python3 -I scripts/check-python-empty-except.py
    python3 -I scripts/check-python-empty-except.py [path/to/file.py ...]
    python3 -I scripts/check-python-empty-except.py --self-test
"""

from __future__ import annotations

import argparse
import ast
from datetime import date, timedelta
import io
import pathlib
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
import tokenize
from typing import TextIO

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from standards_exceptions import filter_excused, load_entries

DESCRIPTION = "Check that Python except clauses with bare pass include explanatory comments."

SELF_TEST_CASES: list[tuple[str, str, int]] = [
    (
        "bare_pass",
        "try:\n    do_work()\nexcept Exception:\n    pass\n",
        1,
    ),
    (
        "comment_above_pass",
        "try:\n    do_work()\nexcept Exception:\n    # explanatory comment\n    pass\n",
        0,
    ),
    (
        "comment_on_except_line",
        "try:\n    do_work()\nexcept Exception:  # explanatory comment\n    pass\n",
        0,
    ),
    (
        "hash_in_string_on_except_line",
        "try:\n    do_work()\nexcept ('#' and Exception):\n    pass\n",
        1,
    ),
    (
        "body_with_pass_and_other_stmt",
        "try:\n    do_work()\nexcept Exception:\n    log_error()\n    pass\n",
        0,
    ),
]


def check_source_content(
    source_bytes: bytes,
    display_path: str,
) -> tuple[list[str], list[str]]:
    """Check Python source bytes for bare except handlers lacking explanatory comments."""
    try:
        tree = ast.parse(source_bytes, filename=display_path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno or 1}: syntax error during parse: {exc.msg}"], []

    try:
        tokens = list(tokenize.tokenize(io.BytesIO(source_bytes).readline))
    except (tokenize.TokenError, UnicodeDecodeError) as exc:
        return [f"{display_path}:1: tokenize error: {exc}"], []

    comment_lines = {tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT}
    violations: list[str] = []

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ):
            pass_node = node.body[0]
            has_comment = any(
                line in comment_lines
                for line in range(node.lineno, pass_node.lineno + 1)
            )
            if not has_comment:
                violations.append(
                    f"{display_path}:{node.lineno}: empty except without explanatory comment"
                )

    violations.sort()
    return [], violations


def check_file(path: pathlib.Path, repo_root: pathlib.Path | None = None) -> tuple[list[str], list[str]]:
    """Check a single Python file on disk."""
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        return [f"{path}:1: cannot read file: {exc}"], []
    try:
        if repo_root and path.is_relative_to(repo_root):
            display_path = str(path.relative_to(repo_root))
        else:
            display_path = str(path)
    except (ValueError, AttributeError):
        display_path = str(path)
    return check_source_content(source_bytes, display_path)


def get_tracked_python_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Retrieve git-tracked Python files via git ls-files."""
    proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        ["git", "ls-files", "*.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return [repo_root / line for line in lines]


def check_files(
    files: list[pathlib.Path],
    repo_root: pathlib.Path | None = None,
    entries: list[dict[str, str]] | None = None,
    today: date | None = None,
    out: TextIO = sys.stdout,
) -> list[str]:
    """Check Python files and filter excused violations."""
    diagnostics: list[str] = []
    all_violations: list[str] = []
    for file_path in files:
        diags, violations = check_file(file_path, repo_root)
        diagnostics.extend(diags)
        all_violations.extend(violations)

    if entries is None:
        entries = load_entries(repo_root) if repo_root else []
    if today is None:
        today = date.today()
    return diagnostics + filter_excused(all_violations, entries, "HISS-07", today, out=out)


def exceptions_self_test() -> list[str]:
    """Verify live, missing, expired, and mismatched exceptions on planted violations."""
    failures: list[str] = []
    fixture_path = "fixture/target.py"
    violation = f"{fixture_path}:10: empty except without explanatory comment"
    ref_today = date(2026, 11, 4)
    yesterday = (ref_today - timedelta(days=1)).isoformat()
    future = (ref_today + timedelta(days=3)).isoformat()

    # Case 1: live fixture entry -> excused
    live_entries = [
        {"rule": "HISS-07", "path": fixture_path, "reason": "category B, 3 days: test", "expires": future}
    ]
    buf = io.StringIO()
    if filter_excused([violation], live_entries, "HISS-07", ref_today, out=buf):
        failures.append("live exception was not excused")
    expected_msg = f"{fixture_path}: excused by .standards.yaml exception HISS-07 until {future}\n"
    if buf.getvalue() != expected_msg:
        failures.append(f"live exception output mismatch: {buf.getvalue()!r} != {expected_msg!r}")

    # Case 2: same finding, no entry -> refused
    if filter_excused([violation], [], "HISS-07", ref_today, out=io.StringIO()) != [violation]:
        failures.append("violation without exception was not refused")

    # Case 3: same finding, entry expired -> refused
    expired_entries = [
        {"rule": "HISS-07", "path": fixture_path, "reason": "category B, 3 days: test", "expires": yesterday}
    ]
    if filter_excused([violation], expired_entries, "HISS-07", ref_today, out=io.StringIO()) != [violation]:
        failures.append("expired exception was not refused")

    # Case 4: entry for the other rule id -> refused
    other_entries = [
        {"rule": "HISS-10", "path": fixture_path, "reason": "category B, 3 days: test", "expires": future}
    ]
    if filter_excused([violation], other_entries, "HISS-07", ref_today, out=io.StringIO()) != [violation]:
        failures.append("mismatched rule exception was not refused")

    # Case 5: syntax error with live exception -> diagnostic retained
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = pathlib.Path(tmp)
        bad_file = tmp_root / "syntax_error.py"
        bad_file.write_text("def broken(\n", encoding="utf-8")
        bad_entries = [
            {"rule": "HISS-07", "path": "syntax_error.py", "reason": "category B, 3 days: test", "expires": future}
        ]
        retained = check_files([bad_file], tmp_root, entries=bad_entries, today=ref_today, out=io.StringIO())
        if not any("syntax error" in item for item in retained):
            failures.append("syntax error with live exception was silently excused")

    return failures


def run_self_test() -> int:
    """Run self-test suite covering planted cases and report outcome."""
    failures = 0
    for name, source, expect_violations in SELF_TEST_CASES:
        diags, violations = check_source_content(source.encode("utf-8"), f"<self-test:{name}>")
        if diags:
            print(
                f"FAIL self-test '{name}': unexpected diagnostics: {diags}",
                file=sys.stderr,
            )
            failures += 1
        if len(violations) != expect_violations:
            print(
                f"FAIL self-test '{name}': expected {expect_violations} violations, got {len(violations)}: {violations}",
                file=sys.stderr,
            )
            failures += 1

    for failure in exceptions_self_test():
        print(f"FAIL self-test (exceptions): {failure}", file=sys.stderr)
        failures += 1

    if failures:
        print(f"python-empty-except self-test: {failures} failures", file=sys.stderr)
        return 1

    print(f"python-empty-except self-test: {len(SELF_TEST_CASES) + 5} cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and check python files or run self-test."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test on planted cases",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        help="Optional Python files to check (defaults to git-tracked *.py)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.paths:
        files = args.paths
    else:
        if shutil.which("git") is None:
            print(
                "python-empty-except: git not on PATH; pass files or use --self-test",
                file=sys.stderr,
            )
            return 2
        files = get_tracked_python_files(repo_root)

    all_violations = check_files(files, repo_root)

    if all_violations:
        for violation in all_violations:
            print(violation)
        return 1

    print(f"python-empty-except: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
