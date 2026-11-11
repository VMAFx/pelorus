#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Keep storage device-local and copy before mapped host reads (ADR-0255).

Permit documented bulk/sink arguments, plain cast stores and store-only aliases.
Bulk destination calls must discard their result; member callees fail closed.
Exact known reads require unique sites and entries with unexpired MEL dates.
C/H files and isolated patch hunks undergo splicing and lexical masking; patch
reports require added-line overlap. Macro and pointer provenance are not followed.
"""

from __future__ import annotations

import pathlib
import datetime
import re
import sys
import tempfile
from typing import NamedTuple

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
sys.dont_write_bytecode = True
from c_scan import (  # noqa: E402
    MAX_CALL_CHARS,
    PatchError,
    added_overlap,
    argument_index,
    call_args,
    enclosing_call,
    mask_c,
    parse_args,
    pointer_expression,
    resolve,
    source_texts,
    statement_at,
    statement_bounds,
    store_only_tail,
)
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


def check_text(
    name: str, text: str, added: set[int] | None = None
) -> tuple[list[str], int]:
    """Return (errors, number of calls read) for one source text."""

    errors: list[str] = []
    calls = 0
    pattern = "|".join(re.escape(c) for c in CALLS)
    for match in re.finditer(rf"\b(?:{pattern})", text):
        line = text.count("\n", 0, match.start()) + 1
        call = match.group(0)
        usage_arg, mem_arg = CALLS[call]
        limit = min(len(text), match.start() + MAX_CALL_CHARS)
        args, end = call_args(text, match.end() - 1) or ([], limit)
        if not added_overlap(text, match.start(), end, added):
            continue
        if len(args) <= mem_arg:
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


def allowed_call(text: str, pos: int, call: tuple[str, int]) -> bool:
    """Allow a complete mapped pointer only at a documented bulk/sink argument."""

    name, start = call
    parsed = call_args(text, start)
    index = argument_index(text, start, pos)
    if parsed is None:
        return False
    args, end = parsed
    if index >= len(args) or not pointer_expression(
        args[index], CAST, CHAIN, "mapped_mem"
    ):
        return False
    if name in BULK and index == 0:
        boundary = max(text.rfind(c, 0, start) for c in ";{}") + 1
        prefix = re.sub(rf"\b{name}\s*$", "", text[boundary:start]).strip()
        suffix, _ = statement_at(text[end:], 0)
        return prefix in ("", "(void)") and suffix == ";"
    return index in BULK.get(name, {}) or index == SINKS.get(name, (-1, ""))[0]


def allowed_statement(text: str, statement: str, end: int) -> tuple[bool, int]:
    """Accept plain cast stores or non-const aliases used only by plain stores."""

    if len(re.findall(r"\bmapped_mem\b", statement)) != 1:
        return False, end
    if re.match(rf"^\(\s*{CAST}\s*{CHAIN}\s*\){STORE}", statement):
        return True, end
    alias = re.fullmatch(
        rf"({TYPE}(?:\s+{TYPE})*)\s*\*\s*(\w+)\s*=\s*({CAST})\s*{CHAIN};", statement
    )
    if not alias or re.search(r"\bconst\b", alias[1] + " " + alias[3]):
        return False, end
    return store_only_tail(text, alias[2], end, STORE)


def mapped_sites(
    name: str, text: str, added: set[int] | None = None
) -> tuple[list[Site], int]:
    """Classify every mapped_mem token; return refused sites and total count."""

    refused: list[Site] = []
    matches = list(re.finditer(r"\bmapped_mem\b", text))
    for match in matches:
        pos = match.start()
        statement, end = statement_at(text, pos)
        call = enclosing_call(text, pos)
        start, _ = statement_bounds(text, pos)
        if call:
            allowed = allowed_call(text, pos, call)
        else:
            allowed, end = allowed_statement(text, statement, end)
        if not allowed and added_overlap(text, start, end, added):
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
        count = sum((name, statement) == key for name, statement, _ in sites)
        errors += known_entry_errors(entry, count, today)
    return errors


def check(
    texts: list[tuple[str, str | PatchError, set[int] | None]],
    today: datetime.date,
    files: pathlib.Path = FILES,
) -> list[str]:
    """Enforce allocation and mapped-read rules across the complete source scan."""

    errors: list[str] = []
    total = mapped_total = 0
    sites: list[Site] = []
    for name, text, added in texts:
        if isinstance(text, PatchError):
            errors.append(f"{name}:{text}")
            continue
        found, calls = check_text(name, text, added)
        errors += found
        total += calls
        refused, mapped_count = mapped_sites(name, text, added)
        sites += refused
        mapped_total += mapped_count
    directory = files.relative_to(ROOT) if files.is_relative_to(ROOT) else files
    if total == 0:
        errors.append(f"no buffer allocation call found under {directory}")
    if mapped_total == 0:
        errors.append(f"no mapped_mem use found under {directory}")
    return errors + check_known(sites, today)


HOST_ONLY = "VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT"
POOL = "ff_vk_get_pooled_buffer(s, &p, &r, usage, NULL, n, mem);"
STORAGE_ALLOC = (
    f"ff_vk_get_pooled_buffer(s, &p, &r,\n {STORAGE}, NULL, n,\n {HOST_ONLY});"
)
PLANTED = {
    "subscript allocation": f"ff_vk_create_buf(s, &bufs[i, 0], n, NULL, NULL, {STORAGE}, {HOST_ONLY});",
    "inline flags in a C file": STORAGE_ALLOC,
    "named flag variable": f"const VkMemoryPropertyFlagBits mem = {HOST_ONLY};\nconst VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT | {STORAGE};\n{POOL}",
    "usage built with |=": f"VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;\nusage |= {STORAGE};\nconst VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n{POOL}",
    "local name reused by a later function": f"static int a(void) {{ int mem = {DEVICE_LOCAL}; return mem; }}\nstatic int b(void) {{ int mem = {HOST_ONLY};\nreturn ff_vk_get_pooled_buffer(v, &p, &r, {STORAGE}, NULL, n, mem); }}",
    "ff_vk_create_buf storage buffer": f"ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, {HOST_ONLY});",
    "unresolvable local flags": STORAGE_ALLOC.replace(STORAGE, "usage"),
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
    "bracket call closer": "memcpy(b->mapped_mem, src, n];",
    "brace call closer": "memcpy(b->mapped_mem, src, n};",
    "compound literal sink": "pel_mc_build_predictors(h, (S){a, b}.w, n, (int32_t *)b->mapped_mem, &gx, &gy);",
    "subscript sink input": "pel_mc_build_predictors(hosts[i = 0, j = 0, k], (const int32_t *)b->mapped_mem, n, prev, &gx, &gy);",
    "brace sink input": "pel_mc_build_predictors((S){a, b, c}.p, (const int32_t *)b->mapped_mem, n, prev, &gx, &gy);",
    "nested mapped source": "memcpy(dst, ((FFVkBuffer *)b->mapped_mem)->mapped_mem, n);",
    "nested mapped destination": "memcpy(((FFVkBuffer *)b->mapped_mem)->mapped_mem, src, n);",
    "nested mapped sink": "pel_mc_build_predictors(h, w, n, ((FFVkBuffer *)b->mapped_mem)->mapped_mem, &gx, &gy);",
    "nested mapped store": "((uint32_t *)((FFVkBuffer *)b->mapped_mem)->mapped_mem)[0] = 0;",
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
    "memcpy result suffix": "memcpy(b->mapped_mem, s, n) + 0;",
    "nested block alias escape": "{ T *p = (T *)b->mapped_mem; if (x) { p[0] = 1; } consume(p); }",
    "return output": "{ return *out = (T *)b->mapped_mem; }",
    "else output": "{ if (x) y = 1; else *out = (T *)b->mapped_mem; }",
    "do output": "{ do *out = (T *)b->mapped_mem; while (0); }",
    "chained alias store": "{ T *p = (T *)b->mapped_mem; p[0] = p[1] = 0; }",
    "alias store argument": "{ T *p = (T *)b->mapped_mem; f(p[0] = 1); }",
    "alias RHS": "{ T *p = (T *)b->mapped_mem; p[0] = p[1]; }",
    "alias RHS sum": "{ T *p = (T *)b->mapped_mem; p[0] = 1 + p[1]; }",
    "alias index": "{ T *p = (T *)b->mapped_mem; q[p[0]] = 1; }",
    "unterminated char": "x = 'a; reader(b->mapped_mem);",
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
    "memcpy size": "memcpy(dst, src, ((T *)b->mapped_mem)[0]);",
    "offset": "memcpy(dst, b->mapped_mem + off, n);",
    "cast equality": "((T *)b->mapped_mem)[0] == 1;",
    "store with mapped RHS": "((T *)b->mapped_mem)[0] = ((T *)c->mapped_mem)[0];",
}
MAPPED_ACCEPTED = {
    "subscript memcpy source": "memcpy(dst[i, 0], b->mapped_mem, n);",
    "alias first store": "{ T *p = (T *)b->mapped_mem; p[0] = 1; }",
    "store after block": "{ if (x) { y = 1; } ((uint32_t *)b->mapped_mem)[0] = 0; }",
    "literal token": 'log("mapped_mem");',
    "void memset": "(void)memset(b->mapped_mem, 0, n);",
    "comment splits token": "mapped/**/_mem;",
    "memcpy source": "memcpy(s->mv_host, ((const FFVkBuffer *)mvx_ref->data)->mapped_mem, bytes);",
    "memcpy destination": "memcpy(b->mapped_mem, src, n);",
    "memset": "memset(((FFVkBuffer *)s->prevmv_buf->data)->mapped_mem, 0, n * sizeof(int32_t));",
    "cast store": "((uint32_t *)b->mapped_mem)[0] = 0;",
    "write alias": "{ uint32_t *md = (uint32_t *)b->mapped_mem; for (j = 0; j < cells; j++) { md[j] = j; } md[0] = 0; if (x) md[1] = 1; else md[2] = 2; do md[3] = 3; while (0); }",
    "alias type groups": "{ T *a = (T *)b->mapped_mem; struct s *c = (struct s *)b->mapped_mem; int *d = (int *)b->mapped_mem; a[0] = 1; c[0] = 1; d[0] = 1; }",
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

    failures = mapped_self_test() + scan_self_test()
    failures += source_self_test() + patch_self_test()
    for planted, cases in ((True, PLANTED), (False, ACCEPTED)):
        for label, text in cases.items():
            errors, calls = check_text(label, mask_c(text))
            if calls != 1 or bool(errors) != planted:
                failures.append(f"allocation case failed: {label}: {errors}")
    empty = check([("empty.c", "int x;", None)], datetime.date(2026, 11, 11))
    for message in ("no buffer allocation call found", "no mapped_mem use found"):
        if not any(message in error for error in empty):
            failures.append(f"empty tree accepted: {message}")
    return failures


PATCH_CASES = {
    (True, ""): {
        "patch context comment close": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,3 @@\n+/*\n */\n+reader(b->mapped_mem);",
        "patch reader": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+reader(b->mapped_mem);",
        "patch header-shaped read": "--- a/x.c\n+++ b/x.c\n@@ -1 +1 @@\n--- i;\n+++ i, x = b->mapped_mem[0];",
        "patch header-shaped argument": "--- a/x.c\n+++ b/x.c\n@@ -1,2 +1,2 @@\n reader(\n--- i,\n+++ i, b->mapped_mem);",
        "patch header-shaped then read": "--- a/x.c\n+++ b/x.c\n@@ -1 +1,2 @@\n--- i;\n+++ i;\n+x = b->mapped_mem[0];",
        "patch empty context": "--- a/x.c\n+++ b/x.c\n@@ -1,3 +1,3 @@\n a\n\n-b\n+x = b->mapped_mem[0];",
        "patch result escape": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n p =\n+memcpy(b->mapped_mem, src, n);",
        "patch spliced reader": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n x = reader\\\n+(b->mapped_mem);",
        "patch context alias": "--- a/x.c\n+++ b/x.c\n@@ -1,2 +1,3 @@\n { T *p = (T *)b->mapped_mem;\n+x = p[0];\n }",
        "patch open alias": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n T *p = (T *)b->mapped_mem;\n+x = p[0];",
        "patch reader argument": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n reader(\n+b->mapped_mem);",
        "patch added alias": "--- a/x.c\n+++ b/x.c\n@@ -1,3 +1,4 @@\n {\n+T *p = (T *)b->mapped_mem;\n x = p[0];\n }",
        "patch removed splice": "--- a/x.c\n+++ b/x.c\n@@ -1,2 +1,2 @@\n x = b->mapped_\\\n-old;\n+mem;",
        "patch added removed splice": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n+x = b->mapped_\\\n-old;\n+mem;",
        "patch marker splice": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,2 @@\n+x = b->mapped_\\\n\\ No newline at end of file\n+mem;",
        "patch context splice read": "--- a/x.c\n+++ b/x.c\n@@ -1,2 +1,3 @@\n if (ok) \\\n+    value = ((const int *)buf->mapped_mem)[0];\n     do_something();",
        "patch hunk call": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+memcpy(dst,\n@@ -9,0 +9 @@\n+b->mapped_mem, n);",
        "patch hunk context call": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,1 @@\n memcpy(dst,\n@@ -9,0 +9 @@\n+b->mapped_mem, n);",
        "patch hunk alias": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n {\n+T *p = (T *)b->mapped_mem;\n@@ -9,0 +10 @@\n+p[0] = 1; }",
        "patch file call": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+memcpy(dst,\n--- a/y.c\n+++ b/y.c\n@@ -0,0 +1 @@\n+b->mapped_mem, n);",
        "patch open added alias": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n {\n+T *p = (T *)b->mapped_mem;",
    },
    (False, "unreadable patch"): {
        "patch trailing addition": "@@ -1 +1 @@\n-a\n+z\n+x = b->mapped_mem[0];",
        "patch incomplete": "@@ -1,2 +1,2 @@\n-a\n+z",
        "patch EOF header": "@@ -1 +1 @@",
        "patch unknown prefix": "@@ -1,2 +1,2 @@\n a\n*b",
        "patch negative count": "@@ -1,0 +1 @@\n-a\n+z",
        "patch malformed header": "@@ -0,0 +0,0 @@\n@@ broken\n+x;",
        "patch no hunk": "--- a/x.c\n+++ b/x.c\n+x;",
    },
    (False, "storage buffer"): {
        "hand-maintained patch": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,3 @@\n+"
        + STORAGE_ALLOC.replace("\n", "\n+"),
        "patch line starting with ++": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,3 @@\n+++i; "
        + STORAGE_ALLOC.replace("\n", "\n+"),
        "patch split allocation": f"--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n ff_vk_create_buf(s, &buf, n, NULL, NULL,\n+{STORAGE}, {HOST_ONLY});",
        "patch subscript allocation": f"--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+ff_vk_create_buf(s, &bufs[i, 0], n, NULL, NULL, {STORAGE}, {HOST_ONLY});",
    },
    (False, "unreadable"): {
        "patch hunk allocation": f"--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+ff_vk_create_buf(s, &buf, n, NULL, NULL,\n@@ -9,0 +9 @@\n+{STORAGE}, {HOST_ONLY});",
    },
    (False, "flags not resolvable here"): {
        "patch hunk flags": f"--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+flags = {DEVICE_LOCAL};\n@@ -9,0 +9 @@\n+ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, flags);",
    },
    (False, ""): {
        "patch hunk context reader": "--- a/x.c\n+++ b/x.c\n@@ -1,0 +1,1 @@\n+y = 1;\n@@ -9 +9 @@\n reader(b->mapped_mem);",
        "patch hunk context alias": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,1 @@\n T *p = (T *)b->mapped_mem;\n@@ -9,0 +9 @@\n+x = p[0]; }",
        "patch reader after addition": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n+y = 1;\n x = reader(b->mapped_mem);",
        "patch context reader": "--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n x = reader(b->mapped_mem);\n+y = 1;",
        "patch context allocation": f"--- a/x.c\n+++ b/x.c\n@@ -1,1 +1,2 @@\n ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, {HOST_ONLY});\n+y = 1;",
    },
}


def patch_self_test() -> list[str]:
    """Exercise patch classification and parse diagnostics through full checks."""
    failures: list[str] = []
    baseline = [(e.file, e.statement, None) for e in KNOWN_READS]
    baseline.append(("valid.c", ACCEPTED["device-local storage buffer"], None))
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        for (mapped, allocation), cases in PATCH_CASES.items():
            for label, patch in cases.items():
                (root / "test.patch").write_text(patch)
                errors = check(
                    source_texts(root) + baseline, datetime.date(2026, 11, 11)
                )
                found = any("element-wise host read" in e for e in errors)
                errors = [e for e in errors if "element-wise host read" not in e]
                valid = (
                    len(errors) == 1 and allocation in errors[0]
                    if allocation
                    else not errors
                )
                if found != mapped or not valid:
                    failures.append(f"patch case failed: {label}: {found}, {errors}")
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
            f"--- a/x.c\n+++ b/x.c\n@@ -1 +1,3 @@\n reader(b->mapped_mem);\n+reader(b->mapped_mem);\n+ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, {HOST_ONLY});\n@@ -9,0 +9 @@\n+reader(b->mapped_mem);\n"
        )
        errors = check(source_texts(root), datetime.date(2026, 11, 11), root)
    expected = (
        "b.h:1: element-wise",
        "c.patch:5: element-wise",
        "c.patch:6: storage buffer",
        "c.patch:8: element-wise",
    )
    if len(errors) != 4 or not all(
        any(e.startswith(p) for e in errors) for p in expected
    ):
        return [f"source tree case failed: {errors}"]
    return []


def main(argv: list[str] | None = None) -> int:
    """Entry point for checking Vulkan buffer domain contract."""
    args = parse_args(__doc__, FILES, argv)
    errors = check(source_texts(args.files), datetime.date.today(), args.files)
    if args.self_test:
        errors += self_test()
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
