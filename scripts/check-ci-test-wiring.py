#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Refuse an ffmpeg-patches/test/ program that no CI workflow runs.

A test file counts as wired when a GitHub workflow, a composite action or a
Meson build file (CI runs `meson test`) names it outside a comment, or when a wired test file names it (a runner script that
calls a helper, a helper that imports a module by its stem). Every other
`*.sh`, `*.py` or `*.c` file directly under ffmpeg-patches/test/ is a finding,
unless a live `.standards.yaml` exception with rule HISS-15 names its path.

The class this stops: `qsv-roi-regression.sh` shipped as the regression test
of 28e720a, and no workflow ever ran it.

Usage:
    python3 -I scripts/check-ci-test-wiring.py
    python3 -I scripts/check-ci-test-wiring.py --self-test
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import io
import pathlib
import re
import sys
import tempfile
import tokenize
from typing import TextIO

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from standards_exceptions import filter_excused, load_entries

DESCRIPTION = "Refuse an ffmpeg-patches/test/ program that no CI workflow runs."
RULE = "HISS-15"
TEST_DIR = pathlib.PurePosixPath("ffmpeg-patches/test")
SUFFIXES = (".sh", ".py", ".c")
# CI runs the workflows; its core job runs `meson test --suite=fast`, so the
# Meson build files that register tests are roots as well.
ROOT_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "meson.build",
    "libpelorus/**/meson.build",
    "tools/**/meson.build",
)
# A composite action runs only when a wired file names it in `uses:`.
ACTION_GLOBS = (".github/actions/**/*.yml", ".github/actions/**/*.yaml")
HASH_COMMENT_SUFFIXES = (".sh", ".py", ".yml", ".yaml", ".build")


def strip_comment_lines(path: pathlib.Path, text: str) -> str:
    """Drop whole-line `#` comments of shell, Python and YAML text; C is kept."""
    if path.suffix not in HASH_COMMENT_SUFFIXES:
        return text
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def read_text(path: pathlib.Path) -> str:
    """Read a file with comment lines removed; Python honours its coding cookie."""
    if path.suffix == ".py":
        with tokenize.open(path) as handle:
            return strip_comment_lines(path, handle.read())
    return strip_comment_lines(path, path.read_text(encoding="utf-8"))


def read_all(repo_root: pathlib.Path, paths: list[pathlib.Path], errors: list[str]) -> dict[pathlib.Path, str]:
    """Read every path; an unreadable one adds an error and reads as empty text."""
    texts: dict[pathlib.Path, str] = {}
    for path in paths:
        try:
            texts[path] = read_text(path)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            errors.append(f"{path.relative_to(repo_root).as_posix()}:1: cannot read: {exc}")
            texts[path] = ""
    return texts


def name_pattern(name: str) -> re.Pattern[str]:
    """Match a file name as a whole token, not inside a longer name or `name.bak`."""
    return re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.\w)")


def node_patterns(repo_root: pathlib.Path, node: pathlib.Path) -> list[re.Pattern[str]]:
    """Patterns that wire a node: a composite action by its `uses:` directory,
    a test by file name, a Python module also by stem."""
    rel = node.relative_to(repo_root)
    if rel.parts[:2] == (".github", "actions"):
        return [re.compile(r"\./" + re.escape(rel.parent.as_posix()) + r"(?![\w.-])")]
    patterns = [name_pattern(node.name)]
    if node.suffix == ".py":
        patterns.append(name_pattern(node.stem))
    return patterns


