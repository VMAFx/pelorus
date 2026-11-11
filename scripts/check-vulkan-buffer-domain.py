#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Keep shader buffers device-local and bulk-copy before mapped host reads.

ADR-0222: ff_vk_get_pooled_buffer/ff_vk_create_buf storage usage requires
DEVICE_LOCAL; transfer-only buffers may stay host-only. Local flags resolve
from their nearest assignment plus later |=; unresolved names fail closed.
ADR-0255: mapped_mem permits only memcpy, memset, two documented write-only
sink arguments, plain cast stores and block-local write-only aliases. Three
known reads carry exact-statement, expiring MEL exceptions.
Scope: C/H and added patch lines under ffmpeg-patches/files (or --files DIR),
with comments stripped. Shared patches, patch context, pointers obtained from
vk->MapMemory and wrapper implementations are not followed. Pointer arithmetic
such as mapped_mem + off is refused (fail closed). --self-test exercises both
rules with planted defects, allowed uses and deterministic expiry boundaries.
"""

from __future__ import annotations

import pathlib
import argparse
import datetime
import re
import sys
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ROOT / "ffmpeg-patches" / "files"
# Call name -> (usage argument index, memory-flag argument index).
CALLS = {
    "ff_vk_get_pooled_buffer(": (3, 6),
    "ff_vk_create_buf(": (5, 6),
}
STORAGE = "VK_BUFFER_USAGE_STORAGE_BUFFER_BIT"
DEVICE_LOCAL = "VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT"
MAX_CALL_CHARS = 4096
MAX_LINES = 200000
BULK = {"memcpy": {0: "write", 1: "read"}, "memset": {0: "write"}}
SINKS = {
    "pel_mc_build_predictors": (
        3,
        "output parameter written only: pelorus_mc_stats.h:66-84",
    ),
    "pelorus_qpmap_raster": (
        3,
        "output parameter written only: vulkan-pelorus-qpmap.patch:1313-1345",
    ),
}
CONTROL = {"if", "while", "for", "switch", "return", "sizeof"}
CAST = r"\(\s*(?:const\s+)?\w+(?:\s+\w+)*\s*\*+\s*\)"
CHAIN = r"\w+(?:(?:->|\.)\w+)*(?:->|\.)mapped_mem"
STORE = r"\s*\[[^\[\]]*\]\s*=(?!=)"
MEL_DAYS = {"B": 3, "C": 10, "D": 120}
Site = tuple[str, str, int]


class KnownRead(NamedTuple):
    """One exact source statement with a dated MEL exception."""

    file: str
    statement: str
    issue: int
    category: str
    entered: str
    reason: str


KNOWN_READS = (
    KnownRead(
        "vf_pelorus_analyze_vulkan.c",
        "base = (const uint32_t *)buf_vk->mapped_mem;",
        596,
        "D",
        "2026-11-11",
        "Per-tile reads scale with resolution; copy out in #596.",
    ),
    KnownRead(
        "vf_pelorus_denoise_vulkan.c",
        "*acc_out = (const PelorusDenoiseBuf *)buf_vk->mapped_mem;",
        596,
        "D",
        "2026-11-11",
        "Fixed 160-word accumulator; copy out in #596.",
    ),
    KnownRead(
        "vf_pelorus_grain_estimate_vulkan.c",
        "if (err >= 0) err = attach_estimate(s, in, (const PelorusGrainBuf *)buf_vk->mapped_mem);",
        596,
        "D",
        "2026-11-11",
        "Fixed grain statistics struct; copy out in #596.",
    ),
)


def strip_comments(text: str) -> str:
    """Remove C comments, keeping line numbers, so prose cannot satisfy the contract."""

    text = re.sub(
        r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL
    )
    return re.sub(r"//[^\n]*", "", text)


def added_lines(patch: str) -> str:
    """Return the added side of a unified diff; other lines stay blank."""

    out: list[str] = []
    prev = ""
    for line in patch.splitlines()[:MAX_LINES]:
        header = line.startswith("+++ ") and prev.startswith("--- ")
        out.append(line[1:] if line.startswith("+") and not header else "")
        prev = line
    return "\n".join(out)


def call_args(text: str, start: int) -> list[str] | None:
    """Split the call opened at ``start`` into its top-level arguments."""

    depth = 0
    args: list[str] = []
    current: list[str] = []
    end = min(len(text), start + MAX_CALL_CHARS)
    for pos in range(start, end):
        char = text[pos]
        if char == "(":
            depth += 1
            if depth == 1:
                continue
        elif char == ")":
            depth -= 1
            if depth == 0:
                args.append("".join(current).strip())
                return args
        elif char == "," and depth == 1:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    return None


def resolve(text: str, arg: str, pos: int) -> str | None:
    """Resolve a local-name argument to its value at ``pos``; None if unknown.

    Upper-case names are constants and stay as they are.
    """

    if not re.fullmatch(r"[a-z_]\w*", arg):
        return arg
    name = re.escape(arg)
    found = list(re.finditer(rf"\b{name}\s*=(?!=)\s*([^;]*);", text[:pos]))
    if not found:
        return None
    last = found[-1]
    more = re.findall(rf"\b{name}\s*\|=\s*([^;]*);", text[last.end() : pos])
    return " | ".join([last.group(1), *more])


def check_text(name: str, text: str) -> tuple[list[str], int]:
    """Return (errors, number of calls read) for one source text."""

    errors: list[str] = []
    calls = 0
    pattern = "|".join(re.escape(c) for c in CALLS)
    for match in re.finditer(rf"\b(?:{pattern})", text):
        line = text.count("\n", 0, match.start()) + 1
        call = match.group(0)
        usage_arg, mem_arg = CALLS[call]
        args = call_args(text, match.end() - 1)
        if args is None or len(args) <= mem_arg:
            errors.append(f"{name}:{line}: unreadable {call} call")
            continue
        calls += 1
        usage = resolve(text, args[usage_arg], match.start())
        mem = resolve(text, args[mem_arg], match.start())
        if usage is None or mem is None:
            errors.append(f"{name}:{line}: {call} flags not resolvable here")
        elif STORAGE in usage and DEVICE_LOCAL not in mem:
            errors.append(
                f"{name}:{line}: storage buffer without {DEVICE_LOCAL} "
                f"(memory flags: {' '.join(mem.split())})"
            )
    return errors, calls


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
                name = re.search(
                    r"\b(\w+)\s*$", text[max(0, index - MAX_CALL_CHARS) : index]
                )
                if name and name[1] not in CONTROL:
                    return name[1], index
        elif depth == 0 and char in ";{}":
            return None
    return None


def argument_index(text: str, start: int, pos: int) -> int:
    """Count depth-one commas before a token in a bounded call."""

    depth = 0
    index = 0
    for char in text[start:pos]:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 1:
            index += 1
    return index


def pointer_expression(arg: str) -> bool:
    """Accept only a cast/grouped member chain ending in mapped_mem."""

    plain = re.sub(CAST, "", arg)
    plain = re.sub(r"[()\s]", "", plain)
    return re.fullmatch(CHAIN, plain) is not None


def allowed_call(text: str, pos: int, call: tuple[str, int]) -> bool:
    """Allow a complete mapped pointer only at a documented bulk/sink argument."""

    name, start = call
    args = call_args(text, start)
    index = argument_index(text, start, pos)
    if args is None or index >= len(args) or not pointer_expression(args[index]):
        return False
    return index in BULK.get(name, {}) or index == SINKS.get(name, (-1, ""))[0]


def statement_at(text: str, pos: int) -> tuple[str, int]:
    """Return collapsed statement and end offset, retaining its semicolon."""

    start = max(text.rfind(delimiter, 0, pos) for delimiter in ";{}") + 1
    end = text.find(";", pos)
    end = len(text) if end < 0 else end + 1
    return " ".join(text[start:end].split()), end


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


def allowed_statement(text: str, statement: str, end: int) -> bool:
    """Accept plain cast stores or non-const aliases used only by plain stores."""

    if len(re.findall(r"\bmapped_mem\b", statement)) != 1:
        return False
    if re.match(rf"^\(\s*{CAST}\s*{CHAIN}\s*\){STORE}", statement):
        return True
    alias = re.fullmatch(
        rf"(\w+(?:\s+\w+)*)\s*\*\s*(\w+)\s*=\s*({CAST})\s*{CHAIN};", statement
    )
    if not alias or re.search(r"\bconst\b", alias[1] + " " + alias[3]):
        return False
    tail = block_tail(text, end)
    if tail is None:
        return False
    uses = list(re.finditer(rf"\b{re.escape(alias[2])}\b", tail))
    return all(re.match(STORE, tail[use.end() :]) for use in uses)


def mapped_sites(name: str, text: str) -> tuple[list[Site], int]:
    """Classify every mapped_mem token; return refused sites and total count."""

    refused: list[Site] = []
    matches = list(re.finditer(r"\bmapped_mem\b", text))
    for match in matches:
        pos = match.start()
        statement, end = statement_at(text, pos)
        call = enclosing_call(text, pos)
        allowed = (
            allowed_call(text, pos, call)
            if call
            else allowed_statement(text, statement, end)
        )
        if not allowed:
            refused.append((name, statement, text.count("\n", 0, pos) + 1))
    return refused, len(matches)


def known_entry_errors(entry: KnownRead, count: int, today: datetime.date) -> list[str]:
    """Validate exception coverage, MEL category and inclusive expiry boundary."""

    errors: list[str] = []
    label = f"known read {entry.file}: {entry.statement}"
    if count != 1:
        detail = "no site; remove the entry" if count == 0 else f"{count} sites"
        errors.append(f"{label} matches {detail} (issue #{entry.issue})")
    if entry.category not in MEL_DAYS:
        errors.append(
            f"{label}: unknown MEL category {entry.category} (issue #{entry.issue})"
        )
    else:
        expiry = datetime.date.fromisoformat(entry.entered) + datetime.timedelta(
            days=MEL_DAYS[entry.category]
        )
        if today > expiry:
            errors.append(
                f"known read {entry.file} expired on {expiry} "
                f"(issue #{entry.issue}, MEL category {entry.category})"
            )
    return errors


def check_known(
    sites: list[Site], today: datetime.date, known: tuple[KnownRead, ...] = KNOWN_READS
) -> list[str]:
    """Match exact refused statements against expiring exceptions without I/O."""

    errors: list[str] = []
    covered = {(entry.file, entry.statement) for entry in known}
    for name, statement, line in sites:
        if (name, statement) not in covered:
            errors.append(
                f"{name}:{line}: element-wise host read of mapped device memory: "
                f"{statement}; copy it out with one memcpy and read the copy (ADR-0255)"
            )
    for entry in known:
        count = sum(
            (name, statement) == (entry.file, entry.statement)
            for name, statement, _ in sites
        )
        errors += known_entry_errors(entry, count, today)
    return errors


def source_texts(files: pathlib.Path = FILES) -> list[tuple[str, str]]:
    """Read C/H files and patch additions from the selected source directory."""

    texts = []
    for path in sorted(files.iterdir()):
        if path.suffix in (".c", ".h"):
            texts.append((path.name, strip_comments(path.read_text())))
        elif path.suffix == ".patch":
            texts.append((path.name, strip_comments(added_lines(path.read_text()))))
    return texts


def check(texts: list[tuple[str, str]], today: datetime.date) -> list[str]:
    """Enforce allocation and mapped-read rules across the complete source scan."""

    errors: list[str] = []
    total = 0
    mapped_total = 0
    sites: list[Site] = []
    for name, text in texts:
        found, calls = check_text(name, text)
        errors += found
        total += calls
        refused, mapped_count = mapped_sites(name, text)
        sites += refused
        mapped_total += mapped_count
    if total == 0:
        errors.append(
            f"no buffer allocation call found under {FILES.relative_to(ROOT)}"
        )
    if mapped_total == 0:
        errors.append("no mapped_mem use found under ffmpeg-patches/files")
    errors += check_known(sites, today)
    return errors


HOST_ONLY = "VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT"
PLANTED = {
    "inline flags in a C file": (
        "x.c",
        "e = ff_vk_get_pooled_buffer(&s->vkctx, &s->p, &b,\n"
        f"    {STORAGE}, NULL, n,\n    {HOST_ONLY});",
    ),
    "named flag variable": (
        "y.c",
        f"const VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n"
        f"const VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | {STORAGE};\n"
        "e = ff_vk_get_pooled_buffer(vkctx, &s->p, &b, usage, NULL, n, mem);",
    ),
    "usage built with |=": (
        "u.c",
        f"VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;\n"
        f"usage |= {STORAGE};\n"
        f"const VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n"
        "e = ff_vk_get_pooled_buffer(vkctx, &s->p, &b, usage, NULL, n, mem);",
    ),
    "local name reused by a later function": (
        "r.c",
        f"static int a(void) {{ int mem = {DEVICE_LOCAL}; return mem; }}\n"
        f"static int b(void) {{ int mem = {HOST_ONLY};\n"
        f"return ff_vk_get_pooled_buffer(v, &p, &r, {STORAGE}, NULL, n, mem); }}",
    ),
    "ff_vk_create_buf storage buffer": (
        "c.c",
        f"e = ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE},\n    {HOST_ONLY});",
    ),
    "unresolvable local flags": (
        "w.c",
        f"e = ff_vk_get_pooled_buffer(s, &p, &r, usage, NULL, n, {HOST_ONLY});",
    ),
    "hand-maintained patch": (
        "z.patch",
        strip_comments(
            added_lines(
                "+++ b/libavcodec/vulkan_encode.c\n"
                "+    err = ff_vk_get_pooled_buffer(s, &ctx->pool, &ref,\n"
                f"+                                  {STORAGE}, NULL, n,\n"
                f"+                                  {HOST_ONLY});\n"
            )
        ),
    ),
    "patch line starting with ++": (
        "p.patch",
        strip_comments(
            added_lines(
                "--- a/libavfilter/x.c\n+++ b/libavfilter/x.c\n"
                "@@ -1,1 +1,3 @@\n"
                "+++i; e = ff_vk_get_pooled_buffer(s, &p, &r,\n"
                f"+    {STORAGE}, NULL, n,\n+    {HOST_ONLY});\n"
            )
        ),
    ),
}
ACCEPTED = {
    "host-only transfer staging buffer": (
        "s.c",
        "e = ff_vk_get_pooled_buffer(s, &ctx->stage, &ref,\n"
        f"    VK_BUFFER_USAGE_TRANSFER_SRC_BIT, NULL, n,\n    {HOST_ONLY});",
    ),
    "device-local storage buffer": (
        "d.c",
        "e = ff_vk_get_pooled_buffer(s, &ctx->p, &ref,\n"
        f"    {STORAGE}, NULL, n,\n    {DEVICE_LOCAL} | {HOST_ONLY});",
    ),
}


MAPPED_PLANTED = {
    "reader": "attach_motion(s, in, (const int32_t *)x->mapped_mem, n);",
    "const alias": "{ const T *p = (const T *)b->mapped_mem; x = p[0]; }",
    "cast read": "x = ((T *)b->mapped_mem)[i];",
    "compound store": "((uint32_t *)b->mapped_mem)[0] += 1;",
    "write alias read": "{ T *p = (T *)b->mapped_mem; p[0] = 1; x = p[0]; }",
    "write alias equality": "{ T *p = (T *)b->mapped_mem; p[0] == 1; }",
    "write alias compound": "{ T *p = (T *)b->mapped_mem; p[0] |= 1; }",
    "write alias call": "{ T *p = (T *)b->mapped_mem; reader(p); }",
    "struct read": "x = ((const T *)b->mapped_mem)->total[0];",
    "stored pointer": "*out = (const T *)b->mapped_mem;",
    "sink input": "pel_mc_build_predictors((const int32_t *)b->mapped_mem, mvy, n, prev, &gx, &gy);",
    "patch reader": added_lines("--- a/x.c\n+++ b/x.c\n+reader(b->mapped_mem);"),
    "memcpy size": "memcpy(dst, src, ((T *)b->mapped_mem)[0]);",
    "offset": "memcpy(dst, b->mapped_mem + off, n);",
    "cast equality": "((T *)b->mapped_mem)[0] == 1;",
    "store with mapped RHS": "((T *)b->mapped_mem)[0] = ((T *)c->mapped_mem)[0];",
}
MAPPED_ACCEPTED = {
    "memcpy source": "memcpy(s->mv_host, ((const FFVkBuffer *)mvx_ref->data)->mapped_mem, bytes);",
    "memcpy destination": "memcpy(b->mapped_mem, src, n);",
    "memset": "memset(((FFVkBuffer *)s->prevmv_buf->data)->mapped_mem, 0, n * sizeof(int32_t));",
    "cast store": "((uint32_t *)b->mapped_mem)[0] = 0;",
    "write alias": "{ uint32_t *md = (uint32_t *)b->mapped_mem; for (j = 0; j < cells; j++) { md[j] = j; } }",
    "predictors": "pel_mc_build_predictors(x, y, n, (int32_t *)b->mapped_mem, &gx, &gy);",
    "raster": "pelorus_qpmap_raster(ctx, rects, n, stage->mapped_mem, pitch);",
    "comment": "/* read b->mapped_mem */ // b->mapped_mem\n",
}


def known_self_test() -> list[str]:
    """Exercise exact exception coverage and deterministic MEL boundaries."""

    entry = KNOWN_READS[0]
    entered = datetime.date.fromisoformat(entry.entered)
    site = (entry.file, entry.statement, 1)
    cases = (
        ("covered", [site], entered, entry, ""),
        ("stale", [], entered, entry, "matches no site"),
        (
            "expired",
            [site],
            entered + datetime.timedelta(days=121),
            entry,
            "expired on",
        ),
        ("boundary", [site], entered + datetime.timedelta(days=120), entry, ""),
        ("duplicate", [site, site], entered, entry, "matches 2 sites"),
        (
            "category",
            [site],
            entered,
            entry._replace(category="X"),
            "unknown MEL category",
        ),
    )
    failures: list[str] = []
    for label, sites, today, known, expected in cases:
        errors = check_known(sites, today, (known,))
        valid = any(expected in error for error in errors) if expected else not errors
        if not valid:
            failures.append(f"known-read case failed: {label}: {errors}")
    return failures


def mapped_self_test() -> list[str]:
    """Plant mapped reads and retain legitimate bulk transfers and writes."""

    failures = known_self_test()
    for planted, cases in ((True, MAPPED_PLANTED), (False, MAPPED_ACCEPTED)):
        for label, text in cases.items():
            sites, _ = mapped_sites("mapped.c", strip_comments(text))
            errors = check_known(sites, datetime.date(2026, 11, 11), ())
            if bool(errors) != planted:
                failures.append(f"mapped case failed: {label}: {errors}")
    return failures


def self_test() -> list[str]:
    """Exercise allocation and mapped-read rules with planted regressions."""

    failures = mapped_self_test()
    for label, (name, text) in PLANTED.items():
        errors, calls = check_text(name, text)
        if calls != 1 or not errors:
            failures.append(f"planted defect accepted: {label}")
    for label, (name, text) in ACCEPTED.items():
        errors, calls = check_text(name, text)
        if calls != 1 or errors:
            failures.append(f"valid buffer refused: {label}: {errors}")
    empty = check([("empty.c", "int x;")], datetime.date(2026, 11, 11))
    for message in ("no buffer allocation call found", "no mapped_mem use found"):
        if not any(message in error for error in empty):
            failures.append(f"empty tree accepted: {message}")
    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for check-vulkan-buffer-domain."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run self-test suite")
    parser.add_argument(
        "--files",
        type=pathlib.Path,
        default=FILES,
        metavar="DIR",
        help="source directory for allocation and mapped-read checks",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for checking Vulkan buffer domain contract."""
    args = parse_args(argv)
    errors = check(source_texts(args.files), datetime.date.today())
    if args.self_test:
        errors += self_test()
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
