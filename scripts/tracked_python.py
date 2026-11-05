# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""List the files git tracks and tell which of them are Python.

Shared by scripts/check-suppression-markers.py and
scripts/check-subprocess-markers.py so both read the same file set.
"""

from __future__ import annotations

from pathlib import Path
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline

GIT_DEADLINE_S = 60
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def is_python(path: Path) -> bool:
    """True for a .py or .pyi file or a file whose shebang names python."""
    if path.suffix in PYTHON_SUFFIXES:
        return True
    try:
        with path.open("rb") as handle:
            first = handle.readline(128)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def tracked_files(root: Path) -> list[Path]:
    """Every file git tracks under root."""
    proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=GIT_DEADLINE_S,
    )
    names = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [root / name for name in names if name]
