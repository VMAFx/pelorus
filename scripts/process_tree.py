#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Start a child as the root of a process tree, and stop that tree whole (HISS-02, HISS-08).

Killing only the direct child leaves its descendants running: a hung compiler
driver's grandchild, or a test process that ``meson test`` started. Callers
load this module by path, since ``python3 -I`` drops the script directory from
``sys.path``.

``TreePopen`` starts the child so that ``stop_tree`` reaches every process it
starts. On POSIX the child leads its own process group. On Windows the child
joins a job object before it runs any code, and every process it starts joins
that job too. A launcher that puts its own child into a nested job allowing
breakaway does not change this: a breakaway child moves up the job hierarchy
until it reaches a job that does not permit breakaway, and this job permits
none (Microsoft Learn, "Nested Jobs"). ``TerminateJobObject`` then ends the
tree even after its root exited, which ``taskkill /T`` could not do, since it
walks the tree down from a live root.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import pathlib
import signal
import subprocess  # nosec B404 - Popen subclass for callers' list argv, no shell
import sys
import time
from typing import Any

# Poll step and default wait for a stopped process to disappear.
PID_POLL_S = 0.05
PID_GONE_TIMEOUT_S = 5.0

# Windows (winbase.h, winnt.h, tlhelp32.h, winerror.h). A SYNCHRONIZE handle is
# signalled once the process exits; OpenProcess on a pid no process holds fails
# with ERROR_INVALID_PARAMETER, and on a protected one with ACCESS_DENIED.
_CREATE_SUSPENDED = 0x00000004
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_SYNCHRONIZE = 0x00100000
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_RESUME_FAILED = 0xFFFFFFFF
_ERROR_ACCESS_DENIED = 5
_ERROR_NO_MORE_FILES = 18
_ERROR_INVALID_PARAMETER = 87
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
# Exit status TerminateJobObject gives each process it ends, as Popen.kill() does.
_JOB_KILL_EXIT_CODE = 1
# HISS-02: upper bound on the system-wide thread snapshot walked to resume a child.
_MAX_SNAPSHOT_THREADS = 1 << 20


class _ThreadEntry(ctypes.Structure):
    """THREADENTRY32 (tlhelp32.h)."""

    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


