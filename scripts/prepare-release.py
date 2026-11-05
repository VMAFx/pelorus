#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Prepare and synchronize release versions across repository sites (issue #502).

Validates and updates project version strings and macro definitions across
meson.build, pelorus.h, README.md, and AGENTS.md.
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

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-rc\.([1-9][0-9]*))?\Z")
_MESON_VER_RE = re.compile(r"version:\s*'([^']+)'")
_PELORUS_MAJ_RE = re.compile(r"#define\s+PELORUS_VERSION_MAJOR\s+(\d+)\b")
_PELORUS_MIN_RE = re.compile(r"#define\s+PELORUS_VERSION_MINOR\s+(\d+)\b")
_PELORUS_PAT_RE = re.compile(r"#define\s+PELORUS_VERSION_PATCH\s+(\d+)\b")
_PELORUS_STR_RE = re.compile(r'#define\s+PELORUS_VERSION_STR\s+"([^"]+)"')
_README_VER_RE = re.compile(r"Pre-1\.0\s+\(`v([^`]+)`\)\.")
_AGENTS_VER_RE = re.compile(r"- Release\s+`v([^`]+)`\s+line:")


def _expect(condition: bool, message: str) -> None:
    """Helper raising AssertionError when condition is false (Bandit B101)."""
    if not condition:
        raise AssertionError(message)


def parse_version(ver_str: str) -> tuple[int, int, int, str | None]:
    """Parse version string into (major, minor, patch, rc_number) or raise ValueError."""
    match = _VERSION_RE.match(ver_str)
    if match is None:
        raise ValueError(f"malformed version: '{ver_str}'; expected X.Y.Z or X.Y.Z-rc.N (N >= 1)")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)