def find_candidates(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Test programs directly under ffmpeg-patches/test/, sorted."""
    test_dir = repo_root / TEST_DIR
    return sorted(p for p in test_dir.iterdir() if p.is_file() and p.suffix in SUFFIXES)


def _glob_files(repo_root: pathlib.Path, patterns: tuple[str, ...]) -> list[pathlib.Path]:
    """Files matching any pattern, sorted and unique."""
    found: set[pathlib.Path] = set()
    for pattern in patterns:
        found.update(p for p in repo_root.glob(pattern) if p.is_file())
    return sorted(found)


def find_roots(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Workflow and Meson build files, sorted and unique."""
    return _glob_files(repo_root, ROOT_GLOBS)


def find_actions(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Composite-action files; wired only when a wired file `uses:` them."""
    return _glob_files(repo_root, ACTION_GLOBS)


def reach(
    roots: list[str],
    nodes: dict[pathlib.Path, str],
    patterns: dict[pathlib.Path, list[re.Pattern[str]]],
) -> set[pathlib.Path]:
    """Nodes named by a root text or by an already reached node's text."""
    pending = list(roots)
    wired: set[pathlib.Path] = set()
    # Each root and each newly wired node enters `pending` once.
    for _ in range(len(roots) + len(nodes)):
        if not pending:
            break
        text = pending.pop()
        for node, node_text in nodes.items():
            if node not in wired and any(p.search(text) for p in patterns[node]):
                wired.add(node)
                pending.append(node_text)
    return wired


def unwired(repo_root: pathlib.Path) -> tuple[list[str], list[str]]:
    """Return (errors, findings); a finding is `<rel>:1: <message>`."""
    roots = find_roots(repo_root)
    if not any(r.match(".github/workflows/*") for r in roots):
        return [f"no workflow files under {repo_root / '.github'}"], []
    if not (repo_root / TEST_DIR).is_dir():
        return [f"missing test directory {repo_root / TEST_DIR}"], []
    try:
        candidates = find_candidates(repo_root)
    except OSError as exc:
        return [f"cannot list {repo_root / TEST_DIR}: {exc}"], []
    errors: list[str] = []
    nodes = read_all(repo_root, candidates + find_actions(repo_root), errors)
    patterns = {n: node_patterns(repo_root, n) for n in nodes}
    wired = reach(list(read_all(repo_root, roots, errors).values()), nodes, patterns)
    findings = [
        f"{c.relative_to(repo_root).as_posix()}:1: no CI workflow runs this test, directly or through a wired test file"
        for c in candidates
        if c not in wired
    ]
    return errors, findings


def check(
    repo_root: pathlib.Path,
    entries: list[dict[str, str]] | None = None,
    today: date | None = None,
    out: TextIO = sys.stdout,
) -> list[str]:
    """Errors plus findings that no live exception excuses."""
    errors, findings = unwired(repo_root)
    if entries is None:
        entries = load_entries(repo_root)
    if today is None:
        today = date.today()
    return errors + filter_excused(findings, entries, RULE, today, out=out)


def _plant(root: pathlib.Path, files: dict[str, str]) -> None:
    """Write each planted file under root."""
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


REF_TODAY = date(2026, 11, 5)
ORPHAN = "ffmpeg-patches/test/orphan.sh"
DEEP_TREE = {
    ".github/workflows/ci.yml": (
        "jobs:\n  t:\n    steps:\n"
        "      - run: ffmpeg-patches/test/runner.sh\n"
        "      # - run: ffmpeg-patches/test/commented.sh\n"
        "      - run: ffmpeg-patches/test/runner-extra.sh.bak\n"
        "      - uses: ./.github/actions/used\n"
    ),
    ".github/actions/used/action.yml": "runs:\n  steps:\n    - run: ffmpeg-patches/test/by-used-action.sh\n",
    ".github/actions/unused/action.yml": "runs:\n  steps:\n    - run: ffmpeg-patches/test/by-unused-action.sh\n",
    "ffmpeg-patches/test/by-used-action.sh": "true\n",
    "ffmpeg-patches/test/by-unused-action.sh": "true\n",
    "ffmpeg-patches/test/runner.sh": 'python3 "$HERE/helper.py"\ninstall "$HERE/unit.c" x\n',
    "ffmpeg-patches/test/helper.py": 'mod = import_module("module-by-stem")\n',
    "ffmpeg-patches/test/module-by-stem.py": "X = 1\n",
    "ffmpeg-patches/test/unit.c": "int main(void) { return 0; }\n",
    "ffmpeg-patches/test/commented.sh": "true\n",
    ORPHAN: "bash leaf.sh\n",
    "ffmpeg-patches/test/leaf.sh": "true\n",
    "ffmpeg-patches/test/runner-extra.sh": "true\n",
    "ffmpeg-patches/test/notes.txt": "orphan.sh\n",
}
DEEP_ORPHANS = {
    "ffmpeg-patches/test/commented.sh",
    ORPHAN,
    "ffmpeg-patches/test/leaf.sh",
    "ffmpeg-patches/test/runner-extra.sh",
    "ffmpeg-patches/test/by-unused-action.sh",
}


def _paths(root: pathlib.Path, entries: list[dict[str, str]]) -> set[str]:
    """Paths of the problems check() reports for the planted tree."""
    return {f.split(":", 1)[0] for f in check(root, entries=entries, today=REF_TODAY, out=io.StringIO())}


def _self_test_deep() -> list[str]:
    """Wired, transitive, commented and orphaned programs; exception matching."""
    failures: list[str] = []
    future = (REF_TODAY + timedelta(days=3)).isoformat()
    past = (REF_TODAY - timedelta(days=1)).isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _plant(root, DEEP_TREE)
        got = _paths(root, [])
        if got != DEEP_ORPHANS:
            failures.append(f"orphans: expected {sorted(DEEP_ORPHANS)}, got {sorted(got)}")
        got = _paths(root, [{"rule": RULE, "path": ORPHAN, "expires": future}])
        if got != DEEP_ORPHANS - {ORPHAN}:
            failures.append(f"live exception not applied: {sorted(got)}")
        for name, entries in (
            ("expired", [{"rule": RULE, "path": ORPHAN, "expires": past}]),
            ("other rule", [{"rule": "HISS-07", "path": ORPHAN, "expires": future}]),
        ):
            got = _paths(root, entries)
            if got != DEEP_ORPHANS:
                failures.append(f"{name} exception excused a finding: {sorted(got)}")
    return failures


def _self_test_wide() -> list[str]:
    """More roots than candidates, the naming root read last; then no roots."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        wide = {f".github/workflows/w{i}.yml": "jobs: {}\n" for i in range(1, 6)}
        wide[".github/workflows/w0.yml"] = "run: ffmpeg-patches/test/only.sh\n"
        wide["ffmpeg-patches/test/only.sh"] = "true\n"
        _plant(root, wide)
        got = check(root, entries=[], today=REF_TODAY, out=io.StringIO())
        if got:
            failures.append(f"many roots: wired test reported: {got}")
        for rel in wide:
            if rel.startswith(".github/"):
                (root / rel).unlink()
        if not any("no workflow files" in f for f in check(root, entries=[], today=REF_TODAY, out=io.StringIO())):
            failures.append("a tree without workflows passed")
    return failures


UNREADABLE_TREE = {
    ".github/workflows/ci.yml": "run: bad.sh bogus-cookie.py latin1.py good.sh\n",
    "ffmpeg-patches/test/bad.sh": b"echo \xff\xfe\n",
    "ffmpeg-patches/test/bogus-cookie.py": b"# -*- coding: no-such-codec -*-\nX = 1\n",
    "ffmpeg-patches/test/latin1.py": b"# -*- coding: latin-1 -*-\nX = '\xe9'\n",
    "ffmpeg-patches/test/good.sh": b"true\n",
    "ffmpeg-patches/test/zz-orphan.sh": b"true\n",
}


def _self_test_unreadable() -> list[str]:
    """Undecodable files are errors with their path; the scan goes on."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for rel, body in UNREADABLE_TREE.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, bytes):
                path.write_bytes(body)
            else:
                path.write_text(body, encoding="utf-8")
        got = check(root, entries=[], today=REF_TODAY, out=io.StringIO())
        unread = {f.split(":", 1)[0] for f in got if "cannot read" in f}
        want = {"ffmpeg-patches/test/bad.sh", "ffmpeg-patches/test/bogus-cookie.py"}
        if unread != want:
            failures.append(f"unreadable: expected {sorted(want)}, got {got}")
        if not any(f.startswith("ffmpeg-patches/test/zz-orphan.sh:1: no CI") for f in got):
            failures.append(f"scan stopped after an unreadable file: {got}")
        if len(got) != 3:
            failures.append(f"unreadable tree: expected 3 problems, got {got}")
    return failures


def run_self_test() -> int:
    """Run the planted cases; print each failure."""
    failures = _self_test_deep() + _self_test_wide() + _self_test_unreadable()
    for failure in failures:
        print(f"FAIL self-test: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("check-ci-test-wiring: self-test PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Check the repository, or run the self-test; exit 1 on any problem."""
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--self-test", action="store_true", help="run planted cases and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    problems = check(repo_root)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"check-ci-test-wiring: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("check-ci-test-wiring: every ffmpeg-patches/test program is run by CI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
