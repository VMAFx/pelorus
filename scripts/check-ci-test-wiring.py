#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Refuse an ffmpeg-patches/test/ program that no CI workflow runs.

A test file counts as wired when an enabled GitHub workflow step names it
outside comments (trailing `#` comments in shell/Python/YAML/Meson, and `//`
or `/* */` comments in C); when a composite action that a wired file `uses:`
names it; when a Meson test() call that a workflow's `meson test` line selects
(by suite, by name, or all tests, with value options and line continuations
handled, and --exclude / --no-suite not selecting) names it or runs an
executable assigned to that variable in definition order before the call in the
same file; or when a wired test file names it (including a helper importing a
Python module by stem via import syntax). Every other `*.sh`, `*.py` or `*.c`
file directly under ffmpeg-patches/test/ is a finding, unless a live
`.standards.yaml` exception with rule HISS-15 names its path.

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
from typing import NamedTuple, TextIO

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from standards_exceptions import filter_excused, load_entries

DESCRIPTION = "Refuse an ffmpeg-patches/test/ program that no CI workflow runs."
RULE = "HISS-15"
TEST_DIR = pathlib.PurePosixPath("ffmpeg-patches/test")
SUFFIXES = (".sh", ".py", ".c")
ROOT_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")
# A Meson file wires a test only through a test() call that a workflow's
# `meson test` line selects (by suite, by name, or all tests), or through the
# executable such a call runs.
MESON_GLOBS = ("meson.build", "libpelorus/**/meson.build", "tools/**/meson.build")
MESON_TEST_LINE = re.compile(r"\bmeson\s+test\b([^\n&;|]*)")
SUITE_ARG = re.compile(r"(?<![\w-])--suite(?:=|\s+)([\w-]+)")
NO_SUITE_ARG = re.compile(r"(?<![\w-])--no-suite(?:=|\s+)([\w-]+)")
MESON_VALUE_OPTIONS = frozenset(
    (
        "-k", "--maxfail", "--repeat", "--gdb-path", "--wrapper", "-C",
        "--suite", "--no-suite", "--exclude", "--logbase", "-j",
        "--num-processes", "-t", "--timeout-multiplier", "--setup",
        "--test-args", "--max-lines", "--slice",
    )
)
# A composite action runs only when a wired file names it in `uses:`.
ACTION_GLOBS = (".github/actions/**/*.yml", ".github/actions/**/*.yaml")
HASH_COMMENT_SUFFIXES = (".sh", ".py", ".yml", ".yaml", ".build")
CALL_PATTERN = re.compile(r"(?:\b(\w+)\s*=\s*)?\b(executable|test)\s*\(")


class MesonLine(NamedTuple):
    suites: set[str]
    names: set[str]
    all: bool
    excluded_suites: set[str]
    excluded_names: set[str]


def _handle_quote_char(ch: str, in_quote: str | None) -> tuple[str | None, int]:
    """Advance quote state: returns (updated_in_quote, step_size)."""
    if in_quote is not None:
        if ch == "\\":
            return in_quote, 2
        return (None if ch == in_quote else in_quote), 1
    if ch in ('"', "'"):
        return ch, 1
    return None, 0


def _skip_c_comment(text: str, i: int, n: int) -> tuple[int, str]:
    """If i points to // or /*, return (next_i, insert_str); else (i, "")."""
    if i + 1 >= n or text[i] != "/":
        return i, ""
    nxt = text[i + 1]
    if nxt == "/":
        nl = text.find("\n", i + 2)
        return (n if nl < 0 else nl), ""
    if nxt == "*":
        end = text.find("*/", i + 2)
        return (n if end < 0 else end + 2), " "
    return i, ""


def strip_c_comments(text: str) -> str:
    """Strip // and /* */ comments in C source outside string/char literals."""
    out: list[str] = []
    i, n, in_quote = 0, len(text), None
    for _ in range(n):
        if i >= n:
            break
        ch = text[i]
        nxt_i, repl = _skip_c_comment(text, i, n) if in_quote is None else (i, "")
        if nxt_i != i:
            if repl:
                out.append(repl)
            i = nxt_i
            continue
        in_quote, adv = _handle_quote_char(ch, in_quote)
        step = max(adv, 1)
        out.append(text[i : i + step])
        i += step
    return "".join(out)


