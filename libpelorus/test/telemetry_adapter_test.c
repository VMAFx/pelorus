/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/telemetry.h"

#include <inttypes.h>
#include <locale.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FIXTURE_CAP 8192u
#define FRAME_CAP 32u
#define RECORDED_FRAME_COUNT 10u
#define TEST_CSV_FIELDS_OVER_MAX 257u
#define TEST_TEXT_LINE_OVER_MAX 4097u
#define TEST_TEXT_TOTAL_MAX 1048576u
#define TEST_SKIP 77
#define X265_HEADER_TEXT "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"

static const char x265_header[] = X265_HEADER_TEXT;
static const char svt_row[] = "Picture Number: 0 TL: 0 QP: 34 Average QP: 34 [ PSNR-Y: 43.41 dB, "
                              "PSNR-U: 39.10 dB, PSNR-V: 36.73 dB, MSE-Y: 2.96, MSE-U: 8.00, "
                              "MSE-V: 13.81, SSIM-Y: 0.99230, SSIM-U: 0.98908, SSIM-V: 0.99024 ] "
                              "1364 bytes VBV delay: 0.000 s\n";
static const char svt_qp63_row[] =
    "Picture Number: 0 TL: 0 QP: 63 Average QP: 64 [ PSNR-Y: 43.41 dB, "
    "PSNR-U: 39.10 dB, PSNR-V: 36.73 dB, MSE-Y: 2.96, MSE-U: 8.00, "
    "MSE-V: 13.81, SSIM-Y: 0.99230, SSIM-U: 0.98908, SSIM-V: 0.99024 ] "
    "1364 bytes VBV delay: 0.000 s\n";
static const char vvenc_row[] =
    "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB] [ET     0 ] [L0 ] [L1 ]\n";

#define CHECK(expr)                                                                                \
    do {                                                                                           \
        if (!(expr)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr);                  \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

static int read_fixture(const char *path, char *buf, size_t cap, size_t *out_len)
{
    FILE *fp = fopen(path, "rb");
    size_t got;

    if (fp == NULL) {
        return -1;
    }
    got = fread(buf, 1u, cap, fp);
    if (ferror(fp) || got == cap) {
        (void)fclose(fp);
        return -1;
    }
    (void)fclose(fp);
    *out_len = got;
    return 0;
}

static int bytes_equal(const void *a, const void *b, size_t size)
{
    const unsigned char *lhs = a;
    const unsigned char *rhs = b;
    size_t i;

    for (i = 0; i < size; i++) {
        if (lhs[i] != rhs[i]) {
            return 0;
        }
    }
    return 1;
}

static int check_x265_frame(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK(frame->frame.display_index == 0u && frame->frame.decode_index == 0u);
    CHECK(frame->frame.frame_bytes == 1880u && frame->frame.avg_qp == 32.0f);
    CHECK(frame->frame.picture_type == PEL_PICTURE_I);
    CHECK(frame->frame.frame_flags ==
          (PEL_TLM_FRAME_KEY | PEL_TLM_FRAME_REFERENCE | PEL_TLM_FRAME_SHOWN));
    CHECK((frame->frame.present_mask & PEL_TLM_F_PSNR_Y) == 0u);
    CHECK(frame->frame.psnr_y == 0.0f);
    return failures;
}

static int test_x265_fixture(const char *path, PelorusEncTelemetryInput *first)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[FRAME_CAP];
    size_t len = 0;
    size_t count = 0;
    uint64_t caps = 0;
    int failures = 0;

    (void)memset(frames, 0, sizeof(frames));
    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_x265_csv(text, len, 8u, frames, FRAME_CAP, &count) == PEL_OK);
    CHECK(count == RECORDED_FRAME_COUNT);
    failures += check_x265_frame(&frames[0]);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_X265_CSV, PEL_TLM_CODEC_HEVC, &caps) == PEL_OK);
    CHECK(frames[0].frame.present_mask == caps);
    *first = frames[0];
    return failures;
}

static int check_svt_frame(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK(frame->frame.display_index == 0u && frame->frame.frame_bytes == 1364u);
    CHECK(frame->frame.avg_qp == 34.0f);
    CHECK(fabsf(frame->frame.psnr_y - 43.41f) < 0.001f);
    CHECK(fabsf(frame->frame.ssim_y - 0.99230f) < 0.00001f);
    CHECK((frame->frame.present_mask & PEL_TLM_F_PICTURE_TYPE) == 0u);
    CHECK(frame->frame.picture_type == 0u);
    return failures;
}

static int test_svt_fixture(const char *path, PelorusEncTelemetryInput *first)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[FRAME_CAP];
    size_t len = 0;
    size_t count = 0;
    uint64_t caps = 0;
    int failures = 0;

    (void)memset(frames, 0, sizeof(frames));
    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_svtav1_stat(text, len, 8u, frames, FRAME_CAP, &count) == PEL_OK);
    CHECK(count == RECORDED_FRAME_COUNT);
    failures += check_svt_frame(&frames[0]);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_SVTAV1_STAT_FILE, PEL_TLM_CODEC_AV1, &caps) ==
          PEL_OK);
    CHECK(frames[0].frame.present_mask == caps);
    *first = frames[0];
    return failures;
}

static int check_vvenc_frame_metrics(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK(fabsf(frame->frame.psnr_y - 45.3714f) < 0.001f);
    CHECK(fabsf(frame->frame.psnr_u - 40.9255f) < 0.001f);
    CHECK(fabsf(frame->frame.psnr_v - 40.3762f) < 0.001f);
    CHECK(frame->frame.codec == PEL_TLM_CODEC_VVC);
    CHECK(frame->frame.qp_scale == PEL_QP_SCALE_SLICE_QP);
    CHECK(frame->frame.granularity == PEL_TLM_GRAN_FRAME);
    CHECK(frame->frame.coded_bit_depth == 10u);
    CHECK(frame->frame.metric_source == PEL_TLM_METRIC_ENCODER);
    CHECK(frame->frame.adapter == PEL_TLM_ADAPTER_VVENC_LOG);
    return failures;
}

static int check_vvenc_frame(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK(frame->frame.display_index == 7u && frame->frame.decode_index == 0u);
    CHECK(frame->frame.frame_bytes == 1842u);
    CHECK(frame->frame.avg_qp == 23.0f && frame->frame.avg_qp_norm == 23.0f);
    CHECK(frame->frame.picture_type == PEL_PICTURE_I);
    CHECK(frame->frame.frame_flags == (PEL_TLM_FRAME_KEY | PEL_TLM_FRAME_SHOWN));
    CHECK((frame->frame.present_mask & PEL_TLM_F_REFERENCE) == 0u);
    failures += check_vvenc_frame_metrics(frame);
    return failures;
}

static int check_vvenc_stream_frames(const PelorusEncTelemetryInput *frames, size_t count,
                                     uint64_t caps)
{
    size_t i;
    int failures = 0;

    for (i = 0; i < count; i++) {
        CHECK(frames[i].frame.present_mask == caps);
        CHECK(frames[i].frame.codec == PEL_TLM_CODEC_VVC);
        CHECK(frames[i].frame.decode_index == (uint32_t)i);
        CHECK(frames[i].frame.avg_qp >= 23.0f && frames[i].frame.avg_qp <= 35.0f);
        CHECK(frames[i].frame.avg_qp_norm == frames[i].frame.avg_qp);
        CHECK((frames[i].frame.frame_flags & PEL_TLM_FRAME_SHOWN) != 0u);
    }
    return failures;
}

/*
 * VVenC v1.14.0 log fixture generated via:
 *   vvencapp --threads 1 -v 4 -q 32 --preset fast -i synth.y4m -o /dev/null > vvenc-1.14.0-qp32.log
 */
static int test_vvenc_fixture(const char *path, PelorusEncTelemetryInput *first)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[FRAME_CAP];
    size_t len = 0;
    size_t count = 0;
    uint64_t caps = 0;
    int failures = 0;

    (void)memset(frames, 0, sizeof(frames));
    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_vvenc_log(text, len, 10u, frames, FRAME_CAP, &count) == PEL_OK);
    CHECK(count == RECORDED_FRAME_COUNT);
    failures += check_vvenc_frame(&frames[0]);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_VVENC_LOG, PEL_TLM_CODEC_VVC, &caps) == PEL_OK);
    failures += check_vvenc_stream_frames(frames, count, caps);
    CHECK(frames[1].frame.display_index == 3u && frames[1].frame.picture_type == PEL_PICTURE_B);
    CHECK(frames[1].frame.frame_bytes == 442u && frames[1].frame.avg_qp == 31.0f);
    CHECK(frames[2].frame.display_index == 1u);
    CHECK(frames[3].frame.display_index == 0u);
    *first = frames[0];
    return failures;
}

static int test_matched_qp(const PelorusEncTelemetryInput *x265,
                           const PelorusEncTelemetryInput *svt)
{
    float direct = -1.0f;
    int failures = 0;

    CHECK(fabsf(x265->frame.avg_qp - svt->frame.avg_qp) > 1.0f);
    CHECK(fabsf(x265->frame.avg_qp_norm - svt->frame.avg_qp_norm) <= 1.0f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8u, 34.0f, &direct) == PEL_OK);
    CHECK(fabsf(direct - 32.0f) <= 1.0f);
    return failures;
}

static float qtx_norm(float qtx, uint8_t depth)
{
    return 6.0f * log2f((qtx / (float)(8u << (depth - 8u))) / 0.625f);
}

static int test_qp_normalize_anchors(void)
{
    float norm = 0.0f;
    float expected;
    int failures = 0;

    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8u, 0.0f, &norm) == PEL_OK);
    expected = 6.0f * log2f((4.0f / 8.0f) / 0.625f);
    CHECK(fabsf(norm - expected) < 1e-4f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8u, 63.0f, &norm) == PEL_OK);
    CHECK(fabsf(norm - qtx_norm(1828.0f, 8u)) < 1e-4f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 10u, 34.0f, &norm) == PEL_OK);
    CHECK(fabsf(norm - qtx_norm(797.0f, 10u)) < 1e-4f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 12u, 34.0f, &norm) == PEL_OK);
    CHECK(fabsf(norm - qtx_norm(3185.0f, 12u)) < 1e-4f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8u, 34.5f, &norm) == PEL_OK);
    expected = (qtx_norm(200.0f, 8u) + qtx_norm(215.0f, 8u)) * 0.5f;
    CHECK(fabsf(norm - expected) < 1e-4f);
    return failures;
}

