#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the validate-which-executable Semgrep rule.

Checks that `pelorus-validate-which-executable` in `.semgrep.yml`:
(a) reports unvalidated shutil.which executables passed to subprocess calls,
    while allowing validated executables (Path.resolve, is_file, os.access X_OK);
(b) catches a planted regression in scripts/test-semgrep-float-identity.py when
    git_bin is reverted to raw shutil.which without validation;
(c) produces zero findings across the real scripts directory while confirming
    that scripts/test-semgrep-float-identity.py was scanned.
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
FLOAT_IDENTITY_SCRIPT = ROOT / "scripts" / "test-semgrep-float-identity.py"
RULE_ID = "pelorus-validate-which-executable"

SEMGREP_TIMEOUT_S = 180

PLANTED_PY = """\
import os
import pathlib
import shutil
import subprocess

def case_positive():
    git = shutil.which("git")
    if git:
        subprocess.run([git, "status"])  # reported: raw which -> run

def case_try():
    tool = shutil.which("tool")
    if tool:
        try:
            subprocess.run([tool, "-v"], timeout=5)  # reported: inside try
        except OSError:
            pass

def case_with(tmp):
    tool = shutil.which("tool")
    with open(tmp, "w") as fh:
        subprocess.Popen([tool], stdout=fh)  # reported: inside with

def case_cmd_list():
    tool = shutil.which("tool")
    cmd = [tool, "--help"]
    subprocess.check_output(cmd)  # reported: argv list variable

def case_inline():
    subprocess.run([shutil.which("tool"), "-v"])  # reported: inline which

def case_unrelated_guard():
    tool = shutil.which("tool")
    if tool:
        if os.access("other", os.R_OK):
            pass
        subprocess.run([tool, "-v"])  # reported: unrelated guard

def case_negative():
    git = shutil.which("git")
    if git:
        path = pathlib.Path(git).resolve()
        if path.is_file() and os.access(path, os.X_OK):
            subprocess.run([str(path), "status"])

def case_negative_access():
    tool = shutil.which("tool")
    if tool and os.access(tool, os.X_OK):
        subprocess.run([tool, "-v"])

def case_resolve_executable():
    found = shutil.which("tool")
    if not found:
        return None
    path = pathlib.Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    subprocess.run([found, "-v"])

def case_resolve_executable_str_path():
    found = shutil.which("tool")
    if not found:
        return None
    path = pathlib.Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    subprocess.run([str(path), "-v"])

def case_wrong_mode():
    tool = shutil.which("tool")
    if tool and os.access(tool, os.R_OK):
        subprocess.run([tool, "-v"])  # reported: read mode only

def case_negated_branch():
    tool = shutil.which("tool")
    if not os.access(tool, os.X_OK):
        subprocess.run([tool, "-v"])  # reported: call in failing branch
"""

EXPECTED_HITS = (9, 15, 22, 27, 30, 37, 72, 77)


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
    """(b) Reverting git_bin to raw shutil.which yields one finding."""
    needle = 'git_bin = resolve_executable("git")'
    source = FLOAT_IDENTITY_SCRIPT.read_text(encoding="utf-8")
    if needle not in source:
        return f"step (b): needle '{needle}' not in {FLOAT_IDENTITY_SCRIPT}"
    replacement = 'git_bin = shutil.which("git")'
    planted = source.replace(needle, replacement, 1)
    tree = make_tree(tmp / "revert_repo", "scripts/test-semgrep-float-identity.py", planted)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith("scripts/test-semgrep-float-identity.py"):
        return f"step (b): finding outside test-semgrep-float-identity.py: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) The real tree is clean, and the scan read target scripts."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False)
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith("scripts/test-semgrep-float-identity.py") for p in scanned):
        return (
            "step (c): the scan did not read scripts/test-semgrep-float-identity.py"
        )
    if not any(p.endswith("scripts/test-nvenc-pelorus-mapping.py") for p in scanned):
        return (
            "step (c): the scan did not read scripts/test-nvenc-pelorus-mapping.py"
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
    for required in (RULE_FILE, FLOAT_IDENTITY_SCRIPT):
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
    print("semgrep which executable: planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
