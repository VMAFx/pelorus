# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Read strict source text and parse unified patches into independent regions.

Each patch hunk is an independent classification region with its own masked
new-side context and added origins. Header counts delimit each hunk, as git apply
does; header-shaped body lines remain body lines. Lexical and classification
state cannot cross hunks. Missing counts mean one. Empty body lines are context.
Malformed headers, invalid body prefixes, negative counts, incomplete hunks and
patches without hunks fail closed. Outside bodies, only allow-listed file and
index headers, regular-file modes 100644/100755, hunk headers and adjacent end
markers are accepted.
Binary sections can write file content, so other metadata fails closed.
Sources use strict UTF-8, LF-only splitting and only non-control characters, LF
and tab. CRLF and lone CR fail closed: ISO C17 treats CR as a line end, while
git apply splits on LF. Trigraphs fail closed before masking because ISO C17
translates them even in comments and literals; C23 removal does not apply to
FFmpeg 9, whose default is -std=c17. Digraphs, universal character names and
macro bodies are not translated or expanded; measured mapped reads using them
remain refused because the mapped_mem token stays visible.
Only context and added lines enter lexing and splicing, in new-side order; the
splice origin follows the previous new-side line. Removed lines and metadata
are excluded from that input and restored as blank output at patch positions.
Blank prefix offsets retain diagnostic line numbers. The flat patch_source view
is for lexical fixtures.
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import unicodedata

from c_scan import mask_c, patch_segment


class PatchError(ValueError):
    """Carry a patch line number and a fail-closed parsing diagnostic."""


def _hunk_counts(line: str, number: int) -> tuple[int, int] | None:
    """Read old and new counts, rejecting malformed hunk headers."""
    if not line.startswith("@@"):
        return None
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if match is None:
        raise PatchError(f"{number}: unreadable patch hunk header")
    return tuple(int(match[i]) if match[i] is not None else 1 for i in (2, 4))


def _consume_line(line: str, old: int, new: int, number: int) -> tuple[int, int]:
    """Consume one body line, retaining markers and rejecting invalid counts."""
    prefix = line[:1]
    if prefix in ("", " "):
        old, new = old - 1, new - 1
    elif prefix == "-":
        old -= 1
    elif prefix == "+":
        new -= 1
    elif prefix != "\\":
        raise PatchError(f"{number}: unreadable patch hunk body prefix")
    if min(old, new) < 0:
        raise PatchError(f"{number}: unreadable patch hunk negative count")
    return old, new


def _patch_metadata(line: str, marker: bool) -> bool:
    """Allow regular-file metadata shapes and an immediately trailing marker."""
    shapes = (
        r"diff --git a/\S+ b/\S+|index [0-9a-f]+\.\.[0-9a-f]+(?: 100(?:644|755))?"
        r"|new file mode 100(?:644|755)|--- (?:a/\S+|/dev/null)|\+\+\+ b/\S+"
    )
    return re.fullmatch(shapes, line) is not None or (marker and line.startswith("\\ "))


def patch_regions(patch: str) -> list[tuple[str, set[int]]]:
    """Read counted hunks with independent lexical state and patch line offsets."""
    lines = patch.removesuffix("\n").split("\n")
    regions: list[tuple[str, set[int]]] = []
    old = new = start = 0
    seen = False
    marker = -1
    for i, line in enumerate(lines):
        if old or new:
            old, new = _consume_line(line, old, new, i + 1)
            if old == new == 0:
                marker = i + 1
                masked, added = patch_segment(lines[start : i + 1])
                regions.append(
                    ("\n" * start + "\n".join(masked), {start + n for n in added})
                )
        else:
            counts = _hunk_counts(line, i + 1)
            if counts is not None:
                old, new = counts
                start, seen = i + 1, True
            elif not _patch_metadata(line, i == marker):
                raise PatchError(f"{i + 1}: unreadable patch metadata")
    if old or new:
        raise PatchError(f"{len(lines) + 1}: unreadable patch hunk at EOF")
    if not seen:
        raise PatchError("1: unreadable patch hunk: no hunk found")
    return regions


def patch_source(patch: str) -> tuple[str, set[int]]:
    """Combine masked regions for lexical fixtures, retaining every patch line."""
    out = [""] * len(patch.split("\n"))
    added: set[int] = set()
    for text, origins in patch_regions(patch):
        for i, line in enumerate(text.split("\n")):
            if line:
                out[i] = line
        added.update(origins)
    return "\n".join(out), added


def _read_source(path: pathlib.Path) -> str:
    """Decode bytes without newline conversion and reject C phase-one hazards."""
    kind = "patch" if path.suffix == ".patch" else "source"
    try:
        data = path.read_bytes()
        raw = data.decode("utf-8")
    except UnicodeDecodeError as error:
        line = data.count(b"\n", 0, error.start) + 1
        raise PatchError(f"{line}: unreadable {kind} UTF-8") from error
    except OSError as error:
        raise PatchError(f"1: unreadable {kind}: {error.strerror}") from error
    for number, line in enumerate(raw.split("\n"), 1):
        if any(unicodedata.category(c) == "Cc" and c not in "\t" for c in line):
            raise PatchError(f"{number}: unreadable {kind} control character")
        if re.search(r"\?\?[=(/)'<!>-]", line):
            raise PatchError(f"{number}: unreadable {kind} trigraph")
    return raw


