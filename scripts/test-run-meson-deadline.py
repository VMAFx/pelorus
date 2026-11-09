#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check the deadline and process-tree stop of scripts/run-meson-test.py (HISS-02).

On Windows the wrapper waits for ``meson test`` as a child. This test runs on
Linux and on both Windows CI legs, with live processes:

1. A quick child's exit status passes through; a hung child is stopped at its
   deadline with status 124.
2. A test process the hung child started is stopped with it (POSIX process
   group; Windows ``taskkill /T``).
3. A child that exits after the wait gave up, leaving its test process
   running, still gets its tree stopped. POSIX must stop the test process.
   Windows ``taskkill /T`` cannot walk from an exited root, so there the test
   process must be stopped or the wrapper must say on stderr that it may run.
4. A failed tree step is named on stderr, never a silent fallback; a planted
   taskkill that exits non-zero, or none on PATH, is reported with its cause.
5. Stopping a root that was already reaped signals nothing and says so.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess  # nosec B404 - fixed interpreter argv, no shell, explicit deadline
import sys
import tempfile
import time
from types import ModuleType

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
# The same stand-in, except that it exits at once and leaves its test running.
_EXITING_CHILD = _TREE_CHILD.rpartition("time.sleep(60)\n")[0]

# A planted taskkill: reports a missing process and exits 128, as taskkill does
# for a root that already exited.
_FAILING_TASKKILL = (
    "import sys\n"
    "sys.stderr.write('ERROR: planted taskkill found no process\\n')\n"
    "sys.exit(128)\n"
)


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


def _wait_exited_unreaped(proc: subprocess.Popen, tree: ModuleType) -> bool:
    """Wait until ``proc`` exits without reaping it, so its pid stays reserved."""
    waitid = getattr(os, "waitid", None)
    for _ in range(int(CHILD_WAIT_TIMEOUT_S / PID_FILE_POLL_S) + 1):
        if waitid is not None:
            if waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
                return True
        elif tree.pid_gone(proc.pid):
            return True
        time.sleep(PID_FILE_POLL_S)
    return False


def test_child_deadline(run_mod: ModuleType) -> list[str]:
    """A quick child's status passes through; a hung child is stopped with 124."""
    failures: list[str] = []
    quick = [sys.executable, "-I", "-c", "raise SystemExit(3)"]
    if run_mod._run_and_wait(quick, deadline_s=30) != 3:
        _fail(failures, "child deadline: a quick child's exit status 3 was not passed through")
    hung = [sys.executable, "-I", "-c", "import time; time.sleep(30)"]
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        status = run_mod._run_and_wait(hung, deadline_s=0.5)
    if status != run_mod.STOPPED_EXIT_STATUS or "still running after 0.5 s" not in stderr.getvalue():
        _fail(
            failures,
            f"child deadline: hung child gave status {status}, stderr {stderr.getvalue()!r}",
        )
    if not failures:
        print("PASS: wrapper stops a child at its deadline")
    return failures


def test_child_tree_deadline(run_mod: ModuleType) -> list[str]:
    """A test process the hung child started is stopped with it (HISS-02, HISS-08)."""
    failures: list[str] = []
    tree = run_mod.process_tree
    with tempfile.TemporaryDirectory(prefix="pelorus-meson-tree-") as tmp:
        pid_file = Path(tmp) / "test.pid"
        child = [sys.executable, "-I", "-c", _TREE_CHILD, str(pid_file)]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = run_mod._run_and_wait(child, deadline_s=TREE_DEADLINE_S)
        test_pid = _read_pid(pid_file)
        if status != run_mod.STOPPED_EXIT_STATUS or test_pid <= 1:
            _fail(failures, f"child tree: status {status}, test pid {test_pid}, stderr {stderr.getvalue()!r}")
        elif not tree.wait_pid_gone(test_pid):
            _kill_leftover(test_pid)
            _fail(failures, f"child tree: test process {test_pid} still running after the deadline")
    if not failures:
        print("PASS: wrapper stops the test processes of a hung child")
    return failures


