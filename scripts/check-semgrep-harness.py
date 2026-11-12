#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that all tracked test-semgrep-*.py scripts use scripts/semgrep_harness.py.

Enforces shared harness use, a nonempty tracked test list, and literal hermetic
flags: False for ROOT, REPO_ROOT or repo_root scans, True for planted trees.
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
# Names a test gives the repository tree; every other scan target is planted.
REAL_TREE_NAMES = frozenset({"ROOT", "REPO_ROOT", "repo_root"})


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


def check_hermetic_calls(tree: ast.AST, display_path: str) -> list[str]:
    """Require a literal hermetic flag matching the scan's target argument."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name != "run_semgrep":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        target = node.args[1] if len(node.args) > 1 else keywords.get("target_dir")
        real_tree = isinstance(target, ast.Name) and target.id in REAL_TREE_NAMES
        expected = not real_tree
        hermetic = keywords.get("hermetic")
        if not isinstance(hermetic, ast.Constant) or hermetic.value is not expected:
            violations.append(
                f"{display_path}:{node.lineno}: run_semgrep must pass hermetic={expected} "
                f"for {'ROOT/REPO_ROOT/repo_root' if real_tree else 'a planted-tree target'}"
            )
    return violations


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
    return violations + check_hermetic_calls(tree, display_path)


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
    return require_test_files([
        path for path in tracked_python.tracked_files(repo_root)
        if path.relative_to(repo_root).match("scripts/test-semgrep-*.py")
    ])


def require_test_files(files: list[pathlib.Path]) -> list[pathlib.Path]:
    """Refuse an empty Semgrep test selection without invoking Git."""
    return tracked_python.require_files(files, "tracked scripts/test-semgrep-*.py")


def self_test_cases() -> list[tuple[str, str, str | None]]:
    """Source fixtures cover harness imports, definitions and scan isolation."""
    harness = "import semgrep_harness\n"
    planted = "run_semgrep(bin, tree, rules, 1, cfg, hermetic=False)\n"
    return [
        ("p1", harness + "def run_semgrep():\n    pass\n",
         "defines module-level FunctionDef 'run_semgrep'"),
        ("p2", "def other_func():\n    return 0\n", "does not load semgrep_harness"),
        ("p3", 'import importlib.util\n'
         '_SPEC = importlib.util.spec_from_file_location("semgrep_harness", "semgrep_harness.py")\n'
         'run_semgrep = semgrep_harness.run_semgrep\n', None),
        ("p5", harness + planted, "must pass hermetic=True"),
        ("p6", harness + planted.replace("tree,", "ROOT,"), None),
        ("p7", harness + planted.replace("False", "True"), None),
        ("p8", harness + planted.replace("tree,", "REPO_ROOT,"), None),
        ("p9", harness + "semgrep_harness.run_semgrep("
         "bin, target_dir=tree, rule_path=rules, hermetic=False)\n", "must pass hermetic=True"),
        ("p10", harness + planted.replace(", hermetic=False", ""), "must pass hermetic=True"),
        ("p11", harness + planted.replace("False", "flag"), "must pass hermetic=True"),
        ("p12", harness + planted.replace("tree,", "ROOT,").replace("False", "True"),
         "must pass hermetic=False"),
        ("p13", harness + planted.replace("False", "1"), "must pass hermetic=True"),
        ("p14", harness + planted.replace("tree,", "repo_root,"), None),
        ("p15", harness + planted.replace("tree,", "repo_root,").replace("False", "True"),
         "must pass hermetic=False"),
        ("p16", harness + planted.replace("tree,", "root,"), "must pass hermetic=True"),
    ]


def check_empty_list_case() -> str | None:
    """p4: empty discovery must raise; a nonempty selection must survive."""
    try:
        require_test_files([])
    except RuntimeError as exc:
        if "no files to check" not in str(exc):
            return f"p4 unexpected empty-list error: {exc}"
    else:
        return "p4 empty tracked test list was not refused"
    paths = [pathlib.Path("scripts/test-semgrep-planted.py")]
    if require_test_files(paths) != paths:
        return "p4 nonempty tracked test list was refused"
    return None


def run_self_test() -> int:
    """Run planted self-test cases in a temporary directory."""
    cases = self_test_cases()
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for label, source, expected in cases:
            path = tmp / f"{label}.py"
            path.write_text(source, encoding="utf-8")
            findings = check_file(path)
            if (expected is None and findings) or (
                expected is not None and not any(expected in msg for msg in findings)
            ):
                sys.stderr.write(f"FAIL: {label}: expected {expected!r}, got {findings}\n")
                return 1
    problem = check_empty_list_case()
    if problem:
        sys.stderr.write(f"FAIL: {problem}\n")
        return 1
    print(f"check-semgrep-harness: self-test ({len(cases) + 1} cases) ok")
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
