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
import io
import pathlib
import subprocess
import sys
import tokenize

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
) -> list[str]:
    """Check Python source bytes for bare except handlers lacking explanatory comments."""
    try:
        tree = ast.parse(source_bytes, filename=display_path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno or 1}: syntax error during parse: {exc.msg}"]

    try:
        tokens = list(tokenize.tokenize(io.BytesIO(source_bytes).readline))
    except (tokenize.TokenError, UnicodeDecodeError) as exc:
        return [f"{display_path}:1: tokenize error: {exc}"]

    comment_lines = {tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
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
    return violations


def check_file(path: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[str]:
    """Check a single Python file on disk."""
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        return [f"{path}:1: cannot read file: {exc}"]
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


def run_self_test() -> int:
    """Run self-test suite covering planted cases and report outcome."""
    failures = 0
    for name, source, expect_violations in SELF_TEST_CASES:
        violations = check_source_content(source.encode("utf-8"), f"<self-test:{name}>")
        if len(violations) != expect_violations:
            print(
                f"FAIL self-test '{name}': expected {expect_violations} violations, got {len(violations)}: {violations}",
                file=sys.stderr,
            )
            failures += 1

    if failures:
        print(f"python-empty-except self-test: {failures} failures", file=sys.stderr)
        return 1

    print(f"python-empty-except self-test: {len(SELF_TEST_CASES)} cases ok")
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
        files = get_tracked_python_files(repo_root)

    all_violations: list[str] = []
    for file_path in files:
        violations = check_file(file_path, repo_root)
        all_violations.extend(violations)

    if all_violations:
        for violation in all_violations:
            print(violation)
        return 1

    print(f"python-empty-except: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
