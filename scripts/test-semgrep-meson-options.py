#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the Meson options file name Semgrep rule.

Checks that `pelorus-meson-options-file` in `.semgrep.yml`:
(0) still has a reason to exist: meson.build declares a meson_version floor
    below 1.1, the first Meson that reads the newer options file name; once
    the floor reaches 1.1 this test fails so the rule and the test go;
(a) reports the newer options file name in Python, shell and meson.build
    files, while allowing meson_options.txt;
(b) catches a planted regression in scripts/test-check-tester-kit-install.py
    when the tiny project writes the newer name again;
(c) produces zero findings across the tracked tree while confirming that
    scripts/test-check-tester-kit-install.py was scanned, and no directory
    with a scanned meson.build holds a file under the newer name.

The newer name is assembled from two parts below, so this file does not
match the rule it tests.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile

# python3 -I drops the script directory from sys.path: load the sibling by path.
_SPEC = importlib.util.spec_from_file_location(
    "semgrep_harness", pathlib.Path(__file__).with_name("semgrep_harness.py")
)
semgrep_harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(semgrep_harness)
ScanError = semgrep_harness.ScanError
run_semgrep = semgrep_harness.run_semgrep
resolve_executable = semgrep_harness.resolve_executable
scrubbed_env = semgrep_harness.scrubbed_env

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULE_FILE = ROOT / ".semgrep.yml"
MESON_BUILD = ROOT / "meson.build"
SELFTEST_REL = "scripts/test-check-tester-kit-install.py"
SELFTEST = ROOT / SELFTEST_REL
RULE_ID = "pelorus-meson-options-file"
NEW_NAME = "meson" + ".options"
FIRST_READER = (1, 1)

PLANTED = {
    "scripts/planted_options.py": (
        "from pathlib import Path\n"
        "\n"
        "def write_options(src: Path, text: str) -> None:\n"
        f'    (src / "{NEW_NAME}").write_text(text)  # reported\n'
        '    (src / "meson_options.txt").write_text(text)\n'
    ),
    "scripts/planted_options.sh": (
        "#!/bin/sh\n"
        f'cp opts.txt "$1/{NEW_NAME}"  # reported\n'
        'cp opts.txt "$1/meson_options.txt"\n'
    ),
    "sub/meson.build": (
        f"install_data('{NEW_NAME}')  # reported\n"
        "install_data('meson_options.txt')\n"
    ),
}

# Expected (path, line) pairs follow the `reported` comments.
EXPECTED_HITS = sorted(
    (rel, n)
    for rel, text in PLANTED.items()
    for n, line in enumerate(text.splitlines(), 1)
    if "# reported" in line
)


def rule_results(data: dict) -> list[dict]:
    """Findings of RULE_ID only."""
    return [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]


def step_floor(_semgrep_bin: str, _tmp: pathlib.Path) -> str | None:
    """(0) The declared floor is still below the first Meson that reads the newer name."""
    match = re.search(
        r"meson_version\s*:\s*'>=\s*(\d+)\.(\d+)", MESON_BUILD.read_text(encoding="utf-8")
    )
    if match is None:
        return f"step (0): no meson_version '>=X.Y' floor in {MESON_BUILD}"
    floor = (int(match.group(1)), int(match.group(2)))
    if floor >= FIRST_READER:
        return (
            f"step (0): meson_version floor {floor[0]}.{floor[1]} reads {NEW_NAME}; "
            f"drop {RULE_ID} and this test"
        )
    return None


def step_planted_cases(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(a) Every planted case hits or stays quiet as expected."""
    tree = tmp / "planted_repo"
    for rel, text in PLANTED.items():
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (tree / ".semgrepignore").write_text("build*/\nsubprojects/\n", encoding="utf-8")
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    hits = sorted(
        (str(pathlib.PurePosixPath(r["path"])), r["start"]["line"])
        for r in rule_results(data)
    )
    if hits != EXPECTED_HITS:
        return f"step (a): expected hits {EXPECTED_HITS}, got {hits}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) The tiny project writing the newer name again yields exactly one finding."""
    needle = '(source_dir / "meson_options.txt").write_text('
    source = SELFTEST.read_text(encoding="utf-8")
    if source.count(needle) != 1:
        return f"step (b): needle '{needle}' not exactly once in {SELFTEST}"
    planted = source.replace(needle, needle.replace("meson_options.txt", NEW_NAME))
    tree = tmp / "revert_repo"
    target = tree / SELFTEST_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(planted, encoding="utf-8")
    (tree / ".semgrepignore").write_text("build*/\nsubprojects/\n", encoding="utf-8")
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = rule_results(data)
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith(SELFTEST_REL):
        return f"step (b): finding outside {SELFTEST_REL}: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) Real tree is clean, the scan read the self-test, and no options file uses the newer name."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False)
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith(SELFTEST_REL) for p in scanned):
        return f"step (c): the scan did not read {SELFTEST_REL}"
    results = rule_results(data)
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    project_dirs = {ROOT} | {
        (ROOT / p).parent for p in scanned if pathlib.PurePath(p).name == "meson.build"
    }
    stray = sorted(str(d / NEW_NAME) for d in project_dirs if (d / NEW_NAME).exists())
    if stray:
        return f"step (c): options file under the newer name: {stray}"
    return None


def init_git(tmp: pathlib.Path) -> str | None:
    """git init tmp when git is on PATH; return a problem line or None."""
    git_bin = resolve_executable("git")
    if not git_bin:
        return None
    try:
        proc = subprocess.run(  # nosec B603 - git resolved from PATH to a file; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - git resolved to an executable file, fixed list argv, no shell, explicit deadline
            [git_bin, "init", "-q", str(tmp)],
            capture_output=True,
            text=True,
            env=scrubbed_env(),
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return f"git init -q in {tmp}: no result after 30 s"
    if proc.returncode != 0:
        return f"git init -q in {tmp}: {proc.stderr.strip()}"
    return None


def main(argv: list[str] | None = None) -> int:
    """Run steps (0) to (c); exit 77 (skip) when semgrep is not installed."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, MESON_BUILD, SELFTEST):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        problem = init_git(tmp)
        try:
            for step in (step_floor, step_planted_cases, step_planted_revert, step_real_tree):
                if problem:
                    break
                problem = step(semgrep_bin, tmp)
        except ScanError as err:
            problem = str(err)
    if problem:
        sys.stderr.write(problem + "\n")
        return 1
    print("semgrep meson options: floor, planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
