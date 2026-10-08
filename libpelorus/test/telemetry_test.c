/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * telemetry_test.c — PEL_SEC_ENC_TELEMETRY writer contract (interop ABI 1.4,
 * ADR-0174, docs/api/encoder-telemetry.md): validation, sizing, the
 * non-allocating pack, the adapter capability masks, QP normalisation and the
 * map reader, with positive, negative and boundary cases for each.
 *
 * Issue #221: pelorus/telemetry.h is the first include and this target has no
 * FFmpeg include path, so a header that leaked an FFmpeg type would not build.
 * Not part of the VMAFx-mirrored fixture (telemetry.c is not mirrored at RC4).
 */

#include "pelorus/telemetry.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

static int g_fail;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            g_fail++;                                                                              \
        }                                                                                          \
    } while (0)

/* An 8-aligned blob buffer. */
typedef union TestBlob {
    uint64_t align;
    uint8_t bytes[512];
} TestBlob;

#define GRID_COLS 4u
#define GRID_ROWS 2u
#define GRID_ELEMS (GRID_COLS * GRID_ROWS)

static const int16_t test_qp_map[GRID_ELEMS] = {120, 124, 128, 132, 116, 120, 124, 204};
static const uint32_t test_bits_map[GRID_ELEMS] = {900, 1200, 40, 0, 0, 3100, 77, 5};
static const uint8_t test_mode_map[GRID_ELEMS] = {1, 2, 2, 3, 3, 2, 1, 0};

/* A valid frame-granularity record a caller outside FFmpeg fills by hand (#221). */
static void base_frame(PelorusEncTelemetryInput *in)
{
    PelorusEncTelemetrySection *t = &in->frame;

    memset(in, 0, sizeof(*in));
    t->present_mask = PEL_TLM_F_DISPLAY_INDEX | PEL_TLM_F_DECODE_INDEX | PEL_TLM_F_FRAME_BYTES |
                      PEL_TLM_F_HEADER_BITS | PEL_TLM_F_RESIDUAL_BITS | PEL_TLM_F_AVG_QP |
                      PEL_TLM_F_PSNR_Y | PEL_TLM_F_SSIM_Y | PEL_TLM_F_INTRA_FRACTION |
                      PEL_TLM_F_INTER_FRACTION | PEL_TLM_F_SKIP_FRACTION | PEL_TLM_F_PICTURE_TYPE |
                      PEL_TLM_F_KEY_FRAME | PEL_TLM_F_SHOWN | PEL_TLM_F_CODED_BIT_DEPTH;
    t->display_index = 4;
    t->decode_index = 1;
    t->frame_bytes = 1000;
    t->header_bits = 2000;
    t->residual_bits = 6000; /* boundary: header + residual == frame_bytes * 8 */
    t->avg_qp = 30.0f;
    t->psnr_y = 41.5f;
    t->ssim_y = 0.98f;
    t->intra_fraction = 0.25f; /* boundary: the three shares sum to exactly 1 */
    t->inter_fraction = 0.5f;
    t->skip_fraction = 0.25f;
    t->picture_type = PEL_PICTURE_P;
    t->frame_flags = PEL_TLM_FRAME_SHOWN; /* key_frame reported as "not a key frame" */
    t->coded_bit_depth = 10;
    t->codec = PEL_TLM_CODEC_HEVC;
    t->qp_scale = PEL_QP_SCALE_SLICE_QP;
    t->metric_source = PEL_TLM_METRIC_ENCODER;
    t->granularity = PEL_TLM_GRAN_FRAME;
    t->adapter = PEL_TLM_ADAPTER_EXTERNAL;
}

