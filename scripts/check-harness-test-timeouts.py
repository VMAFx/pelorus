#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that patch-harness test() targets in meson.build have sufficient timeouts.

Each patch harness test executes strict C builds and runs via scripts/patch_harness.py,
which enforces BUILD_TIMEOUT_S (120 s) and RUN_TIMEOUT_S (30 s) deadlines per call
(150 s total). If meson.build leaves a harness test at Meson's default 30 s timeout,
Meson kills the test process before the harness deadline handler can report the
underlying compiler or binary stall (HISS-02, PR #487).

This checker parses meson.build, identifies every test() whose arguments reference a
script under scripts/test-*.py that imports or loads patch_harness, and requires:
  - An explicit timeout: attribute
  - A timeout >= 150 s (the minimum deadline for a single build_and_run cycle).

Usage:
    python3 -I scripts/check-harness-test-timeouts.py [path/to/meson.build]
    python3 -I scripts/check-harness-test-timeouts.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

DEFAULT_MESON_BUILD = pathlib.Path(__file__).resolve().parent.parent / "meson.build"
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
MIN_HARNESS_TIMEOUT_S = 150
DESCRIPTION = "Check that patch-harness tests in meson.build declare sufficient timeouts."


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


def script_loads_patch_harness(script_path: pathlib.Path) -> bool:
    """Return True if script_path imports or loads patch_harness."""
    try:
        text = script_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "patch_harness" in text and (
        "import patch_harness" in text
        or "from patch_harness" in text
        or 'spec_from_file_location("patch_harness"' in text
        or "spec_from_file_location('patch_harness'" in text
        or "patch_harness.py" in text
    )


def check_meson_content(
    content: str, scripts_dir: pathlib.Path
) -> tuple[int, list[str]]:
    """Check test() timeout declarations in content.

    Returns (inspected_count, errors).
    """
    errors: list[str] = []
    harness_tests_found = 0

    for test_block in parse_meson_tests(content):
        # Strip comments so commented timeout or args are not picked up
        clean_block = re.sub(r"#.*", "", test_block)

        m_name = re.search(r"test\s*\(\s*['\"]([^'\"]+)['\"]", clean_block)
        test_name = m_name.group(1) if m_name else "<unknown>"

        # Find scripts referenced in test args
        script_refs = re.findall(
            r"['\"](?:scripts[/\\])?(test-[a-zA-Z0-9_-]+\.py)['\"]", clean_block
        )
        if not script_refs:
            continue

        for script_name in script_refs:
            script_path = scripts_dir / script_name
            if not script_path.is_file():
                fallback_path = SCRIPTS_DIR / script_name
                if fallback_path.is_file():
                    script_path = fallback_path

            if not script_loads_patch_harness(script_path):
                continue

            harness_tests_found += 1
            m_timeout = re.search(r"\btimeout\s*:\s*(\d+)", clean_block)
            timeout = int(m_timeout.group(1)) if m_timeout else None

            if timeout is None:
                errors.append(
                    f"test '{test_name}' ({script_name}) has no timeout: "
                    f"(needs >= {MIN_HARNESS_TIMEOUT_S} s for patch_harness)"
                )
            elif timeout < MIN_HARNESS_TIMEOUT_S:
                errors.append(
                    f"test '{test_name}' ({script_name}) timeout {timeout} s "
                    f"< {MIN_HARNESS_TIMEOUT_S} s (one build+run deadline)"
                )

    return harness_tests_found, errors


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
    ]


def run_self_test(scripts_dir: pathlib.Path) -> int:
    """Verify that planted defective snippets fail and valid snippets pass."""
    cases = SELF_TEST_CASES
    failures = 0
    for name, snippet, expected_pass in cases:
        _, errs = check_meson_content(snippet, scripts_dir)
        passed = len(errs) == 0
        if passed != expected_pass:
            print(
                f"self-test FAIL: case '{name}': got passed={passed}, expected={expected_pass}; errors: {errs}",
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
        f"check-harness-test-timeouts: {count} patch-harness tests checked in {args.meson_file.name}, all timeouts valid"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
