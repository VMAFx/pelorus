#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Self-test for Meson testlog credential scrub and scanner (ADR-0246).

Tests:
1. Positive/negative name sanitization: listed tokens, keys, passwords and
   PAT names are scrubbed; normal environment variables like PATH, HOME,
   and TOKENIZERS_PARALLELISM are preserved.
2. Planted credential in testlog: check-testlog-credentials reports fake
   tokens in testlog.txt/testlog.json without printing them, passes clean
   trees, and refuses symlinks with exit status 2.
3. End-to-end with stub Meson: run-meson-test scrubs planted credential
   variables before exec/child launch so the child sees zero secret matches,
   whereas direct invocation sees the planted variable.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess  # nosec B404 - fixed test and stub argv, no shell, explicit deadline
import sys
import tempfile
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
RUN_MESON_TEST_SCRIPT = ROOT / "scripts" / "run-meson-test.py"
CHECK_CREDENTIALS_SCRIPT = ROOT / "scripts" / "check-testlog-credentials.py"

# Runtime-constructed synthetic credential shapes: never literal tokens in source.
FAKE_GITHUB_TOKEN = "ghp_" + "A" * 36
FAKE_FINE_GRAINED_TOKEN = "github_pat_" + "B" * 30
FAKE_GATEWAY_KEY = "sk-" + "C" * 24


def _load_module(name: str, path: Path) -> ModuleType:
    """Load a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positive_and_negative_names(run_mod: ModuleType) -> list[str]:
    """Verify listed and pattern-matched secret names are removed and safe names kept."""
    failures: list[str] = []
    to_remove = [
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "GH_TOKEN",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "OPENAI_API_KEY",
        "LITELLM_API_KEY",
        "MY_PAT",
    ]
    to_keep = [
        "PATH",
        "HOME",
        "TOKENIZERS_PARALLELISM",
        "PATHEXT",
    ]

    for name in to_remove:
        if not run_mod.is_credential_name(name):
            msg = f"name check: expected {name} to be identified as credential"
            print(f"FAIL: {msg}")
            failures.append(msg)

    for name in to_keep:
        if run_mod.is_credential_name(name):
            msg = f"name check: expected {name} to be preserved"
            print(f"FAIL: {msg}")
            failures.append(msg)

    env: dict[str, str] = {name: f"value_{name}" for name in to_remove + to_keep}
    run_mod.sanitize_process_environment(env)

    for name in to_remove:
        if name in env:
            msg = f"sanitize check: {name} was not removed from environment mapping"
            print(f"FAIL: {msg}")
            failures.append(msg)

    for name in to_keep:
        if name not in env:
            msg = f"sanitize check: {name} was unexpectedly removed from environment mapping"
            print(f"FAIL: {msg}")
            failures.append(msg)

    if not failures:
        print("PASS: positive and negative credential name sanitization")
    return failures


def _fail(failures: list[str], msg: str) -> None:
    """Print one failure line and record it."""
    print(f"FAIL: {msg}")
    failures.append(msg)


def _run_check(check_mod: ModuleType, tree: Path) -> tuple[int, str, str]:
    """Run the scanner on ``tree`` and return its exit code, stdout and stderr."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = check_mod.main([str(tree)])
    return code, stdout.getvalue(), stderr.getvalue()


def _check_symlink_log(check_mod: ModuleType, tmp: Path, log_file: Path) -> list[str]:
    """A testlog that is a symlink must exit 2 without following the link."""
    failures: list[str] = []
    target_file = tmp / "secret_target.txt"
    target_file.write_text(f"SECRET={FAKE_GITHUB_TOKEN}\n", encoding="utf-8")
    log_file.unlink()
    try:
        log_file.symlink_to(target_file)
    except OSError as exc:
        print(f"SKIP: symlink creation not supported: {exc}")
        return failures
    code, _, err_text = _run_check(check_mod, tmp)
    if (
        code != 2
        or "not a regular file" not in err_text
        or FAKE_GITHUB_TOKEN in err_text
    ):
        _fail(
            failures,
            "symlink log: expected exit 2 and regular file error; "
            f"got code={code}, err={err_text!r}",
        )
    return failures


