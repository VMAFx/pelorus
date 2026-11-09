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


def _validate_path_token(token: str, kind: str) -> None:
    if (
        not token
        or ".." in token
        or "/" in token
        or "\\" in token
        or pathlib.Path(token).is_absolute()
    ):
        raise ValueError(f"invalid {kind} '{token}'")


def _parse_copy_directive(parts: list[str], fn: str, lno: int) -> Directive:
    if len(parts) != 3:
        raise TranscriptParseError(
            fn, lno, f"malformed copy directive: '% {' '.join(parts)}'"
        )
    return Directive("copy", parts[1], parts[2])


def _parse_head_directive(parts: list[str], fn: str, lno: int) -> Directive:
    if len(parts) != 4 or not parts[3].isdigit():
        raise TranscriptParseError(
            fn, lno, f"malformed head directive: '% {' '.join(parts)}'"
        )
    return Directive("head", parts[1], parts[2], line_or_n=int(parts[3]))


def _parse_repeat_directive(parts: list[str], fn: str, lno: int) -> Directive:
    if len(parts) != 5 or not parts[3].isdigit() or not parts[4].isdigit():
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
    for bad in ('"', "'", "\\", "\t"):
        if bad in cmd_str:
            raise TranscriptParseError(
                fn, lno, f"command contains forbidden character: {bad!r}"
            )
    if "  " in cmd_str:
        raise TranscriptParseError(fn, lno, "command contains consecutive spaces")
    tokens = cmd_str.split(" ")
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
    if not code_str.isdigit():
        raise TranscriptParseError(fn, lno, f"non-integer exit code: {code_str!r}")
    return int(code_str)


def _feed_output_line(line: str, lno: int, fn: str, cur: dict[str, object]) -> None:
    if cur["command"] is None or cur["exit"] is not None:
        raise TranscriptParseError(fn, lno, "output line outside command block")
    if line == ">" or line.startswith("> "):
        cur["stdout"].append(line[2:] if line.startswith("> ") else "")
    elif line == "!" or line.startswith("! "):
        cur["stderr"].append(line[2:] if line.startswith("! ") else "")
    else:
        raise TranscriptParseError(fn, lno, f"unknown output line syntax: {line!r}")


def _feed_case_line(
    line: str, lno: int, tool_name: str, fn: str, cur: dict[str, object]
) -> None:
    if line.startswith("% "):
        if cur["command"] is not None:
            raise TranscriptParseError(fn, lno, "directive after command")
        cur["directives"].append(_parse_directive(line[2:].split(" "), fn, lno))
    elif line.startswith("$ "):
        if cur["command"] is not None:
            raise TranscriptParseError(fn, lno, "duplicate command in case")
        cur["command"] = _parse_command(line, tool_name, fn, lno)
        cur["cmd_lno"] = lno
    elif line.startswith("[exit "):
        if cur["command"] is None or cur["exit"] is not None:
            raise TranscriptParseError(fn, lno, "unexpected or duplicate [exit N]")
        cur["exit"] = _parse_exit(line, fn, lno)
    elif line.startswith(">") or line.startswith("!"):
        _feed_output_line(line, lno, fn, cur)
    else:
        raise TranscriptParseError(fn, lno, f"unknown line syntax: {line!r}")


def _feed_line(
    raw_line: str,
    lno: int,
    tool_name: str,
    fn: str,
    cur: dict[str, object],
    cases: list[TestCase],
) -> None:
    line = raw_line.rstrip("\r\n")
    if line.startswith("#"):
        if not cur["name"] and line.strip() != "#":
            cur["name"] = line.lstrip("#").strip()
        return
    if not line:
        _finish_case(fn, lno, cur, cases, on_blank=True)
        return
    _feed_case_line(line, lno, tool_name, fn, cur)


