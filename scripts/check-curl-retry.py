#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that curl download invocations include robust retry and retry-max-time options.

Every curl download command in shell scripts, Makefiles, and GitHub Actions workflows
must specify exponential backoff via --retry (with count >= 5) and --retry-max-time
to absorb transient network drops and server 504 errors.

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

from tracked_python import tracked_files  # noqa: E402

DESCRIPTION = "Check that curl downloads specify retry >= 5 and retry-max-time."
SHELL_SUFFIXES = frozenset({".sh", ".bash", ".mk"})
WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})
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
    arg_chars: list[str] = []
    quote: str | None = None
    paren_depth = 0
    i = 0
    while i < len(rest):
        ch = rest[i]
        if quote:
            if ch == "\\" and i + 1 < len(rest):
                arg_chars.extend([ch, rest[i + 1]])
                i += 1
            elif ch == quote:
                quote = None
                arg_chars.append(ch)
            else:
                arg_chars.append(ch)
        else:
            if ch == "\\" and i + 1 < len(rest):
                arg_chars.extend([ch, rest[i + 1]])
                i += 1
            elif ch in ('"', "'"):
                quote = ch
                arg_chars.append(ch)
            elif ch == "$" and i + 1 < len(rest) and rest[i + 1] == "(":
                paren_depth += 1
                arg_chars.extend([ch, rest[i + 1]])
                i += 1
            elif ch == "(":
                paren_depth += 1
                arg_chars.append(ch)
            elif ch == ")":
                if paren_depth > 0:
                    paren_depth -= 1
                    arg_chars.append(ch)
                else:
                    break
            elif ch in (";", "&", "|", "`"):
                break
            elif ch == "#" and (i == 0 or rest[i - 1].isspace()):
                break
            else:
                arg_chars.append(ch)
        i += 1
    return "".join(arg_chars)


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


def is_download_flag(tok: str, next_tok: str | None) -> tuple[bool, int]:
    """Check if token indicates a download output, returning (is_download, tokens_consumed)."""
    if tok in ("-O", "--remote-name"):
        return True, 1
    if tok in ("-o", "--output"):
        target = next_tok.strip("'\"") if next_tok else ""
        return target not in NON_FILE_TARGETS, 2
    if tok.startswith("--output="):
        target = tok.split("=", 1)[1].strip("'\"")
        return target not in NON_FILE_TARGETS, 1
    if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
        is_download = False
        for index, option in enumerate(tok[1:], start=1):
            if option == "O":
                is_download = True
            if option in SHORT_OPTIONS_WITH_ARGS:
                rest = tok[index + 1 :]
                target = (rest or next_tok or "").strip("'\"")
                return is_download or (option == "o" and target not in NON_FILE_TARGETS), 1 if rest else 2
        return is_download, 1
    return False, 1


def parse_retry_flag(tok: str, next_tok: str | None) -> tuple[bool, int | None, int]:
    """Parse --retry flag, returning (has_retry, count_or_none, tokens_consumed)."""
    if tok == "--retry":
        count = None
        if next_tok:
            try:
                count = int(next_tok.strip("'\""))
            except ValueError:
                count = None
        return True, count, 2
    if tok.startswith("--retry="):
        count = None
        try:
            count = int(tok.split("=", 1)[1].strip("'\""))
        except ValueError:
            count = None
        return True, count, 1
    return False, None, 1


def check_curl_args(arg_str: str) -> list[str]:
    """Check parsed arguments of a curl invocation for required retry options."""
    try:
        tokens = shlex.split(arg_str, posix=True)
    except ValueError:
        tokens = arg_str.split()

    is_download = False
    has_retry = False
    retry_val: int | None = None
    has_retry_max_time = False

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        next_tok = tokens[i + 1] if i + 1 < len(tokens) else None
        dl, dl_consumed = is_download_flag(tok, next_tok)
        if dl:
            is_download = True
        if dl_consumed > 1:
            i += dl_consumed
            continue

        ret, val, ret_consumed = parse_retry_flag(tok, next_tok)
        if ret:
            has_retry = True
            # curl uses the last --retry given, so judge the last value.
            retry_val = val
            i += ret_consumed
            continue

        if tok == "--retry-max-time" or tok.startswith("--retry-max-time="):
            has_retry_max_time = True
            if tok == "--retry-max-time" and next_tok:
                i += 1
        i += 1

    violations: list[str] = []
    if is_download and not has_retry:
        violations.append("download without --retry")
    if is_download and has_retry and retry_val is None:
        violations.append("--retry count is not a literal integer")
    elif is_download and has_retry and retry_val is not None and retry_val < 5:
        violations.append(f"--retry count {retry_val} < 5")
    if is_download and has_retry and not has_retry_max_time:
        violations.append("--retry without --retry-max-time")
    return violations


