#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Run ``meson test`` after removing credential-bearing keys (ADR-0246)."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess  # nosec B404 - fixed meson argv, no shell
import sys
from pathlib import Path
from collections.abc import MutableMapping, Sequence

# python3 -I drops the script directory from sys.path: load the sibling by path.
_PROCESS_TREE_SPEC = importlib.util.spec_from_file_location(
    "pelorus_process_tree", Path(__file__).with_name("process_tree.py")
)
if _PROCESS_TREE_SPEC is None or _PROCESS_TREE_SPEC.loader is None:
    raise ImportError("scripts/process_tree.py not found next to run-meson-test.py")
process_tree = importlib.util.module_from_spec(_PROCESS_TREE_SPEC)
_PROCESS_TREE_SPEC.loader.exec_module(process_tree)

CREDENTIAL_ENV_VARS = (
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GITHUB_PAT",
    "GH_PAT",
    "GITHUB_AUTH_TOKEN",
    "GITHUB_API_TOKEN",
    "HOMEBREW_GITHUB_API_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
)

# ADR-0246: a fixed list misses credentials exported under other names (a messaging
# token, a gateway key, a personal *_PAT, a *_KEY, a passphrase or a database connection URL), and Meson then writes them to testlog.txt
# and testlog.json. Names matching this pattern are removed as well. Only names are
# matched; values are never read.
CREDENTIAL_NAME_PATTERN = re.compile(
    r"TOKEN(?!IZER)|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|ACCESS_KEY|PRIVATE_KEY"
    r"|PASSPHRASE|_KEY$|(?:^|_)PAT$|(?:^|_)AUTH(?:_|$)"
    r"|(?:^|_)(?:DATABASE|DB|POSTGRES(?:QL)?|PG|MYSQL|MARIADB|MONGO(?:DB)?|REDIS|AMQP|RABBITMQ"
    r"|BROKER|CELERY_BROKER|SQLALCHEMY_DATABASE)_(?:URL|URI)$|(?:^|_)DSN$|CONNECTION_STRING",
    re.IGNORECASE,
)

# HISS-02: whole-run deadline for the Windows child. `meson test` stops each
# hung test at its own timeout; this stops a hung Meson process itself.
MESON_RUN_TIMEOUT_S = 1800
# Exit status for a run stopped at its deadline, as timeout(1) reports it.
STOPPED_EXIT_STATUS = 124
# Wait for the stopped process tree's root to be reaped (HISS-02).
STOP_WAIT_TIMEOUT_S = 30


def is_credential_name(name: str) -> bool:
    """Return whether ``name`` is a listed or pattern-matched credential key."""
    return (
        name in CREDENTIAL_ENV_VARS or CREDENTIAL_NAME_PATTERN.search(name) is not None
    )


def sanitize_process_environment(environment: MutableMapping[str, str]) -> None:
    """Delete credential keys without inspecting or retaining their values."""
    for name in [key for key in environment if is_credential_name(key)]:
        del environment[name]


def parse_arguments(argv: Sequence[str] | None = None) -> tuple[str, list[str]]:
    """Parse --meson-executable and return it with remaining test arguments."""
    parser = argparse.ArgumentParser(
        description="Run meson test after removing credential-bearing keys (ADR-0246).",
        add_help=False,
        allow_abbrev=False,
    )
    parser.add_argument("--meson-executable", default="meson")
    arguments = list(argv or [])
    # Only a leading --meson-executable pair belongs to this wrapper; the same text
    # later in the list is a Meson or test argument and passes through untouched.
    head = arguments[:2] if arguments[:1] == ["--meson-executable"] else []
    parsed = parser.parse_args(head)
    remainder = arguments[len(head) :]
    if not parsed.meson_executable:
        raise ValueError("missing Meson executable")
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    return parsed.meson_executable, remainder


def _command(argv: Sequence[str] | None = None) -> list[str]:
    """Build the Meson command without invoking a shell."""
    meson_executable, remainder = parse_arguments(argv)
    return [meson_executable, "test", *remainder]


def _resolve_executable(name: str) -> str | None:
    """PATH lookup of ``name``, kept only when it resolves to an executable regular file."""
    found = shutil.which(name)
    resolved = Path(found).resolve() if found else None
    if resolved is None or not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return str(resolved)


def _run_and_wait(command: list[str], deadline_s: float = MESON_RUN_TIMEOUT_S) -> int:
    """Run Meson as a child on Windows and return its exit status (ADR-0246).

    Windows has no exec: the CRT's ``_execvp`` starts the new program and
    ends this process at once with status 0, so every caller saw success while
    ``meson test`` was still running. The child inherits this process's
    already-sanitized environment block; no mapping is passed or copied. A
    child still running at ``deadline_s`` is stopped together with every test
    process it started (``process_tree.stop_tree``), and the run fails with
    status 124. An interrupt stops the tree the same way before it propagates.
    """
    executable = _resolve_executable(command[0])
    if executable is None:
        print("run-meson-test.py: Meson executable not found", file=sys.stderr)
        return 127
    # POSIX: lead a process group so the stop reaches every descendant. Windows
    # needs no flag (taskkill /T), and CREATE_NEW_PROCESS_GROUP would turn off
    # Ctrl+C in the child.
    proc = subprocess.Popen([executable, *command[1:]], start_new_session=(os.name == "posix"))  # nosec B603 - fixed meson argv, no shell  # noqa: S603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - resolved meson path plus caller test arguments, no shell, deadline below
    try:
        return proc.wait(timeout=deadline_s)
    except subprocess.TimeoutExpired:
        print(
            f"run-meson-test.py: meson test still running after {deadline_s:g} s; stopped",
            file=sys.stderr,
        )
        return STOPPED_EXIT_STATUS
    finally:
        # Deadline, interrupt or any other exit while Meson still runs.
        if proc.poll() is None:
            process_tree.stop_tree(proc)
            try:
                proc.wait(timeout=STOP_WAIT_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # Root ignores SIGKILL/TerminateProcess: report the stop, do not hang.
                pass


def main(argv: Sequence[str] | None = None) -> int:
    """Sanitize this process, then replace it with Meson's test runner."""
    try:
        command = _command(argv)
    except (ValueError, SystemExit) as exc:
        if isinstance(exc, SystemExit):
            return exc.code if isinstance(exc.code, int) else 2
        print("run-meson-test.py: --meson-executable requires a value", file=sys.stderr)
        return 2

    sanitize_process_environment(os.environ)
    if os.name == "nt":
        return _run_and_wait(command)
    executable = _resolve_executable(command[0])
    if executable is None:
        print("run-meson-test.py: Meson executable not found", file=sys.stderr)
        return 127
    try:
        # ADR-0246 requires same-process exec so no environment mapping is copied.
        os.execv(executable, [executable, *command[1:]])  # nosec B606 - validated absolute meson path, no shell  # noqa: S606  # nosemgrep: python.lang.security.audit.dangerous-os-exec-audit.dangerous-os-exec-audit, python_exec_rule-start-process-with-no-shell - validated absolute meson path plus caller test arguments, no shell
    except OSError as exc:
        print(f"run-meson-test.py: cannot run Meson: {exc.strerror}", file=sys.stderr)
        return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