static int expect_x265_error(const char *text, size_t len, pel_result want)
{
    PelorusEncTelemetryInput frame;
    unsigned char before[sizeof(frame)];
    size_t count = 99u;
    pel_result got;
    int failures = 0;

    (void)memset(&frame, 0xa5, sizeof(frame));
    (void)memcpy(before, &frame, sizeof(frame));
    got = pel_tlm_parse_x265_csv(text, len, 8u, &frame, 1u, &count);
    if (got != want) {
        (void)fprintf(stderr, "x265 error mismatch: got %d, want %d, text %.80s\n", got, want,
                      text);
    }
    CHECK(got == want);
    CHECK(count == 0u);
    CHECK(bytes_equal(&frame, before, sizeof(frame)));
    return failures;
}

static int expect_svt_error(const char *text, pel_result want)
{
    PelorusEncTelemetryInput frame;
    unsigned char before[sizeof(frame)];
    size_t count = 99u;
    int failures = 0;

    (void)memset(&frame, 0xa5, sizeof(frame));
    (void)memcpy(before, &frame, sizeof(frame));
    CHECK(pel_tlm_parse_svtav1_stat(text, strlen(text), 8u, &frame, 1u, &count) == want);
    CHECK(count == 0u);
    CHECK(bytes_equal(&frame, before, sizeof(frame)));
    return failures;
}

static int expect_vvenc_error(const char *text, size_t len, uint8_t depth, pel_result want)
{
    PelorusEncTelemetryInput frame;
    unsigned char before[sizeof(frame)];
    size_t count = 99u;
    pel_result got;
    int failures = 0;

    (void)memset(&frame, 0xa5, sizeof(frame));
    (void)memcpy(before, &frame, sizeof(frame));
    got = pel_tlm_parse_vvenc_log(text, len, depth, &frame, 1u, &count);
    if (got != want) {
        (void)fprintf(stderr, "vvenc error mismatch: got %d, want %d, text %.80s\n", got, want,
                      text);
    }
    CHECK(got == want);
    CHECK(count == 0u);
    CHECK(bytes_equal(&frame, before, sizeof(frame)));
    return failures;
}

