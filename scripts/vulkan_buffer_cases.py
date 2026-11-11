# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
"""Allocation fixtures and flag constants for the Vulkan buffer checker."""

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
