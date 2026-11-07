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
check reads Python only. UTF-16 non-Python files are decoded before scanning.

``--self-test`` plants each refused shape and requires every one to fail,
then scans the tree; ``--self-test-only`` stops after the planted cases, for
hosts without git (the tree scan lists files with ``git ls-files``).
"""

from __future__ import annotations

import argparse
import ast
from datetime import date, timedelta
import io
import os
from pathlib import Path
import re
import sys
import tempfile
import tokenize
from typing import TextIO

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from standards_exceptions import filter_excused, load_entries
from tracked_python import is_python, tracked_files

ROOT = Path(__file__).resolve().parents[1]
MARKER_WORD = re.compile(r"\bnos(?:emgrep|em|ec)\b", re.IGNORECASE)
RULE_ID = r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
NOSEMGREP = re.compile(rf"^nosemgrep:[ \t]*({RULE_ID}(?:[ \t]*,[ \t]*{RULE_ID})*)[ \t]+-[ \t]+\S")
NOSEC = re.compile(r"^nosec[ \t]+(B\d{3}(?:[ \t]*,[ \t]*B\d{3})*)[ \t]+-[ \t]+\S")
NON_PYTHON_MARKER = re.compile(
    r"(?:#|//|/\*|--)"
    r"(?:[ \t]*nos(?:emgrep|em|ec)\b"       # immediately after delimiter
    r"|[^\n]*?\bnos(?:emgrep|em|ec)\s*:)",   # later in line, only with colon
    re.IGNORECASE,
)
LOCAL_RULE_ID = re.compile(r"^\s*-\s*id:\s*([A-Za-z0-9_.-]+)\s*$", re.MULTILINE)
# A registry id has the form <language>.<category>...<rule>: at least two dots.
REGISTRY_MIN_DOTS = 2
# A GitLab SAST rule id has the form <language>_<category>_rule-<name>, with no dots.
# Codacy's Opengrep engine reports these rules (for example
# python_exec_rule-start-process-with-no-shell) and honours a marker naming them.
GITLAB_SAST_RULE_ID = re.compile(
    r"^[a-z0-9]+_[a-z0-9]+(?:-[a-z0-9]+)*_rule-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
)


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
        short = [
            i
            for i in ids
            if i not in local_ids
            and i.count(".") < REGISTRY_MIN_DOTS
            and GITLAB_SAST_RULE_ID.match(i) is None
        ]
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


def parse_error(name: str, text: str) -> str | None:
    """Return the parse diagnostic for Python source, or None when it parses."""
    try:
        ast.parse(text, filename=name)
    except SyntaxError as exc:
        return f"{name}:{exc.lineno}: not parseable Python: {exc.msg}"
    return None


def python_findings(name: str, text: str, local_ids: frozenset[str]) -> list[str]:
    """Check every marker comment in Python source against its AST start lines."""
    error = parse_error(name, text)
    if error is not None:
        return [error]
    tree = ast.parse(text, filename=name)
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




def readings(data: bytes) -> list[str]:
    """Return UTF-8 and UTF-16 text readings of non-Python bytes.

    No heuristic is used: UTF-8 and both little-endian and big-endian UTF-16
    at byte offsets 0 and 1 are all scanned, so neither the NUL count nor a
    stray byte can hide a marker. Both orders are needed because a trimmed
    odd tail drops the last character of the other order, so a marker at the
    end of a file needs its own byte order.
    """
    texts = [data.decode("utf-8", "surrogateescape")]
    for enc in ("utf-16-le", "utf-16-be"):
        for offset in (0, 1):
            chunk = data[offset:]
            chunk = chunk[: len(chunk) - len(chunk) % 2]
            texts.append(chunk.decode(enc, "replace"))
    return texts


def file_findings(root: Path, path: Path, local_ids: frozenset[str]) -> tuple[list[str], list[str]]:
    """Check one tracked file; Markdown and non-regular files are not read.

    Non-Python files are scanned across UTF-8 and UTF-16 readings.
    Unreadable files are findings. Returns (diagnostics, rule_findings).
    """
    name = path.relative_to(root).as_posix()
    if path.suffix == ".md" or not path.is_file():
        return [], []
    if is_python(path):
        # tokenize.open honours a PEP 263 coding cookie and the UTF-8 BOM; a
        # file that does not decode is a finding, not a crash or a skip.
        try:
            with tokenize.open(path) as handle:
                text = handle.read()
        except OSError as exc:
            return [f"{name}: not readable: {exc}"], []
        except (SyntaxError, UnicodeDecodeError) as exc:
            return [f"{name}: not decodable as declared: {exc}"], []
        # A parse error is a diagnostic: exceptions never excuse it.
        error = parse_error(name, text)
        if error is not None:
            return [error], []
        return [], python_findings(name, text, local_ids)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{name}: not readable: {exc}"], []
    findings: list[str] = []
    seen: set[str] = set()
    for text in readings(raw):
        for item in other_findings(name, text):
            if item not in seen:
                seen.add(item)
                findings.append(item)
    return [], findings


def other_findings(name: str, text: str) -> list[str]:
    """Refuse every marker comment in a non-Python file."""
    return [
        f"{name}:{text.count(chr(10), 0, m.start()) + 1}: marker outside Python; "
        "this check binds Python only"
        for m in NON_PYTHON_MARKER.finditer(text)
    ]



def scan(
    root: Path,
    entries: list[dict[str, str]] | None = None,
    today: date | None = None,
    paths: list[Path] | None = None,
    out: TextIO = sys.stdout,
) -> list[str]:
    """Check every tracked file under root, dropping excused findings."""
    local_ids = local_rule_ids(root)
    diagnostics: list[str] = []
    findings: list[str] = []
    for path in (paths if paths is not None else tracked_files(root)):
        file_diags, file_issues = file_findings(root, path, local_ids)
        diagnostics.extend(file_diags)
        findings.extend(file_issues)
    if entries is None:
        entries = load_entries(root)
    if today is None:
        today = date.today()
    return diagnostics + filter_excused(findings, entries, "HISS-08", today, out=out)


VALID = '''\
import subprocess  # nosec B404 - fixed argv, no shell
def run(argv):
    return subprocess.run(  # nosec B603 - list argv  # nosemgrep: python.lang.security.audit.x.x - list argv, no shell
        argv,  # nosemgrep: python.lang.security.audit.y.y, local-rule, python_exec_rule-start-process-with-no-shell, python_crypto_rule-HTTPConnectionPool, python_file-permissions_rule-general-bad-permission - argv is fixed
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
    "nosemgrep with a GitLab-like id missing its rule part": (
        "run(x)  # nosemgrep: python_exec_start-process - why\n",
        "no full rule id",
    ),
    "nosem alias": ("run(x)  # nosem: python.lang.a.b - why\n", "nosem alias"),
    "bare nosec": ("run(x)  # nosec\n", "nosec needs"),
    "nosec without a reason": ("run(x)  # nosec B603\n", "nosec needs"),
    "marker word mid-comment": ("run(x)  # see nosemgrep: python.lang.a.b - why\n", "inside comment"),
}


# UTF-16 shapes that must be refused: byte-order mark or none, odd length,
# a stray leading byte, both byte orders, and a marker at the last byte.
UTF16_PLANTS = (
    (
        "ps.ps1",
        "utf-16 file",
        "x  # nosemgrep: python.lang.a.b - why\n".encode("utf-16"),
    ),
    (
        "ps_nobom.ps1",
        "utf-16 file, no BOM",
        "x  # nosemgrep: python.lang.a.b - why\n".encode("utf-16-le"),
    ),
    (
        "ps_odd.ps1",
        "utf-16 file, odd length",
        "x  # nosemgrep: python.lang.a.b - why\n".encode("utf-16") + b"\x00",
    ),
    (
        "mixed.sh",
        "utf-16-looking file with an ASCII marker",
        b"A\x00" * 64 + b"  # nosemgrep: a.b.c - why\n",
    ),
    (
        "cjk.ps1",
        "BOM-less UTF-16 with few NUL bytes",
        ("# " + "漢" * 2048 + "\n# nosemgrep: a.b.c - why\n").encode("utf-16-le"),
    ),
    (
        "shifted.ps1",
        "UTF-16 after one stray byte",
        b"\x01" + "# nosemgrep: a.b.c - why\n".encode("utf-16-le"),
    ),
    (
        "be.ps1",
        "BOM-less UTF-16 big-endian",
        "# nosemgrep: a.b.c - why\n".encode("utf-16-be"),
    ),
    (
        "be_eof.ps1",
        "big-endian UTF-16 ending at the marker",
        "# nosemgrep".encode("utf-16-be"),
    ),
    (
        "be_shift_eof.ps1",
        "big-endian UTF-16 after a stray byte, ending at the marker",
        b"\x01" + "# nosemgrep".encode("utf-16-be"),
    ),
    (
        "le_eof.ps1",
        "little-endian UTF-16 ending at the marker",
        "# nosemgrep".encode("utf-16-le"),
    ),
    (
        "le_shift_eof.ps1",
        "little-endian UTF-16 after a stray byte, ending at the marker",
        b"\x01" + "# nosemgrep".encode("utf-16-le"),
    ),
)


# Files without a marker; no reading may report one.
UTF16_CLEAN = (
    ("clean.sh", b"echo 1\n"),
    ("clean.ps1", "Write-Output 1\n".encode("utf-16")),
    ("clean_cjk.ps1", ("# " + "漢" * 100 + "\n").encode("utf-16")),
)


def utf16_self_test(root: Path, local_ids: frozenset[str]) -> list[str]:
    """Plant UTF16_PLANTS (each must be refused) and UTF16_CLEAN (none may be)."""
    failures: list[str] = []
    for name, label, payload in UTF16_PLANTS:
        path = root / name
        path.write_bytes(payload)
        try:
            diags, got = file_findings(root, path, local_ids)
        except (UnicodeError, OSError) as exc:
            failures.append(f"planted '{label}' raised {exc!r}")
        else:
            all_findings = diags + got
            if not all_findings or "outside Python" not in all_findings[0]:
                failures.append(f"planted '{label}' not refused: {all_findings}")
            else:
                print(f"refused as expected ({label}): {all_findings[0]}")
    for name, payload in UTF16_CLEAN:
        path = root / name
        path.write_bytes(payload)
        diags, got = file_findings(root, path, local_ids)
        if all_findings := (diags + got):
            failures.append(f"clean '{name}' unexpectedly refused: {all_findings}")
    return failures


def unreadable_self_test(root: Path, local_ids: frozenset[str]) -> list[str]:
    """Plant an unreadable file; reading it must be reported as a finding."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("skipped (unreadable file): running as root")
        return []
    locked = root / "locked.sh"
    locked.write_text("echo 1  # nosemgrep: a.b.c - why\n", encoding="utf-8")
    locked.chmod(0)
    failures: list[str] = []
    try:
        try:
            with locked.open("rb"):
                pass
        except OSError:
            # Mode-0 file is unreadable as expected; proceed to verify checker detection.
            pass
        else:
            # Windows keeps a mode-0 file readable, so there is no plant.
            print("skipped (unreadable file): mode 0 leaves the file readable here")
            return failures
        try:
            diags, got = file_findings(root, locked, local_ids)
        except OSError as exc:
            failures.append(f"planted 'unreadable file' raised {exc!r}")
        else:
            all_findings = diags + got
            if len(all_findings) != 1 or "not readable" not in all_findings[0]:
                failures.append(f"planted 'unreadable file' not refused: {all_findings}")
            else:
                print(f"refused as expected (unreadable file): {all_findings[0]}")
    finally:
        locked.chmod(0o600)
    return failures


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
        diags, got = file_findings(root, declared, local_ids)
        if diags or got:
            failures.append(f"declared latin-1 source refused: {diags + got}")
        diags, got = file_findings(root, broken, local_ids)
        all_findings = diags + got
        if not all_findings or "not decodable" not in all_findings[0]:
            failures.append(f"planted 'undecodable source' not refused: {all_findings}")
        else:
            print(f"refused as expected (undecodable source): {all_findings[0]}")
        latin = root / "notes.sh"
        latin.write_bytes(b"echo \xff  # nosemgrep: python.lang.a.b - why\n")
        diags, got = file_findings(root, latin, local_ids)
        all_findings = diags + got
        if not all_findings or "outside Python" not in all_findings[0]:
            failures.append(f"planted 'non-UTF-8 non-Python marker' not refused: {all_findings}")
        else:
            print(f"refused as expected (non-UTF-8 non-Python marker): {all_findings[0]}")
        stub = root / "stub.pyi"
        stub.write_text("def f() -> int: ...  # nosemgrep\n", encoding="utf-8")
        diags, got = file_findings(root, stub, local_ids)
        all_findings = diags + got
        if not all_findings or "nosemgrep needs" not in all_findings[0]:
            failures.append(f"planted 'bare marker in a .pyi stub' not read as Python: {all_findings}")
        else:
            print(f"refused as expected (bare marker in a .pyi stub): {all_findings[0]}")
        stub.write_text(
            "def f() -> int: ...  # nosemgrep: python.lang.a.b - why\n", encoding="utf-8"
        )
        diags, got = file_findings(root, stub, local_ids)
        if diags or got:
            failures.append(f"valid .pyi stub refused: {diags + got}")
        failures.extend(utf16_self_test(root, local_ids))
        failures.extend(unreadable_self_test(root, local_ids))
    return failures


