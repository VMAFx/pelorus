#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that script test() targets in meson.build have sufficient timeouts.

Covers every script test whose script defines module-level subprocess deadline
constants or loads patch_harness. When a meson test() runs a script whose own
subprocess deadline exceeds Meson's 30 s default (or declared timeout:), Meson
kills the test before the script can report the underlying stall (HISS-02).

This checker parses meson.build, identifies every test() whose arguments reference
a script under scripts/**/*.py, and requires:
  - An explicit timeout: attribute if required deadline exceeds Meson's 30 s default
  - A timeout >= max(patch_harness floor, script deadline constants).

Usage:
    python3 -I scripts/check-harness-test-timeouts.py [path/to/meson.build]
    python3 -I scripts/check-harness-test-timeouts.py --self-test
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
import tempfile

DEFAULT_MESON_BUILD = pathlib.Path(__file__).resolve().parent.parent / "meson.build"
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
MIN_HARNESS_TIMEOUT_S = 150
TIMEOUT_CONST_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*TIMEOUT[A-Z0-9_]*)\s*=\s*([0-9.]+)", re.MULTILINE
)
DESCRIPTION = "Check that script tests in meson.build declare sufficient timeouts."


def parse_meson_tests(content: str) -> list[str]:
    """Extract individual test(...) invocation text blocks from Meson source."""
    tests: list[str] = []
    for m in re.finditer(r"\btest\s*\(", content):
        start = m.start()
        i = m.end()
        depth = 1
        in_str: str | None = None
        in_comment = False
        while i < len(content) and depth > 0:
            c = content[i]
            if in_comment:
                if c == "\n":
                    in_comment = False
            elif in_str:
                if c == "\\":
                    i += 1
                elif c == in_str:
                    in_str = None
            else:
                if c == "#":
                    in_comment = True
                elif c in ("'", '"'):
                    in_str = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
            i += 1
        tests.append(content[start:i])
    return tests


def extract_script_refs(clean_block: str) -> list[str]:
    """Find scripts/**/*.py paths referenced in a test() block."""
    refs: list[str] = []
    chain_pat = re.compile(
        r"['\"]scripts['\"](?:\s*/\s*['\"][^'\"]+['\"])+"
    )
    for m in chain_pat.finditer(clean_block):
        parts = re.findall(r"['\"]([^'\"]+)['\"]", m.group(0))
        if parts[-1].endswith(".py"):
            refs.append("/".join(parts[1:]))
    direct_pat = re.compile(r"['\"]scripts[/\\]([a-zA-Z0-9_/.-]+\.py)['\"]")
    for m in direct_pat.finditer(clean_block):
        refs.append(m.group(1))
    return list(dict.fromkeys(refs))


