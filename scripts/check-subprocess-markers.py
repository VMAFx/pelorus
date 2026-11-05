#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Ratchet on unmarked subprocess import and call sites in tracked Python files.

Codacy Bandit (B404 on import, B603 on call) and opengrep
dangerous-subprocess-use-audit flag unmarked subprocess invocations only on PRs
in CI. This local ratchet blocks growth before push.

Unmarked import sites (B404) lack 'nosec B404' on their start line.
Unmarked call sites lack 'nosec B603' (Bandit) or
'nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit'
(Semgrep) on their start line.

To update the baseline when sites are eliminated or approved:
    python3 scripts/check-subprocess-markers.py --write-baseline
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
import tokenize

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import tracked_python  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).resolve().parent / "subprocess-markers-baseline.json"
SEMGREP_RULE = (
    "python.lang.security.audit.dangerous-subprocess-use-audit."
    "dangerous-subprocess-use-audit"
)
TARGET_METHODS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    }
)


def has_nosec_rule(comment: str, rule: str) -> bool:
    """True if comment contains nosec with the given rule id."""
    for m in re.finditer(r"\bnosec\b([^\n#]*)", comment):
        segment = m.group(1).split(" - ")[0]
        rule_ids = set(re.findall(r"\bB\d+\b", segment))
        if rule in rule_ids:
            return True
    return False


def has_semgrep_rule(comment: str, rule: str) -> bool:
    """True if comment contains nosemgrep with the given rule id."""
    for m in re.finditer(r"\bnosemgrep\b\s*:?([^\n#]*)", comment):
        segment = m.group(1).split(" - ")[0]
        ids = {tok.strip() for tok in segment.split(",") if tok.strip()}
        if rule in ids:
            return True
    return False


def extract_comments(text: str) -> tuple[dict[int, str], str | None]:
    """Extract comment tokens mapped by line number."""
    comments: dict[int, str] = {}
    try:
        reader = io.StringIO(text).readline
        for tok in tokenize.generate_tokens(reader):
            if tok.type == tokenize.COMMENT:
                line = tok.start[0]
                if line in comments:
                    comments[line] += " " + tok.string
                else:
                    comments[line] = tok.string
    except (SyntaxError, tokenize.TokenError) as exc:
        return {}, f"tokenize error: {exc}"
    return comments, None


def find_bindings_and_b404(
    tree: ast.AST, comments: dict[int, str]
) -> tuple[set[str], set[str], int]:
    """Collect bound subprocess names and count unmarked B404 import statements."""
    sub_modules: set[str] = {"subprocess"}
    sub_funcs: set[str] = set()
    b404_unmarked = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            has_sub = False
            for alias in node.names:
                if alias.name == "subprocess":
                    has_sub = True
                    sub_modules.add(alias.asname or alias.name)
            if has_sub:
                cmt = comments.get(node.lineno, "")
                if not has_nosec_rule(cmt, "B404"):
                    b404_unmarked += 1
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess" and (node.level or 0) == 0:
                cmt = comments.get(node.lineno, "")
                if not has_nosec_rule(cmt, "B404"):
                    b404_unmarked += 1
                for alias in node.names:
                    if alias.name in TARGET_METHODS:
                        sub_funcs.add(alias.asname or alias.name)
                    elif alias.name == "*":
                        sub_funcs.update(TARGET_METHODS)

    return sub_modules, sub_funcs, b404_unmarked


def count_calls(
    tree: ast.AST,
    comments: dict[int, str],
    sub_modules: set[str],
    sub_funcs: set[str],
) -> tuple[int, int]:
    """Count unmarked B603 and Semgrep subprocess call sites."""
    b603_unmarked = 0
    semgrep_unmarked = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_target = False
        if isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in sub_modules
            ):
                if node.func.attr in TARGET_METHODS:
                    is_target = True
        elif isinstance(node.func, ast.Name):
            if node.func.id in sub_funcs:
                is_target = True

        if is_target:
            cmt = comments.get(node.lineno, "")
            if not has_nosec_rule(cmt, "B603"):
                b603_unmarked += 1
            if not has_semgrep_rule(cmt, SEMGREP_RULE):
                semgrep_unmarked += 1

    return b603_unmarked, semgrep_unmarked


