#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the io-unpack-deadline Semgrep rule.

Checks that `pelorus-py-io-unpack-deadline` in `.semgrep.yml`:
(a) reports subprocess.run, requests.get, urllib.request.urlopen, an imported
    urlopen and an injected urlopen parameter that pass keyword arguments
    through a ** unpack without timeout=, and an unpacked call whose explicit
    timeout=None leaves it without a deadline, while allowing an explicit
    timeout= next to the unpack, a dict given its timeout through setdefault()
    or item assignment first, a call without an unpack (timeout=None there is
    the engine's finding, not this rule's) and subprocess.Popen;
(b) catches a planted regression in scripts/bench/run-bench.py when run()
    hands its keyword arguments to subprocess.run through **kw again;
(c) produces zero findings across the tracked tree while confirming that
    scripts/bench/run-bench.py was scanned.
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
BENCH_REL = "scripts/bench/run-bench.py"
BENCH = ROOT / BENCH_REL
RULE_ID = "pelorus-py-io-unpack-deadline"

PLANTED_PY = """\
import subprocess
import requests
import urllib.request
from urllib.request import urlopen


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)  # reported


def fetch(url, **kw):
    return requests.get(url, **kw)  # reported


def open_url(url, **kw):
    return urllib.request.urlopen(url, **kw)  # reported


def open_url_name(url, **kw):
    return urlopen(url, **kw)  # reported


def open_url_injected(urlopen, url, **kw):
    return urlopen(url, **kw)  # reported


def run_none(cmd, **kw):
    return subprocess.run(cmd, timeout=None, **kw)  # reported


def negative_none_no_unpack(cmd):
    return subprocess.run(cmd, timeout=None)


def negative_explicit(cmd, **kw):
    return subprocess.run(cmd, timeout=30, **kw)


def negative_setdefault(cmd, **kw):
    kw.setdefault("timeout", 30)
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


def negative_assign(cmd, **kw):
    kw["timeout"] = 30
    return subprocess.check_output(cmd, **kw)


def negative_no_unpack(cmd):
    return subprocess.run(cmd, timeout=30)


def negative_popen(cmd, **kw):
    return subprocess.Popen(cmd, **kw)
"""

# The hit lines follow the `reported` comments, so a planted line added or
# moved keeps its expectation.
EXPECTED_HITS = tuple(
    n for n, line in enumerate(PLANTED_PY.splitlines(), 1) if "# reported" in line
)


def make_tree(root: pathlib.Path, rel_path: str, source: str) -> pathlib.Path:
    """Lay out root/rel_path with an ignore file that keeps scripts/."""
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
    tree = make_tree(tmp / "planted_repo", "scripts/planted_io_unpack.py", PLANTED_PY)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    lines = sorted(r["start"]["line"] for r in rule_results(data))
    if lines != list(EXPECTED_HITS):
        return f"step (a): expected hits on lines {list(EXPECTED_HITS)}, got {lines}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) run() handing its deadline to subprocess.run through **kw again yields one finding."""
    needle = "timeout=timeout)"
    source = BENCH.read_text(encoding="utf-8")
    if source.count(needle) != 1:
        return f"step (b): needle '{needle}' not exactly once in {BENCH}"
    planted = source.replace(needle, "**kw)")
    tree = make_tree(tmp / "revert_repo", BENCH_REL, planted)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = rule_results(data)
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith(BENCH_REL):
        return f"step (b): finding outside {BENCH_REL}: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) Real tree is clean, and the scan read the bench runner."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False)
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith(BENCH_REL) for p in scanned):
        return f"step (c): the scan did not read {BENCH_REL}"
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
    for required in (RULE_FILE, BENCH):
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
    print("semgrep io unpack deadline: planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
