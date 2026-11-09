#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Smoke check for the Pelorus dev container toolchain image (ADR-0250).

Validates that tools and pkg-config modules needed by CI and offline gates
resolve inside the dev container base image, and runs the offline native gate
inside a container-owned directory copy before pushing the image.
"""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed toolchain and gate argv, no shell, explicit timeout
import sys
import tempfile
from typing import Callable

REQUIRED_COMMANDS: tuple[str, ...] = (
    # Assemblers for FFmpeg x86 SIMD: ffmpeg-stack job / Containerfile line 47
    "nasm",
    "yasm",
    # SPIR-V shader compilers: core + sanitizers jobs / Containerfile line 46
    "glslc",
    "glslangValidator",
    # C toolchain, formatting, static analysis: core + sanitizers jobs / Containerfile line 46
    "clang",
    "clang-format",
    "clang-tidy",
    # Build system: core + sanitizers jobs / Containerfile line 46
    "meson",
    "ninja",
    # TypeScript figure stripping & linting: docs job / Containerfile lines 60-74
    "node",
    # Workflow and git hook linters: docs job / Containerfile lines 75-94
    "actionlint",
    "lefthook",
    # Shell and Python linters: docs job / Containerfile line 49
    "shellcheck",
    "black",
    # Base toolchain, package config and runtime: Containerfile lines 45, 49
    "git",
    "make",
    "pkg-config",
    "python3",
    "curl",
    "xz",
)

# ci.yml ffmpeg-stack receipt loop :136-139 holds the module list
REQUIRED_MODULES: tuple[str, ...] = (
    "vulkan",
    "vpl",
    "aom",
    "SvtAv1Enc",
    "ffnvcodec",
)

PROBE_TIMEOUT = 60.0
DEFAULT_GATE_TIMEOUT = 1500.0

RunnerType = Callable[..., tuple[int, str, str]]


def default_runner(
    cmd: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = PROBE_TIMEOUT,
    capture_output: bool = True,
) -> tuple[int, str, str]:
    """Execute a child process with an explicit deadline and no shell."""
    try:
        if capture_output:
            proc = subprocess.run(  # nosec B603 - fixed argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed argv list, no shell, explicit timeout
                cmd,
                cwd=cwd,
                env=env,
                timeout=timeout,
                capture_output=True,
                text=True,
                check=False,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        proc = subprocess.run(  # nosec B603 - fixed argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed argv list, no shell, explicit timeout
            cmd,
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, "", ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as exc:
        return 127, "", f"failed to execute {cmd[0]}: {exc}"


def check_commands(path: str | None = None) -> list[str]:
    """Return all required commands that fail to resolve on PATH."""
    missing: list[str] = []
    for cmd in REQUIRED_COMMANDS:
        if shutil.which(cmd, path=path) is None:
            missing.append(cmd)
    return missing


def check_modules(
    runner: RunnerType,
    env: dict[str, str] | None = None,
    probe_timeout: float = PROBE_TIMEOUT,
    out_stream: io.TextIOBase | None = None,
) -> list[str]:
    """Check required pkg-config modules and display modversion."""
    active_out = out_stream or sys.stdout
    missing: list[str] = []
    for mod in REQUIRED_MODULES:
        rc, _, _ = runner(
            ["pkg-config", "--exists", mod],
            env=env,
            timeout=probe_timeout,
        )
        if rc != 0:
            missing.append(mod)
        else:
            rc_ver, out_ver, _ = runner(
                ["pkg-config", "--modversion", mod],
                env=env,
                timeout=probe_timeout,
            )
            if rc_ver == 0:
                active_out.write(f"pkg-config {mod} {out_ver.strip()}\n")
            else:
                missing.append(mod)
    return missing


def check_venv(
    work_dir: str,
    runner: RunnerType,
    env: dict[str, str] | None = None,
    probe_timeout: float = PROBE_TIMEOUT,
) -> str | None:
    """Verify that python3 -m venv succeeds and includes pip."""
    venv_dir = os.path.join(work_dir, "venv")
    rc, out, err = runner(
        ["python3", "-m", "venv", venv_dir],
        env=env,
        timeout=probe_timeout,
    )
    if rc != 0:
        details = err.strip() or out.strip() or f"exit code {rc}"
        return f"python3 -m venv failed: {details}"

    venv_python = os.path.join(venv_dir, "bin", "python")
    rc_pip, out_pip, err_pip = runner(
        [venv_python, "-m", "pip", "--version"],
        env=env,
        timeout=probe_timeout,
    )
    if rc_pip != 0:
        details = err_pip.strip() or out_pip.strip() or f"exit code {rc_pip}"
        return f"venv pip check failed: {details}"

    return None


def check_toolchain(
    work_dir: str,
    path: str | None = None,
    runner: RunnerType | None = None,
    env: dict[str, str] | None = None,
    probe_timeout: float = PROBE_TIMEOUT,
    err_stream: io.TextIOBase | None = None,
    out_stream: io.TextIOBase | None = None,
) -> int:
    """Run phase toolchain: verify commands, pkg-config, and venv creation."""
    active_runner = runner or default_runner
    active_err = err_stream or sys.stderr

    missing_cmds = check_commands(path=path)
    missing_mods = check_modules(
        active_runner, env=env, probe_timeout=probe_timeout, out_stream=out_stream
    )
    venv_err = check_venv(work_dir, active_runner, env=env, probe_timeout=probe_timeout)

    has_errors = False
    for cmd in missing_cmds:
        active_err.write(f"check-devcontainer-image: missing command: {cmd}\n")
        has_errors = True
    for mod in missing_mods:
        active_err.write(
            f"check-devcontainer-image: missing pkg-config module: {mod}\n"
        )
        has_errors = True
    if venv_err is not None:
        active_err.write(f"check-devcontainer-image: missing venv: {venv_err}\n")
        has_errors = True

    return 1 if has_errors else 0


def _is_local_tree(folder: str, name: str) -> bool:
    """Return True for a top-level directory a local build or tool run left.

    Only directories match: the tracked file build-config.env starts with
    "build" too and the series scripts read it.
    """
    full = os.path.join(folder, name)
    if os.path.islink(full) or not os.path.isdir(full):
        return False
    return name.startswith("build") or name in (".cache", ".venv")


def _ignore_toplevel(
    src_abs: str,
) -> Callable[[str, list[str]], set[str]]:
    """Build ignore callable skipping top-level build*/, .cache/, .venv/."""

    def _ignore(folder: str, names: list[str]) -> set[str]:
        if os.path.abspath(folder) != src_abs:
            return set()
        return {n for n in names if _is_local_tree(folder, n)}

    return _ignore


def check_gate(
    source_dir: str,
    work_dir: str,
    gate_timeout: float = DEFAULT_GATE_TIMEOUT,
    runner: RunnerType | None = None,
    env: dict[str, str] | None = None,
    err_stream: io.TextIOBase | None = None,
) -> int:
    """Run phase gate: copy checkout to work dir and run verify-offline."""
    active_runner = runner or default_runner
    active_err = err_stream or sys.stderr

    makefile = os.path.join(source_dir, "Makefile")
    if not os.path.isfile(makefile):
        active_err.write(f"check-devcontainer-image: {source_dir} has no Makefile\n")
        return 2

    dest_src = os.path.join(work_dir, "src")
    if os.path.exists(dest_src):
        shutil.rmtree(dest_src)

    src_abs = os.path.abspath(source_dir)
    shutil.copytree(
        source_dir,
        dest_src,
        symlinks=True,
        ignore=_ignore_toplevel(src_abs),
    )

    build_dir = os.path.join(dest_src, "build")
    cmd = ["make", "verify-offline", f"BUILD_DIR={build_dir}"]
    rc, _, _ = active_runner(
        cmd,
        cwd=dest_src,
        env=env,
        timeout=gate_timeout,
        capture_output=False,
    )
    if rc != 0:
        if rc == 124:
            active_err.write(
                f"check-devcontainer-image: gate timed out after {gate_timeout}s\n"
            )
        else:
            active_err.write(
                f"check-devcontainer-image: gate failed with exit code {rc}\n"
            )
        return rc

    return 0


def _write_executable(path: str, content: str) -> None:
    """Write an executable script to path."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.chmod(path, 0o500)


