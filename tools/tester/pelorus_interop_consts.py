#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Interop constants parsed from libpelorus/include/pelorus/interop.h (issue #500).

The one Python source for PELORUS_MAGIC_STR, PELORUS_ABI_MAJOR and
sizeof(PelorusSideData): tools import MAGIC, ABI_MAJOR and HEADER_BYTES from
here instead of typing the values. A missing or inconsistent definition fails
at import, so a consumer never runs on a guessed value.
"""

from __future__ import annotations

import argparse
import codecs
import re
import sys
from pathlib import Path

DEFAULT_INTEROP_H = Path(__file__).resolve().parents[2] / "libpelorus" / "include" / "pelorus" / "interop.h"

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
    target = Path(path) if path is not None else DEFAULT_INTEROP_H
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
    load_interop_constants(DEFAULT_INTEROP_H)
    print(f"pelorus_interop_consts self-test: ok ({len(_REFUSED) + len(_ACCEPTED)} planted cases, real header)")
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
