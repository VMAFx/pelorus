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
 * Own rules (ADR-0236): the NAL size, emulation prevention count and
 * truncation rule match frozen copies of the rules the shared FFmpeg fix
 * series carried until FFmpeg 96b7f8281a, for every length up to 1100 bytes,
 * four byte patterns and three limits. The hook of patch 0022 runs on a model
 * of one picture's SEI array, followed by a model of FFmpeg's own 512-byte
 * check: the room it gives a Pelorus blob is the room both limits leave, for
 * every earlier payload of up to 600 bytes, so FFmpeg never drops a blob the
 * hook kept. Each sei-fit-planted-<rule> build changes one rule and must fail.
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
 * the two budget constants.
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus_analyze_maps.h"
/* A sei-fit-planted-<rule> build includes its planted copy instead of the real
 * header. */
#ifdef PEL_SEI_FIT_PLANTED
#include PEL_SEI_FIT_PLANTED
#else
#include "pelorus_sei_fit.h"
#endif
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

/*
 * The rules of the shared FFmpeg fix series v0.3.0 (nvenc_sei.h of patch
 * 0027) as Pelorus took them over, frozen as the oracle pelorus_sei_fit.h
 * must keep matching (ADR-0236): one emulation prevention byte before every
 * byte <= 3 that follows two zeros; a NAL of start code, NAL header, type,
 * n / 255 + 1 size bytes, payload, trailing bits and one boundary byte, plus
 * the emulation prevention bytes of a payload of at most `limit` bytes; and
 * the truncation of a payload whose emulation prevention bytes exceed the
 * escape buffer of 4/3 of the largest payload plus 5.
 */
static size_t frozen_epb(const uint8_t *p, size_t n, size_t zeros)
{
    size_t extra = 0;

    for (size_t i = 0; i < n; i++) {
        if (zeros >= 2u && p[i] <= 3u) {
            extra++;
            zeros = 0;
        }
        zeros = p[i] == 0u ? zeros + 1u : 0u;
    }
    return extra;
}

static size_t frozen_nal_bytes(const uint8_t *p, size_t n, size_t limit)
{
    if (n > limit || !p)
        return n + n / 255u + 10u;
    return n + n / 255u + 10u + frozen_epb(p, n, 0);
}

static int frozen_truncated(const uint8_t *p, size_t n, size_t pmax)
{
    return n + 3u + frozen_epb(p, n, n % 255u == 0u ? 1u : 0u) > 4u * (pmax + 5u) / 3u;
}

/* One SEI payload as prepare_sei_data_array() holds it. */
typedef struct Sei {
    uint8_t *payload;
    size_t size;
    uint32_t type;
} Sei;

#define PIC_MAX_SEI 64

/* One picture's SEI array; hevc 0 is h264_nvenc. */
typedef struct Picture {
    Sei sei[PIC_MAX_SEI];
    int count;
    int hevc;
} Picture;

static void pic_init(Picture *pic, int hevc)
{
    memset(pic, 0, sizeof(*pic));
    pic->hevc = hevc;
}

static void pic_free(Picture *pic)
{
    for (int i = 0; i < pic->count; i++)
        free(pic->sei[i].payload);
    pic->count = 0;
}

/* nvenc.c's pelorus_udu_carrier() (patch 0022, ADR-0236) on sei[count], the
 * copy av_memdup() just made. Any other payload stays as it is. On hevc_nvenc
 * the SEI before it are charged into the picture's room, a blob whose carrier
 * does not fit loses its maps, and a carrier that still does not fit is
 * dropped. 0 keeps the payload (a Pelorus blob as its carrier), 1 drops it, -1
 * is an allocation failure. */