static int test_negative_rows(void)
{
    static const char xhdr[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n";
    static const char xtrunc[] =
        "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n0,0,I-SLICE,0,32,15040\n";
    static const char xshift[] =
        "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n0,0,I-SLICE,0,32,15040,0,9\n";
    static const char xnul[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"
                               "0,0,I-SLICE,0,32\0.00,15040,0\n";
    static const char svt_trunc[] =
        "Picture Number: 0 TL: 0 QP: 34 Average QP: 34 [ PSNR-Y: 43.41 dB\n";
    static const char svt_shift[] =
        "Picture Number: 0 TL: 0 QP: 34 Average PSNR: 34 [ PSNR-Y: 43.41 dB, "
        "PSNR-U: 39.10 dB, PSNR-V: 36.73 dB, MSE-Y: 2.96, MSE-U: 8.00, "
        "MSE-V: 13.81, SSIM-Y: 0.99230, SSIM-U: 0.98908, SSIM-V: 0.99024 ] "
        "1364 bytes VBV delay: 0.000 s\n";
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    failures += expect_x265_error(xtrunc, sizeof(xtrunc) - 1u, PEL_ERR_TRUNCATED);
    failures += expect_x265_error(xshift, sizeof(xshift) - 1u, PEL_ERR_INVALID);
    failures += expect_x265_error(xnul, sizeof(xnul) - 1u, PEL_ERR_INVALID);
    failures += expect_svt_error(svt_trunc, PEL_ERR_TRUNCATED);
    failures += expect_svt_error(svt_shift, PEL_ERR_INVALID);
    failures += expect_x265_error("", 0u, PEL_ERR_ABSENT);
    failures += expect_svt_error("", PEL_ERR_ABSENT);
    CHECK(pel_tlm_parse_x265_csv(xhdr, strlen(xhdr), 8u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_svtav1_stat("SUMMARY ---\n", 12u, 8u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 0u);
    return failures;
}

static int test_vvenc_negative_rows(void)
{
    static const char vvenc_trunc_bits[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736\n";
    static const char vvenc_trunc_psnr[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB\n";
    static const char vvenc_trunc_bracket[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB\n";
    static const char vvenc_bad_slice[] =
        "POC     7 TId: 0 (IDR_W_RADL, X-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_inverted[] =
        "TId: 0 POC 7 (IDR_W_RADL, I-SLICE, QP 23, TF 2) 14736 bits [Y 45.3714 dB U 40.9255 dB V 40.3762 dB]\n";
    static const char vvenc_bad_poc[] =
        "POC    ab TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_bad_bits[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14735 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_qp_high[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 64, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_qp_neg8[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP -1, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_bad_qp[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP ab, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_tid7[] =
        "POC     7 TId: 7 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_nan_psnr[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y nan dB    U 40.9255 dB    V 40.3762 dB]\n";
    int failures = 0;

    failures +=
        expect_vvenc_error(vvenc_trunc_bits, sizeof(vvenc_trunc_bits) - 1u, 10u, PEL_ERR_TRUNCATED);
    failures +=
        expect_vvenc_error(vvenc_trunc_psnr, sizeof(vvenc_trunc_psnr) - 1u, 10u, PEL_ERR_TRUNCATED);
    failures += expect_vvenc_error(vvenc_trunc_bracket, sizeof(vvenc_trunc_bracket) - 1u, 10u,
                                   PEL_ERR_TRUNCATED);
    failures +=
        expect_vvenc_error(vvenc_bad_slice, sizeof(vvenc_bad_slice) - 1u, 10u, PEL_ERR_INVALID);
    failures +=
        expect_vvenc_error(vvenc_inverted, sizeof(vvenc_inverted) - 1u, 10u, PEL_ERR_INVALID);
    failures += expect_vvenc_error(vvenc_bad_poc, sizeof(vvenc_bad_poc) - 1u, 10u, PEL_ERR_INVALID);
    failures +=
        expect_vvenc_error(vvenc_bad_bits, sizeof(vvenc_bad_bits) - 1u, 10u, PEL_ERR_INVALID);
    failures += expect_vvenc_error(vvenc_qp_high, sizeof(vvenc_qp_high) - 1u, 10u, PEL_ERR_RANGE);
    failures += expect_vvenc_error(vvenc_qp_neg8, sizeof(vvenc_qp_neg8) - 1u, 8u, PEL_ERR_RANGE);
    failures += expect_vvenc_error(vvenc_bad_qp, sizeof(vvenc_bad_qp) - 1u, 10u, PEL_ERR_INVALID);
    failures += expect_vvenc_error(vvenc_tid7, sizeof(vvenc_tid7) - 1u, 10u, PEL_ERR_RANGE);
    failures +=
        expect_vvenc_error(vvenc_nan_psnr, sizeof(vvenc_nan_psnr) - 1u, 10u, PEL_ERR_INVALID);
    return failures;
}

static int test_vvenc_non_poc_lines(void)
{
    static const char vvenc_notices[] = "vvencapp: VVenC, the Fraunhofer H.266/VVC Encoder\n"
                                        "vvenc [notice]: Rate control: QP 32\n"
                                        "vvenc [info]: stats summary\n";
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_vvenc_log(vvenc_notices, sizeof(vvenc_notices) - 1u, 10u, &frame, 1u,
                                  &count) == PEL_OK);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_vvenc_log("", 0u, 10u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 0u);
    return failures;
}

static int test_vvenc_positive_variants(void)
{
    static const char vvenc_crlf[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB] [ET     0 ] [L0 ] [L1 ]\r\n";
    static const char vvenc_no_tf[] =
        "POC     3 TId: 3 (      RADL, B-SLICE, QP 31)             3536 bits [Y 40.1352 dB    U 35.4781 dB    V 35.1209 dB] [ET     0 ] [L0 7 ] [L1 7 ]\n";
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_vvenc_log(vvenc_crlf, sizeof(vvenc_crlf) - 1u, 10u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.display_index == 7u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_no_tf, sizeof(vvenc_no_tf) - 1u, 10u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.avg_qp == 31.0f);
    failures += test_vvenc_non_poc_lines();
    return failures;
}

static int test_vvenc_boundary_variants(void)
{
    static const char vvenc_tid6[] =
        "POC     7 TId: 6 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_unref[] =
        "POC     7 TId: 0 (IDR_W_RADL, b-SLICE, QP 23, TF 2)      14736 bits [Y 45.3714 dB    U 40.9255 dB    V 40.3762 dB]\n";
    static const char vvenc_psnr_inf[] =
        "POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      14736 bits [Y     inf dB    U 40.9255 dB    V 40.3762 dB]\n";
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_vvenc_log(vvenc_tid6, sizeof(vvenc_tid6) - 1u, 10u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_unref, sizeof(vvenc_unref) - 1u, 10u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.picture_type == PEL_PICTURE_B);
    CHECK((frame.frame.present_mask & PEL_TLM_F_REFERENCE) == 0u);
    CHECK((frame.frame.frame_flags & PEL_TLM_FRAME_REFERENCE) == 0u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_psnr_inf, sizeof(vvenc_psnr_inf) - 1u, 10u, &frame, 1u,
                                  &count) == PEL_OK);
    CHECK(count == 1u && frame.frame.psnr_y == 100.0f);
    return failures;
}

static int test_x265_duplicate_headers(void)
{
    static const char trailing_required[] = "Encode Order, Type, POC, QP, Bits, Scenecut, QP\n";
    static const char leading_required[] = "QP, Encode Order, Type, POC, QP, Bits, Scenecut\n";
    static const char repeated_unconsumed[] =
        "Layer, Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"
        "0,base,0,I-SLICE,0,32,15040,0\n";
    PelorusEncTelemetryInput frame;
    size_t count = 0;
    int failures = 0;

    failures +=
        expect_x265_error(trailing_required, sizeof(trailing_required) - 1u, PEL_ERR_INVALID);
    failures += expect_x265_error(leading_required, sizeof(leading_required) - 1u, PEL_ERR_INVALID);
    CHECK(pel_tlm_parse_x265_csv(repeated_unconsumed, sizeof(repeated_unconsumed) - 1u, 8u, &frame,
                                 1u, &count) == PEL_OK);
    CHECK(count == 1u);
    failures += check_x265_frame(&frame);
    return failures;
}

static int test_number_allow_list(void)
{
    static const char hex_float[] = X265_HEADER_TEXT "0,0,I-SLICE,0,0x1p3,15040,0\n";
    static const char infinity[] = X265_HEADER_TEXT "0,0,I-SLICE,0,inf,15040,0\n";
    static const char leading_plus[] = X265_HEADER_TEXT "0,0,I-SLICE,0,+5,15040,0\n";
    static const char inner_space[] = X265_HEADER_TEXT "0,0,I-SLICE,0,3 2,15040,0\n";
    static const char partial_byte[] = X265_HEADER_TEXT "0,0,I-SLICE,0,32,15041,0\n";
    int failures = 0;

    failures += expect_x265_error(hex_float, sizeof(hex_float) - 1u, PEL_ERR_INVALID);
    failures += expect_x265_error(infinity, sizeof(infinity) - 1u, PEL_ERR_INVALID);
    failures += expect_x265_error(leading_plus, sizeof(leading_plus) - 1u, PEL_ERR_INVALID);
    failures += expect_x265_error(inner_space, sizeof(inner_space) - 1u, PEL_ERR_INVALID);
    failures += expect_x265_error(partial_byte, sizeof(partial_byte) - 1u, PEL_ERR_INVALID);
    return failures;
}

static int test_svt_nonfinite_psnr(void)
{
    char row[sizeof(svt_row)];
    char *psnr;
    int failures = 0;

    (void)memcpy(row, svt_row, sizeof(row));
    psnr = strstr(row, "43.41");
    CHECK(psnr != NULL);
    if (psnr != NULL) {
        (void)memcpy(psnr, "inf  ", 5u);
        failures += expect_svt_error(row, PEL_ERR_INVALID);
        (void)memcpy(psnr, "nan  ", 5u);
        failures += expect_svt_error(row, PEL_ERR_INVALID);
    }
    return failures;
}

static int test_x265_terminator_and_scenecut(void)
{
    static const char terminated[] = X265_HEADER_TEXT "0,0,I-SLICE,0,32,15040,1\n"
                                                      "Summary\n"
                                                      "malformed,row\n";
    static const char bad_cut[] = X265_HEADER_TEXT "0,0,I-SLICE,0,32,15040,2\n";
    PelorusEncTelemetryInput frame;
    size_t count = 0;
    int failures = 0;

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_x265_csv(terminated, sizeof(terminated) - 1u, 8u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u);
    CHECK((frame.frame.frame_flags & PEL_TLM_FRAME_SCENE_CUT) != 0u);
    failures += expect_x265_error(bad_cut, sizeof(bad_cut) - 1u, PEL_ERR_RANGE);
    return failures;
}

static int test_vvenc_depths(void)
{
    PelorusEncTelemetryInput frame;
    size_t count = 0;
    int failures = 0;

    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 8u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 8u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 10u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 10u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 12u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 12u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 9u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    return failures;
}

static int test_depths(void)
{
    static const char xrow[] = X265_HEADER_TEXT "0,0,I-SLICE,0,32,15040,0\n";
    PelorusEncTelemetryInput frame;
    size_t count = 0;
    int failures = 0;

    CHECK(pel_tlm_parse_x265_csv(xrow, sizeof(xrow) - 1u, 10u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 10u);
    CHECK(pel_tlm_parse_svtav1_stat(svt_row, sizeof(svt_row) - 1u, 10u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 10u);
    CHECK(pel_tlm_parse_x265_csv(xrow, sizeof(xrow) - 1u, 12u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 12u);
    CHECK(pel_tlm_parse_svtav1_stat(svt_row, sizeof(svt_row) - 1u, 12u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.coded_bit_depth == 12u);
    CHECK(pel_tlm_parse_x265_csv(xrow, sizeof(xrow) - 1u, 9u, &frame, 1u, &count) == PEL_ERR_RANGE);
    CHECK(pel_tlm_parse_svtav1_stat(svt_row, sizeof(svt_row) - 1u, 9u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    failures += test_vvenc_depths();
    return failures;
}

static int test_svt_average_qp_domain(void)
{
    char mutated[sizeof(svt_qp63_row)];
    PelorusEncTelemetryInput frame;
    char *average;
    float expected = 0.0f;
    size_t count = 0;
    int failures = 0;

    CHECK(pel_tlm_parse_svtav1_stat(svt_qp63_row, sizeof(svt_qp63_row) - 1u, 8u, &frame, 1u,
                                    &count) == PEL_OK);
    CHECK(count == 1u && frame.frame.avg_qp == 63.0f);
    CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8u, 63.0f, &expected) == PEL_OK);
    CHECK(fabsf(frame.frame.avg_qp_norm - expected) < 0.0001f);
    (void)memcpy(mutated, svt_qp63_row, sizeof(mutated));
    average = strstr(mutated, "Average QP: 64");
    CHECK(average != NULL);
    if (average != NULL) {
        average[strlen("Average QP: 6")] = '3';
        count = 0u;
        CHECK(pel_tlm_parse_svtav1_stat(mutated, sizeof(mutated) - 1u, 8u, &frame, 1u, &count) ==
              PEL_OK);
        CHECK(count == 1u && fabsf(frame.frame.avg_qp - 62.5f) < 0.0001f);
        average[strlen("Average QP: 6")] = '5';
        failures += expect_svt_error(mutated, PEL_ERR_RANGE);
    }
    return failures;
}

static int test_x265_null_arguments(void)
{
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_x265_csv(NULL, 0u, 8u, &frame, 1u, &count) == PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_x265_csv(x265_header, sizeof(x265_header) - 1u, 8u, NULL, 1u, &count) ==
          PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_x265_csv(x265_header, sizeof(x265_header) - 1u, 8u, &frame, 1u, NULL) ==
          PEL_ERR_INVALID);
    CHECK(pel_tlm_parse_x265_csv(x265_header, sizeof(x265_header) - 1u, 8u, &frame, 0u, &count) ==
          PEL_ERR_INVALID);
    CHECK(count == 0u);
    return failures;
}

static int test_svt_null_arguments(void)
{
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_svtav1_stat(NULL, 0u, 8u, &frame, 1u, &count) == PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_svtav1_stat(svt_row, sizeof(svt_row) - 1u, 8u, NULL, 1u, &count) ==
          PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_svtav1_stat(svt_row, sizeof(svt_row) - 1u, 8u, &frame, 1u, NULL) ==
          PEL_ERR_INVALID);
    CHECK(pel_tlm_parse_svtav1_stat(svt_row, sizeof(svt_row) - 1u, 8u, &frame, 0u, &count) ==
          PEL_ERR_INVALID);
    CHECK(count == 0u);
    return failures;
}

static int test_vvenc_null_arguments(void)
{
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_vvenc_log(NULL, 0u, 10u, &frame, 1u, &count) == PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 10u, NULL, 1u, &count) ==
          PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 10u, &frame, 1u, NULL) ==
          PEL_ERR_INVALID);
    CHECK(pel_tlm_parse_vvenc_log(vvenc_row, sizeof(vvenc_row) - 1u, 10u, &frame, 0u, &count) ==
          PEL_ERR_INVALID);
    CHECK(count == 0u);
    return failures;
}

static int test_line_and_field_bounds(void)
{
    char fields[TEST_CSV_FIELDS_OVER_MAX * 2u];
    char *line = malloc(TEST_TEXT_LINE_OVER_MAX);
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    size_t i;
    int failures = 0;

    CHECK(line != NULL);
    if (line == NULL) {
        return failures;
    }
    (void)memset(line, 'x', TEST_TEXT_LINE_OVER_MAX);
    failures += expect_x265_error(line, TEST_TEXT_LINE_OVER_MAX, PEL_ERR_RANGE);
    CHECK(pel_tlm_parse_svtav1_stat(line, TEST_TEXT_LINE_OVER_MAX, 8u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_vvenc_log(line, TEST_TEXT_LINE_OVER_MAX, 10u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == 0u);
    free(line);
    for (i = 0u; i < TEST_CSV_FIELDS_OVER_MAX; i++) {
        fields[i * 2u] = 'x';
        fields[i * 2u + 1u] = i + 1u == TEST_CSV_FIELDS_OVER_MAX ? '\n' : ',';
    }
    failures += expect_x265_error(fields, sizeof(fields), PEL_ERR_RANGE);
    return failures;
}

static int check_vvenc_total_text_bound(const char *text)
{
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_vvenc_log(text, TEST_TEXT_TOTAL_MAX, 10u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_vvenc_log(text, TEST_TEXT_TOTAL_MAX + 1u, 10u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == 0u);
    return failures;
}

static int test_total_text_bound(void)
{
    char *text = malloc(TEST_TEXT_TOTAL_MAX + 1u);
    PelorusEncTelemetryInput frame;
    size_t count = 99u;
    int failures = 0;

    CHECK(text != NULL);
    if (text == NULL) {
        return failures;
    }
    (void)memset(text, '\n', TEST_TEXT_TOTAL_MAX + 1u);
    CHECK(pel_tlm_parse_x265_csv(text, TEST_TEXT_TOTAL_MAX, 8u, &frame, 1u, &count) ==
          PEL_ERR_ABSENT);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_svtav1_stat(text, TEST_TEXT_TOTAL_MAX, 8u, &frame, 1u, &count) ==
          PEL_ERR_ABSENT);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_x265_csv(text, TEST_TEXT_TOTAL_MAX + 1u, 8u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_svtav1_stat(text, TEST_TEXT_TOTAL_MAX + 1u, 8u, &frame, 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == 0u);
    failures += check_vvenc_total_text_bound(text);
    free(text);
    return failures;
}

static int test_capacity(const char *path)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[RECORDED_FRAME_COUNT];
    unsigned char before[sizeof(frames)];
    size_t len = 0;
    size_t count = 0;
    int failures = 0;

    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_x265_csv(text, len, 8u, frames, RECORDED_FRAME_COUNT, &count) == PEL_OK);
    CHECK(count == RECORDED_FRAME_COUNT);
    (void)memset(frames, 0xa5, sizeof(frames));
    (void)memcpy(before, frames, sizeof(frames));
    CHECK(pel_tlm_parse_x265_csv(text, len, 8u, frames, RECORDED_FRAME_COUNT - 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == RECORDED_FRAME_COUNT);
    CHECK(bytes_equal(frames, before, sizeof(frames)));
    CHECK(pel_tlm_parse_x265_csv(text, len, 8u, frames, 0u, &count) == PEL_ERR_INVALID);
    CHECK(count == 0u);
    return failures;
}

static int test_svt_capacity(const char *path)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[RECORDED_FRAME_COUNT];
    unsigned char before[sizeof(frames)];
    size_t len = 0;
    size_t count = 0;
    int failures = 0;

    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_svtav1_stat(text, len, 8u, frames, RECORDED_FRAME_COUNT, &count) == PEL_OK);
    CHECK(count == RECORDED_FRAME_COUNT);
    (void)memset(frames, 0xa5, sizeof(frames));
    (void)memcpy(before, frames, sizeof(frames));
    CHECK(pel_tlm_parse_svtav1_stat(text, len, 8u, frames, RECORDED_FRAME_COUNT - 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == RECORDED_FRAME_COUNT);
    CHECK(bytes_equal(frames, before, sizeof(frames)));
    return failures;
}

static int test_vvenc_capacity(const char *path)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[RECORDED_FRAME_COUNT];
    unsigned char before[sizeof(frames)];
    size_t len = 0;
    size_t count = 0;
    int failures = 0;

    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_vvenc_log(text, len, 10u, frames, RECORDED_FRAME_COUNT, &count) == PEL_OK);
    CHECK(count == RECORDED_FRAME_COUNT);
    (void)memset(frames, 0xa5, sizeof(frames));
    (void)memcpy(before, frames, sizeof(frames));
    CHECK(pel_tlm_parse_vvenc_log(text, len, 10u, frames, RECORDED_FRAME_COUNT - 1u, &count) ==
          PEL_ERR_RANGE);
    CHECK(count == RECORDED_FRAME_COUNT);
    CHECK(bytes_equal(frames, before, sizeof(frames)));
    CHECK(pel_tlm_parse_vvenc_log(text, len, 10u, frames, 0u, &count) == PEL_ERR_INVALID);
    CHECK(count == 0u);
    return failures;
}

static int test_non_c_numeric_locale(void)
{
    static const char xrow[] = X265_HEADER_TEXT "0,0,I-SLICE,0,32.50,15040,0\n";
    char saved[128];
    /* NOLINTBEGIN(concurrency-mt-unsafe): #111 runs this locale check in a standalone process. */
    const char *current = setlocale(LC_NUMERIC, NULL);
    PelorusEncTelemetryInput frame;
    size_t count = 0;
    size_t saved_len;
    int failures = 0;

    CHECK(current != NULL);
    if (current == NULL) {
        return EXIT_FAILURE;
    }
    saved_len = strlen(current);
    CHECK(saved_len < sizeof(saved));
    if (saved_len >= sizeof(saved)) {
        return EXIT_FAILURE;
    }
    (void)memcpy(saved, current, saved_len + 1u);
    if (setlocale(LC_NUMERIC, "de_DE.UTF-8") == NULL &&
        setlocale(LC_NUMERIC, "de_DE.utf8") == NULL &&
        setlocale(LC_NUMERIC, "fr_FR.UTF-8") == NULL) {
        (void)printf("SKIP: comma-decimal LC_NUMERIC locale unavailable\n");
        return TEST_SKIP;
    }
    CHECK(strcmp(localeconv()->decimal_point, ",") == 0);
    CHECK(pel_tlm_parse_x265_csv(xrow, sizeof(xrow) - 1u, 8u, &frame, 1u, &count) == PEL_OK);
    CHECK(count == 1u && frame.frame.avg_qp == 32.5f);
    CHECK(setlocale(LC_NUMERIC, saved) != NULL);
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
    /* NOLINTEND(concurrency-mt-unsafe) */
}

static void write_u32le(uint8_t *p, uint32_t val)
{
    p[0] = (uint8_t)(val & 0xFFu);
    p[1] = (uint8_t)((val >> 8u) & 0xFFu);
    p[2] = (uint8_t)((val >> 16u) & 0xFFu);
    p[3] = (uint8_t)((val >> 24u) & 0xFFu);
}

static void write_u64le(uint8_t *p, uint64_t val)
{
    p[0] = (uint8_t)(val & 0xFFu);
    p[1] = (uint8_t)((val >> 8u) & 0xFFu);
    p[2] = (uint8_t)((val >> 16u) & 0xFFu);
    p[3] = (uint8_t)((val >> 24u) & 0xFFu);
    p[4] = (uint8_t)((val >> 32u) & 0xFFu);
    p[5] = (uint8_t)((val >> 40u) & 0xFFu);
    p[6] = (uint8_t)((val >> 48u) & 0xFFu);
    p[7] = (uint8_t)((val >> 56u) & 0xFFu);
}

static int check_ffmpeg_qstats_x264_frame(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK(frame->frame.adapter == PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS);
    CHECK(frame->frame.codec == PEL_TLM_CODEC_H264);
    CHECK(frame->frame.qp_scale == PEL_QP_SCALE_SLICE_QP);
    CHECK(frame->frame.avg_qp == 26.0f);
    CHECK(frame->frame.avg_qp_norm == 26.0f);
    CHECK(frame->frame.picture_type == PEL_PICTURE_I);
    CHECK(frame->frame.metric_source == PEL_TLM_METRIC_ADAPTER_SSE);
    return failures;
}

static int check_ffmpeg_qstats_x264_psnr(const PelorusEncTelemetryInput *frame,
                                         const uint64_t plane_samples[3])
{
    const double peak = 255.0;
    const double exp_y = 10.0 * log10((peak * peak * (double)plane_samples[0]) / 1000.0);
    const double exp_u = 10.0 * log10((peak * peak * (double)plane_samples[1]) / 200.0);
    const double exp_v = 10.0 * log10((peak * peak * (double)plane_samples[2]) / 150.0);
    int failures = 0;

    CHECK(fabsf(frame->frame.psnr_y - (float)exp_y) < 0.01f);
    CHECK(fabsf(frame->frame.psnr_u - (float)exp_u) < 0.01f);
    CHECK(fabsf(frame->frame.psnr_v - (float)exp_v) < 0.01f);
    CHECK(pel_enc_telemetry_validate(frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_positive_x264(void)
{
    uint8_t buf[8 + 8 * 3];
    uint64_t plane_samples[3] = {(uint64_t)1920u * 1080u, (uint64_t)960u * 540u,
                                 (uint64_t)960u * 540u};
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u; /* I-frame */
    buf[5] = 3u; /* 3 error planes */
    write_u64le(buf + 8, 1000u);
    write_u64le(buf + 16, 200u);
    write_u64le(buf + 24, 150u);

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, plane_samples,
                                             &frame) == PEL_OK);
    failures += check_ffmpeg_qstats_x264_frame(&frame);
    failures += check_ffmpeg_qstats_x264_psnr(&frame, plane_samples);
    return failures;
}

static int test_ffmpeg_qstats_positive_x265(void)
{
    uint8_t x265_buf[8];
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(x265_buf, 0, sizeof(x265_buf));
    write_u32le(x265_buf, 32u * 118u);
    x265_buf[4] = 2u; /* P-frame */
    x265_buf[5] = 0u; /* no SSE */
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(x265_buf, sizeof(x265_buf), PEL_TLM_CODEC_HEVC,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL, &frame) == PEL_OK);
    CHECK(frame.frame.avg_qp == 32.0f);
    CHECK(frame.frame.avg_qp_norm == 32.0f);
    CHECK(frame.frame.picture_type == PEL_PICTURE_P);
    CHECK((frame.frame.present_mask & PEL_TLM_F_PSNR_Y) == 0u);
    CHECK(frame.frame.metric_source == 0u);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_positive_aom(void)
{
    uint8_t aom_buf[8 + 8 * 3];
    uint64_t plane_samples[3] = {(uint64_t)1920u * 1080u, (uint64_t)960u * 540u,
                                 (uint64_t)960u * 540u};
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(aom_buf, 0, sizeof(aom_buf));
    aom_buf[4] = 1u; /* I-frame */
    aom_buf[5] = 3u; /* 3 SSE */
    write_u64le(aom_buf + 8, 500u);
    write_u64le(aom_buf + 16, 100u);
    write_u64le(aom_buf + 24, 100u);
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(aom_buf, sizeof(aom_buf), PEL_TLM_CODEC_AV1,
                                             PEL_QP_SCALE_AV1_ENCODER_QP, 8u, plane_samples,
                                             &frame) == PEL_OK);
    CHECK((frame.frame.present_mask & PEL_TLM_F_AVG_QP) == 0u);
    CHECK((frame.frame.present_mask & PEL_TLM_F_AVG_QP_NORM) == 0u);
    CHECK(frame.frame.avg_qp == 0.0f);
    CHECK(frame.frame.avg_qp_norm == 0.0f);
    CHECK(frame.frame.qp_scale == 0u);
    CHECK(frame.frame.picture_type == PEL_PICTURE_I);
    CHECK((frame.frame.present_mask & PEL_TLM_F_PSNR_Y) != 0u);
    CHECK(frame.frame.metric_source == PEL_TLM_METRIC_ADAPTER_SSE);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int check_ffmpeg_qstats_vp9_qp(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK(frame->frame.codec == PEL_TLM_CODEC_VP9);
    CHECK((frame->frame.present_mask & (PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM)) == 0u);
    CHECK(frame->frame.avg_qp == 0.0f);
    CHECK(frame->frame.avg_qp_norm == 0.0f);
    CHECK(frame->frame.qp_scale == 0u);
    return failures;
}

static int check_ffmpeg_qstats_vp9_metrics(const PelorusEncTelemetryInput *frame)
{
    int failures = 0;

    CHECK((frame->frame.present_mask & PEL_TLM_F_PICTURE_TYPE) != 0u);
    CHECK(frame->frame.picture_type == PEL_PICTURE_P);
    CHECK((frame->frame.present_mask & PEL_TLM_F_PSNR_Y) != 0u);
    CHECK(frame->frame.psnr_y > 0.0f);
    CHECK(frame->frame.metric_source == PEL_TLM_METRIC_ADAPTER_SSE);
    CHECK(pel_enc_telemetry_validate(frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_positive_vp9(void)
{
    uint8_t buf[8 + 8 * 3];
    uint64_t plane_samples[3] = {(uint64_t)1920u * 1080u, (uint64_t)960u * 540u,
                                 (uint64_t)960u * 540u};
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    /* libvpxenc writes VP8E_GET_LAST_QUANTIZER_64 * 118; ignored by adapter */
    write_u32le(buf, 35u * 118u);
    buf[4] = 2u; /* P-frame */
    buf[5] = 3u;
    write_u64le(buf + 8, 1000u);
    write_u64le(buf + 16, 500u);
    write_u64le(buf + 24, 500u);

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_VP9,
                                             PEL_QP_SCALE_VP9_QINDEX, 8u, plane_samples,
                                             &frame) == PEL_OK);
    failures += check_ffmpeg_qstats_vp9_qp(&frame);
    failures += check_ffmpeg_qstats_vp9_metrics(&frame);
    return failures;
}

static int test_ffmpeg_qstats_depth10_and_sse0(void)
{
    uint8_t buf[8 + 8 * 3];
    uint64_t plane_samples[3] = {(uint64_t)1920u * 1080u, (uint64_t)960u * 540u,
                                 (uint64_t)960u * 540u};
    PelorusEncTelemetryInput frame;
    double peak = 1023.0;
    double exp_y = 10.0 * log10((peak * peak * (double)plane_samples[0]) / 2500.0);
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 28u * 118u);
    buf[4] = 3u; /* B-frame */
    buf[5] = 3u;
    write_u64le(buf + 8, 2500u);
    write_u64le(buf + 16, 0u); /* SSE 0 -> 100 dB cap */
    write_u64le(buf + 24, 500u);

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_HEVC,
                                             PEL_QP_SCALE_SLICE_QP, 10u, plane_samples,
                                             &frame) == PEL_OK);
    CHECK(frame.frame.coded_bit_depth == 10u);
    CHECK(frame.frame.picture_type == PEL_PICTURE_B);
    CHECK(fabsf(frame.frame.psnr_y - (float)exp_y) < 0.01f);
    CHECK(frame.frame.psnr_u == 100.0f);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_boundaries(void)
{
    uint8_t b0[8];
    uint8_t b255[8 + 8 * 255];
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(b0, 0, sizeof(b0));
    write_u32le(b0, 20u * 118u);
    b0[4] = 1u;
    b0[5] = 0u;
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(b0, sizeof(b0), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL, &frame) == PEL_OK);
    CHECK(frame.frame.avg_qp == 20.0f);

    (void)memset(b255, 0, sizeof(b255));
    write_u32le(b255, 20u * 118u);
    b255[4] = 1u;
    b255[5] = 255u;
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(b255, sizeof(b255), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL, &frame) == PEL_OK);
    CHECK(frame.frame.avg_qp == 20.0f);
    return failures;
}

static int test_ffmpeg_qstats_negative_args(void)
{
    uint8_t buf[8 + 8 * 3];
    PelorusEncTelemetryInput frame;
    PelorusEncTelemetryInput untouched;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 3u;
    (void)memset(&untouched, 0x5a, sizeof(untouched));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(NULL, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL,
                                             &frame) == PEL_ERR_INVALID);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL,
                                             NULL) == PEL_ERR_INVALID);

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_AV1_ENCODER_QP, 8u, NULL,
                                             &frame) == PEL_ERR_INVALID);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_AV1,
                                             PEL_QP_SCALE_AV1_QINDEX, 8u, NULL,
                                             &frame) == PEL_ERR_INVALID);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 9u, NULL,
                                             &frame) == PEL_ERR_RANGE);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));
    return failures;
}

