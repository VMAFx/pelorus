#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Interop constants parsed from interop.h (issue #500, #515).

The one Python source for PELORUS_MAGIC_STR, PELORUS_ABI_MAJOR and
sizeof(PelorusSideData): tools import MAGIC, ABI_MAJOR and HEADER_BYTES from
here instead of typing the values. The header is discovered from
INTEROP_H_CANDIDATES (repository checkout or installed tester image layout).
A missing or inconsistent definition fails at import, so a consumer never runs
on a guessed value.
"""

from __future__ import annotations

import argparse
import codecs
import re
import sys
import tempfile
from pathlib import Path


def _candidates(here: Path) -> tuple[Path, ...]:
    """Candidate locations for interop.h: repository checkout or installed image."""
    return (
        here.parents[1] / "libpelorus" / "include" / "pelorus" / "interop.h",
        here.parent / "include" / "pelorus" / "interop.h",
    )


INTEROP_H_CANDIDATES = _candidates(Path(__file__).resolve().parent)


def find_interop_h(here: Path) -> Path:
    """Return the first candidate header that exists as a file, or raise ValueError."""
    for candidate in _candidates(here):
        if candidate.is_file():
            return candidate
    tried = ", ".join(str(c) for c in _candidates(here))
    raise ValueError(f"no interop.h found; tried: {tried}")

_MAGIC_RE = re.compile(r'#define\s+PELORUS_MAGIC_STR\s+"((?:[^"\\]|\\.)*)"')
_MAGIC_LEN_RE = re.compile(r"#define\s+PELORUS_MAGIC_LEN\s+(\d+)[uU]?\b")
_ABI_MAJOR_RE = re.compile(r"#define\s+PELORUS_ABI_MAJOR\s+(\d+)[uU]?\b")
_SIZE_RE = re.compile(r"_Static_assert\s*\(\s*sizeof\s*\(\s*PelorusSideData\s*\)\s*==\s*(\d+)")


def _int_define(pattern: re.Pattern[str], text: str, name: str) -> int:
    """Return the integer a definition names, or raise ValueError naming it."""
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"missing definition: {name}")
    return int(match.group(1))


def _magic(text: str) -> bytes:
    """Return the decoded PELORUS_MAGIC_STR bytes, checked against PELORUS_MAGIC_LEN."""
    match = _MAGIC_RE.search(text)
    if match is None:
        raise ValueError("missing definition: PELORUS_MAGIC_STR")
    try:
        magic, _ = codecs.escape_decode(match.group(1).encode("latin-1"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"malformed PELORUS_MAGIC_STR escape: {match.group(1)}") from exc
    magic_len = _int_define(_MAGIC_LEN_RE, text, "PELORUS_MAGIC_LEN")
    if len(magic) != magic_len:
        raise ValueError(f"PELORUS_MAGIC_STR has {len(magic)} bytes, PELORUS_MAGIC_LEN says {magic_len}")
    return magic


def parse_interop_text(text: str) -> tuple[bytes, int, int]:
    """Parse (magic, abi_major, header_bytes) from interop.h source text.

    Raises ValueError when a definition is missing or the magic disagrees with
    PELORUS_MAGIC_LEN.
    """
    abi_major = _int_define(_ABI_MAJOR_RE, text, "PELORUS_ABI_MAJOR")
    header_bytes = _int_define(_SIZE_RE, text, "_Static_assert(sizeof(PelorusSideData) == N)")
    return _magic(text), abi_major, header_bytes


def load_interop_constants(path: Path | str | None = None) -> tuple[bytes, int, int]:
    """Read interop.h from disk and parse its constants."""
    target = Path(path) if path is not None else find_interop_h(Path(__file__).resolve().parent)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"failed to read interop header {target}: {exc}") from exc
    return parse_interop_text(text)


MAGIC, ABI_MAJOR, HEADER_BYTES = load_interop_constants()

_VALID = (
    '#define PELORUS_MAGIC_STR "PELOR1\\0\\0"\n'
    "#define PELORUS_MAGIC_LEN 8\n"
    "#define PELORUS_ABI_MAJOR 1u\n"
    '_Static_assert(sizeof(PelorusSideData) == 48, "header size");\n'
)

# (label, planted header text); every case must be refused
_REFUSED = (
    ("missing magic", re.sub(r"#define PELORUS_MAGIC_STR [^\n]*\n", "", _VALID)),
    ("missing magic length", re.sub(r"#define PELORUS_MAGIC_LEN [^\n]*\n", "", _VALID)),
    ("missing abi major", re.sub(r"#define PELORUS_ABI_MAJOR [^\n]*\n", "", _VALID)),
    ("missing size assert", re.sub(r"_Static_assert[^\n]*\n", "", _VALID)),
    ("short magic", _VALID.replace('"PELOR1\\0\\0"', '"PELOR1"')),
    ("magic length disagrees", _VALID.replace("PELORUS_MAGIC_LEN 8", "PELORUS_MAGIC_LEN 7")),
)

# (label, planted header text, expected parse); derived values follow the header
_ACCEPTED = (
    ("valid header", _VALID, (b"PELOR1\0\0", 1, 48)),
    (
        "changed values follow the header",
        _VALID.replace("PELOR1", "PELOR2").replace("MAJOR 1u", "MAJOR 2u").replace("== 48", "== 56"),
        (b"PELOR2\0\0", 2, 56),
    ),
)


def _self_test_candidate_search() -> str | None:
    """Planted cases for candidate search: installed image layout and missing header."""
    here = Path(__file__).resolve().parent
    real_h = find_interop_h(here)
    real_consts = load_interop_constants(real_h)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tester_dir = tmp_path / "tester"
        tester_dir.mkdir()
        header_dir = tmp_path / "include" / "pelorus"
        header_dir.mkdir(parents=True)
        planted_h = header_dir / "interop.h"
        planted_h.write_bytes(real_h.read_bytes())
        found_h = find_interop_h(tester_dir)
        if found_h != planted_h:
            return f"installed layout found {found_h}, want {planted_h}"
        if load_interop_constants(found_h) != real_consts:
            return "constants from installed layout do not match real header"
        print("planted case 'installed image layout': ok")
    with tempfile.TemporaryDirectory() as tmp:
        empty_dir = Path(tmp) / "tester"
        empty_dir.mkdir()
        try:
            find_interop_h(empty_dir)
        except ValueError as exc:
            print(f"planted case 'no candidate headers' refused: {exc}")
        else:
            return "find_interop_h accepted empty directory"
    return None


def run_self_test() -> int:
    """Refuse each planted defect, follow each planted change, then parse the real header."""
    for label, text in _REFUSED:
        try:
            parse_interop_text(text)
        except ValueError as exc:
            print(f"planted case '{label}' refused: {exc}")
            continue
        print(f"self-test FAIL: {label} was accepted", file=sys.stderr)
        return 1
    for label, text, want in _ACCEPTED:
        got = parse_interop_text(text)
        if got != want:
            print(f"self-test FAIL: {label}: want {want!r}, got {got!r}", file=sys.stderr)
            return 1
    err = _self_test_candidate_search()
    if err is not None:
        print(f"self-test FAIL: {err}", file=sys.stderr)
        return 1
    load_interop_constants()
    print(f"pelorus_interop_consts self-test: ok ({len(_REFUSED) + len(_ACCEPTED) + 2} planted cases, real header)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Print the parsed constants, or run the self-test."""
    parser = argparse.ArgumentParser(description="Interop constants parsed from interop.h.")
    parser.add_argument("--self-test", action="store_true", help="run the planted cases and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    print(f"MAGIC={MAGIC!r} ABI_MAJOR={ABI_MAJOR} HEADER_BYTES={HEADER_BYTES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