static int hook(Picture *pic)
{
    Sei *cur = &pic->sei[pic->count];
    size_t budget = SIZE_MAX;
    size_t keep = cur->size;
    size_t len;
    uint8_t *carrier;

    if (!pel_sei_is_blob(cur->payload, cur->size))
        return 0;
    if (pic->hevc) {
        PelSeiHevcRoom room = PEL_SEI_HEVC_ROOM_INIT;

        for (int i = 0; i < pic->count; i++) {
            pel_sei_hevc_charge(&room, pic->sei[i].payload, pic->sei[i].type, pic->sei[i].size);
        }
        budget = pel_sei_hevc_budget(&room);
        keep = pel_sei_fit_len(cur->payload, cur->size, budget);
        if (keep != 0u && keep < cur->size) {
            pel_sei_strip_maps(cur->payload, keep);
        } else {
            keep = cur->size;
        }
    }
    len = pel_sei_carrier_len(cur->payload, keep);
    carrier = (uint64_t)len > UINT32_MAX ? NULL : malloc(len);
    if (!carrier) {
        free(cur->payload);
        cur->payload = NULL;
        return -1;
    }
    pel_sei_carrier_write(cur->payload, keep, carrier);
    free(cur->payload);
    cur->payload = carrier;
    cur->size = len;
    if (budget != SIZE_MAX && pel_sei_nal_bytes(carrier, len, budget) > budget) {
        free(cur->payload);
        cur->payload = NULL;
        return 1;
    }
    return 0;
}

/* Append a copy of the `n`-byte payload `p` of `type` as the encoder does,
 * through the hook for user data unregistered. Returns the written length, 0
 * when the payload is dropped. */
static size_t pic_add(Picture *pic, const uint8_t *p, size_t n, uint32_t type)
{
    Sei *cur = &pic->sei[pic->count];
    int res;

    if (pic->count >= PIC_MAX_SEI || n == 0u)
        return 0;
    cur->payload = malloc(n);
    if (!cur->payload)
        return 0;
    memcpy(cur->payload, p, n);
    cur->size = n;
    cur->type = type;
    res = type == 5u ? hook(pic) : 0;
    if (res != 0)
        return 0;
    pic->count++;
    return cur->size;
}

/* FFmpeg's nvenc_hevc_sei_size_check() (96b7f8281a) at the end of
 * prepare_sei_data_array(): in array order, a SEI that would take the
 * picture's payload bytes above NVENC_HEVC_SEI_MAX_PAYLOAD (512, written here
 * as FFmpeg has it) is dropped. Returns the number dropped. */
static int pic_finish(Picture *pic)
{
    size_t total = 0;
    int kept = 0;
    const int count = pic->count;

    if (!pic->hevc)
        return 0;
    for (int i = 0; i < count; i++) {
        if (total + pic->sei[i].size <= 512u) {
            total += pic->sei[i].size;
            pic->sei[kept++] = pic->sei[i];
            continue;
        }
        free(pic->sei[i].payload);
    }
    pic->count = kept;
    return count - kept;
}

/* Annex B bytes of the picture's SEI by the frozen rules. */
static size_t pic_nal(const Picture *pic)
{
    size_t sum = 0;

    for (int i = 0; i < pic->count; i++)
        sum += frozen_nal_bytes(pic->sei[i].payload, pic->sei[i].size, SIZE_MAX);
    return sum;
}

/* 1 when the written payload `w` of `wn` bytes unwraps to exactly `want`. */
static int unwraps_to(const uint8_t *w, size_t wn, const uint8_t *want, size_t want_n)
{
    uint8_t *scratch = w && wn != 0u ? malloc(wn) : NULL;
    const uint8_t *blob = NULL;
    size_t len = 0;
    int ok;

    ok = scratch && pel_blob_unwrap(w, wn, scratch, wn, &blob, &len) == PEL_OK && blob == scratch &&
         len == want_n && memcmp(blob, want, want_n) == 0;
    free(scratch);
    return ok;
}

