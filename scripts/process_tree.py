#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Stop a child process together with every process it started (HISS-02, HISS-08).

Killing only the direct child leaves its descendants running: a hung compiler
driver's grandchild, or a test process that ``meson test`` started. Callers
load this module by path, since ``python3 -I`` drops the script directory from
``sys.path``.

On POSIX the child must lead its own process group (``start_new_session=True``).
On Windows ``taskkill /T`` walks the live parent chain, so no creation flag is
needed for the stop itself.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess  # nosec B404 - fixed taskkill argv, no shell, explicit deadline
import sys
import time

# Deadline for taskkill itself; proc.kill() still ends the direct child after it.
TASKKILL_TIMEOUT_S = 30
# Poll step and default wait for a stopped process to disappear.
PID_POLL_S = 0.05
PID_GONE_TIMEOUT_S = 5.0


def stop_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and its descendants; never raises for an already-exited tree.

    Windows: ``taskkill /T /F`` ends the tree under ``proc``. POSIX: SIGKILL goes
    to the process group ``proc`` leads; a pid of 1 or below is never signalled.
    ``proc.kill()`` then ends the direct child when either step could not.
    """
    if os.name == "nt":
        try:
            subprocess.run(  # nosec B603, B607 - taskkill process tree on deadline; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - taskkill process tree on deadline, list argv, no shell, timeout
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=TASKKILL_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            # taskkill missing or hung: proc.kill() below still ends the direct child.
            pass
    elif proc.pid > 1:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            # Group already gone, or proc does not lead one: proc.kill() covers proc.
            pass
    proc.kill()


def pid_gone(pid: int) -> bool:
    """Return True when ``pid`` no longer runs; a Linux zombie counts as gone.

    POSIX only: on Windows ``os.kill(pid, 0)`` terminates the process instead of
    probing it, so this refuses there. A pid of 1 or below is refused (HISS-08).
    """
    if os.name == "nt":
        raise OSError("pid_gone is POSIX-only: os.kill(pid, 0) terminates on Windows")
    if pid <= 1:
        raise ValueError(f"refusing to probe pid {pid}")
    if sys.platform.startswith("linux"):
        try:
            stat_text = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            parts = stat_text.rpartition(")")[2].split()
            if parts and parts[0] == "Z":
                return True
        except (OSError, UnicodeDecodeError):
            # /proc entry vanished or unreadable: the os.kill(pid, 0) probe decides.
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def wait_pid_gone(pid: int, timeout: float = PID_GONE_TIMEOUT_S) -> bool:
    """Return True when ``pid`` is gone within ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    for _ in range(int(timeout / PID_POLL_S) + 1):
        if pid_gone(pid):
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(PID_POLL_S)
    return False
