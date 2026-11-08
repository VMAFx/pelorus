#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the printf-argv Semgrep rule.

Checks that `pelorus-c-printf-argv` in `.semgrep.yml`:
(a) reports a command-line argument passed straight into printf (as an
    argument or as the format) or copied into a buffer that printf reads
    later, while allowing an argument bound to a named local, the program
    name argv[0] and fprintf to stderr;
(b) catches a planted regression in libpelorus/test/abi_history/abi_history.c
    when the expect-reject message passes argv[2] instead of blob_path;
(c) produces zero findings across the tracked tree while confirming that
    libpelorus/test/abi_history/abi_history.c was scanned.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
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
ABI_HISTORY_REL = "libpelorus/test/abi_history/abi_history.c"
ABI_HISTORY = ROOT / ABI_HISTORY_REL
RULE_ID = "pelorus-c-printf-argv"

PLANTED_C = """\
#include <stdio.h>
#include <string.h>

int case_positive_argument(int argc, char **argv)
{
    if (argc < 2) {
        return 2;
    }
    (void)printf("path %s\\n", argv[1]); /* reported */
    return 0;
}

int case_positive_format(int argc, char **argv)
{
    if (argc < 3) {
        return 2;
    }
    (void)printf(argv[2]); /* reported */
    return 0;
}

int case_positive_copied(int argc, char **argv)
{
    char buf[64];
    if (argc < 2) {
        return 2;
    }
    strncpy(buf, argv[1], sizeof buf - 1); /* reported */
    buf[sizeof buf - 1] = '\\0';
    (void)printf(buf);
    return 0;
}

int case_negative_named_local(int argc, char **argv)
{
    if (argc < 3) {
        return 2;
    }
    const char *blob_path = argv[2];
    (void)printf("ok: reader rejected %s\\n", blob_path);
    return 0;
}

int case_negative_program_name(int argc, char **argv)
{
    (void)argc;
    (void)printf("usage: %s <blob>\\n", argv[0]);
    return 2;
}

int case_negative_stderr(int argc, char **argv)
{
    if (argc < 2) {
        return 2;
    }
    (void)fprintf(stderr, "Unknown mode: %s\\n", argv[1]);
    return 2;
}
"""

# The hit lines follow the `reported` comments, so a planted line added or
# moved keeps its expectation.
EXPECTED_HITS = tuple(
    n for n, line in enumerate(PLANTED_C.splitlines(), 1) if "/* reported */" in line
)


def make_tree(root: pathlib.Path, rel_path: str, source: str) -> pathlib.Path:
    """Lay out root/rel_path with an ignore file that keeps test/."""
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / ".semgrepignore").write_text("build*/\nsubprojects/\n", encoding="utf-8")
    target.write_text(source, encoding="utf-8")
    return root


def rule_results(data: dict) -> list[dict]:
    """Findings of RULE_ID only."""
    return [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]


def step_planted_cases(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(a) Every planted case hits or stays quiet as expected."""
    tree = make_tree(tmp / "planted_repo", "libpelorus/test/planted_argv.c", PLANTED_C)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    lines = sorted(r["start"]["line"] for r in rule_results(data))
    if lines != list(EXPECTED_HITS):
        return f"step (a): expected hits on lines {list(EXPECTED_HITS)}, got {lines}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) Passing argv[2] to the expect-reject printf yields exactly one finding."""
    needle = '(void)printf("ok: reader rejected %s\\n", blob_path);'
    source = ABI_HISTORY.read_text(encoding="utf-8")
    if source.count(needle) != 1:
        return f"step (b): needle '{needle}' not exactly once in {ABI_HISTORY}"
    planted = source.replace(needle, needle.replace("blob_path", "argv[2]"))
    tree = make_tree(tmp / "revert_repo", ABI_HISTORY_REL, planted)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = rule_results(data)
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith(ABI_HISTORY_REL):
        return f"step (b): finding outside {ABI_HISTORY_REL}: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) Real tree is clean, and the scan read the ABI history reader."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False)
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith(ABI_HISTORY_REL) for p in scanned):
        return f"step (c): the scan did not read {ABI_HISTORY_REL}"
    results = rule_results(data)
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
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
    """Run steps (a) to (c); exit 77 (skip) when semgrep is not installed."""
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, ABI_HISTORY):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        problem = init_git(tmp)
        try:
            for step in (step_planted_cases, step_planted_revert, step_real_tree):
                if problem:
                    break
                problem = step(semgrep_bin, tmp)
        except ScanError as err:
            problem = str(err)
    if problem:
        sys.stderr.write(problem + "\n")
        return 1
    print("semgrep printf argv: planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