/* Fill `buf` with pattern `k`: letters, zeros, 00 00 0x runs, or an LCG. */
static void fill_pattern(uint8_t *buf, size_t n, int k)
{
    uint32_t x = 0x9E3779B9u;

    for (size_t i = 0; i < n; i++) {
        x = x * 1664525u + 1013904223u;
        buf[i] = k == 0 ? (uint8_t)'a' :
                 k == 1 ? 0u :
                 k == 2 ? (uint8_t)(i % 3u == 2u ? i % 5u : 0u) :
                          (uint8_t)(x >> 29u); /* 0..7: many zeros and 0..3 */
    }
}

/* 1 when the header's result and the frozen one make the same fit decision
 * and agree exactly on a payload that fits. Over the limit both only promise
 * more than `limit`: the header compares the NAL size, the frozen copy the
 * payload size, with the limit before it scans (36 such cases below, none
 * fits). */
static int same_fit(size_t mine, size_t frozen, size_t limit)
{
    if (mine <= limit || frozen <= limit)
        return mine == frozen;
    return 1;
}

/* The header's rules and the frozen ones agree on every length up to two
 * size byte steps past the budget, four patterns, three limits, and for the
 * truncation rule three largest-payload sizes. */
static void test_rules_match_frozen(TestCtx *t)
{
    static uint8_t buf[1100];
    static const size_t limits[] = {0u, PEL_SEI_HEVC_USER_BUDGET, SIZE_MAX};
    int mismatches = 0;

    for (int k = 0; k < 4; k++) {
        fill_pattern(buf, sizeof(buf), k);
        for (size_t n = 0; n <= sizeof(buf); n++) {
            for (size_t l = 0; l < sizeof(limits) / sizeof(limits[0]); l++) {
                mismatches += !same_fit(pel_sei_nal_bytes(buf, n, limits[l]),
                                        frozen_nal_bytes(buf, n, limits[l]), limits[l]);
            }
            mismatches += pel_sei_epb_count(buf, n, 0) != frozen_epb(buf, n, 0);
            mismatches += pel_sei_epb_count(buf, n, 2) != frozen_epb(buf, n, 2);
            mismatches += pel_sei_udu_truncated(buf, n, n) != frozen_truncated(buf, n, n);
            mismatches += pel_sei_udu_truncated(buf, n, n / 2u) != frozen_truncated(buf, n, n / 2u);
            mismatches += pel_sei_udu_truncated(buf, n, 2000u) != frozen_truncated(buf, n, 2000u);
        }
    }
    CHECK(mismatches == 0);
    CHECK(pel_sei_nal_bytes(NULL, 100, SIZE_MAX) == frozen_nal_bytes(NULL, 100, SIZE_MAX));
    CHECK(PEL_SEI_HEVC_NON_VCL_LIMIT == 1024u && PEL_SEI_HEVC_RESERVE == 256u);
    CHECK(PEL_SEI_HEVC_USER_BUDGET == 768u && PEL_SEI_HEVC_PAYLOAD_LIMIT == 512u);
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
    CHECK(pel_sei_epb_count(zeros, sizeof(zeros), 0) == 2u);
    CHECK(pel_sei_nal_bytes(zeros, sizeof(zeros), 100) == 6u + 0u + 10u + 2u);
    /* Above the limit nothing is scanned and the result exceeds the limit. */
    CHECK(pel_sei_nal_bytes(NULL, 5000, 768) > 768u);
    CHECK(PEL_SEI_HEVC_USER_BUDGET == 768u);
#if SIZE_MAX > UINT32_MAX
    /* Beyond NVENC's 32-bit payloadSize: never scanned, never fits. */
    CHECK(pel_sei_nal_bytes(buf, (size_t)UINT32_MAX + 1u, SIZE_MAX) == SIZE_MAX);
    CHECK(pel_sei_qsv_cost(buf, (size_t)UINT32_MAX + 1u, 1, SIZE_MAX) == SIZE_MAX);
    CHECK(pel_sei_nal_bytes(NULL, UINT32_MAX, SIZE_MAX) < SIZE_MAX);
#endif
}