def count_unmarked_sites(
    text: str, filename: str = "<string>"
) -> tuple[dict[str, int], str | None]:
    """Count unmarked b404, b603, and semgrep sites in Python source text."""
    comments, tok_err = extract_comments(text)
    if tok_err is not None:
        return {"b404": 0, "b603": 0, "semgrep": 0}, f"{filename}: {tok_err}"

    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as exc:
        return {
            "b404": 0,
            "b603": 0,
            "semgrep": 0,
        }, f"{filename}:{exc.lineno}: not parseable: {exc.msg}"

    sub_modules, sub_funcs, b404 = find_bindings_and_b404(tree, comments)
    b603, semgrep = count_calls(tree, comments, sub_modules, sub_funcs)
    return {"b404": b404, "b603": b603, "semgrep": semgrep}, None


def count_file(path: Path, root: Path) -> tuple[dict[str, int], str | None]:
    """Read a Python file in declared encoding and count its unmarked sites."""
    rel = path.relative_to(root).as_posix()
    try:
        with tokenize.open(path) as handle:
            text = handle.read()
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return {"b404": 0, "b603": 0, "semgrep": 0}, f"{rel}: cannot read: {exc}"
    return count_unmarked_sites(text, filename=rel)


def compare_counts(
    current: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> tuple[list[str], list[str]]:
    """Compare current counts to baseline, returning (growth, tighten) findings."""
    growth: list[str] = []
    tighten: list[str] = []
    all_files = sorted(set(current.keys()) | set(baseline.keys()))
    for path in all_files:
        cur_file = current.get(path, {})
        base_file = baseline.get(path, {})
        for kind in ("b404", "b603", "semgrep"):
            now = cur_file.get(kind, 0)
            base = base_file.get(kind, 0)
            if now > base:
                growth.append(f"{path}: {kind} unmarked {now} > baseline {base}")
            elif now < base:
                tighten.append(
                    f"{path}: {kind} baseline can drop to {now} (run --write-baseline)"
                )
    return growth, tighten


def load_baseline(baseline_path: Path) -> tuple[dict[str, dict[str, int]], str | None]:
    """Load the baseline files dict; a missing or malformed baseline is an error."""
    try:
        with baseline_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, f"{baseline_path.name}: cannot read baseline: {exc}"
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or data.get("version") != 1:
        return {}, f"{baseline_path.name}: expected version 1 with a files object"
    return files, None


def save_baseline(
    target: Path, files_dict: dict[str, dict[str, int]]
) -> None:
    """Save baseline files atomically to target."""
    ordered_files = {
        k: {
            "b404": files_dict[k]["b404"],
            "b603": files_dict[k]["b603"],
            "semgrep": files_dict[k]["semgrep"],
        }
        for k in sorted(files_dict.keys())
    }
    data = {"version": 1, "files": ordered_files}
    content = json.dumps(data, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=target.parent, delete=False, encoding="utf-8"
    ) as tf:
        tf.write(content)
        tmp_name = tf.name
    os.replace(tmp_name, target)


def get_tracked_python_files(root: Path) -> list[Path]:
    """Every git-tracked regular Python file under root, sorted."""
    return sorted(
        p
        for p in tracked_python.tracked_files(root)
        if p.is_file() and tracked_python.is_python(p)
    )


def scan_all_files(
    files: list[Path], root: Path
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Scan tracked Python files, returning non-zero counts and diagnostics."""
    current_counts: dict[str, dict[str, int]] = {}
    diagnostics: list[str] = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        counts, err = count_file(p, root)
        if err is not None:
            diagnostics.append(err)
        elif any(counts.values()):
            current_counts[rel] = counts
    return current_counts, diagnostics


def check_planted_1_to_5() -> list[str]:
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
        f"nosemgrep: {SEMGREP_RULE} - why\n"
    )
    c5, e5 = count_unmarked_sites(l5, "case5.py")
    if e5 or c5.get("b603") != 0 or c5.get("semgrep") != 0:
        fails.append(f"case 5 failed: want b603=0 semgrep=0, got {c5}")

    return fails


def check_planted_6_to_9() -> list[str]:
    """Planted cases 6 through 9."""
    fails: list[str] = []
    code6 = (
        "subprocess.run(\n"
        "    ['x'],  # nosec B603 - why  # "
        f"nosemgrep: {SEMGREP_RULE} - why\n"
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
        f'nosemgrep: {SEMGREP_RULE} - why"])\n'
    )
    c9, e9 = count_unmarked_sites(code9, "case9.py")
    if e9 or c9.get("b603") != 1 or c9.get("semgrep") != 1:
        fails.append(f"case 9 failed: want b603=1 semgrep=1, got {c9} err={e9}")

    return fails


def check_case_10() -> list[str]:
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


def check_planted_11() -> list[str]:
    """Planted case 11: unrelated .run( on other object (proc.run()) -> 0."""
    fails: list[str] = []
    code11 = "def f(proc):\n    proc.run()\n"
    c11, e11 = count_unmarked_sites(code11, "case11.py")
    if e11 or c11.get("b603") != 0 or c11.get("semgrep") != 0:
        fails.append(
            f"case 11 failed: want b603=0 semgrep=0, got {c11} err={e11}"
        )
    return fails


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


def check_planted_missing_git() -> list[str]:
    """Planted case 13: missing git on PATH raises RuntimeError naming git."""
    fails: list[str] = []
    orig_which = tracked_python.shutil.which
    try:
        tracked_python.shutil.which = lambda _cmd: None
        try:
            tracked_python.tracked_files(ROOT)
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


def run_self_test_planted() -> int:
    """Run all 16 planted cases in temporary directories; cases 12-13 use throwaway git repos."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sample = Path(tmp_dir) / "sample.py"
        sample.write_text("import subprocess\n", encoding="utf-8")
        counts, err = count_file(sample, Path(tmp_dir))
        failures: list[str] = []
        if err or counts.get("b404") != 1:
            failures.append(f"tempfile test failed: {err} {counts}")
        failures.extend(check_planted_1_to_5())
        failures.extend(check_planted_6_to_9())
        failures.extend(check_case_10())
        failures.extend(check_planted_11())
        failures.extend(check_planted_git_isolation())
        failures.extend(check_planted_missing_git())
        failures.extend(check_planted_git_isolation_skip())
        failures.extend(check_planted_not_a_repo())
        failures.extend(check_planted_not_a_repo_skip())
        if failures:
            for failure in failures:
                print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
            return 1
    print("subprocess markers self-test: 16 planted cases ok")
    return 0


def run_check(root: Path, baseline_path: Path) -> int:
    """Check tracked Python files against baseline; exit 1 on growth/tighten."""
    try:
        files = get_tracked_python_files(root)
    except RuntimeError as exc:
        print(f"check-subprocess-markers: {exc}", file=sys.stderr)
        return 2
    current_counts, diagnostics = scan_all_files(files, root)
    if diagnostics:
        for diag in diagnostics:
            print(diag, file=sys.stderr)
        return 1

    baseline, base_err = load_baseline(baseline_path)
    if base_err is not None:
        print(base_err, file=sys.stderr)
        return 1
    growth, tighten = compare_counts(current_counts, baseline)
    all_findings = growth + tighten
    for item in all_findings:
        print(item, file=sys.stderr)
    if all_findings:
        return 1

    print(f"subprocess markers: {len(files)} files, no growth")
    return 0


def run_write_baseline(root: Path, baseline_path: Path) -> int:
    """Scan tracked Python files and write current counts to baseline."""
    try:
        files = get_tracked_python_files(root)
    except RuntimeError as exc:
        print(f"check-subprocess-markers: {exc}", file=sys.stderr)
        return 2
    current_counts, diagnostics = scan_all_files(files, root)
    if diagnostics:
        for diag in diagnostics:
            print(diag, file=sys.stderr)
        return 1
    save_baseline(baseline_path, current_counts)
    print(f"wrote baseline: {len(current_counts)} files with unmarked sites")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for check-subprocess-markers."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="verify counts against baseline (default)",
    )
    group.add_argument(
        "--write-baseline",
        action="store_true",
        help="write current counts to baseline",
    )
    group.add_argument(
        "--self-test",
        action="store_true",
        help="run planted cases, then the tree check (--check)",
    )
    group.add_argument(
        "--self-test-only",
        action="store_true",
        help="run self-test only",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    if args.self_test_only:
        return run_self_test_planted()

    if args.self_test:
        status = run_self_test_planted()
        if status != 0:
            return status
        return run_check(ROOT, BASELINE_PATH)

    if args.write_baseline:
        return run_write_baseline(ROOT, BASELINE_PATH)

    return run_check(ROOT, BASELINE_PATH)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
