#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Refuse a call that names keyword K and also forwards its function's **kw.

A function `def f(..., **kw)` that calls `g(timeout=60, **kw)` breaks for any
caller that passes `timeout`: Python raises `TypeError: got multiple values for
keyword argument 'timeout'`, and an `except` written for g's own errors does
not catch it. CodeRabbit reported this in `_run_git` of
scripts/tracked_python.py on pull request 579.

A named keyword K is safe when the function removes K from kw before the
unpack: `kw.pop("K", ...)` or `del kw["K"]` at an earlier position, including
`g(K=kw.pop("K", d), **kw)`. The check does not follow control flow, so a
removal in a branch counts, and it does not follow a copy or alias of kw
(`opts = dict(kw)`), so an unpack of the copy is not checked. A pop with a
key that is not a string literal does not count. Removals and calls must share
their nearest function/lambda scope. Defaults and decorators evaluate in the
outer scope, even when the nested function binds kw. Literal dictionary keys
and keywords in `**dict(K=value)` also name K. A Semgrep
rule cannot tie the string in `kw.pop("timeout")` to the keyword name
`timeout`, so it would also report the safe forms; the stdlib AST can.

Usage:
    python3 -I scripts/check-python-kwargs-collision.py
    python3 -I scripts/check-python-kwargs-collision.py [path/to/file.py ...]
    python3 -I scripts/check-python-kwargs-collision.py --self-test
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
DESCRIPTION = "Refuse a call that names keyword K and also forwards its function's **kw."
FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
Position = tuple[int, int]