def _setup_stubs(stub_bin: str) -> None:
    """Populate stub_bin with stub scripts for self-test."""
    for cmd in REQUIRED_COMMANDS:
        script_path = os.path.join(stub_bin, cmd)
        if cmd == "pkg-config":
            content = (
                "#!/bin/sh\n"
                'if [ "$1" = "--exists" ]; then\n'
                '    if [ -n "$PLANT_MISSING_MODULE" ] && [ "$2" = "$PLANT_MISSING_MODULE" ]; then\n'
                "        exit 1\n"
                "    fi\n"
                "    exit 0\n"
                "fi\n"
                'if [ "$1" = "--modversion" ]; then\n'
                '    echo "1.0.0"\n'
                "    exit 0\n"
                "fi\n"
                "exit 0\n"
            )
        elif cmd == "python3":
            content = (
                "#!/bin/sh\n"
                'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
                '    if [ "$PLANT_FAIL_VENV" = "1" ]; then\n'
                '        echo "simulated venv failure" >&2\n'
                "        exit 1\n"
                "    fi\n"
                '    target="$3"\n'
                '    mkdir -p "$target/bin"\n'
                '    printf \'#!/bin/sh\\nif [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then\\n    echo "pip 24.0"\\n    exit 0\\nfi\\nexit 0\\n\' > "$target/bin/python"\n'
                '    chmod 0500 "$target/bin/python"\n'
                "    exit 0\n"
                "fi\n"
                "exit 0\n"
            )
        elif cmd == "make":
            content = (
                "#!/bin/sh\n"
                'if [ "$PLANT_MAKE_SLEEP" = "1" ]; then\n'
                "    sleep 3\n"
                "    exit 0\n"
                "fi\n"
                'if [ -n "$PLANT_MAKE_RC" ]; then\n'
                '    exit "$PLANT_MAKE_RC"\n'
                "fi\n"
                "exit 0\n"
            )
        else:
            content = "#!/bin/sh\nexit 0\n"
        _write_executable(script_path, content)


