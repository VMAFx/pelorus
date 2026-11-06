#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Run ``meson test`` after removing credential-bearing keys (ADR-0246)."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess  # nosec B404 - fixed meson argv, no shell
import sys
from pathlib import Path
from collections.abc import MutableMapping, Sequence

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
# token, a gateway key, a personal *_PAT, a *_KEY or a passphrase), and Meson then writes them to testlog.txt
# and testlog.json. Names matching this pattern are removed as well. Only names are
# matched; values are never read.
CREDENTIAL_NAME_PATTERN = re.compile(
    r"TOKEN(?!IZER)|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_?KEY|ACCESS_KEY|PRIVATE_KEY"
    r"|PASSPHRASE|_KEY$|(?:^|_)PAT$|(?:^|_)AUTH(?:_|$)",
    re.IGNORECASE,
)


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


def _run_and_wait(command: list[str]) -> int:
    """Run Meson as a child on Windows and return its exit status (ADR-0246).

    Windows has no exec: the CRT's ``_execvp`` starts the new program and
    ends this process at once with status 0, so every caller saw success while
    ``meson test`` was still running. The child inherits this process's
    already-sanitized environment block; no mapping is passed or copied.
    """
    executable = _resolve_executable(command[0])
    if executable is None:
        print("run-meson-test.py: Meson executable not found", file=sys.stderr)
        return 127
    # No timeout here: `meson test` enforces its own per-test timeouts.
    completed = subprocess.run([executable, *command[1:]], check=False)  # nosec B603 - fixed meson argv, no shell  # noqa: S603  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - resolved meson path plus caller test arguments, no shell
    return completed.returncode


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
        os.execv(executable, [executable, *command[1:]])  # nosec B606 - validated absolute meson path, no shell  # noqa: S606  # nosemgrep: python.lang.security.audit.dangerous-spawn-process-audit.dangerous-spawn-process-audit - validated absolute meson path plus caller test arguments, no shell
    except OSError as exc:
        print(f"run-meson-test.py: cannot run Meson: {exc.strerror}", file=sys.stderr)
        return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
