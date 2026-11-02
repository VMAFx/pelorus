#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the float-identity Semgrep rule (ADR-0239).

Ported from VMAFx/vmafx test_semgrep_float_identity.py
(sha 25ee5f3a25aebc97ee453fc3a70f676c68da2a81).

Checks that `pelorus-test-float-identity` in `.semgrep.yml`:
(a) reports computed float/double equality comparisons in test sources, while
    allowing literals, macros, k-constants, NaN idioms, integer casts, and calls;
(b) catches a planted regression in libpelorus/test/interop_test.c when
    float_bits_equal() is reverted to ==;
(c) produces zero findings across the real repository tree.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULE_FILE = ROOT / ".semgrep.yml"
INTEROP_TEST = ROOT / "libpelorus" / "test" / "interop_test.c"

PLANTED_C = """\
#include <stdbool.h>

#define LIMIT 2.0
static const double kLimit = 3.0;
static double computed(void);
static unsigned bits(double value);
struct opaque;
extern struct opaque *o;

bool planted(double x, float p, float q, int i, int k, double u, double v)
{
    double y = computed();
    bool hits = x != y;          /* reported: computed double != */
    hits = hits || x == y;       /* reported: computed double == */
    hits = hits || p == q;       /* reported: computed float == */
    hits = hits || p != q;       /* reported: computed float != */
    hits = hits || 1.0 + y == x;  /* reported: literal prefix, computed */
    hits = hits || kLimit + y != x; /* reported: k-constant prefix, computed */
    hits = hits || (double)(y * 2.0) == x; /* reported: cast of a computed value */
    hits = hits || -LIMIT * y == x; /* reported: macro prefix, computed */
    hits = hits || (x + y) == (u + v); /* reported: arithmetic both sides */
    hits = hits || (p * q) != (q - p); /* reported: float arithmetic both sides */
    hits = hits || x == 1.0 + y; /* reported: computed on the right */
    hits = hits || x != kLimit + y; /* reported: computed on the right */
    hits = hits || p == 1.0f + q; /* reported: float, computed on the right */
    hits = hits || p != q * 2.0f; /* reported: float, computed on the right */
    hits = hits || 1.0f + q == p; /* reported: float, computed on the left */
    hits = hits || 2.0f * q != p; /* reported: float, computed on the left */
    hits = hits || x == o->d; /* reported: one side typed, field type unknown */
    hits = hits || o->d == x; /* reported: one side typed, field type unknown */
    hits = hits || x != o->d; /* reported: one side typed, field type unknown */
    hits = hits || o->d != x; /* reported: one side typed, field type unknown */
    hits = hits || p == o->f; /* reported: one side typed, field type unknown */
    hits = hits || o->f == p; /* reported: one side typed, field type unknown */
    hits = hits || p != o->f; /* reported: one side typed, field type unknown */
    hits = hits || o->f != p; /* reported: one side typed, field type unknown */
    bool quiet = x == 0.0;       /* literal */
    quiet = quiet || 1.5 != y;   /* literal on the left */
    quiet = quiet || x == LIMIT; /* macro */
    quiet = quiet || y == kLimit; /* k-constant */
    quiet = quiet || x != x;     /* NaN idiom */
    quiet = quiet || i == k;     /* integers */
    quiet = quiet || x == -1.5e3; /* signed literal with exponent */
    quiet = quiet || p == .5f;   /* float literal */
    quiet = quiet || y != -kLimit; /* signed k-constant */
    quiet = quiet || i == (int)y; /* integer cast of a double */
    quiet = quiet || x == 0x1p0; /* hexadecimal floating literal */
    quiet = quiet || p != -0x1.8p-3f; /* signed hexadecimal float literal */
    quiet = quiet || (i + (int)x) == k; /* integer arithmetic */
    quiet = quiet || bits(x + y) == bits(u + v); /* calls return integers */
    return hits || quiet;
}
"""

EXPECTED_HITS = tuple(range(13, 37))


class ScanError(Exception):
    """A semgrep run ended with an unexpected status or unreadable output."""


def run_semgrep(
    semgrep_bin: str,
    target_dir: pathlib.Path,
    rule_path: pathlib.Path,
    expected_rc: int,
    env_dir: pathlib.Path,
) -> dict:
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
    proc = subprocess.run(
        cmd, cwd=str(target_dir), capture_output=True, text=True, env=env, check=False
    )
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


def make_tree(root: pathlib.Path, name: str, source: str) -> pathlib.Path:
    """Lay out root/libpelorus/test/<name> with an ignore file that keeps test/."""
    test_dir = root / "libpelorus" / "test"
    test_dir.mkdir(parents=True)
    (root / ".semgrepignore").write_text("build*/\nsubprojects/\n", encoding="utf-8")
    (test_dir / name).write_text(source, encoding="utf-8")
    return root


def step_planted_cases(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(a) Every planted case hits or stays quiet as its comment says."""
    tree = make_tree(tmp / "planted_repo", "planted.c", PLANTED_C)
    results = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg").get(
        "results", []
    )
    lines = sorted(r["start"]["line"] for r in results)
    if lines != list(EXPECTED_HITS):
        return f"step (a): expected hits on lines {list(EXPECTED_HITS)}, got {lines}"
    return None


def step_planted_revert(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(b) Reverting float_bits_equal() to == yields exactly one finding."""
    needle = "CHECK(float_bits_equal(got_var, var[i]))"
    source = INTEROP_TEST.read_text(encoding="utf-8")
    if needle not in source:
        return f"step (b): needle '{needle}' not in {INTEROP_TEST}"
    planted = source.replace(needle, "CHECK(got_var == var[i])", 1)
    tree = make_tree(tmp / "revert_repo", "interop_test.c", planted)
    results = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg").get(
        "results", []
    )
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    if not results[0].get("path", "").endswith("libpelorus/test/interop_test.c"):
        return f"step (b): finding outside interop_test.c: {results[0].get('path')}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) The real tree is clean, and the scan did read the test sources."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg")
    # Semgrep's built-in ignore list skips test/ folders; without the
    # repository .semgrepignore the scan reads nothing and passes empty.
    scanned = data.get("paths", {}).get("scanned", [])
    if not any(p.endswith("libpelorus/test/interop_test.c") for p in scanned):
        return "step (c): the real-tree scan did not read libpelorus/test/interop_test.c; check .semgrepignore"
    results = data.get("results", [])
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    return None


def main() -> int:
    semgrep_bin = shutil.which("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, INTEROP_TEST):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        try:
            for step in (step_planted_cases, step_planted_revert, step_real_tree):
                problem = step(semgrep_bin, tmp)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except ScanError as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print("semgrep float identity: planted cases, planted revert and real tree ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
