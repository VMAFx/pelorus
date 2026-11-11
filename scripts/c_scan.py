# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Mask C and bound lexical regions; contracts: ADR-0255, source I/O: c_source.py."""

from __future__ import annotations

import argparse
import pathlib
import re


MAX_CALL_CHARS = 4096
CAST = r"\(\s*(?:const\s+)?\w+(?:\s+\w+)*\s*\*+\s*\)"


class PatchError(ValueError):
    """Carry a physical line number and a fail-closed reading diagnostic."""


def preprocessing_hazard(line: str) -> tuple[int, str] | None:
    """Identify unsupported preprocessing tokens before comments or literals mask them."""
    match = re.search(r"<:|:>|<%|%>|%:", line)
    if match:
        return match.start(), "digraph"
    if "##" in line:
        return line.index("##"), "token paste"
    return None


def check_spliced(text: str, kind: str, positions: list[int] | None = None) -> None:
    """Refuse joined hazards at the physical origin of their first character."""
    origins: list[int] = []
    joined = _splice(text, origins)
    for match in re.finditer(r"[^\n]+", joined):
        hazard = preprocessing_hazard(match[0])
        if hazard:
            offset, reason = hazard
            line = text.count("\n", 0, origins[match.start() + offset])
            number = line + 1 if positions is None else positions[line] + 1
            raise PatchError(f"{number}: unreadable {kind} {reason}")


def statement_at(text: str, pos: int) -> tuple[str, int]:
    """Return collapsed statement and end offset, retaining its semicolon."""

    start, end = statement_bounds(text, pos)
    return " ".join(text[start:end].split()), end


def statement_bounds(text: str, pos: int) -> tuple[int, int]:
    """Return the offsets enclosing the statement at a token."""
    start = _statement_boundary(text, pos)
    start += len(text[start:pos]) - len(text[start:pos].lstrip())
    end = text.find(";", pos)
    return start, len(text) if end < 0 else end + 1


def _statement_boundary(text: str, pos: int) -> int:
    """Find only semicolon or brace boundaries outside nested argument spans."""
    start = skip = 0
    for match in re.finditer(r"[();{}]", text[:pos]):
        if match.start() < skip:
            continue
        if match[0] == "(":
            spans = argument_spans(text, match.start(), len(text))
            if spans:
                skip = spans[-1][1] + 1
        elif match[0] in ";{}":
            start = match.end()
    return start


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


def argument_spans(
    text: str, start: int, max_chars: int = MAX_CALL_CHARS
) -> list[tuple[int, int]] | None:
    """Locate arguments using one bounded scan over all C nesting delimiters."""
    depth = 0
    spans: list[tuple[int, int]] = []
    arg_start = start + 1
    end = min(len(text), start + max_chars)
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


def _splice(text: str, origins: list[int] | None = None) -> str:
    """Join escaped newlines and defer their line count to the next newline."""
    out: list[str] = []
    start = pending = 0
    for match in re.finditer(r"\\[ \t]*\r?\n|\n", text):
        out.append(text[start : match.start()])
        if origins is not None:
            origins.extend(range(start, match.start()))
        if match[0] == "\n":
            out.append("\n" * (pending + 1))
            if origins is not None:
                origins.extend([match.start()] * (pending + 1))
            pending = 0
        else:
            pending += 1
        start = match.end()
    if origins is not None:
        origins.extend(range(start, len(text)))
        origins.extend([len(text)] * pending)
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


def mapped_aliases(text: str) -> set[str]:
    """Collect declared or assigned mapped aliases, including chained assignments."""
    return set(re.findall(r"(?=\b(\w+)\s*=(?!=)[^;{}]*\bmapped_mem\b)", text))


def mapped_binding(text: str, pos: int) -> str | None:
    """Find the nearest assignment name, crossing parentheses belonging to casts."""
    prefix = re.split(r"[;{}(]", re.sub(CAST, "", text[:pos]))[-1]
    binding = re.search(r"\b(\w+)\s*=(?!=)[^;{}=]*$", prefix)
    return binding[1] if binding else None


def binding_tail(text: str, pos: int, end: int) -> int:
    """Extend refused bindings through the block tail on either classifier path."""
    if mapped_binding(text, pos) is None:
        return end
    tail = block_tail(text, end)
    return len(text) if tail is None else end + len(tail)


def directive_alias(text: str, aliases: set[str]) -> bool:
    """Check define replacements, excluding macro names and formal parameters."""
    name = r"(?:\w|\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8})+"
    define = re.match(rf"\s*#\s*define\s+{name}(?:\([^\n)]*\))?(.*)", text)
    return bool(define and aliases.intersection(re.findall(r"\b\w+\b", define[1])))