def _strip_line_hash(line: str) -> str:
    """Strip trailing # comment from a line outside single/double quotes."""
    in_quote: str | None = None
    i, n = 0, len(line)
    for _ in range(n):
        if i >= n:
            break
        ch = line[i]
        in_quote, adv = _handle_quote_char(ch, in_quote)
        if adv > 0:
            i += adv
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
        i += 1
    return line


def strip_comment_lines(path: pathlib.Path, text: str) -> str:
    """Drop trailing comments: # in shell/Python/YAML/build; // and /* */ in C."""
    if path.suffix == ".c":
        return strip_c_comments(text)
    if path.suffix in HASH_COMMENT_SUFFIXES:
        return "\n".join(_strip_line_hash(line) for line in text.splitlines())
    return text


def _is_step_disabled(step_lines: list[str]) -> bool:
    """True if step has an `if:` property whose value is literal false or ${{ false }}."""
    for line in step_lines:
        m = re.match(r"^\s*(?:-\s+)?if:\s*(.+)$", line)
        if m:
            val = m.group(1).strip()
            if val in ("false", "'false'", '"false"'):
                return True
            if re.match(r"^\$\{\{\s*false\s*\}\}$", val):
                return True
    return False


def _flush_step(out: list[str], cur_step: list[str]) -> None:
    """Append cur_step to out if it is not statically disabled."""
    if cur_step and not _is_step_disabled(cur_step):
        out.extend(cur_step)


def _append_blank(out: list[str], cur_step: list[str], line: str) -> None:
    """Append blank line to cur_step or out."""
    if cur_step:
        cur_step.append(line)
    else:
        out.append(line)


def _check_steps_start(line: str, indent: int) -> tuple[bool, int]:
    """Check if line opens a steps block."""
    if re.match(r"^\s*steps:\s*$", line):
        return True, indent
    return False, -1


def _handle_step_line(
    line: str, indent: int, cur_indent: int, cur_step: list[str], out: list[str]
) -> tuple[list[str], int, bool]:
    """Process a line inside steps; returns (cur_step, cur_indent, still_in_steps)."""
    if line.lstrip().startswith("- "):
        _flush_step(out, cur_step)
        return [line], indent, True
    if cur_step and indent > cur_indent:
        cur_step.append(line)
        return cur_step, cur_indent, True
    _flush_step(out, cur_step)
    out.append(line)
    return [], -1, False


def drop_disabled_steps(text: str) -> str:
    """Drop list items under `steps:` whose `if:` is literal false or ${{ false }}."""
    out: list[str] = []
    in_steps, steps_indent, cur_indent = False, -1, -1
    cur_step: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            _append_blank(out, cur_step, line)
            continue
        indent = len(line) - len(line.lstrip())
        if in_steps and indent <= steps_indent:
            _flush_step(out, cur_step)
            cur_step, in_steps, steps_indent = [], False, -1
        if not in_steps:
            in_steps, steps_indent = _check_steps_start(line, indent)
            out.append(line)
            continue
        cur_step, cur_indent, in_steps = _handle_step_line(
            line, indent, cur_indent, cur_step, out
        )
    _flush_step(out, cur_step)
    return "\n".join(out)


def read_text(path: pathlib.Path) -> str:
    """Read a file with comments removed and disabled workflow steps dropped."""
    if path.suffix == ".py":
        with tokenize.open(path) as handle:
            raw = handle.read()
    else:
        raw = path.read_text(encoding="utf-8")
    text = strip_comment_lines(path, raw)
    if path.suffix in (".yml", ".yaml"):
        text = drop_disabled_steps(text)
    return text


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


def stem_patterns(stem: str) -> list[re.Pattern[str]]:
    """Import syntax patterns that wire a Python module by stem in test files."""
    escaped = re.escape(stem)
    return [
        re.compile(r"\bimport\s+(?:[\w.]+\s*,\s*)*" + escaped + r"\b"),
        re.compile(r"\bfrom\s+" + escaped + r"\s+import\b"),
        re.compile(r"\bimport_module\s*\(\s*['\"]" + escaped + r"['\"]\s*\)"),
    ]