def _names_patch_harness(node: ast.AST) -> bool:
    """Return True if node imports patch_harness or loads it by module spec."""
    if isinstance(node, ast.Import):
        return any(alias.name == "patch_harness" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module == "patch_harness"
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        first = node.args[0]
        return (
            name == "spec_from_file_location"
            and isinstance(first, ast.Constant)
            and first.value == "patch_harness"
        )
    return False


def script_loads_patch_harness(script_path: pathlib.Path) -> bool:
    """Return True if script_path imports or loads patch_harness.

    Reads the syntax tree, so a script that only names patch_harness in a
    string or comment (this checker does) is not counted as loading it.
    """
    try:
        text = script_path.read_text(encoding="utf-8")
    except OSError:
        return False
    except UnicodeDecodeError as err:
        # A script the gate cannot read must fail it, not drop out of the check.
        raise SystemExit(
            f"check-harness-test-timeouts: {script_path} is not UTF-8: {err}"
        ) from err
    try:
        tree = ast.parse(text, filename=str(script_path))
    except SyntaxError as err:
        raise SystemExit(
            f"check-harness-test-timeouts: {script_path} does not parse: {err}"
        ) from err
    return any(_names_patch_harness(node) for node in ast.walk(tree))


def script_deadline_s(script_path: pathlib.Path) -> float | None:
    """Return max module-level TIMEOUT constant in seconds, or None if none."""
    try:
        text = script_path.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError as err:
        raise SystemExit(
            f"check-harness-test-timeouts: {script_path} is not UTF-8: {err}"
        ) from err

    deadlines: list[float] = []
    for m in TIMEOUT_CONST_RE.finditer(text):
        name = m.group(1)
        if "MIN_" in name or "CAP" in name or "MAX_" in name:
            continue
        val = float(m.group(2))
        if name.endswith("_MINUTES"):
            val *= 60.0
        deadlines.append(val)
    return max(deadlines) if deadlines else None


def _validate_script_timeout(
    test_name: str,
    script_name: str,
    timeout: int | None,
    is_harness: bool,
    deadline: float | None,
    required: float,
) -> str | None:
    """Return error message if timeout declaration is insufficient, else None."""
    if required <= 30:
        return None

    if is_harness and (deadline is None or deadline <= MIN_HARNESS_TIMEOUT_S):
        if timeout is None:
            return (
                f"test '{test_name}' ({script_name}) has no timeout: "
                f"(needs >= {MIN_HARNESS_TIMEOUT_S} s for patch_harness)"
            )
        if timeout < MIN_HARNESS_TIMEOUT_S:
            return (
                f"test '{test_name}' ({script_name}) timeout {timeout} s "
                f"< {MIN_HARNESS_TIMEOUT_S} s (one build+run deadline)"
            )
        return None

    c_val = int(required) if isinstance(required, float) and required.is_integer() else required
    if timeout is None:
        return (
            f"test '{test_name}' ({script_name}) has no timeout: "
            f"(constant {c_val} s exceeds default 30 s, declared timeout: None)"
        )
    if timeout < required:
        return (
            f"test '{test_name}' ({script_name}) timeout {timeout} s "
            f"< {c_val} s (script deadline constant {c_val} s)"
        )
    return None


def check_meson_content(
    content: str, scripts_dir: pathlib.Path
) -> tuple[int, list[str]]:
    """Check test() timeout declarations in content.

    Returns (inspected_count, errors).
    """
    errors: list[str] = []
    tests_found = 0

    for test_block in parse_meson_tests(content):
        clean_block = re.sub(r"#.*", "", test_block)

        m_name = re.search(r"test\s*\(\s*['\"]([^'\"]+)['\"]", clean_block)
        test_name = m_name.group(1) if m_name else "<unknown>"

        script_refs = extract_script_refs(clean_block)
        if not script_refs:
            continue

        tests_found += 1
        m_timeout = re.search(r"\btimeout\s*:\s*(\d+)", clean_block)
        timeout = int(m_timeout.group(1)) if m_timeout else None

        for script_name in script_refs:
            script_path = scripts_dir / script_name
            if not script_path.is_file():
                fallback_path = SCRIPTS_DIR / script_name
                if fallback_path.is_file():
                    script_path = fallback_path

            is_harness = script_loads_patch_harness(script_path)
            deadline = script_deadline_s(script_path)
            harness_req = MIN_HARNESS_TIMEOUT_S if is_harness else 0
            required = max(harness_req, deadline or 0.0)

            err = _validate_script_timeout(
                test_name, script_name, timeout, is_harness, deadline, required
            )
            if err:
                errors.append(err)

    return tests_found, errors


# (name, planted meson.build snippet, expected to pass)
SELF_TEST_CASES = [
    (
        "missing timeout",
        """
        test('planted-harness-no-timeout',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-nvenc-me-hints.py'],
             workdir: meson.project_source_root(),
             suite: 'fast')
        """,
        False,
    ),
    (
        "timeout: 300 (valid)",
        """
        test('planted-harness-valid-timeout',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-nvenc-me-hints.py'],
             workdir: meson.project_source_root(),
             timeout: 300,
             suite: 'fast')
        """,
        True,
    ),
    (
        "timeout: 60 (under 150 s floor)",
        """
        test('planted-harness-short-timeout',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-nvenc-me-hints.py'],
             workdir: meson.project_source_root(),
             timeout: 60,
             suite: 'fast')
        """,
        False,
    ),
    (
        "non-harness test without timeout",
        """
        test('planted-non-harness',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-grain-accumulator-bounds.py'],
             workdir: meson.project_source_root(),
             suite: 'fast')
        """,
        True,
    ),
    (
        "deadline script without timeout (fails: 90 s > 30 s default)",
        """
        test('planted-deadline-no-timeout',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-deadline-90.py'],
             workdir: meson.project_source_root(),
             suite: 'fast')
        """,
        False,
    ),
    (
        "deadline script with timeout: 120 >= 90 (valid)",
        """
        test('planted-deadline-valid-timeout',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-deadline-90.py'],
             workdir: meson.project_source_root(),
             timeout: 120,
             suite: 'fast')
        """,
        True,
    ),
    (
        "deadline script boundary 30 s without timeout (valid: <= 30 s default)",
        """
        test('planted-deadline-boundary-30',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-deadline-30.py'],
             workdir: meson.project_source_root(),
             suite: 'fast')
        """,
        True,
    ),
    (
        "deadline script with timeout: 89 < 90 (fails)",
        """
        test('planted-deadline-under-timeout',
             python_prog,
             args: [meson.project_source_root() / 'scripts' / 'test-deadline-90.py'],
             workdir: meson.project_source_root(),
             timeout: 89,
             suite: 'fast')
        """,
        False,
    ),
]


def run_self_test(scripts_dir: pathlib.Path) -> int:
    """Verify that planted defective snippets fail and valid snippets pass."""
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = pathlib.Path(tmp_dir_str)
        (tmp_dir / "test-deadline-90.py").write_text(
            "FOO_TIMEOUT_S = 90\n", encoding="utf-8"
        )
        (tmp_dir / "test-deadline-30.py").write_text(
            "FOO_TIMEOUT_S = 30\n", encoding="utf-8"
        )

        cases = SELF_TEST_CASES
        failures = 0
        for name, snippet, expected_pass in cases:
            _, errs = check_meson_content(snippet, tmp_dir)
            passed = len(errs) == 0
            if passed != expected_pass:
                print(
                    f"self-test FAIL: case '{name}': got passed={passed}, "
                    f"expected={expected_pass}; errors: {errs}",
                    file=sys.stderr,
                )
                failures += 1

        if failures:
            print(
                f"check-harness-test-timeouts self-test: {failures} failures",
                file=sys.stderr,
            )
            return 1

        print(f"check-harness-test-timeouts self-test: {len(cases)} cases ok")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test on planted meson snippets",
    )
    parser.add_argument(
        "meson_file",
        nargs="?",
        type=pathlib.Path,
        default=DEFAULT_MESON_BUILD,
        help="Path to meson.build to inspect (default: project root meson.build)",
    )
    args = parser.parse_args(argv)

    scripts_dir = SCRIPTS_DIR
    if args.meson_file and args.meson_file.is_file():
        candidate_dir = args.meson_file.resolve().parent / "scripts"
        if candidate_dir.is_dir():
            scripts_dir = candidate_dir

    if args.self_test:
        return run_self_test(scripts_dir)

    if not args.meson_file.is_file():
        print(f"ERROR: meson file not found: {args.meson_file}", file=sys.stderr)
        return 2

    content = args.meson_file.read_text(encoding="utf-8")
    count, errors = check_meson_content(content, scripts_dir)

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    print(
        f"check-harness-test-timeouts: {count} script tests checked in {args.meson_file.name}, all timeouts valid"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