/* A valid block-granularity record with all three maps on a 4x2 grid of 16x16 blocks. */
static void base_block(PelorusEncTelemetryInput *in)
{
    base_frame(in);
    in->frame.present_mask |= PEL_TLM_F_QP_MAP | PEL_TLM_F_BITS_MAP | PEL_TLM_F_MODE_MAP;
    in->frame.granularity = PEL_TLM_GRAN_BLOCK;
    in->frame.map_cols = (uint16_t)GRID_COLS;
    in->frame.map_rows = (uint16_t)GRID_ROWS;
    in->frame.block_size_log2 = 4;
    in->qp_map = test_qp_map; /* 204 == 51 * 4: the slice_qp upper bound for HEVC */
    in->bits_map = test_bits_map;
    in->mode_map = test_mode_map;
}

/* One planted change to a valid record and the result validation must give. */
typedef struct Mutation {
    size_t offset;       /* field offset in the section                       */
    double value;        /* written as the field's type                       */
    uint64_t set_bits;   /* then OR'd into present_mask                       */
    uint64_t clear_bits; /* then cleared from present_mask                    */
    pel_result want;
    uint8_t size; /* 1, 2 or 4 for an integer field; 0 for a float     */
} Mutation;

#define INT_FIELD(f) offsetof(PelorusEncTelemetrySection, f)
#define FLOAT_FIELD(f) offsetof(PelorusEncTelemetrySection, f)
#define INT_SIZE(f) (uint8_t)sizeof(((PelorusEncTelemetrySection *)0)->f)

/* Enumerations, presence and "value under a clear bit" (all PEL_ERR_INVALID unless noted). */
static const Mutation invalid_mutations[] = {
    {INT_FIELD(codec), 0, 0, 0, PEL_ERR_INVALID, INT_SIZE(codec)},
    {INT_FIELD(codec), 6, 0, 0, PEL_ERR_INVALID, INT_SIZE(codec)},
    {INT_FIELD(codec), PEL_TLM_CODEC_VVC, 0, 0, PEL_OK, INT_SIZE(codec)},
    {INT_FIELD(granularity), 0, 0, 0, PEL_ERR_INVALID, INT_SIZE(granularity)},
    {INT_FIELD(granularity), 4, 0, 0, PEL_ERR_INVALID, INT_SIZE(granularity)},
    {INT_FIELD(adapter), 0, 0, 0, PEL_ERR_INVALID, INT_SIZE(adapter)},
    {INT_FIELD(adapter), 11, 0, 0, PEL_ERR_INVALID, INT_SIZE(adapter)},
    {INT_FIELD(display_index), 4, UINT64_C(1) << 23u, 0, PEL_ERR_INVALID,
     INT_SIZE(display_index)}, /* unknown bit */
    {INT_FIELD(qp_scale), 0, 0, 0, PEL_ERR_INVALID, INT_SIZE(qp_scale)},
    {INT_FIELD(qp_scale), PEL_QP_SCALE_AV1_QINDEX, 0, 0, PEL_ERR_INVALID,
     INT_SIZE(qp_scale)}, /* not HEVC's */
    {INT_FIELD(metric_source), 0, 0, 0, PEL_ERR_INVALID, INT_SIZE(metric_source)},
    {INT_FIELD(metric_source), 3, 0, 0, PEL_ERR_INVALID, INT_SIZE(metric_source)},
    {INT_FIELD(picture_type), 4, 0, 0, PEL_ERR_INVALID, INT_SIZE(picture_type)},
    {INT_FIELD(picture_type), 0, 0, 0, PEL_ERR_INVALID, INT_SIZE(picture_type)},
    {INT_FIELD(frame_flags), PEL_TLM_FRAME_KEY | PEL_TLM_FRAME_SHOWN, 0, 0, PEL_OK,
     INT_SIZE(frame_flags)},
    {INT_FIELD(frame_flags), PEL_TLM_FRAME_REFERENCE, 0, 0, PEL_ERR_INVALID, INT_SIZE(frame_flags)},
    {INT_FIELD(frame_flags), 0x10, 0, 0, PEL_ERR_INVALID, INT_SIZE(frame_flags)},
    {INT_FIELD(display_index), 5, 0, PEL_TLM_F_DISPLAY_INDEX, PEL_ERR_INVALID,
     INT_SIZE(display_index)},
    {INT_FIELD(display_index), 0, 0, PEL_TLM_F_DISPLAY_INDEX, PEL_OK,
     INT_SIZE(display_index)}, /* not reported */
    {INT_FIELD(adapter), PEL_TLM_ADAPTER_X265_CSV, 0, 0, PEL_ERR_INVALID,
     INT_SIZE(adapter)},                                                 /* caps 0 */
    {INT_FIELD(map_cols), 4, 0, 0, PEL_ERR_INVALID, INT_SIZE(map_cols)}, /* frame granularity */
    {INT_FIELD(block_size_log2), 3, 0, 0, PEL_ERR_INVALID,
     INT_SIZE(block_size_log2)}, /* frame granularity */
    {INT_FIELD(display_index), 4, PEL_TLM_F_QP_MAP, 0, PEL_ERR_INVALID, INT_SIZE(display_index)},
    {INT_FIELD(qp_map_offset), 64, 0, 0, PEL_ERR_INVALID,
     INT_SIZE(qp_map_offset)}, /* offset under a clear bit */
    {INT_FIELD(coded_bit_depth), 9, 0, 0, PEL_ERR_RANGE, INT_SIZE(coded_bit_depth)},
    {INT_FIELD(coded_bit_depth), 12, 0, 0, PEL_OK, INT_SIZE(coded_bit_depth)},
    {INT_FIELD(header_bits), 2001, 0, 0, PEL_ERR_RANGE,
     INT_SIZE(header_bits)}, /* one bit past frame_bytes*8 */
};

