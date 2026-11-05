#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Shared Semgrep runner for the scripts/test-semgrep-*.py rule tests.

The tests run under ``python3 -I``, so they load this module by path
(importlib), not by import, as the patch tests load scripts/patch_harness.py.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess  # nosec B404 - fixed semgrep argv, no shell, explicit deadline

# Deadline for one Semgrep scan (HISS-02). A local scan takes a few seconds;
# three sequential scans at this bound stay under the 600 s Meson timeout.
SEMGREP_TIMEOUT_S = 180


class ScanError(Exception):
    """A semgrep run ended with an unexpected status or unreadable output."""


def resolve_executable(name: str) -> str | None:
    """PATH lookup of name, kept only when it is an executable regular file.

    The tests run the binary with a fixed argument list and no shell; the PATH
    the build or CI job sets is the trust boundary.
    """
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
    timeout_s: float = SEMGREP_TIMEOUT_S,
) -> dict:
    """Scan target_dir with one rule file; return the parsed JSON report.

    Semgrep's settings live under env_dir so no user configuration leaks in,
    and an exit code other than expected_rc raises ScanError.
    """
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
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as err:
        raise ScanError(
            f"semgrep scan in {target_dir}: no result after {timeout_s} s"
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
