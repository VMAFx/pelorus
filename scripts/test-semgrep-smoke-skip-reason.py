#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted-defect recall for the smoke-skip-reason Semgrep rule.

Checks that `pelorus-smoke-skip-reason-head` in `.semgrep.yml`:
(a) reports `pel_skip`/`pel_fail` messages built from `head ... | head -n 1`,
    while allowing messages using `pel_first_error` or static reasons;
(b) catches a planted regression in ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh
    when reverted to head ... | head -n 1;
(c) produces zero findings across the real repository tree while confirming
    that ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh,
    ffmpeg-patches/test/nvenc-udu-sei-smoke.sh, and
    ffmpeg-patches/test/fgs-sidedata-smoke.sh were scanned;
(d) unit-checks the `pel_first_error` helper on a log fixture starting with a
    MESA experimental warning followed by a Vulkan device creation error,
    confirming it extracts the Vulkan error line, handles non-warning fallbacks,
    and caps length at 160 characters across all three smoke scripts.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess  # nosec B404 - fixed argv lists, no shell, explicit deadlines
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULE_FILE = ROOT / ".semgrep.yml"
HOP_SMOKE = ROOT / "ffmpeg-patches" / "test" / "nvenc-cuda-hop-smoke.sh"
UDU_SMOKE = ROOT / "ffmpeg-patches" / "test" / "nvenc-udu-sei-smoke.sh"
FGS_SMOKE = ROOT / "ffmpeg-patches" / "test" / "fgs-sidedata-smoke.sh"
RULE_ID = "pelorus-smoke-skip-reason-head"

SEMGREP_TIMEOUT_S = 180

PLANTED_SH = """\
#!/bin/bash
log="probe.log"

# Must-flag 1: pel_skip with head -c 160 piped to head -n 1
pel_skip "hevc_nvenc cannot encode on this host: $(head -c 160 "$log" | head -n 1)"

# Must-flag 2: pel_fail with head piped to head -n 1
pel_fail "device creation failed: $(head "$log" | head -n 1)"

# Must-flag 3: pel_skip with head -n 10 piped to head -1
pel_skip "probe failed: $(head -n 10 "$log" | head -1)"

# Must-flag 4: pel_skip with a direct head -n 1
pel_skip "probe failed: $(head -n 1 "$log")"

# Must-flag 5: pel_fail with a direct head -1
pel_fail "probe failed: $(head -1 "$log")"

# Must-pass 1: pel_skip with pel_first_error
pel_skip "hevc_nvenc cannot encode on this host: $(pel_first_error "$log")"

# Must-pass 2: pel_fail with pel_first_error
pel_fail "input clip generation failed: $(pel_first_error "$log")"

# Must-pass 3: plain command with head -n 1 outside pel_skip / pel_fail
"$PEL_FFMPEG" -version | head -n 1

# Must-pass 4: plain pel_skip without head command substitution
pel_skip "no usable Vulkan device found"
"""

EXPECTED_HITS = (5, 8, 11, 14, 17)

FIXTURE_MESA_WARNING_VULKAN_ERROR = """\
MESA: warning: Support for this platform is experimental with Xe KMD, bug reports may be ignored.
[Vulkan @ 0x0] Device creation failure: VK_ERROR_INITIALIZATION_FAILED
"""