_KERNEL32_SIGNATURES = (
    ("OpenProcess", (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
    ("WaitForSingleObject", (wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
    ("CloseHandle", (wintypes.HANDLE,), wintypes.BOOL),
    ("CreateJobObjectW", (wintypes.LPVOID, wintypes.LPCWSTR), wintypes.HANDLE),
    ("AssignProcessToJobObject", (wintypes.HANDLE, wintypes.HANDLE), wintypes.BOOL),
    ("TerminateJobObject", (wintypes.HANDLE, wintypes.UINT), wintypes.BOOL),
    ("CreateToolhelp32Snapshot", (wintypes.DWORD, wintypes.DWORD), wintypes.HANDLE),
    ("Thread32First", (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)), wintypes.BOOL),
    ("Thread32Next", (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)), wintypes.BOOL),
    ("OpenThread", (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD), wintypes.HANDLE),
    ("ResumeThread", (wintypes.HANDLE,), wintypes.DWORD),
)


class TreePopen(subprocess.Popen):
    """``subprocess.Popen`` whose child roots a tree that ``stop_tree`` ends whole.

    POSIX: the child leads a new session. Windows: the child starts suspended,
    joins a new job object (``job``), then resumes, so it starts no process
    outside the job. A child that cannot join the job is killed and the start
    raises OSError naming the failed call: the run never goes on with a stop
    that reaches the child only. Callers pass a list argv and no shell. Once
    ``wait`` reaps the child, the job handle is closed; the job ends no process
    on close, so a normal exit leaves descendants alone, as on POSIX.
    """

    def __init__(self, args: Any, **kwargs: Any) -> None:
        self.job: int | None = None
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        super().__init__(args, **kwargs)
        if os.name == "nt":
            self._join_job()

    def wait(self, timeout: float | None = None) -> int:
        """``Popen.wait``, closing the job handle once the child is reaped."""
        status = super().wait(timeout=timeout)
        _close_job(self)
        return status

    def _join_job(self) -> None:
        """Put the suspended child into a new job, then resume it; kill it on failure."""
        try:
            self.job = _new_job_with(self.pid)
        except BaseException:
            self.kill()
            try:
                super().wait(timeout=PID_GONE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # The kill was sent; the exception re-raised below names the failed start.
                pass
            raise


def _kernel32() -> Any:
    """kernel32 with the argument and result types this module calls it with."""
    if sys.platform != "win32":
        raise OSError("the Windows process calls run on Windows only")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, argtypes, restype in _KERNEL32_SIGNATURES:
        function = getattr(kernel32, name)
        function.argtypes = argtypes
        function.restype = restype
    return kernel32


def _last_error() -> int:
    """The error code the last kernel32 call through ``_kernel32()`` left."""
    if sys.platform != "win32":
        raise OSError("the Windows process calls run on Windows only")
    return ctypes.get_last_error()


def _windows_error(call: str, error: int) -> OSError:
    """OSError naming the failed Windows call and the system's text for ``error``."""
    if sys.platform != "win32":
        raise OSError("the Windows process calls run on Windows only")
    text = ctypes.FormatError(error).strip()
    return OSError(f"{call} failed: {text} (error {error})")


def _new_job_with(pid: int) -> int:
    """Assign the suspended ``pid`` to a new job, then resume it; return the job handle.

    The child is suspended and its ``Popen`` holds a handle to it, so ``pid``
    cannot name another process here.
    """
    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise _windows_error("CreateJobObjectW", _last_error())
    try:
        _assign_to_job(kernel32, job, pid)
        if _resume_process_threads(kernel32, pid) == 0:
            raise OSError(f"pid {pid} has no suspended thread to resume")
    except BaseException:
        kernel32.CloseHandle(job)
        raise
    return job


def _assign_to_job(kernel32: Any, job: int, pid: int) -> None:
    """AssignProcessToJobObject through a handle with the rights that call needs."""
    process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
    if not process:
        raise _windows_error(f"OpenProcess for pid {pid}", _last_error())
    try:
        assigned = kernel32.AssignProcessToJobObject(job, process)
        error = _last_error()
    finally:
        kernel32.CloseHandle(process)
    if not assigned:
        raise _windows_error(f"AssignProcessToJobObject for pid {pid}", error)


def _resume_process_threads(kernel32: Any, pid: int) -> int:
    """Resume the suspended threads of ``pid``; return how many were suspended.

    ``Popen`` closes the thread handle ``CreateProcess`` returned, so the
    threads are found in a Toolhelp snapshot.
    """
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot in (None, _INVALID_HANDLE_VALUE):
        raise _windows_error("CreateToolhelp32Snapshot", _last_error())
    try:
        return _resume_owned_threads(kernel32, snapshot, pid)
    finally:
        kernel32.CloseHandle(snapshot)


def _resume_owned_threads(kernel32: Any, snapshot: int, pid: int) -> int:
    """Walk ``snapshot`` and resume each thread ``pid`` owns (HISS-02 bounded walk)."""
    entry = _ThreadEntry()
    entry.dwSize = ctypes.sizeof(_ThreadEntry)
    found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
    resumed = 0
    for _ in range(_MAX_SNAPSHOT_THREADS):
        if not found:
            error = _last_error()
            if error != _ERROR_NO_MORE_FILES:
                raise _windows_error("Thread32First or Thread32Next", error)
            return resumed
        if entry.th32OwnerProcessID == pid:
            resumed += _resume_thread(kernel32, entry.th32ThreadID)
        entry.dwSize = ctypes.sizeof(_ThreadEntry)
        found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    raise OSError(f"thread snapshot longer than {_MAX_SNAPSHOT_THREADS} entries")


def _resume_thread(kernel32: Any, tid: int) -> int:
    """Resume thread ``tid``; return 1 when it was suspended, else 0."""
    thread = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, tid)
    if not thread:
        raise _windows_error(f"OpenThread for thread {tid}", _last_error())
    try:
        previous = kernel32.ResumeThread(thread)
        error = _last_error()
    finally:
        kernel32.CloseHandle(thread)
    if previous == _RESUME_FAILED:
        raise _windows_error(f"ResumeThread for thread {tid}", error)
    return 1 if previous > 0 else 0


def _close_job(proc: Any) -> None:
    """Close ``proc.job`` when it holds one; the processes in the job keep running."""
    job, proc.job = getattr(proc, "job", None), None
    if job is not None:
        _kernel32().CloseHandle(job)


def stop_tree(proc: subprocess.Popen) -> bool:
    """Kill ``proc`` and its descendants; return False when the tree step failed.

    A ``TreePopen`` on Windows: ``TerminateJobObject`` ends every process in
    its job, whether or not ``proc`` still runs, and the job handle is closed.
    POSIX: SIGKILL goes to the process group ``proc`` leads; a pid of 1 or
    below is never signalled. A ``proc`` already reaped and holding no job is
    not signalled, since its pid may belong to another process by now.
    ``proc.kill()`` then ends the direct child. A failed or skipped tree step
    (a failed TerminateJobObject, a Windows ``proc`` no job holds, a POSIX
    ``proc`` that leads no process group) is named on stderr with the warning
    that processes ``proc`` started may still run: the fallback is never silent.
    """
    if getattr(proc, "job", None) is not None:
        failure = _terminate_job(proc)
    elif proc.returncode is not None:
        failure = f"pid {proc.pid} was already reaped, so its tree was not signalled"
    elif os.name == "nt":
        failure = f"no job object holds the tree of pid {proc.pid}"
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


def _terminate_job(proc: Any) -> str | None:
    """TerminateJobObject on ``proc.job``, then close it; return why it failed, or None."""
    kernel32 = _kernel32()
    job, proc.job = proc.job, None
    terminated = kernel32.TerminateJobObject(job, _JOB_KILL_EXIT_CODE)
    error = _last_error()
    kernel32.CloseHandle(job)
    if terminated:
        return None
    return str(_windows_error("TerminateJobObject", error))


def _stop_process_group(proc: subprocess.Popen) -> str | None:
    """SIGKILL the process group ``proc`` leads; return why it could not, or None."""
    if proc.pid <= 1:
        return f"refusing to signal process group {proc.pid}"
    try:
        if os.getpgid(proc.pid) != proc.pid:
            return f"pid {proc.pid} leads no process group; its tree was not signalled"
    except ProcessLookupError:
        # No process answers for the pid any more; a group it led may outlive it.
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        # Group already empty: no process proc started is left in it.
        pass
    return None


def _windows_pid_gone(pid: int) -> bool:
    """Probe ``pid`` through a SYNCHRONIZE handle; never signals the process."""
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        error = _last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return True
        if error == _ERROR_ACCESS_DENIED:
            return False
        raise _windows_error(f"OpenProcess for pid {pid}", error)
    try:
        state = kernel32.WaitForSingleObject(handle, 0)
    finally:
        kernel32.CloseHandle(handle)
    if state not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
        raise _windows_error(f"WaitForSingleObject for pid {pid}", _last_error())
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
