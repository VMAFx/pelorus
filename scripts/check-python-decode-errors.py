#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that a guarded text read also catches the decode error.

``Path.read_text()`` raises ``OSError`` when the file cannot be opened and
``UnicodeDecodeError`` when its bytes are not valid in the requested encoding.
A ``try`` block that calls ``read_text`` and handles ``OSError`` means to turn
an unreadable file into a clean error; when no handler of that block also
catches ``UnicodeDecodeError`` (or ``UnicodeError``, ``ValueError``,
``Exception``, ``BaseException`` or a bare ``except``), a file with invalid
bytes escapes as a traceback instead. This checker flags such blocks.

A ``try`` block without an ``OSError`` handler is not a guarded read and is
not checked. A ``read_text`` call does not count when its decode error cannot
reach the block's handlers: it passes a constant non-raising ``errors`` mode
(``"replace"``, ``"ignore"``, ``"backslashreplace"``), it sits in the body
of a nested ``try`` that catches the decode error, or it sits in the body of
a function or lambda defined inside the block and never called there. Any
other ``errors`` value, such as ``"strict"``, ``"surrogateescape"``,
``None`` or a variable, still counts.

Usage:
    python3 -I scripts/check-python-decode-errors.py
    python3 -I scripts/check-python-decode-errors.py [path/to/file.py ...]
    python3 -I scripts/check-python-decode-errors.py --self-test
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys

DESCRIPTION = "Check that a try block guarding read_text() also catches UnicodeDecodeError."

OS_ERRORS = frozenset({"OSError", "IOError", "FileNotFoundError", "PermissionError"})
DECODE_ERRORS = frozenset({"UnicodeDecodeError", "UnicodeError", "ValueError",
                           "Exception", "BaseException", "*"})
# surrogateescape is not listed: it re-raises for bytes below 128, which a
# UTF-16 or UTF-32 decode can reject.
NON_RAISING_MODES = frozenset({"replace", "ignore", "backslashreplace"})
# ast.TryStar exists from Python 3.11; the hooks still run on 3.10
# (docs/development/build.md).
TRY_NODES: tuple[type[ast.Try] | type[ast.TryStar], ...] = (ast.Try,) + (
    (ast.TryStar,) if hasattr(ast, "TryStar") else ())

