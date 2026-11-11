#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Keep shader buffers device-local and copy before mapped host reads (ADR-0255).

Storage allocations require DEVICE_LOCAL; transfer-only buffers may stay host-only.
Local flags resolve from the nearest assignment plus later |=; unknowns fail.
Mapped reads permit memcpy arguments 0/1, memset argument 0 and the documented
write-only sink arguments below. A mapped destination bulk call must be its own
expression statement, optionally cast to void; its returned pointer cannot escape.
Member-qualified callees fail closed. Plain cast stores and block-local non-const
aliases are allowed only with declaration specifiers from an explicit type
allow-list and later uses that start statements and perform plain indexed stores.
Known reads require exact statements, issues, MEL categories and expiry dates;
stale sites, duplicate sites or entries, and expired exceptions fail.
C/H files and patch additions undergo line splicing then comment/literal masking.
Patch context participates in masking before being blanked. This lexical scan
has no preprocessor or macro expansion and does not follow the shared series,
vk->MapMemory pointers or wrappers. Pointer arithmetic and keyword calls fail.
Use --files DIR to scan a copy instead of ffmpeg-patches/files, and --self-test
for planted defects, allowed uses, file integration and expiry boundaries.
"""

from __future__ import annotations

import pathlib
import argparse
import datetime
import re
import sys
import tempfile
from typing import NamedTuple

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
sys.dont_write_bytecode = True
from c_scan import MAX_CALL_CHARS, block_tail, call_args, mask_c, patch_additions  # noqa: E402
from c_scan import self_test as scan_self_test  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ROOT / "ffmpeg-patches" / "files"
# Call name -> (usage argument index, memory-flag argument index).
CALLS = {
    "ff_vk_get_pooled_buffer(": (3, 6),
    "ff_vk_create_buf(": (5, 6),
}
STORAGE = "VK_BUFFER_USAGE_STORAGE_BUFFER_BIT"
DEVICE_LOCAL = "VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT"
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
CAST = r"\(\s*(?:const\s+)?\w+(?:\s+\w+)*\s*\*+\s*\)"
CHAIN = r"\w+(?:(?:->|\.)\w+)*(?:->|\.)mapped_mem"
STORE = r"\s*\[[^\[\]]*\]\s*=(?!=)"
TYPE = r"(?:const|volatile|restrict|signed|unsigned|short|long|int|char|float|double|void|_Bool|(?:struct|union|enum)\s+\w+|\w+_t|[A-Z]\w*)"
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


def check_text(name: str, text: str) -> tuple[list[str], int]:
    """Return (errors, number of calls read) for one source text."""

    errors: list[str] = []
    calls = 0
    pattern = "|".join(re.escape(c) for c in CALLS)
    for match in re.finditer(rf"\b(?:{pattern})", text):
        line = text.count("\n", 0, match.start()) + 1
        call = match.group(0)
        usage_arg, mem_arg = CALLS[call]
        parsed = call_args(text, match.end() - 1)
        args = parsed[0] if parsed else None
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
                prefix = text[max(0, index - MAX_CALL_CHARS) : index]
                name = re.search(r"\b(\w+)\s*$", prefix)
                if name:
                    member = re.search(r"(?:->|\.)\s*$", prefix[: name.start()])
                    return ("" if member else name[1]), index
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
    if re.search(r"[\w)\]]\s*\(", plain):
        return False
    plain = re.sub(r"[()\s]", "", plain)
    return re.fullmatch(CHAIN, plain) is not None


def allowed_call(text: str, pos: int, call: tuple[str, int]) -> bool:
    """Allow a complete mapped pointer only at a documented bulk/sink argument."""

    name, start = call
    parsed = call_args(text, start)
    index = argument_index(text, start, pos)
    if parsed is None:
        return False
    args, end = parsed
    if index >= len(args) or not pointer_expression(args[index]):
        return False
    if name in BULK and index == 0:
        boundary = max(text.rfind(c, 0, start) for c in ";{}") + 1
        prefix = re.sub(rf"\b{name}\s*$", "", text[boundary:start]).strip()
        suffix, _ = statement_at(text[end:], 0)
        return prefix in ("", "(void)") and suffix == ";"
    return index in BULK.get(name, {}) or index == SINKS.get(name, (-1, ""))[0]


def statement_at(text: str, pos: int) -> tuple[str, int]:
    """Return collapsed statement and end offset, retaining its semicolon."""

    start = max(text.rfind(delimiter, 0, pos) for delimiter in ";{}") + 1
    end = text.find(";", pos)
    end = len(text) if end < 0 else end + 1
    return " ".join(text[start:end].split()), end


def statement_start(text: str, pos: int) -> bool:
    """Require an alias store to begin a statement rather than a subexpression."""
    prefix = text[:pos].rstrip()
    return (
        not prefix or prefix[-1] in ";{})" or bool(re.search(r"\b(?:else|do)$", prefix))
    )


def allowed_statement(text: str, statement: str, end: int) -> bool:
    """Accept plain cast stores or non-const aliases used only by plain stores."""

    if len(re.findall(r"\bmapped_mem\b", statement)) != 1:
        return False
    if re.match(rf"^\(\s*{CAST}\s*{CHAIN}\s*\){STORE}", statement):
        return True
    alias = re.fullmatch(
        rf"({TYPE}(?:\s+{TYPE})*)\s*\*\s*(\w+)\s*=\s*({CAST})\s*{CHAIN};", statement
    )
    if not alias or re.search(r"\bconst\b", alias[1] + " " + alias[3]):
        return False
    tail = block_tail(text, end)
    if tail is None:
        return False
    uses = list(re.finditer(rf"\b{re.escape(alias[2])}\b", tail))
    return all(
        re.match(STORE, tail[use.end() :]) and statement_start(tail, use.start())
        for use in uses
    )


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
                f"{statement.removesuffix(';')}; copy it out with one memcpy and read the copy (ADR-0255)"
            )
    seen: set[tuple[str, str]] = set()
    for entry in known:
        key = (entry.file, entry.statement)
        if key in seen:
            errors.append(f"duplicate known read entry {entry.file}: {entry.statement}")
        seen.add(key)
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
        if path.suffix in (".c", ".h", ".patch"):
            scan = patch_additions if path.suffix == ".patch" else mask_c
            texts.append((path.name, scan(path.read_text())))
    return texts


def check(
    texts: list[tuple[str, str]], today: datetime.date, files: pathlib.Path = FILES
) -> list[str]:
    """Enforce allocation and mapped-read rules across the complete source scan."""

    errors: list[str] = []
    total = mapped_total = 0
    sites: list[Site] = []
    for name, text in texts:
        found, calls = check_text(name, text)
        errors += found
        total += calls
        refused, mapped_count = mapped_sites(name, text)
        sites += refused
        mapped_total += mapped_count
    directory = files.relative_to(ROOT) if files.is_relative_to(ROOT) else files
    if total == 0:
        errors.append(f"no buffer allocation call found under {directory}")
    if mapped_total == 0:
        errors.append(f"no mapped_mem use found under {directory}")
    errors += check_known(sites, today)
    return errors


HOST_ONLY = "VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT"
POOL = "ff_vk_get_pooled_buffer(s, &p, &r, usage, NULL, n, mem);"
STORAGE_ALLOC = (
    f"ff_vk_get_pooled_buffer(s, &p, &r,\n {STORAGE}, NULL, n,\n {HOST_ONLY});"
)
PLANTED = {
    "inline flags in a C file": STORAGE_ALLOC,
    "named flag variable": (
        f"const VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n"
        f"const VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | {STORAGE};\n"
        + POOL
    ),
    "usage built with |=": (
        f"VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;\nusage |= {STORAGE};\n"
        f"const VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n" + POOL
    ),
    "local name reused by a later function": (
        f"static int a(void) {{ int mem = {DEVICE_LOCAL}; return mem; }}\n"
        f"static int b(void) {{ int mem = {HOST_ONLY};\n"
        f"return ff_vk_get_pooled_buffer(v, &p, &r, {STORAGE}, NULL, n, mem); }}"
    ),
    "ff_vk_create_buf storage buffer": f"ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, {HOST_ONLY});",
    "unresolvable local flags": STORAGE_ALLOC.replace(STORAGE, "usage"),
    "hand-maintained patch": patch_additions(
        "+++ b/x.c\n+" + STORAGE_ALLOC.replace("\n", "\n+")
    ),
    "patch line starting with ++": patch_additions(
        "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,3 @@\n+++i; "
        + STORAGE_ALLOC.replace("\n", "\n+")
    ),
}
ACCEPTED = {
    "host-only transfer staging buffer": STORAGE_ALLOC.replace(
        STORAGE, "VK_BUFFER_USAGE_TRANSFER_SRC_BIT"
    ),
    "device-local storage buffer": STORAGE_ALLOC.replace(
        HOST_ONLY, DEVICE_LOCAL + " | " + HOST_ONLY
    ),
}


MAPPED_PLANTED = {
    "quoted brace alias": '{ uint32_t *p = (uint32_t *)b->mapped_mem; log_message("}"); consume(p); }',
    "char brace alias": "{ uint32_t *p = (uint32_t *)b->mapped_mem; c = '}'; consume(p); }",
    "quoted comments": 'log("/*"); attach_motion(s, in, b->mapped_mem, n); log("*/");',
    "spliced token": "attach_motion(s, in, b->mapped_\\\nmem, n);",
    "member arrow destination": "s->memcpy(b->mapped_mem, src, n);",
    "member dot source": "ops.memcpy(dst, b->mapped_mem, n);",
    "member sink": "s -> pel_mc_build_predictors(a, b2, c, b->mapped_mem, n);",
    "memset result reader": "attach_motion(s, in, memset(b->mapped_mem, 0, bytes), n);",
    "memcpy result alias": "{ uint32_t *p = memcpy(b->mapped_mem, src, n); consume(p); }",
    "memset result assignment": "x = memset(b->mapped_mem, 0, n);",
    "memcpy result condition": "if (memcpy(b->mapped_mem, s, n)) x = 1;",
    "return output": "{ return *out = (T *)b->mapped_mem; }",
    "else output": "{ if (x) y = 1; else *out = (T *)b->mapped_mem; }",
    "do output": "{ do *out = (T *)b->mapped_mem; while (0); }",
    "chained alias store": "{ T *p = (T *)b->mapped_mem; p[0] = p[1] = 0; }",
    "alias store argument": "{ T *p = (T *)b->mapped_mem; f(p[0] = 1); }",
    "alias RHS": "{ T *p = (T *)b->mapped_mem; p[0] = p[1]; }",
    "alias RHS sum": "{ T *p = (T *)b->mapped_mem; p[0] = 1 + p[1]; }",
    "alias index": "{ T *p = (T *)b->mapped_mem; q[p[0]] = 1; }",
    "unterminated char": "x = 'a; reader(b->mapped_mem);",
    "patch context comment close": patch_additions(
        "@@\n+/*\n */\n+reader(b->mapped_mem);"
    ),
    "keyword sizeof": "memcpy(dst, sizeof(b->mapped_mem), n);",
    "keyword if": "if (b->mapped_mem) x = 1;",
    "parenthesised reader": "memcpy(dst, (reader)(b->mapped_mem), n);",
    "const alias store": "{ const T *p = (const T *)b->mapped_mem; p[0] = 1; }",
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
    "patch reader": patch_additions("--- a/x.c\n+++ b/x.c\n+reader(b->mapped_mem);"),
    "memcpy size": "memcpy(dst, src, ((T *)b->mapped_mem)[0]);",
    "offset": "memcpy(dst, b->mapped_mem + off, n);",
    "cast equality": "((T *)b->mapped_mem)[0] == 1;",
    "store with mapped RHS": "((T *)b->mapped_mem)[0] = ((T *)c->mapped_mem)[0];",
}
MAPPED_ACCEPTED = {
    "literal token": 'log("mapped_mem");',
    "void memset": "(void)memset(b->mapped_mem, 0, n);",
    "comment splits token": "mapped/**/_mem;",
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
    expiry = entered + datetime.timedelta(days=120)
    unknown = entry._replace(category="X")
    cases = (
        ("covered", [site], entered, entry, ""),
        ("stale", [], entered, entry, "matches no site"),
        ("expired", [site], expiry + datetime.timedelta(days=1), entry, "expired on"),
        ("boundary", [site], expiry, entry, ""),
        ("duplicate", [site, site], entered, entry, "matches 2 sites"),
        ("category", [site], entered, unknown, "unknown MEL category"),
    )
    failures: list[str] = []
    for label, sites, today, known, expected in cases:
        errors = check_known(sites, today, (known,))
        valid = any(expected in error for error in errors) if expected else not errors
        if not valid:
            failures.append(f"known-read case failed: {label}: {errors}")
    errors = check_known([site], entered, (entry, entry))
    if not any("duplicate known read entry" in error for error in errors):
        failures.append(f"known-read case failed: duplicate entries: {errors}")
    return failures


def mapped_self_test() -> list[str]:
    """Plant mapped reads and retain legitimate bulk transfers and writes."""

    failures = known_self_test()
    for planted, cases in ((True, MAPPED_PLANTED), (False, MAPPED_ACCEPTED)):
        for label, text in cases.items():
            sites, tokens = mapped_sites("mapped.c", mask_c(text))
            errors = check_known(sites, datetime.date(2026, 11, 11), ())
            if any(";;" in error for error in errors):
                failures.append(f"doubled semicolon: {label}: {errors}")
            if label in ("literal token", "comment splits token") and tokens:
                failures.append(f"false mapped token: {label}")
            if bool(errors) != planted:
                failures.append(f"mapped case failed: {label}: {errors}")
    return failures


def self_test() -> list[str]:
    """Exercise allocation and mapped-read rules with planted regressions."""

    failures = mapped_self_test() + scan_self_test() + source_self_test()
    for planted, cases in ((True, PLANTED), (False, ACCEPTED)):
        for label, text in cases.items():
            errors, calls = check_text(label, mask_c(text))
            if calls != 1 or bool(errors) != planted:
                failures.append(f"allocation case failed: {label}: {errors}")
    empty = check([("empty.c", "int x;")], datetime.date(2026, 11, 11))
    for message in ("no buffer allocation call found", "no mapped_mem use found"):
        if not any(message in error for error in empty):
            failures.append(f"empty tree accepted: {message}")
    return failures


def source_self_test() -> list[str]:
    """Check file selection, patch line numbers and both rules in a real tree."""
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for entry in KNOWN_READS:
            (root / entry.file).write_text(entry.statement)
        (root / "a.c").write_text(
            ACCEPTED["device-local storage buffer"] + "\nmemcpy(dst, b->mapped_mem, n);"
        )
        (root / "b.h").write_text("x = ((T *)b->mapped_mem)[0];")
        (root / "c.patch").write_text(
            "--- a/x.c\n+++ b/x.c\n@@ -1 +1,3 @@\n reader(b->mapped_mem);\n"
            "+reader(b->mapped_mem);\n"
            f"+ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, {HOST_ONLY});\n"
        )
        errors = check(source_texts(root), datetime.date(2026, 11, 11), root)
    expected = (
        "b.h:1: element-wise",
        "c.patch:5: element-wise",
        "c.patch:6: storage buffer",
    )
    if len(errors) != 3 or not all(
        any(e.startswith(p) for e in errors) for p in expected
    ):
        return [f"source tree case failed: {errors}"]
    return []


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
    errors = check(source_texts(args.files), datetime.date.today(), args.files)
    if args.self_test:
        errors += self_test()
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
