# Copyright 2026 Lusoris
#
# SPDX-License-Identifier: EUPL-1.2

"""Driver for CLI demonstrator golden transcript tests."""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import os
import pathlib
import re
import subprocess  # nosec B404 - driver executes tool demonstrator under test
import sys
import tempfile
from typing import Sequence

MAX_TRANSCRIPT_LINES = 100000
MAX_DIRECTIVE_LINES = 100000
MAX_REPEAT_COUNT = 100000
MAX_STREAM_BYTES = 1024 * 1024
RUN_TIMEOUT_SECS = 30


class TranscriptParseError(Exception):
    """Raised when transcript syntax violates the allow-list grammar."""

    def __init__(self, filename: str, lineno: int, message: str) -> None:
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


def minimal_env() -> dict[str, str]:
    """Return a minimal environment containing only platform-required keys."""
    allowed = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG"}
    return {k: v for k, v in os.environ.items() if k in allowed}


def _validate_path_token(
    token: str, kind: str, fixtures_dir: pathlib.Path | None = None
) -> None:
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
    if len(parts) != 3:
        raise TranscriptParseError(
            fn, lno, f"malformed copy directive: '% {' '.join(parts)}'"
        )
    return Directive("copy", parts[1], parts[2])


def _parse_head_directive(parts: list[str], fn: str, lno: int) -> Directive:
    if len(parts) != 4 or not re.fullmatch(r"[0-9]{1,9}", parts[3]):
        raise TranscriptParseError(
            fn, lno, f"malformed head directive: '% {' '.join(parts)}'"
        )
    return Directive("head", parts[1], parts[2], line_or_n=int(parts[3]))


def _parse_repeat_directive(parts: list[str], fn: str, lno: int) -> Directive:
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
    if len(parts) < 3:
        raise TranscriptParseError(
            fn, lno, f"malformed directive '% {' '.join(parts)}'"
        )
    try:
        _validate_path_token(parts[1], "fixture name")
        _validate_path_token(parts[2], "destination name")
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
    if not line.startswith("[exit ") or not line.endswith("]"):
        raise TranscriptParseError(fn, lno, f"malformed exit line: {line!r}")
    code_str = line[6:-1]
    if not re.fullmatch(r"[0-9]{1,9}", code_str):
        raise TranscriptParseError(fn, lno, f"non-integer exit code: {code_str!r}")
    return int(code_str)


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


def _feed_output_line(line: str, lno: int, fn: str, cur: _CaseState) -> None:
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
    cases: list[TestCase] = []
    cur = _CaseState()
    raw_lines = text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()
    for idx, line in enumerate(raw_lines):
        if idx >= MAX_TRANSCRIPT_LINES:
            raise TranscriptParseError(
                filename, idx + 1, "transcript exceeds maximum line limit"
            )
        cur = _feed_line(line, idx + 1, tool_name, filename, cur, cases)
    _finish_case(filename, len(raw_lines) + 1, cur, cases)
    return cases


def _split_fixture_lines(data: bytes) -> list[bytes]:
    if not data:
        return []
    parts = data.split(b"\n")
    if data.endswith(b"\n"):
        return [p + b"\n" for p in parts[:-1]]
    lines = [p + b"\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _execute_directive(
    d: Directive, fixtures_dir: pathlib.Path, tmp_dir: pathlib.Path
) -> None:
    _validate_path_token(d.fixture, "fixture name", fixtures_dir=fixtures_dir)
    src_path = (fixtures_dir / d.fixture).resolve()
    dst_path = tmp_dir / d.dest
    data = src_path.read_bytes()
    lines = _split_fixture_lines(data)
    if len(lines) > MAX_DIRECTIVE_LINES:
        raise ValueError(f"fixture {d.fixture} exceeds maximum line limit")

    if d.kind == "copy":
        dst_path.write_bytes(data)
    elif d.kind == "head":
        dst_path.write_bytes(b"".join(lines[: d.line_or_n]))
    elif d.kind == "repeat":
        if len(lines) < d.line_or_n or len(lines) < 1:
            raise IndexError(
                f"fixture {d.fixture} does not have required line {d.line_or_n}"
            )
        hdr = lines[0]
        row = lines[d.line_or_n - 1]
        out_content = [hdr]
        for _ in range(d.count):
            out_content.append(row)
        dst_path.write_bytes(b"".join(out_content))