def scan_directives(
    text: str, aliases: set[str] | None = None
) -> tuple[str, list[str]]:
    """Refuse mapped directives regardless of added lines, then blank their text."""
    errors = []
    aliases = mapped_aliases(text) if aliases is None else aliases
    pattern = r"(?m)^[ \t]*#[^\n]*"
    for match in re.finditer(pattern, text):
        mapped = re.search(r"\bmapped_mem\b", match[0]) or directive_alias(
            match[0], aliases
        )
        if mapped:
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{line}: mapped_mem in preprocessor directive")
    return re.sub(pattern, lambda match: " " * len(match[0]), text), errors


def patch_segment(lines: list[str]) -> tuple[list[str], set[int]]:
    """Lex only new-side lines, then restore blanks at excluded patch positions."""
    positions = [
        i for i, line in enumerate(lines) if not line or line.startswith(("+", " "))
    ]
    side = [lines[i][1:] for i in positions]
    check_spliced("\n".join(side), "patch", positions)
    added: set[int] = set()
    origin = 0
    for i, pos in enumerate(positions):
        if i == 0 or not re.search(r"\\[ \t]*$", side[i - 1]):
            origin = pos
        if lines[pos].startswith("+"):
            added.add(origin + 1)
    out = [""] * len(lines)
    for pos, masked in zip(positions, mask_c("\n".join(side)).split("\n")):
        out[pos] = masked
    return out, added


def self_test() -> list[str]:
    """Exercise literal, comment and splice boundaries with exact output."""
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
    return failures + _argument_self_test() + _statement_self_test()


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


def splice_cases() -> list[tuple[str, str, bytes, str]]:
    """Plant joined hazards and safe continuations with physical line origins."""
    cases = []
    hazards = [(a + "\\\n" + b, "digraph") for a, b in ("<%", "%>", "<:", ":>", "%:")]
    hazards += [
        ("#define CAT(a, b) a#\\\n#b\nx = b->CAT(mapped_, mem)[0];", "token paste"),
        ("#\\\n#", "token paste"),
        ("{ T *p = (T *)b->mapped_mem; <\\\n% y = 1; } consume(p); }", "digraph"),
    ]
    hazards = [
        (source, hazard)
        for raw, hazard in hazards
        for source in (raw, f'"{raw}";', f"/* {raw} */")
    ]
    safe = (
        "AV_PIX_FMT_NV12, \\\n AV_PIX_FMT_NONE",
        "a <\\\n b",
        "x %\\\n y",
        "a ? b :\\\n c",
    )
    for name in ("x.c", "x.h", "x.patch"):
        kind = "patch" if name.endswith(".patch") else "source"
        for raw, hazard in hazards + [(raw, "") for raw in safe]:
            source = "// head\n" + raw
            if kind == "patch":
                count = raw.count("\n") + 1
                source = f"@@ -0,0 +1,{count} @@\n+" + raw.replace("\n", "\n+")
            expected = f"2: unreadable {kind} {hazard}" if hazard else ""
            cases.append((raw, name, source.encode(), expected))
    return cases + _splice_origin_cases()


def _splice_origin_cases() -> list[tuple[str, str, bytes, str]]:
    """Keep excluded patch lines and physical origins independent of joined text."""
    return [
        (
            "context paste",
            "x.patch",
            b"@@ -1 +1,2 @@\n+a#\\\n #b",
            "2: unreadable patch token paste",
        ),
        ("removed paste", "x.patch", b"@@ -1 +1,2 @@\n+a#\\\n-#b\n+safe", ""),
        (
            "later physical origin",
            "x.c",
            b"a\\\nb<\\\n%",
            "2: unreadable source digraph",
        ),
        (
            "later patch origin",
            "x.patch",
            b"@@ -1 +1,3 @@\n+a\\\n-old\n+b<\\\n+%",
            "4: unreadable patch digraph",
        ),
    ]


def alias_directive_cases() -> tuple[tuple[str, bool], ...]:
    """Refuse replacement aliases; accept formal parameters of UCN-named macros."""
    declaration = "{ uint32_t *p = (uint32_t *)b->mapped_mem;\n"
    ucn = chr(92) + "u00e4"
    long_ucn = chr(92) + "U000000e4"
    return (
        (declaration + "#define P p\nuint32_t x = P[0]; }", True),
        (declaration + "#define G(i) p[i]\nuint32_t x = G(0); }", True),
        (declaration + "#define G(i)p[i]\nuint32_t x = G(0); }", True),
        ("#define P p\n" + declaration + "uint32_t x = P[0]; }", True),
        (declaration + "#define Q q\np[0] = 1; }", False),
        (declaration + "#define G(p) 1\np[0] = 1; }", False),
        (declaration + f"#define G{ucn}(p) 1\np[0] = 1; }}", False),
        (declaration + f"#define G{long_ucn}(p) 1\np[0] = 1; }}", False),
        (declaration + "#define P+p\nuint32_t x = 0 P[0]; (void)x; }", True),
        (declaration + f"#define P{ucn} p\nuint32_t x = P{ucn}[0]; (void)x; }}", True),
        (
            declaration
            + f"#define G{ucn}(i) p[i]\nuint32_t x = G{ucn}(0); (void)x; }}",
            True,
        ),
        (declaration + "#define P pp\np[0] = 1; }", False),
    )


