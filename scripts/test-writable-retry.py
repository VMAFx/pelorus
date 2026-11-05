#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that the snapshot rmtree retry never changes a symlink target's mode.

.config/lefthook/scripts/common.py passes _writable_retry to shutil.rmtree as
the onexc/onerror handler. os.chmod follows symlinks, so an unguarded retry on a link
inside a read-only snapshot would add execute permission to its target outside
the snapshot. Separate read-only trees hold only a regular file
or only a symlink to a 0o600 file outside. Each removal must invoke the retry,
succeed and leave the outside file at 0o600.

    python3 -I scripts/test-writable-retry.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import sys
import tempfile
from unittest.mock import patch

COMMON = (
    pathlib.Path(__file__).resolve().parent.parent
    / ".config"
    / "lefthook"
    / "scripts"
    / "common.py"
)


def load_common():
    """Load the shared snapshot cleanup without importing the hook package."""
    sys.path.insert(0, str(COMMON.parent))
    spec = importlib.util.spec_from_file_location("lefthook_common", COMMON)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {COMMON}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def planted_case(base: pathlib.Path, common, symlink: bool) -> list[str]:
    """Remove a single-entry read-only tree; require the entry's retry to run."""
    outside = base / "outside"
    outside.write_text("x", encoding="utf-8")
    outside.chmod(0o600)
    snap = base / "snap"
    snap.mkdir()
    entry = snap / ("link" if symlink else "f")
    if symlink:
        entry.symlink_to(outside)
    else:
        entry.write_text("x", encoding="utf-8")
        entry.chmod(0o400)
    snap.chmod(0o500)
    retried: list[pathlib.Path] = []
    retry = common._writable_retry

    def record_retry(function, path, exc):
        retried.append(pathlib.Path(path))
        retry(function, path, exc)

    with patch.object(common, "_writable_retry", record_retry):
        common.rmtree_writable(snap)
    problems: list[str] = []
    if entry not in retried:
        problems.append(f"retry not called on {entry.name} path")
    if snap.exists():
        problems.append("read-only snapshot was not removed")
    mode = stat.S_IMODE(os.stat(outside).st_mode)
    if mode != 0o600:
        problems.append(f"symlink target mode changed to {oct(mode)}")
    return problems


def main() -> int:
    common = load_common()
    problems: list[str] = []
    for symlink in (False, True):
        with tempfile.TemporaryDirectory() as td:
            problems.extend(planted_case(pathlib.Path(td), common, symlink))
    for problem in problems:
        sys.stderr.write(f"test-writable-retry: {problem}\n")
    if problems:
        return 1
    print("test-writable-retry: symlink target untouched, snapshot removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