def test_planted_log_and_symlink(check_mod: ModuleType) -> list[str]:
    """Verify testlog scanner catches token shapes, passes clean logs, and refuses symlinks."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        log_file = tmp / "build" / "meson-logs" / "testlog.txt"
        log_file.parent.mkdir(parents=True)
        log_file.write_text(f"DUMP_PAT={FAKE_GITHUB_TOKEN}\n", encoding="utf-8")
        code, out_text, _ = _run_check(check_mod, tmp)
        if (
            code != 1
            or "testlog.txt: 1 line(s)" not in out_text
            or FAKE_GITHUB_TOKEN in out_text
        ):
            _fail(
                failures,
                "planted log: expected exit 1 and count without secret value; "
                f"got code={code}, out={out_text!r}",
            )
        # Same tree without the credential: clean log passes with exit 0.
        log_file.write_text("PATH=/usr/bin\nHOME=/home/user\n", encoding="utf-8")
        code, _, _ = _run_check(check_mod, tmp)
        if code != 0:
            _fail(failures, f"clean log: expected exit 0, got {code}")
        failures.extend(_check_symlink_log(check_mod, tmp, log_file))
    if not failures:
        print("PASS: planted testlog and symlink checks")
    return failures


def _write_stub(stub_path: Path, dump_path: Path) -> None:
    """Write an owner-only stub Meson that dumps its environment as JSON."""
    stub_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"dump_target = {str(dump_path)!r}\n"
        "with open(dump_target, 'w', encoding='utf-8') as handle:\n"
        "    json.dump(dict(os.environ), handle)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    stub_path.chmod(0o500)


def _stub_sees(
    command: list[str], env: dict[str, str], dump_path: Path, key: str
) -> list[str]:
    """Run ``command`` and return failures plus a marker line telling whether ``key`` arrived."""
    dump_path.unlink(missing_ok=True)
    proc = subprocess.run(  # nosec B603 - fixed list argv, no shell, explicit deadline  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed stub or wrapper argv, no shell, explicit deadline
        command, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    if proc.returncode != 0:
        return [f"stub run {command[0]} exited with {proc.returncode}: {proc.stderr}"]
    dump = json.loads(dump_path.read_text(encoding="utf-8"))
    return ["SEEN"] if key in dump else []


def test_end_to_end_stub_meson() -> list[str]:
    """Verify run-meson-test scrubs secrets before child exec while direct run sees secret."""
    failures: list[str] = []
    planted_key = "MY_PAT"
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        dump_path = tmp / "env_dump.json"
        stub_path = tmp / "meson_stub.py"
        _write_stub(stub_path, dump_path)
        test_env = dict(os.environ)
        test_env[planted_key] = FAKE_GITHUB_TOKEN
        # 1. Direct stub run: the planted variable must arrive.
        direct = _stub_sees([str(stub_path), "test"], test_env, dump_path, planted_key)
        if direct != ["SEEN"]:
            _fail(
                failures,
                f"direct stub run: expected {planted_key} to arrive, got {direct}",
            )
        # 2. Run through run-meson-test.py: the planted variable must not arrive.
        wrapper_cmd = [
            sys.executable,
            "-I",
            str(RUN_MESON_TEST_SCRIPT),
            "--meson-executable",
            str(stub_path),
            "--",
            "-C",
            "dummy_build",
        ]
        wrapped = _stub_sees(wrapper_cmd, test_env, dump_path, planted_key)
        if wrapped:
            _fail(
                failures, f"wrapped run: expected {planted_key} removed, got {wrapped}"
            )
    if not failures:
        print("PASS: end-to-end stub Meson execution and secret scrubbing")
    return failures


def test_command_arguments(run_mod: ModuleType) -> list[str]:
    """Verify only a leading --meson-executable pair is taken; later copies pass through."""
    failures: list[str] = []
    cases = [
        (["-C", "b", "--suite=fast"], ["meson", "test", "-C", "b", "--suite=fast"]),
        (["--meson-executable", "/stub", "-C", "b"], ["/stub", "test", "-C", "b"]),
        (["--", "-C", "b"], ["meson", "test", "-C", "b"]),
        (
            ["-C", "b", "--test-args", "--meson-executable"],
            ["meson", "test", "-C", "b", "--test-args", "--meson-executable"],
        ),
    ]
    for argv, expected in cases:
        got = run_mod._command(argv)
        if got != expected:
            msg = f"command check: {argv} -> {got}, expected {expected}"
            print(f"FAIL: {msg}")
            failures.append(msg)
    if not failures:
        print("PASS: wrapper argument handling")
    return failures


def test_refuses_non_executable() -> list[str]:
    """A --meson-executable that is not an executable regular file must exit 127."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_str:
        plain = Path(tmp_str) / "not_meson.txt"
        plain.write_text("not a program\n", encoding="utf-8")
        plain.chmod(0o600)
        command = [
            sys.executable,
            "-I",
            str(RUN_MESON_TEST_SCRIPT),
            "--meson-executable",
        ]
        for target in (str(plain), str(Path(tmp_str) / "missing")):
            proc = subprocess.run(  # nosec B603 - fixed wrapper argv, no shell, explicit deadline  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed wrapper argv, no shell, explicit deadline
                [*command, target, "-C", "b"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 127:
                _fail(
                    failures,
                    f"non-executable {target}: expected exit 127, got {proc.returncode}",
                )
    if not failures:
        print("PASS: wrapper refuses a non-executable Meson path")
    return failures


def run_tests() -> int:
    """Execute all credential sanitization and scanning test suites."""
    run_mod = _load_module("run_meson_test", RUN_MESON_TEST_SCRIPT)
    check_mod = _load_module("check_testlog_credentials", CHECK_CREDENTIALS_SCRIPT)

    failures: list[str] = []
    failures.extend(test_positive_and_negative_names(run_mod))
    failures.extend(test_command_arguments(run_mod))
    failures.extend(test_planted_log_and_symlink(check_mod))
    failures.extend(test_end_to_end_stub_meson())
    failures.extend(test_refuses_non_executable())

    return 1 if failures else 0


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the test suite."""
    parser = argparse.ArgumentParser(
        description="Self-test for Meson testlog credential scrub and scanner (ADR-0246)."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run all self-test cases and return 0 on success or 1 on failure."""
    parse_arguments(argv)
    return run_tests()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
