#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Require Windows guards for Meson tests that scan the Git tree.

Only reads Meson and Python source; never runs Git or imports checked scripts.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
WINDOWS_GATE = "if build_machine.system() != 'windows'"
TOKEN = re.compile(
    r"(?P<comment>\#[^\n]*)|"
    r"(?P<string>'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|"
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")|"
    r"(?P<newline>\n)|(?P<space>[ \t\r]+)|"
    r"(?P<word>[A-Za-z_]\w*)|(?P<operator>!=|==|.)"
)


@dataclass(frozen=True)
class Token:
    """A Meson lexeme with its original line and string/word distinction."""

    value: str
    kind: str
    line: int


def meson_statements(source: str) -> Iterator[list[Token]]:
    """Split statements at newlines outside balanced delimiters and strings."""
    statement: list[Token] = []
    delimiters: list[str] = []
    line = 1
    for match in TOKEN.finditer(source):
        value, kind = match.group(), match.lastgroup or ""
        if kind == "operator":
            if value in ("(", "[", "{"):
                delimiters.append({"(": ")", "[": "]", "{": "}"}[value])
            elif value in (")", "]", "}"):
                if not delimiters or delimiters.pop() != value:
                    raise ValueError(f"meson.build:{line}: unbalanced delimiter")
        if kind not in {"comment", "space", "newline"}:
            statement.append(Token(value, kind, line))
        if kind == "newline" and not delimiters and statement:
            yield statement
            statement = []
        line += value.count("\n")
    if delimiters:
        raise ValueError(f"meson.build:{line}: unclosed delimiter")
    if statement:
        yield statement


def is_windows_gate(condition: list[Token]) -> bool:
    """Recognize the explicit build-machine Windows exclusion."""
    values = [token.value.strip("'\"") for token in condition]
    return (
        values == ["build_machine", ".", "system", "(", ")", "!=", "windows"]
        and condition[-1].kind == "string"
    )


def test_calls(statement: list[Token]) -> Iterator[list[Token]]:
    """Extract every test(...) call, retaining nested parentheses."""
    for index, token in enumerate(statement[:-1]):
        if token.kind != "word" or token.value != "test":
            continue
        if statement[index + 1].value != "(":
            continue
        depth = 0
        for end in range(index + 1, len(statement)):
            item = statement[end]
            if item.kind == "operator":
                depth += (item.value == "(") - (item.value == ")")
            if depth == 0:
                yield statement[index : end + 1]
                break


Bindings = dict[str, tuple[str, ...]]


def bind_script(statement: list[Token], variables: Bindings, conditional: bool) -> None:
    """Record the scripts a `name = <path>` assignment may bind.

    An assignment inside an if/elif/else branch may or may not run, so it adds
    to the earlier bindings; an unconditional one replaces them. The result
    over-approximates: every script a test may run on some branch is checked.
    """
    head = statement[0]
    if not (
        len(statement) >= 3
        and head.kind == "word"
        and statement[1].kind == "operator"
        and statement[1].value == "="
        and statement[2].kind in {"word", "string"}
    ):
        return
    refs = tuple(script_refs(statement[2:], variables))
    if conditional:
        refs = tuple(dict.fromkeys(variables.get(head.value, ()) + refs))
    if refs:
        variables[head.value] = refs
    else:
        variables.pop(head.value, None)


def meson_tests(source: str) -> Iterator[tuple[list[Token], bool, Bindings]]:
    """Yield calls with source-order bindings and unchanged if/elif/else gates."""
    gates: list[bool] = []
    variables: Bindings = {}
    for statement in meson_statements(source):
        head = statement[0]
        if head.kind == "word" and head.value == "if":
            gates.append(is_windows_gate(statement[1:]))
        elif head.kind == "word" and head.value in {"elif", "else", "endif"}:
            if not gates:
                raise ValueError(f"meson.build:{head.line}: unmatched {head.value}")
            if head.value == "endif":
                gates.pop()
            else:
                gates[-1] = head.value == "elif" and is_windows_gate(statement[1:])
        else:
            bind_script(statement, variables, bool(gates))
            for call in test_calls(statement):
                yield call, any(gates), variables.copy()
    if gates:
        raise ValueError("meson.build: unclosed if block")


def script_refs(call: list[Token], variables: Bindings | None = None) -> list[str]:
    """Find literal script paths and names bound to those paths in a test call."""
    refs: list[str] = []
    for index in range(len(call) - 2):
        directory, slash, filename = call[index : index + 3]
        if (
            directory.kind == filename.kind == "string"
            and directory.value.strip("'\"") == "scripts"
            and slash.kind == "operator"
            and slash.value == "/"
        ):
            name = filename.value.strip("'\"")
            if name.endswith(".py"):
                refs.append(name)
    if variables:
        for token in call:
            if token.kind == "word":
                refs.extend(variables.get(token.value, ()))
    return list(dict.fromkeys(refs))


def command_literals(node: ast.AST) -> Iterator[ast.AST]:
    """Read command literals in calls/assignments, excluding prose and fixtures."""
    values: list[ast.AST] = []
    if isinstance(node, ast.Call):
        values = list(node.args) + [keyword.value for keyword in node.keywords]
    elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
        values = [node.value]
    for value in values:
        if isinstance(value, (ast.List, ast.Tuple)):
            yield from value.elts
        else:
            yield value