# Toolchain plants: (label, env additions, command stub to remove, rc, stderr text).
TOOLCHAIN_PLANTS: tuple[tuple[str, dict[str, str], str | None, int, str], ...] = (
    ("a", {}, None, 0, ""),
    ("b", {}, "nasm", 1, "missing command: nasm"),
    (
        "c",
        {"PLANT_MISSING_MODULE": "vulkan"},
        None,
        1,
        "missing pkg-config module: vulkan",
    ),
    ("d", {"PLANT_FAIL_VENV": "1"}, None, 1, "missing venv:"),
)


def _run_toolchain_plants(
    td: str, stub_bin: str, sys_path: str
) -> tuple[int, list[str]]:
    """Execute plants (a) all present, (b) command, (c) module, (d) venv."""
    failures: list[str] = []
    work_dir = os.path.join(td, "work-tc")
    os.makedirs(work_dir, exist_ok=True)
    for label, extra, removed, want_rc, want_err in TOOLCHAIN_PLANTS:
        env = {"PATH": f"{stub_bin}:{sys_path}", **extra}
        if removed is not None:
            os.remove(os.path.join(stub_bin, removed))
        err = io.StringIO()
        rc = check_toolchain(
            work_dir, path=stub_bin, env=env, err_stream=err, out_stream=io.StringIO()
        )
        if removed is not None:
            _write_executable(os.path.join(stub_bin, removed), "#!/bin/sh\nexit 0\n")
        if rc != want_rc or want_err not in err.getvalue():
            failures.append(
                f"plant {label}: expected rc {want_rc} and {want_err!r}, got {rc}"
            )
        else:
            print(f"ok: plant {label}")
    return len(failures), failures


def _run_copy_plant(td: str) -> list[str]:
    """Plant (h): the copy skips local build trees but keeps build-config.env."""
    src = os.path.join(td, "copy_src")
    os.makedirs(os.path.join(src, "build", "meson-private"), exist_ok=True)
    os.makedirs(os.path.join(src, ".cache"), exist_ok=True)
    os.makedirs(os.path.join(src, "sub", "build"), exist_ok=True)
    Path(os.path.join(src, "build-config.env")).write_text("X=1\n", encoding="utf-8")
    dest = os.path.join(td, "copy_dest")
    shutil.copytree(
        src, dest, symlinks=True, ignore=_ignore_toplevel(os.path.abspath(src))
    )
    kept = sorted(os.listdir(dest))
    if kept != ["build-config.env", "sub"] or not os.path.isdir(
        os.path.join(dest, "sub", "build")
    ):
        return [f"plant h: expected build-config.env and sub/build kept, got {kept}"]
    print("ok: plant h")
    return []