# (name, source, call lines expected to be reported; one per unremoved named keyword)
SELF_TEST_CASES: list[tuple[str, str, list[int]]] = [
    ("named_before", "def f(a, **kw):\n    return g(a, timeout=1, **kw)\n", [2]),
    ("named_after", "def f(**kw):\n    return g(**kw, timeout=1)\n", [2]),
    ("other_kwarg_name", "def f(**opts):\n    return g(check=True, **opts)\n", [2]),
    ("two_named", "def f(**kw):\n    return g(a=1, b=2, **kw)\n", [2, 2]),
    ("popped", "def f(**kw):\n    t = kw.pop('timeout', 1)\n    return g(timeout=t, **kw)\n", []),
    ("popped_bare", "def f(**kw):\n    kw.pop('timeout', None)\n    return g(timeout=1, **kw)\n", []),
    ("deleted", "def f(**kw):\n    del kw['timeout']\n    return g(timeout=1, **kw)\n", []),
    ("popped_other_key", "def f(**kw):\n    kw.pop('check', None)\n    return g(timeout=1, **kw)\n", [3]),
    ("one_of_two_popped",
     "def f(**kw):\n    kw.pop('timeout', None)\n    return g(timeout=1, check=True, **kw)\n", [3]),
    ("pop_after_call",
     "def f(**kw):\n    r = g(timeout=1, **kw)\n    kw.pop('timeout', None)\n    return r\n", [2]),
    ("pop_inline_before", "def f(**kw):\n    return g(timeout=kw.pop('timeout', 1), **kw)\n", []),
    ("pop_inline_after", "def f(**kw):\n    return g(**kw, timeout=kw.pop('timeout', 1))\n", [2]),
    ("not_a_kwarg_param", "def f(opts):\n    return g(timeout=1, **opts)\n", []),
    ("keyword_only", "def f(a, *, env=None):\n    return g(a, timeout=1, env=env)\n", []),
    ("unpack_only", "def f(**kw):\n    return g(**kw)\n", []),
    ("closure", "def f(**kw):\n    def inner():\n        return g(timeout=1, **kw)\n    return inner()\n", [3]),
    ("nested_rebinds",
     "def f(**kw):\n    kw.pop('timeout', None)\n    def inner(**kw):\n"
     "        return g(timeout=1, **kw)\n    return inner(**kw)\n", [4]),
    ("nested_own_kwarg_once",
     "def f(**kw):\n    def inner(**kw):\n        return g(timeout=1, **kw)\n    return inner(**kw)\n", [3]),
    ("nested_plain_param",
     "def f(**kw):\n    def inner(kw):\n        return g(timeout=1, **kw)\n    return inner({})\n", []),
    ("forwards_other_dict",
     "def f(**kw):\n    opts = {'check': True}\n    return g(timeout=1, **opts)\n", []),
    ("pop_variable_key",
     "def f(key, **kw):\n    kw.pop(key, None)\n    return g(timeout=1, **kw)\n", [3]),
    ("lambda", "h = lambda **kw: g(timeout=1, **kw)\n", [1]),
    ("super_init",
     "class C(B):\n    def __init__(self, **kwargs):\n        super().__init__(name='c', **kwargs)\n", [3]),
    ("async_def", "async def f(**kw):\n    return await g(timeout=1, **kw)\n", [2]),
    ("nested_pop",
     "def f(**kw):\n    def h():\n        kw.pop('t', None)\n    return g(t=1, **kw)\n", [4]),
    ("nested_default",
     "def f(**kw):\n    def h(kw=g(t=1, **kw)):\n        pass\n", [2]),
    ("nested_decorator",
     "def f(**kw):\n    @g(t=1, **kw)\n    def h(kw):\n        pass\n", [2]),
    ("dict_literal", "def f(**kw):\n    return g(**{'timeout': 1}, **kw)\n", [2]),
    ("dict_call", "def f(**kw):\n    return g(**dict(timeout=1), **kw)\n", [2]),
    ("dict_literal_popped_first",
     "def f(**kw):\n    kw.pop('timeout', None)\n    return g(**{'timeout': 1}, **kw)\n", []),
    ("dict_nonconst_key", "def f(key, **kw):\n    return g(**{key: 1}, **kw)\n", []),
    ("nested_binds_kw_no_forward_in_default",
     "def f(**kw):\n    def h(kw=None):\n        return g(t=1, **kw)\n", []),
    ("closure_pop_and_forward_inside_closure",
     "def f(**kw):\n    def h():\n        kw.pop('t', None)\n        return g(t=1, **kw)\n", []),
    ("outer_pop_before_closure",
     "def f(**kw):\n    kw.pop('t', None)\n    def h():\n        return g(t=1, **kw)\n", [4]),
    ("lambda_default",
     "def f(**kw):\n    return lambda kw=g(t=1, **kw): None\n", [2]),
    ("nested_keyword_default",
     "def f(**kw):\n    def h(*, kw=g(t=1, **kw)):\n        pass\n", [2]),
    ("nested_param_annotation",
     "def f(**kw):\n    def h(x: g(t=1, **kw)):\n        pass\n", [2]),
    ("nested_return_annotation",
     "def f(**kw):\n    def h() -> g(t=1, **kw):\n        pass\n", [2]),
    ("nested_star_annotation",
     "def f(**kw):\n    def h(*a: g(t=1, **kw), **kw: g(u=1, **kw)):\n        pass\n", [2, 2]),
]


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    """Every parameter name a function or lambda binds."""
    args = func.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    names.update(a.arg for a in (args.vararg, args.kwarg) if a is not None)
    return names


def _function_body(func: ast.AST) -> list[ast.AST]:
    """Body nodes without the definition-time expressions."""
    return [func.body] if isinstance(func, ast.Lambda) else func.body


def _definition_values(func: ast.AST) -> list[ast.AST]:
    """Defaults, decorators and annotations evaluated by a function's enclosing scope."""
    args = func.args
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    values = [*args.defaults, *args.kw_defaults, *getattr(func, "decorator_list", [])]
    values += [param.annotation for param in params if param is not None]
    values.append(getattr(func, "returns", None))
    return [node for node in values if node is not None]