static void check_1080p(TestCtx *t, const Blob *maps, const Blob *scal)
{
    Picture pic;
    size_t len;

    pic_init(&pic, 1);
    CHECK(maps->len == 12424u && scal->len == 184u);
    CHECK(pel_sei_scalar_len(maps->bytes, maps->len) == scal->len);
    len = pic_add(&pic, maps->bytes, maps->len, 5u);
    CHECK(len == pel_sei_carrier_len(scal->bytes, scal->len) && pic.count == 1);
    CHECK(pic.count == 1 && unwraps_to(pic.sei[0].payload, len, scal->bytes, scal->len));
    CHECK(pic_finish(&pic) == 0 && pic.count == 1);
    pic_free(&pic);
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
    Picture pic;

    pic_init(&pic, 1);
    CHECK(one.bytes != NULL);
    if (one.bytes) {
        const size_t clen = pel_sei_carrier_len(one.bytes, one.len);

        CHECK(pic_add(&pic, one.bytes, one.len, 5u) == clen);
        CHECK(pic.count == 1 && unwraps_to(pic.sei[0].payload, clen, one.bytes, one.len));
    }
    pic_free(&pic);
    free(one.bytes);
}

static void test_largest_grid(TestCtx *t)
{
    Blob big = pack_analyze(1024, 1024, 1);
    Blob scal = pack_analyze(1024, 1024, 0);
    Picture pic;

    pic_init(&pic, 1);
    CHECK(big.bytes && scal.bytes);
    if (big.bytes && scal.bytes) {
        const size_t clen = pel_sei_carrier_len(scal.bytes, scal.len);

        CHECK(big.len > (size_t)6u * PEL_AN_MAX_CELLS);
        CHECK(pic_add(&pic, big.bytes, big.len, 5u) == clen);
        CHECK(pic.count == 1 && unwraps_to(pic.sei[0].payload, clen, scal.bytes, scal.len));
    }
    pic_free(&pic);
    free(big.bytes);
    free(scal.bytes);
}

/* What one 300-byte payload of type 4 without zeros leaves: 212 payload bytes,
 * 768 - 311 = 457 NAL bytes, so a carrier gets 212 + 0 + 10 = 222. */
static void test_room_pinned(TestCtx *t)
{
    uint8_t buf[600];
    PelSeiHevcRoom room = PEL_SEI_HEVC_ROOM_INIT;

    memset(buf, 'a', sizeof(buf));
    CHECK(pel_sei_hevc_budget(&room) == 524u);
    pel_sei_hevc_charge(&room, buf, 4u, 300u);
    CHECK(room.payload == 212u && room.nal == 457u && pel_sei_hevc_budget(&room) == 222u);
    /* A payload FFmpeg drops (over the payload left) costs NAL bytes only. */
    pel_sei_hevc_charge(&room, buf, 4u, 213u);
    CHECK(room.payload == 212u && room.nal == 457u - 223u);
    /* One exactly at the payload left is charged and leaves nothing. */
    pel_sei_hevc_charge(&room, buf, 4u, 212u);
    CHECK(room.payload == 0u && room.nal == 12u && pel_sei_hevc_budget(&room) == 10u);
    pel_sei_hevc_charge(&room, buf, 4u, 600u);
    CHECK(room.payload == 0u && room.nal == 0u && pel_sei_hevc_budget(&room) == 0u);
}

/* A carrier of c bytes takes c + c / 255 + 10 NAL bytes and fits the budget
 * exactly when it fits both limits, for every carrier and payload room. */
static void test_budget_equivalence(TestCtx *t)
{
    static uint8_t carrier[1100];
    int mismatches = 0;

    memset(carrier, 0x61, sizeof(carrier));
    for (size_t pay = 0; pay <= PEL_SEI_HEVC_PAYLOAD_LIMIT; pay += 7u) {
        for (size_t nal = 0; nal <= PEL_SEI_HEVC_USER_BUDGET; nal += 13u) {
            const PelSeiHevcRoom room = {nal, pay};
            const size_t budget = pel_sei_hevc_budget(&room);

            for (size_t c = 1; c <= sizeof(carrier); c++) {
                const size_t need = pel_sei_nal_bytes(carrier, c, budget);
                const int both = c <= pay && frozen_nal_bytes(carrier, c, SIZE_MAX) <= nal;

                mismatches += (need <= budget) != both;
            }
        }
    }
    CHECK(mismatches == 0);
}

