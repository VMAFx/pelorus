# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Read strict UTF-8 sources and independent counted unified-patch hunks.

Reject controls except tab, Unicode format characters, trigraphs, digraphs and
pastes before masking. LF alone splits lines; patch metadata is allow-listed.
Detailed translation and region contracts: docs/adr/0255-mapped-memory-host-reads.md.
"""

from __future__ import annotations

import pathlib
import re
import tempfile
import unicodedata
from collections.abc import Callable
from typing import NamedTuple

from c_scan import (
    PatchError,
    alias_directive_cases,
    alias_patch_cases,
    check_spliced,
    mapped_aliases,
    mask_c,
    mode_cases,
    patch_segment,
    preprocessing_hazard,
    scan_directives,
    splice_cases,
)


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


def _patch_segment_at(
    lines: list[str], start: int, i: int
) -> tuple[list[str], set[int]]:
    """Translate a hunk-local refusal into its physical patch line number."""
    try:
        masked, added = patch_segment(lines[start : i + 1])
    except PatchError as error:
        number, message = str(error).split(":", 1)
        raise PatchError(f"{start + int(number)}:{message}") from error
    return masked, added


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
                masked, added = _patch_segment_at(lines, start, i)
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
        categories = {unicodedata.category(c) for c in line if c not in "\t"}
        hazards = categories.intersection(("Cc", "Cf"))
        if hazards:
            reason = {"Cc": "control", "Cf": "format"}[min(hazards)]
            raise PatchError(f"{number}: unreadable {kind} {reason} character")
        if re.search(r"\?\?[=(/)'<!>-]", line):
            raise PatchError(f"{number}: unreadable {kind} trigraph")
        hazard = preprocessing_hazard(line)
        if hazard:
            raise PatchError(f"{number}: unreadable {kind} {hazard[1]}")
    if kind == "source":
        check_spliced(raw, kind)
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
                aliases = set().union(*(mapped_aliases(text) for text, _ in regions))
                for text, added in regions:
                    errors = scan_directives(text, aliases)[1]
                    texts.append((path.name, text, added))
                    texts.extend(
                        (path.name, PatchError(error), None) for error in errors
                    )
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
    sources += _reader_cases() + mode_cases() + _preprocessing_cases() + splice_cases()
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
            raw = (prefix + "a;" + bad + "x;").encode()
            cases.append((repr(bad), name, raw, "2: unreadable"))
        kind = "patch" if name.endswith(".patch") else "source"
        for raw in (
            "x = b->mapped_??/\nmem[0];",
            "char c = '??/''; int x = b->mapped_mem[0];",
            "/* ??( */",
            *("// ??" + token for token in "=)'<!>-"),
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


def _preprocessing_cases() -> list[tuple[str, str, bytes, str]]:
    """Reject raw digraphs and token pastes in each source kind at line two."""
    cases = []
    hazards = [(token, "digraph") for token in ("<:", ":>", "<%", "%>", "%:", "%:%:")]
    hazards += [("##", "token paste")]
    hazards += [
        (
            "pel_mc_build_predictors(hosts<:i = 0, j = 0, k:>, "
            "(const int32_t *)b->mapped_mem, n, prev, &gx, &gy);",
            "digraph",
        ),
        ("{ T *p = (T *)b->mapped_mem; <% y = 1; } consume(p); }", "digraph"),
        ("#define CAT(a, b) a##b\nx = b->CAT(mapped_, mem)[0];", "token paste"),
    ]
    for name, prefix in (
        ("x.c", "// head\n"),
        ("x.h", "// head\n"),
        ("x.patch", "@@ -0,0 +1 @@\n+"),
    ):
        kind = "patch" if name.endswith(".patch") else "source"
        for raw, hazard in hazards:
            for source in (raw, f'"{raw}";', f"/* {raw} */"):
                cases.append(
                    (
                        source,
                        name,
                        (prefix + source).encode(),
                        f"2: unreadable {kind} {hazard}",
                    )
                )
        for safe in ("a ? b : c;", "a < b ? c : d;", "x % y;", "a[i] ? b : c;"):
            cases.append((safe, name, (prefix + safe).encode(), ""))
    return cases


def _directive_self_test() -> list[str]:
    """Check directive diagnostics through real C, header and patch files."""
    cases = (
        ("#define MM mapped_mem", True),
        ("#define MM mapped_mem\nx = b->MM[0];", True),
        ("#define MM \\\n mapped_mem", True),
        ("  # define MM mapped_mem", True),
        ("#include <stdint.h>", False),
        ("#if HAVE_X\n#endif", False),
        ("#define MM mapped", False),
        ('#define MM "mapped_mem"', False),
        ("#define MM /* mapped_mem */ x", False),
    )
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for name in ("x.c", "x.h", "x.patch"):
            for source, refused in cases + alias_directive_cases():
                raw = "// head\n" + source
                line = 2
                if name.endswith(".patch"):
                    count = source.count("\n") + 1
                    raw = f"--- a/x.c\n+++ b/x.c\n@@ -0,0 +1,{count} @@\n+"
                    raw += source.replace("\n", "\n+")
                    line = 4
                path = root / name
                path.write_bytes(raw.encode())
                texts = source_texts(root)
                path.unlink()
                line += next(
                    i
                    for i, part in enumerate(source.split("\n"))
                    if part.lstrip().startswith("#")
                )
                expected = [f"{line}: mapped_mem in preprocessor directive"]
                errors = [
                    str(text) for _, text, _ in texts if isinstance(text, PatchError)
                ]
                if errors != (expected if refused else []):
                    failures.append(f"C directive case failed: {source}: {errors}")
    return failures + _directive_patch_self_test()


def _directive_patch_self_test() -> list[str]:
    """Keep context directives separate and mark added continuations at their origin."""
    cases = (
        ("@@ -1 +1,2 @@\n #define MM mapped_mem\n+x = 1;", [2]),
        ("@@ -1 +1,2 @@\n #define MM \\\n+mapped_mem", [2]),
        ("@@ -1 +1,2 @@\n+#define MM \\\n mapped_mem", [2]),
    )
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for raw, lines in cases + alias_patch_cases():
            (root / "x.patch").write_bytes(raw.encode())
            errors = [
                str(text)
                for _, text, _ in source_texts(root)
                if isinstance(text, PatchError)
            ]
            expected = [
                f"{line}: mapped_mem in preprocessor directive" for line in lines
            ]
            if errors != expected:
                failures.append(f"C directive patch case failed: {raw!r}: {errors}")
    return failures


def self_test() -> list[str]:
    """Exercise patch boundaries and strict source reading with exact diagnostics."""
    return _patch_self_test() + _patch_error_self_test() + _directive_self_test()


def lexical_cases() -> list[tuple[str, str, str, str]]:
    """Plant assignment, loop and format-character refusals with exact origins."""
    assignment = " uint8_t *p;\n p = (uint8_t *)b->mapped_mem;\n"
    const = " const uint8_t *p = (const uint8_t *)b->mapped_mem;\n"
    loop = " for (uint32_t *p = (uint32_t *)b->mapped_mem; p < end; ++p) {\n"
    header = "diff --git a/x.c b/x.c\nindex ab..cd 100644\n--- a/x.c\n+++ b/x.c\n"
    bodies = (
        (
            "assigned directive",
            assignment + "+#define P p[0]",
            "8: mapped_mem;7: element-wise",
        ),
        (
            "chained context directive",
            assignment.replace(" p =", " q = p =") + " #define P p[0]\n+x = P;",
            "8: mapped_mem;7: element-wise",
        ),
        ("assignment read", assignment + "+x = p[0];", "7: element-wise"),
        ("const read", const + "+x = p[0];", "6: element-wise"),
        ("for-init read", loop + "+x = p[0];\n }", "6: element-wise"),
        ("assignment outside block", " {\n" + assignment + " }\n+{ y = 1; }", ""),
        (
            "for-init after loop",
            " {\n" + loop + " }\n+{ y = 1; }\n }",
            "7: element-wise",
        ),
        ("for-init outside block", " {\n" + loop + " }\n }\n+{ y = 1; }", ""),
        ("format added", "+x = \u200b1;", "6: unreadable patch format character"),
        (
            "format context",
            " // \u200b\n+x = 1;",
            "6: unreadable patch format character",
        ),
    )
    cases = []
    for label, body, expected in bodies:
        lines = body.split("\n")
        old = sum(line.startswith(" ") for line in lines)
        patch = header + f"@@ -1,{old} +1,{len(lines)} @@\n" + body
        cases.append((label, "x.patch", patch, expected))
    bom = "\ufeff#define P p\nvoid f(B *b) {\n" + const[1:] + "x = P[0];\n}"
    bom = bom.replace("const ", "")
    cases.append(("BOM source", "x.c", bom, "1: unreadable source format character"))
    header = header.replace("index ab", "new file mode 100644\nindex 0000000")
    header = header.replace("--- a/x.c", "--- /dev/null")
    patch = header + "@@ -0,0 +1,5 @@\n+" + bom.replace("\n", "\n+")
    cases.append(
        ("BOM patch", "x.patch", patch, "7: unreadable patch format character")
    )
    return cases


def lexical_self_test(
    check: Callable[[list[tuple[str, str | PatchError, set[int] | None]]], list[str]],
) -> list[str]:
    """Require exact diagnostic counts and numbered refusals through the checker."""
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for label, name, raw, expected in lexical_cases():
            path = root / name
            path.write_bytes(raw.encode())
            errors = check(source_texts(root))
            path.unlink()
            wanted = expected.split(";") if expected else []
            if len(errors) != len(wanted) or not all(
                error.split(":", 1)[1].startswith(part)
                for part, error in zip(wanted, errors)
            ):
                failures.append(f"lexical case failed: {label}: {errors}")
    return failures


class KnownRead(NamedTuple):
    """One exact source statement with a dated MEL exception."""

    file: str
    statement: str
    issue: int
    category: str
    entered: str
    reason: str
