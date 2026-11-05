#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the chmod mode Semgrep rule.

Checks that `pelorus-chmod-owner-only` in `.semgrep.yml`:
(a) reports chmod calls with group/other permissions or mode >= 0o650, while
    allowing owner-only modes (0, 0o400, 0o500, 0o600, S_IWUSR);
(b) catches a planted regression in tools/tester/install-fast-suite.py when
    runner chmod is reverted to 0o755;
(c) produces zero findings across all tracked .py files while confirming
    that scripts/check-suppression-markers.py was scanned.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess  # nosec B404 - fixed semgrep argv, no shell, explicit deadline
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULE_FILE = ROOT / ".semgrep.yml"
INSTALL_FAST_SUITE_SCRIPT = ROOT / "tools" / "tester" / "install-fast-suite.py"
RULE_ID = "pelorus-chmod-owner-only"

SEMGREP_TIMEOUT_S = 180

PLANTED_PY = """\
import os
import stat
from pathlib import Path

def case_positive_0o640():
    os.chmod("f1", 0o640)

def case_positive_0o700():
    os.chmod("f2", 0o700)

def case_positive_0o755():
    os.chmod("f3", 0o755)

def case_positive_0o604():
    os.chmod("f4", 0o604)

def case_positive_path_0o750():
    Path("f5").chmod(0o750)

def case_positive_stat_irgrp():
    os.chmod("f6", stat.S_IRGRP)

def case_negative_0o600():
    os.chmod("s1", 0o600)

def case_negative_0o400():
    os.chmod("s2", 0o400)

def case_negative_0o500():
    os.chmod("s3", 0o500)

def case_negative_0():
    os.chmod("s4", 0)

def case_negative_stat_iwusr():
    st_mode = 0
    os.chmod("s5", st_mode | stat.S_IWUSR)
"""

EXPECTED_HITS = (6, 9, 12, 15, 18, 21)


class ScanError(Exception):
    """A semgrep run ended with an unexpected status or unreadable output."""


def scrubbed_env() -> dict[str, str]:
    """Copy os.environ with all GIT_* keys dropped to prevent repo steering."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def resolve_executable(name: str) -> str | None:
    """PATH lookup of name, kept only when it is an executable regular file."""
    found = shutil.which(name)
    if not found:
        return None
    path = pathlib.Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)


def run_semgrep(
    semgrep_bin: str,
    target_dir: pathlib.Path,
    rule_path: pathlib.Path,
    expected_rc: int,
    env_dir: pathlib.Path,
    *,
    hermetic: bool = False,
) -> dict:
    """Scan target_dir with one rule file; return parsed JSON report."""
    env = scrubbed_env()
    env["XDG_CONFIG_HOME"] = str(env_dir)
    env["SEMGREP_SETTINGS_FILE"] = str(env_dir / "semgrep-settings.yaml")
    env["SEMGREP_LOG_FILE"] = str(env_dir / "semgrep.log")
    cmd = [
        semgrep_bin,
        "scan",
        "--config",
        str(rule_path),
        "--error",
        "--json",
        "--metrics=off",
        "--disable-version-check",
    ]
    if hermetic:
        cmd += ["--no-git-ignore", "--project-root", "."]
    cmd.append(".")
    try:
        proc = subprocess.run(  # nosec B603 - semgrep resolved from PATH to a file; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit, python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args - semgrep resolved to an executable file, fixed list argv, no shell, explicit deadline
            cmd,
            cwd=str(target_dir),
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=SEMGREP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as err:
        raise ScanError(
            f"semgrep scan in {target_dir}: no result after {SEMGREP_TIMEOUT_S} s"
        ) from err
    if proc.returncode != expected_rc:
        raise ScanError(
            f"semgrep scan in {target_dir}: expected rc={expected_rc}, "
            f"got rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as err:
        raise ScanError(
            f"semgrep json output unreadable: {err}\n{proc.stdout}"
        ) from err


def make_tree(root: pathlib.Path, rel_path: str, source: str) -> pathlib.Path:
    """Lay out root/rel_path with an ignore file that keeps test/."""
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / ".semgrepignore").write_text("build*/\nsubprojects/\n", encoding="utf-8")
    target.write_text(source, encoding="utf-8")
    return root


def step_planted_cases(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(a) Every planted case hits or stays quiet as expected."""
    tree = make_tree(tmp / "planted_repo", "scripts/test_planted.py", PLANTED_PY)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    lines = sorted(r["start"]["line"] for r in results)
    if lines != list(EXPECTED_HITS):
        return f"step (a): expected hits on lines {list(EXPECTED_HITS)}, got {lines}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) Reverting runner chmod to 0o755 yields exactly one finding."""
    needle = "runner.chmod(0o500)"
    source = INSTALL_FAST_SUITE_SCRIPT.read_text(encoding="utf-8")
    if needle not in source:
        return f"step (b): needle '{needle}' not in {INSTALL_FAST_SUITE_SCRIPT}"
    replacement = "runner.chmod(0o755)"
    planted = source.replace(needle, replacement, 1)
    tree = make_tree(
        tmp / "revert_repo", "tools/tester/install-fast-suite.py", planted
    )
    data = run_semgrep(
        semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True
    )
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith("tools/tester/install-fast-suite.py"):
        return f"step (b): finding outside install-fast-suite.py: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) Real tree is clean, and scan read scripts/check-suppression-markers.py."""
    data = run_semgrep(
        semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False
    )
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(
        p.endswith("scripts/check-suppression-markers.py") for p in scanned
    ):
        return (
            "step (c): the scan did not read scripts/check-suppression-markers.py"
        )
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    return None


def main(argv: list[str] | None = None) -> int:
    """Run steps (a) to (c); exit 77 (skip) when semgrep is not installed."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, INSTALL_FAST_SUITE_SCRIPT):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        git_bin = resolve_executable("git")
        if git_bin:
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
                sys.stderr.write(f"git init -q in {tmp}: no result after 30 s\n")
                return 1
            if proc.returncode != 0:
                sys.stderr.write(f"git init -q in {tmp}: {proc.stderr.strip()}\n")
                return 1
        try:
            for step in (step_planted_cases, step_planted_revert, step_real_tree):
                problem = step(semgrep_bin, tmp)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except ScanError as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print("semgrep chmod mode: planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
