#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Build the native fast-suite tests and install them into a tester kit (ADR-0248).

Single source for building and packaging the relocatable tester-kit test suite.
Used by tools/tester/Containerfile during image builds and by
scripts/check-tester-kit-install.py --run-kit during local gate checks.

Usage: python3 -I build-fast-suite.py SOURCE_DIR BUILD_DIR PREFIX [--jobs N]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - run build, introspect, and kit installer subprocesses with explicit timeouts
import sys


def _resolve_tool(name: str) -> tuple[str | None, str | None]:
    """Look up tool via shutil.which and ensure it is an executable regular file."""
    found = shutil.which(name)
    if not found and name == "ninja":
        found = shutil.which("ninja-build")
    if not found:
        return None, f"build-fast-suite.py: {name} executable not found"
    path = Path(found).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None, f"build-fast-suite.py: {name} not executable: {path}"
    return str(path), None


def _run_step(
    cmd: list[str],
    timeout_seconds: float,
    step_name: str,
) -> tuple[int, bytes, str]:
    """Execute a single build step subprocess with explicit timeout and error capturing.

    On failure the returned message ends with the last 20 lines of stdout then
    stderr: meson and ninja print compiler errors on stdout.
    """
    try:
        proc = subprocess.run(  # nosec B603 - build step argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - build step argv list, no shell, explicit timeout
            cmd,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return (
            124,
            b"",
            f"build-fast-suite.py: {step_name} timed out after {timeout_seconds}s",
        )
    except OSError as exc:
        return 127, b"", f"build-fast-suite.py: failed to run {step_name}: {exc}"

    if proc.returncode != 0:
        output = proc.stdout + proc.stderr
        lines = output.decode("utf-8", errors="replace").splitlines()
        header = (
            f"build-fast-suite.py: {step_name} exited with status {proc.returncode}"
        )
        return proc.returncode, proc.stdout, "\n".join([header, *lines[-20:]])
    return 0, proc.stdout, ""


def _step_setup(meson_bin: str, build_dir: Path, source_dir: Path, prefix: Path) -> int:
    """Run meson setup with kit options."""
    cmd = [
        meson_bin,
        "setup",
        "--werror",
        "-Dtests=true",
        "-Dtools=false",
        "-Dshaders=false",
        f"--prefix={prefix}",
        "--libdir=lib",
        str(build_dir),
        str(source_dir),
    ]
    rc, _, err = _run_step(cmd, 300.0, "meson setup")
    if rc != 0:
        print(err, file=sys.stderr)
    return rc


def _step_ninja(ninja_bin: str, build_dir: Path, jobs: int) -> int:
    """Run ninja install with requested job parallelism."""
    cmd = [ninja_bin, "-C", str(build_dir), f"-j{jobs}", "install"]
    rc, _, err = _run_step(cmd, 600.0, "ninja install")
    if rc != 0:
        print(err, file=sys.stderr)
    return rc


def _step_introspect(meson_bin: str, build_dir: Path) -> int:
    """Run meson introspect and save native-fast-tests.json."""
    cmd = [meson_bin, "introspect", "--tests", str(build_dir)]
    rc, stdout, err = _run_step(cmd, 60.0, "meson introspect")
    if rc != 0:
        print(err, file=sys.stderr)
        return rc
    tests_json = build_dir / "native-fast-tests.json"
    tests_json.write_bytes(stdout)
    return 0


def _step_install(build_dir: Path, source_dir: Path, prefix: Path) -> int:
    """Run install-fast-suite.py to populate prefix/tests/."""
    installer = Path(__file__).resolve().parent / "install-fast-suite.py"
    if not installer.is_file():
        print(
            f"build-fast-suite.py: installer script not found: {installer}",
            file=sys.stderr,
        )
        return 1
    tests_json = build_dir / "native-fast-tests.json"
    cmd = [
        sys.executable,
        "-I",
        str(installer),
        str(tests_json),
        str(build_dir),
        str(source_dir),
        str(prefix / "tests"),
    ]
    rc, _, err = _run_step(cmd, 60.0, "install-fast-suite.py")
    if rc != 0:
        print(err, file=sys.stderr)
    return rc


def build_fast_suite(
    source_dir: Path,
    build_dir: Path,
    prefix: Path,
    jobs: int = 1,
) -> int:
    """Configure, build, introspect, and install the tester kit fast suite."""
    meson_bin, meson_err = _resolve_tool("meson")
    if meson_bin is None:
        print(meson_err, file=sys.stderr)
        return 127
    ninja_bin, ninja_err = _resolve_tool("ninja")
    if ninja_bin is None:
        print(ninja_err, file=sys.stderr)
        return 127

    resolved_source = source_dir.resolve()
    resolved_build = build_dir.resolve()
    resolved_prefix = prefix.resolve()

    rc = _step_setup(meson_bin, resolved_build, resolved_source, resolved_prefix)
    if rc != 0:
        return rc
    rc = _step_ninja(ninja_bin, resolved_build, jobs)
    if rc != 0:
        return rc
    rc = _step_introspect(meson_bin, resolved_build)
    if rc != 0:
        return rc
    return _step_install(resolved_build, resolved_source, resolved_prefix)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for build-fast-suite."""
    parser = argparse.ArgumentParser(
        description="Build native fast-suite tests and install them into a tester kit (ADR-0248)."
    )
    parser.add_argument("source_dir", type=Path, help="Meson source root directory")
    parser.add_argument("build_dir", type=Path, help="Meson build output directory")
    parser.add_argument("prefix", type=Path, help="Installation prefix directory")
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=1,
        help="Parallel ninja jobs (integer >= 1, default: 1)",
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint for build-fast-suite."""
    args = parse_arguments(argv)
    return build_fast_suite(
        source_dir=args.source_dir,
        build_dir=args.build_dir,
        prefix=args.prefix,
        jobs=args.jobs,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
