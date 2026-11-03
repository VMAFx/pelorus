#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Shared diff extraction and strict C build-and-run for the patch harness tests.

The scripts/test-*.py patch tests run under ``python3 -I``, so they load this
module by path (importlib), not by import.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import signal
import subprocess  # nosec B404 - compiler and harness argv, no shell, explicit deadline
import sys
import tempfile
from types import MappingProxyType
from typing import Sequence

DEFAULT_CFLAGS = ("-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function")
# Deadlines for the harness compiler and the harness binary (HISS-02). A
# harness is a few hundred lines and runs in milliseconds; a stall is a failure.
BUILD_TIMEOUT_S = 120.0
RUN_TIMEOUT_S = 30.0

# cl.exe-style drivers take MSVC options. The GNU warning set maps the way
# FFmpeg's configure maps it (msvc_flags: -Wall, -Wextra, -Werror), and the
# CRT defines are the ones its msvcrt branch adds, so a harness of FFmpeg code
# sees the warnings an FFmpeg build under that driver would see.
MSVC_DRIVERS = frozenset({"cl", "clang-cl", "icx-cl"})
MSVC_DEFINES = ("-D_USE_MATH_DEFINES", "-D_CRT_SECURE_NO_WARNINGS", "-D_CRT_NONSTDC_NO_WARNINGS")
_MSVC_WARNINGS = MappingProxyType({
    "-Wall": ("-W3", "-wd4018", "-wd4146", "-wd4244", "-wd4305", "-wd4554", "-wd4267"),
    "-Wextra": (
        "-W4", "-wd4244", "-wd4127", "-wd4018", "-wd4389", "-wd4146", "-wd4057",
        "-wd4204", "-wd4706", "-wd4305", "-wd4152", "-wd4324", "-we4013", "-wd4100",
        "-wd4214", "-wd4307", "-wd4273", "-wd4554", "-wd4701", "-wd4703",
    ),
    "-Werror": ("-WX",),
})
# -Wall maps to -W3 and -Wextra to -W4; cl.exe warns D9025 when a later level
# overrides an earlier one, so driver_argv keeps one level, the highest.
_MSVC_LEVELS = frozenset({"-W1", "-W2", "-W3", "-W4"})
# A GNU -Wno-<name> stays as it is for the clang-based drivers (clang-cl,
# icx-cl), which read it natively; cl.exe takes the matching warning number.
# clang-cl does not map -wd4505 onto -Wno-unused-function.
_CL_WARNING_OFF = MappingProxyType({
    "-Wno-unused-function": "-wd4505",
    "-Wno-unused-parameter": "-wd4100",
})


# C failure counter + expect() every harness MAIN starts with.
EXPECT_C = r"""
static int failures;

static void expect(const char *what, long got, long want)
{
    if (got != want) {
        fprintf(stderr, "FAIL %s: got %ld, want %ld\n", what, got, want);
        failures++;
    }
}
"""


def added_lines(text: str, subpath: str | None = None) -> list[str]:
    """Return the '+' lines of a unified diff without the marker.

    With ``subpath``, only the lines of that file's section count.
    """
    out: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        if subpath is not None:
            match = re.match(r"^diff --git a/(\S+) b/\S+$", line)
            if match:
                current = match.group(1)
                continue
            if current != subpath:
                continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


def new_side_lines(text: str, subpath: str | None = None) -> list[str]:
    """Return the post-image of a unified diff: its '+' and context lines.

    A block added between two existing functions can end on a line that diff
    matches to the original code (a shared ``return 0;`` and ``}``); the '+'
    lines alone then lose that tail. The post-image keeps it. Lines of
    different hunks are joined, so callers slice within one hunk.
    With ``subpath``, only the lines of that file's section count.
    """
    out: list[str] = []
    current: str | None = None
    in_hunk = False
    for line in text.splitlines():
        match = re.match(r"^diff --git a/(\S+) b/\S+$", line)
        if match:
            current = match.group(1)
            in_hunk = False
            continue
        if subpath is not None and current != subpath:
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", " ")):
            out.append(line[1:])
        elif line == "":
            out.append("")
    return out


def extract_function(
    source: str | list[str],
    name: str,
    label: str | None = None,
) -> str:
    """Extract a static C function definition by name, tracking brace nesting."""
    text = "\n".join(source) if isinstance(source, list) else source
    pattern = re.compile(r"^static\s+[^\n;]*\b" + re.escape(name) + r"\s*\(", re.M)
    match = pattern.search(text)
    if not match:
        if label is not None:
            raise ValueError(f"{label}: function {name} not found")
        raise ValueError(f"function {name}() not found")
    open_brace = text.find("{", match.end())
    if open_brace == -1:
        raise ValueError(f"{name}() has no opening brace")
    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[match.start() : index + 1] + "\n"
    raise ValueError(f"{name}() has no closing brace")


def make_parser(
    description: str,
    default_patch: pathlib.Path | None = None,
    *,
    patches: dict[str, pathlib.Path] | None = None,
    self_test: bool = False,
) -> argparse.ArgumentParser:
    """Create standard argument parser with patch path(s), --cc, and optional --self-test."""
    parser = argparse.ArgumentParser(description=description)
    if default_patch is not None:
        parser.add_argument("--patch", type=pathlib.Path, default=default_patch)
    if patches:
        for opt_name, default_val in patches.items():
            parser.add_argument(opt_name, type=pathlib.Path, default=default_val)
    if self_test:
        parser.add_argument(
            "--self-test",
            action="store_true",
            help="also require every planted defect to be rejected",
        )
    parser.add_argument(
        "--cc",
        # REMAINDER, not "+": a Meson compiler command can carry option
        # tokens (["cc", "-pipe"]) that "+" would read as unknown options.
        nargs=argparse.REMAINDER,
        default=[os.environ.get("CC", "cc")],
        help="C compiler command (must be the last option)",
    )
    return parser


