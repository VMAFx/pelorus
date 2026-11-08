#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that curl download invocations include robust retry, speed-time, and retry-max-time options.

Every curl download command in shell scripts, Makefiles, and GitHub Actions workflows
must specify exponential backoff via --retry (with count >= 5), --speed-time to detect
stalled transfers, and --retry-max-time strictly greater than --max-time so hangs do not
exhaust the retry window.

Usage:
    python3 -I scripts/check-curl-retry.py
    python3 -I scripts/check-curl-retry.py [path ...]
    python3 -I scripts/check-curl-retry.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shlex
import sys
import tempfile

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from shell_scan import command_end, is_shell_file, logical_lines  # noqa: E402
from tracked_python import tracked_files  # noqa: E402

DESCRIPTION = (
    "Check that curl downloads specify retry >= 5, speed-time, and "
    "retry-max-time exceeding max-time."
)
# Argument-taking short options from curl 8.23.0's `curl --help all`.
SHORT_OPTIONS_WITH_ARGS = frozenset(
    {
        "A", "C", "D", "E", "F", "H", "K", "P", "Q", "T", "U", "X", "Y",
        "b", "c", "d", "e", "h", "m", "o", "r", "t", "u", "w", "x", "y", "z",
    }
)


def is_curl_command_prefix(prefix: str) -> bool:
    """Determine whether word 'curl' appears in command position based on preceding text."""
    if not prefix:
        return True
    if prefix[-1] in ";|&(`!{@-+:":
        return True
    if prefix.endswith("$("):
        return True
    last_word = prefix.split()[-1]
    keywords = {"if", "elif", "then", "else", "do", "while", "until", "time", "exec"}
    if last_word in keywords:
        return True
    return bool("=" in last_word and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", last_word))


def scan_command_end(rest: str) -> str:
    """Scan forward from after 'curl' to find argument string before command terminator."""
    return rest[: command_end(rest, 0)[0]]


def extract_curl_args(line: str) -> list[str]:
    """Find each curl command invocation in a logical line and extract its arguments."""
    results: list[str] = []
    for m in re.finditer(r"\bcurl\b", line):
        prefix = line[: m.start()].rstrip()
        if is_curl_command_prefix(prefix):
            results.append(scan_command_end(line[m.end() :]))
    return results


# curl writes to stdout for "-o -" (curl 8.23.0 manual, --output); /dev/null discards.
NON_FILE_TARGETS = frozenset({"/dev/null", "-"})


# Long options whose value this check reads; each takes the next word unless
# written as --name=value (curl 8.23.0 manual).
VALUE_LONG_OPTIONS = frozenset(
    {"--output", "--retry", "--retry-max-time", "--max-time", "--speed-time"}
)
# Short spellings of the options this check reads (curl 8.23.0 `curl --help all`).
SHORT_ALIASES = {"O": "--remote-name", "m": "--max-time", "o": "--output", "y": "--speed-time"}
SPEED_TIME_REASON = (
    "--retry without --speed-time: a stalled transfer runs to --max-time"
    " and spends the retry window"
)
WINDOW_REASON = "--retry-max-time <= --max-time: a hang spends the whole retry window"


def _unquote(value: str | None) -> str | None:
    """Strip quotes left on a value when shlex could not split the line."""
    return None if value is None else value.strip("'\"")


def _long_pairs(tok: str, next_tok: str | None, pairs: list[tuple[str, str | None]]) -> int:
    """Append one long option as (name, value); return the words it consumed."""
    name, sep, value = tok.partition("=")
    if sep:
        pairs.append((name, _unquote(value)))
        return 1
    if tok in VALUE_LONG_OPTIONS:
        pairs.append((tok, _unquote(next_tok)))
        return 2
    pairs.append((tok, None))
    return 1


def _short_pairs(tok: str, next_tok: str | None, pairs: list[tuple[str, str | None]]) -> int:
    """Append each option of a short bundle; the first argument-taking one ends it."""
    for index, option in enumerate(tok[1:], start=1):
        name = SHORT_ALIASES.get(option, "-" + option)
        if option in SHORT_OPTIONS_WITH_ARGS:
            rest = tok[index + 1 :]
            pairs.append((name, _unquote(rest or next_tok)))
            return 1 if rest else 2
        pairs.append((name, None))
    return 1


def option_pairs(tokens: list[str]) -> list[tuple[str, str | None]]:
    """Read one operation's words as (option, value) pairs, short options under long names."""
    pairs: list[tuple[str, str | None]] = []
    i = 0
    # Every step consumes at least one word, so the loop ends after len(tokens) steps.
    while i < len(tokens):
        tok = tokens[i]
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok.startswith("--"):
            i += _long_pairs(tok, next_tok, pairs)
        elif tok.startswith("-") and len(tok) > 1:
            i += _short_pairs(tok, next_tok, pairs)
        else:
            i += 1
    return pairs


def is_download(name: str, value: str | None) -> bool:
    """A remote-name option, or an output that is a file rather than stdout or /dev/null."""
    if name in ("--remote-name", "--remote-name-all"):
        return True
    return name == "--output" and value not in NON_FILE_TARGETS


# curl 8.23.0 help: "-:, --next  Make next URL use separate options". curl
# also reads ":" inside a short-option bundle (-s:) as --next, unless an
# argument-taking option earlier in the bundle consumed it (-o: names a file).
def split_bundle(tok: str) -> tuple[list[str | None], bool]:
    """Split a short-option bundle at ':'; None marks a new operation.

    The flag is True when the bundle ends in an argument-taking option, so the
    next token is that option's argument.
    """
    if not tok.startswith("-") or tok.startswith("--") or len(tok) < 2:
        return [tok], False
    pieces: list[str | None] = []
    start = 1
    for index in range(1, len(tok)):
        option = tok[index]
        if option in SHORT_OPTIONS_WITH_ARGS:
            pieces.append("-" + tok[start:])
            return pieces, index == len(tok) - 1
        if option == ":":
            if index > start:
                pieces.append("-" + tok[start:index])
            pieces.append(None)
            start = index + 1
    if start < len(tok):
        pieces.append("-" + tok[start:])
    return pieces, False


def split_operations(tokens: list[str]) -> list[list[str]]:
    """Split curl arguments at --next, -: or a bundled ':'; each part has its own options."""
    operations: list[list[str]] = [[]]
    takes_arg = False
    for tok in tokens:
        if takes_arg:
            operations[-1].append(tok)
            takes_arg = False
            continue
        if tok == "--next":
            operations.append([])
            continue
        pieces, takes_arg = split_bundle(tok)
        for piece in pieces:
            if piece is None:
                operations.append([])
            else:
                operations[-1].append(piece)
    return operations


def check_curl_args(arg_str: str) -> list[str]:
    """Check each operation of a curl invocation for required retry options."""
    try:
        tokens = shlex.split(arg_str, posix=True)
    except ValueError:
        tokens = arg_str.split()
    violations: list[str] = []
    for operation in split_operations(tokens):
        violations.extend(v for v in check_operation(operation) if v not in violations)
    return violations


def _retry_count_violation(value: str | None) -> str | None:
    """--retry must be a literal integer of at least 5."""
    if value is None or not (value.isascii() and value.isdigit()):
        return "--retry count is not a literal integer"
    if int(value) < 5:
        return f"--retry count {int(value)} < 5"
    return None


def window_not_wider(window: str | None, max_time: str | None) -> bool:
    """True when the retry window is the same word as, or no larger than, the max-time."""
    if window is None or max_time is None:
        return False
    if window == max_time:
        return True
    try:
        return float(window) <= float(max_time)
    except ValueError:
        return False


def check_operation(tokens: list[str]) -> list[str]:
    """Check one curl operation (the options before or between --next) for retry rules."""
    pairs = option_pairs(tokens)
    if not any(is_download(name, value) for name, value in pairs):
        return []
    # curl uses the last occurrence of an option, so judge the last value.
    last = dict(pairs)
    if "--retry" not in last:
        return ["download without --retry"]
    narrow = window_not_wider(last.get("--retry-max-time"), last.get("--max-time"))
    candidates = [
        _retry_count_violation(last["--retry"]),
        None if "--retry-max-time" in last else "--retry without --retry-max-time",
        None if "--speed-time" in last else SPEED_TIME_REASON,
        WINDOW_REASON if narrow else None,
    ]
    return [reason for reason in candidates if reason]


def check_content(content: str, display_path: str) -> list[str]:
    """Check file content for conforming curl retry configurations."""
    violations: list[str] = []
    for line in logical_lines(content):
        for arg_str in extract_curl_args(line.text):
            for reason in check_curl_args(arg_str):
                violations.append(f"{display_path}:{line.first_lineno}: {reason}")
    return violations


def check_file(
    path: pathlib.Path, repo_root: pathlib.Path | None = None
) -> tuple[list[str], list[str]]:
    """Check a single file on disk."""
    try:
        content = path.read_text(encoding="utf-8", errors="surrogateescape")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}:1: cannot read file: {exc}"], []

    if repo_root and path.is_relative_to(repo_root):
        display_path = str(path.relative_to(repo_root))
    else:
        display_path = str(path)

    return [], check_content(content, display_path)


