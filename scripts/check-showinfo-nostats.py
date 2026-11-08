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

Limits:
    Filter graphs held in shell variables (-vf "$VAR"), -filter_script /
    -filter_complex_script files, and Python here-docs inside shell scripts
    are not followed; such a command must carry -nostats on the same line as
    a literal showinfo, or the chain must name showinfo on the command line
    (as ffmpeg-patches/test/vulkan-format-matrix.sh does).

Usage:
    python3 -I scripts/check-showinfo-nostats.py
    python3 -I scripts/check-showinfo-nostats.py [path ...]
    python3 -I scripts/check-showinfo-nostats.py --self-test
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable
import pathlib
import re
import sys
import tempfile
from typing import TypeGuard

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from shell_scan import is_shell_file, line_at, logical_lines, split_commands  # noqa: E402
from tracked_python import is_python, tracked_files  # noqa: E402

DESCRIPTION = (
    "Check that ffmpeg invocations using the showinfo filter include -nostats."
)
SHOWINFO_NAME = "show" + "info"
REASON = f"{SHOWINFO_NAME} filter without -nostats: the progress line can split {SHOWINFO_NAME} lines"

SHOWINFO_SHELL_RE = re.compile(
    rf"(?:-vf|-filter(?::[A-Za-z0-9_:]+)?|-filter_complex|-lavfi)\s+[\"']?\s*{SHOWINFO_NAME}(?![A-Za-z0-9_])|[,;\]]\s*{SHOWINFO_NAME}(?![A-Za-z0-9_])"
)
NOSTATS_RE = re.compile(r"(?<![A-Za-z0-9_-])-nostats(?![A-Za-z0-9_-])")
PY_LEAD_RE = re.compile(rf"^\s*{SHOWINFO_NAME}(?=\s*(?:$|[=@,;\[]))")
PY_SEP_RE = re.compile(rf"[,;\]]\s*{SHOWINFO_NAME}(?![A-Za-z0-9_])")
SCOPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def is_scanned(path: pathlib.Path) -> bool:
    """True for shell scripts, Makefiles, workflows, and Python files."""
    return is_shell_file(path) or is_python(path)


def get_tracked_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Git-tracked shell, make, workflow, and Python files."""
    return [f for f in tracked_files(repo_root) if is_scanned(f)]


def check_shell_content(content: str, display_path: str) -> list[str]:
    """Check shell content for showinfo invocations missing -nostats."""
    violations: list[str] = []
    for line in logical_lines(content):
        for offset, seg in split_commands(line.text):
            match = SHOWINFO_SHELL_RE.search(seg)
            if match and not NOSTATS_RE.search(seg):
                lineno = line_at(line, offset + match.start())
                violations.append(f"{display_path}:{lineno}: {REASON}")
    return violations


def collect_docstrings(tree: ast.AST) -> set[int]:
    """Collect id() of docstring Constant nodes across all scopes."""
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, SCOPES):
            continue
        if node.body and isinstance(node.body[0], ast.Expr):
            val = node.body[0].value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                doc_ids.add(id(val))
    return doc_ids


def is_python_filter_use(val: str) -> bool:
    """True if constant string value represents showinfo filter use."""
    return bool(PY_LEAD_RE.search(val) or PY_SEP_RE.search(val))


def own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Return nodes in scope without descending into nested SCOPES."""
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = [scope]
    budget = sum(1 for _ in ast.walk(scope))
    for _ in range(budget):
        if not stack:
            break
        curr = stack.pop()
        nodes.append(curr)
        for child in reversed(list(ast.iter_child_nodes(curr))):
            if not isinstance(child, SCOPES):
                stack.append(child)
    return nodes


def has_nostats(nodes: Iterable[ast.AST]) -> bool:
    """True if any AST node in nodes is the string Constant '-nostats'."""
    for node in nodes:
        if isinstance(node, ast.Constant) and node.value == "-nostats":
            return True
    return False


