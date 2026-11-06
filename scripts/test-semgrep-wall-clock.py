#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the wall-clock Semgrep rule.

Checks that `pelorus-c-wall-clock-elapsed` in `.semgrep.yml`:
(a) reports wall-clock reads (timespec_get with TIME_UTC,
    clock_gettime with CLOCK_REALTIME, gettimeofday, ftime and the Windows
    system-time calls) in C, while allowing monotonic reads
    (clock_gettime with CLOCK_MONOTONIC, QueryPerformanceCounter);
(b) catches a planted regression in ffmpeg-patches/test/mc_stats_test.c
    when mono_now() is reverted to timespec_get(..., TIME_UTC);
(c) produces zero findings across the tracked tree while confirming that
    ffmpeg-patches/test/mc_stats_test.c was scanned.
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
MC_STATS_TEST = ROOT / "ffmpeg-patches" / "test" / "mc_stats_test.c"
MC_STATS_REL = "ffmpeg-patches/test/mc_stats_test.c"
RULE_ID = "pelorus-c-wall-clock-elapsed"

PLANTED_C = """\
#include <sys/time.h>
#include <time.h>

typedef struct { long long QuadPart; } LARGE_INTEGER;
int QueryPerformanceCounter(LARGE_INTEGER *v);
void GetSystemTimeAsFileTime(void *ft);

double case_positive_timespec_utc(void)
{
    struct timespec ts;
    (void)timespec_get(&ts, TIME_UTC); /* reported */
    return (double)ts.tv_sec;
}

double case_positive_realtime(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) { /* reported */
        return -1.0;
    }
    return (double)ts.tv_sec;
}

double case_positive_gettimeofday(void)
{
    struct timeval tv;
    (void)gettimeofday(&tv, 0); /* reported */
    return (double)tv.tv_sec;
}

void case_positive_filetime(void *ft)
{
    GetSystemTimeAsFileTime(ft); /* reported */
}

double case_negative_monotonic(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        return -1.0;
    }
    return (double)ts.tv_sec;
}

double case_negative_qpc(void)
{
    LARGE_INTEGER now;
    if (!QueryPerformanceCounter(&now)) {
        return -1.0;
    }
    return (double)now.QuadPart;
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
    tree = make_tree(tmp / "planted_repo", "libpelorus/test/planted_clock.c", PLANTED_C)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    lines = sorted(r["start"]["line"] for r in rule_results(data))
    if lines != list(EXPECTED_HITS):
        return f"step (a): expected hits on lines {list(EXPECTED_HITS)}, got {lines}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) Reverting mono_now() to the wall clock yields exactly one finding."""
    needle = "if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {"
    source = MC_STATS_TEST.read_text(encoding="utf-8")
    if source.count(needle) != 1:
        return f"step (b): needle '{needle}' not exactly once in {MC_STATS_TEST}"
    planted = source.replace(needle, "if (timespec_get(&ts, TIME_UTC) == 0) {")
    tree = make_tree(tmp / "revert_repo", MC_STATS_REL, planted)
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg", hermetic=True)
    results = rule_results(data)
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith(MC_STATS_REL):
        return f"step (b): finding outside {MC_STATS_REL}: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) Real tree is clean, and the scan read the mc stats test."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg", hermetic=False)
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith(MC_STATS_REL) for p in scanned):
        return f"step (c): the scan did not read {MC_STATS_REL}"
    results = rule_results(data)
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
    for required in (RULE_FILE, MC_STATS_TEST):
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
    print("semgrep wall clock: planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
