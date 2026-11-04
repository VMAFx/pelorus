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
import importlib.util
import re
import subprocess  # nosec B404 - fixed git argv, no shell, explicit timeout
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# the magic as C or Python escapes, also doubled inside a non-raw Python string
LITERAL = re.compile(r"PELOR1(?:\\{1,2}(?:0|x00)){2}")
MIRROR_DEF = re.compile(r"""^[+\- ]?#define\s+\w+_MAGIC\s+"PELOR1\\0\\0"\s*(?:/\*[^*]*\*/\s*)?$""")

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
    """Load (magic, abi_major, header_bytes) from tools/tester/pelorus_interop_consts.py."""
    path = REPO_ROOT / "tools" / "tester" / "pelorus_interop_consts.py"
    spec = importlib.util.spec_from_file_location("pelorus_interop_consts", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MAGIC, mod.ABI_MAJOR, mod.HEADER_BYTES


def _define(text: str, name: str, value: str) -> str | None:
    """Return the value text of `#define <name> <value>`, or None when absent."""
    match = re.search(rf"#define\s+{name}\s+({value})", text)
    return match.group(1) if match else None


def check_mirrors(text: str, label: str, prefix: str, consts: tuple[bytes, int, int]) -> list[str]:
    """Compare the four <prefix>_* mirrors in text with the canonical values."""
    magic, abi_major, header_bytes = consts
    raw = _define(text, f"{prefix}_MAGIC", r'"(?:[^"\\]|\\.)*"')
    found = {
        "MAGIC": None if raw is None else codecs.escape_decode(raw[1:-1].encode("latin-1"))[0],
        "MAGIC_LEN": _define(text, f"{prefix}_MAGIC_LEN", r"\d+"),
        "ABI_MAJOR": _define(text, f"{prefix}_ABI_MAJOR", r"\d+"),
        "HDR_BYTES": _define(text, f"{prefix}_HDR_BYTES", r"\d+"),
    }
    want = {"MAGIC": magic, "MAGIC_LEN": len(magic), "ABI_MAJOR": abi_major, "HDR_BYTES": header_bytes}
    errors = []
    for key, expected in want.items():
        got = found[key]
        if got is None:
            errors.append(f"{label}: missing #define {prefix}_{key}")
        elif (got if key == "MAGIC" else int(got)) != expected:
            errors.append(f"{label}: {prefix}_{key} is {got!r}, interop.h says {expected!r}")
    return errors


def is_allowed(rel_path: str) -> bool:
    """True for allow-listed files and documentation."""
    return rel_path in ALLOWLIST or rel_path.startswith(ALLOW_PREFIXES)


def scan_literals(rel_path: str, text: str) -> list[str]:
    """Report every magic literal in text that is not a *_MAGIC mirror definition."""
    if is_allowed(rel_path):
        return []
    return [
        f"{rel_path}:{line_no}: magic literal outside interop.h; use the interop constant: {line.strip()}"
        for line_no, line in enumerate(text.splitlines(), start=1)
        if LITERAL.search(line) and not MIRROR_DEF.match(line)
    ]


def tracked_files(repo_root: Path) -> list[str]:
    """List tracked files; a git failure raises instead of passing an empty tree."""
    proc = subprocess.run(  # nosec B603 - fixed git argv, explicit timeout
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True,
        timeout=30.0,
        check=True,
    )
    files = [name for name in proc.stdout.decode("utf-8").split("\0") if name]
    if not files:
        raise RuntimeError(f"git ls-files listed no files under {repo_root}")
    return files


def run_checks(repo_root: Path) -> list[str]:
    """Check the mirrors, scan every tracked file, and refuse stale allow-list entries."""
    consts = load_interop_consts()
    errors: list[str] = []
    for rel_path, prefix in MIRRORS:
        text = (repo_root / rel_path).read_text(encoding="utf-8")
        errors.extend(check_mirrors(text, rel_path, prefix, consts))
    for rel_path in tracked_files(repo_root):
        path = repo_root / rel_path
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        errors.extend(scan_literals(rel_path, text))
        if rel_path in ALLOWLIST and not LITERAL.search(text):
            errors.append(f"{rel_path}: stale allow-list entry, the file holds no magic literal")
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
)


def run_self_test() -> int:
    """Run the planted cases, then refuse a failing git listing."""
    for label, kind, rel_path, text, want in _CASES:
        found = scan_literals(rel_path, text) if kind == "scan" else check_mirrors(text, rel_path, "T", _CONSTS)
        if len(found) != want:
            print(f"self-test FAIL: {label}: want {want} findings, got {found}", file=sys.stderr)
            return 1
        print(f"planted case '{label}': {len(found)} finding(s)")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            tracked_files(Path(tmp))
        except (subprocess.SubprocessError, RuntimeError) as exc:
            print(f"planted case 'not a git tree' refused: {type(exc).__name__}")
        else:
            print("self-test FAIL: a failing git listing was accepted", file=sys.stderr)
            return 1
    print(f"check-interop-constants self-test: ok ({len(_CASES) + 1} planted cases)")
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