static int test_ffmpeg_qstats_negative_lens(void)
{
    uint8_t buf[8 + 8 * 3];
    PelorusEncTelemetryInput frame;
    PelorusEncTelemetryInput untouched;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 3u;
    (void)memset(&untouched, 0x5a, sizeof(untouched));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 7u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP, 8u,
                                             NULL, &frame) == PEL_ERR_TRUNCATED);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 24u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP,
                                             8u, NULL, &frame) == PEL_ERR_TRUNCATED);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    buf[5] = 1u;
    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 15u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP,
                                             8u, NULL, &frame) == PEL_ERR_TRUNCATED);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));
    return failures;
}

static int test_ffmpeg_qstats_tolerant_bytes(void)
{
    uint8_t buf[8 + 8 * 3];
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 0u;
    /* Reserved bytes 6..7 must be ignored, not refused */
    buf[6] = 0xefu;
    buf[7] = 0xbeu;
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 8u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP, 8u,
                                             NULL, &frame) == PEL_OK);
    CHECK(frame.frame.avg_qp == 26.0f);

    /* Trailing bytes on 0 SSE planes (len 16 > 8) must be accepted and ignored */
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 16u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP,
                                             8u, NULL, &frame) == PEL_OK);
    CHECK(frame.frame.avg_qp == 26.0f);

    /* Trailing bytes with SSE planes (1 plane = 16 bytes, passed len 32) */
    buf[5] = 1u;
    write_u64le(buf + 8, 100u);
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 32u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP,
                                             8u, NULL, &frame) == PEL_OK);
    CHECK(frame.frame.avg_qp == 26.0f);
    return failures;
}