def source_texts(
    files: pathlib.Path,
) -> list[tuple[str, str | PatchError, set[int] | None]]:
    """Read source regions and retain patch errors for the caller to report."""
    texts = []
    for path in sorted(files.iterdir()):
        if path.suffix in (".c", ".h", ".patch"):
            try:
                raw = _read_source(path)
                regions = (
                    patch_regions(raw)
                    if path.suffix == ".patch"
                    else [(mask_c(raw), None)]
                )
                texts.extend((path.name, text, added) for text, added in regions)
            except PatchError as error:
                texts.append((path.name, error, None))
    return texts


PATCH_FIXTURES = {
    "metadata between hunks": (
        "@@ -0,0 +1 @@\n+x;\n--- a/y.c\n+++ b/y.c\n@@ -0,0 +1 @@\n+y;",
        "\nx;\n\n\n\ny;",
        {2, 6},
    ),
    "live metadata": (
        "diff --git a/x.c b/x.c\nindex ab..cd\nindex ab..cd 100644\n"
        "new file mode 100644\n--- /dev/null\n+++ b/x.c\n@@ -0,0 +1 @@\n+\tx;",
        "\n" * 7 + "\tx;",
        {8},
    ),
    "completed marker then read": (
        "@@ -0,0 +1 @@\n+x;\n\\ No newline at end of file\n"
        "@@ -9,0 +10 @@\n+x = b->mapped_mem[0];",
        "\nx;\n\n\nx = b->mapped_mem[0];",
        {2, 5},
    ),
    "empty context": ("@@ -1 +1,2 @@ section\n\n+x;", "\n\nx;", {3}),
    "zero counts": ("@@ -0,0 +0,0 @@", "", set()),
    "removed decrement splice": (
        "@@ -1,2 +1,2 @@\n x = b->mapped_\\\n--- old;\n+mem;",
        "\nx = b->mapped_mem;\n\n",
        {2},
    ),
    "removed splice origin": (
        "@@ -1,2 +1,2 @@\n x = b->mapped_\\\n-old;\n+mem;",
        "\nx = b->mapped_mem;\n\n",
        {2},
    ),
    "marker splice origin": (
        "@@ -1,0 +1,2 @@\n+x = b->mapped_\\\n\\ No newline at end of file\n+mem;",
        "\nx = b->mapped_mem;\n\n",
        {2},
    ),
    "context splice origin": (
        "@@ -1,1 +1,2 @@\n x = reader\\\n+(b->mapped_mem);",
        "\nx = reader(b->mapped_mem);\n",
        {2},
    ),
    "added splice origin": (
        "@@ -1,1 +1,2 @@\n+x = reader\\\n (b->mapped_mem);",
        "\nx = reader(b->mapped_mem);\n",
        {2},
    ),
    "context closes comment": (
        "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,3 @@\n+/*\n */\n+reader(b->mapped_mem);\n",
        "\n\n\n  \n  \nreader(b->mapped_mem);\n",
        {4, 6},
    ),
    "context opens comment": (
        "@@ -1,2 +1,4 @@\n /*\n+mapped_mem\n */\n+x;",
        "\n  \n          \n  \nx;",
        {3, 5},
    ),
    "added pre-increment": ("@@ -1,0 +1,1 @@\n+++ i;", "\n++ i;", {2}),
    "added increment": (
        "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+++i;",
        "\n\n\n++i;",
        {4},
    ),
    "removed and marker": (
        "@@ -1,1 +1,1 @@\n-reader(b->mapped_mem);\n\\ No newline at end of file\n+x;",
        "\n\n\nx;",
        {4},
    ),
    "segment reset": (
        "@@ -1,0 +1,1 @@\n+/*\n@@ -1,1 +1,2 @@\n+reader(b->mapped_mem);\n */",
        "\n/*\n\nreader(b->mapped_mem);\n*/",
        {2, 4},
    ),
}


def _patch_self_test() -> list[str]:
    """Keep exact new-side text and origins across lexical and splice boundaries."""
    failures: list[str] = []
    for label, (source, expected, origins) in PATCH_FIXTURES.items():
        try:
            masked, added = patch_source(source)
        except PatchError as error:
            failures.append(f"C patch case failed: {label}: {error}")
            continue
        if (masked, added) != (expected, origins) or masked.count("\n") != source.count(
            "\n"
        ):
            failures.append(f"C patch case failed: {label}: {masked!r}")
    return failures