/* Float ranges and NaN (PEL_ERR_RANGE unless noted). */
static const Mutation range_mutations[] = {
    {FLOAT_FIELD(avg_qp), 51.0, 0, 0, PEL_OK, 0u},
    {FLOAT_FIELD(avg_qp), 51.5, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(avg_qp), -12.0, 0, 0, PEL_OK, 0u}, /* -QpBdOffsetY at 10 bits */
    {FLOAT_FIELD(avg_qp), -12.5, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(avg_qp), NAN, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(psnr_y), 0.0, 0, 0, PEL_OK, 0u}, /* a reported 0 dB, not "not reported" */
    {FLOAT_FIELD(psnr_y), -1.0, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(psnr_y), INFINITY, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(psnr_y), NAN, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(psnr_y), 41.5, 0, PEL_TLM_F_PSNR_Y, PEL_ERR_INVALID, 0u}, /* value, bit clear */
    {FLOAT_FIELD(ssim_y), 1.0, 0, 0, PEL_OK, 0u},
    {FLOAT_FIELD(ssim_y), 1.01, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(intra_fraction), 0.2509, 0, 0, PEL_OK, 0u}, /* sum 1.0009 <= 1 + 1e-3 */
    {FLOAT_FIELD(intra_fraction), 0.252, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(intra_fraction), -0.01, 0, 0, PEL_ERR_RANGE, 0u},
    {FLOAT_FIELD(avg_qp_norm), 30.0, PEL_TLM_F_AVG_QP_NORM, 0, PEL_OK, 0u},
    {FLOAT_FIELD(avg_qp_norm), 30.0, 0, 0, PEL_ERR_INVALID, 0u}, /* value, bit clear */
};

