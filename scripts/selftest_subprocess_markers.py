# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Planted self-test cases and verification harness for check-subprocess-markers,
covering B404 and B603 suppressions, Semgrep rules, baseline comparison logic,
and git isolation or missing-executable behaviour under temporary repositories."""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import tracked_python  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEMGREP_RULE = (
    "python.lang.security.audit.dangerous-subprocess-use-audit."
    "dangerous-subprocess-use-audit"
)


def _restore_env_var(name: str, old_val: str | None) -> None:
    """Restore an environment variable to its previous value or remove it."""
    if old_val is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = old_val


def _setup_planted_repo(tmp_path: Path, git: str, clean_env: dict[str, str]) -> Path:
    """Initialize a git repo with one added file in tmp_path."""
    temp_file = tmp_path / "planted.py"
    temp_file.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        [git, "-C", str(tmp_path), "init", "-q"],
        capture_output=True,
        check=True,
        timeout=30.0,
        env=clean_env,
    )
    subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        [git, "-C", str(tmp_path), "add", "planted.py"],
        capture_output=True,
        check=True,
        timeout=30.0,
        env=clean_env,
    )
    return temp_file


def check_planted_1_to_5(
    count_unmarked_sites: Callable[[str, str], tuple[dict[str, int], str | None]],
    semgrep_rule: str = SEMGREP_RULE,
) -> list[str]:
    """Planted cases 1 through 5."""
    fails: list[str] = []
    c1, e1 = count_unmarked_sites("import subprocess\n", "case1.py")
    if e1 or c1.get("b404") != 1:
        fails.append(f"case 1 failed: want b404=1, got {c1}")

    c2, e2 = count_unmarked_sites(
        "import subprocess  # nosec B404 - why\n", "case2.py"
    )
    if e2 or c2.get("b404") != 0:
        fails.append(f"case 2 failed: want b404=0, got {c2}")

    c3, e3 = count_unmarked_sites(
        'subprocess.run(["x"], timeout=1)\n', "case3.py"
    )
    if e3 or c3.get("b603") != 1 or c3.get("semgrep") != 1:
        fails.append(f"case 3 failed: want b603=1 semgrep=1, got {c3}")

    c4, e4 = count_unmarked_sites(
        'subprocess.run(["x"], timeout=1)  # nosec B603 - why\n', "case4.py"
    )
    if e4 or c4.get("b603") != 0 or c4.get("semgrep") != 1:
        fails.append(f"case 4 failed: want b603=0 semgrep=1, got {c4}")

    l5 = (
        'subprocess.run(["x"], timeout=1)  # nosec B603 - why  # '
        f"nosemgrep: {semgrep_rule} - why\n"
    )
    c5, e5 = count_unmarked_sites(l5, "case5.py")
    if e5 or c5.get("b603") != 0 or c5.get("semgrep") != 0:
        fails.append(f"case 5 failed: want b603=0 semgrep=0, got {c5}")

    return fails


def check_planted_6_to_9(
    count_unmarked_sites: Callable[[str, str], tuple[dict[str, int], str | None]],
    semgrep_rule: str = SEMGREP_RULE,
) -> list[str]:
    """Planted cases 6 through 9."""
    fails: list[str] = []
    code6 = (
        "subprocess.run(\n"
        "    ['x'],  # nosec B603 - why  # "
        f"nosemgrep: {semgrep_rule} - why\n"
        "    timeout=1,\n"
        ")\n"
    )
    c6, e6 = count_unmarked_sites(code6, "case6.py")
    if e6 or c6.get("b603") != 1 or c6.get("semgrep") != 1:
        fails.append(f"case 6 failed: want b603=1 semgrep=1, got {c6} err={e6}")

    code7 = "from subprocess import run as r\nr(['x'])\n"
    c7, e7 = count_unmarked_sites(code7, "case7.py")
    if e7 or c7.get("b603") != 1:
        fails.append(f"case 7 failed: want b603=1, got {c7} err={e7}")

    code8 = "import subprocess as sp\nsp.check_output(['x'])\n"
    c8, e8 = count_unmarked_sites(code8, "case8.py")
    if e8 or c8.get("b603") != 1:
        fails.append(f"case 8 failed: want b603=1, got {c8} err={e8}")

    code9 = (
        'subprocess.run(["# nosec B603 - why # '
        f'nosemgrep: {semgrep_rule} - why"])\n'
    )
    c9, e9 = count_unmarked_sites(code9, "case9.py")
    if e9 or c9.get("b603") != 1 or c9.get("semgrep") != 1:
        fails.append(f"case 9 failed: want b603=1 semgrep=1, got {c9} err={e9}")

    return fails


def check_case_10(
    compare_counts: Callable[
        [dict[str, dict[str, int]], dict[str, dict[str, int]]],
        tuple[list[str], list[str]],
    ],
) -> list[str]:
    """Planted case 10: compare growth, tighten, new file, equal."""
    fails: list[str] = []
    base_file = {"b404": 1, "b603": 2, "semgrep": 2}

    grow, tight = compare_counts(
        {"f.py": {"b404": 2, "b603": 2, "semgrep": 2}}, {"f.py": base_file}
    )
    if not grow or tight:
        fails.append("case 10: want growth finding, got none or got tighten")

    grow, tight = compare_counts(
        {"f.py": {"b404": 0, "b603": 2, "semgrep": 2}}, {"f.py": base_file}
    )
    if grow or not tight:
        fails.append("case 10: want tighten finding, got none or got growth")

    grow, tight = compare_counts(
        {"new.py": {"b404": 1, "b603": 0, "semgrep": 0}}, {}
    )
    if not grow or tight:
        fails.append("case 10: want growth finding for new file")

    grow, tight = compare_counts({"f.py": base_file}, {"f.py": base_file})
    if grow or tight:
        fails.append("case 10: want no findings when equal")

    return fails


def check_planted_11(
    count_unmarked_sites: Callable[[str, str], tuple[dict[str, int], str | None]],
) -> list[str]:
    """Planted case 11: unrelated .run( on other object (proc.run()) -> 0."""
    fails: list[str] = []
    code11 = "def f(proc):\n    proc.run()\n"
    c11, e11 = count_unmarked_sites(code11, "case11.py")
    if e11 or c11.get("b603") != 0 or c11.get("semgrep") != 0:
        fails.append(
            f"case 11 failed: want b603=0 semgrep=0, got {c11} err={e11}"
        )
    return fails


def check_planted_git_isolation() -> list[str]:
    """Planted case 12: a caller GIT_DIR (decoy repo, never the real one) cannot steer tracked_files."""
    git = shutil.which("git")
    if git is None:
        print("[SKIP] case 12 git isolation: git not found on PATH")
        return []
    clean_env = {
        k: v
        for k, v in tracked_python.repo_discovery_env().items()
        if not k.startswith("GIT_")
    }
    with tempfile.TemporaryDirectory() as tmp_dir, tempfile.TemporaryDirectory() as decoy_dir:
        tmp_path = Path(tmp_dir)
        temp_file = _setup_planted_repo(tmp_path, git, clean_env)
        _setup_planted_repo(Path(decoy_dir), git, clean_env)
        old_git_dir = os.environ.get("GIT_DIR")
        old_index = os.environ.get("GIT_INDEX_FILE")
        try:
            os.environ["GIT_DIR"] = str(Path(decoy_dir) / ".git")
            os.environ["GIT_INDEX_FILE"] = str(tmp_path / "bogus_index")
            got = tracked_python.tracked_files(tmp_path)
            if got != [temp_file]:
                return [
                    f"case 12 failed: want {[str(temp_file)]}, got {[str(p) for p in got]}"
                ]
        finally:
            _restore_env_var("GIT_DIR", old_git_dir)
            _restore_env_var("GIT_INDEX_FILE", old_index)
    return []


def check_planted_missing_git(root: Path) -> list[str]:
    """Planted case 13: missing git on PATH raises RuntimeError naming git."""
    fails: list[str] = []
    orig_which = tracked_python.shutil.which
    try:
        tracked_python.shutil.which = lambda _cmd: None
        try:
            tracked_python.tracked_files(root)
        except RuntimeError as exc:
            if "git" not in str(exc).lower():
                fails.append(
                    f"case 13 failed: RuntimeError did not name git: {exc!r}"
                )
        except (TypeError, OSError) as exc:
            fails.append(
                f"case 13 failed: expected RuntimeError, got {type(exc).__name__}: {exc}"
            )
        else:
            fails.append("case 13 failed: expected RuntimeError, got no exception")
    finally:
        tracked_python.shutil.which = orig_which
    return fails


def _check_planted_git_skip(
    case_num: int,
    check_fn: Callable[[], list[str]],
    expected_skip: str,
) -> list[str]:
    """Planted skip helper: check_fn skips and prints expected_skip when git is missing."""
    fails: list[str] = []
    orig_which = shutil.which
    buf = io.StringIO()
    try:
        shutil.which = lambda _cmd, *args, **kwargs: None
        with contextlib.redirect_stdout(buf):
            res = check_fn()
        if res != []:
            fails.append(
                f"case {case_num} failed: expected empty failures list on missing git, got {res}"
            )
        output = buf.getvalue()
        if expected_skip not in output:
            fails.append(
                f"case {case_num} failed: expected output to contain {expected_skip!r}, got {output!r}"
            )
    finally:
        shutil.which = orig_which
    return fails


def check_planted_git_isolation_skip() -> list[str]:
    """Planted case 14: check_planted_git_isolation skips when git is missing."""
    return _check_planted_git_skip(14, check_planted_git_isolation, "[SKIP] case 12")


def check_planted_not_a_repo() -> list[str]:
    """Planted case 15: non-git directory raises RuntimeError with stderr details."""
    if shutil.which("git") is None:
        print("[SKIP] case 15 not-a-repo: git not found on PATH")
        return []
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        old_ceiling = os.environ.get("GIT_CEILING_DIRECTORIES")
        try:
            os.environ["GIT_CEILING_DIRECTORIES"] = str(tmp_path.parent)
            tracked_python.tracked_files(tmp_path)
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "ls-files" not in msg:
                fails.append(
                    f"case 15 failed: RuntimeError missing 'ls-files': {exc!r}"
                )
            if "not a git repository" not in msg:
                fails.append(
                    f"case 15 failed: RuntimeError missing 'not a git repository': {exc!r}"
                )
        except Exception as exc:
            fails.append(
                f"case 15 failed: expected RuntimeError, got {type(exc).__name__}: {exc}"
            )
        else:
            fails.append("case 15 failed: expected RuntimeError, got no exception")
        finally:
            _restore_env_var("GIT_CEILING_DIRECTORIES", old_ceiling)
    return fails


def check_planted_not_a_repo_skip() -> list[str]:
    """Planted case 16: check_planted_not_a_repo skips when git is missing."""
    return _check_planted_git_skip(
        16,
        check_planted_not_a_repo,
        "[SKIP] case 15 not-a-repo: git not found on PATH",
    )


def run_self_test_planted(
    count_file_fn: Callable[[Path, Path], tuple[dict[str, int], str | None]],
    count_unmarked_sites_fn: Callable[[str, str], tuple[dict[str, int], str | None]],
    compare_counts_fn: Callable[
        [dict[str, dict[str, int]], dict[str, dict[str, int]]],
        tuple[list[str], list[str]],
    ],
    semgrep_rule: str = SEMGREP_RULE,
    root: Path | None = None,
) -> int:
    """Run all 16 planted cases in temporary directories; cases 12-13 use throwaway git repos."""
    target_root = root if root is not None else ROOT
    with tempfile.TemporaryDirectory() as tmp_dir:
        sample = Path(tmp_dir) / "sample.py"
        sample.write_text("import subprocess\n", encoding="utf-8")
        counts, err = count_file_fn(sample, Path(tmp_dir))
        failures: list[str] = []
        if err or counts.get("b404") != 1:
            failures.append(f"tempfile test failed: {err} {counts}")
        failures.extend(check_planted_1_to_5(count_unmarked_sites_fn, semgrep_rule))
        failures.extend(check_planted_6_to_9(count_unmarked_sites_fn, semgrep_rule))
        failures.extend(check_case_10(compare_counts_fn))
        failures.extend(check_planted_11(count_unmarked_sites_fn))
        failures.extend(check_planted_git_isolation())
        failures.extend(check_planted_missing_git(target_root))
        failures.extend(check_planted_git_isolation_skip())
        failures.extend(check_planted_not_a_repo())
        failures.extend(check_planted_not_a_repo_skip())
        if failures:
            for failure in failures:
                print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
            return 1
    print("subprocess markers self-test: 16 planted cases ok")
    return 0
