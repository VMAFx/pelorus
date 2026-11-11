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
Patch segments retain new-side context during masking, then blank context and
headers so diagnostics retain patch line numbers. Hunk markers (`@@`) reset
lexical state; `+++ ` is a file header only immediately after `--- `.
Removed lines and metadata stay blank without affecting lexical state.
This is a bounded lexical scan, with no preprocessor or macro expansion.
C23 digit separators such as 1'000 become unterminated characters and leave
the rest of that line unmasked.
"""

from __future__ import annotations

import re


MAX_CALL_CHARS = 4096


def statement_at(text: str, pos: int) -> tuple[str, int]:
    """Return collapsed statement and end offset, retaining its semicolon."""

    start = max(text.rfind(delimiter, 0, pos) for delimiter in ";{}") + 1
    end = text.find(";", pos)
    end = len(text) if end < 0 else end + 1
    return " ".join(text[start:end].split()), end


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


def call_args(text: str, start: int) -> tuple[list[str], int] | None:
    """Return top-level arguments and the offset after the closing parenthesis."""

    depth = 0
    args: list[str] = []
    arg_start = start + 1
    end = min(len(text), start + MAX_CALL_CHARS)
    for pos in range(start, end):
        char = text[pos]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                args.append(text[arg_start:pos].strip())
                return args, pos + 1
        elif char == "," and depth == 1:
            args.append(text[arg_start:pos].strip())
            arg_start = pos + 1
    return None


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


def _patch_segment(lines: list[str]) -> list[str]:
    """Mask a complete new-side segment before blanking its context lines."""
    side = [line[1:] if line.startswith(("+", " ")) else "" for line in lines]
    masked = mask_c("\n".join(side)).split("\n")
    return [masked[i] if line.startswith("+") else "" for i, line in enumerate(lines)]


def patch_additions(patch: str) -> str:
    """Return masked additions with one output line for each patch input line."""
    lines = patch.split("\n")
    out: list[str] = []
    start = 0
    for i, line in enumerate(lines):
        new_header = (
            line.startswith("+++ ") and i > 0 and lines[i - 1].startswith("--- ")
        )
        if line.startswith("@@") or new_header:
            out.extend(_patch_segment(lines[start:i]))
            out.append("")
            start = i + 1
    out.extend(_patch_segment(lines[start:]))
    return "\n".join(out)


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
    return failures + _patch_self_test()


def _patch_self_test() -> list[str]:
    """Keep context for lexical state without reporting it as added code."""
    failures: list[str] = []
    patches = {
        "context closes comment": (
            "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n+/*\n */\n+reader(b->mapped_mem);\n",
            "\n\n\n  \n\nreader(b->mapped_mem);\n",
        ),
        "context opens comment": (
            "@@\n /*\n+mapped_mem\n */\n+x;",
            "\n\n          \n\nx;",
        ),
        "added increment": ("--- a/x.c\n+++ b/x.c\n+++i;", "\n\n++i;"),
        "removed and marker": (
            "@@\n-reader(b->mapped_mem);\n\\ No newline at end of file\n+x;",
            "\n\n\nx;",
        ),
        "segment reset": (
            "@@\n+/*\n@@\n+reader(b->mapped_mem);\n */",
            "\n/*\n\nreader(b->mapped_mem);\n",
        ),
    }
    for label, (source, expected) in patches.items():
        masked = patch_additions(source)
        if masked != expected or masked.count("\n") != source.count("\n"):
            failures.append(f"C patch case failed: {label}: {masked!r}")
    return failures
