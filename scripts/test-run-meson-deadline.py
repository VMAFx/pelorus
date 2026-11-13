#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check the deadline and process-tree stop of scripts/run-meson-test.py (HISS-02).

On Windows the wrapper waits for ``meson test`` as a child, started through
``process_tree.TreePopen``. This test runs on the Linux CI leg and the three
Windows legs (MSYS2, clang-cl, MSVC), with live processes:

1. A quick child's exit status passes through; a hung child is stopped at its
   deadline with status 124.
2. A test process the hung child started is stopped with it (POSIX process
   group; Windows job object). Windows: the same holds when the child first
   moves into a nested job that lets the processes it starts break away, as
   the console-script launcher pip installs does for the Python it starts.
3. A child that exits after the wait gave up, leaving its test process
   running, still gets its tree stopped on every OS, with no warning.
4. A tree step that cannot reach the tree is named on stderr, never a silent
   fallback: a POSIX child that leads no process group, a Windows child that
   no job holds, and a failed TerminateJobObject.
5. Windows: a child that cannot join its job is killed and the start raises.
6. Stopping a root that was already reaped signals nothing and says so.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import select
import signal
import subprocess  # nosec B404 - fixed interpreter argv, no shell, explicit deadline
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
RUN_MESON_TEST_SCRIPT = ROOT / "scripts" / "run-meson-test.py"

# Deadline for a short self-test child to exit on its own (HISS-02).
CHILD_WAIT_TIMEOUT_S = 10
# Deadline for a planted pid file to appear (HISS-02).
PID_FILE_TIMEOUT_S = 10.0
PID_FILE_POLL_S = 0.05
# Deadline the hung child gets before the wrapper stops it.
TREE_DEADLINE_S = 2.0

# A stand-in for Meson that starts one long-lived test process, records its
# pid, then hangs: killing only the direct child would leave the test running.
_TREE_CHILD = (
    "import os, subprocess, sys, time\n"
    "test = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])\n"
    "with open(sys.argv[1] + '.tmp', 'w', encoding='utf-8') as f:\n"
    "    f.write(str(test.pid))\n"
    "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
    "time.sleep(60)\n"
)
# The same stand-in, except that it exits on its own half a second after its
# test started and leaves the test running. A probe that reported the exit
# early would stop a live stand-in, whose status then is not 0.
_EXITING_CHILD = _TREE_CHILD.rpartition("time.sleep(60)\n")[0] + "time.sleep(0.5)\n"
# Windows: the stand-in first moves into a nested job with the limits a
# console-script launcher sets (SILENT_BREAKAWAY_OK 0x1000, KILL_ON_JOB_CLOSE
# 0x2000, through JobObjectExtendedLimitInformation 9), so its test process
# breaks away from that job and must stay in the job of the wrapper.
_LAUNCHER_JOB = (
    "import ctypes\n"
    "from ctypes import wintypes\n"
    "class Basic(ctypes.Structure):\n"
    "    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),\n"
    "                ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),\n"
    "                ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),\n"
    "                ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),\n"
    "                ('scheduling', wintypes.DWORD)]\n"
    "class Extended(ctypes.Structure):\n"
    "    _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6),\n"
    "                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),\n"
    "                ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]\n"
    "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "k.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)\n"
    "k.CreateJobObjectW.restype = wintypes.HANDLE\n"
    "k.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)\n"
    "k.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)\n"
    "k.GetCurrentProcess.restype = wintypes.HANDLE\n"
    "job = k.CreateJobObjectW(None, None)\n"
    "info = Extended()\n"
    "info.basic.flags = 0x1000 | 0x2000\n"
    "if not (job and k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))\n"
    "        and k.AssignProcessToJobObject(job, k.GetCurrentProcess())):\n"
    "    raise SystemExit(f'launcher job: error {ctypes.get_last_error()}')\n"
)
_LAUNCHER_CHILD = _LAUNCHER_JOB + _TREE_CHILD


def _load_module(name: str, path: Path) -> ModuleType:
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fail(failures: list[str], msg: str) -> None:
    """Record and print one failure."""
    print(f"FAIL: {msg}", file=sys.stderr)
    failures.append(msg)


