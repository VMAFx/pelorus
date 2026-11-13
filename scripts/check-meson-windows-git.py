#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Require Windows guards and Meson timeouts above Git-scanning script deadlines.

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


def number(node: ast.AST | None) -> int | float | None:
    """A numeric literal, excluding booleans; None for anything else."""
    value = node.value if isinstance(node, ast.Constant) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def bind(numbers: dict[str, int | float | None], name: str, value: int | float | None) -> None:
    """Keep the largest numeric binding; any unreadable binding makes the name unreadable."""
    old = numbers.get(name, value)
    numbers[name] = None if value is None or old is None else max(old, value)


def module_numbers(tree: ast.Module) -> dict[str, int | float | None]:
    """Module-level bindings; None marks a name some binding gives no numeric literal."""
    numbers: dict[str, int | float | None] = {}
    for node in tree.body:
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            bind(numbers, node.target.id, None)
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bind(numbers, target.id, number(node.value))
    return numbers


def readable_names(tree: ast.Module) -> dict[str, int | float | None]:
    """Module bindings merged with every parameter default; a missing default is None."""
    names = module_numbers(tree)
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        args = func.args
        positional = [*args.posonlyargs, *args.args]
        defaults = [None] * (len(positional) - len(args.defaults)) + [*args.defaults]
        for arg, default in zip([*positional, *args.kwonlyargs], [*defaults, *args.kw_defaults], strict=True):
            bind(names, arg.arg, number(default))
    return names


