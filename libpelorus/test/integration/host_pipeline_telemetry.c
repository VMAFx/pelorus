/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "host_pipeline.h"

#include "../test_float_bits.h"

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

/* Verify common header scalars across telemetry section and input frame. */
static void check_telemetry_headers(const PelorusEncTelemetrySection *got,
                                    const PelorusEncTelemetrySection *exp)
{
    CHECK(got->present_mask == exp->present_mask);
    CHECK(got->codec == exp->codec);
    CHECK(got->qp_scale == exp->qp_scale);
    CHECK(got->granularity == exp->granularity);
    CHECK(got->adapter == exp->adapter);
    CHECK(got->block_size_log2 == exp->block_size_log2);
    CHECK(got->metric_source == exp->metric_source);
}

/* Verify display indices, decode indices, and bit counts against presence mask. */
static void check_telemetry_indices_and_bytes(const PelorusEncTelemetrySection *got,
                                              const PelorusEncTelemetrySection *exp)
{
    uint64_t mask = exp->present_mask;

    if (mask & PEL_TLM_F_DISPLAY_INDEX) {
        CHECK(got->display_index == exp->display_index);
    }
    if (mask & PEL_TLM_F_DECODE_INDEX) {
        CHECK(got->decode_index == exp->decode_index);
    }
    if (mask & PEL_TLM_F_FRAME_BYTES) {
        CHECK(got->frame_bytes == exp->frame_bytes);
    }
    if (mask & PEL_TLM_F_HEADER_BITS) {
        CHECK(got->header_bits == exp->header_bits);
    }
    if (mask & PEL_TLM_F_RESIDUAL_BITS) {
        CHECK(got->residual_bits == exp->residual_bits);
    }
}

/* Verify floating point quality metrics match bit-for-bit against presence mask. */
static void check_telemetry_metrics(const PelorusEncTelemetrySection *got,
                                    const PelorusEncTelemetrySection *exp)
{
    uint64_t mask = exp->present_mask;

    if (mask & PEL_TLM_F_AVG_QP) {
        CHECK(float_bits_equal(got->avg_qp, exp->avg_qp));
    }
    if (mask & PEL_TLM_F_AVG_QP_NORM) {
        CHECK(float_bits_equal(got->avg_qp_norm, exp->avg_qp_norm));
    }
    if (mask & PEL_TLM_F_PSNR_Y) {
        CHECK(float_bits_equal(got->psnr_y, exp->psnr_y));
    }
    if (mask & PEL_TLM_F_PSNR_U) {
        CHECK(float_bits_equal(got->psnr_u, exp->psnr_u));
    }
    if (mask & PEL_TLM_F_PSNR_V) {
        CHECK(float_bits_equal(got->psnr_v, exp->psnr_v));
    }
    if (mask & PEL_TLM_F_SSIM_Y) {
        CHECK(float_bits_equal(got->ssim_y, exp->ssim_y));
    }
}

/* Verify block fractions, picture types, and frame flags against presence mask. */
static void check_telemetry_fractions_and_flags(const PelorusEncTelemetrySection *got,
                                                const PelorusEncTelemetrySection *exp)
{
    uint64_t mask = exp->present_mask;

    if (mask & PEL_TLM_F_INTRA_FRACTION) {
        CHECK(float_bits_equal(got->intra_fraction, exp->intra_fraction));
    }
    if (mask & PEL_TLM_F_INTER_FRACTION) {
        CHECK(float_bits_equal(got->inter_fraction, exp->inter_fraction));
    }
    if (mask & PEL_TLM_F_SKIP_FRACTION) {
        CHECK(float_bits_equal(got->skip_fraction, exp->skip_fraction));
    }
    if (mask & PEL_TLM_F_PICTURE_TYPE) {
        CHECK(got->picture_type == exp->picture_type);
    }
    if (mask &
        (PEL_TLM_F_KEY_FRAME | PEL_TLM_F_REFERENCE | PEL_TLM_F_SHOWN | PEL_TLM_F_SCENE_CUT)) {
        CHECK(got->frame_flags == exp->frame_flags);
    }
    if (mask & PEL_TLM_F_CODED_BIT_DEPTH) {
        CHECK(got->coded_bit_depth == exp->coded_bit_depth);
    }
}

