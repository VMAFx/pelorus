#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Bump the Pelorus interop side-data ABI minor version (issue #502).

Verifies consistency across interop.h and AGENTS.md, bumps the minor version
number, updates the MINOR history block, and prints required manual follow-ups.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess  # nosec B404 - fixed python argv, no shell, explicit timeout
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_TESTER = REPO_ROOT / "tools" / "tester"
if str(_TOOLS_TESTER) not in sys.path:
    sys.path.insert(0, str(_TOOLS_TESTER))
from pelorus_interop_consts import find_interop_h, parse_interop_text  # noqa: E402

_ABI_MINOR_RE = re.compile(r"#define\s+PELORUS_ABI_MINOR\s+(\d+)[uU]?\b")
_HISTORY_BLOCK_RE = re.compile(r"/\*.*?\bMINOR history\b.*?\*/", re.DOTALL)
_HISTORY_ENTRY_RE = re.compile(r"^\s*\*\s+(\d+\.\d+)\b", re.MULTILINE)
_AGENTS_ABI_RE = re.compile(r"interop ABI (\d+\.\d+)")
_AGENTS_MINOR_RE = re.compile(r"`PELORUS_ABI_MINOR`\s+(\d+)")


def _expect(condition: bool, message: str) -> None:
    """Helper raising AssertionError when condition is false (Bandit B101)."""
    if not condition:
        raise AssertionError(message)


def _find_minor(text: str) -> tuple[int, int]:
    """Parse PELORUS_ABI_MINOR value and line number from header text."""
    match = _ABI_MINOR_RE.search(text)
    if match is None:
        raise ValueError("missing definition: PELORUS_ABI_MINOR")
    line_no = text[:match.start()].count("\n") + 1
    return int(match.group(1)), line_no


def _find_history_entries(text: str) -> tuple[re.Match[str], list[tuple[str, int]]]:
    """Find MINOR history comment block and list of (version_str, line_no)."""
    block_match = _HISTORY_BLOCK_RE.search(text)
    if block_match is None:
        raise ValueError("missing MINOR history comment block in interop.h")
    entries: list[tuple[str, int]] = []
    block_text = block_match.group(0)
    block_start = block_match.start()
    for m in _HISTORY_ENTRY_RE.finditer(block_text):
        entry_ver = m.group(1)
        line_no = text[:block_start + m.start()].count("\n") + 1
        entries.append((entry_ver, line_no))
    return block_match, entries


def _check_interop(root: Path) -> tuple[int, int, list[str]]:
    """Check interop.h consistency, returning (major, minor, errors)."""
    try:
        header_path = find_interop_h(root / "tools" / "tester")
    except ValueError as exc:
        return 0, 0, [f"interop.h: {exc}"]
    try:
        text = header_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return 0, 0, [f"{header_path}: failed to read: {exc}"]
    rel_path = str(header_path.relative_to(root))
    try:
        _, major, _ = parse_interop_text(text)
        minor, _ = _find_minor(text)
        _, entries = _find_history_entries(text)
    except ValueError as exc:
        return 0, 0, [f"{rel_path}: {exc}"]
    errors: list[str] = []
    expected_ver = f"1.{minor}"
    if not entries:
        errors.append(f"{rel_path}:1: expected {expected_ver}, found no history entries")
    else:
        last_ver, line_no = entries[-1]
        if last_ver != expected_ver:
            errors.append(f"{rel_path}:{line_no}: expected {expected_ver}, found {last_ver}")
    return major, minor, errors


def _check_agents(root: Path, expected_minor: int) -> list[str]:
    """Check AGENTS.md lines against expected minor version."""
    agents_path = root / "AGENTS.md"
    if not agents_path.is_file():
        return [f"AGENTS.md: file not found under {root}"]
    try:
        lines = agents_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [f"AGENTS.md: failed to read: {exc}"]
    errors: list[str] = []
    expected_ver = f"1.{expected_minor}"
    hits = [(n, ln) for n, ln in enumerate(lines, 1) if _AGENTS_ABI_RE.search(ln) and _AGENTS_MINOR_RE.search(ln)]
    if len(hits) != 1:
        return [f"AGENTS.md: expected exactly one interop ABI release line, found {len(hits)}"]
    for line_no, line in hits:
        m_abi = _AGENTS_ABI_RE.search(line)
        if m_abi and m_abi.group(1) != expected_ver:
            errors.append(f"AGENTS.md:{line_no}: expected {expected_ver}, found {m_abi.group(1)}")
        m_minor = _AGENTS_MINOR_RE.search(line)
        if m_minor and int(m_minor.group(1)) != expected_minor:
            errors.append(f"AGENTS.md:{line_no}: expected {expected_ver}, found 1.{m_minor.group(1)}")
    return errors


def run_check(root: Path, quiet: bool = False) -> tuple[int, int, list[str]]:
    """Verify consistency across interop.h and AGENTS.md."""
    major, minor, errors = _check_interop(root)
    if not errors and major != 1:
        errors.append(f"interop.h: expected ABI major 1, found {major} (MAJOR never bumps)")
    if not errors:
        errors.extend(_check_agents(root, minor))
    if errors:
        if not quiet:
            for err in errors:
                print(err)
        return major, minor, errors
    if not quiet:
        print(f"bump-abi: ABI 1.{minor} consistent (3 sites)")
    return major, minor, []


