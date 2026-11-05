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
import io
import json
import os
from pathlib import Path
import re
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


def run_self_test_planted() -> int:
    """Run planted self-test cases in selftest_subprocess_markers."""
    import selftest_subprocess_markers  # noqa: E402

    return selftest_subprocess_markers.run_self_test_planted(
        count_file_fn=count_file,
        count_unmarked_sites_fn=count_unmarked_sites,
        compare_counts_fn=compare_counts,
        semgrep_rule=SEMGREP_RULE,
        root=ROOT,
    )


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
