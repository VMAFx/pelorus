#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Check that every nosemgrep and nosec marker binds to code and names its rules.

ADR-0241. A suppression marker in a tracked Python file must:

  - sit on the start line of a statement or expression, so it binds to the
    line a scanner reports (Semgrep 1.182.1 ignores a marker on an argument
    continuation or a closing-bracket line of a multi-line finding);
  - name its rules: ``nosemgrep: <id>[, <id>...]`` with each id either a
    rule of the local ``.semgrep.yml`` or a full dotted registry id, and
    ``nosec <Bxxx>[, <Bxxx>...]`` with Bandit test ids;
  - carry a reason after `` - `` (space, hyphen, space).

A bare ``nosemgrep`` suppresses every rule on the line, and the ``nosem``
alias or a marker word in the middle of a comment is refused as well.
Markers inside string literals are text, not comments, and do not count.
A marker in a tracked non-Python, non-Markdown file is refused, since this
check reads Python only.

``--self-test`` plants each refused shape and requires every one to fail,
then scans the tree; ``--self-test-only`` stops after the planted cases, for
hosts without git (the tree scan lists files with ``git ls-files``).
"""

from __future__ import annotations

import ast
import io
import re
import subprocess  # nosec B404 - fixed git argv, no shell, explicit deadline
import sys
import tempfile
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_DEADLINE_S = 60
MARKER_WORD = re.compile(r"\bnos(?:emgrep|em|ec)\b", re.IGNORECASE)
RULE_ID = r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
NOSEMGREP = re.compile(rf"^nosemgrep:[ \t]*({RULE_ID}(?:[ \t]*,[ \t]*{RULE_ID})*)[ \t]+-[ \t]+\S")
NOSEC = re.compile(r"^nosec[ \t]+(B\d{3}(?:[ \t]*,[ \t]*B\d{3})*)[ \t]+-[ \t]+\S")
NON_PYTHON_MARKER = re.compile(r"(?:#|//|/\*|--)[ \t]*nos(?:emgrep|em|ec)\b", re.IGNORECASE)
LOCAL_RULE_ID = re.compile(r"^\s*-\s*id:\s*([A-Za-z0-9_.-]+)\s*$", re.MULTILINE)
# A registry id has the form <language>.<category>...<rule>: at least two dots.
REGISTRY_MIN_DOTS = 2


def local_rule_ids(root: Path) -> frozenset[str]:
    """Rule ids declared in the local .semgrep.yml, empty when it is absent."""
    config = root / ".semgrep.yml"
    if not config.is_file():
        return frozenset()
    return frozenset(LOCAL_RULE_ID.findall(config.read_text(encoding="utf-8")))


def segment_findings(segment: str, local_ids: frozenset[str]) -> list[str]:
    """Check one marker segment, which starts at the marker word."""
    if segment.lower().startswith("nosemgrep"):
        match = NOSEMGREP.match(segment)
        if match is None:
            return ["nosemgrep needs 'nosemgrep: <rule id>[, <rule id>] - <reason>'"]
        ids = [part.strip() for part in match.group(1).split(",")]
        short = [i for i in ids if i not in local_ids and i.count(".") < REGISTRY_MIN_DOTS]
        if short:
            return [f"nosemgrep names no full rule id: {', '.join(short)}"]
        return []
    if segment.lower().startswith("nosec"):
        if NOSEC.match(segment) is None:
            return ["nosec needs 'nosec <Bxxx>[, <Bxxx>] - <reason>'"]
        return []
    return ["nosem alias: write 'nosemgrep: <rule id> - <reason>'"]


def comment_findings(comment: str, local_ids: frozenset[str]) -> list[str]:
    """Check one comment token: each marker word must follow a `#` and parse."""
    found: list[str] = []
    for word in MARKER_WORD.finditer(comment):
        prefix = comment[: word.start()].rstrip(" \t")
        if not prefix.endswith("#"):
            found.append(f"marker word '{word.group(0)}' inside comment text, not at a '#'")
            continue
        end = comment.find("#", word.end())
        segment = comment[word.start() : end if end >= 0 else len(comment)].rstrip()
        found.extend(segment_findings(segment, local_ids))
    return found


def python_findings(name: str, text: str, local_ids: frozenset[str]) -> list[str]:
    """Check every marker comment in Python source against its AST start lines."""
    try:
        tree = ast.parse(text, filename=name)
    except SyntaxError as exc:
        return [f"{name}:{exc.lineno}: not parseable Python: {exc.msg}"]
    starts = {
        node.lineno for node in ast.walk(tree) if isinstance(node, (ast.stmt, ast.expr))
    }
    found: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.COMMENT or MARKER_WORD.search(tok.string) is None:
            continue
        line = tok.start[0]
        problems = comment_findings(tok.string, local_ids)
        if line not in starts:
            problems.insert(0, "marker is not on the start line of a statement or expression")
        found.extend(f"{name}:{line}: {problem}" for problem in problems)
    return found


PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def is_python(path: Path) -> bool:
    """True for a .py or .pyi file or a file whose shebang names python."""
    if path.suffix in PYTHON_SUFFIXES:
        return True
    try:
        with path.open("rb") as handle:
            first = handle.readline(128)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def tracked_files(root: Path) -> list[Path]:
    """Every file git tracks under root."""
    proc = subprocess.run(  # nosec B603 - fixed git argv; list argv, no shell  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is a constant git command, no shell, explicit deadline
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=GIT_DEADLINE_S,
    )
    names = proc.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [root / name for name in names if name]


def file_findings(root: Path, path: Path, local_ids: frozenset[str]) -> list[str]:
    """Check one tracked file; Markdown and non-regular files are not read."""
    name = path.relative_to(root).as_posix()
    if path.suffix == ".md" or not path.is_file():
        return []
    if is_python(path):
        # tokenize.open honours a PEP 263 coding cookie and the UTF-8 BOM; a
        # file that does not decode is a finding, not a crash or a skip.
        try:
            with tokenize.open(path) as handle:
                text = handle.read()
        except (SyntaxError, UnicodeDecodeError) as exc:
            return [f"{name}: not decodable as declared: {exc}"]
        return python_findings(name, text, local_ids)
    # Marker words are ASCII; surrogateescape keeps every other byte, so a
    # file that is not UTF-8 is still scanned instead of skipped.
    text = path.read_bytes().decode("utf-8", "surrogateescape")
    return other_findings(name, text)


def other_findings(name: str, text: str) -> list[str]:
    """Refuse every marker comment in a non-Python file."""
    return [
        f"{name}:{text.count(chr(10), 0, m.start()) + 1}: marker outside Python; "
        "this check binds Python only"
        for m in NON_PYTHON_MARKER.finditer(text)
    ]


def scan(root: Path) -> list[str]:
    """Check every tracked file under root."""
    local_ids = local_rule_ids(root)
    found: list[str] = []
    for path in tracked_files(root):
        found.extend(file_findings(root, path, local_ids))
    return found


VALID = '''\
import subprocess  # nosec B404 - fixed argv, no shell
def run(argv):
    return subprocess.run(  # nosec B603 - list argv  # nosemgrep: python.lang.security.audit.x.x - list argv, no shell
        argv,  # nosemgrep: python.lang.security.audit.y.y, local-rule - argv is fixed
        timeout=5,
    )
TEXT = "# nosemgrep"  # the marker word in a string literal is text
'''

# name -> (source, the substring every finding must contain)
PLANTED = {
    "misplaced on the line before": (
        "def f(x):\n    # nosemgrep: python.lang.a.b - why\n    return run(x)\n",
        "not on the start line",
    ),
    "misplaced on a closing bracket": (
        "run(\n    x,\n)  # nosemgrep: python.lang.a.b - why\n",
        "not on the start line",
    ),
    "bare nosemgrep": ("run(x)  # nosemgrep\n", "nosemgrep needs"),
    "nosemgrep without a reason": ("run(x)  # nosemgrep: python.lang.a.b\n", "nosemgrep needs"),
    "nosemgrep with a short id": ("run(x)  # nosemgrep: a-rule - why\n", "no full rule id"),
    "nosem alias": ("run(x)  # nosem: python.lang.a.b - why\n", "nosem alias"),
    "bare nosec": ("run(x)  # nosec\n", "nosec needs"),
    "nosec without a reason": ("run(x)  # nosec B603\n", "nosec needs"),
    "marker word mid-comment": ("run(x)  # see nosemgrep: python.lang.a.b - why\n", "inside comment"),
}


def encoding_self_test(local_ids: frozenset[str]) -> list[str]:
    """Plant files on disk: declared encodings, undecodable bytes, stubs."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        declared = root / "declared.py"
        declared.write_bytes(
            b"# -*- coding: latin-1 -*-\n"
            b'x = "\xe9"  # nosec B603 - fixed argv\n'
        )
        broken = root / "broken.py"
        broken.write_bytes(b'x = "\xe9"  # nosec B603 - fixed argv\n')
        got = file_findings(root, declared, local_ids)
        if got:
            failures.append(f"declared latin-1 source refused: {got}")
        got = file_findings(root, broken, local_ids)
        if not got or "not decodable" not in got[0]:
            failures.append(f"planted 'undecodable source' not refused: {got}")
        else:
            print(f"refused as expected (undecodable source): {got[0]}")
        latin = root / "notes.sh"
        latin.write_bytes(b"echo \xff  # nosemgrep: python.lang.a.b - why\n")
        got = file_findings(root, latin, local_ids)
        if not got or "outside Python" not in got[0]:
            failures.append(f"planted 'non-UTF-8 non-Python marker' not refused: {got}")
        else:
            print(f"refused as expected (non-UTF-8 non-Python marker): {got[0]}")
        stub = root / "stub.pyi"
        stub.write_text("def f() -> int: ...  # nosemgrep\n", encoding="utf-8")
        got = file_findings(root, stub, local_ids)
        if not got or "nosemgrep needs" not in got[0]:
            failures.append(f"planted 'bare marker in a .pyi stub' not read as Python: {got}")
        else:
            print(f"refused as expected (bare marker in a .pyi stub): {got[0]}")
        stub.write_text(
            "def f() -> int: ...  # nosemgrep: python.lang.a.b - why\n", encoding="utf-8"
        )
        got = file_findings(root, stub, local_ids)
        if got:
            failures.append(f"valid .pyi stub refused: {got}")
    return failures