def imports_tracked_python(tree: ast.Module) -> bool:
    """Recognize plain and from imports, including aliases."""
    return any(
        (isinstance(node, ast.Import) and any(a.name == "tracked_python" for a in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "tracked_python")
        for node in ast.walk(tree)
    )


def timeout_value(node: ast.AST, names: dict[str, int | float | None]) -> int | float | None:
    """A numeric literal or a name whose every binding is one; None when unreadable."""
    if isinstance(node, ast.Name):
        return names.get(node.id)
    return number(node)


def script_deadline(path: Path, scripts_dir: Path) -> tuple[int | float, list[str]]:
    """Over-approximate Git waits; also list timeout keywords with no readable value."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = readable_names(tree)
    deadlines: list[int | float] = [0]
    if imports_tracked_python(tree):
        tracked = scripts_dir / "tracked_python.py"
        shared = ast.parse(tracked.read_text(encoding="utf-8"), filename=str(tracked))
        deadline = module_numbers(shared).get("GIT_DEADLINE_S")
        if deadline is None:
            raise ValueError(f"{tracked}: GIT_DEADLINE_S is missing or rebound to a non-literal value")
        deadlines.append(deadline)
    timeouts = [
        keyword.value
        for node in ast.walk(tree) if isinstance(node, ast.Call)
        for keyword in node.keywords if keyword.arg == "timeout"
    ]
    unresolved: list[str] = []
    for value in timeouts:
        seconds = timeout_value(value, names)
        if seconds is None:
            unresolved.append(f"line {value.lineno} ({ast.unparse(value)})")
        else:
            deadlines.append(seconds)
    return max(deadlines), unresolved


def integer_timeout(tokens: list[Token]) -> int | None:
    """Join timeout tokens up to the next comma and require a decimal integer."""
    end = next((i for i, token in enumerate(tokens) if token.value == ","), len(tokens))
    value = "".join(t.value for t in tokens[:end])
    return int(value) if re.fullmatch(r"[0-9]+", value) else None


def meson_timeout(call: list[Token]) -> int | None:
    """Read a top-level integer timeout, joining digit tokens; unset means 30 s."""
    depth = 0
    for index in range(2, len(call) - 1):
        token = call[index]
        if token.kind == "operator":
            depth += (token.value in ("(", "[", "{")) - (token.value in (")", "]", "}"))
        if depth or token.kind != "word" or token.value != "timeout":
            continue
        if call[index + 1].value != ":":
            continue
        return integer_timeout(call[index + 2:-1])
    return 30


def test_findings(
    call: list[Token], gated: bool, self_test: bool, git_scripts: list[str], scripts_dir: Path,
) -> list[str]:
    """Check each possible Git script against its Windows gate and deadline."""
    findings: list[str] = []
    name = call[2].value.strip("'\"")
    for ref in git_scripts:
        if not gated and not self_test:
            findings.append(
                f"meson.build:{call[0].line}: test '{name}' runs scripts/{ref} "
                f"which calls git; gate it with {WINDOWS_GATE} "
                "(git is not on PATH on Windows CI)"
            )
        timeout = meson_timeout(call)
        deadline, unresolved = script_deadline(scripts_dir / ref, scripts_dir)
        findings.extend(
            f"meson.build:{call[0].line}: test '{name}' runs scripts/{ref} whose timeout at "
            f"{where} has no numeric literal value; use a literal, a module constant or a "
            "numeric parameter default"
            for where in unresolved
        )
        if timeout is None or timeout <= deadline or timeout <= 0:
            findings.append(
                f"meson.build:{call[0].line}: test '{name}' times out after {timeout} s "
                f"(Meson default 30 s when unset) but scripts/{ref} waits up to {deadline:g} s "
                f"for git; set timeout: above {deadline:g}"
            )
    return findings


def check(meson_build: Path, scripts_dir: Path) -> tuple[list[str], int, int]:
    """Return refusals, guarded scan count and timed scan/self-test count."""
    findings: list[str] = []
    count = timed = 0
    source = meson_build.read_text(encoding="utf-8")
    for call, gated, variables in meson_tests(source):
        options = {token.value.strip("'\"") for token in call if token.kind == "string"}
        self_test = bool(options & {"--self-test", "--self-test-only"})
        git_scripts = [
            ref
            for ref in script_refs(call, variables)
            if script_uses_git(scripts_dir / ref)
        ]
        count += int(gated and not self_test and bool(git_scripts))
        timed += bool(git_scripts)
        findings.extend(test_findings(call, gated, self_test, git_scripts, scripts_dir))
    return findings, count, timed


def self_test_cases() -> list[tuple[str, str, bool]]:
    """Windows guards, unsafe branches, literal and variable-backed scripts."""
    return literal_cases() + variable_cases() + timeout_cases()


def literal_cases() -> list[tuple[str, str, bool]]:
    """Windows guards and unsafe branches around literal script paths."""
    git_test = "test('scan', python, args: ['scripts' / 'scan.py'], timeout: 90)\n"
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
        "test('scan', python, args: ['-I', scan_script], timeout: 90)\n"
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
            "test('scan', python, args: ['-I', scan_script], timeout: 90)\n",
            True,
        ),
        (
            "p18 branch reassigned",
            "if enabled\nscan_script = 'scripts' / 'scan.py'\nendif\n"
            "scan_script = 'scripts' / 'plain.py'\n"
            "test('scan', python, args: ['-I', scan_script], timeout: 90)\n",
            False,
        ),
    ]


def timeout_cases() -> list[tuple[str, str, bool]]:
    """Git deadlines must expire before Meson, including guarded self-tests."""
    base = f"{WINDOWS_GATE}\ntest('deadline', python, args: ['scripts' / 'scan.py']{{}})\nendif\n"
    return [
        ("t1 missing timeout", base.format(""), True),
        ("t2 equal deadline", base.format(", timeout: 60"), True),
        ("t3 above deadline", base.format(", timeout: 90"), False),
        ("t4 literal deadline", base.format("").replace("scan.py", "literal.py"), True),
        ("t5 constant deadline", base.format(", timeout: 90").replace("scan.py", "constant.py"), True),
        ("t6 non-git", base.format("").replace("scan.py", "plain.py"), False),
        ("t7 self-test", base.format("").replace("'scan.py'", "'scan.py', '--self-test'"), True),
        ("t8 dynamic", base.format(", timeout: limit"), True),
        ("t9 unlimited", base.format(", timeout: 0"), True),
        ("t10 negative", base.format(", timeout: -1"), True),
        ("t11 float", base.format(", timeout: 90.0"), True),
        ("t12 expression", base.format(", timeout: 60 + 30"), True),
        ("t13 literal float", base.format(", timeout: 30").replace("scan.py", "float.py"), True),
        ("t14 annotated constant", base.format(", timeout: 90").replace("scan.py", "annotated.py"), True),
        ("t15 unused constant", base.format("").replace("scan.py", "unused.py"), False),
        ("t16 reassigned constant", base.format(", timeout: 90").replace("scan.py", "reassigned.py"), True),
        ("t17 timeout expression", base.format(", timeout: 150").replace("scan.py", "expression.py"), True),
        ("t18 rebound constant", base.format(", timeout: 90").replace("scan.py", "rebound.py"), True),
        ("t19 parameter default", base.format(", timeout: 90").replace("scan.py", "parameter.py"), True),
        ("t20 parameter default met", base.format(", timeout: 150").replace("scan.py", "parameter.py"), False),
        ("t21 parameter None", base.format(", timeout: 150").replace("scan.py", "parameter_none.py"), True),
    ]


UNREADABLE_SHARED_DEADLINES = (
    "OTHER = 60\n",
    "GIT_DEADLINE_S = '60'\n",
    "GIT_DEADLINE_S = 60\nGIT_DEADLINE_S = 120 + 0\n",
    "GIT_DEADLINE_S = 60\nGIT_DEADLINE_S += 60\n",
)


def shared_deadline_self_test(scripts: Path, meson: Path) -> bool:
    """A missing, nonnumeric or rebound shared deadline must produce a named failure."""
    shared = scripts / "tracked_python.py"
    meson.write_text(
        f"{WINDOWS_GATE}\ntest('shared', python, args: ['scripts' / 'scan.py'], timeout: 90)\nendif\n",
        encoding="utf-8",
    )
    for source in UNREADABLE_SHARED_DEADLINES:
        shared.write_text(source, encoding="utf-8")
        try:
            check(meson, scripts)
        except ValueError as exc:
            if "tracked_python.py" not in str(exc) or "GIT_DEADLINE_S" not in str(exc):
                print(f"FAIL: shared deadline not named: {exc}", file=sys.stderr)
                return False
        else:
            print(f"FAIL: unreadable GIT_DEADLINE_S accepted: {source!r}", file=sys.stderr)
            return False
    shared.write_text("GIT_DEADLINE_S: float = 60.0\n", encoding="utf-8")
    return not check(meson, scripts)[0]


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
            "tracked_python.py": "GIT_DEADLINE_S = 60\n",
            "literal.py": "run(['git'], timeout=30)\n",
            "constant.py": "GIT_TIMEOUT_S = 120\nrun(['git'], timeout=GIT_TIMEOUT_S)\n",
            "float.py": "run(['git'], timeout=30.5)\n",
            "annotated.py": "git_deadline: float = 120.5\nrun(['git'], timeout=git_deadline)\n",
            "unused.py": "UNUSED_TIMEOUT = 120\nrun(['git'])\n",
            "reassigned.py": "GIT_TIMEOUT = 120\nrun(['git'], timeout=GIT_TIMEOUT)\nGIT_TIMEOUT = 10\n",
            "expression.py": "GIT_TIMEOUT = 60\nrun(['git'], timeout=GIT_TIMEOUT * 2)\n",
            "rebound.py": "GIT_TIMEOUT = 60\nrun(['git'], timeout=GIT_TIMEOUT)\nGIT_TIMEOUT = 120 + 0\n",
            "parameter.py": "def run_git(timeout=120):\n    run(['git'], timeout=timeout)\n",
            "parameter_none.py": "def run_git(timeout=None):\n    run(['git'], timeout=timeout)\n",
        }
        for name, source in fixtures.items():
            (scripts / name).write_text(source, encoding="utf-8")
        meson = root / "meson.build"
        for label, source, refused in cases:
            meson.write_text(source, encoding="utf-8")
            findings, count, timed = check(meson, scripts)
            expected_count = int(label in {"p2 gated", "p6 nested", "p13 variable gated"})
            expected_count += int(label.startswith("t") and label not in {"t6 non-git", "t7 self-test"})
            expected_timed = int(label not in {
                "p4 no git", "p14 variable no git", "p16 later git assignment",
                "p18 branch reassigned", "t6 non-git",
            })
            if bool(findings) != refused or count != expected_count or timed != expected_timed:
                print(f"FAIL: {label}: {findings}, gated={count}, timed={timed}", file=sys.stderr)
                return 1
        if not shared_deadline_self_test(scripts, meson):
            return 1
    print(f"check-meson-windows-git: self-test ({len(cases) + len(UNREADABLE_SHARED_DEADLINES) + 1} cases) ok")
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
        findings, count, timed = check(args.meson_build, args.scripts_dir)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        print(f"check-meson-windows-git: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("\n".join(findings), file=sys.stderr)
        return 1
    print(f"check-meson-windows-git: {count} git-scanning tests gated; {timed} timed tests checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
