#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that ffmpeg invocations using the showinfo filter include -nostats.

Every ffmpeg command in shell scripts, Makefiles, GitHub Actions workflows,
and Python test harnesses that parses showinfo output must specify -nostats.
Otherwise, FFmpeg's main thread periodically writes a progress line (terminated
by a carriage return) while the filter thread logs showinfo lines across multiple
calls, which can split a side-data line and cause parsed frame side-data counts
to drop to zero.

Usage:
    python3 -I scripts/check-showinfo-nostats.py
    python3 -I scripts/check-showinfo-nostats.py [path ...]
    python3 -I scripts/check-showinfo-nostats.py --self-test
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
import tempfile

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from tracked_python import is_python, tracked_files  # noqa: E402

DESCRIPTION = (
    "Check that ffmpeg invocations using the showinfo filter include -nostats."
)
SHOWINFO_NAME = "show" + "info"
REASON = f"{SHOWINFO_NAME} filter without -nostats: the progress line can split {SHOWINFO_NAME} lines"

SHELL_SUFFIXES = frozenset({".sh", ".bash", ".mk"})
WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})

SHOWINFO_SHELL_RE = re.compile(
    rf"(?:-vf|-filter:v|-filter_complex|-lavfi)\s+[\"']?{SHOWINFO_NAME}(?![A-Za-z0-9_])|[,;\]]{SHOWINFO_NAME}(?![A-Za-z0-9_])"
)
NOSTATS_RE = re.compile(r"(?<![A-Za-z0-9_-])-nostats(?![A-Za-z0-9_-])")
SHOWINFO_PY_RE = re.compile(rf"[,;\]]{SHOWINFO_NAME}(?![A-Za-z0-9_])")