static void apply_mutation(PelorusEncTelemetrySection *t, const Mutation *m)
{
    uint8_t *field = (uint8_t *)t + m->offset;

    /* Convert only for the field's own type: a float mutation may hold NaN or a negative
     * value, and converting those to an unsigned type is undefined (UBSan). Integer
     * mutations hold values in 0..2^32, which narrow by the defined modulo rule. */
    if (m->size == 0u) {
        const float f = (float)m->value;
        memcpy(field, &f, sizeof(f));
    } else {
        const uint32_t u32 = (uint32_t)m->value;
        const uint16_t u16 = (uint16_t)u32;
        const uint8_t u8 = (uint8_t)u32;
        CHECK(m->size == 1u || m->size == 2u || m->size == 4u);
        if (m->size == 1u) {
            memcpy(field, &u8, sizeof(u8));
        } else if (m->size == 2u) {
            memcpy(field, &u16, sizeof(u16));
        } else {
            memcpy(field, &u32, sizeof(u32));
        }
    }
    t->present_mask = (t->present_mask | m->set_bits) & ~m->clear_bits;
}

static void run_mutations(const Mutation *table, size_t n, const char *name)
{
    size_t i;

    for (i = 0; i < n; i++) {
        PelorusEncTelemetryInput in;
        pel_result got;
        base_frame(&in);
        apply_mutation(&in.frame, &table[i]);
        got = pel_enc_telemetry_validate(&in);
        if (got != table[i].want) {
            (void)fprintf(stderr, "FAIL %s[%zu]: got %d want %d\n", name, i, (int)got,
                          (int)table[i].want);
            g_fail++;
        }
    }
}

static void test_validate_frame(void)
{
    PelorusEncTelemetryInput in;

    base_frame(&in);
    CHECK(pel_enc_telemetry_validate(&in) == PEL_OK);
    CHECK(pel_enc_telemetry_validate(NULL) == PEL_ERR_INVALID);
    run_mutations(invalid_mutations, sizeof(invalid_mutations) / sizeof(invalid_mutations[0]),
                  "invalid_mutations");
    run_mutations(range_mutations, sizeof(range_mutations) / sizeof(range_mutations[0]),
                  "range_mutations");

    /* A different scale belongs to a different codec: AV1 qindex 255 is its maximum. */
    base_frame(&in);
    in.frame.codec = PEL_TLM_CODEC_AV1;
    in.frame.qp_scale = PEL_QP_SCALE_AV1_QINDEX;
    in.frame.avg_qp = 255.0f;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_OK);
    in.frame.avg_qp = 256.0f;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
}

/* Map slots and granularity: each map bit has exactly its pointer. */
static void test_validate_map_slots(void)
{
    PelorusEncTelemetryInput in;

    base_block(&in);
    CHECK(pel_enc_telemetry_validate(&in) == PEL_OK);
    in.mode_map = NULL; /* bit set, no pointer */
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_INVALID);
    base_block(&in);
    in.frame.present_mask &= ~PEL_TLM_F_BITS_MAP; /* pointer, bit clear */
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_INVALID);
    base_block(&in);
    in.frame.present_mask &= ~(PEL_TLM_F_QP_MAP | PEL_TLM_F_BITS_MAP | PEL_TLM_F_MODE_MAP);
    in.qp_map = NULL;
    in.bits_map = NULL;
    in.mode_map = NULL;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_INVALID); /* block granularity, no map */
    base_block(&in);
    in.frame.granularity = PEL_TLM_GRAN_ROW; /* row granularity needs map_cols == 1 */
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_INVALID);
}

/* Grid bounds: block edge 2..7 and the 2^20-element boundary. */
static void test_validate_map_bounds(void)
{
    uint8_t *big_mode = calloc(PEL_TLM_MAP_MAX_ELEMS, 1); /* zero: "not reported" */
    PelorusEncTelemetryInput in;

    base_block(&in);
    in.frame.block_size_log2 = 1;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
    in.frame.block_size_log2 = 8;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
    in.frame.block_size_log2 = 7;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_OK);

    /* Boundary: exactly PEL_TLM_MAP_MAX_ELEMS passes, one more row does not. */
    CHECK(big_mode != NULL);
    if (big_mode == NULL) {
        return;
    }
    base_frame(&in);
    in.frame.present_mask |= PEL_TLM_F_MODE_MAP;
    in.frame.granularity = PEL_TLM_GRAN_BLOCK;
    in.frame.map_cols = 1024;
    in.frame.map_rows = 1024;
    in.frame.block_size_log2 = 3;
    in.mode_map = big_mode;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_OK);
    in.frame.map_rows = 1025;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
    in.frame.map_rows = 0;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
    free(big_mode);
}