def script_uses_git(path: Path) -> bool:
    """Detect actual tracked-file references or Git command literals via AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {
            "tracked_python",
            "tracked_files",
        }:
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == "tracked_python" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "tracked_python":
            return True
        if any(
            isinstance(value, ast.Constant) and value.value == "git"
            for value in command_literals(node)
        ):
            return True
    return False


def check(meson_build: Path, scripts_dir: Path) -> tuple[list[str], int]:
    """Return refusals and the number of guarded Git-scanning test calls."""
    findings: list[str] = []
    count = 0
    source = meson_build.read_text(encoding="utf-8")
    for call, gated, variables in meson_tests(source):
        options = {token.value.strip("'\"") for token in call if token.kind == "string"}
        if options & {"--self-test", "--self-test-only"}:
            continue
        name = call[2].value.strip("'\"")
        git_scripts = [
            ref
            for ref in script_refs(call, variables)
            if script_uses_git(scripts_dir / ref)
        ]
        if gated:
            count += bool(git_scripts)
            continue
        for ref in git_scripts:
            findings.append(
                f"meson.build:{call[0].line}: test '{name}' runs scripts/{ref} "
                f"which calls git; gate it with {WINDOWS_GATE} "
                "(git is not on PATH on Windows CI)"
            )
    return findings, count


def self_test_cases() -> list[tuple[str, str, bool]]:
    """Windows guards, unsafe branches, literal and variable-backed scripts."""
    return literal_cases() + variable_cases()


def literal_cases() -> list[tuple[str, str, bool]]:
    """Windows guards and unsafe branches around literal script paths."""
    git_test = "test('scan', python, args: ['scripts' / 'scan.py'])\n"
    return [
        ("p1 ungated", git_test, True),
        ("p2 gated", f"{WINDOWS_GATE}\n{git_test}endif\n", False),
        (
            "p3 self-test",
            git_test.replace("'scan.py'", "'scan.py', '--self-test'"),
            False,
        ),
        ("p4 no git", git_test.replace("scan.py", "plain.py"), False),
        ("p5 else", f"{WINDOWS_GATE}\nelse\n{git_test}endif\n", True),
        ("p6 nested", f"{WINDOWS_GATE}\nif enabled\n{git_test}endif\nendif\n", False),
        ("p7 elif", f"{WINDOWS_GATE}\nelif enabled\n{git_test}endif\n", True),
        (
            "p8 strings",
            "# test('comment', '(')\n"
            + git_test.replace("python,", "find_program('python'),"),
            True,
        ),
        ("p9 command", git_test.replace("scan.py", "command.py"), True),
        ("p10 which", git_test.replace("scan.py", "which.py"), True),
        (
            "p11 self-test-only",
            git_test.replace("'scan.py'", "'scan.py', '--self-test-only'"),
            False,
        ),
    ]


def variable_cases() -> list[tuple[str, str, bool]]:
    """Scripts bound to Meson variables, in source order and across branches."""
    variable_test = (
        "scan_script = meson.project_source_root() / 'scripts' / 'scan.py'\n"
        "test('scan', python, args: ['-I', scan_script])\n"
    )
    return [
        ("p12 variable ungated", variable_test, True),
        (
            "p13 variable gated",
            variable_test.replace("test('scan'", f"{WINDOWS_GATE}\ntest('scan'")
            + "endif\n",
            False,
        ),
        ("p14 variable no git", variable_test.replace("scan.py", "plain.py"), False),
        (
            "p15 later no-git assignment",
            variable_test + "scan_script = 'scripts' / 'plain.py'\n",
            True,
        ),
        (
            "p16 later git assignment",
            variable_test.replace("scan.py", "plain.py")
            + "scan_script = 'scripts' / 'scan.py'\n",
            False,
        ),
        (
            "p17 branch assignments",
            "if enabled\nscan_script = 'scripts' / 'scan.py'\nelse\n"
            "scan_script = 'scripts' / 'plain.py'\nendif\n"
            "test('scan', python, args: ['-I', scan_script])\n",
            True,
        ),
        (
            "p18 branch reassigned",
            "if enabled\nscan_script = 'scripts' / 'scan.py'\nendif\n"
            "scan_script = 'scripts' / 'plain.py'\n"
            "test('scan', python, args: ['-I', scan_script])\n",
            False,
        ),
    ]


def run_self_test() -> int:
    """Plant Windows guards, unsafe branches, nested calls and false positives."""
    cases = self_test_cases()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scripts = root / "scripts"
        scripts.mkdir()
        fixtures = {
            "scan.py": "import tracked_python\n",
            "plain.py": '# tracked_files\nTEXT = "tracked_python"\n',
            "command.py": 'COMMAND = ["git", "ls-files"]\n',
            "which.py": 'import shutil\nshutil.which("git")\n',
        }
        for name, source in fixtures.items():
            (scripts / name).write_text(source, encoding="utf-8")
        meson = root / "meson.build"
        for label, source, refused in cases:
            meson.write_text(source, encoding="utf-8")
            findings, count = check(meson, scripts)
            expected_count = int(
                label in {"p2 gated", "p6 nested", "p13 variable gated"}
            )
            if bool(findings) != refused or count != expected_count:
                print(f"FAIL: {label}: {findings}, gated={count}", file=sys.stderr)
                return 1
    print(f"check-meson-windows-git: self-test ({len(cases)} cases) ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the source-only check or its planted self-test cases."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meson-build", type=Path, default=ROOT / "meson.build")
    parser.add_argument("--scripts-dir", type=Path, default=ROOT / "scripts")
    parser.add_argument("--self-test", action="store_true", help="Run planted cases")
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            return run_self_test()
        findings, count = check(args.meson_build, args.scripts_dir)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        print(f"check-meson-windows-git: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    print(f"check-meson-windows-git: {count} git-scanning tests gated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