static int test_ffmpeg_qstats_pict_types(void)
{
    uint8_t buf[8];
    PelorusEncTelemetryInput frame;
    PelorusEncTelemetryInput untouched;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 20u * 118u);

    buf[4] = 0u;
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL, &frame) == PEL_OK);
    CHECK((frame.frame.present_mask & PEL_TLM_F_PICTURE_TYPE) == 0u);
    CHECK(frame.frame.picture_type == 0u);

    buf[4] = 4u;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL, &frame) == PEL_OK);
    CHECK((frame.frame.present_mask & PEL_TLM_F_PICTURE_TYPE) == 0u);

    (void)memset(&untouched, 0x5a, sizeof(untouched));
    frame = untouched;
    buf[4] = 8u;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL,
                                             &frame) == PEL_ERR_RANGE);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));
    return failures;
}

static int test_ffmpeg_qstats_positive_av1_encoder_qp(void)
{
    uint8_t buf[8];
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 30u * 118u);
    buf[4] = 1u;
    buf[5] = 0u;
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_AV1,
                                             PEL_QP_SCALE_AV1_ENCODER_QP, 8u, NULL,
                                             &frame) == PEL_OK);
    CHECK((frame.frame.present_mask & PEL_TLM_F_AVG_QP) != 0u);
    CHECK((frame.frame.present_mask & PEL_TLM_F_AVG_QP_NORM) != 0u);
    CHECK(frame.frame.avg_qp == 30.0f);
    CHECK(frame.frame.avg_qp_norm > 0.0f);
    CHECK(frame.frame.qp_scale == PEL_QP_SCALE_AV1_ENCODER_QP);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_plane_selection_y_only(void)
{
    uint8_t buf[8 + 8 * 3];
    uint64_t plane_samples_y[3] = {(uint64_t)1920u * 1080u, 0u, 0u};
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 3u;
    write_u64le(buf + 8, 1000u);
    write_u64le(buf + 16, 200u);
    write_u64le(buf + 24, 150u);

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, plane_samples_y,
                                             &frame) == PEL_OK);
    CHECK((frame.frame.present_mask & PEL_TLM_F_PSNR_Y) != 0u);
    CHECK((frame.frame.present_mask & (PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V)) == 0u);
    CHECK(frame.frame.psnr_y > 0.0f);
    CHECK(frame.frame.psnr_u == 0.0f);
    CHECK(frame.frame.psnr_v == 0.0f);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_plane_selection_count1(void)
{
    uint8_t buf[8 + 8];
    uint64_t plane_samples_all[3] = {(uint64_t)1920u * 1080u, (uint64_t)960u * 540u,
                                     (uint64_t)960u * 540u};
    PelorusEncTelemetryInput frame;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 1u;
    write_u64le(buf + 8, 1000u);

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, plane_samples_all,
                                             &frame) == PEL_OK);
    CHECK((frame.frame.present_mask & PEL_TLM_F_PSNR_Y) != 0u);
    CHECK((frame.frame.present_mask & (PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V)) == 0u);
    CHECK(frame.frame.psnr_y > 0.0f);
    CHECK(frame.frame.psnr_u == 0.0f);
    CHECK(frame.frame.psnr_v == 0.0f);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_tiny_sse_and_depth12(void)
{
    uint8_t buf[8 + 8];
    uint64_t plane_samples[3] = {(uint64_t)1920u * 1080u, 0u, 0u};
    PelorusEncTelemetryInput frame;
    const double peak12 = 4095.0;
    const double exp12_y = 10.0 * log10((peak12 * peak12 * (double)plane_samples[0]) / 20000000.0);
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 1u;
    write_u64le(buf + 8, 1u); /* tiny nonzero SSE -> PSNR exactly 100.0f cap */

    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_H264,
                                             PEL_QP_SCALE_SLICE_QP, 8u, plane_samples,
                                             &frame) == PEL_OK);
    CHECK(frame.frame.psnr_y == 100.0f);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);

    write_u64le(buf + 8, 20000000u);
    (void)memset(&frame, 0, sizeof(frame));
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_HEVC,
                                             PEL_QP_SCALE_SLICE_QP, 12u, plane_samples,
                                             &frame) == PEL_OK);
    CHECK(frame.frame.coded_bit_depth == 12u);
    CHECK(fabsf(frame.frame.psnr_y - (float)exp12_y) < 0.01f);
    CHECK(pel_enc_telemetry_validate(&frame) == PEL_OK);
    return failures;
}

static int test_ffmpeg_qstats_negative_codecs_and_scales(void)
{
    uint8_t buf[8];
    PelorusEncTelemetryInput frame;
    PelorusEncTelemetryInput untouched;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 26u * 118u);
    buf[4] = 1u;
    buf[5] = 0u;
    (void)memset(&untouched, 0x5a, sizeof(untouched));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), PEL_TLM_CODEC_VP9,
                                             PEL_QP_SCALE_SLICE_QP, 8u, NULL,
                                             &frame) == PEL_ERR_INVALID);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), 0u, PEL_QP_SCALE_SLICE_QP, 8u, NULL,
                                             &frame) == PEL_ERR_INVALID);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, sizeof(buf), 6u, PEL_QP_SCALE_SLICE_QP, 8u, NULL,
                                             &frame) == PEL_ERR_INVALID);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));
    return failures;
}

