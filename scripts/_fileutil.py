# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Atomic write helper for scripts updating source files in place."""

from __future__ import annotations

import os
import pathlib
import tempfile


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write text to path atomically, preserving file mode and group."""
    st = os.stat(path)
    orig_mode, orig_gid = st.st_mode, st.st_gid
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = -1
            f.write(text)
        os.chmod(tmp, orig_mode)
        if hasattr(os, "chown"):
            os.chown(tmp, -1, orig_gid)
        os.replace(tmp, path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:  # tmp already gone
            pass
        raise
