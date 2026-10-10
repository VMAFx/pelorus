/*
 * Copyright 2026 Lusoris
 *
 * This file is part of FFmpeg.
 *
 * FFmpeg is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at
 * your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
 * License for more details.
 */

/*
 * DRM format modifier selection for Pelorus Vulkan output pools (issue #103,
 * ADR-0184). Plain C with no libav* and no Vulkan dependency:
 * pelorus_vulkan_pool.h feeds it the driver's modifier list, and Pelorus's fast
 * suite tests it directly (ffmpeg-patches/test/drm_modifier_test.c).
 *
 * Rule. A modifier is a candidate when the Vulkan driver reports, for the
 * pool's exact image parameters, storage-image support on every plane view
 * format and DMA-BUF export (the caller sets `usable`), and when it has no
 * auxiliary memory plane: its memory plane count equals the format's plane
 * count. FFmpeg n9.0.2's Vulkan to DRM export describes one memory plane per
 * format plane (hwcontext_vulkan.c vulkan_map_to_drm), so a compression
 * modifier with metadata planes would be exported wrongly.
 *
 * The candidates are intersected with the modifiers the consumer imports: the
 * filter's `drm_modifiers` list, or every candidate when the list is empty.
 * The driver picks from the intersection, in driver order. An empty
 * intersection falls back to DRM_FORMAT_MOD_LINEAR when LINEAR is usable, and
 * the caller logs that substitution by name. Nothing usable at all is an
 * error: the pool is never silently allocated with OPTIMAL tiling.
 */

#ifndef AVFILTER_PELORUS_DRM_MODIFIER_H
#define AVFILTER_PELORUS_DRM_MODIFIER_H

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define PEL_DRM_MOD_LINEAR 0x0000000000000000ULL
/* DRM_FORMAT_MOD_INVALID: fourcc_mod_code(NONE, DRM_FORMAT_RESERVED) */
#define PEL_DRM_MOD_INVALID 0x00ffffffffffffffULL
/* Bound of every modifier list handled here (driver lists hold < 16). */
#define PEL_DRM_MOD_MAX 64
/* Longest name pel_drm_mod_name() writes, terminator included. */
#define PEL_DRM_MOD_NAME_SIZE 64

typedef struct PelDrmModCandidate {
    uint64_t modifier;
    uint32_t mem_planes; /* VkDrmFormatModifierPropertiesEXT plane count */
    int usable;          /* storage on every view format + DMA-BUF export */
} PelDrmModCandidate;

typedef struct PelDrmModChoice {
    uint64_t modifiers[PEL_DRM_MOD_MAX];
    int nb_modifiers;
    int linear_fallback; /* intersection empty, LINEAR substituted */
} PelDrmModChoice;

/* Parse a '|'- or space-separated list of modifiers (hex with 0x, octal with
 * 0, or decimal). Returns the count, or -1 on a malformed token, on
 * DRM_FORMAT_MOD_INVALID, or on more than `max` entries. NULL or an empty
 * string is an empty list. */
static inline int pel_drm_mod_parse_list(const char *str, uint64_t *out, int max)
{
    int n = 0;

    if (!str)
        return 0;
    for (int guard = 0; guard <= 4 * PEL_DRM_MOD_MAX; guard++) {
        char *end = NULL;
        unsigned long long value;

        while (*str == '|' || *str == ' ')
            str++;
        if (!*str)
            return n;
        if (*str < '0' || *str > '9' || n >= max)
            return -1;
        errno = 0;
        value = strtoull(str, &end, 0);
        if (errno || end == str || (*end && *end != '|' && *end != ' ') ||
            value == PEL_DRM_MOD_INVALID)
            return -1;
        out[n++] = value;
        str = end;
    }
    return -1;
}