SELF_TEST_CASES: list[tuple[str, str, int]] = [
    ("oserror_only",
     "try:\n    t = p.read_text(encoding='utf-8')\nexcept OSError as e:\n    raise X(e)\n", 1),
    ("filenotfound_only",
     "try:\n    t = p.read_text()\nexcept FileNotFoundError:\n    t = ''\n", 1),
    ("nested_call_oserror_only",
     "try:\n    n = len((root / rel).read_text(encoding='utf-8'))\nexcept OSError:\n    n = 0\n", 1),
    ("tuple_with_decode",
     "try:\n    t = p.read_text(encoding='utf-8')\nexcept (OSError, UnicodeDecodeError) as e:\n    raise X(e)\n", 0),
    ("separate_decode_handler",
     "try:\n    t = p.read_text()\nexcept OSError:\n    t = ''\nexcept UnicodeError:\n    t = ''\n", 0),
    ("valueerror_handler",
     "try:\n    t = p.read_text()\nexcept (OSError, ValueError):\n    t = ''\n", 0),
    ("bare_except",
     "try:\n    t = p.read_text()\nexcept OSError:\n    t = ''\nexcept:\n    raise\n", 0),
    ("no_oserror_handler",
     "try:\n    t = p.read_text()\nexcept KeyError:\n    t = ''\n", 0),
    ("read_bytes_oserror_only",
     "try:\n    b = p.read_bytes()\nexcept OSError:\n    b = b''\n", 0),
    ("read_text_outside_try",
     "t = p.read_text()\n", 0),
    ("errors_keyword_cannot_raise",
     "try:\n    t = p.read_text(encoding='utf-8', errors='replace')\nexcept OSError:\n    t = None\n", 0),
    ("read_text_in_handler_only",
     "try:\n    t = f()\nexcept OSError:\n    t = p.read_text()\n", 0),
    ("errors_strict_can_raise",
     "try:\n    t = p.read_text(errors='strict')\nexcept OSError:\n    t = ''\n", 1),
    ("errors_none_can_raise",
     "try:\n    t = p.read_text(errors=None)\nexcept OSError:\n    t = ''\n", 1),
    ("errors_variable_can_raise",
     "try:\n    t = p.read_text(errors=mode)\nexcept OSError:\n    t = ''\n", 1),
    ("errors_positional_replace",
     "try:\n    t = p.read_text('utf-8', 'replace')\nexcept OSError:\n    t = ''\n", 0),
    ("errors_surrogateescape_can_raise",
     "try:\n    t = p.read_text(encoding='utf-16-le', errors='surrogateescape')\n"
     "except OSError:\n    t = ''\n", 1),
    ("errors_positional_strict",
     "try:\n    t = p.read_text('utf-8', 'strict')\nexcept OSError:\n    t = ''\n", 1),
    ("nested_try_catches_decode",
     "try:\n    try:\n        t = p.read_text()\n    except UnicodeDecodeError:\n        t = ''\n"
     "except OSError:\n    t = ''\n", 0),
    ("nested_try_oserror_only_both_flagged",
     "try:\n    try:\n        t = p.read_text()\n    except OSError:\n        t = ''\n"
     "except OSError:\n    t = ''\n", 2),
    ("read_after_nested_try_still_flagged",
     "try:\n    try:\n        t = p.read_text()\n    except UnicodeDecodeError:\n        t = ''\n"
     "    u = q.read_text()\nexcept OSError:\n    u = ''\n", 1),
    ("read_in_nested_handler_reaches_outer",
     "try:\n    try:\n        t = f()\n    except UnicodeDecodeError:\n        t = p.read_text()\n"
     "except OSError:\n    t = ''\n", 1),
    ("read_in_nested_def_body",
     "try:\n    def g():\n        return p.read_text()\nexcept OSError:\n    g = None\n", 0),
    ("read_in_nested_lambda_body",
     "try:\n    g = lambda: p.read_text()\nexcept OSError:\n    g = None\n", 0),
    ("read_in_nested_def_called_in_block",
     "try:\n    def g():\n        return p.read_text()\n    t = g()\nexcept OSError:\n    t = ''\n", 1),
    ("read_in_lambda_called_in_place",
     "try:\n    t = (lambda: p.read_text())()\nexcept OSError:\n    t = ''\n", 1),
    ("read_in_named_lambda_called_in_block",
     "try:\n    g = lambda: p.read_text()\n    t = g()\nexcept OSError:\n    t = ''\n", 1),
    ("read_in_nested_def_default_runs",
     "try:\n    def g(t=p.read_text()):\n        return t\nexcept OSError:\n    g = None\n", 1),
]


