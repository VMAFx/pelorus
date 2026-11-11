# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Mask C text so comments and literals neither hide nor fabricate code tokens.

Splicing precedes comment and literal recognition, as in C translation phases
2 and 3. Swallowed newlines move after the next surviving newline, or to EOF.
Comments become spaces with their newlines retained; literals retain their
quotes and replace contents, including escapes, with spaces. Other than splice
removal and newline relocation, masking preserves text length.
Unterminated literals remain unmasked from their opening quote to an unescaped
newline or EOF. Unterminated block comments remain unmasked through EOF.
Each patch hunk is an independent classification region with its own masked
new-side context and added origins. Header counts delimit each hunk, as git apply
does; header-shaped body lines remain body lines. Lexical and classification
state cannot cross hunks. Missing counts mean one. Empty body lines are context.
Malformed headers, invalid body prefixes, negative counts, incomplete hunks and
patches without hunks fail closed. Outside bodies, only allow-listed file and
index headers, new-file modes, hunk headers and adjacent end markers are accepted.
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
This is a bounded lexical scan, with no preprocessor or macro expansion.
C23 digit separators such as 1'000 become unterminated characters and leave
the rest of that line unmasked.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import tempfile
import unicodedata


MAX_CALL_CHARS = 4096


def statement_at(text: str, pos: int) -> tuple[str, int]:
    """Return collapsed statement and end offset, retaining its semicolon."""

    start, end = statement_bounds(text, pos)
    return " ".join(text[start:end].split()), end


def statement_bounds(text: str, pos: int) -> tuple[int, int]:
    """Return the offsets enclosing the statement at a token."""
    start = max(text.rfind(delimiter, 0, pos) for delimiter in ";{}") + 1
    start += len(text[start:pos]) - len(text[start:pos].lstrip())
    end = text.find(";", pos)
    return start, len(text) if end < 0 else end + 1


def added_overlap(text: str, start: int, end: int, added: set[int] | None) -> bool:
    """Require an added output line in the classified region of a patch."""
    if added is None:
        return True
    first = text.count("\n", 0, start) + 1
    last = text.count("\n", 0, max(start, end - 1)) + 1
    return any(first <= line <= last for line in added)


def statement_start(text: str, pos: int) -> bool:
    """Require a position to begin a statement rather than a subexpression."""
    prefix = text[:pos].rstrip()
    return (
        not prefix or prefix[-1] in ";{})" or bool(re.search(r"\b(?:else|do)$", prefix))
    )


def block_tail(text: str, start: int) -> str | None:
    """Return later text up to the enclosing block's closing brace."""

    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            if depth == 0:
                return text[start:pos]
            depth -= 1
    return None


def argument_spans(text: str, start: int) -> list[tuple[int, int]] | None:
    """Locate arguments using one bounded scan over all C nesting delimiters."""
    depth = 0
    spans: list[tuple[int, int]] = []
    arg_start = start + 1
    end = min(len(text), start + MAX_CALL_CHARS)
    for pos in range(start, end):
        char = text[pos]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0 and char == ")":
                return [*spans, (arg_start, pos)]
        elif char == "," and depth == 1:
            spans.append((arg_start, pos))
            arg_start = pos + 1
    return None


def call_args(text: str, start: int) -> tuple[list[str], int] | None:
    """Return top-level arguments and the offset after the closing parenthesis."""
    spans = argument_spans(text, start)
    if spans is None:
        return None
    return [text[left:right].strip() for left, right in spans], spans[-1][1] + 1


def argument_index(text: str, start: int, pos: int) -> int:
    """Find a token's argument using the same nesting scan as argument splitting."""
    spans = argument_spans(text, start) or []
    return next((i for i, (left, right) in enumerate(spans) if left <= pos < right), -1)


def enclosing_call(text: str, pos: int) -> tuple[str, int] | None:
    """Find the nearest enclosing call, crossing casts and grouping parentheses."""

    depth = 0
    for index in range(pos - 1, max(-1, pos - MAX_CALL_CHARS - 1), -1):
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth:
                depth -= 1
            else:
                prefix = text[max(0, index - MAX_CALL_CHARS) : index]
                name = re.search(r"\b(\w+)\s*$", prefix)
                if name:
                    member = re.search(r"(?:->|\.)\s*$", prefix[: name.start()])
                    return ("" if member else name[1]), index
        elif depth == 0 and char in ";{}":
            return None
    return None