/* Compare all telemetry fields in consumed section against original frame data. */
static void check_telemetry_section_match(const PelorusEncTelemetrySection *got,
                                          const PelorusEncTelemetrySection *exp)
{
    check_telemetry_headers(got, exp);
    check_telemetry_indices_and_bytes(got, exp);
    check_telemetry_metrics(got, exp);
    check_telemetry_fractions_and_flags(got, exp);
}

typedef struct HpTlmBuffers {
    uint8_t *blob;
    size_t blob_cap;
    uint8_t *carrier;
    size_t carrier_cap;
} HpTlmBuffers;

/* Pack, encode, unwrap, and verify a single telemetry frame. */
static void run_single_telemetry_frame(const PelorusEncTelemetryInput *frame,
                                       const PelorusEncTelemetrySection *exp,
                                       const HpTlmBuffers *bufs)
{
    PelorusSideData meta;
    size_t blob_len = 0;
    size_t carrier_len = 0;
    _Alignas(8) uint8_t unwrap_scratch[HP_SCRATCH_SIZE];
    const uint8_t *unwrapped = NULL;
    size_t unwrapped_len = 0;
    const void *sec_ptr = NULL;
    size_t sec_size = 0;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = frame->frame.display_index;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 8u;
    meta.producer_id = PLRS_PRODUCER_ID;

    CHECK(pel_enc_telemetry_pack(&meta, frame, bufs->blob, bufs->blob_cap, &blob_len) == PEL_OK);
    CHECK(blob_len > 0u);

    CHECK(pel_blob_carrier_encode(bufs->blob, blob_len, bufs->carrier, bufs->carrier_cap,
                                  &carrier_len) == PEL_OK);
    CHECK(carrier_len > 0u);

    CHECK(pel_blob_unwrap(bufs->carrier, carrier_len, unwrap_scratch, sizeof(unwrap_scratch),
                          &unwrapped, &unwrapped_len) == PEL_OK);
    CHECK(unwrapped != NULL && unwrapped_len == blob_len);
    if (unwrapped == NULL) {
        return;
    }
    CHECK(memcmp(unwrapped, bufs->blob, blob_len) == 0);

    CHECK(pel_blob_find_section(unwrapped, unwrapped_len, PEL_SEC_ENC_TELEMETRY,
                                sizeof(PelorusEncTelemetrySection), &sec_ptr, &sec_size) == PEL_OK);
    CHECK(sec_ptr != NULL && sec_size >= sizeof(PelorusEncTelemetrySection));
    if (sec_ptr == NULL || sec_size < sizeof(PelorusEncTelemetrySection)) {
        return;
    }

    {
        const PelorusEncTelemetrySection *got = sec_ptr;
        check_telemetry_section_match(got, exp);
    }
}

/* Find the maximum packed blob size required across an array of telemetry frames. */
static size_t find_max_telemetry_blob_size(const PelorusEncTelemetryInput *frames, size_t count)
{
    size_t max_blob_size = 0;

    for (size_t i = 0; i < count && i < HP_TELEMETRY_FRAME_CAP; i++) {
        size_t sz = 0;
        CHECK(pel_enc_telemetry_blob_size(&frames[i], &sz) == PEL_OK);
        if (sz > max_blob_size) {
            max_blob_size = sz;
        }
    }
    return max_blob_size;
}