def _finish_case(
    fn: str,
    lno: int,
    cur: dict[str, object],
    cases: list[TestCase],
    on_blank: bool,
) -> None:
    if cur["command"] is None and not cur["directives"]:
        cur["name"] = ""
        return
    if cur["command"] is None:
        raise TranscriptParseError(fn, lno, "case has directives but no command")
    if cur["exit"] is None:
        cmd_lno = cur.get("cmd_lno", lno)
        raise TranscriptParseError(fn, cmd_lno, "case missing required [exit N]")
    name = str(cur["name"]) if cur["name"] else f"line {cur.get('cmd_lno', lno)}"
    cases.append(
        TestCase(
            name=name,
            lineno=int(cur.get("cmd_lno", lno)),
            directives=list(cur["directives"]),
            command=list(cur["command"]),
            expected_stdout=list(cur["stdout"]),
            expected_stderr=list(cur["stderr"]),
            expected_exit=int(cur["exit"]),
        )
    )
    cur["name"] = ""
    cur["directives"] = []
    cur["command"] = None
    cur["stdout"] = []
    cur["stderr"] = []
    cur["exit"] = None
    cur["cmd_lno"] = 0


def parse_transcript(text: str, filename: str, tool_name: str) -> list[TestCase]:
    """Parse transcript text with an allow-list parser into test cases."""
    cases: list[TestCase] = []
    cur: dict[str, object] = {
        "name": "",
        "directives": [],
        "command": None,
        "stdout": [],
        "stderr": [],
        "exit": None,
        "cmd_lno": 0,
    }
    lines = text.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if idx >= MAX_TRANSCRIPT_LINES:
            raise TranscriptParseError(
                filename, idx + 1, "transcript exceeds maximum line limit"
            )
        _feed_line(line, idx + 1, tool_name, filename, cur, cases)
    _finish_case(filename, len(lines) + 1, cur, cases, on_blank=False)
    return cases


def _execute_directive(
    d: Directive, fixtures_dir: pathlib.Path, tmp_dir: pathlib.Path
) -> None:
    src_path = fixtures_dir / d.fixture
    dst_path = tmp_dir / d.dest
    if not src_path.is_file():
        raise FileNotFoundError(f"fixture not found: {src_path}")
    lines = src_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) > MAX_DIRECTIVE_LINES:
        raise ValueError(f"fixture {d.fixture} exceeds maximum line limit")

    if d.kind == "copy":
        dst_path.write_text("".join(lines), encoding="utf-8")
    elif d.kind == "head":
        head_lines = lines[: d.line_or_n]
        dst_path.write_text("".join(head_lines), encoding="utf-8")
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
        dst_path.write_text("".join(out_content), encoding="utf-8")


def format_block(
    stdout_lines: list[str], stderr_lines: list[str], exit_code: int
) -> list[str]:
    """Format stdout, stderr, and exit code lines as a transcript block."""
    result: list[str] = []
    for line in stdout_lines:
        result.append(f"> {line}\n" if line else ">\n")
    for line in stderr_lines:
        result.append(f"! {line}\n" if line else "!\n")
    result.append(f"[exit {exit_code}]\n")
    return result


def _check_stream_caps(
    proc: subprocess.CompletedProcess[bytes], quiet: bool, case_name: str
) -> bool:
    if len(proc.stdout) > MAX_STREAM_BYTES or len(proc.stderr) > MAX_STREAM_BYTES:
        if not quiet:
            sys.stderr.write(f"FAIL: {case_name}: output stream exceeded 1 MiB limit\n")
        return False
    return True


def _decode_streams(
    proc: subprocess.CompletedProcess[bytes],
) -> tuple[list[str], list[str]]:
    out_text = proc.stdout.decode("utf-8")
    err_text = proc.stderr.decode("utf-8")
    if os.name == "nt":
        out_text = out_text.replace("\r\n", "\n")
        err_text = err_text.replace("\r\n", "\n")
    actual_out = out_text.splitlines() if out_text else []
    actual_err = err_text.splitlines() if err_text else []
    return actual_out, actual_err


