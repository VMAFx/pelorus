#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Verify that fast-suite tests and fixtures can be packaged and run by the tester kit.

A fast-suite test whose command or arguments cannot be installed into the
relocatable tester kit passes every local gate and fails only in the
tester-publish CI workflow. This check introspects the Meson test suite
for a build directory, runs tools/tester/install-fast-suite.py against a
throwaway destination, and reports installation failures before push.

With --run-kit (ADR-0248), after the installation check passes, it builds
the kit native suite in a temporary directory via tools/tester/build-fast-suite.py
and executes <PREFIX>/tests/run-fast-suite.sh to verify that kit-shipped tests
pass without relying on Meson-only expectations (such as should_fail: true).

Usage: python3 scripts/check-tester-kit-install.py [--run-kit] [--source-dir DIR] BUILD_DIR
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess  # nosec B404 - run meson introspect, kit installer, kit builder, and kit runner subprocesses with explicit timeouts
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
        return (
            None,
            f"check-tester-kit-install.py: build directory not found: {resolved_build}",
        )

    repo_root = Path(__file__).resolve().parent.parent
    resolved_source = source_dir.resolve() if source_dir is not None else repo_root
    if not resolved_source.is_dir():
        return (
            None,
            f"check-tester-kit-install.py: source directory not found: {resolved_source}",
        )

    installer = repo_root / "tools" / "tester" / "install-fast-suite.py"
    if not installer.is_file():
        return (
            None,
            f"check-tester-kit-install.py: installer script not found: {installer}",
        )

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
            return (
                1,
                None,
                f"check-tester-kit-install.py: tests JSON file not found: {resolved}",
            )
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


def _run_install_check(
    python_bin: str,
    build_dir: Path,
    source_dir: Path | None,
    tests_json_path: Path | None,
    timeout_seconds: float,
) -> int:
    """Run the install-only packaging check against a temporary destination."""
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


def _partial_text(value: bytes | str | None) -> str:
    """Return a TimeoutExpired partial output as text.

    subprocess.run fills TimeoutExpired.stdout from the raw pipe on POSIX,
    so it holds bytes even with text=True; on Windows it holds str.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _print_runner_failure(output: str) -> None:
    """Print the last test banner and the last 20 output lines to stderr."""
    lines = output.splitlines()
    last_banner = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("=== ") and stripped.endswith(" ==="):
            last_banner = stripped
    if last_banner is not None:
        print(last_banner, file=sys.stderr)
    tail = lines[-20:]
    for line in tail:
        print(line, file=sys.stderr)


def _run_kit_build(
    python_bin: str,
    builder: Path,
    jobs: int,
    source_dir: Path,
    build_dir: Path,
    prefix_dir: Path,
    timeout_seconds: float = 1200.0,
) -> int:
    """Invoke tools/tester/build-fast-suite.py to build and package the kit suite.

    The default bound stays above the sum of the builder's own step bounds
    (300 + 600 + 60 + 60 s), so the builder reports which step timed out.
    """
    cmd = [
        python_bin,
        "-I",
        str(builder),
        "--jobs",
        str(jobs),
        str(source_dir),
        str(build_dir),
        str(prefix_dir),
    ]
    try:
        proc = subprocess.run(  # nosec B603 - kit build-fast-suite argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - kit build-fast-suite argv list, no shell, explicit timeout
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(
            f"check-tester-kit-install.py: build-fast-suite timed out after {timeout_seconds}s",
            file=sys.stderr,
        )
        return 124
    except OSError as exc:
        print(
            f"check-tester-kit-install.py: failed to run build-fast-suite: {exc}",
            file=sys.stderr,
        )
        return 127

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        if err:
            print(err, file=sys.stderr)
        return proc.returncode
    return 0


def _run_kit_runner(
    runner_path: Path,
    cwd_path: Path,
    timeout_seconds: float = 600.0,
) -> int:
    """Execute the installed kit fast-suite runner and report failures."""
    try:
        proc = subprocess.run(  # nosec B603 - kit fast-suite runner argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - kit fast-suite runner argv list, no shell, explicit timeout
            [str(runner_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout_seconds,
            cwd=str(cwd_path),
        )
    except subprocess.TimeoutExpired as exc:
        _print_runner_failure(_partial_text(exc.stdout))
        print(
            f"check-tester-kit-install.py: kit runner timed out after {timeout_seconds}s",
            file=sys.stderr,
        )
        return 124
    except OSError as exc:
        print(
            f"check-tester-kit-install.py: failed to execute kit runner: {exc}",
            file=sys.stderr,
        )
        return 127

    if proc.returncode != 0:
        _print_runner_failure(proc.stdout or "")
        return proc.returncode
    return 0


def run_kit_check(
    python_bin: str,
    source_dir: Path | None = None,
) -> int:
    """Build and execute the relocatable tester-kit fast suite."""
    repo_root = Path(__file__).resolve().parent.parent
    resolved_source = source_dir.resolve() if source_dir is not None else repo_root
    builder = repo_root / "tools" / "tester" / "build-fast-suite.py"
    if not builder.is_file():
        print(
            f"check-tester-kit-install.py: builder script not found: {builder}",
            file=sys.stderr,
        )
        return 1

    cpu_count = os.cpu_count() or 1
    jobs = max(1, cpu_count // 3)

    with tempfile.TemporaryDirectory(prefix="pelorus-run-kit-") as run_kit_tmp:
        tmp_path = Path(run_kit_tmp)
        temp_build = tmp_path / "build"
        temp_prefix = tmp_path / "prefix"

        rc = _run_kit_build(
            python_bin=python_bin,
            builder=builder,
            jobs=jobs,
            source_dir=resolved_source,
            build_dir=temp_build,
            prefix_dir=temp_prefix,
        )
        if rc != 0:
            return rc

        runner_path = temp_prefix / "tests" / "run-fast-suite.sh"
        if not runner_path.is_file():
            print(
                f"check-tester-kit-install.py: kit runner not found: {runner_path}",
                file=sys.stderr,
            )
            return 1

        return _run_kit_runner(runner_path=runner_path, cwd_path=tmp_path)


def run_check(
    build_dir: Path,
    source_dir: Path | None = None,
    tests_json_path: Path | None = None,
    timeout_seconds: float = 30.0,
    run_kit: bool = False,
) -> int:
    """Run tester kit install verification and return exit code."""
    try:
        python_bin = _resolve_python()
    except RuntimeError as exc:
        print(f"check-tester-kit-install.py: {exc}", file=sys.stderr)
        return 127

    rc = _run_install_check(
        python_bin=python_bin,
        build_dir=build_dir,
        source_dir=source_dir,
        tests_json_path=tests_json_path,
        timeout_seconds=timeout_seconds,
    )
    if rc != 0:
        return rc

    if not run_kit:
        return 0

    return run_kit_check(python_bin=python_bin, source_dir=source_dir)


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
    parser.add_argument(
        "--run-kit",
        action="store_true",
        help="Build and execute the tester-kit fast suite in a temporary directory (ADR-0248)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint for check-tester-kit-install."""
    args = parse_arguments(argv)
    return run_check(
        build_dir=args.build_dir,
        source_dir=args.source_dir,
        tests_json_path=args.tests_json,
        run_kit=args.run_kit,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
