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
import threading
import time
from typing import IO, Sequence

_TOOLS_DIR = pathlib.Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from transcript_parse import (
    MAX_DIRECTIVE_LINES,
    Directive,
    TestCase,
    TranscriptParseError,
    validate_path_token,
    parse_transcript,
)
from transcript_plants import capture_plants, parse_plants, runtime_plants

MAX_STREAM_BYTES = 1024 * 1024
RUN_TIMEOUT_SECS = 30
PIPE_DRAIN_SECS = 5.0
MAX_INPUT_BYTES = 8 * 1024 * 1024


@dataclasses.dataclass(frozen=True)
class CaptureResult:
    """Result of bounded subprocess execution and stream capture."""

    stdout: bytes
    stderr: bytes
    returncode: int
    verdict: str
    text: str


class _StreamReader(threading.Thread):
    """Daemon thread that reads pipe up to stream cap and kills process on overflow."""

    def __init__(self, pipe: IO[bytes] | None, proc: subprocess.Popen[bytes]) -> None:
        """Initialize daemon thread with stream pipe and process handle."""
        super().__init__(daemon=True)
        self.pipe = pipe
        self.proc = proc
        self.data = b""
        self.overflow = False
        self.held = False

    def run(self) -> None:
        """Read up to MAX_STREAM_BYTES + 1 and kill process if exceeded."""
        if self.pipe is not None:
            self.data = self.pipe.read(MAX_STREAM_BYTES + 1)
            if len(self.data) > MAX_STREAM_BYTES:
                self.overflow = True
                self.proc.kill()


def minimal_env() -> dict[str, str]:
    """Return a minimal environment containing only platform-required keys."""
    allowed = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG"}
    return {k: v for k, v in os.environ.items() if k in allowed}


def _split_fixture_lines(data: bytes) -> list[bytes]:
    """Split fixture byte content into lines preserving newline delimiters."""
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
    """Execute a copy, head, or repeat directive in temporary directory."""
    validate_path_token(d.fixture, "fixture name", fixtures_dir=fixtures_dir)
    src_path = (fixtures_dir / d.fixture).resolve()
    dst_path = tmp_dir / d.dest
    with open(src_path, "rb") as f:
        data = f.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"fixture {d.fixture} exceeds {MAX_INPUT_BYTES} bytes")
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
    """Execute case directives sequentially and report any failure."""
    for d in directives:
        try:
            _execute_directive(d, fixtures_dir, tmp_dir)
        except (FileNotFoundError, IndexError, ValueError, OSError) as exc:
            if not quiet:
                sys.stderr.write(f"FAIL: {case.name} (line {case.lineno}): {exc}\n")
            return False
    return True


def _format_stream_lines(prefix: str, lines: list[str]) -> list[str]:
    """Format lines with the given transcript stream prefix."""
    result: list[str] = []
    for line in lines:
        result.append(f"{prefix} {line}\n" if line else f"{prefix}\n")
    return result


def _stream_to_block_lines(prefix: str, text: str, missing_marker: str) -> list[str]:
    """Convert stream text into prefixed block lines with missing newline markers."""
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
    """Print unified diff between expected and actual test case outputs."""
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


