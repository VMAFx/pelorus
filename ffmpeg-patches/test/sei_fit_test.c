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
 *
 * Zero-free carrier (issue #284, ADR-0183): every Pelorus blob leaves the
 * encoder as pel_sei_carrier_write() writes it, byte-identical to libpelorus's
 * pel_blob_carrier_encode() and turned back into the blob by pel_blob_unwrap();
 * the budget is charged for the carrier, so flat maps that fit as a carrier
 * keep them, and a payload from another producer is never converted.
 *
 * QSV (issue #286, pelorus_sei_fit_qsv.h): the per-picture budget counts the
 * SEI message as handed to the runtime, plus emulation prevention bytes on
 * H.264 only. Positive: the 1080p blob loses its maps on hevc_qsv, byte-identical
 * to the maps=0 blob, and fits whole on h264_qsv. Negative: a foreign payload
 * and a blob whose scalars exceed the budget are dropped, never cut. Boundary:
 * the largest payload that fits and one byte more, the sum over payloads, and
 * budgets that stay under the A380 limits (4 107 and about 42 420 bytes).
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus_analyze_maps.h"
#include "pelorus_sei_fit.h"
#include "pelorus_sei_fit_qsv.h"

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

static Blob pack_analyze_fill(uint16_t cols, uint16_t rows, int with_maps, int flat)
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
        for (size_t i = 0; i < cells && !flat; i++) {
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

/* vf_pelorus_analyze's blob with patterned maps. */
static Blob pack_analyze(uint16_t cols, uint16_t rows, int with_maps)
{
    return pack_analyze_fill(cols, rows, with_maps, 0);
}

/* nvenc.c's pelorus_udu_carrier() on a payload copy: strip a shortened blob,
 * then replace a blob with its carrier. Returns the written length. */
static size_t to_carrier(uint8_t **copy, size_t len, size_t n)
{
    uint8_t *carrier;
    size_t clen;

    if (len < n)
        pel_sei_strip_maps(*copy, len);
    if (!pel_sei_is_blob(*copy, len))
        return len;
    clen = pel_sei_carrier_len(*copy, len);
    carrier = malloc(clen);
    if (!carrier) {
        free(*copy);
        *copy = NULL;
        return 0;
    }
    pel_sei_carrier_write(*copy, len, carrier);
    free(*copy);
    *copy = carrier;
    return clen;
}

/* The encoder's steps for one payload: fit, copy, strip and convert, charge.
 * 0 when the payload is dropped; else the written length, copied into *out
 * (a Pelorus blob as its carrier). */
static size_t carry(const uint8_t *p, size_t n, size_t *budget, uint8_t **out)
{
    size_t len = pel_sei_fit_len(p, n, *budget);
    uint8_t *copy;
    size_t need;

    *out = NULL;
    copy = len != 0u ? malloc(len) : NULL;
    if (!copy)
        return 0;
    memcpy(copy, p, len);
    len = to_carrier(&copy, len, n);
    if (!copy)
        return 0;
    need = pel_sei_nal_bytes(copy, len, *budget);
    if (need > *budget) {
        free(copy);
        return 0;
    }
    *budget -= need;
    *out = copy;
    return len;
}

/* 1 when the written payload `w` of `wn` bytes unwraps to exactly `want`. */
static int unwraps_to(const uint8_t *w, size_t wn, const uint8_t *want, size_t want_n)
{
    uint8_t *scratch = w ? malloc(wn) : NULL;
    const uint8_t *blob = NULL;
    size_t len = 0;
    int ok;

    ok = scratch && pel_blob_unwrap(w, wn, scratch, wn, &blob, &len) == PEL_OK && blob == scratch &&
         len == want_n && memcmp(blob, want, want_n) == 0;
    free(scratch);
    return ok;
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
    CHECK(len == pel_sei_carrier_len(scal->bytes, scal->len) && out != NULL);
    CHECK(unwraps_to(out, len, scal->bytes, scal->len));
    free(out);
    CHECK(budget ==
          PEL_SEI_HEVC_USER_BUDGET - pel_sei_carrier_nal_bytes(scal->bytes, scal->len, 1000));
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
        const size_t clen = pel_sei_carrier_len(one.bytes, one.len);

        CHECK(carry(one.bytes, one.len, &budget, &out) == clen);
        CHECK(unwraps_to(out, clen, one.bytes, one.len));
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
        const size_t clen = pel_sei_carrier_len(scal.bytes, scal.len);

        CHECK(big.len > (size_t)6u * PEL_AN_MAX_CELLS);
        CHECK(carry(big.bytes, big.len, &budget, &out) == clen);
        CHECK(unwraps_to(out, clen, scal.bytes, scal.len));
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

/* One blob through both encoders: header and libpelorus agree byte for byte,
 * the carrier holds no zero byte and its NAL needs no emulation prevention. */
static void check_carrier_twin(TestCtx *t, const uint8_t *p, size_t n)
{
    const size_t clen = pel_sei_carrier_len(p, n);
    uint8_t *mine = malloc(clen);
    uint8_t *ref = malloc(PEL_CARRIER_MAX_LEN(n));
    size_t rlen = 0;

    CHECK(pel_sei_is_blob(p, n));
    CHECK(mine && ref);
    if (mine && ref) {
        pel_sei_carrier_write(p, n, mine);
        CHECK(pel_blob_carrier_encode(p, n, ref, PEL_CARRIER_MAX_LEN(n), &rlen) == PEL_OK);
        CHECK(rlen == clen && memcmp(mine, ref, clen) == 0);
        CHECK(memchr(mine, 0, clen) == NULL && pel_sei_epb(mine, clen) == 0u);
        CHECK(pel_sei_carrier_nal_bytes(p, n, SIZE_MAX) == pel_sei_nal_bytes(mine, clen, SIZE_MAX));
        CHECK(unwraps_to(mine, clen, p, n));
    }
    free(mine);
    free(ref);
}

static void test_carrier_twin(TestCtx *t)
{
    static const uint16_t grids[][2] = {{1, 1}, {10, 6}, {60, 34}, {120, 68}};
    uint8_t blob[16u + 48u + 600u];
    Blob scal = pack_analyze(60, 34, 0);

    for (size_t g = 0; g < sizeof(grids) / sizeof(grids[0]); g++) {
        for (int flat = 0; flat < 2; flat++) {
            Blob b = pack_analyze_fill(grids[g][0], grids[g][1], 1, flat);

            CHECK(b.bytes != NULL);
            if (b.bytes)
                check_carrier_twin(t, b.bytes, b.len);
            free(b.bytes);
        }
    }
    /* Tails without a zero byte: one full block, one byte past it, two blocks. */
    CHECK(scal.bytes != NULL);
    for (size_t tail = 254; tail <= 508 && scal.bytes; tail += 127) {
        memcpy(blob, scal.bytes, 16u + 48u);
        for (size_t i = 0; i < tail; i++)
            blob[16u + 48u + i] = (uint8_t)(1u + i % 255u);
        check_carrier_twin(t, blob, 16u + 48u + tail);
    }
    free(scal.bytes);
}

/* Flat content, 10x8 cells of zero maps: written whole as a carrier with its
 * maps, although the blob's own emulation prevention would not fit. */
static void test_flat_maps_fit_as_carrier(TestCtx *t)
{
    Blob flat = pack_analyze_fill(10, 8, 1, 1);
    size_t budget = PEL_SEI_HEVC_USER_BUDGET;
    uint8_t *out = NULL;

    CHECK(flat.bytes != NULL);
    if (flat.bytes) {
        const size_t clen = pel_sei_carrier_len(flat.bytes, flat.len);

        CHECK(pel_sei_nal_bytes(flat.bytes, flat.len, SIZE_MAX) > PEL_SEI_HEVC_USER_BUDGET);
        CHECK(pel_sei_carrier_nal_bytes(flat.bytes, flat.len, budget) <= budget);
        CHECK(carry(flat.bytes, flat.len, &budget, &out) == clen);
        CHECK(unwraps_to(out, clen, flat.bytes, flat.len));
    }
    free(out);
    free(flat.bytes);
}

/* Only an ABI 1.x Pelorus blob is converted. */
static void test_carrier_only_for_blobs(TestCtx *t)
{
    Blob b = pack_analyze(1, 1, 0);
    uint8_t other[300];
    uint8_t *out = NULL;
    size_t budget = PEL_SEI_HEVC_USER_BUDGET;

    memset(other, 0x5A, sizeof(other));
    CHECK(!pel_sei_is_blob(other, sizeof(other)) && !pel_sei_is_blob(NULL, 100));
    CHECK(carry(other, sizeof(other), &budget, &out) == sizeof(other));
    if (out)
        CHECK(memcmp(out, other, sizeof(other)) == 0);
    free(out);
    CHECK(b.bytes != NULL);
    if (b.bytes) {
        CHECK(pel_sei_is_blob(b.bytes, b.len) && !pel_sei_is_blob(b.bytes, 63u));
        b.bytes[16 + 8] = 2; /* abi_major 2: not a blob this encoder may rewrite */
        CHECK(!pel_sei_is_blob(b.bytes, b.len));
        b.bytes[16 + 8] = 1;
        b.bytes[16] = 'X'; /* bad magic */
        CHECK(!pel_sei_is_blob(b.bytes, b.len));
        b.bytes[16] = 'P';
        b.bytes[0] ^= 1u; /* another UUID */
        CHECK(!pel_sei_is_blob(b.bytes, b.len));
    }
    free(b.bytes);
}

_Static_assert(PEL_SEI_QSV_HEVC_BUDGET < 4107u, "hevc_qsv budget under the measured limit");
_Static_assert(PEL_SEI_QSV_H264_BUDGET < 42422u, "h264_qsv budget under the measured limit");

/* Fit `n` bytes of `p` into `*budget` as the QSV encoder does: charge what is
 * written. Returns the bytes kept; `out` (n bytes) holds them. */
static size_t qsv_charge(const uint8_t *p, size_t n, int escaped, size_t *budget, uint8_t *out)
{
    const size_t keep = pel_sei_qsv_fit(p, n, escaped, *budget, out);
    const size_t cost = keep ? pel_sei_qsv_cost(out, keep, escaped, *budget) : 0u;

    *budget -= cost;
    return keep;
}

static void test_qsv_message_len(TestCtx *t)
{
    CHECK(pel_sei_qsv_msg_len(16u) == 18u);
    CHECK(pel_sei_qsv_msg_len(254u) == 256u);
    CHECK(pel_sei_qsv_msg_len(255u) == 258u); /* size 255 is 0xFF 0x00 */
    CHECK(pel_sei_qsv_msg_len(4089u) == 4107u);
    CHECK(pel_sei_qsv_msg_len(42255u) == 42422u);
}

static void test_qsv_boundary(TestCtx *t)
{
    static uint8_t in[5000];
    static uint8_t out[5000];
    size_t budget = PEL_SEI_QSV_HEVC_BUDGET;

    memset(in, 0x61, sizeof(in));
    /* The largest payload that fits 4 040 bytes, and one byte more. */
    CHECK(pel_sei_qsv_msg_len(4023u) == PEL_SEI_QSV_HEVC_BUDGET);
    CHECK(qsv_charge(in, 4023u, 0, &budget, out) == 4023u && budget == 0u);
    budget = PEL_SEI_QSV_HEVC_BUDGET;
    CHECK(qsv_charge(in, 4024u, 0, &budget, out) == 0u && budget == PEL_SEI_QSV_HEVC_BUDGET);
    /* Per picture: two 2 000-byte payloads cost 4 018 bytes and leave 22. */
    budget = PEL_SEI_QSV_HEVC_BUDGET;
    CHECK(qsv_charge(in, 2000u, 0, &budget, out) == 2000u);
    CHECK(qsv_charge(in, 2000u, 0, &budget, out) == 2000u && budget == 22u);
    CHECK(qsv_charge(in, 100u, 0, &budget, out) == 0u);
}

static void test_qsv_sum_over_payloads(TestCtx *t)
{
    static uint8_t in[2100];
    static uint8_t out[2100];
    size_t budget = PEL_SEI_QSV_HEVC_BUDGET;

    memset(in, 0x61, sizeof(in));
    /* Each payload fits alone; 2 x 2 033 = 4 066 bytes does not fit a picture. */
    CHECK(qsv_charge(in, 2023u, 0, &budget, out) == 2023u);
    CHECK(qsv_charge(in, 2023u, 0, &budget, out) == 0u);
}

static void test_qsv_emulation_prevention(TestCtx *t)
{
    static uint8_t zeros[28000];
    static uint8_t out[28000];

    /* hevc_qsv does not count emulation prevention bytes: 4 000 zeros fit. */
    CHECK(pel_sei_qsv_fit(zeros, 4000u, 0, PEL_SEI_QSV_HEVC_BUDGET, out) == 4000u);
    /* h264_qsv does: 4 000 zeros need 1 999 more bytes. */
    CHECK(pel_sei_qsv_cost(zeros, 4000u, 1, SIZE_MAX) == pel_sei_qsv_msg_len(4000u) + 1999u);
    CHECK(pel_sei_qsv_fit(zeros, 27000u, 1, PEL_SEI_QSV_H264_BUDGET, out) == 27000u);
    CHECK(pel_sei_qsv_fit(zeros, 28000u, 1, PEL_SEI_QSV_H264_BUDGET, out) == 0u);
    CHECK(pel_sei_qsv_fit(zeros, 28000u, 0, PEL_SEI_QSV_H264_BUDGET, out) == 28000u);
}

static void check_qsv_1080p(TestCtx *t, const Blob *maps, const Blob *scal)
{
    static uint8_t out[12424];

    /* hevc_qsv: the scalar sections only, byte-identical to the maps=0 blob. */
    CHECK(pel_sei_qsv_fit(maps->bytes, maps->len, 0, PEL_SEI_QSV_HEVC_BUDGET, out) == scal->len);
    CHECK(memcmp(out, scal->bytes, scal->len) == 0);
    /* h264_qsv: 12 424 bytes (and its emulation prevention bytes) fit whole. */
    CHECK(pel_sei_qsv_fit(maps->bytes, maps->len, 1, PEL_SEI_QSV_H264_BUDGET, out) == maps->len);
    CHECK(memcmp(out, maps->bytes, maps->len) == 0);
    /* Scalars that do not fit the budget are dropped, never cut. */
    CHECK(pel_sei_qsv_fit(maps->bytes, maps->len, 0, scal->len, out) == 0u);
    CHECK(pel_sei_qsv_fit(scal->bytes, scal->len, 0, scal->len, out) == 0u);
    CHECK(pel_sei_qsv_fit(scal->bytes, scal->len, 0, pel_sei_qsv_msg_len(scal->len), out) ==
          scal->len);
}

static void test_qsv_blob(TestCtx *t)
{
    Blob maps = pack_analyze(60, 34, 1);
    Blob scal = pack_analyze(60, 34, 0);
    Blob big = pack_analyze(240, 135, 1); /* 3840x2160 at cell 16 */

    CHECK(maps.bytes && scal.bytes && big.bytes);
    if (maps.bytes && scal.bytes)
        check_qsv_1080p(t, &maps, &scal);
    if (big.bytes) {
        uint8_t *out = malloc(big.len);

        CHECK(out != NULL);
        /* A 4K blob is over h264_qsv's budget as well: it loses its maps there too. */
        if (out)
            CHECK(pel_sei_qsv_fit(big.bytes, big.len, 1, PEL_SEI_QSV_H264_BUDGET, out) ==
                  pel_sei_scalar_len(big.bytes, big.len));
        free(out);
    }
    free(maps.bytes);
    free(scal.bytes);
    free(big.bytes);
}

static void test_qsv_foreign(TestCtx *t)
{
    static uint8_t other[12424];
    static uint8_t out[12424];

    memset(other, 0x61, sizeof(other));
    CHECK(pel_sei_qsv_fit(other, sizeof(other), 0, PEL_SEI_QSV_HEVC_BUDGET, out) == 0u);
    CHECK(pel_sei_qsv_fit(other, sizeof(other), 1, PEL_SEI_QSV_H264_BUDGET, out) == sizeof(other));
}

/* The decision made before allocating (pel_sei_qsv_plan) agrees with the one that
 * writes (pel_sei_qsv_fit): a payload planned to be kept is never refused, and a
 * payload planned to be dropped is dropped by both. */
static void test_qsv_plan_matches_fit(TestCtx *t)
{
    static uint8_t zeros[28000];
    static uint8_t other[12424];
    static uint8_t out[28000];
    Blob maps = pack_analyze(60, 34, 1);
    const size_t budgets[2] = {PEL_SEI_QSV_HEVC_BUDGET, PEL_SEI_QSV_H264_BUDGET};

    memset(other, 0x61, sizeof(other));
    for (int e = 0; e < 2; e++) {
        const size_t b = budgets[e];

        CHECK(pel_sei_qsv_plan(other, 4023u, e, b) == 4023u);
        CHECK(pel_sei_qsv_plan(other, sizeof(other), e, b) == (e ? sizeof(other) : 0u));
        CHECK(pel_sei_qsv_plan(zeros, 27000u, e, b) == (e ? 27000u : 0u));
        CHECK(pel_sei_qsv_plan(zeros, 28000u, e, b) == 0u);
        for (size_t n = 4020u; n < 4030u; n++)
            CHECK(pel_sei_qsv_plan(other, n, e, b) == pel_sei_qsv_fit(other, n, e, b, out));
        if (maps.bytes) {
            const size_t plan = pel_sei_qsv_plan(maps.bytes, maps.len, e, b);
            const uint8_t *src = maps.bytes;

            CHECK(plan == pel_sei_qsv_fit(src, maps.len, e, b, out));
            CHECK(pel_sei_qsv_plan(src, maps.len, e, 100u) == 0u);
        }
    }
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
    test_carrier_twin(t);
    test_flat_maps_fit_as_carrier(t);
    test_carrier_only_for_blobs(t);
    test_qsv_message_len(t);
    test_qsv_boundary(t);
    test_qsv_sum_over_payloads(t);
    test_qsv_emulation_prevention(t);
    test_qsv_blob(t);
    test_qsv_foreign(t);
    test_qsv_plan_matches_fit(t);
    if (t->failures != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", t->failures);
        return EXIT_FAILURE;
    }
    (void)printf("sei fit: all checks passed\n");
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