def _run_directives(
    directives: list[Directive],
    fixtures_dir: pathlib.Path,
    tmp_dir: pathlib.Path,
    case: TestCase,
    quiet: bool,
) -> bool:
    for d in directives:
        try:
            _execute_directive(d, fixtures_dir, tmp_dir)
        except (FileNotFoundError, IndexError, ValueError, OSError) as exc:
            if not quiet:
                sys.stderr.write(f"FAIL: {case.name} (line {case.lineno}): {exc}\n")
            return False
    return True


def _format_stream_lines(prefix: str, lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        result.append(f"{prefix} {line}\n" if line else f"{prefix}\n")
    return result


def _stream_to_block_lines(prefix: str, text: str, missing_marker: str) -> list[str]:
    if not text:
        return []
    if text.endswith("\n"):
        return _format_stream_lines(prefix, text[:-1].split("\n"))
    lines = _format_stream_lines(prefix, text.split("\n"))
    lines.append(f"{missing_marker}\n")
    return lines


def _print_case_diff(
    case: TestCase, actual_out: str, actual_err: str, returncode: int
) -> None:
    expected_block: list[str] = []
    expected_block.extend(_format_stream_lines(">", case.expected_stdout))
    expected_block.extend(_format_stream_lines("!", case.expected_stderr))
    expected_block.append(f"[exit {case.expected_exit}]\n")

    actual_block: list[str] = []
    actual_block.extend(
        _stream_to_block_lines(">", actual_out, "[no newline at end of stdout]")
    )
    actual_block.extend(
        _stream_to_block_lines("!", actual_err, "[no newline at end of stderr]")
    )
    actual_block.append(f"[exit {returncode}]\n")

    sys.stderr.write(f"FAIL: {case.name} (line {case.lineno})\n")
    diff = difflib.unified_diff(
        expected_block, actual_block, fromfile="expected", tofile="actual"
    )
    for line in diff:
        sys.stderr.write(line)


def _check_stream_caps(
    proc: subprocess.CompletedProcess[bytes], quiet: bool, case_name: str
) -> bool:
    if len(proc.stdout) > MAX_STREAM_BYTES or len(proc.stderr) > MAX_STREAM_BYTES:
        if not quiet:
            sys.stderr.write(f"FAIL: {case_name}: output stream exceeded 1 MiB limit\n")
        return False
    return True


def _decode_stream(
    data: bytes, stream_name: str, case: TestCase, quiet: bool
) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if not quiet:
            sys.stderr.write(
                f"FAIL: {case.name} (line {case.lineno}): {stream_name} is not UTF-8 at byte {exc.start}\n"
            )
        return None
    if os.name == "nt":
        return text.replace("\r\n", "\n")
    return text


def _spawn_tool(
    cmd: list[str],
    exe_path: str,
    tmp_dir: pathlib.Path,
    case: TestCase,
    quiet: bool,
) -> subprocess.CompletedProcess[bytes] | None:
    env = minimal_env()
    try:
        return subprocess.run(  # nosec B603 - invoke tool demonstrator directly  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed tool argv, no shell, bounded timeout
            cmd,
            executable=exe_path,
            cwd=tmp_dir,
            capture_output=True,
            timeout=RUN_TIMEOUT_SECS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        if not quiet:
            sys.stderr.write(
                f"FAIL: {case.name} (line {case.lineno}): timed out after {RUN_TIMEOUT_SECS} s\n"
            )
        return None
    except OSError as exc:
        if not quiet:
            err_msg = exc.strerror if exc.strerror else str(exc)
            sys.stderr.write(f"FAIL: {case.name}: cannot run {exe_path}: {err_msg}\n")
        return None


def _verify_output(
    case: TestCase, actual_out: str, actual_err: str, returncode: int, quiet: bool
) -> bool:
    expected_out = "".join(f"{line}\n" for line in case.expected_stdout)
    expected_err = "".join(f"{line}\n" for line in case.expected_stderr)
    if (
        actual_out == expected_out
        and actual_err == expected_err
        and returncode == case.expected_exit
    ):
        return True
    if not quiet:
        _print_case_diff(case, actual_out, actual_err, returncode)
    return False


def _run_single_case(
    case: TestCase,
    exe_path: str,
    tool_name: str,
    fixtures_dir: pathlib.Path,
    tmp_dir: pathlib.Path,
    quiet: bool = False,
) -> bool:
    if not _run_directives(case.directives, fixtures_dir, tmp_dir, case, quiet):
        return False
    proc = _spawn_tool([tool_name, *case.command], exe_path, tmp_dir, case, quiet)
    if proc is None or not _check_stream_caps(proc, quiet, case.name):
        return False
    actual_out = _decode_stream(proc.stdout, "stdout", case, quiet)
    if actual_out is None:
        return False
    actual_err = _decode_stream(proc.stderr, "stderr", case, quiet)
    if actual_err is None:
        return False
    return _verify_output(case, actual_out, actual_err, proc.returncode, quiet)


def run_cases(
    cases: list[TestCase],
    exe_path: str,
    tool_name: str,
    fixtures_dir: pathlib.Path,
    quiet: bool = False,
) -> int:
    """Run test cases sequentially, each within a fresh temporary directory."""
    failed = 0
    for case in cases:
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = pathlib.Path(tmp_str)
            ok = _run_single_case(
                case, exe_path, tool_name, fixtures_dir, tmp_dir, quiet=quiet
            )
            if not ok:
                failed += 1
    return failed


def run_transcript_file(
    path: pathlib.Path,
    exe_path: str,
    tool_name: str,
    fixtures_dir: pathlib.Path,
) -> int:
    """Read and run a transcript file. Returns 0 on success, 1 on test failure, 2 on parse error."""
    try:
        # read_bytes, not read_text: universal newlines would turn a lone CR into
        # a line break; _feed_line strips one CR before each LF instead.
        content = path.read_bytes().decode("utf-8")
        cases = parse_transcript(content, str(path), tool_name)
    except TranscriptParseError as exc:
        sys.stderr.write(f"parse error: {exc}\n")
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error reading transcript {path}: {exc}\n")
        return 2

    failed = run_cases(cases, exe_path, tool_name, fixtures_dir)
    if failed == 0:
        print(f"ok: {len(cases)} cases in {path}")
        return 0
    sys.stderr.write(f"FAIL: {failed} of {len(cases)} cases in {path}\n")
    return 1


def _run_string_probe(text: str, exe: str, tool: str, fix: pathlib.Path) -> int:
    try:
        cases = parse_transcript(text, "<planted>", tool)
    except TranscriptParseError:
        return 2
    except (OSError, ValueError):
        return 2
    failed = run_cases(cases, exe, tool, fix, quiet=True)
    return 1 if failed > 0 else 0


def _get_runtime_plants(tool_name: str) -> list[tuple[str, str, int]]:
    pass_case = (
        f"$ {tool_name}\n"
        f"! {tool_name}: <x265.csv> is required\n"
        f"! usage: {tool_name} <x265.csv> [--requested-qp N]\n"
        "[exit 1]"
    )
    return [
        (
            "a (wrong byte)",
            f"$ {tool_name}\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! Usage: {tool_name} <x265.csv> [--requested-qp N]\n"
            "[exit 1]",
            1,
        ),
        (
            "b (wrong exit code)",
            f"$ {tool_name}\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! usage: {tool_name} <x265.csv> [--requested-qp N]\n"
            "[exit 2]",
            1,
        ),
        (
            "c (missing stderr line)",
            f"$ {tool_name}\n! {tool_name}: <x265.csv> is required\n[exit 1]",
            1,
        ),
        (
            "d (extra stdout line)",
            f"$ {tool_name}\n"
            "> extra\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! usage: {tool_name} <x265.csv> [--requested-qp N]\n"
            "[exit 1]",
            1,
        ),
        (
            "m (copy non-ascii filename directive)",
            f"% copy x265-4.3-qp32.csv café_文件.csv\n"
            f"$ {tool_name} café_文件.csv\n"
            "> PEL_SEC_QPREPORT (read back from a 144-byte Pelorus blob)\n"
            ">   source            : x265 --csv (HEVC SW, PEL_QPSRC_NONE=0)\n"
            ">   frames parsed     : 10\n"
            ">   avg_qp (bit-wtd)  : 32.0000\n"
            ">   total_bits        : 59928\n"
            ">   psnr_y/u/v (dB)   : 0.000 / 0.000 / 0.000\n"
            ">   qp_valid          : 0  (0 => frame-granular, no per-cell map)\n"
            ">   honored_fraction  : 0.0000  (no requested map supplied)\n"
            "[exit 0]",
            0,
        ),
        ("pass", pass_case, 0),
    ]


def _get_parse_plants(tool_name: str) -> list[tuple[str, str, int]]:
    return [
        (
            "e (missing exit line)",
            f"$ {tool_name}\n"
            f"! {tool_name}: <x265.csv> is required\n"
            f"! usage: {tool_name} <x265.csv> [--requested-qp N]",
            2,
        ),
        ("f (unknown line prefix)", "? unknown prefix\n[exit 0]", 2),
        (
            "g (path traversal in directive)",
            f"% copy ../x y\n$ {tool_name} y\n[exit 0]",
            2,
        ),
        ("h (quoted command argument)", f'$ {tool_name} "quoted"\n[exit 1]', 2),
        ("i (trailing space after argument)", f"$ {tool_name} \n[exit 1]", 2),
        (
            "j (invalid destination syntax)",
            f"% copy x265-4.3-qp32.csv a:b\n$ {tool_name} a:b\n[exit 0]",
            2,
        ),
        (
            "k (leading dot in fixture name)",
            f"% copy .hidden y\n$ {tool_name} y\n[exit 0]",
            2,
        ),
        ("l (non-ascii exit code)", f"$ {tool_name}\n[exit ²]", 2),
    ]


def run_self_test(exe_path: str, tool_name: str, fixtures_dir: pathlib.Path) -> int:
    """Run self-test verifying planted failure handling and allow-list reader."""
    runtime = _get_runtime_plants(tool_name)
    plants = [*runtime[:4], *_get_parse_plants(tool_name), *runtime[4:]]
    for label, text, expected_rc in plants:
        rc = _run_string_probe(text, exe_path, tool_name, fixtures_dir)
        if rc != expected_rc:
            sys.stderr.write(
                f"self-test failed for plant {label}: expected rc {expected_rc}, got {rc}\n"
            )
            return 1
        print(f"ok: plant {label} -> {rc}")
    print("ok: self-test passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for transcript driver CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, help="Path to tool executable")
    parser.add_argument("--tool", required=True, help="Tool name")
    parser.add_argument("--fixtures", required=True, help="Fixtures directory")
    parser.add_argument("--self-test", action="store_true", help="Run self-test suite")
    parser.add_argument(
        "transcript", nargs="?", default=None, help="Transcript file (.t)"
    )

    args = parser.parse_args(argv)
    exe_path = str(pathlib.Path(args.exe).resolve())
    fixtures_dir = pathlib.Path(args.fixtures).resolve()

    if args.self_test:
        return run_self_test(exe_path, args.tool, fixtures_dir)

    if not args.transcript:
        sys.stderr.write(
            "error: transcript file argument required when --self-test is not specified\n"
        )
        return 2

    transcript_path = pathlib.Path(args.transcript).resolve()
    return run_transcript_file(transcript_path, exe_path, args.tool, fixtures_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
