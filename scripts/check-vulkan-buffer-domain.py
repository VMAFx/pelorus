#!/usr/bin/env python3
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Keep every shader-read Pelorus storage buffer in device-local memory.

A storage buffer allocated HOST_VISIBLE without DEVICE_LOCAL lives in system
RAM on a discrete GPU, so each shader read crosses PCIe while the dispatch
runs (zero-copy gap in docs/research/0172-zero-copy-audit.md, issue #109).
Every ``ff_vk_get_pooled_buffer()`` and ``ff_vk_create_buf()`` call in
``ffmpeg-patches/files/`` whose usage holds
``VK_BUFFER_USAGE_STORAGE_BUFFER_BIT`` must therefore request
``VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT``. A pure transfer buffer (staging for
a copy) may stay host-only. A usage or memory argument may be a local name:
it resolves to the nearest assignment before the call plus any ``|=`` between
that assignment and the call. A lower-case name with no assignment fails
closed. Scope: the C sources and the added side of the hand-maintained
patches in ``ffmpeg-patches/files/``; the shared FFmpeg fix series and patch
context lines are not read, and a wrapper that forwards its flags is not
followed.

``--self-test`` plants a host-only storage buffer in each source shape (a C
file, a named flag variable, ``|=`` usage, a reused local name, a
``ff_vk_create_buf()`` call, a hand-maintained patch, a patch line starting
with ``++``) and requires the check to refuse each one, and requires a
host-only transfer buffer to pass.
"""

from __future__ import annotations

import pathlib
import re
import sys

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


def source_texts() -> list[tuple[str, str]]:
    texts = []
    for path in sorted(FILES.iterdir()):
        if path.suffix in (".c", ".h"):
            texts.append((path.name, strip_comments(path.read_text())))
        elif path.suffix == ".patch":
            texts.append((path.name, strip_comments(added_lines(path.read_text()))))
    return texts


def check(texts: list[tuple[str, str]]) -> list[str]:
    errors: list[str] = []
    total = 0
    for name, text in texts:
        found, calls = check_text(name, text)
        errors += found
        total += calls
    if total == 0:
        errors.append(f"no buffer allocation call found under {FILES.relative_to(ROOT)}")
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


def self_test() -> list[str]:
    failures: list[str] = []
    for label, (name, text) in PLANTED.items():
        errors, calls = check_text(name, text)
        if calls != 1 or not errors:
            failures.append(f"planted defect accepted: {label}")
    for label, (name, text) in ACCEPTED.items():
        errors, calls = check_text(name, text)
        if calls != 1 or errors:
            failures.append(f"valid buffer refused: {label}: {errors}")
    if not check([("empty.c", "int x;")]):
        failures.append("a tree without any pooled buffer call was accepted")
    return failures


def main(argv: list[str]) -> int:
    errors = check(source_texts())
    if "--self-test" in argv:
        errors += self_test()
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
