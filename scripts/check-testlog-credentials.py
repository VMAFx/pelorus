#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Refuse a checkout whose Meson testlog files hold credential-shaped values (ADR-0246).

Meson's test runner writes its parent environment to ``meson-logs/testlog.txt``
and each test's environment to ``testlog.json``. The repository's test
wrapper for Meson removes credentials before Meson starts, but a direct Meson run skips it,
and the logs then sit in the gitignored build directory, where a hosted agent lane
or an archive can read them. This check walks the checkout for those logs and fails
when any line holds a token-shaped value. It prints paths and counts only, never a
value.

Usage: python3 scripts/check-testlog-credentials.py [ROOT ...]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

# Value shapes, not names: GitHub classic and fine-grained tokens, OpenAI-style
# ``sk-`` keys (the gateway key shape), AWS access key ids and URLs that carry a
# password in their userinfo.
CREDENTIAL_VALUE_PATTERN = re.compile(
    r"\bgh[pousr]_[A-Za-z0-9]{36}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{22,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    # A URL whose userinfo holds a password: scheme://user:password@host. The
    # password may hold an unencoded ? or # (SQLAlchemy accepts that); a port
    # followed by a query or fragment (host:8443?mail=a@b) is not a password.
    r"|\b[a-z][a-z0-9+.-]{1,30}://[^\s:/?#@]{1,256}:(?!\d{1,5}[?#])[^\s/@]{1,256}@"
)
LOG_NAME_PATTERN = re.compile(r"^testlog.*\.(?:txt|json)$")
SKIP_DIRECTORIES = frozenset({".git", "node_modules", ".venv", "__pycache__"})
MAX_DIRECTORIES = 200_000
MAX_LOG_BYTES = 512 * 1024 * 1024


def _raise_walk_error(error: OSError) -> None:
    """Make ``os.walk`` fail on an unreadable directory instead of skipping it."""
    raise error


def _checked_log(path: Path) -> Path:
    """Return ``path`` when it is a regular file; a symlink or other type is an error."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{path} is not a regular file")
    return path


def _test_logs(root: Path) -> Iterator[Path]:
    """Yield every ``meson-logs/testlog*`` file below ``root``, bounded."""
    if not root.is_dir():
        raise RuntimeError(f"{root} is not a directory")
    visited = 0
    for directory, subdirectories, files in os.walk(root, onerror=_raise_walk_error):
        visited += 1
        if visited > MAX_DIRECTORIES:
            raise RuntimeError(f"more than {MAX_DIRECTORIES} directories below {root}")
        subdirectories[:] = [
            name for name in subdirectories if name not in SKIP_DIRECTORIES
        ]
        yield from _linked_build_logs(Path(directory), subdirectories)
        if Path(directory).name == "meson-logs":
            yield from _logs_in(Path(directory), files)


def _logs_in(directory: Path, names: Iterable[str]) -> Iterator[Path]:
    """Yield the ``testlog*`` files among ``names`` in one ``meson-logs`` directory."""
    for name in sorted(names):
        if LOG_NAME_PATTERN.match(name):
            yield _checked_log(directory / name)


def _linked_build_logs(
    directory: Path, subdirectories: Sequence[str]
) -> Iterator[Path]:
    """Yield logs of a symlinked build directory, which ``os.walk`` does not enter.

    Only the link's own ``meson-logs`` is read, so a link to a large tree is not
    walked; a link straight to a ``meson-logs`` directory counts as well.
    """
    for name in subdirectories:
        link = directory / name
        if not link.is_symlink():
            continue
        logs = link if name == "meson-logs" else link / "meson-logs"
        if logs.is_dir():
            yield from _logs_in(logs, (entry.name for entry in logs.iterdir()))


def count_credential_lines(path: Path) -> int:
    """Return how many lines of ``path`` hold a credential-shaped value."""
    if path.stat().st_size > MAX_LOG_BYTES:
        raise RuntimeError(f"{path} exceeds {MAX_LOG_BYTES} bytes")
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if CREDENTIAL_VALUE_PATTERN.search(line))


def findings(roots: Sequence[Path]) -> list[tuple[Path, int]]:
    """Return ``(log path, matching line count)`` for every log with a match."""
    result: list[tuple[Path, int]] = []
    for root in roots:
        for log in _test_logs(root):
            count = count_credential_lines(log)
            if count:
                result.append((log, count))
    return result


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the testlog credential scanner."""
    parser = argparse.ArgumentParser(
        description="Refuse a checkout whose Meson testlog files hold credential-shaped values (ADR-0246)."
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        default=[],
        help="root directories to scan for meson-logs (defaults to current working directory)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Report logs that hold credential-shaped values; exit 1 when any exists."""
    args = parse_arguments(argv)
    roots = args.roots or [Path.cwd()]
    try:
        found = findings(roots)
    except (OSError, RuntimeError) as exc:
        print(f"check-testlog-credentials.py: {exc}", file=sys.stderr)
        return 2
    for log, count in found:
        print(f"{log.as_posix()}: {count} line(s) hold a credential-shaped value")
    if found:
        print(
            "Delete these meson-logs directories, and run tests through the "
            "repository's test wrapper for Meson, which removes credentials first (ADR-0246).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
