/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Unit tests for ffmpeg-patches/files/pelorus_drm_modifier.h, the DRM format
 * modifier rule of the Pelorus Vulkan output pools (issue #103, ADR-0184).
 *
 * Positive: the candidates are the usable modifiers without auxiliary planes,
 * in driver order; a drm_modifiers list narrows them to its intersection.
 * Negative: an empty intersection substitutes DRM_FORMAT_MOD_LINEAR and says
 * so; nothing usable, LINEAR included, is an error; malformed lists and
 * DRM_FORMAT_MOD_INVALID are rejected. Boundary: a list of exactly
 * PEL_DRM_MOD_MAX entries parses and one more does not; a compression
 * modifier listed by the consumer is still refused. The candidate tables are
 * the ones ANV (Arc A380) and RADV (Radeon 610M) reported for NV12 and P010
 * (docs/research/0184-vulkan-drm-modifier-pools.md).
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pelorus_drm_modifier.h"

typedef struct TestCtx {
    int failures;
} TestCtx;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);         \
            t->failures++;                                                                         \
        }                                                                                          \
    } while (0)

#define I915_X 0x0100000000000001ULL
#define I915_Y 0x0100000000000002ULL
#define I915_4 0x0100000000000009ULL
#define I915_4_RC_CCS 0x010000000000000aULL
#define AMD_R_X 0x0200000000401b03ULL
#define AMD_SW_D 0x0200000000000a01ULL

/* ANV, Arc A380, NV12: LINEAR, X and 4 tiled; 2 memory planes each. A CCS
 * modifier with a metadata plane per format plane is added as a negative. */
static const PelDrmModCandidate anv_nv12[] = {
    {PEL_DRM_MOD_LINEAR, 2, 1},
    {I915_X, 2, 1},
    {I915_4, 2, 1},
    {I915_4_RC_CCS, 4, 1},
};

static void test_parse_valid(TestCtx *t)
{
    uint64_t out[PEL_DRM_MOD_MAX] = {0};

    CHECK(pel_drm_mod_parse_list(NULL, out, PEL_DRM_MOD_MAX, NULL) == 0);
    CHECK(pel_drm_mod_parse_list("", out, PEL_DRM_MOD_MAX, NULL) == 0);
    CHECK(pel_drm_mod_parse_list("0x0100000000000009", out, PEL_DRM_MOD_MAX, NULL) == 1);
    CHECK(out[0] == I915_4);
    CHECK(pel_drm_mod_parse_list(" 0x0|72057594037927937 | 0x0200000000401b03 ", out,
                                 PEL_DRM_MOD_MAX, NULL) == 3);
    CHECK(out[0] == PEL_DRM_MOD_LINEAR && out[1] == I915_X && out[2] == AMD_R_X);
}

/* The parser points at the offending token, which the filter names. */
static void test_parse_bad_token(TestCtx *t)
{
    uint64_t out[PEL_DRM_MOD_MAX] = {0};
    const char *bad = NULL;
    const char *bad_list = "0x9|0x1ffffffffffffffff|0x0";

    CHECK(pel_drm_mod_parse_list(bad_list, out, PEL_DRM_MOD_MAX, &bad) == -1);
    CHECK(bad == bad_list + 4);
    CHECK(pel_drm_mod_parse_list("0x9", out, PEL_DRM_MOD_MAX, &bad) == 1 && bad == NULL);
    CHECK(pel_drm_mod_parse_list("0x9|zz", out, PEL_DRM_MOD_MAX, &bad) == -1);
    CHECK(bad && !strcmp(bad, "zz"));
}

static void test_parse_invalid(TestCtx *t)
{
    uint64_t out[PEL_DRM_MOD_MAX] = {0};
    char list[PEL_DRM_MOD_MAX * 4 + 8];
    size_t pos = 0;

    CHECK(pel_drm_mod_parse_list("zz", out, PEL_DRM_MOD_MAX, NULL) == -1);
    CHECK(pel_drm_mod_parse_list("0x9,", out, PEL_DRM_MOD_MAX, NULL) == -1);
    CHECK(pel_drm_mod_parse_list("-1", out, PEL_DRM_MOD_MAX, NULL) == -1);
    CHECK(pel_drm_mod_parse_list("0x1ffffffffffffffff", out, PEL_DRM_MOD_MAX, NULL) == -1);
    CHECK(pel_drm_mod_parse_list("0x00ffffffffffffff", out, PEL_DRM_MOD_MAX, NULL) == -1);
    for (int i = 0; i < PEL_DRM_MOD_MAX; i++)
        pos += (size_t)snprintf(list + pos, sizeof(list) - pos, "%d|", i % 10);
    CHECK(pel_drm_mod_parse_list(list, out, PEL_DRM_MOD_MAX, NULL) == PEL_DRM_MOD_MAX);
    (void)snprintf(list + pos, sizeof(list) - pos, "7");
    CHECK(pel_drm_mod_parse_list(list, out, PEL_DRM_MOD_MAX, NULL) == -1);
}