def node_patterns(
    repo_root: pathlib.Path, node: pathlib.Path
) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    """Return (file_patterns, stem_patterns) that wire a node."""
    rel = node.relative_to(repo_root)
    if rel.parts[:2] == (".github", "actions"):
        return [re.compile(r"\./" + re.escape(rel.parent.as_posix()) + r"(?![\w.-])")], []
    file_pats = [name_pattern(node.name)]
    stem_pats = stem_patterns(node.stem) if node.suffix == ".py" else []
    return file_pats, stem_pats


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
    """Workflow files, sorted and unique."""
    return _glob_files(repo_root, ROOT_GLOBS)


def _record_exclusion(opt: str, val: str, exc_names: set[str], exc_suites: set[str]) -> None:
    """Record excluded test name or suite."""
    if opt == "--exclude":
        exc_names.add(val)
    elif opt == "--no-suite":
        exc_suites.add(val)


def _parse_meson_args(args: str) -> tuple[list[str], set[str], set[str]]:
    """Extract positional test names, excluded test names, and excluded suites."""
    names: list[str] = []
    excluded_names: set[str] = set()
    excluded_suites: set[str] = set()
    skip = False
    skip_opt = ""
    for token in args.split():
        if skip:
            _record_exclusion(skip_opt, token, excluded_names, excluded_suites)
            skip, skip_opt = False, ""
            continue
        opt, has_val, val = token.partition("=")
        if opt in MESON_VALUE_OPTIONS:
            if has_val:
                _record_exclusion(opt, val, excluded_names, excluded_suites)
            else:
                skip, skip_opt = True, opt
        elif not token.startswith("-"):
            names.append(token)
    return names, excluded_names, excluded_suites


def _parse_meson_line(args: str) -> MesonLine:
    """Extract (suites, names, all, excluded suites, excluded names) from one meson test line."""
    suites = set(SUITE_ARG.findall(args))
    pos_names, exc_names, exc_suites = _parse_meson_args(args)
    exc_suites.update(NO_SUITE_ARG.findall(args))
    names = set(pos_names)
    all_tests = not suites and not names
    return MesonLine(suites, names, all_tests, exc_suites, exc_names)


def meson_selection(workflow_texts: list[str]) -> list[MesonLine]:
    """Parse each `meson test` line into one MesonLine record."""
    records: list[MesonLine] = []
    for raw in workflow_texts:
        text = re.sub(r"\\\r?\n[ \t]*", " ", raw)
        for match in MESON_TEST_LINE.finditer(text):
            records.append(_parse_meson_line(match.group(1)))
    return records


def _close_paren(text: str, start: int) -> int:
    """Index of the parenthesis closing the call opened before start; skips strings and comments."""
    depth, i, in_str = 1, start, False
    for _ in range(len(text)):
        if i >= len(text):
            break
        ch = text[i]
        if in_str:
            in_str = ch != "'"
            i += 2 if ch == "\\" else 1
            continue
        if ch == "#":
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline
            continue
        in_str = ch == "'"
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0:
            return i
        i += 1
    return len(text)


def _line_matches(rec: MesonLine, test_name: str, body_suites: set[str]) -> bool:
    """True when a single meson test line selects this test without excluding it."""
    if test_name and test_name in rec.excluded_names:
        return False
    if body_suites and (body_suites & rec.excluded_suites):
        return False
    return rec.all or bool(body_suites & rec.suites) or (bool(test_name) and test_name in rec.names)


def _selected(body: str, selection: list[MesonLine]) -> bool:
    """True when any CI `meson test` line selects this test() call without excluding it."""
    name_m = re.match(r"\s*'([^']*)'", body)
    test_name = name_m.group(1) if name_m else ""
    suite_arg = re.search(r"\bsuite\s*:\s*(\[[^\]]*\]|'[^']*')", body)
    body_suites = set(re.findall(r"'([\w-]+)'", suite_arg.group(1))) if suite_arg else set()
    for rec in selection:
        if _line_matches(rec, test_name, body_suites):
            return True
    return False