def self_test() -> int:
    """Plant each refused shape and the valid sources; 0 when all behave."""
    local_ids = frozenset({"local-rule"})
    failures: list[str] = []
    valid = python_findings("valid.py", VALID, local_ids)
    if valid:
        failures.append(f"valid source refused: {valid}")
    for label, (source, expect) in PLANTED.items():
        got = python_findings("planted.py", source, local_ids)
        if not got or not all(expect in finding for finding in got):
            failures.append(f"planted '{label}' not refused as '{expect}': {got}")
        else:
            print(f"refused as expected ({label}): {got[0]}")
    if not other_findings("planted.sh", "run x  # nosemgrep: python.lang.a.b - why\n"):
        failures.append("planted 'marker in a shell file' not refused")
    else:
        print("refused as expected (marker in a shell file)")
    failures.extend(encoding_self_test(local_ids))
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    """Run the self-test and/or the tree scan; exit 1 on a finding."""
    if argv[1:] == ["--self-test-only"]:
        return self_test()
    if argv[1:] == ["--self-test"]:
        status = self_test()
        if status:
            return status
    elif argv[1:]:
        print(
            "usage: check-suppression-markers.py [--self-test | --self-test-only]",
            file=sys.stderr,
        )
        return 2
    found = scan(ROOT)
    for finding in found:
        print(finding, file=sys.stderr)
    if found:
        print(f"{len(found)} suppression marker finding(s) (ADR-0241)", file=sys.stderr)
        return 1
    print("suppression markers: every marker binds to a start line and names its rules")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