def _statement_self_test() -> list[str]:
    """Retain added call text and bound delimiter starts by the flat boundary."""
    failures = []
    firsts = ("(struct Host){ .p = host }.p", "host", "(S){0}.p" + " " * MAX_CALL_CHARS)
    cases = [
        (
            first,
            ["-memcpy(", "+reader(", f" {first},", " b->mapped_mem, n);"],
            "reader(",
        )
        for first in firsts
    ]
    cases += [
        (label, patch.split("\n")[1:], "new")
        for label, patch in _call_tail_patches().items()
    ]
    for label, lines, prefix in cases:
        masked, added = patch_segment(lines)
        text = "\n".join(masked)
        if "mapped_mem" not in text:
            failures.append(f"C statement case failed: {label.strip()}")
            continue
        pos = text.index("mapped_mem")
        flat = max(text.rfind(c, 0, pos) for c in ";{}") + 1
        if _statement_boundary(text, pos) > flat:
            failures.append(f"C statement start invariant failed: {label.strip()}")
        start, end = statement_bounds(text, pos)
        if not text[start:].startswith(prefix) or not added_overlap(
            text, start, end, added
        ):
            failures.append(f"C statement case failed: {label.strip()}")
    return failures


def alias_patch_cases() -> tuple[tuple[str, list[int]], ...]:
    """Refuse context and added directives using aliases anywhere on the new side."""
    declaration = " { T *p = (T *)b->mapped_mem;\n"
    return (
        ("@@ -2,2 +2,3 @@\n" + declaration + "+#define P p\n }", [3]),
        ("@@ -2,3 +2,4 @@\n" + declaration + " #define P p\n+x = 1;\n }", [3]),
        ("@@ -2,3 +2,4 @@\n" + declaration + " #define P \\\n+p\n }", [3]),
        ("@@ -2,3 +2,4 @@\n" + declaration + "+#define P \\\n p\n }", [3]),
        ("@@ -0,0 +1 @@\n+#define P p\n@@ -8 +9 @@\n" + declaration.rstrip(), [2]),
        ("@@ -1,2 +1,2 @@\n-{ T *p = b->mapped_mem; }\n+#define P p\n x;", []),
        ("@@ -1,3 +1,4 @@\n #define P p\n" + declaration + "+x = P[0];\n }", [2]),
        ("@@ -1 +1,2 @@\n #define MM mapped_mem\n+x = b->MM[0];", [2]),
        (
            "@@ -1,3 +1,4 @@\n #define P p\n {\n+T *p = b->mapped_mem;\n x = P[0]; }",
            [2],
        ),
    )


def call_patches(mapped: bool, allocation: str) -> dict[str, str]:
    """Add callee-only replacements to the mapped-read refusal fixture group."""
    if not mapped or allocation:
        return {}
    return {
        f"patch callee {first}": (
            f"@@ -1,3 +1,3 @@\n-memcpy(\n+reader(\n {first},\n b->mapped_mem, n);"
        )
        for first in ("(struct Host){ .p = host }.p", "host")
    } | _call_tail_patches()


def _call_tail_patches() -> dict[str, str]:
    """Keep an added argument tail even when its opening parenthesis is absent."""
    return {
        f"patch call tail {close!r}": (
            f"@@ -5,2 +5,2 @@\n-    old{close}\n+    new{close}\n"
            "     + ((uint32_t *)b->mapped_mem)[0];\n"
        )
        for close in (")", "")
    }


def mode_cases() -> list[tuple[str, str, bytes, str]]:
    """Accept regular-file metadata and reject unsupported modes at their line."""
    cases = []
    modes = [(mode, True) for mode in ("100644", "100755")]
    modes += [(mode, False) for mode in ("120000", "160000", "100664")]
    metadata_cases = [("index ab..cd", True)]
    metadata_cases += [
        (prefix + mode, accepted)
        for prefix in ("new file mode ", "index ab..cd ")
        for mode, accepted in modes
    ]
    metadata_cases += [
        ("new file mode " + mode, False) for mode in ("040000", "junk", "")
    ]
    for metadata, accepted in metadata_cases:
        raw = f"diff --git a/x.c b/x.c\n{metadata}\n@@ -0,0 +1 @@\n+x;"
        expected = "" if accepted else "2: unreadable patch metadata"
        cases.append((metadata, "x.patch", raw.encode(), expected))
    return cases