def is_filter_constant(node: ast.AST, doc_ids: set[int]) -> TypeGuard[ast.Constant]:
    """True if node is a non-docstring string Constant naming the showinfo filter."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in doc_ids
        and is_python_filter_use(node.value)
    )


def call_arg_elements(call: ast.Call) -> list[ast.AST]:
    """Return elements of any List or Tuple passed directly as an arg or kwarg of call."""
    elements: list[ast.AST] = []
    for arg in [*call.args, *(kw.value for kw in call.keywords)]:
        if isinstance(arg, (ast.List, ast.Tuple)):
            elements.extend(arg.elts)
    return elements


def _check_call(call: ast.Call, doc_ids: set[int], call_bound: set[int]) -> list[int]:
    """Record call-bound filter constants in call and return violating line numbers."""
    call_ok = has_nostats(ast.walk(call))
    bad: list[int] = []
    for elt in call_arg_elements(call):
        if is_filter_constant(elt, doc_ids):
            call_bound.add(id(elt))
            if not call_ok:
                bad.append(elt.lineno)
    return bad


def check_scope(scope: ast.AST, doc_ids: set[int], display_path: str) -> list[str]:
    """Check one Python scope for call-bound and scope-bound showinfo filter uses."""
    nodes = own_nodes(scope)
    call_bound: set[int] = set()
    bad_lines: list[int] = []

    for node in nodes:
        if isinstance(node, ast.Call):
            bad_lines.extend(_check_call(node, doc_ids, call_bound))

    if not has_nostats(nodes):
        for node in nodes:
            if id(node) not in call_bound and is_filter_constant(node, doc_ids):
                bad_lines.append(node.lineno)

    return [f"{display_path}:{lineno}: {REASON}" for lineno in sorted(bad_lines)]


def check_python_content(data: bytes, display_path: str) -> tuple[list[str], list[str]]:
    """Check Python source bytes AST for showinfo filter uses without -nostats."""
    try:
        tree = ast.parse(data, filename=display_path)
    except (SyntaxError, ValueError) as exc:
        lineno = getattr(exc, "lineno", None) or 1
        text = getattr(exc, "msg", None) or str(exc)
        msg = f"{display_path}:{lineno}: python syntax error: {text}"
        return [msg], []

    doc_ids = collect_docstrings(tree)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, SCOPES):
            violations.extend(check_scope(node, doc_ids, display_path))
    return [], violations


def _display_path(path: pathlib.Path, repo_root: pathlib.Path | None) -> str:
    """Format path relative to repo_root when inside it, else as given."""
    if repo_root is not None:
        resolved_path = path.resolve()
        resolved_root = repo_root.resolve()
        if resolved_path.is_relative_to(resolved_root):
            return str(resolved_path.relative_to(resolved_root))
    return str(path)


def check_file(
    path: pathlib.Path, repo_root: pathlib.Path | None = None
) -> tuple[list[str], list[str]]:
    """Check a single shell, workflow, Makefile, or Python file on disk."""
    display_path = _display_path(path, repo_root)
    if is_shell_file(path):
        try:
            content = path.read_text(encoding="utf-8", errors="surrogateescape")
        except (OSError, UnicodeDecodeError) as exc:
            return [f"{path}:1: cannot read file: {exc}"], []
        return [], check_shell_content(content, display_path)
    if is_python(path):
        try:
            data = path.read_bytes()
        except OSError as exc:
            return [f"{path}:1: cannot read file: {exc}"], []
        return check_python_content(data, display_path)
    return [], []


SELF_TEST_CASES: tuple[tuple[str, str, str | bytes, str | None], ...] = (
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
    (
        "shell_semicolon_later_nostats",
        "test16.sh",
        f"ffmpeg -vf {SHOWINFO_NAME} -f null - ; ffmpeg -nostats -f null -\n",
        REASON,
    ),
    (
        "shell_and_earlier_nostats",
        "test17.sh",
        f"ffmpeg -nostats -i a b && ffmpeg -i b -vf {SHOWINFO_NAME} -f null -\n",
        REASON,
    ),
    (
        "shell_pipe_later_nostats",
        "test18.sh",
        f"ffmpeg -i a -vf {SHOWINFO_NAME} -f null - | ffmpeg -nostats -i - -f null -\n",
        REASON,
    ),
    (
        "shell_quoted_semicolon_with_nostats",
        "test19.sh",
        f'ffmpeg -nostats -filter_complex "[0:v]null;[0:v]{SHOWINFO_NAME}" -f null -\n',
        None,
    ),
    (
        "shell_redirect_with_nostats",
        "test20.sh",
        f"ffmpeg -nostats 2>&1 -vf {SHOWINFO_NAME} -f null - | tee log\n",
        None,
    ),
    (
        "shell_comment_nostats",
        "test21.sh",
        f"ffmpeg -vf {SHOWINFO_NAME} -f null - # -nostats\n",
        REASON,
    ),
    (
        "shell_trailing_comment_showinfo",
        "test22.sh",
        f"echo ok # see ffmpeg -vf {SHOWINFO_NAME}\n",
        None,
    ),
    (
        "shell_comma_space",
        "test23.sh",
        f'ffmpeg -vf "scale=1:1, {SHOWINFO_NAME}" -f null -\n',
        REASON,
    ),
    (
        "shell_quote_space",
        "test24.sh",
        f'ffmpeg -vf " {SHOWINFO_NAME}" -f null -\n',
        REASON,
    ),
    (
        "shell_filter_stream_specifier",
        "test25.sh",
        f"ffmpeg -filter:v:0 {SHOWINFO_NAME} -f null -\n",
        REASON,
    ),
    (
        "shell_continuation_lineno",
        "test26.sh",
        f"ffmpeg -i a \\\n  -vf {SHOWINFO_NAME} -f null -\n",
        f"test26.sh:2: {REASON}",
    ),
    (
        "python_two_calls_one_def",
        "test27.py",
        f'import subprocess\n\ndef f():\n    subprocess.run(["ffmpeg", "-nostats", "-i", "a", "b"])\n    subprocess.run(["ffmpeg", "-i", "b", "-vf", "{SHOWINFO_NAME}", "-f", "null", "-"])\n',
        REASON,
    ),
    (
        "python_module_level_with_nested_def",
        "test28.py",
        f'RUN = ["ffmpeg", "-vf", "{SHOWINFO_NAME}"]\n\ndef f():\n    return ["-nostats"]\n',
        REASON,
    ),
    (
        "python_chain_var_with_nostats",
        "test29.py",
        f'def f():\n    chain = "null,{SHOWINFO_NAME}"\n    run(["-nostats", "-vf", chain])\n',
        None,
    ),
    (
        "python_leading_comma",
        "test30.py",
        f'def f():\n    return ["-vf", "{SHOWINFO_NAME},null"]\n',
        REASON,
    ),
    (
        "python_leading_equals",
        "test31.py",
        f'def f():\n    return ["-vf", "{SHOWINFO_NAME}=checksum=0"]\n',
        REASON,
    ),
    (
        "python_sep_space",
        "test32.py",
        f'def f():\n    return ["-vf", "null, {SHOWINFO_NAME}"]\n',
        REASON,
    ),
    (
        "python_utf8_bom",
        "test33.py",
        b"\xef\xbb\xbf"
        + f'def f():\n    return ["-vf", "{SHOWINFO_NAME}"]\n'.encode("utf-8"),
        REASON,
    ),
    (
        "python_latin1_cookie",
        "test34.py",
        b"# -*- coding: latin-1 -*-\n# caf\xe9\n"
        + f'def f():\n    return ["-vf", "{SHOWINFO_NAME}"]\n'.encode("ascii"),
        REASON,
    ),
    (
        "python_invalid_bytes",
        "test35.py",
        b"x = '\xff'\n",
        "python syntax error",
    ),
    (
        "workflow_without_repo_root",
        ".github/workflows/no_root.yml",
        f"jobs:\n  t:\n    steps:\n      - run: ffmpeg -vf {SHOWINFO_NAME}\n",
        REASON,
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
        if is_scanned(p) != expected:
            print(
                f"FAIL self-test path '{rel}': expected scanned={expected}",
                file=sys.stderr,
            )
            failures += 1
    rel_wf = pathlib.Path(".github/workflows/r.yml")
    if not is_scanned(rel_wf):
        print(
            f"FAIL self-test relative path '{rel_wf}': expected scanned=True",
            file=sys.stderr,
        )
        failures += 1
    return failures


def _run_self_test_case(
    tmp_path: pathlib.Path,
    name: str,
    rel_path: str,
    content: str | bytes,
    expected_reason: str | None,
) -> bool:
    """Execute one planted self-test case and return True when it passes."""
    test_file = tmp_path / rel_path
    test_file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        test_file.write_bytes(content)
    else:
        test_file.write_text(content, encoding="utf-8")

    diags, violations = (
        check_file(test_file)
        if name == "workflow_without_repo_root"
        else check_file(test_file, tmp_path)
    )
    if set(diags) & set(violations):
        print(f"FAIL self-test '{name}': message reported twice: {diags}", file=sys.stderr)
        return False
    if expected_reason is None:
        if not diags and not violations:
            return True
        print(
            f"FAIL self-test '{name}': expected clean, got diags={diags} viols={violations}",
            file=sys.stderr,
        )
        return False

    if any(expected_reason in item for item in [*violations, *diags]):
        return True
    print(
        f"FAIL self-test '{name}': expected reason '{expected_reason}', got diags={diags} viols={violations}",
        file=sys.stderr,
    )
    return False


def run_self_test() -> int:
    """Run self-test on planted cases in a temporary directory."""
    cases = SELF_TEST_CASES
    failures = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        for name, rel_path, content, expected_reason in cases:
            if not _run_self_test_case(
                tmp_path, name, rel_path, content, expected_reason
            ):
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