def _meson_file_root_text(text: str, selection: list[MesonLine]) -> list[str]:
    """Selected test() calls and their paired executables from one Meson file in definition order."""
    selected_parts: list[str] = []
    latest_exes: dict[str, str] = {}
    for match in CALL_PATTERN.finditer(text):
        var = match.group(1) or ""
        func = match.group(2)
        body = text[match.end() : _close_paren(text, match.end())]
        if func == "executable":
            if var:
                latest_exes[var] = body
        elif func == "test" and _selected(body, selection):
            selected_parts.append(body)
            for exe_var, exe_body in latest_exes.items():
                if re.search(r"\b" + re.escape(exe_var) + r"\b", body):
                    selected_parts.append(exe_body)
    return selected_parts


def meson_root_text(meson_texts: list[str], selection: list[MesonLine]) -> str:
    """The CI-selected test() calls plus the executables they run, as one root text."""
    parts: list[str] = []
    for text in meson_texts:
        parts.extend(_meson_file_root_text(text, selection))
    return "\n".join(parts)


def find_actions(repo_root: pathlib.Path) -> list[pathlib.Path]:
    """Composite-action files; wired only when a wired file `uses:` them."""
    return _glob_files(repo_root, ACTION_GLOBS)


def reach(
    roots: list[str],
    nodes: dict[pathlib.Path, str],
    patterns: dict[pathlib.Path, tuple[list[re.Pattern[str]], list[re.Pattern[str]]]],
    repo_root: pathlib.Path,
) -> set[pathlib.Path]:
    """Nodes named by a root text or by an already reached node's text."""
    pending: list[tuple[bool, str]] = [(False, r) for r in roots]
    wired: set[pathlib.Path] = set()
    total_budget = len(roots) + len(nodes)
    for _ in range(total_budget):
        if not pending:
            break
        is_test, text = pending.pop()
        for node, node_text in nodes.items():
            if node in wired:
                continue
            file_pats, stem_pats = patterns[node]
            hit = any(p.search(text) for p in file_pats)
            if not hit and is_test and stem_pats:
                hit = any(p.search(text) for p in stem_pats)
            if hit:
                wired.add(node)
                is_node_test = (repo_root / TEST_DIR) in node.parents
                pending.append((is_node_test, node_text))
    return wired


def unwired(repo_root: pathlib.Path) -> tuple[list[str], list[str]]:
    """Return (errors, findings); a finding is `<rel>:1: <message>`."""
    roots = find_roots(repo_root)
    if not roots:
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
    workflow_texts = list(read_all(repo_root, roots, errors).values())
    meson_texts = list(read_all(repo_root, _glob_files(repo_root, MESON_GLOBS), errors).values())
    root_texts = [*workflow_texts, meson_root_text(meson_texts, meson_selection(workflow_texts))]
    wired = reach(root_texts, nodes, patterns, repo_root)
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


def _plant(root: pathlib.Path, files: dict[str, str | bytes]) -> None:
    """Write each planted file under root."""
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
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
        "      - run: meson test -C build --suite=fast --print-errorlogs && true\n"
        "      - run: meson test -C build meson-named --verbose\n"
    ),
    "meson.build": (
        "exe_ok = executable('a', files('ffmpeg-patches/test/meson-ok.c'))\n"
        "test('a', exe_ok,  # a ) in a comment\n     suite: 'fast')\n"
        "exe_untested = executable('b', files('ffmpeg-patches/test/meson-untested.c'))\n"
        "exe_slow = executable('c', files('ffmpeg-patches/test/meson-slow.c'))\n"
        "test('c', exe_slow, suite: ['slow', 'x)'])\n"
        "test('meson-named', find_program('sh'), args: ['ffmpeg-patches/test/meson-named.sh'])\n"
        "test('d', python, args: ['ffmpeg-patches/test/meson-script.py'], suite: ['numpy', 'fast'])\n"
    ),
    "ffmpeg-patches/test/meson-ok.c": "int main(void) { return 0; }\n",
    "ffmpeg-patches/test/meson-untested.c": "int main(void) { return 0; }\n",
    "ffmpeg-patches/test/meson-slow.c": "int main(void) { return 0; }\n",
    "ffmpeg-patches/test/meson-named.sh": "true\n",
    "ffmpeg-patches/test/meson-script.py": "X = 1\n",
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
    "ffmpeg-patches/test/meson-untested.c",
    "ffmpeg-patches/test/meson-slow.c",
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
        wide[".github/workflows/w0.yml"] = "run: ffmpeg-patches/test/only.sh\nrun: meson test -C build\n"
        wide["ffmpeg-patches/test/only.sh"] = "true\n"
        wide["meson.build"] = "exe = executable('e', files('ffmpeg-patches/test/any.c'))\ntest('e', exe)\n"
        wide["ffmpeg-patches/test/any.c"] = "int main(void) { return 0; }\n"
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
        _plant(root, UNREADABLE_TREE)
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


