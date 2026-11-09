#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Refuse a Python file that imports one module with both import and from-import.

CodeQL rule py/import-and-import-from reports a file that names a module M in
both `import M` (with or without `as`) and `from M import ...`, at any nesting
level. It reported `import ctypes` next to `from ctypes import wintypes` in
scripts/process_tree.py on pull request 579, after the push. This check runs the
same test locally before CI, and reports at the plain import line as CodeQL does.

`import M.sub` and `import M.sub as s` name the module M.sub, not M, so they do
not pair with `from M import ...`; relative imports do not count. Semgrep reads
`import M.sub as s` as `from M import sub as s`, so a Semgrep rule would report
that form too; the stdlib AST keeps the two apart.

Usage:
    python3 -I scripts/check-python-dual-import.py
    python3 -I scripts/check-python-dual-import.py [path/to/file.py ...]
    python3 -I scripts/check-python-dual-import.py --self-test
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import tracked_python  # noqa: E402

ROOT = _SCRIPTS_DIR.parent
DESCRIPTION = "Refuse a Python file that imports one module with both import and from-import."

# (name, source, plain-import lines expected to be reported)
SELF_TEST_CASES: list[tuple[str, str, list[int]]] = [
    ("plain_then_from", "import ctypes\nfrom ctypes import wintypes\n", [1]),
    ("aliased_plain", "import re as regex\nfrom re import compile\n", [1]),
    ("from_then_plain", "from json import loads\nimport json\n", [2]),
    ("nested_from", "import os\n\ndef f():\n    from os import sep\n    return sep\n", [1]),
    ("multi_name", "import sys, json\nfrom json import (\n    dumps,\n)\n", [1]),
    ("two_plain", "import os\nfrom os import sep\n\ndef g():\n    import os\n", [1, 5]),
    ("submodule_alias", "import ctypes\nimport ctypes.wintypes as wintypes\n", []),
    ("submodule_plain", "import os.path\nfrom os import sep\n", []),
    ("relative_from", "from . import util\nimport util\n", []),
    ("relative_module", "from .json import loads\nimport json\n", []),
    ("from_only", "from ctypes import wintypes\nfrom ctypes import util\n", []),
]


def check_source(source: bytes, display: str) -> list[str]:
    """Findings for one file: a dual-imported module or a parse failure."""
    try:
        tree = ast.parse(source, filename=display)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None) or 1
        return [f"{display}:{line}: cannot parse: {exc}"]
    plain: list[tuple[int, str]] = []
    froms: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            plain.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            froms.add(node.module)
    return [
        f"{display}:{line}: module '{name}' is imported with both 'import' and "
        "'import from' (CodeQL py/import-and-import-from)"
        for line, name in sorted(plain)
        if name in froms
    ]


def check_files(files: list[pathlib.Path]) -> list[str]:
    """Findings across files; an unreadable file is a finding."""
    findings: list[str] = []
    for path in files:
        try:
            display = str(path.relative_to(ROOT))
        except ValueError:
            display = str(path)
        try:
            source = path.read_bytes()
        except OSError as exc:
            findings.append(f"{display}:1: cannot read: {exc}")
            continue
        findings.extend(check_source(source, display))
    return findings


def run_self_test() -> int:
    """Planted cases: each reports exactly its expected lines; a parse error is a finding."""
    failures: list[str] = []
    for name, source, want in SELF_TEST_CASES:
        got = [int(f.split(":")[1]) for f in check_source(source.encode("utf-8"), name)]
        if got != want:
            failures.append(f"{name}: expected lines {want}, got {got}")
    if len(check_source(b"def broken(\n", "syntax_error")) != 1:
        failures.append("syntax_error: a file that does not parse gave no finding")
    for failure in failures:
        print(f"FAIL self-test {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"python-dual-import self-test: {len(SELF_TEST_CASES) + 1} cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Check the given files, or every tracked Python file, or run the self-test."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--self-test", action="store_true", help="run the planted cases")
    parser.add_argument("paths", nargs="*", type=pathlib.Path,
                        help="Python files to check (default: every tracked Python file)")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        files = list(args.paths) or tracked_python.require_files(
            [p for p in tracked_python.tracked_files(ROOT)
             if p.is_file() and tracked_python.is_python(p)],
            "tracked Python files",
        )
    except RuntimeError as exc:
        print(f"python-dual-import: {exc}", file=sys.stderr)
        return 2
    findings = check_files(files)
    for finding in findings:
        print(finding)
    if findings:
        return 1
    print(f"python-dual-import: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