def _read_pid(pid_file: Path) -> int:
    """Wait for ``pid_file`` and return the pid in it, or 0."""
    for _ in range(int(PID_FILE_TIMEOUT_S / PID_FILE_POLL_S) + 1):
        if pid_file.is_file():
            text = pid_file.read_text(encoding="utf-8").strip()
            return int(text) if text.isdigit() else 0
        time.sleep(PID_FILE_POLL_S)
    return 0


def _kill_leftover(pid: int) -> None:
    """End a test process a failed case left running (cleanup only, HISS-08)."""
    if pid <= 1:
        return
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        # Exited between the last probe and this cleanup kill.
        pass


def _wait_exited_unreaped(
    proc: subprocess.Popen, tree: ModuleType, exit_read: int | None
) -> bool:
    """Wait until ``proc`` exits without reaping it, so its pid stays reserved.

    POSIX waits for end of file on ``exit_read``, a pipe whose write end only
    ``proc`` holds: a process closes its descriptors as it exits, before it is
    reaped. This needs neither ``os.waitid`` (on macOS only from Python 3.13)
    nor ``pid_gone`` (an unreaped macOS process still answers ``kill(pid, 0)``),
    so Linux runs the probe macOS runs. Windows probes the process handle.
    """
    for _ in range(int(CHILD_WAIT_TIMEOUT_S / PID_FILE_POLL_S) + 1):
        if exit_read is not None:
            if select.select([exit_read], [], [], PID_FILE_POLL_S)[0]:
                # The stand-in never writes, so a readable end is end of file.
                return os.read(exit_read, 1) == b""
        elif tree.pid_gone(proc.pid):
            return True
        else:
            time.sleep(PID_FILE_POLL_S)
    return False


def _start_exiting_root(
    tree: ModuleType, pid_file: Path
) -> tuple[subprocess.Popen, int, bool]:
    """Start the exiting stand-in; return it, its test pid and whether it exited.

    On POSIX the stand-in alone gets the write end of the exit pipe: its test
    process starts with ``close_fds`` and does not inherit it.
    """
    exit_read, exit_write = (None, None) if os.name == "nt" else os.pipe()
    popen_kw = {} if exit_write is None else {"pass_fds": (exit_write,)}
    try:
        try:
            proc = tree.TreePopen(
                [sys.executable, "-I", "-c", _EXITING_CHILD, str(pid_file)],
                **popen_kw,
            )
        finally:
            if exit_write is not None:
                os.close(exit_write)
        test_pid = _read_pid(pid_file)
        exited = _wait_exited_unreaped(proc, tree, exit_read)
    finally:
        if exit_read is not None:
            os.close(exit_read)
    return proc, test_pid, exited


def _stop_and_capture(tree: ModuleType, proc: subprocess.Popen) -> tuple[bool, str]:
    """``stop_tree(proc)`` with its stderr captured, then reap ``proc``."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        stopped = tree.stop_tree(proc)
    proc.wait(timeout=CHILD_WAIT_TIMEOUT_S)
    return stopped, stderr.getvalue()


def test_child_deadline(run_mod: ModuleType) -> list[str]:
    """A quick child's status passes through; a hung child is stopped with 124."""
    failures: list[str] = []
    quick = [sys.executable, "-I", "-c", "raise SystemExit(3)"]
    if run_mod._run_and_wait(quick, deadline_s=30) != 3:
        _fail(
            failures,
            "child deadline: a quick child's exit status 3 was not passed through",
        )
    hung = [sys.executable, "-I", "-c", "import time; time.sleep(30)"]
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        status = run_mod._run_and_wait(hung, deadline_s=0.5)
    if (
        status != run_mod.STOPPED_EXIT_STATUS
        or "still running after 0.5 s" not in stderr.getvalue()
    ):
        _fail(
            failures,
            f"child deadline: hung child gave status {status}, stderr {stderr.getvalue()!r}",
        )
    if not failures:
        print("PASS: wrapper stops a child at its deadline")
    return failures