def _run_with_deadline(
    argv: Sequence[str],
    timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    """Run argv with tree kill on deadline (HISS-02, HISS-08)."""
    kwargs: dict[str, object] = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    proc = subprocess.Popen(  # nosec B603 - configured compiler or harness binary; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - configured compiler or harness binary, list argv, no shell, timeout
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - result record only, executes nothing
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(  # nosec B603, B607 - taskkill process tree on deadline; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - taskkill process tree on deadline, list argv, no shell, timeout
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                # taskkill missing or hung: proc.kill() below still ends the driver.
                pass
            proc.kill()
        elif proc.pid > 1:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                # Process group already terminated or reaped before SIGKILL.
                pass
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Driver unresponsive to SIGKILL; abandon wait to report deadline timeout.
                pass
        return None


def is_msvc_driver(cc: Sequence[str]) -> bool:
    """True when the compiler command is a cl.exe-style driver."""
    return bool(cc) and pathlib.PureWindowsPath(cc[0]).stem.lower() in MSVC_DRIVERS


def msvc_flag(flag: str, clang_based: bool = False) -> tuple[str, ...]:
    """Map one GNU-style harness flag to its MSVC form; refuse any other flag."""
    if flag in _MSVC_WARNINGS:
        return _MSVC_WARNINGS[flag]
    if flag in _CL_WARNING_OFF:
        return (flag,) if clang_based else (_CL_WARNING_OFF[flag],)
    if flag.startswith("-std=c"):
        return ("/std:" + flag[len("-std="):],)
    if flag.startswith(("-I", "-D")):
        return (flag,)
    if flag == "-lm":
        return ()  # the UCRT holds the math library
    raise ValueError(f"no MSVC form for harness flag {flag!r}")


def _one_warning_level(mapped: list[str]) -> list[str]:
    """Keep one MSVC warning level, the highest, at the first level's place."""
    levels = [flag for flag in mapped if flag in _MSVC_LEVELS]
    if len(levels) < 2:
        return mapped
    top = max(levels)
    out: list[str] = []
    for flag in mapped:
        if flag not in _MSVC_LEVELS:
            out.append(flag)
        elif top not in out:
            out.append(top)
    return out


def driver_argv(
    cc: Sequence[str],
    flags: Sequence[str],
    c_file: pathlib.Path,
    exe: pathlib.Path,
    libs: Sequence[str] = (),
) -> list[str]:
    """Compile-and-link argv for ``cc``, in GNU or MSVC driver syntax."""
    if not cc:
        raise ValueError("empty C compiler command")
    if not is_msvc_driver(cc):
        return [*cc, *flags, str(c_file), "-o", str(exe), *libs]
    clang_based = pathlib.PureWindowsPath(cc[0]).stem.lower() != "cl"
    mapped = _one_warning_level(
        [out for flag in (*flags, *libs) for out in msvc_flag(flag, clang_based)]
    )
    obj = exe.with_suffix(".obj")
    return [*cc, "-nologo", *MSVC_DEFINES, *mapped, str(c_file), f"-Fo{obj}", f"-Fe{exe}"]


def build_and_run(
    source: str,
    cc: Sequence[str],
    prefix: str = "pelorus-harness-",
    cflags: Sequence[str] = (),
    libs: Sequence[str] = (),
    build_timeout: float = BUILD_TIMEOUT_S,
    run_timeout: float = RUN_TIMEOUT_S,
) -> tuple[int, str]:
    """Build ``source`` strictly in a tempdir and run it.

    Returns (status, output): 0 = ran and exited 0, 1 = ran and failed or
    passed ``run_timeout``, 2 = did not compile or passed ``build_timeout``.
    ``libs`` follow the source file so the linker resolves them under
    --as-needed.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        tmp_path = pathlib.Path(tmp)
        c_file = tmp_path / "harness.c"
        exe = tmp_path / ("harness.exe" if os.name == "nt" else "harness")
        c_file.write_text(source, encoding="utf-8")
        try:
            argv = driver_argv(cc, (*DEFAULT_CFLAGS, *cflags), c_file, exe, libs)
        except ValueError as err:
            return 2, f"harness failed to compile: {err}\n"
        build = _run_with_deadline(argv, build_timeout)
        if build is None:
            return 2, f"harness build exceeded {build_timeout:g} s\n"
        if build.returncode != 0:
            return 2, "harness failed to compile:\n" + build.stdout + build.stderr
        run = _run_with_deadline([str(exe)], run_timeout)
        if run is None:
            return 1, f"harness run exceeded {run_timeout:g} s\n"
        return (0 if run.returncode == 0 else 1), run.stdout + run.stderr


def compile_and_run(
    source: str,
    cc: Sequence[str],
    prefix: str = "pelorus-harness-",
    libs: Sequence[str] = (),
    cflags: Sequence[str] = (),
) -> int:
    """build_and_run plus reporting; returns the script exit code (0 or 1)."""
    status, output = build_and_run(source, cc, prefix, cflags=cflags, libs=libs)
    if status == 2:
        print("ERROR: " + output, file=sys.stderr)
        return 1
    sys.stdout.write(output)
    if status != 0:
        print("ERROR: harness exited non-zero", file=sys.stderr)
        return 1
    return 0
