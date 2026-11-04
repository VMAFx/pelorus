#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that Python scripts parse command lines with stdlib argparse.

Rule:
1. Every sys.argv reference must be sys.argv[0] or sys.argv[1:] passed directly
   as a call argument (e.g. main(sys.argv[1:])).
2. Any Subscript, .index() or .pop() call, or "in", "not in", "==" or "!=" comparison on a variable named
   argv, or a name bound to argv or a list/tuple/deque copy of it, is prohibited in any module that has a command line (it reads sys.argv or
   defines main or parse_args) and does not import argparse, and inside a
   function named main or parse_args in every module (argparse owns the command
   line there; a hand check next to it bypasses the parser). A module without a
   command line, such as a test helper whose argv is a recorded subprocess call,
   is out of scope.

Usage:
    python3 -I scripts/check-python-argv.py
    python3 -I scripts/check-python-argv.py [path/to/file.py ...]
    python3 -I scripts/check-python-argv.py --self-test
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys

DESCRIPTION = "Check that Python scripts parse command lines with stdlib argparse."

# Explicitly excepted files with justification:
EXCEPTIONS: dict[str, str] = {
    ".config/agent/hooks/block_evasion.py": "Praetor-managed hook, not edited by policy",
    "ffmpeg-patches/test/doc-recipes-stub-ffmpeg.py": "FFmpeg CLI stand-in mimicking FFmpeg argument syntax",
}

CLI_FUNCTIONS = frozenset({"main", "parse_args"})
# Methods that read the command line by hand when called on argv or a copy of it.
ARGV_READ_METHODS = frozenset({"index", "pop"})
# Calls that copy argv into a new name the checker then follows (args = list(argv)).
ARGV_COPY_CALLS = frozenset({"list", "tuple", "deque"})
MAX_ALIAS_PASSES = 8
# Comparisons that read the command line by hand; `argv is None` stays allowed.
ARGV_COMPARE_OPS: dict[type[ast.cmpop], str] = {
    ast.In: "in", ast.NotIn: "not in", ast.Eq: "==", ast.NotEq: "!=",
}

SELF_TEST_CASES: list[tuple[str, str, int]] = [
    (
        "argv_copy_pop_loop",
        "import sys\ndef main(argv):\n    args = list(argv)\n    while args:\n        arg = args.pop(0)\n"
        "        if arg == '--root' and args:\n            return args.pop(0)\n    return None\n"
        "if __name__ == '__main__':\n    sys.exit(main(sys.argv[1:]))\n",
        2,
    ),
    (
        "argv_alias_chain_subscript",
        "def main(argv):\n    rest = argv\n    more: list = tuple(rest)\n    return more[0]\n",
        1,
    ),
    (
        "argparse_result_not_alias",
        "import argparse\ndef main(argv):\n    args = argparse.ArgumentParser().parse_args(argv)\n"
        "    return args.items[0]\n",
        0,
    ),
    (
        "argparse_module_main_equality_bypass",
        "import argparse\ndef main(argv=None):\n    if argv == ['--self-test']:\n        return 0\n"
        "    if argv is None:\n        return 1\n    return argparse.ArgumentParser().parse_args(argv)\n",
        1,
    ),
    (
        "library_recorded_subprocess_argv",
        "def run(argv, timeout_s):\n    if '-b:v' in argv:\n        return argv[1]\n    return None\n",
        0,
    ),
    (
        "cli_module_helper_hand_check",
        "import sys\ndef parse(argv):\n    return argv[1]\nif __name__ == '__main__':\n"
        "    sys.exit(parse(sys.argv[1:]))\n",
        1,
    ),
    (
        "argparse_module_main_hand_check",
        "import argparse\ndef main(argv):\n    if argv and argv[0].endswith('.py'):\n"
        "        argv = argv[1:]\n    return argparse.ArgumentParser().parse_args(argv)\n",
        2,
    ),
    (
        "argparse_module_main_in_check",
        "import argparse\ndef main(argv):\n    if '--self-test' in argv:\n        return 0\n"
        "    return argparse.ArgumentParser().parse_args(argv)\n",
        1,
    ),
    (
        "argparse_module_helper_command_list",
        "import argparse\ndef run(argv):\n    return argv[0]\n",
        0,
    ),
    (
        "argv_index",
        "def main(argv):\n    return argv.index('--flag')\n",
        1,
    ),
    (
        "argv_subscript",
        "def main(argv):\n    return argv[1]\n",
        1,
    ),
    (
        "argv_in",
        "def main(argv):\n    if '--flag' in argv:\n        return 0\n    return 1\n",
        1,
    ),
    (
        "sys_argv_subscript",
        "import sys\nx = sys.argv[1]\n",
        1,
    ),
    (
        "sys_argv_in",
        "import sys\nif '--flag' in sys.argv:\n    pass\n",
        1,
    ),
    (
        "sys_argv_slice_outside_call",
        "import sys\nx = sys.argv[1:]\n",
        1,
    ),
    (
        "argparse_clean",
        "import argparse\nparser = argparse.ArgumentParser()\nargs = parser.parse_args()\n",
        0,
    ),
    (
        "sys_argv_passthrough",
        "import argparse\nimport sys\ndef main(argv=None):\n    parser = argparse.ArgumentParser()\n    return 0\nif __name__ == '__main__':\n    sys.exit(main(sys.argv[1:]))\n",
        0,
    ),
    (
        "sys_argv_0_allowed",
        "import argparse\nimport sys\nprog = sys.argv[0]\n",
        0,
    ),
]


