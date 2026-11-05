#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the shell-splice Semgrep rule.

Checks that `pelorus-shell-splice-into-inline-source` in `.semgrep.yml`:
(a) reports shell variables spliced into inline interpreter programs
    (python3, python, perl, ruby, node with -c or -e) via closing and
    reopening quotes, while allowing argv parameters and plain commands;
(b) catches a planted regression in scripts/test-abi-upgrade.sh when
    sys.argv[1] is reverted to an inline quote splice;
(c) produces zero findings across the real repository tree while confirming
    that scripts/test-abi-upgrade.sh was scanned.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess  # nosec B404 - fixed semgrep argv, no shell, explicit deadline
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULE_FILE = ROOT / ".semgrep.yml"
ABI_UPGRADE_SCRIPT = ROOT / "scripts" / "test-abi-upgrade.sh"
RULE_ID = "pelorus-shell-splice-into-inline-source"

SEMGREP_TIMEOUT_S = 180

PLANTED_SH = """\
#!/bin/bash
st="corrupt.bin"
x="value"
v="perl_val"

# Must-flag 1: multi-line python3 -I -B -c with open
timeout 600 python3 -I -B -c '
with open("'"$st"'", "r+b") as f:
    f.seek(300)
'

# Must-flag 2: one-line python3 -c print
python3 -c 'print("'"${x}"'")'

# Must-flag 3: perl -e with double-quoted splice
perl -e 'print "'"$v"'";'

# Must-flag 4: backslash-newline continuation between -c and quoted program
python3 -c \\
    'print("'"$x"'")'

# Must-flag 5: perl -le cluster with splice
perl -le 'print "'"$v"'"'

# Must-flag 6: python3 -Ic cluster with splice
python3 -Ic 'print("'"$x"'")'

# Must-flag 7: perl with quoted option argument before inline flag
perl -I "$libdir" -le 'print "'"$v"'"'

# Must-flag 8: literal prefix inside double-quoted splice
python3 -c 'print("'"prefix-$x"'")'

# Must-flag 9: backslash-newline continuation before inline flag
python3 \\
    -c 'print("'"$x"'")'

# Must-pass 1: fixed form passing argv
python3 -c 'import sys; print(sys.argv[1])' "$st"

# Must-pass 2: plain echo outside any -c program
# (stays quiet because echo is not an inline interpreter command)
echo '"$x"'

# Must-pass 3: python3 -c with argv
python3 -c 'print(1)' "$x"

# Must-pass 4: backslash-newline continuation with argv
python3 -c \\
    'import sys; print(sys.argv[1])' "$x"

# Must-pass 5: perl -le passing argv
perl -le 'print $ARGV[0]' "$v"

# Must-pass 6: command boundary before non-interpreter -c command
python3 script.py; sh -c 'echo "'"$x"'"'

# Must-pass 7: escaped literal dollar inside double-quoted splice
python3 -c 'print("'"\\$HOME"'")'
"""

EXPECTED_HITS = (7, 13, 16, 19, 23, 26, 29, 32, 35)


class ScanError(Exception):
    """A semgrep run ended with an unexpected status or unreadable output."""


def run_semgrep(
    semgrep_bin: str,
    target_dir: pathlib.Path,
    rule_path: pathlib.Path,
    expected_rc: int,
    env_dir: pathlib.Path,
) -> dict:
    """Scan target_dir with one rule file; return parsed JSON report."""
    env = dict(os.environ)
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
        ".",
    ]
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
    tree = make_tree(tmp / "planted_repo", "scripts/test_planted.sh", PLANTED_SH)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg")
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
    """(b) Reverting sys.argv[1] to an inline quote splice yields one finding."""
    needle = 'with open(sys.argv[1], "r+b") as f:'
    source = ABI_UPGRADE_SCRIPT.read_text(encoding="utf-8")
    if needle not in source:
        return f"step (b): needle '{needle}' not in {ABI_UPGRADE_SCRIPT}"
    replacement = 'with open("\'"$st_corrupt"\'", "r+b") as f:'
    planted = source.replace(needle, replacement, 1)
    tree = make_tree(tmp / "revert_repo", "scripts/test-abi-upgrade.sh", planted)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg")
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith("scripts/test-abi-upgrade.sh"):
        return f"step (b): finding outside scripts/test-abi-upgrade.sh: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) The real tree is clean, and the scan did read test-abi-upgrade.sh."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg")
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith("scripts/test-abi-upgrade.sh") for p in scanned):
        return (
            "step (c): the real-tree scan did not read "
            "scripts/test-abi-upgrade.sh; check .semgrepignore and path config"
        )
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    return None


def resolve_semgrep() -> str | None:
    """PATH lookup of semgrep, kept only when it is an executable regular file."""
    found = shutil.which("semgrep")
    if not found:
        return None
    path = pathlib.Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)


def main() -> int:
    """Run steps (a) to (c); exit 77 (skip) when semgrep is not installed."""
    semgrep_bin = resolve_semgrep()
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, ABI_UPGRADE_SCRIPT):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        try:
            for step in (
                step_planted_cases,
                step_planted_revert,
                step_real_tree,
            ):
                problem = step(semgrep_bin, tmp)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except ScanError as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print(
        "semgrep shell splice: planted cases, planted revert and real tree ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