def _format_adr(adr: str) -> str:
    """Format ADR reference as ADR-NNNN."""
    clean = adr.strip()
    if clean.isdigit():
        return f"ADR-{int(clean):04d}"
    if clean.startswith("ADR-"):
        return clean
    return f"ADR-{clean}"


def _update_interop_text(text: str, minor: int, new_minor: int, note: str, adr: str | None) -> str:
    """Return updated interop.h text with bumped minor and new history line."""
    updated = _ABI_MINOR_RE.sub(f"#define PELORUS_ABI_MINOR {new_minor}u", text, count=1)
    block_match, entries = _find_history_entries(updated)
    last_ver, last_line_no = entries[-1]
    _expect(last_ver == f"1.{minor}", f"history last entry mismatch: {last_ver}")
    clean_note = note.strip().lstrip("+").strip().rstrip(".")
    adr_str = f" ({_format_adr(adr)})" if adr else ""
    new_entry = f" *   1.{new_minor}  + {clean_note}{adr_str}. */\n"
    lines = updated.splitlines(keepends=True)
    target_idx = last_line_no - 1
    old_line = lines[target_idx]
    lines[target_idx] = re.sub(r"\s*\*/\s*$", "", old_line) + "\n"
    lines.insert(target_idx + 1, new_entry)
    return "".join(lines)