def _scope_nodes(func: ast.AST, kwname: str) -> list[tuple[ast.AST, ast.AST]]:
    """Visible nodes with nearest function scope; nested defaults stay outside."""
    pending = [(node, func) for node in _function_body(func)]
    visible: list[tuple[ast.AST, ast.AST]] = []
    for _ in range(sum(1 for _node in ast.walk(func))):
        if not pending:
            break
        node, scope = pending.pop()
        visible.append((node, scope))
        if isinstance(node, FUNC_TYPES):
            pending.extend((child, scope) for child in _definition_values(node))
            if kwname not in _param_names(node):
                pending.extend((child, node) for child in _function_body(node))
        else:
            pending.extend((child, scope) for child in ast.iter_child_nodes(node))
    return visible


def _removal(node: ast.AST, kwname: str) -> list[tuple[str, Position]]:
    """Keys that node removes from kwname: kw.pop("K", ...) or del kw["K"]."""
    out: list[tuple[str, Position]] = []
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "pop" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == kwname and node.args
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        out.append((node.args[0].value, (node.lineno, node.col_offset)))
    if isinstance(node, ast.Delete):
        for target in node.targets:
            if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                    and target.value.id == kwname and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)):
                out.append((target.slice.value, (node.lineno, node.col_offset)))
    return out


def _forward_position(call: ast.Call, kwname: str) -> Position | None:
    """Position of the **kwname unpack in call, or None when call does not forward it."""
    for keyword in call.keywords:
        value = keyword.value
        if keyword.arg is None and isinstance(value, ast.Name) and value.id == kwname:
            return (value.lineno, value.col_offset)
    return None


def _dict_keys(value: ast.Dict) -> list[str]:
    """Only literal string keys name a keyword in a dictionary unpack."""
    return [key.value for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)]


def _keyword_names(keyword: ast.keyword) -> list[str]:
    """Known names from a keyword, literal dictionary or dict(k=v) unpack."""
    if keyword.arg is not None:
        return [keyword.arg]
    value = keyword.value
    if isinstance(value, ast.Dict):
        return _dict_keys(value)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
        return [item.arg for item in value.keywords if item.arg is not None]
    return []


def _call_keyword_names(call: ast.Call) -> list[str]:
    """All statically named keywords in a call."""
    return [name for keyword in call.keywords for name in _keyword_names(keyword)]


def _check_function(func: ast.AST, kwname: str, label: str, display: str) -> list[str]:
    """Findings for one function that takes **kwname."""
    nodes = _scope_nodes(func, kwname)
    removed: dict[ast.AST, list[tuple[str, Position]]] = {}
    for node, scope in nodes:
        removed.setdefault(scope, []).extend(_removal(node, kwname))
    findings: list[str] = []
    for call, scope in ((n, s) for n, s in nodes if isinstance(n, ast.Call)):
        unpack_at = _forward_position(call, kwname)
        if unpack_at is None:
            continue
        for name in _call_keyword_names(call):
            if any(k == name and at < unpack_at for k, at in removed[scope]):
                continue
            findings.append(
                f"{display}:{call.lineno}: {label}() names keyword '{name}' and forwards "
                f"**{kwname}; a caller that passes '{name}' gets a TypeError. Take "
                f"'{name}' as a parameter or remove it first with {kwname}.pop('{name}', ...)")
    return findings


def check_source(source: bytes, display: str) -> list[str]:
    """Findings for one file: a keyword collision or a parse failure."""
    try:
        tree = ast.parse(source, filename=display)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None) or 1
        return [f"{display}:{line}: cannot parse: {exc}"]
    findings: list[str] = []
    for func in ast.walk(tree):
        if isinstance(func, FUNC_TYPES) and func.args.kwarg is not None:
            label = getattr(func, "name", "<lambda>")
            findings.extend(_check_function(func, func.args.kwarg.arg, label, display))
    return findings


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
    print(f"python-kwargs-collision self-test: {len(SELF_TEST_CASES) + 1} cases ok")
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
        print(f"python-kwargs-collision: {exc}", file=sys.stderr)
        return 2
    findings = check_files(files)
    for finding in findings:
        print(finding)
    if findings:
        return 1
    print(f"python-kwargs-collision: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
