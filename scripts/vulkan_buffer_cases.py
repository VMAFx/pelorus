# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Allocation and alias-directive fixtures for the Vulkan buffer checker."""

STORAGE = "VK_BUFFER_USAGE_STORAGE_BUFFER_BIT"

DEVICE_LOCAL = "VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT"

HOST_ONLY = "VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT"

POOL = "ff_vk_get_pooled_buffer(s, &p, &r, usage, NULL, n, mem);"

STORAGE_ALLOC = (
    f"ff_vk_get_pooled_buffer(s, &p, &r,\n {STORAGE}, NULL, n,\n {HOST_ONLY});"
)

PLANTED = {
    "subscript allocation": (
        f"ff_vk_create_buf(s, &bufs[i, 0], n, NULL, NULL, {STORAGE}, {HOST_ONLY});"
    ),
    "inline flags in a C file": STORAGE_ALLOC,
    "named flag variable": (
        f"const VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n"
        "const VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT"
        f" | {STORAGE};\n{POOL}"
    ),
    "usage built with |=": (
        "VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT;\n"
        f"usage |= {STORAGE};\nconst VkMemoryPropertyFlagBits mem = {HOST_ONLY};\n{POOL}"
    ),
    "local name reused by a later function": (
        f"static int a(void) {{ int mem = {DEVICE_LOCAL}; return mem; }}\n"
        f"static int b(void) {{ int mem = {HOST_ONLY};\n"
        f"return ff_vk_get_pooled_buffer(v, &p, &r, {STORAGE}, NULL, n, mem); }}"
    ),
    "ff_vk_create_buf storage buffer": (
        f"ff_vk_create_buf(s, &buf, n, NULL, NULL, {STORAGE}, {HOST_ONLY});"
    ),
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

PATCH_MACRO = (
    f"#define MK(b) ff_vk_create_buf(s, &(b), n, NULL, NULL, {STORAGE}, {HOST_ONLY})"
)


def alias_directive_cases() -> tuple[tuple[str, bool], ...]:
    """Refuse captured aliases; accept replacement-list uses of macro formals."""
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
        (declaration + "#define SCALE(p) ((p) * 2)\np[0] = SCALE(7); }", False),
        (declaration + "#define V(p...) (p)\np[0] = V(7); }", False),
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