def test_root_exits_after_deadline(run_mod: ModuleType) -> list[str]:
    """A root that exits after the wait gave up still gets its tree stopped."""
    failures: list[str] = []
    tree = run_mod.process_tree
    with tempfile.TemporaryDirectory(prefix="pelorus-meson-exited-") as tmp:
        pid_file = Path(tmp) / "test.pid"
        proc = subprocess.Popen(  # nosec B603 - fixed interpreter argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed interpreter argv, no shell, deadline below
            [sys.executable, "-I", "-c", _EXITING_CHILD, str(pid_file)],
            start_new_session=(os.name == "posix"),
        )
        test_pid = _read_pid(pid_file)
        exited = _wait_exited_unreaped(proc, tree)
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
        warned = "may still run" in stderr.getvalue()
        if not exited or test_pid <= 1 or status != run_mod.STOPPED_EXIT_STATUS:
            _fail(failures, f"exited root: exited {exited}, test pid {test_pid}, status {status}")
        elif not tree.wait_pid_gone(test_pid):
            _kill_leftover(test_pid)
            if os.name != "nt" or not warned:
                _fail(
                    failures,
                    f"exited root: test process {test_pid} still running and the stop "
                    f"said nothing about it: stderr {stderr.getvalue()!r}",
                )
        if test_pid > 1 and not tree.pid_gone(test_pid):
            _kill_leftover(test_pid)
    if not failures:
        print("PASS: wrapper stops the tree of a root that exited after the deadline")
    return failures


def test_failed_tree_step_is_named(run_mod: ModuleType) -> list[str]:
    """A failing or missing taskkill is reported with its cause (no silent fallback)."""
    failures: list[str] = []
    tree = run_mod.process_tree
    sleeper = [sys.executable, "-I", "-c", "import time; time.sleep(30)"]
    proc = subprocess.Popen(sleeper)  # nosec B603 - fixed interpreter argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed interpreter argv, no shell, deadline below
    try:
        with tempfile.TemporaryDirectory(prefix="pelorus-taskkill-") as tmp:
            stub = Path(tmp) / "taskkill.py"
            stub.write_text(_FAILING_TASKKILL, encoding="utf-8")
            planted = tree._stop_windows_tree(proc, taskkill=(sys.executable, "-I", str(stub)))
            missing = tree._stop_windows_tree(proc, taskkill=(str(Path(tmp) / "absent"),))
    finally:
        proc.kill()
        proc.wait(timeout=CHILD_WAIT_TIMEOUT_S)
    if planted is None or "exited 128" not in planted or "planted taskkill" not in planted:
        _fail(failures, f"failed taskkill: reported {planted!r}")
    if missing is None or "could not start" not in missing:
        _fail(failures, f"missing taskkill: reported {missing!r}")
    if not failures:
        print("PASS: a failed or missing taskkill is named with its cause")
    return failures


def test_stop_tree_after_reap(run_mod: ModuleType) -> list[str]:
    """Stopping a reaped root raises nothing, signals nothing and says so."""
    failures: list[str] = []
    done = subprocess.Popen(  # nosec B603 - fixed interpreter argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed interpreter argv, no shell, deadline below
        [sys.executable, "-I", "-c", "pass"], start_new_session=(os.name == "posix")
    )
    done.wait(timeout=CHILD_WAIT_TIMEOUT_S)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            stopped = run_mod.process_tree.stop_tree(done)
    except OSError as exc:
        _fail(failures, f"stop_tree on a reaped process raised {exc!r}")
    else:
        if stopped or "already reaped" not in stderr.getvalue():
            _fail(failures, f"reaped root: returned {stopped}, stderr {stderr.getvalue()!r}")
    if not failures:
        print("PASS: stopping a reaped root signals nothing and says so")
    return failures


def run_tests() -> int:
    """Run every case; return 0 when all pass."""
    run_mod = _load_module("run_meson_test", RUN_MESON_TEST_SCRIPT)
    failures: list[str] = []
    failures.extend(test_child_deadline(run_mod))
    failures.extend(test_child_tree_deadline(run_mod))
    failures.extend(test_root_exits_after_deadline(run_mod))
    failures.extend(test_failed_tree_step_is_named(run_mod))
    failures.extend(test_stop_tree_after_reap(run_mod))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, then run every case."""
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    return run_tests()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