def _run_single_case(
    case: TestCase,
    exe_path: str,
    tool_name: str,
    fixtures_dir: pathlib.Path,
    tmp_dir: pathlib.Path,
    quiet: bool = False,
) -> bool:
    for d in case.directives:
        _execute_directive(d, fixtures_dir, tmp_dir)

    env = minimal_env()
    proc = subprocess.run(  # nosec B603 - invoke tool demonstrator directly  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed tool argv, no shell, bounded timeout
        [tool_name, *case.command],
        executable=exe_path,
        cwd=tmp_dir,
        capture_output=True,
        timeout=RUN_TIMEOUT_SECS,
        check=False,
        env=env,
    )
    if not _check_stream_caps(proc, quiet, case.name):
        return False

    actual_out, actual_err = _decode_streams(proc)
    expected_block = format_block(
        case.expected_stdout, case.expected_stderr, case.expected_exit
    )
    actual_block = format_block(actual_out, actual_err, proc.returncode)

    if expected_block != actual_block:
        if not quiet:
            sys.stderr.write(f"FAIL: {case.name} (line {case.lineno})\n")
            diff = difflib.unified_diff(
                expected_block, actual_block, fromfile="expected", tofile="actual"
            )
            for line in diff:
                sys.stderr.write(line)
        return False
    return True


def run_cases(
    cases: list[TestCase],
    exe_path: str,
    tool_name: str,
    fixtures_dir: pathlib.Path,
    quiet: bool = False,
) -> int:
    """Run test cases sequentially within a temporary directory."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = pathlib.Path(tmp_str)
        passed = 0
        failed = 0
        for case in cases:
            ok = _run_single_case(
                case, exe_path, tool_name, fixtures_dir, tmp_dir, quiet=quiet
            )
            if ok:
                passed += 1
            else:
                failed += 1
        if failed > 0:
            return 1
    return 0


def run_transcript_file(
    path: pathlib.Path,
    exe_path: str,
    tool_name: str,
    fixtures_dir: pathlib.Path,
) -> int:
    """Read and run a transcript file. Returns 0 on success, 1 on test failure, 2 on parse error."""
    try:
        content = path.read_text(encoding="utf-8")
        cases = parse_transcript(content, str(path), tool_name)
    except TranscriptParseError as exc:
        sys.stderr.write(f"parse error: {exc}\n")
        return 2
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error reading transcript {path}: {exc}\n")
        return 2

    rc = run_cases(cases, exe_path, tool_name, fixtures_dir)
    if rc == 0:
        print(f"ok: {len(cases)} cases in {path}")
    return rc


def _run_string_probe(text: str, exe: str, tool: str, fix: pathlib.Path) -> int:
    try:
        cases = parse_transcript(text, "<planted>", tool)
    except TranscriptParseError:
        return 2
    except (OSError, ValueError):
        return 2
    return run_cases(cases, exe, tool, fix, quiet=True)


def run_self_test(exe_path: str, tool_name: str, fixtures_dir: pathlib.Path) -> int:
    """Run self-test verifying planted failure handling and allow-list reader."""
    plants: list[tuple[str, str, int]] = [
        ("a (wrong stdout line)", f"$ {tool_name}\n> WRONG STDOUT\n[exit 1]", 1),
        ("b (wrong stderr line)", f"$ {tool_name}\n! WRONG STDERR\n[exit 1]", 1),
        (
            "c (wrong exit code)",
            f"$ {tool_name}\n! {tool_name}: <x265.csv> is required\n! usage: {tool_name} <x265.csv> [--requested-qp N]\n[exit 0]",
            1,
        ),
        ("d (missing exit)", f"$ {tool_name}\n> some output", 2),
        ("e (unknown line prefix)", "? unknown prefix\n[exit 0]", 2),
        ("f (copy path traversal)", f"% copy ../x y\n$ {tool_name} y\n[exit 0]", 2),
        ("g (quoted arg)", f'$ {tool_name} "quoted"\n[exit 1]', 2),
        (
            "pass",
            f"$ {tool_name}\n! {tool_name}: <x265.csv> is required\n! usage: {tool_name} <x265.csv> [--requested-qp N]\n[exit 1]",
            0,
        ),
    ]

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
