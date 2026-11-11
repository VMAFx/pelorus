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
Patch segments retain masked new-side context and added-line origins. Headers
stay blank so diagnostics retain patch line numbers. Hunk markers (`@@`) reset
lexical state; `+++ ` is a file header only immediately after `--- `.
Removed lines and metadata stay blank without affecting lexical state.
This is a bounded lexical scan, with no preprocessor or macro expansion.
C23 digit separators such as 1'000 become unterminated characters and leave
the rest of that line unmasked.
"""

from __future__ import annotations

import argparse
import pathlib
import re


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
    """Mask the new side and carry added origins onto joined splice lines."""
    side = [line[1:] if line.startswith(("+", " ")) else "" for line in lines]
    added: set[int] = set()
    origin = 0
    for i, line in enumerate(lines):
        if i == 0 or not re.search(r"\\[ \t]*\r?$", side[i - 1]):
            origin = i
        if line.startswith("+"):
            added.add(origin + 1)
    return mask_c("\n".join(side)).split("\n"), added


def patch_source(patch: str) -> tuple[str, set[int]]:
    """Return masked new-side text and added origins, preserving patch lines."""
    lines = patch.split("\n")
    out: list[str] = []
    added: set[int] = set()
    start = 0
    for i, line in enumerate(lines):
        new_header = (
            line.startswith("+++ ") and i > 0 and lines[i - 1].startswith("--- ")
        )
        if line.startswith("@@") or new_header:
            masked, origins = _patch_segment(lines[start:i])
            out.extend(masked if start < i else [])
            added.update(start + origin for origin in origins)
            out.append("")
            start = i + 1
    masked, origins = _patch_segment(lines[start:])
    out.extend(masked if start < len(lines) else [])
    added.update(start + origin for origin in origins)
    return "\n".join(out), added


def source_texts(files: pathlib.Path) -> list[tuple[str, str, set[int] | None]]:
    """Read masked C/H files and patch new sides with added-line origins."""
    texts = []
    for path in sorted(files.iterdir()):
        if path.suffix in (".c", ".h", ".patch"):
            raw = path.read_text()
            text, added = (
                patch_source(raw) if path.suffix == ".patch" else (mask_c(raw), None)
            )
            texts.append((path.name, text, added))
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
    return failures + _patch_self_test() + _argument_self_test()


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


def _patch_self_test() -> list[str]:
    """Keep exact new-side text and origins across lexical and splice boundaries."""
    failures: list[str] = []
    patches = {
        "context splice origin": (
            "@@\n x = reader\\\n+(b->mapped_mem);",
            "\nx = reader(b->mapped_mem);\n",
            {2},
        ),
        "added splice origin": (
            "@@\n+x = reader\\\n (b->mapped_mem);",
            "\nx = reader(b->mapped_mem);\n",
            {2},
        ),
        "context closes comment": (
            "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n+/*\n */\n+reader(b->mapped_mem);\n",
            "\n\n\n  \n  \nreader(b->mapped_mem);\n",
            {4, 6},
        ),
        "context opens comment": (
            "@@\n /*\n+mapped_mem\n */\n+x;",
            "\n  \n          \n  \nx;",
            {3, 5},
        ),
        "added pre-increment": ("@@\n+++ i;", "\n++ i;", {2}),
        "added increment": ("--- a/x.c\n+++ b/x.c\n+++i;", "\n\n++i;", {3}),
        "removed and marker": (
            "@@\n-reader(b->mapped_mem);\n\\ No newline at end of file\n+x;",
            "\n\n\nx;",
            {4},
        ),
        "segment reset": (
            "@@\n+/*\n@@\n+reader(b->mapped_mem);\n */",
            "\n/*\n\nreader(b->mapped_mem);\n*/",
            {2, 4},
        ),
    }
    for label, (source, expected, origins) in patches.items():
        masked, added = patch_source(source)
        if (masked, added) != (expected, origins) or masked.count("\n") != source.count(
            "\n"
        ):
            failures.append(f"C patch case failed: {label}: {masked!r}")
    return failures