/* Map element values: block modes are enum values, QP elements stay in the scale. */
static void test_validate_map_values(void)
{
    PelorusEncTelemetryInput in;
    int16_t qp[GRID_ELEMS];
    uint8_t mode[GRID_ELEMS];

    base_block(&in);
    memcpy(qp, test_qp_map, sizeof(qp));
    memcpy(mode, test_mode_map, sizeof(mode));
    in.qp_map = qp;
    in.mode_map = mode;
    mode[5] = 4; /* no such block mode */
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_INVALID);
    mode[5] = PEL_BLOCK_MODE_SKIP;
    qp[7] = 205; /* 51.25 in Q2: above HEVC's 51 */
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
    qp[7] = -48; /* -12 in Q2: -QpBdOffsetY at 10 bits */
    CHECK(pel_enc_telemetry_validate(&in) == PEL_OK);
    qp[7] = -49;
    CHECK(pel_enc_telemetry_validate(&in) == PEL_ERR_RANGE);
}

/* The three maps of a packed block record, read through the public reader. */
static void check_packed_maps(const uint8_t *blob, size_t len, const PelorusEncTelemetrySection *t,
                              size_t got)
{
    const void *map = NULL;
    uint32_t elems = 0;

    CHECK(pel_enc_telemetry_map(blob, len, t, got, PEL_TLM_F_QP_MAP, &map, &elems) == PEL_OK);
    CHECK(elems == GRID_ELEMS && map != NULL && memcmp(map, test_qp_map, sizeof(test_qp_map)) == 0);
    CHECK(pel_enc_telemetry_map(blob, len, t, got, PEL_TLM_F_BITS_MAP, &map, &elems) == PEL_OK);
    CHECK(map != NULL && memcmp(map, test_bits_map, sizeof(test_bits_map)) == 0);
    CHECK(pel_enc_telemetry_map(blob, len, t, got, PEL_TLM_F_MODE_MAP, &map, &elems) == PEL_OK);
    CHECK(map != NULL && memcmp(map, test_mode_map, sizeof(test_mode_map)) == 0);
}

/* Read a packed block record back through the public readers. */
static void check_packed_block(const uint8_t *blob, size_t len)
{
    PelorusEncTelemetrySection t;
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_ENC_TELEMETRY, sizeof(t), &p, &got) == PEL_OK);
    CHECK(got == sizeof(t));
    if (p == NULL || got != sizeof(t)) {
        return;
    }
    memcpy(&t, p, sizeof(t));
    CHECK(t.qp_map_size == GRID_ELEMS * 2u && t.bits_map_size == GRID_ELEMS * 4u);
    CHECK(t.mode_map_size == GRID_ELEMS && (t.qp_map_offset & 7u) == 0u);
    CHECK(t.psnr_y == 41.5f && t.adapter == PEL_TLM_ADAPTER_EXTERNAL);
    check_packed_maps(blob, len, &t, got);
}

