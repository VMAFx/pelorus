/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Unit tests for ffmpeg-patches/files/pelorus_sei_fit.h, which keeps
 * hevc_nvenc's udu_sei path inside NVENC's 1024-byte per-picture header limit
 * (issue #267, ADR-0181).
 *
 * Mirror: every interop.h offset the header copies matches offsetof().
 * Positive: the 1080p analyze blob (cell 32, 12 424 bytes) is carried as its
 * scalar sections, byte-identical to the maps=0 blob, and a 1x1 grid keeps its
 * maps. Negative: a payload from another producer, a Pelorus blob with a
 * section the header cannot strip, and malformed blobs are dropped, never cut.
 * Boundary: the largest grid (2^20 cells, 6 MiB) is stripped without a scan,
 * a payload exactly at the budget fits and one byte more does not.
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus_analyze_maps.h"
#include "pelorus_sei_fit.h"

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

_Static_assert(PEL_SEI_UUID_LEN == PELORUS_SIDEDATA_UUID_LEN, "uuid length mirror");
_Static_assert(PEL_SEI_HDR_MIN == sizeof(PelorusSideData), "header size mirror");
_Static_assert(PEL_SEI_DIR_ENTRY == sizeof(PelorusSectionDir), "directory entry mirror");
_Static_assert(offsetof(PelorusSideData, total_size) == 12, "total_size mirror");
_Static_assert(offsetof(PelorusSideData, section_mask) == 16, "section_mask mirror");
_Static_assert(offsetof(PelorusSideData, section_count) == 20, "section_count mirror");
_Static_assert(offsetof(PelorusSideData, header_size) == 22, "header_size mirror");
_Static_assert(offsetof(PelorusBandingSection, cell_data_offset) == 8, "banding map mirror");
_Static_assert(offsetof(PelorusBandingSection, cell_data_size) == 12, "banding map mirror");
_Static_assert(offsetof(PelorusVarianceSection, var_cell_offset) == 12, "variance map mirror");
_Static_assert(offsetof(PelorusVarianceSection, var_cell_size) == 16, "variance map mirror");
_Static_assert(offsetof(PelorusVarianceSection, edge_cell_offset) == 20, "edge map mirror");
_Static_assert(offsetof(PelorusVarianceSection, edge_cell_size) == 24, "edge map mirror");
_Static_assert(offsetof(PelorusMotionSection, mv_field_offset) == 20, "motion map mirror");
_Static_assert(offsetof(PelorusMotionSection, mv_field_size) == 24, "motion map mirror");
_Static_assert(offsetof(PelorusMotionConfSection, conf_field_offset) == 0, "conf map mirror");
_Static_assert(offsetof(PelorusMotionConfSection, conf_field_size) == 4, "conf map mirror");
_Static_assert(PEL_SEI_STRIPPABLE == ((uint32_t)PEL_SEC_BANDING | (uint32_t)PEL_SEC_VARIANCE |
                                      (uint32_t)PEL_SEC_DENOISE | (uint32_t)PEL_SEC_FILMGRAIN |
                                      (uint32_t)PEL_SEC_MOTION | (uint32_t)PEL_SEC_MOTION_CONF |
                                      (uint32_t)PEL_SEC_COMPLEXITY),
               "strippable sections");

/* All mutable test state lives here and is passed explicitly (no globals). */
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

/* One analyze blob, heap-allocated; bytes == NULL when packing failed. */
typedef struct Blob {
    uint8_t *bytes;
    size_t len;
} Blob;

static Blob pack_analyze(uint16_t cols, uint16_t rows, int with_maps)
{
    const size_t cells = (size_t)cols * rows;
    PelorusSideData meta;
    PelAnSections sec;
    PelAnMaps maps = {NULL, calloc(cells, 1), calloc(cells, sizeof(float)), calloc(cells, 1)};
    Blob b = {NULL, 0};
    size_t need = 0;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 7;
    meta.bit_depth = 8;
    meta.grid_cols = cols;
    meta.grid_rows = rows;
    meta.producer_id = 0x41524C50u; /* PEL_FOURCC('P', 'L', 'R', 'A') */
    memset(&sec, 0, sizeof(sec));
    sec.var.global_variance = 0.125f;
    sec.band.flat_area_fraction = 0.5f;
    sec.cx.complexity = 0.25f;
    if (maps.band && maps.var && maps.edge && pel_an_blob_size(&meta, with_maps, &need) == PEL_OK)
        b.bytes = calloc(need, 1);
    if (b.bytes) {
        for (size_t i = 0; i < cells; i++) {
            maps.band[i] = (uint8_t)(i * 7u);
            maps.var[i] = 0.001f * (float)(i % 100u);
            maps.edge[i] = (uint8_t)(255u - (i & 0xFFu));
        }
        if (pel_an_pack(&meta, &sec, with_maps ? &maps : NULL, b.bytes, need, &b.len) != PEL_OK) {
            free(b.bytes);
            b.bytes = NULL;
        }
    }
    free(maps.band);
    free(maps.var);
    free(maps.edge);
    return b;
}

/* The encoder's three steps for one payload: fit, copy (strip), charge. 0 when
 * the payload is dropped; else the carried length, copied into *out. */
static size_t carry(const uint8_t *p, size_t n, size_t *budget, uint8_t **out)
{
    size_t len = pel_sei_fit_len(p, n, *budget);
    uint8_t *copy;

    *out = NULL;
    copy = len != 0u ? malloc(len) : NULL;
    if (!copy)
        return 0;
    memcpy(copy, p, len);
    if (len < n)
        pel_sei_strip_maps(copy, len);
    if (pel_sei_nal_bytes(copy, len, *budget) > *budget) {
        free(copy);
        return 0;
    }
    *budget -= pel_sei_nal_bytes(copy, len, *budget);
    *out = copy;
    return len;
}

static void test_nal_bytes(TestCtx *t)
{
    uint8_t buf[1000];
    const uint8_t zeros[6] = {0};

    /* NVENC wrote 1011 bytes (start code to trailing bits) for this payload;
     * the estimate is that plus one margin byte. */
    memset(buf, 'a', sizeof(buf));
    CHECK(pel_sei_nal_bytes(buf, 999, 2000) == 1012u);
    /* 00 00 00 00 00 00 is coded 00 00 03 00 00 03 00 00. */
    CHECK(pel_sei_epb(zeros, sizeof(zeros)) == 2u);
    CHECK(pel_sei_nal_bytes(zeros, sizeof(zeros), 100) == 6u + 0u + 10u + 2u);
    /* Above the limit nothing is scanned and the result exceeds the limit. */
    CHECK(pel_sei_nal_bytes(NULL, 5000, 768) > 768u);
    CHECK(PEL_SEI_HEVC_USER_BUDGET == 768u);
}

static void check_1080p(TestCtx *t, const Blob *maps, const Blob *scal)
{
    size_t budget = PEL_SEI_HEVC_USER_BUDGET;
    uint8_t *out = NULL;
    size_t len;

    CHECK(maps->len == 12424u && scal->len == 184u);
    CHECK(pel_sei_scalar_len(maps->bytes, maps->len) == scal->len);
    len = carry(maps->bytes, maps->len, &budget, &out);
    CHECK(len == scal->len && out != NULL);
    if (out)
        CHECK(memcmp(out, scal->bytes, scal->len) == 0);
    free(out);
    CHECK(budget == PEL_SEI_HEVC_USER_BUDGET - pel_sei_nal_bytes(scal->bytes, scal->len, 1000));
    /* A maps=0 blob has nothing to strip and fits whole. */
    CHECK(pel_sei_scalar_len(scal->bytes, scal->len) == 0u);
    CHECK(pel_sei_fit_len(scal->bytes, scal->len, PEL_SEI_HEVC_USER_BUDGET) == scal->len);
}

static void test_1080p_strips_to_scalars(TestCtx *t)
{
    Blob maps = pack_analyze(60, 34, 1);
    Blob scal = pack_analyze(60, 34, 0);

    CHECK(maps.bytes && scal.bytes);
    if (maps.bytes && scal.bytes)
        check_1080p(t, &maps, &scal);
    free(maps.bytes);
    free(scal.bytes);
}

static void test_small_grid_keeps_maps(TestCtx *t)
{
    Blob one = pack_analyze(1, 1, 1);
    size_t budget = PEL_SEI_HEVC_USER_BUDGET;
    uint8_t *out = NULL;

    CHECK(one.bytes != NULL);
    if (one.bytes) {
        CHECK(carry(one.bytes, one.len, &budget, &out) == one.len);
        if (out)
            CHECK(memcmp(out, one.bytes, one.len) == 0);
    }
    free(out);
    free(one.bytes);
}

static void test_largest_grid(TestCtx *t)
{
    Blob big = pack_analyze(1024, 1024, 1);
    Blob scal = pack_analyze(1024, 1024, 0);
    size_t budget = PEL_SEI_HEVC_USER_BUDGET;
    uint8_t *out = NULL;

    CHECK(big.bytes && scal.bytes);
    if (big.bytes && scal.bytes) {
        CHECK(big.len > (size_t)6u * PEL_AN_MAX_CELLS);
        CHECK(carry(big.bytes, big.len, &budget, &out) == scal.len);
        if (out)
            CHECK(memcmp(out, scal.bytes, scal.len) == 0);
    }
    free(out);
    free(big.bytes);
    free(scal.bytes);
}

static void test_budget_edge(TestCtx *t)
{
    uint8_t buf[800];
    size_t budget;
    uint8_t *out = NULL;
    /* n + n / 255 + 10 == 768 at n == 756 for a payload without zeros. */
    const size_t fits = 756;

    memset(buf, 'a', sizeof(buf));
    budget = PEL_SEI_HEVC_USER_BUDGET;
    CHECK(carry(buf, fits, &budget, &out) == fits && budget == 0u);
    free(out);
    budget = PEL_SEI_HEVC_USER_BUDGET;
    CHECK(carry(buf, fits + 1u, &budget, &out) == 0u && out == NULL);
    free(out);
    CHECK(budget == PEL_SEI_HEVC_USER_BUDGET);
    /* The budget is per picture: a second payload sees what the first left. */
    budget = PEL_SEI_HEVC_USER_BUDGET;
    CHECK(carry(buf, 400, &budget, &out) == 400u);
    free(out);
    CHECK(carry(buf, 400, &budget, &out) == 0u);
    free(out);
}

/* Each case must be dropped (0), never cut short. */
static void test_rejects_foreign(TestCtx *t)
{
    uint8_t other[2000];

    memset(other, 0x5A, sizeof(other));
    CHECK(pel_sei_fit_len(other, sizeof(other), PEL_SEI_HEVC_USER_BUDGET) == 0u);
    CHECK(pel_sei_scalar_len(other, 40) == 0u);
    CHECK(pel_sei_scalar_len(NULL, 5000) == 0u);
}

/* Corrupt one header or directory field at a time; restore it after. */
static void check_corrupt_blob(TestCtx *t, uint8_t *blob, size_t len)
{
    uint8_t *img = blob + PEL_SEI_UUID_LEN;
    const uint8_t qp_report = (uint8_t)PEL_SEC_QPREPORT;

    img[16] |= qp_report; /* a section with maps this file does not know */
    CHECK(pel_sei_scalar_len(blob, len) == 0u);
    img[16] &= (uint8_t)(0xFFu ^ qp_report);
    CHECK(pel_sei_scalar_len(blob, len) == 184u);
    img[8] = 2; /* abi_major 2 */
    CHECK(pel_sei_scalar_len(blob, len) == 0u);
    img[8] = 1;
    img[20] = 33; /* more directory entries than section bits */
    CHECK(pel_sei_scalar_len(blob, len) == 0u);
    img[20] = 3;
    CHECK(pel_sei_scalar_len(blob, len - 1u) == 0u); /* total_size past the end */
    pel_sei_wr32(img + 48 + 4, 0xFFFFFFF0u);         /* first section outside total_size */
    CHECK(pel_sei_scalar_len(blob, len) == 0u);
}

static void test_rejects_corrupt(TestCtx *t)
{
    Blob maps = pack_analyze(60, 34, 1);

    CHECK(maps.bytes != NULL);
    if (maps.bytes)
        check_corrupt_blob(t, maps.bytes, maps.len);
    free(maps.bytes);
}

int main(void)
{
    TestCtx ctx = {0};
    TestCtx *t = &ctx;

    test_nal_bytes(t);
    test_1080p_strips_to_scalars(t);
    test_small_grid_keeps_maps(t);
    test_largest_grid(t);
    test_budget_edge(t);
    test_rejects_foreign(t);
    test_rejects_corrupt(t);
    if (t->failures != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", t->failures);
        return EXIT_FAILURE;
    }
    (void)printf("sei fit: all checks passed\n");
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
