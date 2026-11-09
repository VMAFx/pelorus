# Copyright 2026 Lusoris
#
# SPDX-License-Identifier: EUPL-1.2

"""Allow-list parser and AST data structures for CLI golden transcripts."""

from __future__ import annotations

import dataclasses
import pathlib
import re

MAX_TRANSCRIPT_LINES = 100000
MAX_DIRECTIVE_LINES = 100000
MAX_REPEAT_COUNT = 100000


class TranscriptParseError(Exception):
    """Raised when transcript syntax violates the allow-list grammar."""

    def __init__(self, filename: str, lineno: int, message: str) -> None:
        """Initialize parse error with filename, 1-based line number, and message."""
        super().__init__(f"{filename}:{lineno}: {message}")
        self.filename = filename
        self.lineno = lineno
        self.message = message


@dataclasses.dataclass(frozen=True)
class Directive:
    """File preparation directive executed before running tool command."""

    kind: str
    fixture: str
    dest: str
    line_or_n: int = 0
    count: int = 0


@dataclasses.dataclass
class TestCase:
    """Single test case parsed from a transcript."""

    name: str
    lineno: int
    directives: list[Directive]
    command: list[str]
    expected_stdout: list[str]
    expected_stderr: list[str]
    expected_exit: int


@dataclasses.dataclass
class _CaseState:
    """Lines read so far for the case being parsed."""

    name: str = ""
    directives: list[Directive] = dataclasses.field(default_factory=list)
    command: list[str] | None = None
    stdout: list[str] = dataclasses.field(default_factory=list)
    stderr: list[str] = dataclasses.field(default_factory=list)
    exit: int | None = None
    cmd_lno: int = 0


def validate_path_token(
    token: str, kind: str, fixtures_dir: pathlib.Path | None = None
) -> None:
    """Validate path token against path traversal and disallowed characters."""
    if not (1 <= len(token) <= 255) or token[0] in ".-" or ".." in token:
        raise ValueError(f"invalid {kind} '{token}'")
    for ch in token:
        if not (ch.isalnum() or ch in "._-"):
            raise ValueError(f"invalid {kind} '{token}'")
    if fixtures_dir is not None:
        path = (fixtures_dir / token).resolve()
        if not path.is_file() or path.parent != fixtures_dir:
            raise FileNotFoundError(f"fixture '{token}' not found in {fixtures_dir}")


def _parse_copy_directive(parts: list[str], fn: str, lno: int) -> Directive:
    """Parse '% copy <src> <dst>' directive."""
    if len(parts) != 3:
        raise TranscriptParseError(
            fn, lno, f"malformed copy directive: '% {' '.join(parts)}'"
        )
    return Directive("copy", parts[1], parts[2])


def _parse_head_directive(parts: list[str], fn: str, lno: int) -> Directive:
    """Parse '% head <src> <dst> <n>' directive."""
    if len(parts) != 4 or not re.fullmatch(r"[0-9]{1,9}", parts[3]):
        raise TranscriptParseError(
            fn, lno, f"malformed head directive: '% {' '.join(parts)}'"
        )
    return Directive("head", parts[1], parts[2], line_or_n=int(parts[3]))


def _parse_repeat_directive(parts: list[str], fn: str, lno: int) -> Directive:
    """Parse '% repeat <src> <dst> <line> <count>' directive."""
    if (
        len(parts) != 5
        or not re.fullmatch(r"[0-9]{1,9}", parts[3])
        or not re.fullmatch(r"[0-9]{1,9}", parts[4])
    ):
        raise TranscriptParseError(
            fn, lno, f"malformed repeat directive: '% {' '.join(parts)}'"
        )
    line_num = int(parts[3])
    count = int(parts[4])
    if line_num < 1 or count < 0 or count > MAX_REPEAT_COUNT:
        raise TranscriptParseError(
            fn, lno, f"repeat line/count out of bounds: {parts[3]} {parts[4]}"
        )
    return Directive("repeat", parts[1], parts[2], line_or_n=line_num, count=count)


def _parse_directive(parts: list[str], fn: str, lno: int) -> Directive:
    """Parse directive line parts into a typed Directive."""
    if len(parts) < 3:
        raise TranscriptParseError(
            fn, lno, f"malformed directive '% {' '.join(parts)}'"
        )
    try:
        validate_path_token(parts[1], "fixture name")
        validate_path_token(parts[2], "destination name")
    except ValueError as exc:
        raise TranscriptParseError(fn, lno, str(exc)) from exc

    op = parts[0]
    if op == "copy":
        return _parse_copy_directive(parts, fn, lno)
    if op == "head":
        return _parse_head_directive(parts, fn, lno)
    if op == "repeat":
        return _parse_repeat_directive(parts, fn, lno)
    raise TranscriptParseError(fn, lno, f"unknown directive '% {' '.join(parts)}'")


