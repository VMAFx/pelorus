# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Provide self-test helpers used only by scripts/check-suppression-markers.py.

These helpers verify suppression marker checks and self-test wiring for
ADR-0241 and ADR-0256.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path


def formatter_self_test(
    findings: Callable[[str, str, frozenset[str]], list[str]],
    local_ids: frozenset[str],
) -> list[str]:
    """Refuse formatter comment variants while accepting literal marker text."""
    plants = (
        ("inline", "x = 1  # fmt: skip\n"),
        ("pair", "# fmt: off\nx = 1\n# fmt: on\n"),
        ("fmt on", "# fmt: on\nx = 1\n"),
        ("yapf", "# yapf: disable\nx = 1\n"),
        ("compact", "x = 1  #fmt:skip\n"),
        ("uppercase", "x = 1  # FMT: SKIP\n"),
        ("spaced colon", "# fmt : skip\nx = 1\n"),
        ("yapf enable", "# yapf: enable\nx = 1\n"),
        ("autopep8 off", "# autopep8: off\nx = 1\n"),
        ("autopep8 on", "# autopep8: on\nx = 1\n"),
    )
    failures: list[str] = []
    message = "formatter suppression comment refused (ADR-0256)"
    for label, source in plants:
        got = findings("formatter.py", source, local_ids)
        if not got or not all(message in item for item in got):
            failures.append(f"formatter '{label}' not refused: {got}")
        else:
            print(f"refused as expected (formatter {label}): {got[0]}")
    clean = 'text = "# fmt: skip"\n'
    if got := findings("literal.py", clean, local_ids):
        failures.append(f"formatter string literal refused: {got}")
    return failures


def nonpython_self_test(findings: Callable[[str, str], list[str]]) -> list[str]:
    """Refuse each planted marker shape in shell and C sources."""
    plants = (
        (
            "planted.sh",
            "run x  # nosemgrep: python.lang.a.b - why\n",
            "marker in a shell file",
        ),
        (
            "planted.c",
            "// explanation nosemgrep: rule.id - reason\n",
            "late marker in a C comment",
        ),
        (
            "planted.c",
            "call(); // reviewed nosemgrep\n",
            "bare late marker in a C comment",
        ),
        ("planted.c", "call(); // reviewed nosec\n", "bare late nosec in a C comment"),
        (
            "planted.c",
            "/* late bare nosemgrep */\n",
            "late bare nosemgrep in a C block comment",
        ),
        ("planted.c", "// late bare nosem\n", "late bare nosem in a C comment"),
        (
            "planted.c",
            "/* late bare nosemgrep */ do_work();\n",
            "nosemgrep before code on same line",
        ),
        ("planted.c", "// late bare nosem\r\n", "late bare nosem with CRLF"),
    )
    failures: list[str] = []
    for name, source, label in plants:
        if not findings(name, source):
            failures.append(f"planted '{label}' not refused")
        else:
            print(f"refused as expected ({label})")
    return failures


def self_test_names(source: str) -> frozenset[str]:
    """Return the names of module-level functions ending in _self_test."""
    return frozenset(
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name.endswith("_self_test")
    )


def wiring_failures(source: str, imported: frozenset[str] = frozenset()) -> list[str]:
    """Report each module-level *_self_test function that self_test never reaches.

    The walk follows direct calls by name with an explicit worklist bounded by
    the function count, so a helper reached through another helper counts.
    Direct calls to imported helpers mark those helpers as reached.
    """
    tree = ast.parse(source)
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    worklist = ["self_test"] if "self_test" in functions else []
    reached = set(worklist)
    for _ in range(len(functions)):
        if not worklist:
            break
        calls = {
            call.func.id
            for call in ast.walk(functions[worklist.pop()])
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        pending = sorted(calls.intersection(functions).difference(reached))
        reached.update(calls.intersection(imported))
        reached.update(pending)
        worklist.extend(pending)
    helpers = self_test_names(source) | imported
    return [
        f"{name} not reachable from self_test"
        for name in sorted(helpers.difference(reached))
    ]


def wiring_self_test(checker: Path) -> list[str]:
    """Check the checker's wiring and plant local and imported helper cases."""
    failures = wiring_failures(
        checker.read_text(encoding="utf-8"),
        self_test_names(Path(__file__).read_text(encoding="utf-8")),
    )
    dead = "def helper_self_test(): pass\ndef self_test(): pass\n"
    indirect = (
        "def helper_self_test(): pass\n"
        "def bridge(): helper_self_test()\n"
        "def self_test(): bridge()\n"
    )
    if wiring_failures(dead) != ["helper_self_test not reachable from self_test"]:
        failures.append("planted 'disconnected self-test helper' not refused")
    if wiring_failures(indirect):
        failures.append("planted 'indirectly reached self-test helper' refused")
    if wiring_failures("def self_test(): pass\n", frozenset({"helper_self_test"})) != [
        "helper_self_test not reachable from self_test"
    ]:
        failures.append("planted 'uncalled imported self-test helper' not refused")
    if wiring_failures(
        "def self_test(): helper_self_test()\n", frozenset({"helper_self_test"})
    ):
        failures.append("planted 'called imported self-test helper' refused")
    return failures