def is_shell_file(path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    """True for shell scripts, Makefiles, and GitHub Actions workflows."""
    rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
    if path.suffix in SHELL_SUFFIXES or path.name == "Makefile":
        return True
    return (
        len(rel.parts) >= 2
        and rel.parts[0] == ".github"
        and rel.parts[1] == "workflows"
        and path.suffix in WORKFLOW_SUFFIXES
    )


def is_scanned(path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    """True for shell scripts, Makefiles, workflows, and Python files."""
    if is_shell_file(path, repo_root):
        return True
    return is_python(path)


def get_tracked_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Git-tracked shell, make, workflow, and Python files."""
    return [f for f in tracked_files(repo_root) if is_scanned(f, repo_root)]


def is_shell_violation(cmd: str) -> bool:
    """True if cmd uses showinfo filter without -nostats option."""
    return bool(SHOWINFO_SHELL_RE.search(cmd) and not NOSTATS_RE.search(cmd))


def check_shell_content(content: str, display_path: str) -> list[str]:
    """Check shell content for showinfo invocations missing -nostats."""
    violations: list[str] = []
    lines = content.splitlines()
    buffer: list[str] = []
    start_lineno = 1

    for idx, raw_line in enumerate(lines, start=1):
        clean = raw_line.rstrip("\r\n")
        if not buffer and clean.lstrip().startswith("#"):
            continue
        if not buffer:
            start_lineno = idx

        if clean.endswith("\\"):
            buffer.append(clean[:-1].rstrip())
        else:
            buffer.append(clean)
            cmd = " ".join(buffer)
            buffer = []
            if is_shell_violation(cmd):
                violations.append(f"{display_path}:{start_lineno}: {REASON}")

    if buffer:
        cmd = " ".join(buffer)
        if is_shell_violation(cmd):
            violations.append(f"{display_path}:{start_lineno}: {REASON}")

    return violations


def collect_docstrings(tree: ast.AST) -> set[int]:
    """Collect id() of docstring Constant nodes across all scopes."""
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if node.body and isinstance(node.body[0], ast.Expr):
            val = node.body[0].value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                doc_ids.add(id(val))
    return doc_ids


def is_python_filter_use(val: str) -> bool:
    """True if constant string value represents showinfo filter use."""
    if val == SHOWINFO_NAME:
        return True
    return bool(SHOWINFO_PY_RE.search(val))


def scope_has_nostats(scope: ast.AST) -> bool:
    """True if any string Constant inside scope node equals -nostats."""
    for child in ast.walk(scope):
        if isinstance(child, ast.Constant) and child.value == "-nostats":
            return True
    return False


def link_parents(tree: ast.AST) -> None:
    """Link each AST child node to its parent node using ast.walk."""
    setattr(tree, "parent", None)
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "parent", parent)


def get_innermost_scope(node: ast.AST, tree: ast.AST) -> ast.AST:
    """Resolve innermost enclosing def/async def, or module if none."""
    curr = getattr(node, "parent", None)
    while curr is not None and not isinstance(
        curr, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        curr = getattr(curr, "parent", None)
    return curr if curr is not None else tree


def check_python_content(
    content: str, display_path: str
) -> tuple[list[str], list[str]]:
    """Check Python content AST for showinfo filter constants without -nostats in scope."""
    try:
        tree = ast.parse(content, filename=display_path)
    except SyntaxError as exc:
        msg = f"{display_path}:{exc.lineno or 1}: python syntax error: {exc.msg}"
        return [msg], [msg]

    link_parents(tree)
    doc_ids = collect_docstrings(tree)
    violations: list[str] = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in doc_ids or not is_python_filter_use(node.value):
            continue
        scope = get_innermost_scope(node, tree)
        if not scope_has_nostats(scope):
            violations.append(f"{display_path}:{node.lineno}: {REASON}")

    return [], violations


def check_file(
    path: pathlib.Path, repo_root: pathlib.Path | None = None
) -> tuple[list[str], list[str]]:
    """Check a single file on disk."""
    try:
        content = path.read_text(encoding="utf-8", errors="surrogateescape")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}:1: cannot read file: {exc}"], []

    root = repo_root if repo_root is not None else path.parent
    display_path = (
        str(path.relative_to(root))
        if repo_root and path.is_relative_to(root)
        else str(path)
    )

    if is_shell_file(path, root):
        return [], check_shell_content(content, display_path)
    if is_python(path):
        return check_python_content(content, display_path)
    return [], []


SELF_TEST_CASES: tuple[tuple[str, str, str, str | None], ...] = (
    (
        "shell_without_nostats",
        "test1.sh",
        f"ffmpeg -vf {SHOWINFO_NAME} -f null -\n",
        REASON,
    ),
    (
        "shell_with_nostats",
        "test2.sh",
        f"ffmpeg -nostats -vf {SHOWINFO_NAME} -f null -\n",
        None,
    ),
    (
        "shell_backslash_earlier_nostats",
        "test3.sh",
        f"ffmpeg -nostats \\\n  -vf {SHOWINFO_NAME} \\\n  -f null -\n",
        None,
    ),
    (
        "shell_chain_without_nostats",
        "test4.sh",
        f'ffmpeg -vf "format=yuv420p,{SHOWINFO_NAME}" -f null -\n',
        REASON,
    ),
    (
        "shell_nostats_next_command",
        "test5.sh",
        f"ffmpeg -vf {SHOWINFO_NAME} -f null -\nffmpeg -nostats -f null -\n",
        REASON,
    ),
    ("shell_comment", "test6.sh", f"# ffmpeg -vf {SHOWINFO_NAME} -f null -\n", None),
    (
        "shell_prose",
        "test7.sh",
        f'echo "the first {SHOWINFO_NAME} frame"\necho "[Parsed_{SHOWINFO_NAME}_6 @ 0x1]"\nls *{SHOWINFO_NAME}*\n',
        None,
    ),
    (
        "shell_nostatsx",
        "test8.sh",
        f"ffmpeg -nostatsx -vf {SHOWINFO_NAME} -f null -\n",
        REASON,
    ),
    (
        "workflow_run_without_nostats",
        ".github/workflows/x.yml",
        f"jobs:\n  t:\n    steps:\n      - run: ffmpeg -vf {SHOWINFO_NAME}\n",
        REASON,
    ),
    (
        "docs_yaml_not_scanned",
        "docs/x.yaml",
        f"key: ffmpeg -vf {SHOWINFO_NAME}\n",
        None,
    ),
    (
        "python_def_without_nostats",
        "test11.py",
        f'def run():\n    return ["-vf", "{SHOWINFO_NAME}"]\n',
        REASON,
    ),
    (
        "python_def_with_nostats",
        "test12.py",
        f'def run():\n    return ["-nostats", "-vf", "{SHOWINFO_NAME}"]\n',
        None,
    ),
    (
        "python_nostats_different_def",
        "test13.py",
        f'def a():\n    return ["-vf", "{SHOWINFO_NAME}"]\n\ndef b():\n    return ["-nostats"]\n',
        REASON,
    ),
    (
        "python_docstring_mention",
        "test14.py",
        f'"""Module {SHOWINFO_NAME} doc."""\n\ndef f():\n    """Func {SHOWINFO_NAME} doc."""\n    pass\n',
        None,
    ),
    (
        "python_syntax_error",
        "test15.py",
        "def broken(:\n    pass\n",
        "python syntax error",
    ),
)


def self_test_scanned(root: pathlib.Path) -> int:
    """Planted paths: workflows, shell, makefiles and python are scanned; others not."""
    cases = [
        (".github/workflows/a.yml", True),
        (".github/workflows/b.yaml", True),
        ("docs/c.yaml", False),
        ("scripts/d.sh", True),
        ("scripts/e.bash", True),
        ("Makefile", True),
        ("rules.mk", True),
        ("tools/f.py", True),
        ("docs/g.md", False),
    ]
    failures = 0
    for rel, expected in cases:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        if is_scanned(p, root) != expected:
            print(
                f"FAIL self-test path '{rel}': expected scanned={expected}",
                file=sys.stderr,
            )
            failures += 1
    return failures


def run_self_test() -> int:
    """Run self-test on planted cases in a temporary directory."""
    cases = SELF_TEST_CASES
    failures = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        for name, rel_path, content, expected_reason in cases:
            test_file = tmp_path / rel_path
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(content, encoding="utf-8")
            if not is_scanned(test_file, tmp_path):
                diags: list[str] = []
                violations: list[str] = []
            else:
                diags, violations = check_file(test_file, tmp_path)

            if expected_reason is None:
                if diags or violations:
                    print(
                        f"FAIL self-test '{name}': expected clean, got diags={diags} viols={violations}",
                        file=sys.stderr,
                    )
                    failures += 1
            else:
                matched = any(expected_reason in v for v in violations) or any(
                    expected_reason in d for d in diags
                )
                if not matched:
                    print(
                        f"FAIL self-test '{name}': expected reason '{expected_reason}', got diags={diags} viols={violations}",
                        file=sys.stderr,
                    )
                    failures += 1
        failures += self_test_scanned(tmp_path)

    if failures:
        print(f"check-showinfo-nostats self-test: {failures} failures", file=sys.stderr)
        return 1

    print(f"check-showinfo-nostats self-test: {len(cases)} cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and check files or run self-test."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test on planted cases in a temporary directory",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        help="Optional files to check (defaults to git ls-files)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.paths:
        files = args.paths
    else:
        try:
            files = get_tracked_files(repo_root)
        except RuntimeError as exc:
            print(f"check-showinfo-nostats: {exc}", file=sys.stderr)
            return 2

    diagnostics: list[str] = []
    all_violations: list[str] = []
    for f in files:
        diags, viols = check_file(f, repo_root)
        diagnostics.extend(diags)
        all_violations.extend(viols)

    for diag in diagnostics:
        print(diag, file=sys.stderr)
    for viol in all_violations:
        print(viol)

    if diagnostics or all_violations:
        return 1

    print(f"check-showinfo-nostats: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
