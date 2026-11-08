#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Verify that fast-suite tests and fixtures can be packaged by the tester kit.

A fast-suite test whose command or arguments cannot be installed into the
relocatable tester kit passes every local gate and fails only in the
tester-publish CI workflow. This check introspects the Meson test suite
for a build directory, runs tools/tester/install-fast-suite.py against a
throwaway destination, and reports installation failures before push.

Usage: python3 scripts/check-tester-kit-install.py BUILD_DIR
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess  # nosec B404 - run meson introspect and fast-suite installer subprocesses with explicit timeouts
import sys
import tempfile

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from semgrep_harness import resolve_executable


@dataclass(frozen=True)
class KitPaths:
    """Resolved filesystem locations required for tester kit packaging."""

    build_dir: Path
    source_dir: Path
    installer: Path
    destination: Path


def _last_line(text: str, fallback: str) -> str:
    """Return the last non-empty line of text or fallback."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else fallback


def _resolve_python() -> str:
    """Return the absolute path of the current Python interpreter."""
    resolved = Path(sys.executable).resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RuntimeError(f"Python interpreter not executable: {sys.executable}")
    return str(resolved)


def introspect_tests(
    meson_bin: str, build_dir: Path, timeout_seconds: float = 30.0
) -> tuple[int, bytes, str]:
    """Run ``meson introspect --tests`` and return (returncode, stdout_bytes, last_error)."""
    try:
        proc = subprocess.run(  # nosec B603 - meson introspect argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - meson introspect argv list, no shell, explicit timeout
            [meson_bin, "introspect", "--tests", str(build_dir)],
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return 124, b"", f"meson introspect timed out after {timeout_seconds}s"
    except OSError as exc:
        return 127, b"", f"failed to run meson introspect: {exc}"

    if proc.returncode != 0:
        last_error = _last_line(
            proc.stderr.decode("utf-8", errors="replace"),
            f"meson introspect failed with exit code {proc.returncode}",
        )
        return proc.returncode, proc.stdout, last_error
    return 0, proc.stdout, ""


def run_installer(
    python_bin: str,
    tests_json: Path,
    paths: KitPaths,
    timeout_seconds: float = 30.0,
) -> tuple[int, str]:
    """Run install-fast-suite.py and return (returncode, last_error_line)."""
    try:
        proc = subprocess.run(  # nosec B603 - fast-suite installer argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fast-suite installer argv list, no shell, explicit timeout
            [
                python_bin,
                "-I",
                str(paths.installer),
                str(tests_json),
                str(paths.build_dir),
                str(paths.source_dir),
                str(paths.destination),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return 124, f"installer timed out after {timeout_seconds}s"
    except OSError as exc:
        return 127, f"failed to run installer: {exc}"

    last_error = ""
    if proc.returncode != 0:
        err_msg = _last_line(proc.stderr, "")
        if err_msg:
            last_error = err_msg
        else:
            last_error = _last_line(
                proc.stdout, f"installer exited with status {proc.returncode}"
            )
    return proc.returncode, last_error


def _resolve_paths(
    build_dir: Path,
    source_dir: Path | None,
    destination: Path,
) -> tuple[KitPaths | None, str | None]:
    """Validate and resolve required filesystem paths."""
    resolved_build = build_dir.resolve()
    if not resolved_build.is_dir():
        return None, f"check-tester-kit-install.py: build directory not found: {resolved_build}"

    repo_root = Path(__file__).resolve().parent.parent
    resolved_source = source_dir.resolve() if source_dir is not None else repo_root
    if not resolved_source.is_dir():
        return None, f"check-tester-kit-install.py: source directory not found: {resolved_source}"

    installer = repo_root / "tools" / "tester" / "install-fast-suite.py"
    if not installer.is_file():
        return None, f"check-tester-kit-install.py: installer script not found: {installer}"

    return KitPaths(resolved_build, resolved_source, installer, destination), None


def _tests_json(
    build_dir: Path,
    tmp_path: Path,
    tests_json_path: Path | None,
    timeout_seconds: float = 30.0,
) -> tuple[int, Path | None, str]:
    """Obtain tests JSON path via introspection or existing file validation."""
    if tests_json_path is not None:
        resolved = tests_json_path.resolve()
        if not resolved.is_file():
            return 1, None, f"check-tester-kit-install.py: tests JSON file not found: {resolved}"
        return 0, resolved, ""

    meson_bin = resolve_executable("meson")
    if meson_bin is None:
        return 127, None, "check-tester-kit-install.py: Meson executable not found"

    rc, stdout_bytes, last_err = introspect_tests(
        meson_bin, build_dir, timeout_seconds=timeout_seconds
    )
    if rc != 0:
        return rc, None, last_err

    json_file = tmp_path / "tests.json"
    json_file.write_bytes(stdout_bytes)
    return 0, json_file, ""


def run_check(
    build_dir: Path,
    source_dir: Path | None = None,
    tests_json_path: Path | None = None,
    timeout_seconds: float = 30.0,
) -> int:
    """Run tester kit install verification and return exit code."""
    try:
        python_bin = _resolve_python()
    except RuntimeError as exc:
        print(f"check-tester-kit-install.py: {exc}", file=sys.stderr)
        return 127

    with tempfile.TemporaryDirectory(prefix="pelorus-tester-check-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        temp_dest = tmp_path / "dest"
        temp_dest.mkdir()

        paths, path_err = _resolve_paths(build_dir, source_dir, temp_dest)
        if paths is None:
            print(path_err, file=sys.stderr)
            return 1

        rc, json_path, json_err = _tests_json(
            paths.build_dir, tmp_path, tests_json_path, timeout_seconds=timeout_seconds
        )
        if rc != 0:
            if json_err:
                print(json_err, file=sys.stderr)
            return rc

        if json_path is None:
            return 1

        rc, last_error = run_installer(
            python_bin, json_path, paths, timeout_seconds=timeout_seconds
        )
        if rc != 0:
            if last_error:
                print(last_error, file=sys.stderr)
            return rc
    return 0


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for check-tester-kit-install."""
    parser = argparse.ArgumentParser(
        description="Verify that fast-suite tests and fixtures can be packaged by the tester kit."
    )
    parser.add_argument("build_dir", type=Path, help="Meson build directory")
    parser.add_argument(
        "--tests-json",
        type=Path,
        default=None,
        help="Optional path to pre-generated introspection JSON",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Optional source root directory (defaults to repository root)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint for check-tester-kit-install."""
    args = parse_arguments(argv)
    return run_check(
        build_dir=args.build_dir,
        source_dir=args.source_dir,
        tests_json_path=args.tests_json,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