def diagnostic_retained(file_name: str, content: bytes, expect: str, today: date, expires: str) -> bool:
    """Scan one planted file under a live HISS-08 exception; True when the diagnostic survives."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        planted = tmp_root / file_name
        planted.write_bytes(content)
        entries = [{"rule": "HISS-08", "path": file_name, "reason": "category B, 3 days: test", "expires": expires}]
        retained = scan(tmp_root, entries=entries, today=today, paths=[planted], out=io.StringIO())
    return any(expect in item for item in retained)


def exceptions_self_test() -> list[str]:
    """Verify live, missing, expired, and mismatched exceptions on planted findings."""
    failures: list[str] = []
    fixture_path = "fixture/target.py"
    finding = f"{fixture_path}:10: marker is not on the start line of a statement or expression"
    ref_today = date(2026, 11, 4)
    yesterday = (ref_today - timedelta(days=1)).isoformat()
    future = (ref_today + timedelta(days=3)).isoformat()

    # Case 1: live fixture entry -> excused
    live_entries = [
        {"rule": "HISS-08", "path": fixture_path, "reason": "category B, 3 days: test", "expires": future}
    ]
    buf = io.StringIO()
    if filter_excused([finding], live_entries, "HISS-08", ref_today, out=buf):
        failures.append("live exception was not excused")
    expected_msg = f"{fixture_path}: excused by .standards.yaml exception HISS-08 until {future}\n"
    if buf.getvalue() != expected_msg:
        failures.append(f"live exception output mismatch: {buf.getvalue()!r} != {expected_msg!r}")

    # Case 2: same finding, no entry -> refused
    if filter_excused([finding], [], "HISS-08", ref_today, out=io.StringIO()) != [finding]:
        failures.append("finding without exception was not refused")

    # Case 3: same finding, entry expired -> refused
    expired_entries = [
        {"rule": "HISS-08", "path": fixture_path, "reason": "category B, 3 days: test", "expires": yesterday}
    ]
    if filter_excused([finding], expired_entries, "HISS-08", ref_today, out=io.StringIO()) != [finding]:
        failures.append("expired exception was not refused")

    # Case 4: entry for the other rule id -> refused
    other_entries = [
        {"rule": "HISS-07", "path": fixture_path, "reason": "category B, 3 days: test", "expires": future}
    ]
    if filter_excused([finding], other_entries, "HISS-08", ref_today, out=io.StringIO()) != [finding]:
        failures.append("mismatched rule exception was not refused")

    # Cases 5 and 6: a read or parse diagnostic outlives a live exception
    for file_name, content, expect in (
        ("undecodable.py", b"\xff\xfe\x00bad", "not decodable"),
        ("broken.py", b"def f(:\n    pass\n", "not parseable"),
    ):
        if not diagnostic_retained(file_name, content, expect, ref_today, future):
            failures.append(f"'{expect}' diagnostic of {file_name} with live exception was silently excused")

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
    if not other_findings("planted.c", "// explanation nosemgrep: rule.id - reason\n"):
        failures.append("planted 'late marker in a C comment' not refused")
    else:
        print("refused as expected (late marker in a C comment)")
    failures.extend(encoding_self_test(local_ids))
    failures.extend(exceptions_self_test())
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for check-suppression-markers."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-test", action="store_true", help="run self-test before tree scan")
    group.add_argument("--self-test-only", action="store_true", help="run self-test only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the self-test and/or the tree scan; exit 1 on a finding."""
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    if args.self_test_only:
        return self_test()
    if args.self_test:
        status = self_test()
        if status:
            return status
    try:
        found = scan(ROOT)
    except RuntimeError as exc:
        print(f"check-suppression-markers: {exc}", file=sys.stderr)
        return 2
    for finding in found:
        print(finding, file=sys.stderr)
    if found:
        print(f"{len(found)} suppression marker finding(s) (ADR-0241)", file=sys.stderr)
        return 1
    print("suppression markers: every marker binds to a start line and names its rules")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