def _check_meson(root: Path) -> tuple[str, list[str]]:
    """Parse canonical version from meson.build and report syntax issues."""
    path = root / "meson.build"
    if not path.is_file():
        return "", [f"meson.build: file not found under {root}"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "", [f"meson.build: failed to read: {exc}"]
    m = _MESON_VER_RE.search(text)
    if m is None:
        return "", ["meson.build: missing version: '...' in project()"]
    ver = m.group(1)
    line_no = text[:m.start()].count("\n") + 1
    try:
        parse_version(ver)
    except ValueError as exc:
        return ver, [f"meson.build:{line_no}: {exc}"]
    return ver, []


def _check_pelorus_h(root: Path, exp_ver: str, exp_core: tuple[int, int, int]) -> list[str]:
    """Verify pelorus.h macros match expected version and core numbers."""
    rel = "libpelorus/include/pelorus/pelorus.h"
    path = root / rel
    if not path.is_file():
        return [f"{rel}: file not found under {root}"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}: failed to read: {exc}"]
    errors: list[str] = []
    checks = (
        (_PELORUS_STR_RE, exp_ver, "PELORUS_VERSION_STR"),
        (_PELORUS_MAJ_RE, str(exp_core[0]), "PELORUS_VERSION_MAJOR"),
        (_PELORUS_MIN_RE, str(exp_core[1]), "PELORUS_VERSION_MINOR"),
        (_PELORUS_PAT_RE, str(exp_core[2]), "PELORUS_VERSION_PATCH"),
    )
    for pat, expected, name in checks:
        m = pat.search(text)
        if m is None:
            errors.append(f"{rel}: missing definition for {name}")
            continue
        val = m.group(1)
        line_no = text[:m.start()].count("\n") + 1
        if val != expected:
            errors.append(f"{rel}:{line_no}: expected {expected}, found {val}")
    return errors


def _check_doc_site(path: Path, rel: str, pattern: re.Pattern[str], exp_ver: str) -> list[str]:
    """Verify documentation file version matches expected version."""
    if not path.is_file():
        return [f"{rel}: file not found under {path.parent}"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}: failed to read: {exc}"]
    m = pattern.search(text)
    if m is None:
        return [f"{rel}: release version pattern not found"]
    val = m.group(1)
    line_no = text[:m.start()].count("\n") + 1
    if val != exp_ver:
        return [f"{rel}:{line_no}: expected {exp_ver}, found {val}"]
    return []


def run_check(root: Path, quiet: bool = False) -> tuple[str, list[str]]:
    """Verify version consistency across all four tracked files."""
    canonical_ver, errors = _check_meson(root)
    if errors or not canonical_ver:
        if not quiet:
            for err in errors:
                print(err)
        return canonical_ver, errors
    maj, min_, pat, _ = parse_version(canonical_ver)
    errors.extend(_check_pelorus_h(root, canonical_ver, (maj, min_, pat)))
    errors.extend(_check_doc_site(root / "README.md", "README.md", _README_VER_RE, canonical_ver))
    errors.extend(_check_doc_site(root / "AGENTS.md", "AGENTS.md", _AGENTS_VER_RE, canonical_ver))
    if errors:
        if not quiet:
            for err in errors:
                print(err)
        return canonical_ver, errors
    if not quiet:
        print(f"prepare-release: version {canonical_ver} consistent (4 files)")
    return canonical_ver, []


def run_write(root: Path, new_ver: str) -> int:
    """Rewrite version strings across all four tracked files, or refuse on error."""
    try:
        maj, min_, pat, _ = parse_version(new_ver)
    except ValueError as exc:
        print(f"prepare-release: refusal: {exc}", file=sys.stderr)
        return 2
    cur_ver, errors = run_check(root, quiet=True)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        print("prepare-release: refusal: existing sites must agree before write", file=sys.stderr)
        return 2
    if new_ver == cur_ver:
        print(f"prepare-release: version {new_ver} is already current; nothing written")
        return 0
    _write_all(root, new_ver, (maj, min_, pat))
    print(f"prepare-release: updated 4 files to version {new_ver}:")
    print("  meson.build\n  libpelorus/include/pelorus/pelorus.h\n  README.md\n  AGENTS.md")
    print("\nfollow-up steps:\n  praetorctl compile-context\n  changelog rotation per docs/development/release.md")
    return 0


def _write_all(root: Path, new_ver: str, core: tuple[int, int, int]) -> None:
    """Build all four replacement texts first, then write them together."""
    m_path = root / "meson.build"
    h_path = root / "libpelorus/include/pelorus/pelorus.h"
    r_path = root / "README.md"
    a_path = root / "AGENTS.md"
    h_text = h_path.read_text(encoding="utf-8")
    h_text = _PELORUS_STR_RE.sub(f'#define PELORUS_VERSION_STR "{new_ver}"', h_text, count=1)
    h_text = _PELORUS_MAJ_RE.sub(f"#define PELORUS_VERSION_MAJOR {core[0]}", h_text, count=1)
    h_text = _PELORUS_MIN_RE.sub(f"#define PELORUS_VERSION_MINOR {core[1]}", h_text, count=1)
    h_text = _PELORUS_PAT_RE.sub(f"#define PELORUS_VERSION_PATCH {core[2]}", h_text, count=1)
    _write_texts([
        (m_path, _MESON_VER_RE.sub(f"version: '{new_ver}'", m_path.read_text(encoding="utf-8"), count=1)),
        (h_path, h_text),
        (r_path, _README_VER_RE.sub(f"Pre-1.0 (`v{new_ver}`).", r_path.read_text(encoding="utf-8"), count=1)),
        (a_path, _AGENTS_VER_RE.sub(f"- Release `v{new_ver}` line:", a_path.read_text(encoding="utf-8"), count=1)),
    ])

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
    """Execute prepare-release.py CLI in a subprocess with explicit safety constraints."""
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


def _copy_tree_files(src: Path, dst: Path) -> list[Path]:
    """Copy the four tracked release-version files to temporary root."""
    paths = [
        Path("meson.build"),
        Path("libpelorus/include/pelorus/pelorus.h"),
        Path("README.md"),
        Path("AGENTS.md"),
    ]
    out = []
    for rel in paths:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((src / rel).read_bytes())
        out.append(target)
    return out


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
        files = _copy_tree_files(real_root, tmp_root)
        _, h_file, r_file, a_file = files
        cur_ver, _ = run_check(tmp_root, quiet=True)
        cur_major, cur_minor, cur_patch, _ = parse_version(cur_ver)
        nxt_minor = cur_minor + 1
        target = f"{cur_major}.{nxt_minor}.0-rc.1"
        # Case 1: consistent tree -> 0
        res1 = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res1.returncode == 0, f"case 1 failed: {res1.stderr}")
        # Case 2: planted pelorus.h PATCH mismatch -> 1
        orig_h = h_file.read_bytes()
        h_file.write_text(orig_h.decode("utf-8").replace(f"PELORUS_VERSION_PATCH {cur_patch}", f"PELORUS_VERSION_PATCH {cur_patch + 1}"), encoding="utf-8")
        res2 = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res2.returncode == 1, f"case 2 expected exit 1, got {res2.returncode}")
        h_file.write_bytes(orig_h)
        # Case 3: --write <major>.<minor+1>.0-rc.1 updates all 4 files then --check is 0
        res3 = _run_cli(["--root", str(tmp_root), "--write", target])
        _expect(res3.returncode == 0, f"case 3 --write failed: {res3.stderr}")
        h_txt = h_file.read_text(encoding="utf-8")
        _expect(f'#define PELORUS_VERSION_STR "{target}"' in h_txt, "case 3 STR missing")
        _expect(f"#define PELORUS_VERSION_MAJOR {cur_major}" in h_txt, "case 3 MAJOR missing")
        _expect(f"#define PELORUS_VERSION_MINOR {nxt_minor}" in h_txt, "case 3 MINOR missing")
        _expect("#define PELORUS_VERSION_PATCH 0" in h_txt, "case 3 PATCH missing")
        _expect(f"version: '{target}'" in files[0].read_text(encoding="utf-8"), "case 3 meson missing")
        _expect(f"v{target}" in r_file.read_text(encoding="utf-8"), "case 3 README missing")
        _expect(f"v{target}" in a_file.read_text(encoding="utf-8"), "case 3 AGENTS missing")
        res3_check = _run_cli(["--root", str(tmp_root), "--check"])
        _expect(res3_check.returncode == 0, f"case 3 --check failed: {res3_check.stderr}")
        # Case 4: --write 0.5 -> 2 and files byte-identical
        snap = [f.read_bytes() for f in files]
        res4 = _run_cli(["--root", str(tmp_root), "--write", "0.5"])
        _expect(res4.returncode == 2, f"case 4 expected exit 2, got {res4.returncode}")
        _expect([f.read_bytes() for f in files] == snap, "case 4 files modified on error")
        # Case 5: --write 1.0.0-rc.0 -> 2 and files byte-identical
        res5 = _run_cli(["--root", str(tmp_root), "--write", "1.0.0-rc.0"])
        _expect(res5.returncode == 2, f"case 5 expected exit 2, got {res5.returncode}")
        _expect([f.read_bytes() for f in files] == snap, "case 5 files modified on error")
        # Case 6: trailing newline and leading zero -> 2 and files byte-identical
        for bad in (f"{target}\n", f"{cur_major}.0{nxt_minor}.0"):
            res6 = _run_cli(["--root", str(tmp_root), "--write", bad])
            _expect(res6.returncode == 2, f"case 6 {bad!r} expected exit 2, got {res6.returncode}")
            _expect([f.read_bytes() for f in files] == snap, f"case 6 {bad!r} files modified on error")
        _test_rollback(tmp_root)
    print("prepare-release: self-test PASS (all 7 planted cases)")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for prepare-release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify version consistency across sites")
    parser.add_argument("--write", type=str, metavar="VERSION", default=None, help="write new version to all sites")
    parser.add_argument("--self-test", action="store_true", help="run planted self-test suite")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for prepare-release."""
    args = parse_args(argv)
    if args.self_test:
        return _self_test(args.root.resolve())
    if args.check:
        _, errors = run_check(args.root.resolve())
        return 1 if errors else 0
    if args.write is not None:
        return run_write(args.root.resolve(), args.write)
    print("prepare-release: specify --check, --write <version>, or --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
