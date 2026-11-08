#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Validation of curl timing environment variables in repository scripts.

Proves that scripts/fetch-ffmpeg-series.sh, scripts/check-gitleaks.sh, and
scripts/bench/fetch-corpus.sh enforce positive-decimal-integer format on
CURL_MAX_TIME, CURL_STALL_TIME, and CURL_RETRY_WINDOW before transfers,
accept boundary value 1, and default to expected values when unset.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
import pathlib
import shutil
import subprocess  # nosec B404 - test harness runs known local scripts
import sys
import tempfile

RUN_TIMEOUT = 10

ROOT = pathlib.Path(__file__).resolve().parents[1]
FETCH_SERIES_SCRIPT = pathlib.Path(
    os.environ.get("FETCH_SERIES_SCRIPT", ROOT / "scripts" / "fetch-ffmpeg-series.sh")
)
CHECK_GITLEAKS_SCRIPT = pathlib.Path(
    os.environ.get("CHECK_GITLEAKS_SCRIPT", ROOT / "scripts" / "check-gitleaks.sh")
)
FETCH_CORPUS_SCRIPT = pathlib.Path(
    os.environ.get(
        "FETCH_CORPUS_SCRIPT", ROOT / "scripts" / "bench" / "fetch-corpus.sh"
    )
)

VARIABLES: tuple[str, ...] = (
    "CURL_MAX_TIME",
    "CURL_STALL_TIME",
    "CURL_RETRY_WINDOW",
)
BAD_VALUES: tuple[str, ...] = ("0", "-1", "abc", "1.5", "07", "6 ", "1e3")


def _run_script(
    argv: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Execute script command with RUN_TIMEOUT and captured output."""
    return subprocess.run(  # nosec B603 - fixed bash argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - test harness runs known local scripts with explicit deadline, no shell
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
        check=False,
    )


def _write_curl_shim(tools_dir: pathlib.Path, log_file: pathlib.Path) -> None:
    """Write capture-only curl shim that appends argv JSON to log_file and exits 28."""
    tools_dir.mkdir(parents=True, exist_ok=True)
    shim = tools_dir / "curl"
    code = (
        f"#!{sys.executable} -I\n"
        f"import json\n"
        f"import sys\n"
        f"with open({repr(str(log_file))}, 'a', encoding='utf-8') as f:\n"
        f"    f.write(json.dumps(sys.argv) + '\\n')\n"
        f"sys.exit(28)\n"
    )
    shim.write_text(code, encoding="utf-8")
    shim.chmod(0o755)


def _clean_env(tools_dir: pathlib.Path, overrides: dict[str, str]) -> dict[str, str]:
    """Build clean subprocess environment with tools_dir prepended to PATH."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in (*VARIABLES, "FFMPEG_SERIES_FROM")
    }
    env["PATH"] = f"{tools_dir}:{env.get('PATH', '')}"
    env.update(overrides)
    return env