static int test_ffmpeg_qstats_negative_quality_and_sse(void)
{
    uint8_t buf[8 + 8];
    uint64_t plane_samples[3] = {100u, 0u, 0u};
    PelorusEncTelemetryInput frame;
    PelorusEncTelemetryInput untouched;
    int failures = 0;

    (void)memset(buf, 0, sizeof(buf));
    write_u32le(buf, 200u * 118u); /* huge quality factor -> PEL_ERR_RANGE */
    buf[4] = 1u;
    buf[5] = 0u;
    (void)memset(&untouched, 0x5a, sizeof(untouched));

    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 8u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP, 8u,
                                             NULL, &frame) == PEL_ERR_RANGE);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    write_u32le(buf, UINT32_MAX);
    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 8u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP, 8u,
                                             NULL, &frame) == PEL_ERR_RANGE);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));

    write_u32le(buf, 26u * 118u);
    buf[5] = 1u;
    write_u64le(buf + 8, 7000000u); /* SSE > peak^2 * samples (6502500) -> PEL_ERR_RANGE */
    frame = untouched;
    CHECK(pel_tlm_parse_ffmpeg_quality_stats(buf, 16u, PEL_TLM_CODEC_H264, PEL_QP_SCALE_SLICE_QP,
                                             8u, plane_samples, &frame) == PEL_ERR_RANGE);
    CHECK(bytes_equal(&frame, &untouched, sizeof(frame)));
    return failures;
}

static int check_ffmpeg_qstats_codec_caps(uint8_t codec, uint64_t exp)
{
    uint64_t caps = 0;
    int failures = 0;

    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS, codec, &caps) == PEL_OK);
    CHECK(caps == exp);
    return failures;
}

static int test_ffmpeg_qstats_caps(void)
{
    uint64_t caps = 0;
    const uint64_t exp_caps =
        (PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM | PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_PSNR_Y |
         PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V | PEL_TLM_F_CODED_BIT_DEPTH);
    const uint64_t exp_vp9_caps = (PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_PSNR_Y | PEL_TLM_F_PSNR_U |
                                   PEL_TLM_F_PSNR_V | PEL_TLM_F_CODED_BIT_DEPTH);
    int failures = 0;

    failures += check_ffmpeg_qstats_codec_caps(PEL_TLM_CODEC_H264, exp_caps);
    failures += check_ffmpeg_qstats_codec_caps(PEL_TLM_CODEC_HEVC, exp_caps);
    failures += check_ffmpeg_qstats_codec_caps(PEL_TLM_CODEC_AV1, exp_caps);
    failures += check_ffmpeg_qstats_codec_caps(PEL_TLM_CODEC_VP9, exp_vp9_caps);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS, PEL_TLM_CODEC_VP9, &caps) ==
          PEL_OK);
    CHECK((caps & (PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM)) == 0u);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS, 0u, &caps) == PEL_ERR_INVALID);
    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS, 6u, &caps) == PEL_ERR_INVALID);
    return failures;
}

static int test_ffmpeg_quality_stats(void)
{
    int failures = 0;

    failures += test_ffmpeg_qstats_positive_x264();
    failures += test_ffmpeg_qstats_positive_x265();
    failures += test_ffmpeg_qstats_positive_aom();
    failures += test_ffmpeg_qstats_positive_vp9();
    failures += test_ffmpeg_qstats_positive_av1_encoder_qp();
    failures += test_ffmpeg_qstats_plane_selection_y_only();
    failures += test_ffmpeg_qstats_plane_selection_count1();
    failures += test_ffmpeg_qstats_depth10_and_sse0();
    failures += test_ffmpeg_qstats_tiny_sse_and_depth12();
    failures += test_ffmpeg_qstats_boundaries();
    failures += test_ffmpeg_qstats_tolerant_bytes();
    failures += test_ffmpeg_qstats_negative_args();
    failures += test_ffmpeg_qstats_negative_lens();
    failures += test_ffmpeg_qstats_negative_codecs_and_scales();
    failures += test_ffmpeg_qstats_negative_quality_and_sse();
    failures += test_ffmpeg_qstats_pict_types();
    failures += test_ffmpeg_qstats_caps();
    return failures;
}

static PelTlmLibaomFrame libaom_frame(void)
{
    const PelTlmLibaomFrame in = {0x1u, 123u, 32, 1u, {40.0, 41.0, 42.0, 43.0}, 7u, 3u};
    return in;
}

static int expect_libaom_error(const char *label, const PelTlmLibaomFrame *in, uint8_t depth,
                               int null_out, pel_result want)
{
    PelorusEncTelemetryInput out = {0};
    unsigned char before[sizeof(out)];
    int failures = 0;
    pel_result got;

    (void)memset(&out, 0xa5, sizeof(out));
    (void)memcpy(before, &out, sizeof(out));
    got = pel_tlm_from_libaom(in, depth, null_out ? NULL : &out);
    if (got != want) {
        (void)fprintf(stderr, "libaom %s: got %d, want %d\n", label, got, want);
    }
    CHECK(got == want);
    CHECK(bytes_equal(&out, before, sizeof(out)));
    return failures;
}

static int test_libaom_negative(void)
{
    PelTlmLibaomFrame in = libaom_frame();
    int failures = 0;

    in.frame_flags = 0x100u;
    failures += expect_libaom_error("unknown flag", &in, 8u, 0, PEL_ERR_INVALID);
    in.frame_flags |= 0x1f0011u;
    failures +=
        expect_libaom_error("unknown public flag with private bits", &in, 8u, 0, PEL_ERR_INVALID);
    in = libaom_frame();
    in.quantizer_64 = 64;
    failures += expect_libaom_error("quantizer 64", &in, 8u, 0, PEL_ERR_RANGE);
    in.quantizer_64 = -2;
    failures += expect_libaom_error("quantizer -2", &in, 8u, 0, PEL_ERR_RANGE);
    in = libaom_frame();
    in.frame_bytes = (uint64_t)UINT32_MAX + 1u;
    failures += expect_libaom_error("bytes overflow", &in, 8u, 0, PEL_ERR_RANGE);
    in = libaom_frame();
    in.has_psnr = 2u;
    failures += expect_libaom_error("has_psnr 2", &in, 8u, 0, PEL_ERR_INVALID);
    in = libaom_frame();
    for (unsigned i = 0u; i < 4u; i++) {
        in.psnr[i] = NAN;
        failures += expect_libaom_error("NaN PSNR", &in, 8u, 0, PEL_ERR_RANGE);
        in.psnr[i] = -1.0;
        failures += expect_libaom_error("negative PSNR", &in, 8u, 0, PEL_ERR_RANGE);
        in.psnr[i] = -INFINITY;
        failures += expect_libaom_error("-inf PSNR", &in, 8u, 0, PEL_ERR_RANGE);
        in = libaom_frame();
    }
    failures += expect_libaom_error("NULL input", NULL, 8u, 0, PEL_ERR_INVALID);
    failures += expect_libaom_error("NULL output", &in, 8u, 1, PEL_ERR_INVALID);
    failures += expect_libaom_error("bad depth", &in, 9u, 0, PEL_ERR_RANGE);
    in.quantizer_64 = -1;
    in.has_psnr = 0u;
    failures += expect_libaom_error("bad depth without QP", &in, 0u, 0, PEL_ERR_RANGE);
    return failures;
}

static int test_libaom_boundaries(void)
{
    PelTlmLibaomFrame in = libaom_frame();
    PelorusEncTelemetryInput out = {0};
    const uint8_t depths[] = {8u, 10u, 12u};
    const uint64_t qp_bits = PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM;
    int failures = 0;

    for (unsigned i = 0u; i < 3u; i++) {
        float norm = 0.0f;
        for (unsigned j = 0u; j < 2u; j++) {
            in.quantizer_64 = j == 0u ? 0 : 63;
            CHECK(pel_tlm_from_libaom(&in, depths[i], &out) == PEL_OK);
            CHECK(out.frame.avg_qp == (float)in.quantizer_64);
            CHECK((out.frame.present_mask & qp_bits) == qp_bits);
            CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, depths[i], (float)in.quantizer_64,
                                   &norm) == PEL_OK);
            CHECK(fabsf(out.frame.avg_qp_norm - norm) < 0.0001f);
        }
    }
    in.quantizer_64 = -1;
    in.psnr[0] = in.psnr[1] = INFINITY;
    in.psnr[2] = 101.0;
    in.psnr[3] = 100.0;
    in.frame_bytes = UINT32_MAX;
    CHECK(pel_tlm_from_libaom(&in, 8u, &out) == PEL_OK);
    CHECK((out.frame.present_mask & qp_bits) == 0u && out.frame.qp_scale == 0u);
    CHECK(out.frame.avg_qp == 0.0f && out.frame.avg_qp_norm == 0.0f);
    CHECK(out.frame.frame_bytes == UINT32_MAX);
    CHECK(out.frame.psnr_y == 100.0f && out.frame.psnr_u == 100.0f && out.frame.psnr_v == 100.0f);
    in.has_psnr = 0u;
    in.psnr[1] = NAN; /* ignored when absent */
    in.frame_flags = 0u;
    in.frame_bytes = 0u;
    in.display_index = in.decode_index = UINT32_MAX;
    CHECK(pel_tlm_from_libaom(&in, 8u, &out) == PEL_OK);
    CHECK(out.frame.frame_bytes == 0u && out.frame.metric_source == 0u);
    CHECK(out.frame.display_index == UINT32_MAX && out.frame.decode_index == UINT32_MAX);
    CHECK((out.frame.present_mask & (PEL_TLM_F_PSNR_Y | PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V)) ==
          0u);
    CHECK(out.frame.psnr_y == 0.0f && out.frame.psnr_u == 0.0f && out.frame.psnr_v == 0.0f);
    return failures;
}