/* Read CSV fixture and parse telemetry frames into input array. */
static int parse_telemetry_fixture(const char *csv_path, PelorusEncTelemetryInput *frames,
                                   size_t *out_count)
{
    char *csv_text = malloc(HP_FIXTURE_BUF_CAP);
    size_t csv_len = 0;
    size_t frame_count = 0;

    CHECK(csv_text != NULL);
    if (csv_text == NULL) {
        return -1;
    }
    CHECK(hp_read_fixture(csv_path, csv_text, HP_FIXTURE_BUF_CAP, &csv_len) == 0);
    CHECK(pel_tlm_parse_x265_csv(csv_text, csv_len, 8u, frames, HP_TELEMETRY_FRAME_CAP,
                                 &frame_count) == PEL_OK);
    CHECK(frame_count == HP_EXPECTED_X265_FRAME_COUNT);
    free(csv_text);
    *out_count = frame_count;
    return (frame_count == HP_EXPECTED_X265_FRAME_COUNT) ? 0 : -1;
}

typedef struct HpTelemetryPackCtx {
    const PelorusSideData *meta;
    const PelorusEncTelemetryInput *frame;
} HpTelemetryPackCtx;

static pel_result pack_boundary_telemetry(const void *ctx, uint8_t *out, size_t cap,
                                          size_t *out_len)
{
    const HpTelemetryPackCtx *telemetry = ctx;
    return pel_enc_telemetry_pack(telemetry->meta, telemetry->frame, out, cap, out_len);
}

/* Verify boundary behavior and size query for telemetry packing. */
static void test_boundary_telemetry_pack(const PelorusEncTelemetryInput *frame)
{
    PelorusSideData meta;
    size_t need = 0;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 0u;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 8u;
    meta.producer_id = PLRS_PRODUCER_ID;

    CHECK(pel_enc_telemetry_blob_size(frame, &need) == PEL_OK);
    const HpTelemetryPackCtx ctx = {&meta, frame};
    hp_check_size_boundary("telemetry pack", pack_boundary_telemetry, &ctx, need);
}

typedef struct ExpectedTlmRow {
    uint32_t decode_index;
    uint32_t display_index;
    uint32_t frame_bytes;
    uint8_t picture_type;
    uint8_t frame_flags;
} ExpectedTlmRow;

#define X265_EXPECTED_PRESENT_MASK                                                                 \
    (PEL_TLM_F_DISPLAY_INDEX | PEL_TLM_F_DECODE_INDEX | PEL_TLM_F_FRAME_BYTES | PEL_TLM_F_AVG_QP | \
     PEL_TLM_F_AVG_QP_NORM | PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_KEY_FRAME | PEL_TLM_F_REFERENCE |  \
     PEL_TLM_F_SHOWN | PEL_TLM_F_SCENE_CUT | PEL_TLM_F_CODED_BIT_DEPTH)

static const ExpectedTlmRow EXPECTED_X265_ROWS[HP_EXPECTED_X265_FRAME_COUNT] = {
    {0u, 0u, 1880u, PEL_PICTURE_I,
     (uint8_t)(PEL_TLM_FRAME_SHOWN | PEL_TLM_FRAME_KEY | PEL_TLM_FRAME_REFERENCE)},
    {1u, 3u, 877u, PEL_PICTURE_P, (uint8_t)(PEL_TLM_FRAME_SHOWN | PEL_TLM_FRAME_REFERENCE)},
    {2u, 2u, 538u, PEL_PICTURE_B, (uint8_t)(PEL_TLM_FRAME_SHOWN | PEL_TLM_FRAME_REFERENCE)},
    {3u, 1u, 455u, PEL_PICTURE_B, (uint8_t)PEL_TLM_FRAME_SHOWN},
    {4u, 5u, 833u, PEL_PICTURE_P, (uint8_t)(PEL_TLM_FRAME_SHOWN | PEL_TLM_FRAME_REFERENCE)},
    {5u, 4u, 549u, PEL_PICTURE_B, (uint8_t)PEL_TLM_FRAME_SHOWN},
    {6u, 9u, 925u, PEL_PICTURE_P, (uint8_t)(PEL_TLM_FRAME_SHOWN | PEL_TLM_FRAME_REFERENCE)},
    {7u, 7u, 470u, PEL_PICTURE_B, (uint8_t)(PEL_TLM_FRAME_SHOWN | PEL_TLM_FRAME_REFERENCE)},
    {8u, 6u, 533u, PEL_PICTURE_B, (uint8_t)PEL_TLM_FRAME_SHOWN},
    {9u, 8u, 431u, PEL_PICTURE_B, (uint8_t)PEL_TLM_FRAME_SHOWN},
};

