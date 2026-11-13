#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check the status contract and the deadlines of scripts/patch_harness.py.

build_and_run() backs every C patch-harness test of the fast suite. A harness
or compiler that stalls must end as a failure inside its deadline (HISS-02),
not hang the suite. Each case plants one outcome: pass, fail, compile error,
a harness that never exits, a compiler that never exits, and a compiler that
spawns a long-lived grandchild holding inherited output pipes.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import signal
import sys
import tempfile
import time
from typing import Sequence

# python3 -I drops the script directory from sys.path: load the sibling by path.
_SPEC = importlib.util.spec_from_file_location("patch_harness", pathlib.Path(__file__).with_name("patch_harness.py"))
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("scripts/patch_harness.py not found next to this test")
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)

DESCRIPTION = "Check the status contract and the deadlines of scripts/patch_harness.py."

OK_C = "int main(void) { return 0; }\n"
FAIL_C = "int main(void) { return 3; }\n"
BROKEN_C = "int main(void) { return }\n"
# volatile keeps the loop: an empty infinite loop without side effects is
# undefined behaviour and may be removed by the optimiser.
HANG_C = "int main(void) { volatile int spin = 1; while (spin) { } return 0; }\n"
# A stand-in compiler that never finishes inside the deadline.
STALL_CC = (sys.executable, "-I", "-c", "import time; time.sleep(30)")