/* Pack a hand-filled external record into a caller buffer (#221), then read it back. */
static void test_pack_roundtrip(void)
{
    PelorusEncTelemetryInput in;
    PelorusSideData meta;
    TestBlob blob;
    size_t need = 0;
    size_t len = 0;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 77;
    meta.producer_id = 0x544D4C54u; /* 'TLMT' */
    base_block(&in);
    CHECK(pel_enc_telemetry_blob_size(&in, &need) == PEL_OK);
    /* 16 UUID + 64 header and dir + 104 section; maps at 168 (16 B), 184 (32 B), 216 (8 B). */
    CHECK(need == 16u + 224u);
    memset(blob.bytes, 0xA5, sizeof(blob.bytes));
    CHECK(pel_enc_telemetry_pack(&meta, &in, blob.bytes, need - 1u, &len) == PEL_ERR_RANGE);
    CHECK(len == need && blob.bytes[0] == 0xA5); /* never a partial blob */
    CHECK(pel_enc_telemetry_pack(&meta, &in, blob.bytes, sizeof(blob.bytes), &len) == PEL_OK);
    CHECK(len == need && blob.bytes[need] == 0xA5);
    check_packed_block(blob.bytes, len);

    /* A frame-granularity record carries no maps: header, dir and section only. */
    base_frame(&in);
    CHECK(pel_enc_telemetry_blob_size(&in, &need) == PEL_OK && need == 16u + 168u);
    CHECK(pel_enc_telemetry_pack(&meta, &in, blob.bytes, sizeof(blob.bytes), &len) == PEL_OK);
}

/* Invalid input or arguments: nothing written, the error says why. */
static void test_pack_rejects(void)
{
    PelorusEncTelemetryInput in;
    PelorusSideData meta;
    TestBlob blob;
    size_t need = 0;
    size_t len = 0;

    memset(&meta, 0, sizeof(meta));
    base_frame(&in);
    in.frame.codec = 0;
    CHECK(pel_enc_telemetry_blob_size(&in, &need) == PEL_ERR_INVALID && need == 0u);
    CHECK(pel_enc_telemetry_pack(&meta, &in, blob.bytes, sizeof(blob.bytes), &len) ==
          PEL_ERR_INVALID);
    base_frame(&in);
    CHECK(pel_enc_telemetry_pack(NULL, &in, blob.bytes, sizeof(blob.bytes), &len) ==
          PEL_ERR_INVALID);
    CHECK(pel_enc_telemetry_pack(&meta, &in, NULL, sizeof(blob.bytes), &len) == PEL_ERR_INVALID);
    CHECK(pel_enc_telemetry_pack(&meta, &in, blob.bytes, sizeof(blob.bytes), NULL) ==
          PEL_ERR_INVALID);
    CHECK(pel_enc_telemetry_blob_size(NULL, &need) == PEL_ERR_INVALID);
}

/* The reader's checks 1 and 2 on a well-formed blob with a doctored section copy. */
static void test_map_reader_checks(void)
{
    PelorusEncTelemetryInput in;
    PelorusSideData meta;
    PelorusEncTelemetrySection t;
    TestBlob blob;
    const void *p = NULL;
    const void *map = NULL;
    size_t got = 0;
    size_t len = 0;
    uint32_t elems = 0;

    memset(&meta, 0, sizeof(meta));
    base_block(&in);
    in.frame.present_mask &= ~PEL_TLM_F_BITS_MAP;
    in.bits_map = NULL;
    CHECK(pel_enc_telemetry_pack(&meta, &in, blob.bytes, sizeof(blob.bytes), &len) == PEL_OK);
    CHECK(pel_blob_find_section(blob.bytes, len, PEL_SEC_ENC_TELEMETRY, sizeof(t), &p, &got) ==
          PEL_OK);
    if (p == NULL) {
        return;
    }
    memcpy(&t, p, sizeof(t));
    /* Check 1: a clear bit is "not reported", not an empty map. */
    CHECK(pel_enc_telemetry_map(blob.bytes, len, &t, got, PEL_TLM_F_BITS_MAP, &map, &elems) ==
          PEL_ERR_ABSENT);
    CHECK(map == NULL && elems == 0u);
    CHECK(pel_enc_telemetry_map(blob.bytes, len, &t, got, PEL_TLM_F_SHOWN, &map, &elems) ==
          PEL_ERR_INVALID);
    CHECK(pel_enc_telemetry_map(blob.bytes, len, &t, got - 1u, PEL_TLM_F_QP_MAP, &map, &elems) ==
          PEL_ERR_INVALID);
    /* Check 2: a geometry no writer produces is corrupt framing. */
    t.map_cols = 0;
    CHECK(pel_enc_telemetry_map(blob.bytes, len, &t, got, PEL_TLM_F_QP_MAP, &map, &elems) ==
          PEL_ERR_ABI);
    t.map_cols = (uint16_t)GRID_COLS;
    /* Checks 3 to 5 run in pel_blob_map: a map that starts at total_size leaves the blob. */
    t.qp_map_offset = (uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN);
    CHECK(pel_enc_telemetry_map(blob.bytes, len, &t, got, PEL_TLM_F_QP_MAP, &map, &elems) ==
          PEL_ERR_TRUNCATED);
    t.granularity = PEL_TLM_GRAN_FRAME;
    CHECK(pel_enc_telemetry_map(blob.bytes, len, &t, got, PEL_TLM_F_QP_MAP, &map, &elems) ==
          PEL_ERR_ABSENT);
}