def _tree_deadline_case(run_mod: ModuleType, script: str, label: str) -> list[str]:
    """Run ``script`` through ``_run_and_wait``; its test process must stop at the deadline.

    The child goes through ``_run_and_wait``, so the way it starts Meson stays
    under test. The deadline starts only once the child has written its test
    pid: a slow runner cannot stop the child before the state this case
    checks exists.
    """
    failures: list[str] = []
    tree = run_mod.process_tree
    real_wait_or_stop = run_mod._wait_or_stop
    with tempfile.TemporaryDirectory(prefix="pelorus-meson-tree-") as tmp:
        pid_file = Path(tmp) / "test.pid"
        child = [sys.executable, "-I", "-c", script, str(pid_file)]
        test_pids: list[int] = []

        def wait_after_start(proc: subprocess.Popen, deadline_s: float) -> int:
            test_pids.append(_read_pid(pid_file))
            return real_wait_or_stop(proc, deadline_s)

        # _run_and_wait looks _wait_or_stop up in its module namespace.
        namespace = vars(run_mod)
        namespace["_wait_or_stop"] = wait_after_start
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                status = run_mod._run_and_wait(child, deadline_s=TREE_DEADLINE_S)
        finally:
            namespace["_wait_or_stop"] = real_wait_or_stop
        test_pid = test_pids[0] if test_pids else 0
        if status != run_mod.STOPPED_EXIT_STATUS or test_pid <= 1:
            _fail(
                failures,
                f"{label}: status {status}, test pid {test_pid}, stderr {stderr.getvalue()!r}",
            )
        elif not tree.wait_pid_gone(test_pid):
            _kill_leftover(test_pid)
            _fail(
                failures,
                f"{label}: test process {test_pid} still running after the deadline",
            )
    return failures


def test_child_tree_deadline(run_mod: ModuleType) -> list[str]:
    """A test process the hung child started is stopped with it (HISS-02, HISS-08)."""
    failures = _tree_deadline_case(run_mod, _TREE_CHILD, "child tree")
    if not failures:
        print("PASS: wrapper stops the test processes of a hung child")
    return failures


def test_nested_launcher_job(run_mod: ModuleType) -> list[str]:
    """Windows: a test process that breaks away from a nested job still stops."""
    if os.name != "nt":
        print("SKIP: nested launcher job: job objects exist on Windows only")
        return []
    failures = _tree_deadline_case(run_mod, _LAUNCHER_CHILD, "nested launcher job")
    if not failures:
        print("PASS: wrapper stops a test process that broke away from a nested job")
    return failures


def test_root_exits_after_deadline(run_mod: ModuleType) -> list[str]:
    """A root that exits after the wait gave up still gets its tree stopped, on every OS."""
    failures: list[str] = []
    tree = run_mod.process_tree
    with tempfile.TemporaryDirectory(prefix="pelorus-meson-exited-") as tmp:
        proc, test_pid, exited = _start_exiting_root(tree, Path(tmp) / "test.pid")
        real_wait = proc.wait
        timed_out: list[float | None] = []

        def wait_past_deadline(timeout: float | None = None) -> int:
            # The first wait gives up at the deadline, as if Meson exited just after it.
            if not timed_out:
                timed_out.append(timeout)
                raise subprocess.TimeoutExpired(proc.args, timeout or 0.0)
            return real_wait(timeout=timeout)

        proc.wait = wait_past_deadline  # type: ignore[method-assign]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = run_mod._wait_or_stop(proc, deadline_s=0.1)
        # A root that exited on its own keeps status 0 through the stop.
        if (
            not exited
            or test_pid <= 1
            or status != run_mod.STOPPED_EXIT_STATUS
            or proc.returncode != 0
        ):
            _fail(
                failures,
                f"exited root: exited {exited}, test pid {test_pid}, status {status}, "
                f"root status {proc.returncode}",
            )
        elif not tree.wait_pid_gone(test_pid):
            _kill_leftover(test_pid)
            _fail(
                failures,
                f"exited root: test process {test_pid} still running after the stop: "
                f"stderr {stderr.getvalue()!r}",
            )
        elif "may still run" in stderr.getvalue():
            _fail(
                failures,
                f"exited root: a stop that worked warned: {stderr.getvalue()!r}",
            )
        if test_pid > 1 and not tree.pid_gone(test_pid):
            _kill_leftover(test_pid)
    if not failures:
        print("PASS: wrapper stops the tree of a root that exited after the deadline")
    return failures