def _run_gate_plants(td: str, stub_bin: str, sys_path: str) -> tuple[int, list[str]]:
    """Execute plants (e), (f), and (g)."""
    failures: list[str] = []
    child_env = {"PATH": f"{stub_bin}:{sys_path}"}
    work_dir = os.path.join(td, "work-gate")
    os.makedirs(work_dir, exist_ok=True)

    # (e) --source without Makefile -> rc 2
    empty_src = os.path.join(td, "empty_src")
    os.makedirs(empty_src, exist_ok=True)
    rc_e = check_gate(
        empty_src,
        work_dir,
        gate_timeout=10.0,
        env=child_env,
        err_stream=io.StringIO(),
    )
    if rc_e != 2:
        failures.append(f"plant e: expected rc 2, got {rc_e}")
    else:
        print("ok: plant e")

    valid_src = os.path.join(td, "valid_src")
    os.makedirs(valid_src, exist_ok=True)
    Path(os.path.join(valid_src, "Makefile")).write_text(
        "all:\n\t@true\n", encoding="utf-8"
    )

    # (f) stub make exiting 3 -> gate rc 3 propagated
    env_f = dict(child_env)
    env_f["PLANT_MAKE_RC"] = "3"
    rc_f = check_gate(
        valid_src,
        work_dir,
        gate_timeout=10.0,
        env=env_f,
        err_stream=io.StringIO(),
    )
    if rc_f != 3:
        failures.append(f"plant f: expected rc 3, got {rc_f}")
    else:
        print("ok: plant f")

    # (g) stub make sleeping past a 1 s gate timeout -> rc 124
    env_g = dict(child_env)
    env_g["PLANT_MAKE_SLEEP"] = "1"
    rc_g = check_gate(
        valid_src,
        work_dir,
        gate_timeout=1.0,
        env=env_g,
        err_stream=io.StringIO(),
    )
    if rc_g != 124:
        failures.append(f"plant g: expected rc 124, got {rc_g}")
    else:
        print("ok: plant g")

    return len(failures), failures


def run_self_test() -> int:
    """Run hermetic host-only self-test with stubbed bin directory."""
    tmp_parent = os.environ.get("TMPDIR")
    td = tempfile.mkdtemp(prefix="pelorus-devcontainer-selftest-", dir=tmp_parent)
    try:
        stub_bin = os.path.join(td, "bin")
        os.makedirs(stub_bin, exist_ok=True)
        _setup_stubs(stub_bin)
        sys_path = "/usr/bin:/bin"

        _, fails_tc = _run_toolchain_plants(td, stub_bin, sys_path)
        _, fails_gate = _run_gate_plants(td, stub_bin, sys_path)

        all_fails = fails_tc + fails_gate + _run_copy_plant(td)
        if all_fails:
            for f in all_fails:
                sys.stderr.write(f"check-devcontainer-image self-test: {f}\n")
            return 1
        return 0
    finally:
        shutil.rmtree(td, ignore_errors=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Pelorus dev container toolchain smoke check."
    )
    parser.add_argument(
        "--source",
        type=str,
        help="Repository source directory (must contain Makefile).",
    )
    parser.add_argument(
        "--work",
        type=str,
        default=None,
        help="Working directory for checkout copy and build.",
    )
    parser.add_argument(
        "--gate-timeout",
        type=float,
        default=DEFAULT_GATE_TIMEOUT,
        help=f"Timeout in seconds for gate execution (default: {DEFAULT_GATE_TIMEOUT}).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run hermetic self-tests on planted defects.",
    )
    args = parser.parse_args(argv)
    if not args.self_test and not args.source:
        parser.error("--source is required when not running --self-test")
    return args


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()

    auto_work = False
    work_dir = args.work
    if work_dir is None:
        auto_work = True
        work_dir = tempfile.mkdtemp(
            prefix="pelorus-devcontainer-", dir=os.environ.get("TMPDIR")
        )
    else:
        os.makedirs(work_dir, exist_ok=True)

    tc_rc = check_toolchain(work_dir)
    if tc_rc != 0:
        if auto_work:
            sys.stderr.write(
                f"check-devcontainer-image: keeping work dir for logs: {work_dir}\n"
            )
        return tc_rc
    # The gate's make streams to the same descriptors; flush first so the
    # toolchain lines stay ahead of it in a non-tty CI log.
    print("check-devcontainer-image: toolchain ok", flush=True)

    gate_rc = check_gate(args.source, work_dir, gate_timeout=args.gate_timeout)
    if gate_rc != 0:
        if auto_work:
            sys.stderr.write(
                f"check-devcontainer-image: keeping work dir for logs: {work_dir}\n"
            )
        return gate_rc

    if auto_work:
        shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