def _decode_stream(
    data: bytes, stream_name: str, case: TestCase, quiet: bool
) -> str | None:
    """Decode process output bytes to UTF-8 and normalize Windows CRLF."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if not quiet:
            sys.stderr.write(
                f"FAIL: {case.name} (line {case.lineno}): {stream_name} is not UTF-8 at byte {exc.start}\n"
            )
        return None
    if os.name == "nt":
        # The Windows C runtime writes text-mode stdout/stderr with CRLF; a lone CR is kept.
        return text.replace("\r\n", "\n")
    return text


def _resolve_capture_verdict(
    timed_out: bool,
    t_out: _StreamReader,
    t_err: _StreamReader,
    returncode: int,
    timeout: float,
) -> CaptureResult:
    """Determine final CaptureResult from execution flags and stream readers."""
    held = f"still open {int(PIPE_DRAIN_SECS)} s after the tool exited (a child process holds it)"
    checks = (
        (timed_out, "timeout", f"timed out after {int(timeout)} s"),
        (t_out.held, "held-stdout", f"stdout {held}"),
        (t_err.held, "held-stderr", f"stderr {held}"),
        (t_out.overflow, "overflow-stdout", "stdout exceeded 1 MiB limit"),
        (t_err.overflow, "overflow-stderr", "stderr exceeded 1 MiB limit"),
    )
    for flag, verdict, text in checks:
        if flag:
            return CaptureResult(t_out.data, t_err.data, returncode, verdict, text)
    return CaptureResult(t_out.data, t_err.data, returncode, "ok", "")


def capture(
    argv: list[str], exe: str, cwd: pathlib.Path, timeout: float
) -> CaptureResult:
    """Execute command with bounded runtime and stream capture."""
    env = minimal_env()
    try:
        proc = subprocess.Popen(  # nosec B603 - invoke tool demonstrator directly  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - fixed tool argv, no shell, bounded timeout
            argv,
            executable=exe,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        err_msg = exc.strerror if exc.strerror else str(exc)
        return CaptureResult(
            b"", b"", -1, "spawn-error", f"cannot run {exe}: {err_msg}"
        )

    t_out = _StreamReader(proc.stdout, proc)
    t_err = _StreamReader(proc.stderr, proc)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()

    deadline = time.monotonic() + PIPE_DRAIN_SECS
    for reader in (t_out, t_err):
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
        reader.held = reader.is_alive()
        # A child process that inherited the pipe keeps the reader blocked, and
        # closing a pipe under a blocked read waits until that child exits, so a
        # held pipe stays open; the daemon thread ends when the child closes it.
        if not reader.held and reader.pipe is not None:
            reader.pipe.close()

    return _resolve_capture_verdict(timed_out, t_out, t_err, proc.returncode, timeout)


def _verify_output(
    case: TestCase, actual_out: str, actual_err: str, returncode: int, quiet: bool
) -> bool:
    """Compare actual stdout, stderr, and exit code against expectations."""
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
    """Execute a single test case within a temporary directory."""
    if not _run_directives(case.directives, fixtures_dir, tmp_dir, case, quiet):
        return False
    res = capture([tool_name, *case.command], exe_path, tmp_dir, RUN_TIMEOUT_SECS)
    if res.verdict != "ok":
        if not quiet:
            if res.verdict == "spawn-error":
                sys.stderr.write(f"FAIL: {case.name}: {res.text}\n")
            else:
                sys.stderr.write(
                    f"FAIL: {case.name} (line {case.lineno}): {res.text}\n"
                )
        return False
    actual_out = _decode_stream(res.stdout, "stdout", case, quiet)
    if actual_out is None:
        return False
    actual_err = _decode_stream(res.stderr, "stderr", case, quiet)
    if actual_err is None:
        return False
    return _verify_output(case, actual_out, actual_err, res.returncode, quiet)


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
        # Binary mode, not text mode: universal newlines would turn a lone CR into
        # a line break; _feed_line strips one CR before each LF instead. One read
        # of cap + 1 bytes bounds memory before any decode or split.
        with open(path, "rb") as f:
            raw_bytes = f.read(MAX_INPUT_BYTES + 1)
        if len(raw_bytes) > MAX_INPUT_BYTES:
            sys.stderr.write(
                f"error reading transcript {path}: exceeds {MAX_INPUT_BYTES} bytes\n"
            )
            return 2
        content = raw_bytes.decode("utf-8")
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
    """Execute planted transcript string and return driver exit code."""
    try:
        cases = parse_transcript(text, "<planted>", tool)
    except TranscriptParseError:
        return 2
    except (OSError, ValueError):
        return 2
    failed = run_cases(cases, exe, tool, fix, quiet=True)
    return 1 if failed > 0 else 0


def _run_capture_plant(label: str, code: str, want: str, tmp: pathlib.Path) -> bool:
    """Run one Python stub through capture; True when its verdict is want within 10 s."""
    t0 = time.monotonic()
    res = capture([sys.executable, "-c", code], sys.executable, tmp, RUN_TIMEOUT_SECS)
    elapsed = time.monotonic() - t0
    if res.verdict == want and elapsed < 10.0:
        print(f"ok: plant {label} -> 0")
        return True
    sys.stderr.write(
        f"self-test failed for plant {label}: expected verdict {want} within 10 s, got {res.verdict} in {elapsed:.2f} s\n"
    )
    return False


def run_self_test(exe_path: str, tool_name: str, fixtures_dir: pathlib.Path) -> int:
    """Run self-test verifying planted failure handling and allow-list reader."""
    runtime = runtime_plants(tool_name)
    parse = parse_plants(tool_name)
    plants = [*runtime[:4], *parse, *runtime[4:]]
    for label, text, expected_rc in plants:
        rc = _run_string_probe(text, exe_path, tool_name, fixtures_dir)
        if rc != expected_rc:
            sys.stderr.write(
                f"self-test failed for plant {label}: expected rc {expected_rc}, got {rc}\n"
            )
            return 1
        print(f"ok: plant {label} -> {rc}")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = pathlib.Path(tmp_str)
        for label, code, want in capture_plants():
            if not _run_capture_plant(label, code, want, tmp):
                return 1

        pass_t = tmp / "pass.t"
        pass_text = [t for lbl, t, _ in runtime if lbl == "pass"][0]
        pass_t.write_text(pass_text, encoding="utf-8")
        rc_q = main(
            [
                "--exe",
                str(fixtures_dir),
                "--tool",
                tool_name,
                "--fixtures",
                str(fixtures_dir),
                str(pass_t),
            ]
        )
        if rc_q != 2:
            sys.stderr.write(
                f"self-test failed for plant q (exe is a directory): expected rc 2, got {rc_q}\n"
            )
            return 1
        print(f"ok: plant q (exe is a directory) -> {rc_q}")

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
    exe_file = pathlib.Path(args.exe).resolve()
    if not exe_file.is_file() or not os.access(exe_file, os.X_OK):
        sys.stderr.write(f"error: --exe {args.exe} is not an executable file\n")
        return 2
    exe_path = str(exe_file)

    fixtures_dir = pathlib.Path(args.fixtures).resolve()
    if not fixtures_dir.is_dir():
        sys.stderr.write(f"error: --fixtures {args.fixtures} is not a directory\n")
        return 2

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