static inline const char *pel_drm_mod_intel_name(uint64_t code)
{
    static const char *const names[] = {
        NULL,
        "I915_FORMAT_MOD_X_TILED",
        "I915_FORMAT_MOD_Y_TILED",
        "I915_FORMAT_MOD_Yf_TILED",
        "I915_FORMAT_MOD_Y_TILED_CCS",
        "I915_FORMAT_MOD_Yf_TILED_CCS",
        "I915_FORMAT_MOD_Y_TILED_GEN12_RC_CCS",
        "I915_FORMAT_MOD_Y_TILED_GEN12_MC_CCS",
        "I915_FORMAT_MOD_Y_TILED_GEN12_RC_CCS_CC",
        "I915_FORMAT_MOD_4_TILED",
        "I915_FORMAT_MOD_4_TILED_DG2_RC_CCS",
        "I915_FORMAT_MOD_4_TILED_DG2_MC_CCS",
        "I915_FORMAT_MOD_4_TILED_DG2_RC_CCS_CC",
        "I915_FORMAT_MOD_4_TILED_MTL_RC_CCS",
        "I915_FORMAT_MOD_4_TILED_MTL_MC_CCS",
        "I915_FORMAT_MOD_4_TILED_MTL_RC_CCS_CC",
        "I915_FORMAT_MOD_4_TILED_LNL_CCS",
        "I915_FORMAT_MOD_4_TILED_BMG_CCS",
    };

    return code < sizeof(names) / sizeof(names[0]) ? names[code] : NULL;
}

/* Write a readable name for `mod` into buf (at least PEL_DRM_MOD_NAME_SIZE
 * bytes) and return buf. Names follow drm_fourcc.h; AMD modifiers name their
 * tile version, swizzle mode and DCC bit; others name their vendor. */
static inline const char *pel_drm_mod_name(uint64_t mod, char *buf, size_t size)
{
    const unsigned vendor = (unsigned)(mod >> 56);
    const uint64_t code = mod & 0x00ffffffffffffffULL;
    const char *intel = vendor == 0x01 ? pel_drm_mod_intel_name(code) : NULL;

    if (mod == PEL_DRM_MOD_LINEAR)
        (void)snprintf(buf, size, "DRM_FORMAT_MOD_LINEAR");
    else if (mod == PEL_DRM_MOD_INVALID)
        (void)snprintf(buf, size, "DRM_FORMAT_MOD_INVALID");
    else if (intel)
        (void)snprintf(buf, size, "%s", intel);
    else if (vendor == 0x02)
        (void)snprintf(buf, size, "AMD_FMT_MOD(tile_version=%u,tile=%u,dcc=%u)",
                       (unsigned)(code & 0xff), (unsigned)((code >> 8) & 0x1f),
                       (unsigned)((code >> 13) & 0x1));
    else
        (void)snprintf(buf, size, "vendor 0x%02x modifier", vendor);
    return buf;
}

static inline int pel_drm_mod_listed(uint64_t mod, const uint64_t *list, int nb)
{
    for (int i = 0; i < nb && i < PEL_DRM_MOD_MAX; i++)
        if (list[i] == mod)
            return 1;
    return 0;
}

/* Choose the pool's modifier list (see the rule above). `consumer` may be
 * NULL with nb_consumer 0 (no list given). Returns 0 with `out` filled, or -1
 * when neither a candidate nor DRM_FORMAT_MOD_LINEAR is usable. */
static inline int pel_drm_mod_choose(const PelDrmModCandidate *cand, int nb_cand,
                                     uint32_t format_planes, const uint64_t *consumer,
                                     int nb_consumer, PelDrmModChoice *out)
{
    int linear_usable = 0;

    out->nb_modifiers = 0;
    out->linear_fallback = 0;
    for (int i = 0; i < nb_cand && i < PEL_DRM_MOD_MAX; i++) {
        const PelDrmModCandidate *c = &cand[i];

        if (!c->usable || c->mem_planes != format_planes)
            continue;
        if (c->modifier == PEL_DRM_MOD_LINEAR)
            linear_usable = 1;
        if (nb_consumer > 0 && !pel_drm_mod_listed(c->modifier, consumer, nb_consumer))
            continue;
        out->modifiers[out->nb_modifiers++] = c->modifier;
    }
    if (out->nb_modifiers > 0)
        return 0;
    if (!linear_usable)
        return -1;
    out->modifiers[0] = PEL_DRM_MOD_LINEAR;
    out->nb_modifiers = 1;
    out->linear_fallback = 1;
    return 0;
}

#endif /* AVFILTER_PELORUS_DRM_MODIFIER_H */
