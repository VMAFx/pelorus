#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that all tracked test-semgrep-*.py scripts use scripts/semgrep_harness.py.

Enforces that every tracked Semgrep test script loads semgrep_harness and does
not define run_semgrep, scrubbed_env, resolve_executable, or ScanError at module
level.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
import tempfile

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import tracked_python  # noqa: E402

BANNED_MODULE_DEFS = frozenset({
    "run_semgrep",
    "scrubbed_env",
    "resolve_executable",
    "ScanError",
})


def loads_harness(tree: ast.AST) -> bool:
    """Return True if tree imports semgrep_harness or loads it via importlib."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "semgrep_harness" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "semgrep_harness":
                return True
        elif isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "semgrep_harness":
                    return True
    return False


def check_source(source_bytes: bytes, display_path: str) -> list[str]:
    """Check Python source for semgrep_harness load and banned definitions."""
    try:
        tree = ast.parse(source_bytes, filename=display_path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno or 1}: syntax error: {exc.msg}"]

    violations: list[str] = []
    if not loads_harness(tree):
        violations.append(f"{display_path}: does not load semgrep_harness")

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if stmt.name in BANNED_MODULE_DEFS:
                violations.append(
                    f"{display_path}:{stmt.lineno}: defines module-level "
                    f"{type(stmt).__name__} '{stmt.name}' instead of using semgrep_harness"
                )
    return violations


def check_file(path: pathlib.Path, repo_root: pathlib.Path | None = None) -> list[str]:
    """Check a single file path for semgrep_harness compliance."""
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
    return check_source(source_bytes, display_path)


def get_tracked_semgrep_tests(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Discover git-tracked scripts/test-semgrep-*.py files."""
    return [
        path for path in tracked_python.tracked_files(repo_root)
        if path.relative_to(repo_root).match("scripts/test-semgrep-*.py")
    ]


def run_self_test() -> int:
    """Run planted self-test cases in a temporary directory."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        p1 = tmp / "p1.py"
        p1.write_text(
            'import importlib.util\n'
            '_SPEC = importlib.util.spec_from_file_location("semgrep_harness", "semgrep_harness.py")\n'
            'def run_semgrep():\n'
            '    pass\n',
            encoding="utf-8",
        )
        p2 = tmp / "p2.py"
        p2.write_text(
            'def other_func():\n'
            '    return 0\n',
            encoding="utf-8",
        )
        p3 = tmp / "p3.py"
        p3.write_text(
            'import importlib.util\n'
            '_SPEC = importlib.util.spec_from_file_location("semgrep_harness", "semgrep_harness.py")\n'
            'semgrep_harness = importlib.util.module_from_spec(_SPEC)\n'
            'run_semgrep = semgrep_harness.run_semgrep\n'
            'def main():\n'
            '    return 0\n',
            encoding="utf-8",
        )

        v1 = check_file(p1)
        if not any("defines module-level FunctionDef 'run_semgrep'" in msg for msg in v1):
            sys.stderr.write("FAIL: p1 defining run_semgrep was not refused\n")
            return 1

        v2 = check_file(p2)
        if not any("does not load semgrep_harness" in msg for msg in v2):
            sys.stderr.write("FAIL: p2 not loading harness was not refused\n")
            return 1

        v3 = check_file(p3)
        if v3:
            sys.stderr.write(f"FAIL: p3 conforming file was refused: {v3}\n")
            return 1

    print("check-semgrep-harness: self-test (3 cases) ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for checking semgrep harness use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run planted self-test cases")
    parser.add_argument("paths", nargs="*", type=pathlib.Path, help="Optional files to check")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.paths:
        files = args.paths
    else:
        try:
            files = get_tracked_semgrep_tests(repo_root)
        except RuntimeError as exc:
            sys.stderr.write(f"check-semgrep-harness: {exc}\n")
            return 2

    violations: list[str] = []
    for file_path in files:
        violations.extend(check_file(file_path, repo_root))

    if violations:
        for violation in violations:
            sys.stderr.write(f"{violation}\n")
        return 1

    print(f"check-semgrep-harness: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
