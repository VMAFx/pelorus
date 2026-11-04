#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Guard the interop constants against hand-typed copies (issue #500).

1. The named mirrors in the FFmpeg patch sources (PEL_SEI_* in
   pelorus_sei_fit.h, PEL_FG_* and PEL_* in the NVENC patches) must equal
   the values parsed from interop.h by tools/tester/pelorus_interop_consts.py.
2. The magic literal "PELOR1\\0\\0" may appear in tracked files only in
   interop.h, as the definition of a *_MAGIC mirror, or in an allow-listed
   file with a reason; consumers use the names instead.
"""

from __future__ import annotations

import argparse
import codecs
import os
import re
import runpy
import subprocess  # nosec B404 - fixed git argv, no shell, explicit timeout
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def literal_patterns(magic: bytes) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Build (literal_re, mirror_def_re) from magic bytes."""
    nul_idx = magic.find(b"\0")
    if nul_idx == -1:
        prefix_bytes, suffix = magic, b""
    else:
        prefix_bytes, suffix = magic[:nul_idx], magic[nul_idx:]
    if not prefix_bytes or any(b != 0 for b in suffix):
        raise ValueError(f"magic must be printable ASCII then NULs only: {magic!r}")
    try:
        prefix = prefix_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"magic prefix is not ASCII: {magic!r}") from exc
    if not all(32 <= ord(c) <= 126 for c in prefix):
        raise ValueError(f"magic prefix is not printable ASCII: {magic!r}")

    esc_prefix = re.escape(prefix)
    nul_count = len(suffix)
    nul_shape = rf"(?:\\{{1,2}}(?:0|x00)){{{nul_count}}}" if nul_count else ""
    literal_pat = re.compile(rf"{esc_prefix}{nul_shape}")

    mirror_nuls = r"\\0" * nul_count
    mirror_pat = re.compile(
        rf"""^[+\- ]?#define\s+\w+_MAGIC\s+"{esc_prefix}{mirror_nuls}"\s*(?:/\*[^*]*\*/\s*)?$"""
    )
    return literal_pat, mirror_pat


# the default patterns match the canonical PELOR1 magic
DEFAULT_PATTERNS = literal_patterns(b"PELOR1\0\0")
LITERAL, MIRROR_DEF = DEFAULT_PATTERNS
_NOT_PLAIN = object()

# path -> reason; every entry must still hold the literal (stale entries fail)
ALLOWLIST: dict[str, str] = {
    "libpelorus/include/pelorus/interop.h": "canonical definition",
    "tools/tester/pelorus_interop_consts.py": "self-test plants a header text",
    "scripts/check-interop-constants.py": "this guard names the literal it refuses",
}
ALLOW_PREFIXES = ("docs/", "changelog.d/", "CHANGELOG.md")

# (file under the repository, macro prefix) whose mirrors must equal interop.h
MIRRORS = (
    ("ffmpeg-patches/files/pelorus_sei_fit.h", "PEL_SEI"),
    ("ffmpeg-patches/files/nvenc-pelorus-film-grain.patch", "PEL_FG"),
    ("ffmpeg-patches/files/nvenc-pelorus-me-hints.patch", "PEL"),
)


def load_interop_consts() -> tuple[bytes, int, int]:
    """Load (magic, abi_major, header_bytes) from tools/tester/pelorus_interop_consts.py (issue #500)."""
    # Import shared interop constants from tools/tester by file path (issue #500).
    path = REPO_ROOT / "tools" / "tester" / "pelorus_interop_consts.py"
    consts = runpy.run_path(str(path))
    return consts["MAGIC"], consts["ABI_MAJOR"], consts["HEADER_BYTES"]