static int test_libaom_flags_caps(void)
{
    const uint32_t flags[] = {0u,    1u,    2u,        0x10u,       0x20u,      0x40u,
                              0x80u, 0xf3u, 0x1f0011u, 0xffff0000u, 0xffff0011u};
    const uint64_t expected =
        PEL_TLM_F_DISPLAY_INDEX | PEL_TLM_F_DECODE_INDEX | PEL_TLM_F_FRAME_BYTES |
        PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM | PEL_TLM_F_PSNR_Y | PEL_TLM_F_PSNR_U |
        PEL_TLM_F_PSNR_V | PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_KEY_FRAME | PEL_TLM_F_CODED_BIT_DEPTH;
    PelTlmLibaomFrame in = libaom_frame();
    PelorusEncTelemetryInput out = {0};
    uint64_t caps = 0u;
    int failures = 0;

    CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_LIBAOM_STATS, PEL_TLM_CODEC_AV1, &caps) == PEL_OK);
    CHECK(caps == expected);
    for (unsigned i = 0u; i < sizeof(flags) / sizeof(flags[0]); i++) {
        in.frame_flags = flags[i];
        CHECK(pel_tlm_from_libaom(&in, 8u, &out) == PEL_OK);
        CHECK(out.frame.picture_type == ((flags[i] & 0x11u) ? PEL_PICTURE_I : PEL_PICTURE_P));
        CHECK(out.frame.frame_flags == ((flags[i] & 1u) ? PEL_TLM_FRAME_KEY : 0u));
        CHECK(out.frame.present_mask == expected);
        CHECK(out.frame.adapter == PEL_TLM_ADAPTER_LIBAOM_STATS);
        CHECK(out.frame.codec == PEL_TLM_CODEC_AV1 && out.frame.granularity == PEL_TLM_GRAN_FRAME);
        CHECK(out.frame.display_index == 7u && out.frame.decode_index == 3u);
        CHECK(out.frame.psnr_y == 41.0f && out.frame.psnr_u == 42.0f && out.frame.psnr_v == 43.0f);
        CHECK(out.frame.metric_source == PEL_TLM_METRIC_ENCODER);
        CHECK(out.qp_map == NULL && out.bits_map == NULL && out.mode_map == NULL);
        CHECK(pel_enc_telemetry_validate(&out) == PEL_OK);
    }
    for (uint8_t codec = PEL_TLM_CODEC_H264; codec <= PEL_TLM_CODEC_VP9; codec++) {
        if (codec != PEL_TLM_CODEC_AV1) {
            CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_LIBAOM_STATS, codec, &caps) ==
                  PEL_ERR_UNSUPPORTED);
            CHECK(caps == 0u);
        }
    }
    return failures;
}

#define X264_ROW                                                                                   \
    "in:0 out:0 type:I dur:2 cpbdur:2 q:32.00 aq:29.50 tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:;\n"

static int expect_x264_error(const char *text, size_t len, uint8_t depth, pel_result want)
{
    PelorusEncTelemetryInput frames[2];
    unsigned char before[sizeof(frames)];
    size_t count = 99u;
    int failures = 0;

    (void)memset(frames, 0xa5, sizeof(frames));
    (void)memcpy(before, frames, sizeof(frames));
    CHECK(pel_tlm_parse_x264_stats(text, len, depth, frames, 2u, &count) == want);
    CHECK(count == 0u);
    CHECK(bytes_equal(frames, before, sizeof(frames)));
    return failures;
}

static int check_x264_stream_frame(const PelorusEncTelemetryInput *frame, size_t i)
{
    int failures = 0;

    CHECK(frame->frame.codec == PEL_TLM_CODEC_H264);
    CHECK(frame->frame.qp_scale == PEL_QP_SCALE_SLICE_QP);
    CHECK(frame->frame.avg_qp_norm == frame->frame.avg_qp);
    CHECK(frame->frame.decode_index == (uint32_t)i);
    CHECK(pel_enc_telemetry_validate(frame) == PEL_OK);
    CHECK((frame->frame.present_mask &
           (PEL_TLM_F_PSNR_Y | PEL_TLM_F_SSIM_Y | PEL_TLM_F_QP_MAP | PEL_TLM_F_SCENE_CUT)) == 0u);
    CHECK(frame->frame.psnr_y == 0.0f && frame->qp_map == NULL);
    return failures;
}

static int test_x264_fixture(const char *path)
{
    char text[FIXTURE_CAP];
    PelorusEncTelemetryInput frames[FRAME_CAP];
    size_t len = 0;
    size_t count = 0;
    size_t i;
    int failures = 0;

    (void)memset(frames, 0, sizeof(frames));
    CHECK(read_fixture(path, text, sizeof(text), &len) == 0);
    CHECK(pel_tlm_parse_x264_stats(text, len, 8u, frames, FRAME_CAP, &count) == PEL_OK);
    CHECK(count == 30u);
    CHECK(frames[0].frame.frame_bytes == 4376u);
    CHECK(frames[0].frame.avg_qp == 29.0f);
    CHECK(frames[1].frame.display_index == 3u && frames[1].frame.decode_index == 1u);
    CHECK(frames[3].frame.picture_type == PEL_PICTURE_B);
    CHECK((frames[3].frame.frame_flags & PEL_TLM_FRAME_REFERENCE) == 0u);
    for (i = 0u; i < count && i < FRAME_CAP; i++) {
        failures += check_x264_stream_frame(&frames[i], i);
    }
    return failures;
}

/* Replay libaom-3.15.2-cq32.txt unchanged, including encoder-private flags. */
static int test_libaom_fixture(const char *path)
{
    FILE *fp = fopen(path, "rb");
    int failures = 0;

    CHECK(fp != NULL);
    if (fp == NULL) {
        return failures;
    }
    for (unsigned i = 0u; i < RECORDED_FRAME_COUNT; i++) {
        PelTlmLibaomFrame in = {0};
        PelorusEncTelemetryInput out = {0};
        char line[256];
        uint64_t caps = 0u;
        float norm = 0.0f;
        if (fgets(line, sizeof(line), fp) == NULL) {
            CHECK(0);
            break;
        }
        const int fields = sscanf(
            line, "%" SCNu32 " %" SCNu32 " %" SCNu32 " %" SCNu64 " %" SCNd32 " %lf %lf %lf %lf",
            &in.display_index, &in.decode_index, &in.frame_flags, &in.frame_bytes, &in.quantizer_64,
            &in.psnr[0], &in.psnr[1], &in.psnr[2], &in.psnr[3]);
        CHECK(fields == 9);
        if (fields != 9) {
            break;
        }
        in.has_psnr = 1u;
        const pel_result rc = pel_tlm_from_libaom(&in, 8u, &out);
        if (rc != PEL_OK) {
            (void)fprintf(stderr,
                          "libaom fixture frame %u: flags 0x%08" PRIx32 ": got %d, want PEL_OK\n",
                          i, in.frame_flags, rc);
        }
        CHECK(rc == PEL_OK);
        if (rc != PEL_OK) {
            break;
        }
        CHECK(in.display_index == i && in.decode_index == i);
        CHECK(out.frame.display_index == i && out.frame.decode_index == i);
        CHECK(out.frame.frame_bytes == in.frame_bytes && in.frame_bytes > 0u);
        CHECK(in.quantizer_64 == 32 && out.frame.avg_qp == 32.0f);
        CHECK(pel_qp_normalize(PEL_QP_SCALE_AV1_ENCODER_QP, 8u, (float)in.quantizer_64, &norm) ==
              PEL_OK);
        CHECK(fabsf(out.frame.avg_qp_norm - norm) < 0.0001f);
        CHECK(out.frame.psnr_y == (float)in.psnr[1]);
        CHECK(out.frame.psnr_u == (float)in.psnr[2]);
        CHECK(out.frame.psnr_v == (float)in.psnr[3]);
        CHECK(out.frame.picture_type == (i == 0u ? PEL_PICTURE_I : PEL_PICTURE_P));
        CHECK(out.frame.frame_flags == (i == 0u ? PEL_TLM_FRAME_KEY : 0u));
        CHECK(pel_tlm_adapter_caps(PEL_TLM_ADAPTER_LIBAOM_STATS, PEL_TLM_CODEC_AV1, &caps) ==
              PEL_OK);
        CHECK(out.frame.present_mask == caps);
        CHECK(pel_enc_telemetry_validate(&out) == PEL_OK);
    }
    CHECK(fgetc(fp) == EOF && !ferror(fp));
    CHECK(fclose(fp) == 0);
    return failures;
}

static int check_x264_type(const PelorusEncTelemetryInput *frame, char type, uint8_t picture)
{
    int failures = 0;

    CHECK(frame->frame.picture_type == picture);
    CHECK(frame->frame.avg_qp == 29.5f && frame->frame.avg_qp_norm == 29.5f);
    CHECK(frame->frame.frame_bytes == 120u);
    CHECK(frame->frame.header_bits == 160u && frame->frame.residual_bits == 800u);
    CHECK(((frame->frame.frame_flags & PEL_TLM_FRAME_KEY) != 0u) == (type == 'I'));
    CHECK(((frame->frame.frame_flags & PEL_TLM_FRAME_REFERENCE) != 0u) == (type != 'b'));
    CHECK((frame->frame.frame_flags & PEL_TLM_FRAME_SHOWN) != 0u);
    CHECK(frame->frame.metric_source == 0u);
    return failures;
}

static int test_x264_types_and_qp(void)
{
    static const char types[] = "IiPBb";
    static const uint8_t pictures[] = {PEL_PICTURE_I, PEL_PICTURE_I, PEL_PICTURE_P, PEL_PICTURE_B,
                                       PEL_PICTURE_B};
    char text[] = X264_ROW;
    PelorusEncTelemetryInput frame;
    size_t count = 0;
    size_t i;
    int failures = 0;

    for (i = 0u; i < sizeof(types) - 1u; i++) {
        text[16] = types[i];
        CHECK(pel_tlm_parse_x264_stats(text, sizeof(text) - 1u, 8u, &frame, 1u, &count) == PEL_OK);
        CHECK(count == 1u);
        failures += check_x264_type(&frame, types[i], pictures[i]);
    }
    CHECK(pel_tlm_parse_x264_stats(text, sizeof(text) - 1u, 10u, &frame, 1u, &count) == PEL_OK);
    CHECK(frame.frame.avg_qp == 17.5f);
    CHECK(pel_tlm_parse_x264_stats(text, sizeof(text) - 1u, 12u, &frame, 1u, &count) == PEL_OK);
    CHECK(frame.frame.avg_qp == 5.5f);
    return failures;
}