FIXTURE_MESA_WARNING_FALLBACK = """\
MESA: warning: Support for this platform is experimental with Xe KMD, bug reports may be ignored.
Driver warning: kernel mode driver revision mismatch
Selected Vulkan device does not support required extensions
"""


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
        proc = subprocess.run(  # nosec B603 - fixed list argv, no shell, explicit deadline
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
    tree = make_tree(
        tmp / "planted_repo", "ffmpeg-patches/test/test_planted.sh", PLANTED_SH
    )
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
    """(b) Reverting probe-devices to head -n 1 yields one finding."""
    needle = (
        'pel_skip "no usable CUDA or Vulkan device: '
        '$(pel_first_error "$PEL_OUT/probe-devices.log")"'
    )
    source = HOP_SMOKE.read_text(encoding="utf-8")
    if needle not in source:
        return f"step (b): needle '{needle}' not in {HOP_SMOKE}"
    replacement = (
        'pel_skip "no usable CUDA or Vulkan device: '
        '$(head -c 160 "$PEL_OUT/probe-devices.log" | head -n 1)"'
    )
    planted = source.replace(needle, replacement, 1)
    tree = make_tree(
        tmp / "revert_repo",
        "ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh",
        planted,
    )
    data = run_semgrep(semgrep_bin, tree, RULE_FILE, 1, tmp / "cfg")
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if len(results) != 1:
        return f"step (b): expected exactly 1 finding, got {len(results)}"
    path = results[0].get("path", "")
    if not path.endswith("ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh"):
        return f"step (b): finding outside nvenc-cuda-hop-smoke.sh: {path}"
    return None


def step_real_tree(semgrep_bin: str, tmp: pathlib.Path) -> str | None:
    """(c) The real tree is clean, and the scan did read the target scripts."""
    data = run_semgrep(semgrep_bin, ROOT, RULE_FILE, 0, tmp / "cfg")
    scanned = data.get("paths", {}).get("scanned", [])
    required_scripts = [
        "ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh",
        "ffmpeg-patches/test/nvenc-udu-sei-smoke.sh",
        "ffmpeg-patches/test/fgs-sidedata-smoke.sh",
    ]
    for script_name in required_scripts:
        if not any(p.endswith(script_name) for p in scanned):
            return (
                f"step (c): the real-tree scan did not read {script_name}; "
                "check .semgrepignore and path config"
            )
    results = [
        r
        for r in data.get("results", [])
        if r.get("check_id", "").endswith(RULE_ID)
    ]
    if results:
        return f"step (c): real tree has {len(results)} findings: {results}"
    return None


BASH_TIMEOUT_S = 30

LONG_LINE = "error: " + ("A" * 200)

FIXTURE_CANNOT_LOAD_BEFORE_ENCODER_ERROR = """\
MESA: warning: Support for this platform is experimental with Xe KMD, bug reports may be ignored.
[hevc_nvenc @ 0x0] Cannot load libcuda.so.1
[vost#0:0/hevc_nvenc @ 0x0] Error while opening encoder - maybe incorrect parameters
"""

# (fixture file name, fixture text, expected pel_first_error output)
HELPER_CASES = (
    (
        "vulkan.log",
        FIXTURE_MESA_WARNING_VULKAN_ERROR,
        "[Vulkan @ 0x0] Device creation failure: VK_ERROR_INITIALIZATION_FAILED",
    ),
    (
        "fallback.log",
        FIXTURE_MESA_WARNING_FALLBACK,
        "Selected Vulkan device does not support required extensions",
    ),
    ("long.log", LONG_LINE + "\n", LONG_LINE[:160]),
    (
        "cannot-load.log",
        FIXTURE_CANNOT_LOAD_BEFORE_ENCODER_ERROR,
        "[hevc_nvenc @ 0x0] Cannot load libcuda.so.1",
    ),
)


def resolve_executable(name: str) -> str | None:
    """PATH lookup of name, kept only when it is an executable regular file."""
    found = shutil.which(name)
    if not found:
        return None
    path = pathlib.Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)


def run_first_error(bash_bin: str, fn_code: str, fixture: pathlib.Path) -> str:
    """Run the extracted pel_first_error on fixture; return its stripped stdout."""
    run_cmd = f'{fn_code}\npel_first_error "$1"'
    env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}
    try:
        proc = subprocess.run(  # nosec B603 - fixed list argv, resolved bash, explicit deadline
            [bash_bin, "-c", run_cmd, "_", str(fixture)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=BASH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as err:
        raise ScanError(
            f"pel_first_error on {fixture.name}: no result after {BASH_TIMEOUT_S} s"
        ) from err
    if proc.returncode != 0:
        raise ScanError(
            f"pel_first_error on {fixture.name}: rc={proc.returncode}: {proc.stderr}"
        )
    return proc.stdout.strip()


def step_helper_unit_check(tmp: pathlib.Path) -> str | None:
    """(d) unit-check pel_first_error on log fixtures for all smoke scripts."""
    bash_bin = resolve_executable("bash")
    if not bash_bin:
        return "step (d): bash not found"
    for name, text, _ in HELPER_CASES:
        (tmp / name).write_text(text, encoding="utf-8")
    for script_path in (HOP_SMOKE, UDU_SMOKE, FGS_SMOKE):
        source = script_path.read_text(encoding="utf-8")
        match = re.search(r"(pel_first_error\(\)\s*\{.*?\n\})", source, re.DOTALL)
        if not match:
            return f"step (d): pel_first_error not found in {script_path}"
        for name, _, want in HELPER_CASES:
            got = run_first_error(bash_bin, match.group(1), tmp / name)
            if got != want:
                return (
                    f"step (d): {script_path.name} on {name}: "
                    f"expected {want!r}, got {got!r}"
                )
    return None


def main() -> int:
    """Run steps (a) to (d); exit 77 (skip) when semgrep is not installed."""
    semgrep_bin = resolve_executable("semgrep")
    if not semgrep_bin:
        print("SKIP: semgrep not found")
        return 77
    for required in (RULE_FILE, HOP_SMOKE, UDU_SMOKE, FGS_SMOKE):
        if not required.is_file():
            sys.stderr.write(f"missing file: {required}\n")
            return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "cfg").mkdir()
        try:
            for step in (
                step_helper_unit_check,
                lambda t: step_planted_cases(semgrep_bin, t),
                lambda t: step_planted_revert(semgrep_bin, t),
                lambda t: step_real_tree(semgrep_bin, t),
            ):
                problem = step(tmp)
                if problem:
                    sys.stderr.write(problem + "\n")
                    return 1
        except ScanError as err:
            sys.stderr.write(f"{err}\n")
            return 1
    print(
        "semgrep smoke skip reason: helper units, planted cases, planted revert and real tree ok"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