def _failed_terminate_is_named(tree: ModuleType, sleeper: list[str]) -> list[str]:
    """Windows: a TerminateJobObject on a handle that is no job is named with its cause."""
    failures: list[str] = []
    proc = tree.TreePopen(sleeper)
    real_job, proc.job = proc.job, 0
    try:
        stopped, text = _stop_and_capture(tree, proc)
    finally:
        proc.job = real_job
        tree._close_job(proc)
    if (
        stopped
        or "TerminateJobObject failed" not in text
        or "may still run" not in text
    ):
        _fail(failures, f"failed job stop: returned {stopped}, stderr {text!r}")
    return failures


def test_failed_tree_step_is_named(run_mod: ModuleType) -> list[str]:
    """A tree step that cannot reach the tree is named with its cause (no silent fallback)."""
    failures: list[str] = []
    tree = run_mod.process_tree
    sleeper = [sys.executable, "-I", "-c", "import time; time.sleep(30)"]
    # A plain Popen: no process group of its own on POSIX, no job on Windows.
    plain = subprocess.Popen(sleeper)  # nosec B603 - fixed interpreter argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed interpreter argv, no shell, deadline below
    stopped, text = _stop_and_capture(tree, plain)
    expected = (
        "no job object holds the tree" if os.name == "nt" else "leads no process group"
    )
    if stopped or expected not in text or "may still run" not in text:
        _fail(failures, f"unreachable tree: returned {stopped}, stderr {text!r}")
    if os.name == "nt":
        failures.extend(_failed_terminate_is_named(tree, sleeper))
    if not failures:
        print("PASS: a tree step that cannot reach the tree is named with its cause")
    return failures


def test_failed_job_start_kills_child(run_mod: ModuleType) -> list[str]:
    """Windows: a child that cannot join its job is killed and the start raises."""
    if os.name != "nt":
        print("SKIP: failed job start: job objects exist on Windows only")
        return []
    failures: list[str] = []
    tree = run_mod.process_tree
    namespace = vars(tree)
    real_new_job = namespace["_new_job_with"]
    seen: list[int] = []

    def planted_failure(pid: int) -> int:
        seen.append(pid)
        raise OSError("planted job failure")

    namespace["_new_job_with"] = planted_failure
    try:
        tree.TreePopen([sys.executable, "-I", "-c", "import time; time.sleep(30)"])
    except OSError as exc:
        raised = str(exc)
    else:
        raised = ""
    finally:
        namespace["_new_job_with"] = real_new_job
    pid = seen[0] if seen else 0
    if "planted job failure" not in raised or pid <= 1:
        _fail(failures, f"failed job start: raised {raised!r}, child pid {pid}")
    elif not tree.wait_pid_gone(pid):
        _kill_leftover(pid)
        _fail(
            failures,
            f"failed job start: child {pid} still running after the start raised",
        )
    if not failures:
        print("PASS: a child that cannot join its job is killed and the start raises")
    return failures


def test_stop_tree_after_reap(run_mod: ModuleType) -> list[str]:
    """Stopping a reaped root raises nothing, signals nothing and says so."""
    failures: list[str] = []
    done = run_mod.process_tree.TreePopen([sys.executable, "-I", "-c", "pass"])
    done.wait(timeout=CHILD_WAIT_TIMEOUT_S)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            stopped = run_mod.process_tree.stop_tree(done)
    except OSError as exc:
        _fail(failures, f"stop_tree on a reaped process raised {exc!r}")
    else:
        if stopped or "already reaped" not in stderr.getvalue():
            _fail(
                failures,
                f"reaped root: returned {stopped}, stderr {stderr.getvalue()!r}",
            )
    if not failures:
        print("PASS: stopping a reaped root signals nothing and says so")
    return failures


def test_pid_gone_permission_denied(run_mod: ModuleType) -> list[str]:
    """EPERM means a live process, including when tested off POSIX."""
    tree = run_mod.process_tree
    namespace = vars(tree)
    real_os = namespace["os"]
    pid = os.getpid()
    failures: list[str] = []

    def denied(target: int, sig: int) -> None:
        if (target, sig) != (pid, 0):
            raise AssertionError(f"unexpected kill({target}, {sig})")
        raise PermissionError("planted EPERM")

    namespace["os"] = SimpleNamespace(name="posix", kill=denied)
    try:
        if tree.pid_gone(pid):
            _fail(failures, "pid_gone permission denied: live pid reported gone")
    except PermissionError:
        _fail(failures, "pid_gone permission denied: EPERM escaped")
    finally:
        namespace["os"] = real_os
    if not failures:
        print("PASS: pid_gone permission denied means live")
    return failures