def resolve(text: str, arg: str, pos: int) -> str | None:
    """Resolve local flags at ``pos``; retain constants and fail on unknown names."""

    if not re.fullmatch(r"[a-z_]\w*", arg):
        return arg
    name = re.escape(arg)
    found = list(re.finditer(rf"\b{name}\s*=(?!=)\s*([^;]*);", text[:pos]))
    if not found:
        return None
    last = found[-1]
    more = re.findall(rf"\b{name}\s*\|=\s*([^;]*);", text[last.end() : pos])
    return " | ".join([last.group(1), *more])


def parse_args(
    description: str, files: pathlib.Path, argv: list[str] | None = None
) -> argparse.Namespace:
    """Parse source-checker options for a selected default directory."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--self-test", action="store_true", help="run self-test suite")
    parser.add_argument(
        "--files",
        type=pathlib.Path,
        default=files,
        metavar="DIR",
        help="source directory for allocation and mapped-read checks",
    )
    return parser.parse_args(argv)


def pointer_expression(arg: str, cast: str, chain: str, token: str) -> bool:
    """Accept only a cast/grouped member chain containing one named token."""

    if len(re.findall(rf"\b{re.escape(token)}\b", arg)) != 1:
        return False
    plain = re.sub(cast, "", arg)
    if re.search(r"[\w)\]]\s*\(", plain):
        return False
    plain = re.sub(r"[()\s]", "", plain)
    return re.fullmatch(chain, plain) is not None


def store_only_tail(text: str, alias: str, start: int, store: str) -> tuple[bool, int]:
    """Check later alias uses for plain stores and return the scanned region end."""
    tail = block_tail(text, start)
    if tail is None:
        return False, len(text)
    uses = list(re.finditer(rf"\b{re.escape(alias)}\b", tail))
    allowed = all(
        re.match(store, tail[use.end() :]) and statement_start(tail, use.start())
        for use in uses
    )

    return allowed, start + len(tail)


def _splice(text: str) -> str:
    """Join escaped newlines and defer their line count to the next newline."""
    out: list[str] = []
    start = pending = 0
    for match in re.finditer(r"\\[ \t]*\r?\n|\n", text):
        out.append(text[start : match.start()])
        if match[0] == "\n":
            out.append("\n" * (pending + 1))
            pending = 0
        else:
            pending += 1
        start = match.end()
    return "".join(out) + text[start:] + "\n" * pending


def _literal(text: str, start: int) -> tuple[str, int]:
    """Mask one string or character literal, retaining malformed input."""
    escaped = False
    for pos in range(start + 1, len(text)):
        char = text[pos]
        if char == "\n":
            return text[start:pos], pos
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == text[start]:
            return char + " " * (pos - start - 1) + char, pos + 1
    return text[start:], len(text)


def _comment(text: str, start: int, kind: str) -> tuple[str, int]:
    """Mask line or block comment state, preserving unterminated block text."""
    end = text.find("\n" if kind == "//" else "*/", start + 2)
    if end < 0 and kind == "/*":
        return text[start:], len(text)
    end = len(text) if end < 0 else end + (2 if kind == "/*" else 0)
    return re.sub(r"[^\n]", " ", text[start:end]), end


def mask_c(text: str) -> str:
    """Mask comments and literals in one bounded pass after line splicing."""
    text = _splice(text)
    out: list[str] = []
    pos = 0
    for _ in range(len(text) + 1):
        if pos >= len(text):
            break
        pair = text[pos : pos + 2]
        if pair in ("//", "/*"):
            comment, pos = _comment(text, pos, pair)
            out.append(comment)
        elif text[pos] in "\"'":
            literal, pos = _literal(text, pos)
            out.append(literal)
        else:
            out.append(text[pos])
            pos += 1
    return "".join(out)


def _patch_segment(lines: list[str]) -> tuple[list[str], set[int]]:
    """Lex only new-side lines, then restore blanks at excluded patch positions."""
    positions = [
        i for i, line in enumerate(lines) if not line or line.startswith(("+", " "))
    ]
    side = [lines[i][1:] for i in positions]
    added: set[int] = set()
    origin = 0
    for i, pos in enumerate(positions):
        if i == 0 or not re.search(r"\\[ \t]*\r?$", side[i - 1]):
            origin = pos
        if lines[pos].startswith("+"):
            added.add(origin + 1)
    out = [""] * len(lines)
    for pos, masked in zip(positions, mask_c("\n".join(side)).split("\n")):
        out[pos] = masked
    return out, added


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
    """Allow only known metadata shapes and an immediately trailing marker."""
    shapes = (
        r"diff --git a/\S+ b/\S+|index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?"
        r"|new file mode [0-7]{6}|--- (?:a/\S+|/dev/null)|\+\+\+ b/\S+"
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
                masked, added = _patch_segment(lines[start : i + 1])
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


def self_test() -> list[str]:
    """Exercise literal, comment, splice and patch boundaries with exact output."""
    cases = {
        "escaped quote": (r'"a\"}"', '"    "'),
        "escaped character": (r"'\''", "'  '"),
        "string slash": (
            '"//"; reader(b->mapped_mem);',
            '"  "; reader(b->mapped_mem);',
        ),
        "comment quote": ('/* " */x', "       x"),
        "comment token boundary": ("mapped/**/_mem", "mapped    _mem"),
        "line comment": ("a// mapped_mem\nb", "a             \nb"),
        "block lines": ("a/*\nx*/b", "a  \n   b"),
        "unterminated block": (
            "/* reader(b->mapped_mem);",
            "/* reader(b->mapped_mem);",
        ),
        "unterminated character": (
            "x = 'a; reader(b->mapped_mem);\nx;",
            "x = 'a; reader(b->mapped_mem);\nx;",
        ),
        "literal newline stop": (
            "x = 'a; reader(b->mapped_mem);\ny = 'b';",
            "x = 'a; reader(b->mapped_mem);\ny = ' ';",
        ),
        "unterminated string": (
            '"reader(b->mapped_mem);\nx;',
            '"reader(b->mapped_mem);\nx;',
        ),
        "unterminated EOF": ('"mapped_mem', '"mapped_mem'),
        "digit separator": (
            "1'000; reader(b->mapped_mem);",
            "1'000; reader(b->mapped_mem);",
        ),
        "splice EOF": ("mapped_\\\nmem", "mapped_mem\n"),
        "splice newline": ("mapped_\\ \t\r\nmem;\nx;", "mapped_mem;\n\nx;"),
        "multiple splices": ("map\\\nped_\\\nmem;\nx;", "mapped_mem;\n\n\nx;"),
        "spliced comment": ("// hide\\\n mapped_mem\nx;", "                  \n\nx;"),
    }
    failures = []
    for label, (source, expected) in cases.items():
        masked = mask_c(source)
        if masked != expected or masked.count("\n") != source.count("\n"):
            failures.append(f"C masking case failed: {label}: {masked!r}")
    return (
        failures + _patch_self_test() + _argument_self_test() + _patch_error_self_test()
    )


def _argument_self_test() -> list[str]:
    """Assert shared argument boundaries across subscripts and brace expressions."""
    failures: list[str] = []
    for first in ("hosts[i = 0, j = 0, k]", "(S){a, b, c}.p"):
        text = f"({first}, mapped_mem, n)"
        parsed = call_args(text, 0)
        index = argument_index(text, 0, text.index("mapped_mem"))
        if parsed != ([first, "mapped_mem", "n"], len(text)) or index != 1:
            failures.append(f"C argument case failed: {first}: {parsed}, {index}")
    return failures


PATCH_FIXTURES = {
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
        (label, "x.patch", raw.encode(), line) for label, (raw, line) in cases.items()
    ]
    sources += _reader_cases()
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for label, name, data, line in sources:
            path = root / name
            path.write_bytes(data)
            texts = source_texts(root)
            path.unlink()
            errors = [str(text) for _, text, _ in texts if isinstance(text, PatchError)]
            valid = bool(texts) and not errors
            if line:
                valid = len(errors) == 1 and errors[0].startswith(f"{line}: unreadable")
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


def _reader_cases() -> list[tuple[str, str, bytes, int]]:
    """Plant reader hazards across source kinds and retain safe text boundaries."""
    cases = []
    for name, prefix in (
        ("x.c", "// head\n"),
        ("x.h", "// head\n"),
        ("x.patch", "@@ -0,0 +1 @@\n+"),
    ):
        for bad in ("\r", "\r\n", "\0", "\f", "\v", "\x1b", "\x7f", "\x85"):
            cases.append((repr(bad), name, (prefix + "a;" + bad + "x;").encode(), 2))
        for raw in (
            "x = b->mapped_\\\rmem[0];",
            "x = b->mapped_??/\nmem[0];",
            "char c = '??/''; int x = b->mapped_mem[0];",
            "// ??=",
            "/* ??( */",
        ):
            cases.append((raw, name, (prefix + raw).encode(), 2))
        cases.append(("invalid UTF-8", name, prefix.encode() + b"\xff", 2))
        cases.append(("safe text", name, (prefix + '\t/* — */ "??x";').encode(), 0))
    return cases
