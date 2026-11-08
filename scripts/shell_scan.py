# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Shared shell, Makefile, and workflow file scanning helpers.

Used by scripts/check-curl-retry.py and scripts/check-showinfo-nostats.py
to select shell-like files, join backslash-continued logical lines, and
split logical lines into unquoted shell command segments.
"""

from __future__ import annotations

import bisect
import pathlib
import sys
from typing import NamedTuple

SHELL_SUFFIXES = frozenset({".sh", ".bash", ".mk"})
WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})


class LogicalLine(NamedTuple):
    """A backslash-joined logical line with physical line start offsets."""

    text: str
    starts: tuple[tuple[int, int], ...]

    @property
    def first_lineno(self) -> int:
        """Physical 1-based line number where this logical line begins."""
        return self.starts[0][1] if self.starts else 1


def is_shell_file(path: pathlib.Path) -> bool:
    """True for shell scripts, Makefiles, and GitHub Actions workflow files."""
    if path.suffix in SHELL_SUFFIXES or path.name == "Makefile":
        return True
    parts = path.parts
    return (
        len(parts) >= 3
        and parts[-3] == ".github"
        and parts[-2] == "workflows"
        and path.suffix in WORKFLOW_SUFFIXES
    )


def logical_lines(content: str) -> list[LogicalLine]:
    """Join backslash-continued physical lines, skipping standalone comments."""
    result: list[LogicalLine] = []
    pieces: list[str] = []
    starts: list[tuple[int, int]] = []
    offset = 0

    for idx, raw_line in enumerate(content.splitlines(), start=1):
        clean = raw_line.rstrip("\r\n")
        if not pieces and clean.lstrip().startswith("#"):
            continue
        continued = clean.endswith("\\")
        piece = clean[:-1].rstrip() if continued else clean
        starts.append((offset, idx))
        pieces.append(piece)
        offset += len(piece) + 1
        if not continued:
            result.append(LogicalLine(" ".join(pieces), tuple(starts)))
            pieces = []
            starts = []
            offset = 0

    if pieces:
        result.append(LogicalLine(" ".join(pieces), tuple(starts)))
    return result


def line_at(line: LogicalLine, offset: int) -> int:
    """Return the physical 1-based line number holding character offset."""
    if not line.starts:
        return 1
    idx = bisect.bisect_right(line.starts, (offset, sys.maxsize))
    return line.starts[max(0, idx - 1)][1]


def _step_quoted(text: str, i: int, quote: str) -> tuple[str | None, bool]:
    """Advance quote state for one character inside single or double quotes."""
    ch = text[i]
    if quote == '"' and ch == "\\" and i + 1 < len(text):
        return quote, True
    if ch == quote:
        return None, False
    return quote, False


def _is_separator(text: str, i: int, start: int) -> bool:
    """True when text[i] is an unquoted command separator (not a redirection)."""
    ch = text[i]
    if ch == ";":
        return True
    if ch == "|":
        return not (i > start and text[i - 1] == ">")
    if ch == "&":
        after_redir = i > start and text[i - 1] in (">", "<")
        before_redir = i + 1 < len(text) and text[i + 1] == ">"
        return not (after_redir or before_redir)
    return False


def _terminator_kind(text: str, i: int, start: int, depth: int) -> str | None:
    """Return the terminator kind at unquoted index i, or None."""
    ch = text[i]
    if _is_separator(text, i, start):
        return "sep"
    if ch == "`":
        return "backtick"
    if ch == ")" and depth == 0:
        return "paren"
    if ch == "#" and (i == start or text[i - 1].isspace()):
        return "comment"
    return None


def _step_unquoted(text: str, i: int, depth: int) -> tuple[str | None, int, bool]:
    """Advance quote, paren depth, and escape state for an unquoted character."""
    ch = text[i]
    if ch == "\\" and i + 1 < len(text):
        return None, depth, True
    if ch in ('"', "'"):
        return ch, depth, False
    if ch == "(":
        return None, depth + 1, False
    if ch == ")" and depth > 0:
        return None, depth - 1, False
    return None, depth, False


def command_end(text: str, start: int) -> tuple[int, str]:
    """Scan from start to the first unquoted terminator; return (index, kind)."""
    quote: str | None = None
    depth = 0
    skip = False

    for i in range(start, len(text)):
        if skip:
            skip = False
            continue
        if quote is not None:
            quote, skip = _step_quoted(text, i, quote)
            continue
        kind = _terminator_kind(text, i, start, depth)
        if kind is not None:
            return i, kind
        quote, depth, skip = _step_unquoted(text, i, depth)

    return len(text), "eof"


def split_commands(text: str) -> list[tuple[int, str]]:
    """Split a logical line into (start_offset, segment_text) command segments."""
    segments: list[tuple[int, str]] = []
    pos = 0
    for _ in range(len(text) + 1):
        if pos >= len(text):
            break
        end, kind = command_end(text, pos)
        seg = text[pos:end]
        if seg.strip():
            segments.append((pos, seg))
        if kind in ("comment", "eof"):
            break
        pos = end + 1
        if kind == "sep" and pos < len(text) and text[pos] == text[end]:
            pos += 1
    return segments