/* One SEI of `n` bytes of pattern `k` before a maps=0 blob: the hook keeps the
 * blob exactly when its carrier fits what the frozen rules and FFmpeg's
 * 512-byte check leave, and FFmpeg's check then keeps every blob the hook
 * kept, inside 768 NAL bytes. */
static int check_room_case(TestCtx *t, const Blob *scal, const uint8_t *pre, size_t n)
{
    const size_t clen = pel_sei_carrier_len(scal->bytes, scal->len);
    const size_t pre_nal = frozen_nal_bytes(pre, n, SIZE_MAX);
    const size_t nal_left = pre_nal < 768u ? 768u - pre_nal : 0u;
    const size_t pay_left = n <= 512u ? 512u - n : 512u;
    const int want = clen <= pay_left && clen + clen / 255u + 10u <= nal_left;
    Picture pic;
    int got;
    int bad = 0;

    pic_init(&pic, 1);
    CHECK(pic_add(&pic, pre, n, 4u) == n);
    got = pic_add(&pic, scal->bytes, scal->len, 5u) != 0u;
    bad += got != want;
    (void)pic_finish(&pic);
    if (got && pic.count < 1) {
        bad++;
    } else if (got) {
        bad += pic.sei[pic.count - 1].size != clen;
        bad += pic_nal(&pic) > 768u && pic.count > 1;
    }
    pic_free(&pic);
    return bad;
}

static void test_room_matches_oracle(TestCtx *t)
{
    static uint8_t pre[600];
    Blob scal = pack_analyze(60, 34, 0);
    int bad = 0;

    CHECK(scal.bytes != NULL);
    for (int k = 0; k < 4 && scal.bytes; k++) {
        fill_pattern(pre, sizeof(pre), k);
        for (size_t n = 1; n <= sizeof(pre); n++)
            bad += check_room_case(t, &scal, pre, n);
    }
    CHECK(bad == 0);
    free(scal.bytes);
}

/* The blob after an earlier user data unregistered payload of another
 * producer: 300 bytes leave a 222-byte budget and the 1080p blob goes out as
 * its scalar carrier; 400 bytes leave 122 and it is dropped, never cut. */
static void test_after_earlier_udu(TestCtx *t)
{
    Blob maps = pack_analyze(60, 34, 1);
    Blob scal = pack_analyze(60, 34, 0);
    uint8_t other[400];
    Picture pic;

    memset(other, 0x5A, sizeof(other));
    CHECK(maps.bytes && scal.bytes);
    if (maps.bytes && scal.bytes) {
        const size_t clen = pel_sei_carrier_len(scal.bytes, scal.len);

        CHECK(clen <= 212u && clen > 112u);
        pic_init(&pic, 1);
        CHECK(pic_add(&pic, other, 300u, 5u) == 300u);
        CHECK(pic_add(&pic, maps.bytes, maps.len, 5u) == clen);
        CHECK(pic.count == 2 && unwraps_to(pic.sei[1].payload, clen, scal.bytes, scal.len));
        CHECK(pic_finish(&pic) == 0 && pic.count == 2);
        pic_free(&pic);
        pic_init(&pic, 1);
        CHECK(pic_add(&pic, other, 400u, 5u) == 400u);
        CHECK(pic_add(&pic, maps.bytes, maps.len, 5u) == 0u && pic.count == 1);
        pic_free(&pic);
    }
    free(maps.bytes);
    free(scal.bytes);
}

/* FFmpeg's check on the arrays the hook leaves: a later payload that does not
 * fit is FFmpeg's to drop, the blob stays; an earlier one FFmpeg drops is not
 * charged as payload, so the blob after it stays too; one that takes exactly
 * the 512 payload bytes leaves the blob no room, and the hook drops it. */