def _check_tree_orphans(tree: dict[str, str], want: set[str], label: str) -> list[str]:
    """Check that check() on tree reports exactly want orphans."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _plant(root, tree)
        got = _paths(root, [])
        if got != want:
            return [f"{label}: expected {sorted(want)}, got {sorted(got)}"]
    return []


def _self_test_f1_stem() -> list[str]:
    """F1: Stem pattern must not match plain words in YAML or scripts."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: echo 'run'\n"
            "      - run: ffmpeg-patches/test/runner.sh\n"
        ),
        "ffmpeg-patches/test/runner.sh": "echo run\n",
        "ffmpeg-patches/test/run.py": "X = 1\n",
    }
    return _check_tree_orphans(tree, {"ffmpeg-patches/test/run.py"}, "F1 stem plain word")


def _self_test_f2_comments() -> list[str]:
    """F2: Trailing # comments in YAML/shell and // or /* */ in C must not wire."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: true # ffmpeg-patches/test/orphan-hash.sh\n"
            "      - run: ffmpeg-patches/test/c-runner.c\n"
            "      - run: ffmpeg-patches/test/shell-runner.sh\n"
        ),
        "ffmpeg-patches/test/orphan-hash.sh": "true\n",
        "ffmpeg-patches/test/orphan-c-line.sh": "true\n",
        "ffmpeg-patches/test/orphan-c-block.sh": "true\n",
        "ffmpeg-patches/test/c-runner.c": (
            "// ffmpeg-patches/test/orphan-c-line.sh\n"
            "/* ffmpeg-patches/test/orphan-c-block.sh */\n"
            "int main(void) { return 0; }\n"
        ),
        "ffmpeg-patches/test/shell-runner.sh": (
            "echo $#; echo ${#x}; echo 'https://example.com/a#b'; ffmpeg-patches/test/kept.sh\n"
        ),
        "ffmpeg-patches/test/kept.sh": "true\n",
    }
    want = {
        "ffmpeg-patches/test/orphan-hash.sh",
        "ffmpeg-patches/test/orphan-c-line.sh",
        "ffmpeg-patches/test/orphan-c-block.sh",
    }
    return _check_tree_orphans(tree, want, "F2 comments")


def _self_test_f3_meson_order() -> list[str]:
    """F3: Meson test executable pairing follows definition order per file."""
    tree = {
        ".github/workflows/ci.yml": "jobs:\n  t:\n    steps:\n      - run: meson test -C build --suite=fast\n",
        "meson.build": (
            "exe = executable('fast-exe', files('ffmpeg-patches/test/fast.c'))\n"
            "test('fast-test', exe, suite: 'fast')\n"
            "exe = executable('slow-exe', files('ffmpeg-patches/test/slow.c'))\n"
            "test('slow-test', exe, suite: 'slow')\n"
        ),
        "ffmpeg-patches/test/fast.c": "int main(void) { return 0; }\n",
        "ffmpeg-patches/test/slow.c": "int main(void) { return 0; }\n",
    }
    return _check_tree_orphans(tree, {"ffmpeg-patches/test/slow.c"}, "F3 meson exe order")


def _self_test_f4_continuation() -> list[str]:
    """F4: Backslash line continuation in workflow joins before parsing."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: |\n"
            "          meson test -C build \\\n"
            "            --suite=fast\n"
        ),
        "meson.build": (
            "test('fast-test', find_program('sh'), args: ['ffmpeg-patches/test/f4-fast.sh'], suite: 'fast')\n"
        ),
        "ffmpeg-patches/test/f4-fast.sh": "true\n",
    }
    return _check_tree_orphans(tree, set(), "F4 backslash continuation")