def _define(text: str, name: str, value: str) -> str | object | None:
    """Return the value text of `#define <name> <value>`, _NOT_PLAIN for non-plain value, or None when absent."""
    pattern = re.compile(
        rf"^[+\- ]?#define\s+{name}\s+({value})[uU]?\s*(?:/\*[^*]*\*/\s*|//.*)?$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is not None:
        return match.group(1)
    if re.search(rf"^[+\- ]?#define\s+{name}\b", text, re.MULTILINE):
        return _NOT_PLAIN
    return None


def check_mirrors(text: str, label: str, prefix: str, consts: tuple[bytes, int, int]) -> list[str]:
    """Compare the four <prefix>_* mirrors in text with the canonical values."""
    magic, abi_major, header_bytes = consts
    raw = _define(text, f"{prefix}_MAGIC", r'"(?:[^"\\]|\\.)*"')
    magic_val: bytes | object | None = raw
    if isinstance(raw, str):
        magic_val = codecs.escape_decode(raw[1:-1].encode("latin-1"))[0]
    found = {
        "MAGIC": magic_val,
        "MAGIC_LEN": _define(text, f"{prefix}_MAGIC_LEN", r"\d+"),
        "ABI_MAJOR": _define(text, f"{prefix}_ABI_MAJOR", r"\d+"),
        "HDR_BYTES": _define(text, f"{prefix}_HDR_BYTES", r"\d+"),
    }
    want = {"MAGIC": magic, "MAGIC_LEN": len(magic), "ABI_MAJOR": abi_major, "HDR_BYTES": header_bytes}
    errors = []
    for key, expected in want.items():
        got = found[key]
        if got is _NOT_PLAIN:
            errors.append(f"{label}: {prefix}_{key} is not a plain value")
        elif got is None:
            errors.append(f"{label}: missing #define {prefix}_{key}")
        elif (got if key == "MAGIC" else int(got)) != expected:
            errors.append(f"{label}: {prefix}_{key} is {got!r}, interop.h says {expected!r}")
    return errors


def is_allowed(rel_path: str) -> bool:
    """True for allow-listed files and documentation."""
    return rel_path in ALLOWLIST or rel_path.startswith(ALLOW_PREFIXES)


def scan_literals(
    rel_path: str,
    text: str,
    patterns: tuple[re.Pattern[str], re.Pattern[str]] | None = None,
) -> list[str]:
    """Report every magic literal in text that is not a *_MAGIC mirror definition."""
    if is_allowed(rel_path):
        return []
    literal_re, mirror_def_re = patterns if patterns is not None else DEFAULT_PATTERNS
    return [
        f"{rel_path}:{line_no}: magic literal outside interop.h; use the interop constant: {line.strip()}"
        for line_no, line in enumerate(text.splitlines(), start=1)
        if literal_re.search(line) and not mirror_def_re.match(line)
    ]


def repo_discovery_env() -> dict[str, str]:
    """Return the environment without Git's repository-selecting variables.

    Git exports GIT_DIR and its siblings to hooks; inherited, they make
    `git -C <dir>` list the invoking repository instead of <dir>, so the
    pre-push self-test accepted a non-git directory. The list is Git's own
    (`git rev-parse --local-env-vars`).
    """
    names = subprocess.run(  # nosec B603 - fixed git argv, explicit timeout
        ["git", "rev-parse", "--local-env-vars"],
        capture_output=True,
        timeout=30.0,
        check=True,
    ).stdout.decode("utf-8").split()
    if "GIT_DIR" not in names:
        raise RuntimeError("git rev-parse --local-env-vars did not list GIT_DIR")
    return {key: value for key, value in os.environ.items() if key not in names}


def tracked_files(repo_root: Path) -> list[str]:
    """List tracked files; a git failure raises instead of passing an empty tree."""
    proc = subprocess.run(  # nosec B603 - fixed git argv, explicit timeout
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        timeout=30.0,
        check=True,
        env=repo_discovery_env(),
    )
    files = [name for name in proc.stdout.decode("utf-8").split("\0") if name]
    if not files:
        raise RuntimeError(f"git ls-files listed no files under {repo_root}")
    return files


def scan_tracked_file(
    repo_root: Path,
    rel_path: str,
    patterns: tuple[re.Pattern[str], re.Pattern[str]] | None = None,
) -> list[str]:
    """Scan one tracked file for stray magic literals and stale allow-list entries."""
    path = repo_root / rel_path
    if not path.is_file():
        return []
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{rel_path}: cannot read: {exc}"]
    text = raw.decode("utf-8", errors="replace")
    errors = scan_literals(rel_path, text, patterns)
    literal_re, _ = patterns if patterns is not None else DEFAULT_PATTERNS
    if rel_path in ALLOWLIST and not literal_re.search(text):
        errors.append(f"{rel_path}: stale allow-list entry, the file holds no magic literal")
    return errors


def run_checks(repo_root: Path) -> list[str]:
    """Check the mirrors, scan every tracked file, and refuse stale allow-list entries."""
    consts = load_interop_consts()
    patterns = literal_patterns(consts[0])
    errors: list[str] = []
    for rel_path, prefix in MIRRORS:
        try:
            text = (repo_root / rel_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{rel_path}: cannot read: {exc}")
            continue
        errors.extend(check_mirrors(text, rel_path, prefix, consts))
    for rel_path in tracked_files(repo_root):
        errors.extend(scan_tracked_file(repo_root, rel_path, patterns))
    return errors


_CONSTS = (b"PELOR1\0\0", 1, 48)
_MIRRORS_OK = '#define T_MAGIC "PELOR1\\0\\0"\n#define T_MAGIC_LEN 8\n#define T_ABI_MAJOR 1u\n#define T_HDR_BYTES 48\n'

# (label, kind, path, text, expected error count); kind "scan" or "mirror"
_CASES = (
    ("C string copy", "scan", "src/bad.c", 'memcpy(p, "PELOR1\\0\\0", 8);\n', 1),
    ("Python bytes copy", "scan", "tools/bad.py", 'M = b"PELOR1\\0\\0"\n', 1),
    ("hex escapes", "scan", "tools/hex.py", 'M = b"PELOR1\\x00\\x00"\n', 1),
    ("C text in a Python string", "scan", "tools/gen.py", 'SRC = "memcpy(p, \\"PELOR1\\\\0\\\\0\\", 8);"\n', 1),
    ("copy in a numbered patch", "scan", "ffmpeg-patches/0099-x.patch", '+    memcmp(p, "PELOR1\\0\\0", 8)\n', 1),
    ("mirror definition", "scan", "ffmpeg-patches/0099-x.patch", '+#define PEL_X_MAGIC "PELOR1\\0\\0"\n', 0),
    ("commented mirror", "scan", "x.h", '#define X_MAGIC "PELOR1\\0\\0" /* PELORUS_MAGIC_STR */\n', 0),
    ("code after a mirror", "scan", "y.h", '#define Y_MAGIC "PELOR1\\0\\0" + 1\n', 1),
    ("documentation", "scan", "docs/interop.md", 'Magic "PELOR1\\0\\0"\n', 0),
    ("matching mirrors", "mirror", "ok.h", _MIRRORS_OK, 0),
    ("drifted mirrors", "mirror", "bad.h", _MIRRORS_OK.replace("PELOR1", "BAD__1").replace("8\n", "7\n")
     .replace("1u", "2u").replace("48", "40"), 4),
    ("missing mirrors", "mirror", "empty.h", "/* none */\n", 4),
    ("trailing expression", "mirror", "expr.h", _MIRRORS_OK.replace("1u", "1u + 1"), 1),
)


def _case_unreadable() -> str | None:
    """Planted case: an unreadable file produces an error and does not abort scanning."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("planted case 'unreadable file' skipped (root)")
        return None
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        locked = tmp_path / "locked.c"
        ok = tmp_path / "ok.c"
        locked.write_text("/* unreadable */\n", encoding="utf-8")
        ok.write_text('memcpy(p, "PELOR1\\0\\0", 8);\n', encoding="utf-8")
        os.chmod(locked, 0)
        try:
            errors: list[str] = []
            for rel in ("locked.c", "ok.c"):
                errors.extend(scan_tracked_file(tmp_path, rel))
            if (
                len(errors) != 2
                or not any("locked.c: cannot read" in e for e in errors)
                or not any("ok.c" in e for e in errors)
            ):
                return f"expected 2 errors (one locked.c cannot read, one ok.c), got {errors}"
            print(f"planted case 'unreadable file': {len(errors)} finding(s)")
            return None
        finally:
            os.chmod(locked, 0o600)


def _case_changed_magic() -> str | None:
    """Planted case: regexes follow changed magic and ignore old magic."""
    for bad_magic in (b"", b"\0\0", b"PEL\0\x01", b"\xff\0"):
        try:
            literal_patterns(bad_magic)
        except ValueError:
            continue  # refused, as the planted case requires
        return f"literal_patterns accepted invalid magic {bad_magic!r}"
    new_pats = literal_patterns(b"NEWMAG\0\0")
    new_found = scan_literals("tools/x.py", 'M = b"NEWMAG\\0\\0"\n', new_pats)
    if len(new_found) != 1:
        return f"changed magic new: want 1 finding, got {new_found}"
    pelor_found = scan_literals("tools/x.py", 'M = b"PELOR1\\0\\0"\n', new_pats)
    if len(pelor_found) != 0:
        return f"changed magic old: want 0 findings, got {pelor_found}"
    print("planted case 'changed magic': 1 finding(s) on new magic, 0 on old magic")
    return None


def run_self_test() -> int:
    """Run the planted cases, then refuse a failing git listing."""
    for label, kind, rel_path, text, want in _CASES:
        found = scan_literals(rel_path, text) if kind == "scan" else check_mirrors(text, rel_path, "T", _CONSTS)
        if len(found) != want:
            print(f"self-test FAIL: {label}: want {want} findings, got {found}", file=sys.stderr)
            return 1
        print(f"planted case '{label}': {len(found)} finding(s)")
    magic_err = _case_changed_magic()
    if magic_err is not None:
        print(f"self-test FAIL: {magic_err}", file=sys.stderr)
        return 1
    unreadable_err = _case_unreadable()
    if unreadable_err is not None:
        print(f"self-test FAIL: {unreadable_err}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        try:
            tracked_files(Path(tmp))
        except (subprocess.SubprocessError, RuntimeError) as exc:
            print(f"planted case 'not a git tree' refused: {type(exc).__name__}")
        else:
            print("self-test FAIL: a failing git listing was accepted", file=sys.stderr)
            return 1
    print(f"check-interop-constants self-test: ok ({len(_CASES) + 3} planted cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Check the repository, or run the self-test."""
    parser = argparse.ArgumentParser(description="Guard interop constant mirrors and magic literals.")
    parser.add_argument("--self-test", action="store_true", help="run the planted cases and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    errors = run_checks(REPO_ROOT)
    for err in errors:
        print(err, file=sys.stderr)
    if errors:
        print(f"check-interop-constants: {len(errors)} finding(s)", file=sys.stderr)
        return 1
    print("check-interop-constants: mirrors match interop.h; no stray magic literal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