def check_content(content: str, display_path: str) -> list[str]:
    """Check file content for conforming curl retry configurations."""
    violations: list[str] = []
    lines = content.splitlines()
    buffer: list[str] = []
    start_lineno: int = 1

    for idx, raw_line in enumerate(lines, start=1):
        clean = raw_line.rstrip("\r\n")
        if not buffer and clean.lstrip().startswith("#"):
            continue

        if not buffer:
            start_lineno = idx

        if clean.endswith("\\"):
            buffer.append(clean[:-1].rstrip())
        else:
            buffer.append(clean)
            logical_line = " ".join(buffer)
            buffer = []
            for arg_str in extract_curl_args(logical_line):
                for reason in check_curl_args(arg_str):
                    violations.append(f"{display_path}:{start_lineno}: {reason}")

    if buffer:
        logical_line = " ".join(buffer)
        for arg_str in extract_curl_args(logical_line):
            for reason in check_curl_args(arg_str):
                violations.append(f"{display_path}:{start_lineno}: {reason}")

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


def is_scanned(path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    """Shell scripts, Makefiles and GitHub workflow files carry curl commands."""
    rel = path.relative_to(repo_root)
    if path.suffix in SHELL_SUFFIXES or path.name == "Makefile":
        return True
    return rel.parent == pathlib.Path(".github/workflows") and path.suffix in WORKFLOW_SUFFIXES


def get_tracked_files(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Git-tracked shell, make and workflow files (shared tracked_files helper)."""
    return [f for f in tracked_files(repo_root) if is_scanned(f, repo_root)]


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
        if is_scanned(root / rel, root) != expected:
            print(f"FAIL self-test path '{rel}': expected scanned={expected}", file=sys.stderr)
            failures += 1
    return failures


def run_self_test() -> int:
    """Run self-test on planted cases in a temporary directory."""
    cases = [
        ("p1", "curl --retry 2 --retry-max-time 60 -o out https://example.com/file\n", True),
        ("p2", "curl -o out https://example.com/file\n", True),
        ("p3", "curl --retry 6 -o out https://example.com/file\n", True),
        (
            "p4",
            "curl \\\n  --fail \\\n  --retry 6 \\\n  --retry-max-time 60 \\\n  -o out \\\n  https://example.com/file\n",
            False,
        ),
        ("p5", "curl -fsS -o /dev/null https://example.com/probe\n", False),
        ("p6", "# curl --output x https://example.com/file\n", False),
        ("p7", 'curl --retry "$N" --retry-max-time 60 -o out https://example.com/file\n', True),
        ("p8", "curl --retry-max-time 60 -o out https://example.com/file --retry\n", True),
        ("p9", "curl -fsSLo out https://example.com/file\n", True),
        ("p10", "curl -OL https://example.com/file\n", True),
        ("p11", "curl -fsSLo /dev/null https://example.com/file\n", False),
        ("p12", "curl -sH 'X-o: 1' https://example.com/file\n", False),
        ("p13", "curl -fsSL -o out --retry 6 --retry-max-time 60 https://example.com/file\n", False),
        ("p14", "curl --retry 2 -o /dev/null https://example.com/file\n", False),
        ("p15", 'curl --retry 6 --retry "$N" --retry-max-time 60 -o out https://example.com/file\n', True),
        ("p16", "curl --retry 6 --retry 2 --retry-max-time 60 -o out https://example.com/file\n", True),
        ("p17", 'curl --retry "$N" --retry 6 --retry-max-time 60 -o out https://example.com/file\n', False),
        ("p18", "curl -fsSLo - https://example.com/status | jq .\n", False),
        ("p19", "curl -o - https://example.com/status\n", False),
        ("p20", "curl --output=- https://example.com/status\n", False),
    ]
    failures = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        for name, content, should_refuse in cases:
            test_file = tmp_path / f"{name}.sh"
            test_file.write_text(content, encoding="utf-8")
            diags, violations = check_file(test_file, tmp_path)
            if diags:
                print(f"FAIL self-test '{name}': unexpected diagnostics: {diags}", file=sys.stderr)
                failures += 1
            has_violations = len(violations) > 0
            if has_violations != should_refuse:
                print(
                    f"FAIL self-test '{name}': expected refusal={should_refuse}, got violations={violations}",
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