def handler_names(handler: ast.ExceptHandler) -> set[str]:
    """Return the exception class names one handler catches; ``{"*"}`` for a bare except."""
    if handler.type is None:
        return {"*"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = set()
    for node in nodes:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def catches_decode(node: ast.Try | ast.TryStar) -> bool:
    """Report whether a try node has a handler that catches the decode error."""
    return any(handler_names(h) & DECODE_ERRORS for h in node.handlers)


def read_can_raise_decode(node: ast.AST) -> bool:
    """Report whether a node is a ``.read_text(...)`` call that can raise a decode error."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_text"):
        return False
    modes = [kw.value for kw in node.keywords if kw.arg == "errors"]
    if len(node.args) >= 2:
        modes.append(node.args[1])
    return not any(isinstance(m, ast.Constant) and m.value in NON_RAISING_MODES
                   for m in modes)


def called_in(body: list[ast.stmt]) -> tuple[set[str], set[int]]:
    """Return the names called by plain name and the ids of lambdas called in place in ``body``."""
    names: set[str] = set()
    lambdas: set[int] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Lambda):
                lambdas.add(id(node.func))
    return names, lambdas


def lambda_bindings(body: list[ast.stmt]) -> dict[int, set[str]]:
    """Map each lambda assigned to plain names in ``body`` to those names."""
    bound: dict[int, set[str]] = {}
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
                bound[id(node.value)] = {t.id for t in node.targets if isinstance(t, ast.Name)}
    return bound


def deferred_body(node: ast.AST, called: tuple[set[str], set[int]],
                  bound: dict[int, set[str]]) -> list[ast.AST]:
    """Return the body of a nested function or lambda the try block never calls, else []."""
    names, lambdas = called
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [] if node.name in names else list(node.body)
    if isinstance(node, ast.Lambda):
        if id(node) in lambdas or bound.get(id(node), set()) & names:
            return []
        return [node.body]
    return []


def shielded_nodes(body: list[ast.stmt]) -> set[int]:
    """Return ids of nodes whose decode error cannot reach the handlers of the enclosing try.

    Covers the body of a nested try that catches the decode error, and the body of a
    nested function or lambda that the block never calls (it runs later, outside the
    block). A function called by name in the block, a lambda called in place, or a
    lambda assigned to a name the block calls is not shielded. Decorators and default
    arguments run inline and are not shielded either.
    """
    called = called_in(body)
    bound = lambda_bindings(body)
    shielded: set[int] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, TRY_NODES) and catches_decode(node):
                inner: list[ast.AST] = list(node.body)
            else:
                inner = deferred_body(node, called, bound)
            for sub in inner:
                shielded.update(id(n) for n in ast.walk(sub))
    return shielded


def body_reads_text(body: list[ast.stmt]) -> bool:
    """Report whether a try body calls ``.read_text(...)`` whose decode error reaches its handlers."""
    shielded = shielded_nodes(body)
    for stmt in body:
        for node in ast.walk(stmt):
            if id(node) not in shielded and read_can_raise_decode(node):
                return True
    return False


def check_source_content(source_bytes: bytes, display_path: str) -> list[str]:
    """Return one violation per try block that guards read_text() against OSError only."""
    try:
        tree = ast.parse(source_bytes, filename=display_path)
    except SyntaxError as exc:
        return [f"{display_path}:{exc.lineno or 1}: syntax error during parse: {exc.msg}"]
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, TRY_NODES) or not body_reads_text(node.body):
            continue
        caught = set()
        for handler in node.handlers:
            caught |= handler_names(handler)
        if not caught & OS_ERRORS or catches_decode(node):
            continue
        violations.append(f"{display_path}:{node.lineno}: read_text() guarded against "
                          "OSError but not UnicodeDecodeError")
    violations.sort()
    return violations


def check_file(path: pathlib.Path, repo_root: pathlib.Path) -> list[str]:
    """Check a single Python file on disk."""
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        return [f"{path}:1: cannot read file: {exc}"]
    try:
        display_path = str(path.relative_to(repo_root))
    except ValueError:
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
    return [repo_root / line.strip() for line in proc.stdout.splitlines() if line.strip()]


def run_self_test() -> int:
    """Run every planted case; each must yield its expected violation count."""
    failures = 0
    for name, source, expect in SELF_TEST_CASES:
        got = check_source_content(source.encode("utf-8"), f"<self-test:{name}>")
        if len(got) != expect:
            print(f"FAIL self-test '{name}': expected {expect} violations, got {len(got)}: {got}",
                  file=sys.stderr)
            failures += 1
    if failures:
        print(f"python-decode-errors self-test: {failures} failures", file=sys.stderr)
        return 1
    print(f"python-decode-errors self-test: {len(SELF_TEST_CASES)} cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and check Python files or run the self-test."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--self-test", action="store_true", help="Run the planted cases")
    parser.add_argument("paths", nargs="*", type=pathlib.Path,
                        help="Python files to check (default: git-tracked *.py)")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.paths:
        files = args.paths
    else:
        if shutil.which("git") is None:
            print("python-decode-errors: git not on PATH; pass files or use --self-test",
                  file=sys.stderr)
            return 2
        files = get_tracked_python_files(repo_root)
    violations = []
    for file_path in files:
        violations.extend(check_file(file_path, repo_root))
    for violation in violations:
        print(violation)
    if violations:
        return 1
    print(f"python-decode-errors: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