static int test_x264_stream_control(void)
{
    static const char duplicate[] = "#options: test\n" X264_ROW X264_ROW;
    PelorusEncTelemetryInput frames[2];
    size_t count = 99u;
    int failures = 0;

    CHECK(pel_tlm_parse_x264_stats("", 0u, 8u, frames, 2u, &count) == PEL_OK);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_x264_stats("#options: test\n", 15u, 8u, frames, 2u, &count) == PEL_OK);
    CHECK(count == 0u);
    /* VVenC has no mandatory header or duplicate-index check; preserve that contract. */
    CHECK(pel_tlm_parse_x264_stats(X264_ROW, sizeof(X264_ROW) - 1u, 8u, frames, 2u, &count) ==
          PEL_OK);
    CHECK(count == 1u);
    CHECK(pel_tlm_parse_x264_stats(duplicate, sizeof(duplicate) - 1u, 8u, frames, 2u, &count) ==
          PEL_OK);
    CHECK(count == 2u);
    CHECK(frames[0].frame.display_index == frames[1].frame.display_index);
    return failures;
}

static int test_x264_corruptions(void)
{
    static const struct {
        const char *text;
        pel_result want;
    } cases[] = {
        {"in:0 out:0 type:I dur:2", PEL_ERR_TRUNCATED},
        {"out:0 in:0 type:I dur:2", PEL_ERR_INVALID},
        {"in:0 out:0 type:X dur:2", PEL_ERR_INVALID},
        {"in:0 out:0 type:p dur:2", PEL_ERR_INVALID},
        {"in:0 out:0 type:I dur:2 cpbdur:2 aq:32 q:32", PEL_ERR_INVALID},
        {"in:4294967296 out:0 type:I", PEL_ERR_RANGE},
        {"in:-1 out:0 type:I", PEL_ERR_INVALID},
        {"in:0 out:0 type:I dur:2 cpbdur:2 q:64 aq:32", PEL_ERR_RANGE},
        {"in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:nan", PEL_ERR_INVALID},
        {X264_ROW "in:1 out:1 type:X", PEL_ERR_INVALID},
        {"garbage", PEL_ERR_INVALID},
    };
    size_t i;
    int failures = 0;

    for (i = 0u; i < sizeof(cases) / sizeof(cases[0]); i++) {
        failures += expect_x264_error(cases[i].text, strlen(cases[i].text), 8u, cases[i].want);
    }
    failures += expect_x264_error(X264_ROW, sizeof(X264_ROW) - 3u, 8u, PEL_ERR_TRUNCATED);
    failures += expect_x264_error(X264_ROW, sizeof(X264_ROW) - 1u, 9u, PEL_ERR_RANGE);
    return failures;
}

static int test_x264_tail_errors(void)
{
    static const char prefix[] = "in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:31 ";
    static const char *const tails[] = {
        "tex:801 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:;",
        "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:z ref:;",
        "tex:800 mv:80 misc:80 imb:1 smb:0 pmb:0 d:- ref:;",
        "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:no;",
        "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:; junk",
        "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:0 w:0,1;",
        "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:0 w:0,1,2,3;",
        "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:0 w:0,1,nan;",
    };
    char text[256];
    size_t i;
    int failures = 0;

    for (i = 0u; i < sizeof(tails) / sizeof(tails[0]); i++) {
        const int written = snprintf(text, sizeof(text), "%s%s", prefix, tails[i]);

        CHECK(written > 0 && (size_t)written < sizeof(text));
        failures += expect_x264_error(text, strlen(text), 8u, PEL_ERR_INVALID);
    }
    return failures;
}

static int test_x264_numeric_boundaries(void)
{
    static const char max_row[] =
        "in:4294967295 out:4294967295 type:i "
        "dur:9223372036854775807 cpbdur:4294967296 q:51 aq:51 "
        "tex:4294967288 mv:0 misc:0 imb:4294967295 pmb:0 smb:0 d:t ref:0 4294967295;";
    static const char overflow_bits[] = "in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:31 "
                                        "tex:800 mv:4294967295 misc:1 imb:0 pmb:0 smb:0 d:s ref:;";
    PelorusEncTelemetryInput frame = {0};
    size_t count = 0;
    int failures = 0;

    CHECK(pel_tlm_parse_x264_stats(max_row, sizeof(max_row) - 1u, 8u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 1u && frame.frame.display_index == UINT32_MAX);
    CHECK(frame.frame.frame_bytes == 536870911u && frame.frame.avg_qp == 51.0f);
    failures += expect_x264_error(overflow_bits, sizeof(overflow_bits) - 1u, 8u, PEL_ERR_RANGE);
    failures += expect_x264_error("in:0 out:0 type:I dur:9223372036854775808",
                                  sizeof("in:0 out:0 type:I dur:9223372036854775808") - 1u, 8u,
                                  PEL_ERR_RANGE);
    failures += expect_x264_error("in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:51.01",
                                  sizeof("in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:51.01") - 1u, 8u,
                                  PEL_ERR_RANGE);
    failures += expect_x264_error("in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:-1",
                                  sizeof("in:0 out:0 type:I dur:2 cpbdur:2 q:32 aq:-1") - 1u, 8u,
                                  PEL_ERR_RANGE);
    return failures;
}

static int test_x264_weighted_refs(void)
{
    static const char prefix[] = "in:1 out:1 type:P dur:2 cpbdur:2 q:32 aq:31 "
                                 "tex:800 mv:80 misc:80 imb:1 pmb:0 smb:0 d:- ref:";
    static const char *const tails[] = {"0 w:0,1,-2 ;", "1 2 w:0,1,-2,0,1,-3,1,4 ;"};
    char text[256];
    PelorusEncTelemetryInput frame = {0};
    size_t count = 0;
    size_t i;
    int failures = 0;

    for (i = 0u; i < sizeof(tails) / sizeof(tails[0]); i++) {
        const int written = snprintf(text, sizeof(text), "%s%s", prefix, tails[i]);

        CHECK(written > 0 && (size_t)written < sizeof(text));
        CHECK(pel_tlm_parse_x264_stats(text, strlen(text), 8u, &frame, 1u, &count) == PEL_OK);
        CHECK(count == 1u && frame.frame.avg_qp == 31.0f);
    }
    return failures;
}

static int test_x264_bounds_and_args(void)
{
    char line[TEST_TEXT_LINE_OVER_MAX + 1u];
    PelorusEncTelemetryInput frame;
    unsigned char before[sizeof(frame)];
    size_t count = 99u;
    int failures = 0;

    (void)memset(&frame, 0xa5, sizeof(frame));
    (void)memcpy(before, &frame, sizeof(frame));
    CHECK(pel_tlm_parse_x264_stats(X264_ROW X264_ROW, sizeof(X264_ROW X264_ROW) - 1u, 8u, &frame,
                                   1u, &count) == PEL_ERR_RANGE);
    CHECK(count == 2u && bytes_equal(&frame, before, sizeof(frame)));
    CHECK(pel_tlm_parse_x264_stats(NULL, 0u, 8u, &frame, 1u, &count) == PEL_ERR_INVALID);
    CHECK(count == 0u);
    CHECK(pel_tlm_parse_x264_stats("", 0u, 8u, NULL, 1u, &count) == PEL_ERR_INVALID);
    CHECK(pel_tlm_parse_x264_stats("", 0u, 8u, &frame, 0u, &count) == PEL_ERR_INVALID);
    CHECK(pel_tlm_parse_x264_stats("", 0u, 8u, &frame, 1u, NULL) == PEL_ERR_INVALID);
    failures += expect_x264_error("", TEST_TEXT_TOTAL_MAX + 1u, 8u, PEL_ERR_RANGE);
    (void)memset(line, ' ', sizeof(line));
    failures += expect_x264_error(line, TEST_TEXT_LINE_OVER_MAX, 8u, PEL_ERR_RANGE);
    CHECK(pel_tlm_parse_x264_stats(line, TEST_TEXT_LINE_OVER_MAX - 1u, 8u, &frame, 1u, &count) ==
          PEL_OK);
    CHECK(count == 0u);
    return failures;
}

static int test_x264(const char *path)
{
    int failures = 0;

    failures += test_x264_fixture(path);
    failures += test_x264_types_and_qp();
    failures += test_x264_stream_control();
    failures += test_x264_corruptions();
    failures += test_x264_bounds_and_args();
    failures += test_x264_weighted_refs();
    failures += test_x264_tail_errors();
    failures += test_x264_numeric_boundaries();
    return failures;
}

int main(int argc, char **argv)
{
    PelorusEncTelemetryInput x265;
    PelorusEncTelemetryInput svt;
    PelorusEncTelemetryInput vvenc;
    int failures = 0;

    if (argc == 2 && strcmp(argv[1], "--locale-only") == 0) {
        return test_non_c_numeric_locale();
    }
    if (argc == 2 && strcmp(argv[1], "--libaom-negative-only") == 0) {
        return test_libaom_negative() == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
    }
    if (argc != 6) {
        (void)fprintf(stderr, "usage: %s X265.csv SVT.stat VVENC.log LIBAOM.txt X264.stats\n",
                      argv[0]);
        return EXIT_FAILURE;
    }
    (void)memset(&x265, 0, sizeof(x265));
    (void)memset(&svt, 0, sizeof(svt));
    (void)memset(&vvenc, 0, sizeof(vvenc));
    failures += test_x265_fixture(argv[1], &x265);
    failures += test_svt_fixture(argv[2], &svt);
    failures += test_vvenc_fixture(argv[3], &vvenc);
    failures += test_matched_qp(&x265, &svt);
    failures += test_qp_normalize_anchors();
    failures += test_negative_rows();
    failures += test_vvenc_negative_rows();
    failures += test_vvenc_positive_variants();
    failures += test_vvenc_boundary_variants();
    failures += test_x265_duplicate_headers();
    failures += test_number_allow_list();
    failures += test_svt_nonfinite_psnr();
    failures += test_x265_terminator_and_scenecut();
    failures += test_depths();
    failures += test_svt_average_qp_domain();
    failures += test_x265_null_arguments();
    failures += test_svt_null_arguments();
    failures += test_vvenc_null_arguments();
    failures += test_line_and_field_bounds();
    failures += test_total_text_bound();
    failures += test_capacity(argv[1]);
    failures += test_svt_capacity(argv[2]);
    failures += test_vvenc_capacity(argv[3]);
    failures += test_ffmpeg_quality_stats();
    failures += test_libaom_negative();
    failures += test_libaom_boundaries();
    failures += test_libaom_flags_caps();
    failures += test_libaom_fixture(argv[4]);
    failures += test_x264(argv[5]);
    if (failures != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    (void)printf("encoder telemetry adapters: all checks passed\n");
    return EXIT_SUCCESS;
}