static void test_ffmpeg_check_keeps_blob(TestCtx *t)
{
    Blob scal = pack_analyze(60, 34, 0);
    uint8_t other[520];
    Picture pic;

    memset(other, 0x61, sizeof(other));
    CHECK(scal.bytes != NULL);
    if (scal.bytes) {
        const size_t clen = pel_sei_carrier_len(scal.bytes, scal.len);

        pic_init(&pic, 1);
        CHECK(pic_add(&pic, scal.bytes, scal.len, 5u) == clen);
        CHECK(pic_add(&pic, other, 400u, 4u) == 400u);
        CHECK(pic_finish(&pic) == 1 && pic.count == 1 && pic.sei[0].size == clen);
        pic_free(&pic);
        pic_init(&pic, 1);
        CHECK(pic_add(&pic, other, 520u, 4u) == 520u);
        CHECK(pic_add(&pic, scal.bytes, scal.len, 5u) == clen);
        CHECK(pic_finish(&pic) == 1 && pic.count == 1 && pic.sei[0].size == clen);
        pic_free(&pic);
        pic_init(&pic, 1);
        CHECK(pic_add(&pic, other, 512u, 4u) == 512u);
        CHECK(pic_add(&pic, scal.bytes, scal.len, 5u) == 0u && pic.count == 1);
        pic_free(&pic);
    }
    free(scal.bytes);
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
        CHECK(memchr(mine, 0, clen) == NULL && pel_sei_epb_count(mine, clen, 0) == 0u);
        CHECK(!pel_sei_udu_truncated(mine, clen, clen));
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

/* Flat content, 7x7 cells of zero maps: written whole as a carrier with its
 * maps, although the blob's own emulation prevention would not fit and NVENC
 * would write the blob itself truncated. */
static void test_flat_maps_fit_as_carrier(TestCtx *t)
{
    Blob flat = pack_analyze_fill(7, 7, 1, 1);
    const PelSeiHevcRoom room = PEL_SEI_HEVC_ROOM_INIT;
    const size_t budget = pel_sei_hevc_budget(&room);
    Picture pic;

    pic_init(&pic, 1);
    CHECK(flat.bytes != NULL);
    if (flat.bytes) {
        const size_t clen = pel_sei_carrier_len(flat.bytes, flat.len);

        CHECK(pel_sei_nal_bytes(flat.bytes, flat.len, SIZE_MAX) > budget);
        CHECK(pel_sei_udu_truncated(flat.bytes, flat.len, flat.len));
        CHECK(pel_sei_carrier_nal_bytes(flat.bytes, flat.len, budget) <= budget);
        CHECK(pic_add(&pic, flat.bytes, flat.len, 5u) == clen);
        CHECK(pic.count == 1 && unwraps_to(pic.sei[0].payload, clen, flat.bytes, flat.len));
        CHECK(pic_finish(&pic) == 0);
    }
    pic_free(&pic);
    free(flat.bytes);
}

/* Only an ABI 1.x Pelorus blob is converted. */
static void test_carrier_only_for_blobs(TestCtx *t)
{
    Blob b = pack_analyze(1, 1, 0);
    uint8_t other[300];
    Picture pic;

    pic_init(&pic, 1);
    memset(other, 0x5A, sizeof(other));
    CHECK(!pel_sei_is_blob(other, sizeof(other)) && !pel_sei_is_blob(NULL, 100));
    CHECK(pic_add(&pic, other, sizeof(other), 5u) == sizeof(other));
    CHECK(pic.count == 1 && memcmp(pic.sei[0].payload, other, sizeof(other)) == 0);
    pic_free(&pic);
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

_Static_assert(PEL_SEI_QSV_HEVC_BUDGET == 4040u, "hevc_qsv budget");
_Static_assert(PEL_SEI_QSV_H264_BUDGET == 40960u, "h264_qsv budget");

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
    CHECK(pel_sei_qsv_msg_len(4023u) == 4040u);
    CHECK(pel_sei_qsv_msg_len(40799u) == 40960u);
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
        if (out) {
            CHECK(pel_sei_qsv_fit(big.bytes, big.len, 1, PEL_SEI_QSV_H264_BUDGET, out) ==
                  pel_sei_scalar_len(big.bytes, big.len));
        }
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

/* The hook of patch 0022 on the paths the GPU smoke cannot reach from a filter
 * graph. A blob with a section this file cannot strip and a carrier over the
 * budget is dropped by the hook, never cut. Without a budget (h264_nvenc) the
 * same blob and the 1080p blob go out whole as carriers, maps included. */
static void test_hook_unstrippable_and_no_budget(TestCtx *t)
{
    Blob maps = pack_analyze(60, 34, 1);
    Picture pic;
    size_t len;

    CHECK(maps.bytes != NULL);
    if (!maps.bytes)
        return;
    pic_init(&pic, 0);
    len = pic_add(&pic, maps.bytes, maps.len, 5u);
    CHECK(len == pel_sei_carrier_len(maps.bytes, maps.len));
    CHECK(pic.count == 1 && unwraps_to(pic.sei[0].payload, len, maps.bytes, maps.len));
    pic_free(&pic);
    maps.bytes[PEL_SEI_UUID_LEN + 16u] |= (uint8_t)PEL_SEC_QPREPORT;
    CHECK(pel_sei_is_blob(maps.bytes, maps.len));
    CHECK(pel_sei_fit_len(maps.bytes, maps.len, PEL_SEI_HEVC_USER_BUDGET) == 0u);
    pic_init(&pic, 1);
    CHECK(pic_add(&pic, maps.bytes, maps.len, 5u) == 0u && pic.count == 0);
    pic_free(&pic);
    pic_init(&pic, 0);
    len = pic_add(&pic, maps.bytes, maps.len, 5u);
    CHECK(len == pel_sei_carrier_len(maps.bytes, maps.len));
    CHECK(pic.count == 1 && unwraps_to(pic.sei[0].payload, len, maps.bytes, maps.len));
    pic_free(&pic);
    free(maps.bytes);
}

int main(void)
{
    TestCtx ctx = {0};
    TestCtx *t = &ctx;

    test_nal_bytes(t);
    test_rules_match_frozen(t);
    test_1080p_strips_to_scalars(t);
    test_small_grid_keeps_maps(t);
    test_largest_grid(t);
    test_room_pinned(t);
    test_budget_equivalence(t);
    test_room_matches_oracle(t);
    test_after_earlier_udu(t);
    test_ffmpeg_check_keeps_blob(t);
    test_rejects_foreign(t);
    test_rejects_corrupt(t);
    test_carrier_twin(t);
    test_flat_maps_fit_as_carrier(t);
    test_carrier_only_for_blobs(t);
    test_hook_unstrippable_and_no_budget(t);
    test_qsv_message_len(t);
    test_qsv_boundary(t);
    test_qsv_sum_over_payloads(t);
    test_qsv_emulation_prevention(t);
    test_qsv_blob(t);
    test_qsv_foreign(t);
    test_qsv_plan_matches_fit(t);
#ifdef PEL_SEI_FIT_PLANTED
    /* A planted build passes only when a check caught the planted rule, so
     * it needs no should_fail, which meson introspect does not report and
     * the tester kit could not honour; a crash still fails it. */
    if (t->failures == 0) {
        (void)fprintf(stderr, "planted rule %s not caught\n", PEL_SEI_FIT_PLANTED);
        return EXIT_FAILURE;
    }
    (void)printf("planted rule %s caught: %d check(s) failed\n", PEL_SEI_FIT_PLANTED, t->failures);
    return EXIT_SUCCESS;
#endif
    if (t->failures != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", t->failures);
        return EXIT_FAILURE;
    }
    (void)printf("sei fit: all checks passed\n");
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
