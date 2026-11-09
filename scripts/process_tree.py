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

import ctypes
from ctypes import wintypes
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

# Windows process probe (winnt.h, winbase.h, winerror.h): a SYNCHRONIZE handle
# is signalled once the process exits; OpenProcess on a pid no process holds
# fails with ERROR_INVALID_PARAMETER, and on a protected one with ACCESS_DENIED.
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87


def stop_tree(proc: subprocess.Popen) -> bool:
    """Kill ``proc`` and its descendants; return False when the tree step failed.

    Windows: ``taskkill /T /F`` ends the tree under ``proc``. POSIX: SIGKILL goes
    to the process group ``proc`` leads; a pid of 1 or below is never signalled.
    ``proc.kill()`` then ends the direct child when either step could not. A
    ``proc`` already reaped is not signalled: its pid may belong to another
    process by now. A failed or skipped tree step (taskkill missing, hung or
    exiting non-zero, which includes a root that already exited, since taskkill
    walks the tree from a live root) is named on stderr with the warning that
    processes ``proc`` started may still run: the fallback is never silent.
    """
    if proc.returncode is not None:
        failure = f"pid {proc.pid} was already reaped, so its tree was not signalled"
    elif os.name == "nt":
        failure = _stop_windows_tree(proc)
    else:
        failure = _stop_process_group(proc)
    proc.kill()
    if failure is None:
        return True
    print(
        f"process_tree: {failure}; processes pid {proc.pid} started may still run",
        file=sys.stderr,
    )
    return False


def _stop_windows_tree(
    proc: subprocess.Popen, taskkill: tuple[str, ...] = ("taskkill",)
) -> str | None:
    """Run ``taskkill /T /F`` on ``proc``; return why it failed, or None.

    ``taskkill`` is the argv prefix; the self-test plants a failing stand-in.
    """
    try:
        result = subprocess.run(  # nosec B603, B607 - taskkill process tree on deadline; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - taskkill process tree on deadline, list argv, no shell, timeout
            [*taskkill, "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
            errors="replace",
            timeout=TASKKILL_TIMEOUT_S,
        )
    except OSError as exc:
        return f"taskkill could not start ({exc})"
    except subprocess.TimeoutExpired:
        return f"taskkill still running after {TASKKILL_TIMEOUT_S} s"
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return f"taskkill exited {result.returncode}" + (f" ({detail[0]})" if detail else "")


def _stop_process_group(proc: subprocess.Popen) -> str | None:
    """SIGKILL the process group ``proc`` leads; return why it could not, or None."""
    if proc.pid <= 1:
        return f"refusing to signal process group {proc.pid}"
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        # Group already empty: no process proc started is left in it.
        pass
    return None


def _windows_pid_gone(pid: int) -> bool:
    """Probe ``pid`` through a SYNCHRONIZE handle; never signals the process."""
    if sys.platform != "win32":
        raise OSError("_windows_pid_gone runs on Windows only")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return True
        if error == _ERROR_ACCESS_DENIED:
            return False
        raise ctypes.WinError(error)
    try:
        state = kernel32.WaitForSingleObject(handle, 0)
    finally:
        kernel32.CloseHandle(handle)
    if state not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
        raise ctypes.WinError(ctypes.get_last_error())
    return state == _WAIT_OBJECT_0


def pid_gone(pid: int) -> bool:
    """Return True when ``pid`` no longer runs; a Linux zombie counts as gone.

    Windows probes a SYNCHRONIZE handle (an exited process whose handle is still
    open counts as gone); ``os.kill(pid, 0)`` would terminate it there. A pid of
    1 or below is refused (HISS-08). A pid reused by a new process reads as live.
    """
    if pid <= 1:
        raise ValueError(f"refusing to probe pid {pid}")
    if os.name == "nt":
        return _windows_pid_gone(pid)
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