def test_windows_wait_error_preserved(run_mod: ModuleType) -> list[str]:
    """CloseHandle must not overwrite the failed wait's error; no Windows calls."""
    tree = run_mod.process_tree
    namespace = vars(tree)
    saved = {name: namespace[name] for name in ("_kernel32", "_last_error", "_windows_error")}
    errors = [123]
    fake = SimpleNamespace(
        OpenProcess=lambda *_: 77,
        WaitForSingleObject=lambda *_: 0xFFFFFFFF,
        CloseHandle=lambda *_: errors.__setitem__(0, 456),
    )
    namespace.update(
        _kernel32=lambda: fake,
        _last_error=lambda: errors[0],
        _windows_error=lambda call, error: OSError(f"{call}: error {error}"),
    )
    failures: list[str] = []
    try:
        tree._windows_pid_gone(os.getpid())
    except OSError as exc:
        if "WaitForSingleObject" not in str(exc) or "error 123" not in str(exc):
            _fail(failures, f"Windows wait error overwritten: {exc}")
    else:
        _fail(failures, "Windows wait error: failed wait did not raise")
    finally:
        namespace.update(saved)
    if not failures:
        print("PASS: Windows wait error survives CloseHandle")
    return failures


def _failed_job_cleanup_case(tree: ModuleType, stage: str, terminated: int) -> list[str]:
    """Plant an assign/resume failure and record the job cleanup call order."""
    namespace = vars(tree)
    saved = {name: namespace[name] for name in
             ("_kernel32", "_assign_to_job", "_resume_process_threads")}
    calls: list[tuple] = []
    original = RuntimeError(f"planted {stage} failure")

    def fail(*_: object) -> None:
        raise original

    def terminate(job: int, code: int) -> int:
        calls.append(("terminate", job, code))
        return terminated

    fake = SimpleNamespace(
        CreateJobObjectW=lambda *_: 77,
        TerminateJobObject=terminate,
        CloseHandle=lambda job: calls.append(("close", job)),
    )
    namespace.update(
        _kernel32=lambda: fake,
        _assign_to_job=fail if stage == "assign" else lambda *_: None,
        _resume_process_threads=fail if stage == "resume" else lambda *_: 1,
    )
    failures: list[str] = []
    try:
        tree._new_job_with(os.getpid())
    except RuntimeError as exc:
        if exc is not original:
            _fail(failures, f"failed job cleanup {stage}: original exception masked")
    else:
        _fail(failures, f"failed job cleanup {stage}: original exception lost")
    finally:
        namespace.update(saved)
    if calls != [("terminate", 77, 1), ("close", 77)]:
        _fail(failures, f"failed job cleanup {stage}/{terminated}: call order {calls!r}")
    return failures


def test_failed_job_cleanup(run_mod: ModuleType) -> list[str]:
    """Assign/resume failures terminate before closing, even on terminate failure."""
    failures: list[str] = []
    for stage in ("assign", "resume"):
        for terminated in (1, 0):
            failures.extend(_failed_job_cleanup_case(run_mod.process_tree, stage, terminated))
    if not failures:
        print("PASS: failed job cleanup terminates before close and preserves exception")
    return failures


def run_tests() -> int:
    """Run every case; return 0 when all pass."""
    run_mod = _load_module("run_meson_test", RUN_MESON_TEST_SCRIPT)
    failures: list[str] = []
    for case in (
        test_child_deadline,
        test_child_tree_deadline,
        test_nested_launcher_job,
        test_root_exits_after_deadline,
        test_failed_tree_step_is_named,
        test_failed_job_start_kills_child,
        test_stop_tree_after_reap,
        test_pid_gone_permission_denied,
        test_windows_wait_error_preserved,
        test_failed_job_cleanup,
    ):
        failures.extend(case(run_mod))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, then run every case."""
    argparse.ArgumentParser(
        description="Check the deadline and process-tree stop of scripts/run-meson-test.py."
    ).parse_args(argv)
    return run_tests()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
