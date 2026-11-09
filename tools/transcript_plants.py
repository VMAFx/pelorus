# Copyright 2026 Lusoris
#
# SPDX-License-Identifier: EUPL-1.2

"""Plant data for transcript driver self-tests."""

from __future__ import annotations


def runtime_plants(tool_name: str) -> list[tuple[str, str, int]]:
    """Return runtime test cases verifying stream diff and exit code handling."""
    pass_case = (
        f"$ {tool_name}\n"
        f"! {tool_name}: <x265.csv> is required\n"
        f"! usage: {tool_name} <x265.csv> [--requested-qp N]\n"
        "[exit 1]"
    )
    return [
        (
            "a (wrong byte)",
            f"$ {tool_name}\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! Usage: {tool_name} <x265.csv> [--requested-qp N]\n"
            "[exit 1]",
            1,
        ),
        (
            "b (wrong exit code)",
            f"$ {tool_name}\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! usage: {tool_name} <x265.csv> [--requested-qp N]\n"
            "[exit 2]",
            1,
        ),
        (
            "c (missing stderr line)",
            f"$ {tool_name}\n! {tool_name}: <x265.csv> is required\n[exit 1]",
            1,
        ),
        (
            "d (extra stdout line)",
            f"$ {tool_name}\n"
            "> extra\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! usage: {tool_name} <x265.csv> [--requested-qp N]\n"
            "[exit 1]",
            1,
        ),
        (
            "m (copy non-ascii filename directive)",
            f"% copy x265-4.3-qp32.csv café_文件.csv\n"
            f"$ {tool_name} café_文件.csv\n"
            "> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)\n"
            ">   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)\n"
            ">   frames parsed     : 10\n"
            ">   avg_qp (bit-wtd)  : 32.0000\n"
            ">   total_bits        : 59928\n"
            ">   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000\n"
            ">   qp_valid          : 0  (0 => frame-granular, no per-cell map)\n"
            ">   honored_fraction  : 0.0000  (no requested map supplied)\n"
            "[exit 0]",
            0,
        ),
        ("pass", pass_case, 0),
    ]


def capture_plants() -> list[tuple[str, str, str]]:
    """Return Python stubs run through capture with the verdict each must get."""
    return [
        (
            "p (output flood)",
            "import sys, time; sys.stdout.write('x' * (2 << 20)); sys.stdout.flush(); time.sleep(60)",
            "overflow-stdout",
        ),
        (
            # The child passes its own pipes on explicitly, so it holds them on
            # Windows as well, where handles are not inherited by default. It
            # runs outside the self-test directory: Windows refuses to remove a
            # directory that a live process uses as its working directory.
            "r (child process holds stdout)",
            "import os, subprocess, sys; subprocess.Popen("
            "[sys.executable, '-c', 'import time; time.sleep(15)'], "
            "stdout=sys.stdout, stderr=sys.stderr, cwd=os.path.dirname(sys.executable))",
            "held-stdout",
        ),
    ]


def parse_plants(tool_name: str) -> list[tuple[str, str, int]]:
    """Return syntactically malformed or boundary transcripts for parser testing."""
    return [
        (
            "e (missing exit line)",
            f"$ {tool_name}\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! usage: {tool_name} <x265.csv> [--requested-qp N]",
            2,
        ),
        ("f (unknown line prefix)", "? unknown prefix\n[exit 0]", 2),
        (
            "g (path traversal in directive)",
            f"% copy ../x y\n$ {tool_name} y\n[exit 0]",
            2,
        ),
        ("h (quoted command argument)", f'$ {tool_name} "quoted"\n[exit 1]', 2),
        ("i (trailing space after argument)", f"$ {tool_name} \n[exit 1]", 2),
        (
            "j (invalid destination syntax)",
            f"% copy x265-4.3-qp32.csv a:b\n$ {tool_name} a:b\n[exit 0]",
            2,
        ),
        (
            "k (leading dot in fixture name)",
            f"% copy .hidden y\n$ {tool_name} y\n[exit 0]",
            2,
        ),
        ("l (non-ascii exit code)", f"$ {tool_name}\n[exit ²]", 2),
        ("n (empty transcript)", "", 2),
        ("o (comment-only transcript)", "# comment only\n", 2),
    ]