def _read_curl_log(log_file: pathlib.Path) -> list[list[str]]:
    """Read recorded curl argv lists from JSON lines in log_file."""
    if not log_file.is_file():
        return []
    calls: list[list[str]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if trimmed:
            calls.append(json.loads(trimmed))
    return calls


def _run_series(
    overrides: dict[str, str],
) -> tuple[int, str, list[list[str]]]:
    """Run fetch-ffmpeg-series.sh in temporary root and return (rc, stderr, curl_calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        tools = work / "tools"
        log_file = work / "curl.log"
        _write_curl_shim(tools, log_file)
        root = work / "root"
        scripts_dir = root / "scripts"
        scripts_dir.mkdir(parents=True)
        shutil.copy(FETCH_SERIES_SCRIPT, scripts_dir / "fetch-ffmpeg-series.sh")
        shutil.copy(ROOT / "build-config.env", root / "build-config.env")
        out = root / "out"
        env = _clean_env(tools, overrides)
        proc = _run_script(
            ["bash", str(scripts_dir / "fetch-ffmpeg-series.sh"), str(out)],
            env,
        )
        return proc.returncode, proc.stderr, _read_curl_log(log_file)


def _run_gitleaks(
    overrides: dict[str, str],
) -> tuple[int, str, list[list[str]]]:
    """Run check-gitleaks.sh --install in temporary root and return (rc, stderr, curl_calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        tools = work / "tools"
        log_file = work / "curl.log"
        _write_curl_shim(tools, log_file)
        scripts_dir = work / "scripts"
        scripts_dir.mkdir(parents=True)
        script_copy = scripts_dir / "check-gitleaks.sh"
        shutil.copy(CHECK_GITLEAKS_SCRIPT, script_copy)
        install_dir = work / "gl"
        env = _clean_env(tools, overrides)
        proc = _run_script(
            ["bash", str(script_copy), "--install", str(install_dir)],
            env,
        )
        return proc.returncode, proc.stderr, _read_curl_log(log_file)


def _run_corpus(
    overrides: dict[str, str],
) -> tuple[int, str, list[list[str]]]:
    """Run fetch-corpus.sh in temporary layout and return (rc, stderr, curl_calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        tools = work / "tools"
        log_file = work / "curl.log"
        _write_curl_shim(tools, log_file)
        corpus_dir = work / "corpus"
        lock_file = work / "corpus.lock"
        ffmpeg_stub = work / "ffmpeg-stub"
        ffmpeg_stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        ffmpeg_stub.chmod(0o755)
        sha = "0" * 64
        lock_file.write_text(
            f"clip | http://127.0.0.1:9/clip.bin | {sha} | -ss 0 | 16x16 | yuv420p | 1 | 30 | CC-BY-4.0\n",
            encoding="utf-8",
        )
        env = _clean_env(tools, overrides)
        env["CORPUS"] = str(corpus_dir)
        env["CORPUS_LOCK"] = str(lock_file)
        env["FFMPEG"] = str(ffmpeg_stub)
        proc = _run_script(["bash", str(FETCH_CORPUS_SCRIPT)], env)
        return proc.returncode, proc.stderr, _read_curl_log(log_file)


def _option_value(curl_words: list[str], name: str) -> str | None:
    """Return option value after the last occurrence of name in curl_words."""
    if name not in curl_words:
        return None
    index = len(curl_words) - 1 - curl_words[::-1].index(name)
    return curl_words[index + 1] if index + 1 < len(curl_words) else None


TARGETS: tuple[
    tuple[str, Callable[[dict[str, str]], tuple[int, str, list[list[str]]]]], ...
] = (
    ("fetch-ffmpeg-series", _run_series),
    ("check-gitleaks", _run_gitleaks),
    ("fetch-corpus", _run_corpus),
)


def _test_negative_leg() -> tuple[list[str], int]:
    """Verify refusal of invalid values across scripts and variables."""
    failures: list[str] = []
    count = 0
    for name, runner in TARGETS:
        for var in VARIABLES:
            for val in BAD_VALUES:
                count += 1
                rc, stderr, curl_calls = runner({var: val})
                if rc == 0:
                    failures.append(
                        f"{name}: {var}={val!r} succeeded (rc=0), expected failure"
                    )
                expected_msg = f"{var} must be a positive decimal integer"
                if expected_msg not in stderr:
                    failures.append(
                        f"{name}: {var}={val!r} stderr missing {expected_msg!r}: {stderr.strip()!r}"
                    )
                if curl_calls:
                    failures.append(
                        f"{name}: {var}={val!r} invoked curl {len(curl_calls)} times"
                    )
    return failures, count


def _test_boundary_leg() -> list[str]:
    """Verify that setting all variables to 1 is accepted and passed to curl."""
    failures: list[str] = []
    boundary_env = {var: "1" for var in VARIABLES}
    for name, runner in TARGETS:
        _, stderr, curl_calls = runner(boundary_env)
        if "must be a positive decimal integer" in stderr:
            failures.append(
                f"{name} boundary: unexpected validation failure in stderr: {stderr.strip()!r}"
            )
        if not curl_calls:
            failures.append(f"{name} boundary: no curl invocation recorded")
            continue
        words = curl_calls[0]
        for opt, val in (
            ("--max-time", "1"),
            ("--speed-time", "1"),
            ("--retry-max-time", "1"),
        ):
            got = _option_value(words, opt)
            if got != val:
                failures.append(f"{name} boundary: {opt} is {got!r}, want {val!r}")
    return failures


# Default --max-time and --retry-max-time per script; the window default is
# twice CURL_MAX_TIME in all three. --speed-time defaults to 30 everywhere.
DEFAULTS = {
    "fetch-ffmpeg-series": ("120", "240"),
    "check-gitleaks": ("120", "240"),
    "fetch-corpus": ("600", "1200"),
}


def _check_default_call(name: str, words: list[str]) -> list[str]:
    """Compare one default curl call against the script's expected timing options."""
    max_t, retry_w = DEFAULTS[name]
    errs: list[str] = []
    for opt, want in (
        ("--speed-time", "30"),
        ("--max-time", max_t),
        ("--retry-max-time", retry_w),
    ):
        got = _option_value(words, opt)
        if got != want:
            errs.append(f"{name} default: {opt} is {got!r}, want {want!r}")
    return errs


def _test_default_leg() -> list[str]:
    """Verify default timing options when no variables are set."""
    failures: list[str] = []
    for name, runner in TARGETS:
        _, _, curl_calls = runner({})
        if not curl_calls:
            failures.append(f"{name} default: no curl invocation recorded")
            continue
        failures.extend(_check_default_call(name, curl_calls[0]))
    return failures


def main() -> int:
    """Run all validation tests and report failures or summary."""
    failures, refusals = _test_negative_leg()
    failures.extend(_test_boundary_leg())
    failures.extend(_test_default_leg())
    if failures:
        for fail in failures:
            print(f"FAIL: {fail}", file=sys.stderr)
        return 1
    print(f"curl-env-validation: {refusals} refusals, 3 boundary, 3 default runs ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
