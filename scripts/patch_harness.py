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
import subprocess
import sys
import tempfile
from typing import Sequence

DEFAULT_CFLAGS = ("-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function")


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
        nargs="+",
        default=[os.environ.get("CC", "cc")],
        help="C compiler command (must be the last option)",
    )
    return parser


def build_and_run(
    source: str,
    cc: Sequence[str],
    prefix: str = "pelorus-harness-",
    cflags: Sequence[str] = (),
    libs: Sequence[str] = (),
) -> tuple[int, str]:
    """Build ``source`` strictly in a tempdir and run it.

    Returns (status, output): 0 = ran and exited 0, 1 = ran and failed,
    2 = did not compile. ``libs`` follow the source file so the linker
    resolves them under --as-needed.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        tmp_path = pathlib.Path(tmp)
        c_file = tmp_path / "harness.c"
        exe = tmp_path / ("harness.exe" if os.name == "nt" else "harness")
        c_file.write_text(source, encoding="utf-8")
        build = subprocess.run(
            [*cc, *DEFAULT_CFLAGS, *cflags, str(c_file), "-o", str(exe), *libs],
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            return 2, "harness failed to compile:\n" + build.stdout + build.stderr
        run = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
        return (0 if run.returncode == 0 else 1), run.stdout + run.stderr


def compile_and_run(
    source: str,
    cc: Sequence[str],
    prefix: str = "pelorus-harness-",
    libs: Sequence[str] = (),
) -> int:
    """build_and_run plus reporting; returns the script exit code (0 or 1)."""
    status, output = build_and_run(source, cc, prefix, libs=libs)
    if status == 2:
        print("ERROR: " + output, file=sys.stderr)
        return 1
    sys.stdout.write(output)
    if status != 0:
        print("ERROR: harness exited non-zero", file=sys.stderr)
        return 1
    return 0