def _parse_command(line: str, tool_name: str, fn: str, lno: int) -> list[str]:
    """Parse command line ensuring tool name and token safety."""
    cmd_str = line[2:]
    tokens = cmd_str.split(" ")
    for tok in tokens:
        if not tok:
            raise TranscriptParseError(
                fn, lno, "command contains empty token from invalid spacing"
            )
        for ch in tok:
            if not (ch.isprintable() and ch not in "\"'\\"):
                raise TranscriptParseError(
                    fn, lno, f"command contains forbidden character: {ch!r}"
                )
    if not tokens or tokens[0] != tool_name:
        first = tokens[0] if tokens else ""
        raise TranscriptParseError(
            fn, lno, f"command tool '{first}' does not match expected '{tool_name}'"
        )
    return tokens[1:]


def _parse_exit(line: str, fn: str, lno: int) -> int:
    """Parse '[exit N]' line and return expected exit code."""
    if not line.startswith("[exit ") or not line.endswith("]"):
        raise TranscriptParseError(fn, lno, f"malformed exit line: {line!r}")
    code_str = line[6:-1]
    if not re.fullmatch(r"[0-9]{1,9}", code_str):
        raise TranscriptParseError(fn, lno, f"non-integer exit code: {code_str!r}")
    return int(code_str)


def _feed_output_line(line: str, lno: int, fn: str, cur: _CaseState) -> None:
    """Append output line to current case expected stdout or stderr."""
    if cur.command is None or cur.exit is not None:
        raise TranscriptParseError(fn, lno, "output line outside command block")
    if line == ">" or line.startswith("> "):
        cur.stdout.append(line[2:])
    elif line == "!" or line.startswith("! "):
        cur.stderr.append(line[2:])
    else:
        raise TranscriptParseError(fn, lno, f"unknown output line syntax: {line!r}")


def _feed_case_line(
    line: str, lno: int, tool_name: str, fn: str, cur: _CaseState
) -> None:
    """Process directive, command, output, or exit line for current case."""
    if line.startswith("% "):
        if cur.command is not None:
            raise TranscriptParseError(fn, lno, "directive after command")
        cur.directives.append(_parse_directive(line[2:].split(" "), fn, lno))
    elif line.startswith("$ "):
        if cur.command is not None:
            raise TranscriptParseError(fn, lno, "duplicate command in case")
        cur.command = _parse_command(line, tool_name, fn, lno)
        cur.cmd_lno = lno
    elif line.startswith("[exit "):
        if cur.command is None or cur.exit is not None:
            raise TranscriptParseError(fn, lno, "unexpected or duplicate [exit N]")
        cur.exit = _parse_exit(line, fn, lno)
    elif line.startswith((">", "!")):
        _feed_output_line(line, lno, fn, cur)
    else:
        raise TranscriptParseError(fn, lno, f"unknown line syntax: {line!r}")


def _feed_line(
    raw_line: str,
    lno: int,
    tool_name: str,
    fn: str,
    cur: _CaseState,
    cases: list[TestCase],
) -> _CaseState:
    """Feed one transcript line; return the state for the next line."""
    line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
    if line.startswith("#"):
        if not cur.name and line.strip() != "#":
            cur.name = line.lstrip("#").strip()
        return cur
    if not line:
        return _finish_case(fn, lno, cur, cases)
    _feed_case_line(line, lno, tool_name, fn, cur)
    return cur


def _finish_case(
    fn: str, lno: int, cur: _CaseState, cases: list[TestCase]
) -> _CaseState:
    """Append the case read so far, if any; return a fresh state."""
    if cur.command is None and not cur.directives:
        return _CaseState()
    if cur.command is None:
        raise TranscriptParseError(fn, lno, "case has directives but no command")
    if cur.exit is None:
        raise TranscriptParseError(fn, cur.cmd_lno, "case missing required [exit N]")
    cases.append(
        TestCase(
            name=cur.name or f"line {cur.cmd_lno}",
            lineno=cur.cmd_lno,
            directives=cur.directives,
            command=cur.command,
            expected_stdout=cur.stdout,
            expected_stderr=cur.stderr,
            expected_exit=cur.exit,
        )
    )
    return _CaseState()


def parse_transcript(text: str, filename: str, tool_name: str) -> list[TestCase]:
    """Parse transcript text with an allow-list parser into test cases."""
    if text.count("\n") >= MAX_TRANSCRIPT_LINES:
        raise TranscriptParseError(filename, 1, "transcript exceeds maximum line limit")
    cases: list[TestCase] = []
    cur = _CaseState()
    raw_lines = text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    for idx, line in enumerate(raw_lines):
        cur = _feed_line(line, idx + 1, tool_name, filename, cur, cases)
    _finish_case(filename, len(raw_lines) + 1, cur, cases)
    if not cases:
        raise TranscriptParseError(filename, 1, "transcript has no cases")
    return cases