def is_sys_argv_0(node: ast.Subscript) -> bool:
    """Return True if node is sys.argv[0]."""
    return (
        isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sys"
        and node.value.attr == "argv"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == 0
    )


def is_sys_argv_1_slice(node: ast.Subscript) -> bool:
    """Return True if node is sys.argv[1:]."""
    return (
        isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sys"
        and node.value.attr == "argv"
        and isinstance(node.slice, ast.Slice)
        and isinstance(node.slice.lower, ast.Constant)
        and node.slice.lower.value == 1
        and node.slice.upper is None
        and node.slice.step is None
    )


def imports_argparse_module(tree: ast.AST) -> bool:
    """Return True if the module imports argparse."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "argparse" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == "argparse":
            return True
    return False


def is_sys_argv(node: ast.AST) -> bool:
    """Return True if node is the attribute sys.argv."""
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "argv"
    )


def cli_scope_of(tree: ast.AST) -> tuple[set[int], bool]:
    """Return ids of nodes inside main/parse_args, and whether the module has a command line."""
    has_cli = any(is_sys_argv(n) for n in ast.walk(tree))
    scope: set[int] = set()
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and func.name in CLI_FUNCTIONS:
            has_cli = True
            for stmt in func.body:
                scope.update(id(n) for n in ast.walk(stmt))
    return scope, has_cli


def sys_argv_violation(node: ast.AST, parent_map: dict[ast.AST, ast.AST], path: str) -> str | None:
    """Rule 1: return a finding for an unapproved sys.argv use, else None."""
    parent = parent_map.get(node)
    if not isinstance(parent, ast.Subscript):
        return f"{path}:{getattr(node, 'lineno', 1)}: unapproved sys.argv reference"
    if is_sys_argv_0(parent):
        return None
    if not is_sys_argv_1_slice(parent):
        return f"{path}:{parent.lineno}: unapproved sys.argv subscript"
    grandparent = parent_map.get(parent)
    if isinstance(grandparent, ast.Call) and parent in grandparent.args:
        return None
    return f"{path}:{parent.lineno}: sys.argv[1:] used outside call argument"


def is_argv_name(node: ast.AST, names: frozenset[str] = frozenset({"argv"})) -> bool:
    """Return True if node is a Name called argv or bound to a copy of it."""
    return isinstance(node, ast.Name) and node.id in names


def copies_argv(value: ast.AST | None, names: set[str]) -> bool:
    """Return True if value is an argv name or a list/tuple/deque copy of one."""
    if isinstance(value, ast.Name):
        return value.id in names
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in ARGV_COPY_CALLS
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Name)
        and value.args[0].id in names
    )


def argv_aliases(tree: ast.AST) -> frozenset[str]:
    """Return argv plus every name bound to argv or a copy of it (args = list(argv))."""
    names = {"argv"}
    assigns = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))]
    for _ in range(MAX_ALIAS_PASSES):
        before = len(names)
        for node in assigns:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if copies_argv(node.value, names):
                names.update(t.id for t in targets if isinstance(t, ast.Name))
        if len(names) == before:
            break
    return frozenset(names)


def argv_violations(node: ast.AST, path: str, names: frozenset[str] = frozenset({"argv"})) -> list[str]:
    """Rule 2: return findings for hand-parsed argv at node."""
    line = getattr(node, "lineno", 1)
    if isinstance(node, ast.Subscript) and is_argv_name(node.value, names):
        return [f"{path}:{line}: subscript on argv (hand-parsed argv)"]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ARGV_READ_METHODS
        and is_argv_name(node.func.value, names)
    ):
        return [f"{path}:{line}: argv.{node.func.attr}() call (hand-parsed argv)"]
    if not isinstance(node, ast.Compare):
        return []
    found: list[str] = []
    for name in [node.left] + node.comparators:
        if is_argv_name(name, names):
            found.extend(
                f"{path}:{line}: '{ARGV_COMPARE_OPS[type(op)]}' comparison on argv (hand-parsed argv)"
                for op in node.ops
                if type(op) in ARGV_COMPARE_OPS
            )
    return found


def check_source_content(
    source_bytes: bytes,
    display_path: str,
) -> list[str]:
    """Check Python source bytes for hand-parsed command-line argv."""
    if display_path in EXCEPTIONS:
        return []
    try:
        tree = ast.parse(source_bytes, filename=display_path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno or 1}: syntax error during parse: {exc.msg}"]

    imports_argparse = imports_argparse_module(tree)
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    cli_scope, has_cli = cli_scope_of(tree)
    aliases = argv_aliases(tree)

    violations: list[str] = []
    for node in ast.walk(tree):
        if is_sys_argv(node):
            finding = sys_argv_violation(node, parent_map, display_path)
            if finding:
                violations.append(finding)
        # Rule 2: hand-parsed argv in a command-line module without argparse,
        # or inside main()/parse_args() anywhere.
        if (has_cli and not imports_argparse) or id(node) in cli_scope:
            violations.extend(argv_violations(node, display_path, aliases))
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


def run_self_test(repo_root: pathlib.Path | None = None) -> int:
    """Run self-test suite covering planted cases and report outcome."""
    failures = 0
    for name, source, expect_violations in SELF_TEST_CASES:
        violations = check_source_content(
            source.encode("utf-8"), f"<self-test:{name}>"
        )
        if len(violations) != expect_violations:
            print(
                f"FAIL self-test '{name}': expected {expect_violations} violations, got {len(violations)}: {violations}",
                file=sys.stderr,
            )
            failures += 1

    # Mirror-contract subprocess test: python3 -I scripts/check-mirror-contract.py --list
    if repo_root is None:
        repo_root = pathlib.Path(__file__).resolve().parent.parent
    mirror_script = repo_root / "scripts" / "check-mirror-contract.py"
    if mirror_script.is_file():
        proc = subprocess.run(  # nosec B603 - fixed python argv, no shell, explicit deadline
            [sys.executable, "-I", str(mirror_script), "--list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 2:
            print(
                f"FAIL self-test 'mirror_contract_cli': expected exit 2, got {proc.returncode}",
                file=sys.stderr,
            )
            failures += 1
        if "Traceback" in proc.stderr:
            print(
                f"FAIL self-test 'mirror_contract_cli': unexpected Traceback in stderr: {proc.stderr}",
                file=sys.stderr,
            )
            failures += 1

    if failures:
        print(f"python-argv self-test: {failures} failures", file=sys.stderr)
        return 1

    print(
        f"python-argv self-test: {len(SELF_TEST_CASES)} synthetic cases + mirror-contract CLI ok"
    )
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

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.self_test:
        return run_self_test(repo_root)

    if args.paths:
        files = args.paths
    else:
        if shutil.which("git") is None:
            print(
                "python-argv: git not on PATH; pass files or use --self-test",
                file=sys.stderr,
            )
            return 2
        files = get_tracked_python_files(repo_root)

    all_violations: list[str] = []
    for file_path in files:
        violations = check_file(file_path, repo_root)
        all_violations.extend(violations)

    if all_violations:
        for violation in all_violations:
            print(violation)
        return 1

    print(f"python-argv: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
