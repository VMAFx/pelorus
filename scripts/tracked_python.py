# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""List the files git tracks and tell which of them are Python.

Shared by scripts/check-suppression-markers.py and
scripts/check-subprocess-markers.py so both read the same file set.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
from typing import Any

GIT_DEADLINE_S = 60
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def _git_executable() -> str:
    """Resolve git executable path on PATH or raise RuntimeError."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git not found on PATH")
    return git


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


def _run_git(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
    """Run git with timeout and error wrapping."""
    kw.setdefault("capture_output", True)
    kw.setdefault("check", True)
    kw.setdefault("timeout", GIT_DEADLINE_S)
    env = dict(kw.get("env") or os.environ)
    # C locale keeps git's stderr in English, so callers can name the cause.
    env["LC_ALL"] = "C"
    kw["env"] = env
    try:
        return subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
            argv,
            **kw,
        )
    except subprocess.CalledProcessError as exc:
        raw_err = exc.stderr
        if isinstance(raw_err, bytes):
            err_text = raw_err.decode("utf-8", "replace").strip()
        elif isinstance(raw_err, str):
            err_text = raw_err.strip()
        else:
            err_text = ""
        raise RuntimeError(
            f"git {argv[1:]} failed (exit {exc.returncode}): {err_text[:400]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git {argv[1:]} timed out after deadline of {exc.timeout}s"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"cannot run git: {exc}") from exc


def repo_discovery_env() -> dict[str, str]:
    """Return the environment without Git's repository-selecting variables.

    Git exports GIT_DIR and its siblings to hooks; inherited, they make
    `git -C <dir>` list the invoking repository instead of <dir>, so the
    pre-push self-test accepted a non-git directory. The list is Git's own
    (`git rev-parse --local-env-vars`).
    """
    git = _git_executable()
    proc = _run_git([git, "rev-parse", "--local-env-vars"])
    names = proc.stdout.decode("utf-8").split()
    if "GIT_DIR" not in names:
        raise RuntimeError("git rev-parse --local-env-vars did not list GIT_DIR")
    return {key: value for key, value in os.environ.items() if key not in names}


def require_files(files: list[Path], label: str) -> list[Path]:
    """Refuse an empty mandatory scan instead of reporting a vacuous success."""
    if not files:
        raise RuntimeError(f"{label}: no files to check")
    return files


def tracked_files(root: Path) -> list[Path]:
    """Every file git tracks under root."""
    git = _git_executable()
    proc = _run_git(
        [git, "-C", str(root), "ls-files", "-z"],
        env=repo_discovery_env(),
    )
    names = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    return require_files([root / name for name in names if name], "git ls-files")