def _write_stub(path: pathlib.Path) -> None:
    """Write stand-in compiler that spawns long-lived grandchild (HISS-02)."""
    path.write_text(
        "import os, subprocess, sys, time\n"
        "pid_file = os.environ.get('PELORUS_TEST_GRANDCHILD_PID_FILE')\n"
        "proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "if pid_file:\n"
        "    with open(pid_file + '.tmp', 'w') as f:\n"
        "        f.write(str(proc.pid))\n"
        "    os.replace(pid_file + '.tmp', pid_file)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )


def _read_pid(path: pathlib.Path) -> int | None:
    """Read PID from file if present and valid (HISS-02, HISS-07)."""
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
        return int(content) if content else None
    except (OSError, ValueError):
        return None


def test_stalled_compiler_child() -> bool:
    """A grandchild holding compiler pipes must be killed on deadline (HISS-02, PR #487)."""
    with tempfile.TemporaryDirectory(prefix="pelorus-stalled-child-") as tmp:
        tmp_dir = pathlib.Path(tmp)
        stub_py = tmp_dir / "stub_cc.py"
        pid_file = tmp_dir / "grandchild.pid"
        _write_stub(stub_py)
        old_env = os.environ.get("PELORUS_TEST_GRANDCHILD_PID_FILE")
        os.environ["PELORUS_TEST_GRANDCHILD_PID_FILE"] = str(pid_file)
        grandchild_pid: int | None = None
        try:
            start = time.monotonic()
            status, output = harness.build_and_run(
                OK_C, cc=[sys.executable, str(stub_py)], prefix="pelorus-harness-child-", build_timeout=3.0
            )
            wall_time = time.monotonic() - start
            grandchild_pid = _read_pid(pid_file)
            if status != 2 or "harness build exceeded" not in output or wall_time >= 15.0:
                print(f"FAIL stalled child: status {status}, wall {wall_time:.2f} s, out: {output.strip()!r}")
                return False
            # Every OS: the grandchild sits in the compiler's process group (POSIX) or
            # job object (Windows), so the deadline stop must have ended it.
            if grandchild_pid is None:
                print("FAIL stalled child: setup, no grandchild pid")
                return False
            if not harness.process_tree.wait_pid_gone(grandchild_pid):
                print(f"FAIL stalled child: grandchild {grandchild_pid} still alive after deadline")
                return False
            return True
        finally:
            if old_env is None:
                os.environ.pop("PELORUS_TEST_GRANDCHILD_PID_FILE", None)
            else:
                os.environ["PELORUS_TEST_GRANDCHILD_PID_FILE"] = old_env
            if os.name != "nt" and grandchild_pid is not None and grandchild_pid > 1:
                try:
                    os.kill(grandchild_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    # Grandchild process already terminated or inaccessible during cleanup.
                    pass


def test_driver_argv() -> bool:
    """GNU drivers keep their argv; cl.exe-style drivers get the MSVC forms."""
    src, exe = pathlib.Path("h.c"), pathlib.Path("h.exe")
    flags = (*harness.DEFAULT_CFLAGS, "-DX=1", "-Iinc")
    gnu = harness.driver_argv(("cc",), flags, src, exe, ("-lm",))
    msvc = harness.driver_argv(("C:\\VS\\bin\\cl.exe",), flags, src, exe, ("-lm",))
    clang_cl = harness.driver_argv(("clang-cl.exe",), flags, src, exe)
    gnu_stray = {"-nologo", "-WX"} & set(gnu)
    msvc_stray = {"-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function", "-lm", "-o"}
    checks = (
        ("gnu argv unchanged", gnu == ["cc", *flags, "h.c", "-o", "h.exe", "-lm"] and not gnu_stray),
        ("msvc argv mapped", {"-nologo", "/std:c11", "-W4", "-WX", "-wd4505", "-DX=1", "-Iinc",
                              "-Feh.exe", "-Foh.obj"} <= set(msvc)),
        ("msvc argv has no GNU flag", not msvc_stray & set(msvc)),
        ("clang-cl by path", harness.is_msvc_driver(("/usr/bin/clang-cl",))),
        ("clang-cl keeps -Wno-", "-Wno-unused-function" in clang_cl and "-wd4505" not in clang_cl),
        ("cl takes -wd4100", harness.msvc_flag("-Wno-unused-parameter") == ("-wd4100",)),
        ("clang is GNU", not harness.is_msvc_driver(("clang",))),
        ("one msvc warning level", [f for f in msvc if f in ("-W3", "-W4")] == ["-W4"]),
        ("--cc keeps option tokens",
         harness.make_parser("t").parse_args(["--cc", "cc", "-pipe"]).cc == ["cc", "-pipe"]),
    )
    ok = True
    for name, passed in checks:
        if not passed:
            print(f"FAIL {name}")
            ok = False
    try:
        harness.msvc_flag("-fsanitize=address")
        print("FAIL unmapped flag accepted")
        ok = False
    except ValueError:
        # Expected: a flag without an MSVC form is refused, never passed through.
        pass
    try:
        harness.driver_argv((), flags, src, exe)
        print("FAIL empty compiler command accepted")
        ok = False
    except ValueError:
        # Expected: "--cc" with no tokens is refused before anything runs.
        pass
    try:
        harness._MSVC_WARNINGS["-Wall"] = ()  # type: ignore[index]
        print("FAIL warning map is mutable")
        ok = False
    except TypeError:
        # Expected: the flag maps are read-only views.
        pass
    return ok


def _cleanup_stub_pids(paths: Sequence[pathlib.Path]) -> None:
    """Kill only recorded fixture pids if a cleanup regression leaves them alive."""
    for path in paths:
        pid = _read_pid(path)
        if pid is not None and pid > 1 and not harness.process_tree.pid_gone(pid):
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except ProcessLookupError:
                # Fixture exited between its liveness check and cleanup signal.
                pass


def test_interrupted_compiler_child() -> bool:
    """A communicate interrupt propagates only after compiler and grandchild stop."""
    tree = harness.process_tree
    real_popen = tree.TreePopen
    old_env = os.environ.get("PELORUS_TEST_GRANDCHILD_PID_FILE")
    original = KeyboardInterrupt("planted compiler interrupt")
    seen: list = []
    with tempfile.TemporaryDirectory(prefix="pelorus-interrupted-child-") as tmp:
        root = pathlib.Path(tmp)
        stub, child, grandchild = root / "stub_cc.py", root / "compiler.pid", root / "grandchild.pid"
        _write_stub(stub)

        class InterruptingPopen(real_popen):
            """Interrupt once the compiler has started its grandchild."""

            def communicate(self, *args, **kwargs):
                if not seen:
                    seen.append(self)
                    child.write_text(str(self.pid), encoding="utf-8")
                    for _ in range(200):
                        if _read_pid(grandchild) is not None:
                            raise original
                        time.sleep(0.05)
                    raise AssertionError("interrupted child: no grandchild pid")
                return super().communicate(*args, **kwargs)

        vars(tree)["TreePopen"] = InterruptingPopen
        os.environ["PELORUS_TEST_GRANDCHILD_PID_FILE"] = str(grandchild)
        try:
            try:
                harness.build_and_run(OK_C, cc=[sys.executable, str(stub)])
            except KeyboardInterrupt as exc:
                if exc is not original:
                    print("FAIL interrupted child: original KeyboardInterrupt masked")
                    return False
            else:
                print("FAIL interrupted child: KeyboardInterrupt did not propagate")
                return False
            for path in (child, grandchild):
                pid = _read_pid(path)
                if pid is None or not tree.wait_pid_gone(pid):
                    print(f"FAIL interrupted child: {path.name} pid {pid} still alive")
                    return False
            print("PASS: interrupt stops compiler and grandchild before propagating")
            return True
        finally:
            vars(tree)["TreePopen"] = real_popen
            _cleanup_stub_pids((grandchild, child))
            for proc in seen:
                proc.communicate(timeout=5)
            if old_env is None:
                os.environ.pop("PELORUS_TEST_GRANDCHILD_PID_FILE", None)
            else:
                os.environ["PELORUS_TEST_GRANDCHILD_PID_FILE"] = old_env


def test_failed_stop_still_reaps() -> bool:
    """A stop_tree fault is named; the child is still killed and reaped, the interrupt kept."""
    tree = harness.process_tree
    real_popen, real_stop = tree.TreePopen, tree.stop_tree
    original = KeyboardInterrupt("planted capture interrupt")
    seen: list = []

    class InterruptOnce(real_popen):
        """Interrupt the first capture, as Ctrl-C during communicate() would."""

        def communicate(self, *args, **kwargs):
            if not seen:
                seen.append(self)
                raise original
            return super().communicate(*args, **kwargs)

    def refused_stop(_proc: object) -> bool:
        raise PermissionError("planted tree stop refusal")

    stderr = io.StringIO()
    vars(tree).update(TreePopen=InterruptOnce, stop_tree=refused_stop)
    try:
        with contextlib.redirect_stderr(stderr):
            harness._run_with_deadline(list(STALL_CC), 30.0)
    except KeyboardInterrupt as exc:
        kept = exc is original
    except Exception as exc:  # the stop fault replaced the interrupt
        stderr.write(f"raised {exc!r}\n")
        kept = False
    else:
        kept = False
    finally:
        vars(tree).update(TreePopen=real_popen, stop_tree=real_stop)
    proc = seen[0] if seen else None
    reaped = proc is not None and proc.returncode is not None
    if proc is not None and not reaped:
        proc.kill()
        proc.communicate(timeout=5)
    if not kept or not reaped or "planted tree stop refusal" not in stderr.getvalue():
        print(f"FAIL failed stop: interrupt kept {kept}, reaped {reaped}, stderr {stderr.getvalue()!r}")
        return False
    print("PASS: a failed tree stop is named, the child reaped, the interrupt kept")
    return True


def _write_undecodable_stub(path: pathlib.Path) -> None:
    """Write a compiler that prints undecodable bytes, leaves a grandchild off its pipes, fails."""
    path.write_text(
        "import os, subprocess, sys\n"
        "proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "                        stderr=subprocess.DEVNULL)\n"
        "with open(os.environ['PELORUS_TEST_GRANDCHILD_PID_FILE'], 'w') as f:\n"
        "    f.write(str(proc.pid))\n"
        "os.write(1, b'out \\x81\\xff')\n"
        "os.write(2, b'err \\x81\\xff')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )


def test_undecodable_output() -> bool:
    """Undecodable compiler output is replaced, never raised after communicate() reaped the compiler."""
    old_env = os.environ.get("PELORUS_TEST_GRANDCHILD_PID_FILE")
    with tempfile.TemporaryDirectory(prefix="pelorus-undecodable-") as tmp:
        stub, grandchild = pathlib.Path(tmp) / "stub_cc.py", pathlib.Path(tmp) / "grandchild.pid"
        _write_undecodable_stub(stub)
        os.environ["PELORUS_TEST_GRANDCHILD_PID_FILE"] = str(grandchild)
        try:
            status, output = harness.build_and_run(OK_C, [sys.executable, "-I", str(stub)])
        except UnicodeDecodeError as exc:
            print(f"FAIL undecodable output: capture raised {exc!r}")
            return False
        finally:
            _cleanup_stub_pids((grandchild,))
            if old_env is None:
                os.environ.pop("PELORUS_TEST_GRANDCHILD_PID_FILE", None)
            else:
                os.environ["PELORUS_TEST_GRANDCHILD_PID_FILE"] = old_env
    if status != 2 or "out \ufffd" not in output or "err \ufffd" not in output:
        print(f"FAIL undecodable output: status {status}, output {output!r}")
        return False
    print("PASS: undecodable compiler output is replaced, the build reports its status")
    return True


def _remove_tree_calls(
    errors: Sequence[BaseException | None], is_windows: bool
) -> tuple[int, str, list[float]]:
    """Run remove_tree over a fake rmtree that raises ``errors`` in turn.

    Returns (rmtree calls, name of the exception remove_tree raised or "",
    delays passed to sleep). The last entry repeats once the sequence runs out.
    """
    calls = 0
    delays: list[float] = []

    def fake_rmtree(_: object) -> None:
        nonlocal calls
        err = errors[min(calls, len(errors) - 1)]
        calls += 1
        if err is not None:
            raise err

    try:
        harness.remove_tree("unused", rmtree=fake_rmtree, sleep=delays.append, is_windows=is_windows)
    except OSError as err:
        return calls, type(err).__name__, delays
    return calls, "", delays


def test_remove_tree() -> bool:
    """Check remove_tree: bounded retry on a locked file on Windows, no retry elsewhere."""
    denied = PermissionError("Access is denied")
    tries = harness.CLEANUP_TRIES
    cases = (
        ("unlocks after 3 denials", [denied, denied, denied, None], True, 4, "", 3),
        ("stays locked", [denied], True, tries, "PermissionError", tries - 1),
        ("off Windows", [denied], False, 1, "PermissionError", 0),
        ("other error", [FileNotFoundError("gone")], True, 1, "FileNotFoundError", 0),
    )
    ok = True
    for name, errors, is_windows, want_calls, want_raised, want_sleeps in cases:
        got = _remove_tree_calls(errors, is_windows)
        want = (want_calls, want_raised, [harness.CLEANUP_DELAY_S] * want_sleeps)
        if got != want:
            print(f"FAIL remove_tree {name}: calls, raised, delays {got!r}; want {want!r}")
            ok = False
    return ok


def test_build_and_run_cleanup() -> bool:
    """Check build_and_run removes its folder through remove_tree, also when the build fails."""
    removed: list[str] = []
    original = harness.remove_tree

    def spy(path: str, *args: object, **kwargs: object) -> None:
        removed.append(str(path))
        original(path, *args, **kwargs)

    failing_cc = (sys.executable, "-I", "-c", "import sys; sys.exit(1)")
    harness.remove_tree = spy
    try:
        status, _ = harness.build_and_run(OK_C, failing_cc, prefix="pelorus-cleanup-probe-")
    finally:
        harness.remove_tree = original
    folder = pathlib.Path(removed[0]) if len(removed) == 1 else None
    if status != 2 or folder is None or not folder.name.startswith("pelorus-cleanup-probe-") or folder.exists():
        print(f"FAIL build_and_run cleanup: status {status}, remove_tree calls {removed!r}")
        return False
    return True


def main() -> int:
    """Run patch harness tests across compiler cases; returns 0 on success, 1 on failure."""
    parser = harness.make_parser(DESCRIPTION)
    args = parser.parse_args()
    cases = (
        ("pass", OK_C, args.cc, {}, 0, ""),
        ("fail", FAIL_C, args.cc, {}, 1, ""),
        ("compile error", BROKEN_C, args.cc, {}, 2, "failed to compile"),
        ("run deadline", HANG_C, args.cc, {"run_timeout": 1.0}, 1, "run exceeded 1 s"),
        ("build deadline", OK_C, STALL_CC, {"build_timeout": 1.0}, 2, "build exceeded 1 s"),
    )
    failures = 0
    for name, source, cc, deadlines, want, needle in cases:
        status, output = harness.build_and_run(source, cc, prefix="pelorus-harness-self-", **deadlines)
        if status != want or needle not in output:
            print(f"FAIL {name}: status {status}, want {want}; output: {output.strip()!r}")
            failures += 1
    if not test_stalled_compiler_child():
        failures += 1
    if not test_driver_argv():
        failures += 1
    if not test_remove_tree():
        failures += 1
    if not test_build_and_run_cleanup():
        failures += 1
    if not test_interrupted_compiler_child():
        failures += 1
    if not test_failed_stop_still_reaps():
        failures += 1
    if not test_undecodable_output():
        failures += 1
    if failures:
        return 1
    print(f"patch-harness: {len(cases) + 7} cases ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