static void test_name(TestCtx *t)
{
    char buf[PEL_DRM_MOD_NAME_SIZE];

    CHECK(!strcmp(pel_drm_mod_name(PEL_DRM_MOD_LINEAR, buf, sizeof(buf)), "DRM_FORMAT_MOD_LINEAR"));
    CHECK(
        !strcmp(pel_drm_mod_name(PEL_DRM_MOD_INVALID, buf, sizeof(buf)), "DRM_FORMAT_MOD_INVALID"));
    CHECK(!strcmp(pel_drm_mod_name(I915_4, buf, sizeof(buf)), "I915_FORMAT_MOD_4_TILED"));
    CHECK(!strcmp(pel_drm_mod_name(0x0100000000000011ULL, buf, sizeof(buf)),
                  "I915_FORMAT_MOD_4_TILED_BMG_CCS"));
    CHECK(
        !strcmp(pel_drm_mod_name(0x0100000000000012ULL, buf, sizeof(buf)), "vendor 0x01 modifier"));
    CHECK(!strcmp(pel_drm_mod_name(AMD_R_X, buf, sizeof(buf)),
                  "AMD_FMT_MOD(tile_version=3,tile=27,dcc=0)"));
    CHECK(
        !strcmp(pel_drm_mod_name(0x0c00000000000001ULL, buf, sizeof(buf)), "vendor 0x0c modifier"));
}

static void test_choose_anv(TestCtx *t)
{
    const uint64_t want_4[] = {I915_4};
    const uint64_t want_y[] = {I915_Y};
    const uint64_t want_ccs[] = {I915_4_RC_CCS};
    PelDrmModChoice c;

    CHECK(pel_drm_mod_choose(anv_nv12, 4, 2, NULL, 0, &c) == 0);
    CHECK(c.nb_modifiers == 3 && !c.linear_fallback);
    CHECK(c.modifiers[0] == PEL_DRM_MOD_LINEAR && c.modifiers[1] == I915_X &&
          c.modifiers[2] == I915_4);
    CHECK(pel_drm_mod_choose(anv_nv12, 4, 2, want_4, 1, &c) == 0);
    CHECK(c.nb_modifiers == 1 && c.modifiers[0] == I915_4 && !c.linear_fallback);
    CHECK(pel_drm_mod_choose(anv_nv12, 4, 2, want_y, 1, &c) == 0);
    CHECK(c.nb_modifiers == 1 && c.modifiers[0] == PEL_DRM_MOD_LINEAR && c.linear_fallback);
    CHECK(pel_drm_mod_choose(anv_nv12, 4, 2, want_ccs, 1, &c) == 0);
    CHECK(c.nb_modifiers == 1 && c.modifiers[0] == PEL_DRM_MOD_LINEAR && c.linear_fallback);
    CHECK(pel_drm_mod_choose(anv_nv12, 0, 2, NULL, 0, &c) == -1);
}

static void test_choose_radv(TestCtx *t)
{
    /* RADV, P010: 0x...0a01 is not listed for the R16G16 plane view, so the
     * caller marks it unusable. */
    const PelDrmModCandidate radv_p010[] = {
        {AMD_R_X, 2, 1}, {AMD_SW_D, 2, 0}, {PEL_DRM_MOD_LINEAR, 2, 1}};
    const PelDrmModCandidate no_linear[] = {{I915_X, 2, 0}, {PEL_DRM_MOD_LINEAR, 2, 0}};
    const uint64_t want_amd_sw_d[] = {AMD_SW_D};
    PelDrmModChoice c;

    CHECK(pel_drm_mod_choose(radv_p010, 3, 2, NULL, 0, &c) == 0);
    CHECK(c.nb_modifiers == 2 && c.modifiers[0] == AMD_R_X && c.modifiers[1] == PEL_DRM_MOD_LINEAR);
    CHECK(pel_drm_mod_choose(radv_p010, 3, 2, want_amd_sw_d, 1, &c) == 0);
    CHECK(c.linear_fallback && c.modifiers[0] == PEL_DRM_MOD_LINEAR);
    CHECK(pel_drm_mod_choose(radv_p010, 3, 1, NULL, 0, &c) == -1);
    CHECK(pel_drm_mod_choose(no_linear, 2, 2, NULL, 0, &c) == -1);
    CHECK(c.nb_modifiers == 0);
}

int main(void)
{
    TestCtx ctx = {0};
    TestCtx *t = &ctx;

    test_parse_valid(t);
    test_parse_invalid(t);
    test_parse_bad_token(t);
    test_name(t);
    test_choose_anv(t);
    test_choose_radv(t);
    if (ctx.failures) {
        (void)fprintf(stderr, "drm_modifier_test: %d failure(s)\n", ctx.failures);
        return 1;
    }
    (void)printf("drm_modifier_test: OK\n");
    return 0;
}