static void test_adapter_caps(void)
{
    uint64_t mask = 1;

    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_EXTERNAL, PEL_TLM_CODEC_AV1, &mask) == PEL_OK);
    CHECK(mask == PEL_TLM_KNOWN_MASK);
    /* No adapter table has landed yet (#86): "not reported" for every field. */
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_X265_CSV, PEL_TLM_CODEC_HEVC, &mask) ==
          PEL_ERR_UNSUPPORTED);
    CHECK(mask == 0u);
    CHECK(pel_tlm_adapter_caps(0, PEL_TLM_CODEC_HEVC, &mask) == PEL_ERR_INVALID);
    CHECK(pel_tlm_adapter_caps(11, PEL_TLM_CODEC_HEVC, &mask) == PEL_ERR_INVALID);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_EXTERNAL, 6, &mask) == PEL_ERR_INVALID);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_EXTERNAL, PEL_TLM_CODEC_HEVC, NULL) ==
          PEL_ERR_INVALID);
}

static void test_qp_normalize_slice_qp(void)
{
    float norm = -1.0f;

    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 8, 30.0f, &norm) == PEL_OK && norm == 30.0f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 10, -12.0f, &norm) == PEL_OK && norm == -12.0f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 10, -12.5f, &norm) == PEL_ERR_RANGE);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 8, 63.0f, &norm) == PEL_OK); /* VVC max */
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 8, 63.5f, &norm) == PEL_ERR_RANGE);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 9, 30.0f, &norm) == PEL_ERR_RANGE);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 8, NAN, &norm) == PEL_ERR_RANGE);
}

/* Unverified step-size tables (research 0174, E3): refused, never guessed. */
static void test_qp_normalize_refused(void)
{
    float norm = -1.0f;

    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_QINDEX, 8, 120.0f, &norm) == PEL_ERR_UNSUPPORTED);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_VP9_QINDEX, 8, 120.0f, &norm) == PEL_ERR_UNSUPPORTED);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8, 32.0f, &norm) == PEL_ERR_UNSUPPORTED);
    CHECK(norm == -1.0f);
    CHECK(pel_qp_normalize(0, 8, 30.0f, &norm) == PEL_ERR_INVALID);
    CHECK(pel_qp_normalize(5, 8, 30.0f, &norm) == PEL_ERR_INVALID);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_SLICE_QP, 8, 30.0f, NULL) == PEL_ERR_INVALID);
}

int main(void)
{
    test_validate_frame();
    test_validate_map_slots();
    test_validate_map_bounds();
    test_validate_map_values();
    test_pack_roundtrip();
    test_pack_rejects();
    test_map_reader_checks();
    test_adapter_caps();
    test_qp_normalize_slice_qp();
    test_qp_normalize_refused();

    if (g_fail != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", g_fail);
        return EXIT_FAILURE;
    }
    (void)printf("enc-telemetry: all checks passed (ABI %u.%u)\n", PELORUS_ABI_MAJOR,
                 PELORUS_ABI_MINOR);
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