def _patch_error_self_test() -> list[str]:
    """Require each corrupt patch class to retain a line-numbered read error."""
    failures: list[str] = []
    cases = {
        "incomplete body": ("@@ -1,2 +1,2 @@\n-a\n+z", 4),
        "header at EOF": ("@@ -1 +1 @@", 2),
        "unknown prefix": ("@@ -1,2 +1,2 @@\n a\n*b", 3),
        "negative old count": ("@@ -1,0 +1 @@\n-a\n+z", 2),
        "negative new count": ("@@ -1 +1,0 @@\n+z\n-a", 2),
        "malformed header": ("@@ -0,0 +0,0 @@\n@@ broken\n+x;", 2),
        "no hunk": ("--- a/x.c\n+++ b/x.c", 1),
        "trailing addition": ("@@ -1 +1 @@\n-a\n+z\n+x;", 4),
        "nested header": ("@@ -1 +1 @@\n@@ -2 +2 @@", 2),
    }
    cases.update({line: (line + "\n@@ -0,0 +0,0 @@", 1) for line in BAD_METADATA})
    cases.update(
        {
            "blank between hunks": ("@@ -0,0 +0,0 @@\n\n@@ -0,0 +0,0 @@", 2),
            "marker before hunk": ("\\ No newline at end of file\n@@ -0,0 +0,0 @@", 1),
        }
    )
    sources = [
        (label, "x.patch", raw.encode(), f"{line}: unreadable")
        for label, (raw, line) in cases.items()
    ]
    sources += _reader_cases() + _mode_cases()
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for label, name, data, expected in sources:
            path = root / name
            path.write_bytes(data)
            texts = source_texts(root)
            path.unlink()
            errors = [str(text) for _, text, _ in texts if isinstance(text, PatchError)]
            valid = bool(texts) and not errors
            if expected:
                valid = len(errors) == 1 and errors[0].startswith(expected)
            if not valid:
                failures.append(f"C source case failed: {label}: {errors}")
    return failures


BAD_METADATA = (
    "GIT binary patch",
    "literal 40",
    "delta 40",
    "rename from a.c",
    "rename to b.c",
    "copy from a.c",
    "copy to b.c",
    "similarity index 90%",
    "old mode 100644",
    "new mode 100755",
    "deleted file mode 100644",
    "From: x",
    "diff --git a/x.c b/x.c junk",
    "index zz..yy",
    "+++ /dev/null",
)


def _reader_cases() -> list[tuple[str, str, bytes, str]]:
    """Plant reader hazards across source kinds and retain safe text boundaries."""
    cases = [("CRLF patch", "x.patch", b"@@ -0,0 +1 @@\r\n+x;\r\n", "1: unreadable")]
    for name, prefix in (
        ("x.c", "// head\n"),
        ("x.h", "// head\n"),
        ("x.patch", "@@ -0,0 +1 @@\n+"),
    ):
        for bad in ("\r", "\r\n", "\0", "\f", "\v", "\x1b", "\x7f", "\x85"):
            cases.append(
                (
                    repr(bad),
                    name,
                    (prefix + "a;" + bad + "x;").encode(),
                    "2: unreadable",
                )
            )
        kind = "patch" if name.endswith(".patch") else "source"
        for raw in (
            "x = b->mapped_??/\nmem[0];",
            "char c = '??/''; int x = b->mapped_mem[0];",
            "// ??=",
            "/* ??( */",
            "// ??)",
            "// ??'",
            "// ??<",
            "// ??!",
            "// ??>",
            "// ??-",
            "x ??'= 1; int x = b->mapped_mem[0]; c = '?';",
        ):
            cases.append(
                (raw, name, (prefix + raw).encode(), f"2: unreadable {kind} trigraph")
            )
        raw = "x = b->mapped_\\\rmem[0];"
        cases.append((raw, name, (prefix + raw).encode(), "2: unreadable"))
        cases.append(
            ("invalid UTF-8", name, prefix.encode() + b"\xff", "2: unreadable")
        )
        cases.append(("safe text", name, (prefix + '\t/* — */ "??x";').encode(), ""))
    return cases


def _mode_cases() -> list[tuple[str, str, bytes, str]]:
    """Accept regular-file metadata and reject unsupported modes at their line."""
    cases = []
    for metadata, accepted in (
        ("new file mode 100644", True),
        ("new file mode 100755", True),
        ("index ab..cd 100644", True),
        ("index ab..cd 100755", True),
        ("index ab..cd", True),
        ("new file mode 120000", False),
        ("new file mode 160000", False),
        ("new file mode 100664", False),
        ("new file mode 040000", False),
        ("new file mode junk", False),
        ("new file mode ", False),
        ("index ab..cd 120000", False),
        ("index ab..cd 160000", False),
        ("index ab..cd 100664", False),
    ):
        raw = f"diff --git a/x.c b/x.c\n{metadata}\n@@ -0,0 +1 @@\n+x;"
        expected = "" if accepted else "2: unreadable patch metadata"
        cases.append((metadata, "x.patch", raw.encode(), expected))
    return cases


def self_test() -> list[str]:
    """Exercise patch boundaries and strict source reading with exact diagnostics."""
    return _patch_self_test() + _patch_error_self_test()
