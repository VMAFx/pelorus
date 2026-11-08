#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Self-test for the tester-kit packaging pre-CI gate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess  # nosec B404 - run test commands and check-tester-kit-install subprocesses with explicit timeouts
import sys
import tempfile
import time
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = ROOT / "scripts" / "check-tester-kit-install.py"

_TINY_MAIN_C = """#include <string.h>

int main(int argc, char **argv) {
    if (argc > 1) {
        if (strcmp(argv[1], "fail") == 0) {
            return 1;
        }
        if (strcmp(argv[1], "skip") == 0) {
            return 77;
        }
    }
    return 0;
}
"""

_TINY_MESON_OPTIONS = """option('tests', type: 'boolean', value: true)
option('tools', type: 'boolean', value: false)
option('shaders', type: 'boolean', value: false)
"""


def _run_subproc(
    cmd: list[str],
    timeout_seconds: float = 60.0,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute command as subprocess with explicit timeout."""
    return subprocess.run(  # nosec B603 - test subprocess argv list, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - test subprocess argv list, no shell, explicit timeout
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
        cwd=str(cwd) if cwd is not None else None,
    )


def _entry(name: str, exe: Path, arg: str, workdir: Path) -> list[dict[str, object]]:
    """Build a mock Meson test metadata entry with a single argument."""
    return [
        {
            "name": name,
            "suite": ["fast"],
            "cmd": [str(exe), arg],
            "env": {},
            "workdir": str(workdir),
            "protocol": "exitcode",
            "timeout": 30,
            "extra_paths": [],
        }
    ]


def _setup_mock_files(tmp: Path) -> tuple[Path, Path, Path, Path]:
    """Create directory structure and mock fixture files for install checks."""
    build_dir = tmp / "mock_build"
    source_dir = tmp / "mock_source"
    outside_dir = tmp / "mock_outside"
    build_dir.mkdir()
    source_dir.mkdir()
    outside_dir.mkdir()

    dummy_exe = build_dir / "stub_test"
    dummy_exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    dummy_exe.chmod(0o500)

    fix_file = source_dir / "fix" / "a.tbl"
    fix_file.parent.mkdir(parents=True)
    fix_file.write_text("table data\n", encoding="utf-8")

    outside_file = outside_dir / "other.tbl"
    outside_file.write_text("outside data\n", encoding="utf-8")
    (source_dir / "sub").mkdir(parents=True)
    return build_dir, source_dir, fix_file, outside_file


def _test_mock_cases(tmp: Path) -> list[str]:
    """Test positive, negative (planted ..), and boundary (outside trees) mock cases."""
    failures: list[str] = []
    build_dir, source_dir, fix_file, outside_file = _setup_mock_files(tmp)
    dummy_exe = build_dir / "stub_test"

    cases = [
        ("positive-test", str(fix_file), True, ""),
        (
            "negative-test",
            f"{source_dir}/sub/../fix/a.tbl",
            False,
            "missing or unsafe test input",
        ),
        ("boundary-test", str(outside_file), False, "outside build/source trees"),
    ]

    for name, fixture_arg, want_ok, want_text in cases:
        json_path = tmp / f"{name}.json"
        payload = _entry(name, dummy_exe, fixture_arg, build_dir)
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        cmd = [
            sys.executable,
            "-I",
            str(CHECK_SCRIPT),
            str(build_dir),
            "--tests-json",
            str(json_path),
            "--source-dir",
            str(source_dir),
        ]
        proc = _run_subproc(cmd, timeout_seconds=30.0)
        if want_ok and proc.returncode != 0:
            failures.append(
                f"{name} failed with rc {proc.returncode}: {proc.stderr.strip()}"
            )
        elif not want_ok:
            if proc.returncode == 0:
                failures.append(f"{name} unexpectedly succeeded (expected non-zero rc)")
            if want_text not in proc.stderr:
                failures.append(
                    f"{name} did not name '{want_text}': {proc.stderr.strip()}"
                )
        if not failures or not failures[-1].startswith(name):
            print(f"case: {name} (mock)")
    return failures


def _check_run_kit_prerequisites(
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, str] | None:
    """Return ("skip", reason) on Windows, ("fail", reason) for a missing POSIX tool.

    The kit runner is /bin/sh, so Windows skips. On POSIX the fast suite runs
    under meson with ninja and a C compiler, so a missing tool means the run-kit
    cases were lost, and the self-test must fail instead of passing silently.
    """
    if platform == "win32":
        return "skip", "POSIX-only kit runner (run-fast-suite.sh is /bin/sh)"
    if not which("meson"):
        return "fail", "meson executable not found"
    if not (which("ninja") or which("ninja-build")):
        return "fail", "ninja executable not found"
    if not (which("cc") or which("gcc") or which("clang")):
        return "fail", "C compiler (cc/gcc/clang) not found"
    return None


def _test_prerequisite_handling() -> list[str]:
    """Missing tools fail on POSIX and skip on Windows; present tools run the cases."""
    failures: list[str] = []

    def none_found(_name: str) -> str | None:
        return None

    def all_found(name: str) -> str | None:
        return f"/usr/bin/{name}"

    cases = [
        ("prereq-posix-missing", "linux", none_found, ("fail", "meson executable not found")),
        ("prereq-windows", "win32", none_found, ("skip", "POSIX-only kit runner (run-fast-suite.sh is /bin/sh)")),
        ("prereq-posix-present", "linux", all_found, None),
    ]
    for name, platform, which, want in cases:
        got = _check_run_kit_prerequisites(platform, which)
        if got != want:
            failures.append(f"{name}: expected {want}, got {got}")
        else:
            print(f"case: {name} -> {got}")
    return failures


def _load_check_module():
    """Load scripts/check-tester-kit-install.py by path (its name has hyphens)."""
    spec = importlib.util.spec_from_file_location("check_tester_kit_install", CHECK_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {CHECK_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses look up the defining module in sys.modules while the class body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _test_runner_timeout(tmp: Path) -> list[str]:
    """A runner that prints a banner and then hangs times out with rc 124 and the banner on stderr.

    On POSIX, TimeoutExpired.stdout holds bytes even with text=True; the check
    must decode it instead of raising TypeError.
    """
    runner = tmp / "hanging_runner.sh"
    runner.write_text("#!/bin/sh\necho '=== planted_timeout ==='\nexec sleep 30\n", encoding="utf-8")
    runner.chmod(0o500)
    check = _load_check_module()
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            rc = check._run_kit_runner(runner, tmp, timeout_seconds=2.0)
    except Exception as exc:  # noqa: BLE001 - any raise is the planted failure under test
        return [f"runner-timeout: raised {type(exc).__name__}: {exc}"]
    if rc != 124:
        return [f"runner-timeout: expected rc 124, got {rc}"]
    if "=== planted_timeout ===" not in captured.getvalue():
        return [f"runner-timeout: stderr lacks the banner: {captured.getvalue().strip()}"]
    print("case: runner-timeout (partial bytes output) -> rc 124, banner on stderr")
    return []


def _write_tiny_project(source_dir: Path, meson_build_content: str) -> None:
    """Write tiny Meson project files to source_dir."""
    source_dir.mkdir(parents=True, exist_ok=True)
    # Meson reads meson_options.txt at every version from the 0.57 floor on;
    # the newer options file name needs Meson 1.1 (.semgrep.yml pelorus-meson-options-file).
    (source_dir / "meson_options.txt").write_text(_TINY_MESON_OPTIONS, encoding="utf-8")
    (source_dir / "main.c").write_text(_TINY_MAIN_C, encoding="utf-8")
    (source_dir / "meson.build").write_text(meson_build_content, encoding="utf-8")


def _build_tiny_project(source_dir: Path, build_dir: Path) -> tuple[int, str]:
    """Configure and build a tiny Meson project."""
    proc_setup = _run_subproc(["meson", "setup", str(build_dir), str(source_dir)])
    if proc_setup.returncode != 0:
        return proc_setup.returncode, f"meson setup failed: {proc_setup.stderr.strip()}"
    ninja_bin = shutil.which("ninja") or shutil.which("ninja-build") or "ninja"
    proc_ninja = _run_subproc([ninja_bin, "-C", str(build_dir)])
    if proc_ninja.returncode != 0:
        return proc_ninja.returncode, f"ninja build failed: {proc_ninja.stderr.strip()}"
    return 0, ""


def _test_run_kit_positive(tmp: Path) -> list[str]:
    """Verify positive case: tiny project with only ok test passes with --run-kit rc 0."""
    source_dir = tmp / "tiny_pos_src"
    build_dir = tmp / "tiny_pos_build"
    content = (
        "project('tiny_pos', 'c', version: '0.1.0')\n"
        "exe = executable('tiny_exe', 'main.c')\n"
        "test('ok', exe, suite: 'fast')\n"
    )
    _write_tiny_project(source_dir, content)
    rc, err = _build_tiny_project(source_dir, build_dir)
    if rc != 0:
        return [f"run-kit-positive build failed: {err}"]

    cmd = [
        sys.executable,
        "-I",
        str(CHECK_SCRIPT),
        "--run-kit",
        "--source-dir",
        str(source_dir),
        str(build_dir),
    ]
    proc = _run_subproc(cmd, timeout_seconds=120.0)
    if proc.returncode != 0:
        return [
            f"run-kit-positive failed with rc {proc.returncode}: {proc.stderr.strip()}"
        ]
    print("case: run-kit-positive (tiny project ok)")
    return []


def _check_negative_variants(source_dir: Path, build_dir: Path) -> list[str]:
    """Assert check passes without --run-kit (rc 0) and fails with --run-kit naming planted."""
    cmd_no_run = [
        sys.executable,
        "-I",
        str(CHECK_SCRIPT),
        "--source-dir",
        str(source_dir),
        str(build_dir),
    ]
    proc_old = _run_subproc(cmd_no_run, timeout_seconds=60.0)
    if proc_old.returncode != 0:
        return [
            f"run-kit-negative: check without --run-kit failed with rc {proc_old.returncode}"
        ]

    cmd_run_kit = [
        sys.executable,
        "-I",
        str(CHECK_SCRIPT),
        "--run-kit",
        "--source-dir",
        str(source_dir),
        str(build_dir),
    ]
    proc_new = _run_subproc(cmd_run_kit, timeout_seconds=120.0)
    if proc_new.returncode == 0:
        return [
            "run-kit-negative: check with --run-kit unexpectedly succeeded with rc 0"
        ]
    if "planted" not in proc_new.stderr:
        return [
            f"run-kit-negative: stderr did not name 'planted': {proc_new.stderr.strip()}"
        ]

    print(
        f"case: run-kit-negative (planted should_fail) -> "
        f"without --run-kit rc {proc_old.returncode}, with --run-kit rc {proc_new.returncode}"
    )
    return []


_TINY_WERROR_C = """int planted_helper(void);

int planted_helper(void) {
    int planted_unused = 0;
    return 0;
}
"""


def _test_run_kit_build_error(tmp: Path) -> list[str]:
    """Verify a kit-only build failure reaches stderr with the compiler message.

    The plain build has no --werror and passes; the kit build (--werror) fails
    on an unused variable. Ninja prints compiler errors on stdout, so the
    variable name in stderr proves build-fast-suite.py forwards the stdout tail.
    """
    source_dir = tmp / "tiny_werror_src"
    build_dir = tmp / "tiny_werror_build"
    content = (
        "project('tiny_werror', 'c', version: '0.1.0')\n"
        "exe = executable('tiny_exe', 'main.c', 'werror.c')\n"
        "test('ok', exe, suite: 'fast')\n"
    )
    _write_tiny_project(source_dir, content)
    (source_dir / "werror.c").write_text(_TINY_WERROR_C, encoding="utf-8")
    rc, err = _build_tiny_project(source_dir, build_dir)
    if rc != 0:
        return [f"run-kit-build-error: plain build failed: {err}"]

    cmd = [
        sys.executable,
        "-I",
        str(CHECK_SCRIPT),
        "--run-kit",
        "--source-dir",
        str(source_dir),
        str(build_dir),
    ]
    proc = _run_subproc(cmd, timeout_seconds=120.0)
    if proc.returncode == 0:
        return ["run-kit-build-error: --werror kit build unexpectedly succeeded"]
    if "planted_unused" not in proc.stderr:
        return [
            "run-kit-build-error: stderr lacks the compiler message naming "
            f"'planted_unused': {proc.stderr.strip()}"
        ]
    print(
        "case: run-kit-build-error (kit-only --werror failure) -> "
        f"with --run-kit rc {proc.returncode}, compiler message forwarded"
    )
    return []


def _test_run_kit_negative(tmp: Path) -> list[str]:
    """Verify negative case: planted should_fail passes meson test, passes check without --run-kit, fails with --run-kit."""
    source_dir = tmp / "tiny_neg_src"
    build_dir = tmp / "tiny_neg_build"
    content = (
        "project('tiny_neg', 'c', version: '0.1.0')\n"
        "exe = executable('tiny_exe', 'main.c')\n"
        "test('ok', exe, suite: 'fast')\n"
        "test('planted', exe, args: ['fail'], should_fail: true, suite: 'fast')\n"
    )
    _write_tiny_project(source_dir, content)
    rc, err = _build_tiny_project(source_dir, build_dir)
    if rc != 0:
        return [f"run-kit-negative build failed: {err}"]

    proc_test = _run_subproc(["meson", "test", "-C", str(build_dir), "--suite=fast"])
    if proc_test.returncode != 0:
        return [
            f"run-kit-negative: meson test unexpectedly failed with rc {proc_test.returncode}"
        ]

    return _check_negative_variants(source_dir, build_dir)


def _test_run_kit_boundary(tmp: Path) -> list[str]:
    """Verify boundary case: exit 77 skip test fails under kit runner (set -eu) with non-zero rc."""
    source_dir = tmp / "tiny_skip_src"
    build_dir = tmp / "tiny_skip_build"
    content = (
        "project('tiny_skip', 'c', version: '0.1.0')\n"
        "exe = executable('tiny_exe', 'main.c')\n"
        "test('ok', exe, suite: 'fast')\n"
        "test('boundary_skip', exe, args: ['skip'], suite: 'fast')\n"
    )
    _write_tiny_project(source_dir, content)
    rc, err = _build_tiny_project(source_dir, build_dir)
    if rc != 0:
        return [f"run-kit-boundary build failed: {err}"]

    # Kit runner runs with set -eu; rc 77 triggers shell error exit (kit tests must not exit 77)
    cmd = [
        sys.executable,
        "-I",
        str(CHECK_SCRIPT),
        "--run-kit",
        "--source-dir",
        str(source_dir),
        str(build_dir),
    ]
    proc = _run_subproc(cmd, timeout_seconds=120.0)
    if proc.returncode == 0:
        return [
            "run-kit-boundary: check with --run-kit unexpectedly succeeded on exit 77 test"
        ]
    if "boundary_skip" not in proc.stderr:
        return [
            f"run-kit-boundary: stderr did not name 'boundary_skip': {proc.stderr.strip()}"
        ]

    print(
        f"case: run-kit-boundary (exit 77 skip) -> with --run-kit rc {proc.returncode}"
    )
    return []


def test_tester_kit_install_check() -> list[str]:
    """Test mock argument checks and tiny project run-kit positive, negative, boundary and build-error cases."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pelorus-test-kit-") as tmp_str:
        tmp = Path(tmp_str)
        failures.extend(_test_mock_cases(tmp))
        failures.extend(_test_prerequisite_handling())

        problem = _check_run_kit_prerequisites()
        if problem is not None:
            kind, reason = problem
            if kind == "skip":
                print(f"skip: run-kit cases ({reason})")
            else:
                failures.append(f"run-kit cases could not run: {reason}")
            return failures

        failures.extend(_test_runner_timeout(tmp))

        failures.extend(_test_run_kit_positive(tmp))
        failures.extend(_test_run_kit_negative(tmp))
        failures.extend(_test_run_kit_boundary(tmp))
        failures.extend(_test_run_kit_build_error(tmp))

    return failures


def main() -> int:
    """Run self-tests and exit non-zero on failure."""
    start_time = time.monotonic()
    failures = test_tester_kit_install_check()
    elapsed = time.monotonic() - start_time
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"PASS: check-tester-kit-install self-test ({elapsed:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