def _self_test_f5_options() -> list[str]:
    """F5: Value-taking meson test options --exclude and --no-suite do not select."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: meson test -C=build --maxfail=1 --repeat=2 --gdb-path=/bin/gdb "
            "--wrapper=w --logbase=l -j=2 --num-processes=2 -t=1 --timeout-multiplier=1 "
            "--setup=s --test-args=a --max-lines=10 --slice=1/2 --exclude f5-excluded --no-suite slow\n"
        ),
        "meson.build": (
            "test('f5-excluded', find_program('sh'), args: ['ffmpeg-patches/test/f5-excluded.sh'])\n"
            "test('slow', find_program('sh'), args: ['ffmpeg-patches/test/f5-slow.sh'], suite: 'slow')\n"
        ),
        "ffmpeg-patches/test/f5-excluded.sh": "true\n",
        "ffmpeg-patches/test/f5-slow.sh": "true\n",
    }
    want = {"ffmpeg-patches/test/f5-excluded.sh", "ffmpeg-patches/test/f5-slow.sh"}
    return _check_tree_orphans(tree, want, "F5 meson value options")


def _self_test_f6_disabled_steps() -> list[str]:
    """F6: Statically disabled workflow steps do not wire tests."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - if: false\n"
            "        run: ffmpeg-patches/test/f6-disabled.sh\n"
            "      - if: ${{ false }}\n"
            "        run: ffmpeg-patches/test/f6-disabled-expr.sh\n"
        ),
        "ffmpeg-patches/test/f6-disabled.sh": "true\n",
        "ffmpeg-patches/test/f6-disabled-expr.sh": "true\n",
    }
    want = {"ffmpeg-patches/test/f6-disabled.sh", "ffmpeg-patches/test/f6-disabled-expr.sh"}
    return _check_tree_orphans(tree, want, "F6 disabled steps")


def _self_test_repro_a() -> list[str]:
    """Repro A: multiple meson test lines; one line's exclusion does not hide another's selection."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: meson test -C build --suite fast --no-suite gpu\n"
            "      - run: meson test -C build --suite gpu\n"
        ),
        "meson.build": (
            "g = executable('g', 'ffmpeg-patches/test/gpu.c')\n"
            "test('g', g, suite: 'gpu')\n"
        ),
        "ffmpeg-patches/test/gpu.c": "int main(void) { return 0; }\n",
    }
    return _check_tree_orphans(tree, set(), "Repro A line exclusion independence")


def _self_test_repro_b() -> list[str]:
    """Repro B: multiple meson test lines; one line's --exclude does not hide another's named selection."""
    tree = {
        ".github/workflows/ci.yml": (
            "jobs:\n  t:\n    steps:\n"
            "      - run: meson test -C build --exclude g\n"
            "      - run: meson test -C build g\n"
        ),
        "meson.build": (
            "g = executable('g', 'ffmpeg-patches/test/gpu.c')\n"
            "test('g', g)\n"
        ),
        "ffmpeg-patches/test/gpu.c": "int main(void) { return 0; }\n",
    }
    return _check_tree_orphans(tree, set(), "Repro B name exclusion independence")


def run_self_test() -> int:
    """Run the planted cases; print each failure."""
    failures = (
        _self_test_deep()
        + _self_test_wide()
        + _self_test_unreadable()
        + _self_test_f1_stem()
        + _self_test_f2_comments()
        + _self_test_f3_meson_order()
        + _self_test_f4_continuation()
        + _self_test_f5_options()
        + _self_test_f6_disabled_steps()
        + _self_test_repro_a()
        + _self_test_repro_b()
    )
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
