#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that the snapshot rmtree retry never changes a symlink target's mode.

.config/lefthook/scripts/common.py passes _writable_retry to shutil.rmtree as
the onexc handler. os.chmod follows symlinks, so an unguarded retry on a link
inside a read-only snapshot would make the link target, which may live outside
the snapshot, owner-writable. The planted tree holds a read-only directory with
a read-only file and a symlink to a 0o600 file outside it; the removal must
succeed and leave the outside file at 0o600.

    python3 -I scripts/test-writable-retry.py
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import stat
import sys
import tempfile

COMMON = pathlib.Path(__file__).resolve().parent.parent / ".config" / "lefthook" / "scripts" / "common.py"


def load_retry():
    """Load _writable_retry from common.py without importing the hook package."""
    sys.path.insert(0, str(COMMON.parent))
    spec = importlib.util.spec_from_file_location("lefthook_common", COMMON)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {COMMON}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._writable_retry


def planted_case(base: pathlib.Path, retry) -> list[str]:
    """Remove a read-only tree holding a symlink; report every broken promise."""
    outside = base / "outside"
    outside.write_text("x", encoding="utf-8")
    outside.chmod(0o600)
    snap = base / "snap"
    snap.mkdir()
    (snap / "f").write_text("x", encoding="utf-8")
    (snap / "f").chmod(0o400)
    (snap / "link").symlink_to(outside)
    snap.chmod(0o500)
    shutil.rmtree(snap, onexc=retry)
    problems: list[str] = []
    if snap.exists():
        problems.append("read-only snapshot was not removed")
    mode = stat.S_IMODE(os.stat(outside).st_mode)
    if mode != 0o600:
        problems.append(f"symlink target mode changed to {oct(mode)}")
    return problems


def main() -> int:
    retry = load_retry()
    with tempfile.TemporaryDirectory() as td:
        problems = planted_case(pathlib.Path(td), retry)
    for problem in problems:
        sys.stderr.write(f"test-writable-retry: {problem}\n")
    if problems:
        return 1
    print("test-writable-retry: symlink target untouched, snapshot removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