/* Populate expected telemetry sections for x265 4.3 QP 32 characterization fixture. */
static void init_expected_telemetry(PelorusEncTelemetrySection *exp, size_t count)
{
    for (size_t i = 0; i < count && i < HP_EXPECTED_X265_FRAME_COUNT; i++) {
        memset(&exp[i], 0, sizeof(exp[i]));
        exp[i].present_mask = X265_EXPECTED_PRESENT_MASK;
        exp[i].codec = PEL_TLM_CODEC_HEVC;
        exp[i].qp_scale = PEL_QP_SCALE_SLICE_QP;
        exp[i].granularity = PEL_TLM_GRAN_FRAME;
        exp[i].adapter = PEL_TLM_ADAPTER_X265_CSV;
        exp[i].coded_bit_depth = 8u;
        exp[i].metric_source = 0u;
        exp[i].avg_qp = 32.0f;
        exp[i].avg_qp_norm = 32.0f;
        exp[i].decode_index = EXPECTED_X265_ROWS[i].decode_index;
        exp[i].display_index = EXPECTED_X265_ROWS[i].display_index;
        exp[i].frame_bytes = EXPECTED_X265_ROWS[i].frame_bytes;
        exp[i].picture_type = EXPECTED_X265_ROWS[i].picture_type;
        exp[i].frame_flags = EXPECTED_X265_ROWS[i].frame_flags;
    }
}

/* Execute Scenario 2 integration test with x265 telemetry frames. */
void hp_test_scenario2_telemetry(const char *csv_path)
{
    PelorusEncTelemetryInput frames[HP_TELEMETRY_FRAME_CAP];
    PelorusEncTelemetrySection expected[HP_EXPECTED_X265_FRAME_COUNT];
    size_t frame_count = 0;
    size_t max_blob_size = 0;
    uint8_t *blob_buf;
    uint8_t *carrier_buf;
    size_t max_carrier_size;

    memset(frames, 0, sizeof(frames));
    init_expected_telemetry(expected, HP_EXPECTED_X265_FRAME_COUNT);
    if (parse_telemetry_fixture(csv_path, frames, &frame_count) != 0) {
        return;
    }

    max_blob_size = find_max_telemetry_blob_size(frames, frame_count);
    CHECK(max_blob_size > 0u);
    if (max_blob_size == 0u) {
        return;
    }
    blob_buf = malloc(max_blob_size);
    CHECK(blob_buf != NULL);
    max_carrier_size = PEL_CARRIER_MAX_LEN(max_blob_size);
    carrier_buf = malloc(max_carrier_size);
    CHECK(carrier_buf != NULL);

    if (blob_buf != NULL && carrier_buf != NULL) {
        HpTlmBuffers bufs;
        bufs.blob = blob_buf;
        bufs.blob_cap = max_blob_size;
        bufs.carrier = carrier_buf;
        bufs.carrier_cap = max_carrier_size;

        for (size_t i = 0; i < frame_count && i < HP_EXPECTED_X265_FRAME_COUNT; i++) {
            check_telemetry_section_match(&frames[i].frame, &expected[i]);
            run_single_telemetry_frame(&frames[i], &expected[i], &bufs);
        }
        test_boundary_telemetry_pack(&frames[0]);
    }
    free(blob_buf);
    free(carrier_buf);
}

/* NOLINTEND(modernize-use-nullptr) */