def is_scanned(path: pathlib.Path) -> bool:
    """Shell scripts, Makefiles and GitHub workflow files carry curl commands."""
    return is_shell_file(path)


def get_tracked_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Git-tracked shell, make and workflow files (shared tracked_files helper)."""
    return [f for f in tracked_files(repo_root) if is_scanned(f)]


def self_test_scanned(root: pathlib.Path) -> int:
    """Planted paths: both workflow suffixes are scanned, other YAML is not."""
    cases = [
        (".github/workflows/a.yml", True),
        (".github/workflows/b.yaml", True),
        ("docs/c.yaml", False),
        ("scripts/d.sh", True),
    ]
    failures = 0
    for rel, expected in cases:
        if is_scanned(root / rel) != expected:
            print(f"FAIL self-test path '{rel}': expected scanned={expected}", file=sys.stderr)
            failures += 1
    return failures


# Planted curl lines and the refusal reason each must produce (None: accepted).
SELF_TEST_CASES: tuple[tuple[str, str, str | None], ...] = (
    ("p1", "curl --retry 2 --retry-max-time 60 -o out https://example.com/file\n", "--retry count 2 < 5"),
    ("p2", "curl -o out https://example.com/file\n", "download without --retry"),
    ("p3", "curl --retry 6 -o out https://example.com/file\n", "--retry without --retry-max-time"),
    (
        "p4",
        "curl \\\n  --fail \\\n  --speed-time 30 \\\n  --retry 6 \\\n  --retry-max-time 60 \\\n  -o out \\\n  https://example.com/file\n",
        None,
    ),
    ("p5", "curl -fsS -o /dev/null https://example.com/probe\n", None),
    ("p6", "# curl --output x https://example.com/file\n", None),
    ("p7", 'curl --retry "$N" --retry-max-time 60 -o out https://example.com/file\n', "--retry count is not a literal integer"),
    ("p8", "curl --retry-max-time 60 -o out https://example.com/file --retry\n", "--retry count is not a literal integer"),
    ("p9", "curl -fsSLo out https://example.com/file\n", "download without --retry"),
    ("p10", "curl -OL https://example.com/file\n", "download without --retry"),
    ("p11", "curl -fsSLo /dev/null https://example.com/file\n", None),
    ("p12", "curl -sH 'X-o: 1' https://example.com/file\n", None),
    ("p13", "curl -fsSL -o out --speed-time 30 --retry 6 --retry-max-time 60 https://example.com/file\n", None),
    ("p14", "curl --retry 2 -o /dev/null https://example.com/file\n", None),
    ("p15", 'curl --retry 6 --retry "$N" --retry-max-time 60 -o out https://example.com/file\n', "--retry count is not a literal integer"),
    ("p16", "curl --retry 6 --retry 2 --retry-max-time 60 -o out https://example.com/file\n", "--retry count 2 < 5"),
    ("p17", 'curl --speed-time 30 --retry "$N" --retry 6 --retry-max-time 60 -o out https://example.com/file\n', None),
    ("p18", "curl -fsSLo - https://example.com/status | jq .\n", None),
    ("p19", "curl -o - https://example.com/status\n", None),
    ("p20", "curl --output=- https://example.com/status\n", None),
    ("p21", "curl --remote-name-all https://example.com/a https://example.com/b\n", "download without --retry"),
    ("p22", "curl --remote-name-all -o - https://example.com/a https://example.com/b\n", "download without --retry"),
    (
        "p23",
        'curl --speed-time 30 --retry 6 --retry-max-time 60 -o out https://example.com/f --next --retry "$N" -o o2 https://example.com/g\n',
        "--retry count is not a literal integer",
    ),
    (
        "p24",
        "curl --speed-time 30 --retry 6 --retry-max-time 60 -o out https://example.com/f -: -o o2 https://example.com/g\n",
        "download without --retry",
    ),
    (
        "p25",
        'curl --speed-time 30 --retry 6 --retry-max-time 60 -o out https://example.com/f --next --retry "$N" https://example.com/s\n',
        None,
    ),
    (
        "p26",
        "curl --speed-time 30 --retry 6 --retry-max-time 60 -o out https://example.com/f -s: -o o2 https://example.com/g\n",
        "download without --retry",
    ),
    ("p27", "curl -fo -: --speed-time 30 --retry 6 --retry-max-time 60 https://example.com/f\n", None),
    (
        "p28",
        'curl --speed-time 30 --max-time "$T" --retry 6 --retry-max-time "$T" -o out https://example.com/f\n',
        WINDOW_REASON,
    ),
    (
        "p29",
        "curl --speed-time 30 --max-time 120 --retry 6 --retry-max-time 60 -o out https://example.com/f\n",
        WINDOW_REASON,
    ),
    (
        "p30",
        "curl --speed-time 30 --max-time 120 --retry 6 --retry-max-time 120 -o out https://example.com/f\n",
        WINDOW_REASON,
    ),
    (
        "p31",
        "curl --max-time 120 --retry 6 --retry-max-time 240 -o out https://example.com/f\n",
        SPEED_TIME_REASON,
    ),
    (
        "p32",
        "curl --speed-time=30 --max-time 120 --retry 6 --retry-max-time 240 -o out https://example.com/f\n",
        None,
    ),
    (
        "p33",
        "curl -y 30 --max-time 120 --retry 6 --retry-max-time 240 -o out https://example.com/f\n",
        None,
    ),
    (
        "p34",
        "curl -sSy 30 --max-time 120 --retry 6 --retry-max-time 240 -o out https://example.com/f\n",
        None,
    ),
    (
        "p35",
        "curl -sSy30 --max-time 120 --retry 6 --retry-max-time 240 -o out https://example.com/f\n",
        None,
    ),
    (
        "p36",
        "curl --speed-time 30 --retry 6 --retry-max-time 60 -o out https://example.com/f\n",
        None,
    ),
)


def run_self_test() -> int:
    """Run self-test on planted cases in a temporary directory."""
    cases = SELF_TEST_CASES
    failures = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        for name, content, expected_reason in cases:
            test_file = tmp_path / f"{name}.sh"
            test_file.write_text(content, encoding="utf-8")
            diags, violations = check_file(test_file, tmp_path)
            if diags:
                print(f"FAIL self-test '{name}': unexpected diagnostics: {diags}", file=sys.stderr)
                failures += 1
            if expected_reason is None:
                if violations:
                    print(
                        f"FAIL self-test '{name}': expected acceptance, got violations={violations}",
                        file=sys.stderr,
                    )
                    failures += 1
            else:
                matched = any(expected_reason in v for v in violations)
                if not matched:
                    print(
                        f"FAIL self-test '{name}': expected reason '{expected_reason}', got violations={violations}",
                        file=sys.stderr,
                    )
                    failures += 1
        failures += self_test_scanned(tmp_path)

    if failures:
        print(f"check-curl-retry self-test: {failures} failures", file=sys.stderr)
        return 1

    print(f"check-curl-retry self-test: {len(cases)} cases ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and check files or run self-test."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test on planted cases in a temporary directory",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=pathlib.Path,
        help="Optional files to check (defaults to git ls-files)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if args.paths:
        files = args.paths
    else:
        try:
            files = get_tracked_files(repo_root)
        except RuntimeError as exc:
            print(f"check-curl-retry: {exc}", file=sys.stderr)
            return 2

    diagnostics: list[str] = []
    all_violations: list[str] = []
    for f in files:
        diags, viols = check_file(f, repo_root)
        diagnostics.extend(diags)
        all_violations.extend(viols)

    for diag in diagnostics:
        print(diag, file=sys.stderr)
    for viol in all_violations:
        print(viol)

    if diagnostics or all_violations:
        return 1

    print(f"check-curl-retry: {len(files)} files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