def _update_agents_text(text: str, minor: int, new_minor: int, adr: str | None) -> str:
    """Return updated AGENTS.md text with bumped minor and optional ADR."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    adr_tag = _format_adr(adr) if adr else ""
    for line in lines:
        if "interop ABI" in line and f"interop ABI 1.{minor}" in line:
            line = re.sub(rf"interop ABI 1\.{minor}\b", f"interop ABI 1.{new_minor}", line)
            line = re.sub(rf"(`PELORUS_ABI_MINOR`\s+){minor}\b", rf"\g<1>{new_minor}", line)
            if adr_tag:
                line = re.sub(r"\),\s*append-only\.", f", {adr_tag}), append-only.", line)
        out.append(line)
    return "".join(out)


def run_write(root: Path, note: str, adr: str | None) -> int:
    """Bump the ABI minor across interop.h and AGENTS.md, or refuse on error."""
    if not note or not note.strip():
        print("bump-abi: --note text must not be empty", file=sys.stderr)
        return 2
    _, minor, errors = run_check(root, quiet=True)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        print("bump-abi: refusal: consistent ABI check required before write", file=sys.stderr)
        return 2
    new_minor = minor + 1
    header_path = find_interop_h(root / "tools" / "tester")
    agents_path = root / "AGENTS.md"
    new_header = _update_interop_text(header_path.read_text(encoding="utf-8"), minor, new_minor, note, adr)
    new_agents = _update_agents_text(agents_path.read_text(encoding="utf-8"), minor, new_minor, adr)
    _write_texts([(header_path, new_header), (agents_path, new_agents)])
    print(f"bump-abi: bumped ABI 1.{minor} -> 1.{new_minor}:")
    print(f"  {header_path.relative_to(root)}")
    print(f"  {agents_path.relative_to(root)}")
    print("\nfollow-up steps:")
    print("  praetorctl compile-context")
    print("  extend libpelorus/test/interop_test.c")
    print("  docs/api/interop-abi.md")
    print("  changelog fragment")
    return 0


def _write_texts(updates: list[tuple[Path, str]]) -> None:
    """Write every file through a sibling temp file; restore all originals if one write fails."""
    originals = [(path, path.read_bytes()) for path, _ in updates]
    done: list[Path] = []
    try:
        for path, text in updates:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            done.append(path)
    except OSError:
        for path, data in originals:
            if path in done:
                path.write_bytes(data)
        raise


def _run_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute bump-abi.py CLI in a subprocess with explicit safety constraints."""
    exe = Path(sys.executable).resolve()
    _expect(exe.is_file() and os.access(exe, os.X_OK), f"python interpreter not executable: {exe}")
    cmd = [str(exe), "-I", str(Path(__file__).resolve())] + argv
    return subprocess.run(  # nosec B603 - fixed python argv, no shell, explicit timeout  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit - argv is sys.executable plus this script and fixed flags, no shell, explicit timeout
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _copy_tree_files(src_root: Path, dst_root: Path) -> tuple[Path, Path]:
    """Copy the two tracked ABI files into a clean temporary root."""
    header_rel = Path("libpelorus/include/pelorus/interop.h")
    agents_rel = Path("AGENTS.md")
    dst_h = dst_root / header_rel
    dst_a = dst_root / agents_rel
    dst_h.parent.mkdir(parents=True, exist_ok=True)
    dst_h.write_bytes((src_root / header_rel).read_bytes())
    dst_a.write_bytes((src_root / agents_rel).read_bytes())
    return dst_h, dst_a


def _test_rollback(tmp_root: Path) -> None:
    """Planted write failure on the second file must restore the first one byte-identical."""
    first, second = tmp_root / "rollback-a.txt", tmp_root / "rollback-b.txt"
    first.write_text("old a\n", encoding="utf-8")
    second.write_text("old b\n", encoding="utf-8")
    (tmp_root / "rollback-b.txt.tmp").mkdir()
    raised = False
    try:
        _write_texts([(first, "new a\n"), (second, "new b\n")])
    except OSError:
        raised = True
    _expect(raised, "rollback case: planted write failure did not raise")
    _expect(first.read_text(encoding="utf-8") == "old a\n", "rollback case: first file not restored")
    _expect(second.read_text(encoding="utf-8") == "old b\n", "rollback case: second file changed")


def _self_test(real_root: Path) -> int:
    """Run planted defect and mutation tests against isolated temporary trees."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        dst_h, dst_a = _copy_tree_files(real_root, tmp_root)
        _, minor, _ = run_check(tmp_root, quiet=True)
        cur, nxt = f"interop ABI 1.{minor}", f"interop ABI 1.{minor + 1}"
        # Case 1: consistent tree -> --check returns 0
        res1 = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res1.returncode == 0, f"case 1 failed: {res1.stderr}")
        # Case 2: planted AGENTS.md mismatch -> 1 naming the file
        orig_agents = dst_a.read_bytes()
        dst_a.write_text(orig_agents.decode("utf-8").replace(cur, "interop ABI 0.0"), encoding="utf-8")
        res2 = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res2.returncode == 1, f"case 2 expected exit 1, got {res2.returncode}")
        _expect("AGENTS.md" in (res2.stdout + res2.stderr), "case 2 output missing AGENTS.md")
        dst_a.write_bytes(orig_agents)
        # Case 3: --write bumps minor -> minor+1, then --check is 0 and history ends with note
        res3 = _run_cli(["--root", str(tmp_root), "--write", "--note", "synthetic grain", "--adr", "0215"])
        _expect(res3.returncode == 0, f"case 3 --write failed: {res3.stderr}")
        res3_check = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res3_check.returncode == 0, f"case 3 --check failed: {res3_check.stderr}")
        _expect(f"*   1.{minor + 1}  + synthetic grain (ADR-0215). */" in dst_h.read_text(encoding="utf-8"), "case 3 note missing")
        # Case 4: --write on mismatched tree -> 2 and files byte-identical
        dst_a.write_text(dst_a.read_text(encoding="utf-8").replace(nxt, "interop ABI 0.0"), encoding="utf-8")
        snap_h, snap_a = dst_h.read_bytes(), dst_a.read_bytes()
        res4 = _run_cli(["--root", str(tmp_root), "--write", "--note", "should abort"])
        _expect(res4.returncode == 2, f"case 4 expected exit 2, got {res4.returncode}")
        _expect(dst_h.read_bytes() == snap_h and dst_a.read_bytes() == snap_a, "case 4 files modified on error")
        # Case 5: empty note -> 2 and files byte-identical
        dst_a.write_bytes(orig_agents)
        dst_h.write_bytes((real_root / Path("libpelorus/include/pelorus/interop.h")).read_bytes())
        snap_h5, snap_a5 = dst_h.read_bytes(), dst_a.read_bytes()
        res5 = _run_cli(["--root", str(tmp_root), "--write", "--note", "   "])
        _expect(res5.returncode == 2, f"case 5 expected exit 2, got {res5.returncode}")
        _expect(dst_h.read_bytes() == snap_h5 and dst_a.read_bytes() == snap_a5, "case 5 files modified on error")
        # Case 6: AGENTS.md without the release line -> 1
        dst_a.write_text(orig_agents.decode("utf-8").replace(cur, "interop contract"), encoding="utf-8")
        res6 = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res6.returncode == 1, f"case 6 expected exit 1, got {res6.returncode}")
        # Case 7: header declares ABI major 2 -> --check 1
        dst_a.write_bytes(orig_agents)
        dst_h.write_text(snap_h5.decode("utf-8").replace("#define PELORUS_ABI_MAJOR 1u", "#define PELORUS_ABI_MAJOR 2u"), encoding="utf-8")
        res7 = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res7.returncode == 1, f"case 7 expected exit 1, got {res7.returncode}")
        _test_rollback(tmp_root)
    print("bump-abi: self-test PASS (all 8 planted cases)")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for bump-abi."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify ABI consistency across sites")
    parser.add_argument("--write", action="store_true", help="bump minor across all sites")
    parser.add_argument("--note", type=str, default="", help="history note for the bumped version")
    parser.add_argument("--adr", type=str, default=None, help="ADR number associated with the bump")
    parser.add_argument("--self-test", action="store_true", help="run planted self-test suite")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for bump-abi."""
    args = parse_args(argv)
    if args.self_test:
        return _self_test(args.root.resolve())
    if args.check:
        _, _, errors = run_check(args.root.resolve())
        return 1 if errors else 0
    if args.write:
        return run_write(args.root.resolve(), args.note, args.adr)
    print("bump-abi: specify --check, --write, or --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
