/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef _GNU_SOURCE
/* ADR-0174 decision 8: _GNU_SOURCE exposes locale_t and strtod_l on glibc. */
/* NOLINTNEXTLINE(bugprone-reserved-identifier,cert-dcl37-c,cert-dcl51-cpp): ADR-0174; glibc locale_t/strtod_l visibility. */
#define _GNU_SOURCE
#endif

/* In-memory x265 CSV and SVT-AV1 stat-file adapters (issue #111). */

#include "pelorus/telemetry.h"

#include <errno.h>
#include <limits.h>
#include <locale.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define TLM_TEXT_LINE_MAX 4096u
#define TLM_TEXT_TOTAL_MAX 1048576u
#define TLM_CSV_FIELDS_MAX 256u
#define TLM_NUMBER_MAX 64u

/* SVT-AV1 v4.2.0 commit 9292ec8e: md_process.c quantizer_to_qindex. */
static const uint8_t av1_encoder_qindex[64] = {
    0,   4,   8,   12,  16,  20,  24,  28,  32,  36,  40,  44,  48,  52,  56,  60,
    64,  68,  72,  76,  80,  84,  88,  92,  96,  100, 104, 108, 112, 116, 120, 124,
    128, 132, 136, 140, 144, 148, 152, 156, 160, 164, 168, 172, 176, 180, 184, 188,
    192, 196, 200, 204, 208, 212, 216, 220, 224, 228, 232, 236, 240, 244, 249, 255,
};

typedef struct TlmSpan {
    const char *ptr;
    size_t len;
} TlmSpan;

#ifdef _WIN32
typedef _locale_t TlmLocale;
#else
typedef locale_t TlmLocale;
#endif

typedef struct TlmCursor {
    TlmSpan line;
    size_t pos;
    TlmLocale locale;
} TlmCursor;

typedef struct X265Cols {
    size_t fields;
    size_t encode_order;
    size_t type;
    size_t poc;
    size_t qp;
    size_t bits;
    size_t scenecut;
} X265Cols;

typedef struct SvtValues {
    uint32_t picture;
    uint32_t temporal_layer;
    uint32_t qp;
    uint32_t frame_bytes;
    uint32_t avg_qp;
    float psnr[3];
    float mse[3];
    float ssim[3];
    float vbv_delay;
} SvtValues;

typedef struct VvencValues {
    uint32_t poc;
    uint32_t tid;
    uint32_t bits;
    float qp;
    float psnr[3];
    uint8_t picture_type;
    uint8_t frame_flags;
} VvencValues;

static int ascii_space(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == '\v';
}

static TlmSpan span_trim(TlmSpan s)
{
    size_t begin = 0;

    while (begin < s.len && ascii_space(s.ptr[begin])) {
        begin++;
    }
    while (s.len > begin && ascii_space(s.ptr[s.len - 1u])) {
        s.len--;
    }
    s.ptr += begin;
    s.len -= begin;
    return s;
}

static int span_eq(TlmSpan s, const char *literal)
{
    const size_t n = strlen(literal);

    return s.len == n && memcmp(s.ptr, literal, n) == 0;
}

static int span_starts(TlmSpan s, const char *literal)
{
    const size_t n = strlen(literal);

    return s.len >= n && memcmp(s.ptr, literal, n) == 0;
}

static int span_contains(TlmSpan s, const char *literal)
{
    const size_t n = strlen(literal);
    size_t i;

    if (s.len < n) {
        return 0;
    }
    for (i = 0; i <= s.len - n; i++) {
        if (memcmp(s.ptr + i, literal, n) == 0) {
            return 1;
        }
    }
    return 0;
}

/* One bounded line from a pointer+length buffer; no NUL terminator is assumed. */
static pel_result next_line(const char *text, size_t len, size_t *offset, TlmSpan *out, int *have)
{
    size_t end;

    *have = 0;
    if (*offset >= len) {
        return PEL_OK;
    }
    end = *offset;
    while (end < len && text[end] != '\n' && end - *offset <= TLM_TEXT_LINE_MAX) {
        end++;
    }
    if (end - *offset > TLM_TEXT_LINE_MAX) {
        return PEL_ERR_RANGE;
    }
    out->ptr = text + *offset;
    out->len = end - *offset;
    *offset = end < len ? end + 1u : end;
    *out = span_trim(*out);
    *have = 1;
    return PEL_OK;
}

static pel_result span_number_copy(TlmSpan s, char *buf, size_t *out_len)
{
    s = span_trim(s);
    if (s.len == 0u || s.len >= TLM_NUMBER_MAX) {
        return PEL_ERR_INVALID;
    }
    (void)memcpy(buf, s.ptr, s.len);
    buf[s.len] = '\0';
    *out_len = s.len;
    return PEL_OK;
}

static int span_u32_chars(const char *buf, size_t len)
{
    size_t i;

    for (i = 0u; i < len; i++) {
        if (buf[i] < '0' || buf[i] > '9') {
            return 0;
        }
    }
    return 1;
}

static int span_float_chars(const char *buf, size_t len)
{
    size_t i;

    for (i = 0u; i < len; i++) {
        const char c = buf[i];
        const int exponent_minus = i > 0u && (buf[i - 1u] == 'e' || buf[i - 1u] == 'E');

        if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E') {
            continue;
        }
        if (c != '-' || (i != 0u && !exponent_minus)) {
            return 0;
        }
    }
    return 1;
}

static pel_result span_u32(TlmSpan s, uint32_t *out)
{
    char buf[TLM_NUMBER_MAX];
    char *end = NULL;
    unsigned long long value;
    size_t len = 0;
    pel_result rc = span_number_copy(s, buf, &len);

    if (rc != PEL_OK || !span_u32_chars(buf, len)) {
        return PEL_ERR_INVALID;
    }
    errno = 0;
    value = strtoull(buf, &end, 10);
    if (end == buf || end != buf + len) {
        return PEL_ERR_INVALID;
    }
    if (errno == ERANGE || value > UINT32_MAX) {
        return PEL_ERR_RANGE;
    }
    *out = (uint32_t)value;
    return PEL_OK;
}

#ifdef _WIN32
static TlmLocale tlm_locale_create(void)
{
    return _create_locale(LC_NUMERIC, "C");
}

static void tlm_locale_free(TlmLocale locale)
{
    _free_locale(locale);
}

static double tlm_strtod(const char *text, char **end, TlmLocale locale)
{
    return _strtod_l(text, end, locale);
}
#else
static TlmLocale tlm_locale_create(void)
{
    return newlocale(LC_NUMERIC_MASK, "C", (locale_t)0);
}

static void tlm_locale_free(TlmLocale locale)
{
    freelocale(locale);
}

static double tlm_strtod(const char *text, char **end, TlmLocale locale)
{
    return strtod_l(text, end, locale);
}
#endif

static pel_result span_float(TlmSpan s, float *out, TlmLocale locale)
{
    char buf[TLM_NUMBER_MAX];
    char *end = NULL;
    double value;
    size_t len = 0;
    pel_result rc = span_number_copy(s, buf, &len);

    if (rc != PEL_OK || !span_float_chars(buf, len)) {
        return rc == PEL_OK ? PEL_ERR_INVALID : rc;
    }
    errno = 0;
    value = tlm_strtod(buf, &end, locale);
    if (end == buf || end != buf + len) {
        return PEL_ERR_INVALID;
    }
    if (errno == ERANGE || !isfinite(value)) {
        return PEL_ERR_RANGE;
    }
    *out = (float)value;
    return isfinite(*out) ? PEL_OK : PEL_ERR_RANGE;
}

static pel_result csv_fields(TlmSpan line, TlmSpan *fields, size_t *out_count)
{
    size_t start = 0;
    size_t count = 0;
    size_t i;

    for (i = 0; i <= line.len; i++) {
        if (i == line.len || line.ptr[i] == ',') {
            if (count >= TLM_CSV_FIELDS_MAX) {
                return PEL_ERR_RANGE;
            }
            fields[count].ptr = line.ptr + start;
            fields[count].len = i - start;
            fields[count] = span_trim(fields[count]);
            count++;
            start = i + 1u;
        }
    }
    *out_count = count;
    return PEL_OK;
}

static pel_result x265_set_col(size_t *slot, size_t value)
{
    if (*slot != SIZE_MAX) {
        return PEL_ERR_INVALID;
    }
    *slot = value;
    return PEL_OK;
}

static pel_result x265_header(TlmSpan line, X265Cols *cols)
{
    TlmSpan field[TLM_CSV_FIELDS_MAX];
    size_t count = 0;
    size_t i;
    pel_result rc = csv_fields(line, field, &count);

    if (rc != PEL_OK) {
        return rc;
    }
    (void)memset(cols, 0xff, sizeof(*cols));
    cols->fields = count;
    for (i = 0; rc == PEL_OK && i < count; i++) {
        if (span_eq(field[i], "Encode Order")) {
            rc = x265_set_col(&cols->encode_order, i);
        } else if (span_eq(field[i], "Type")) {
            rc = x265_set_col(&cols->type, i);
        } else if (span_eq(field[i], "POC")) {
            rc = x265_set_col(&cols->poc, i);
        } else if (span_eq(field[i], "QP")) {
            rc = x265_set_col(&cols->qp, i);
        } else if (span_eq(field[i], "Bits")) {
            rc = x265_set_col(&cols->bits, i);
        } else if (span_eq(field[i], "Scenecut")) {
            rc = x265_set_col(&cols->scenecut, i);
        }
    }
    if (rc != PEL_OK) {
        return rc;
    }
    if (cols->encode_order == SIZE_MAX || cols->type == SIZE_MAX || cols->poc == SIZE_MAX ||
        cols->qp == SIZE_MAX || cols->bits == SIZE_MAX || cols->scenecut == SIZE_MAX) {
        return PEL_ERR_INVALID;
    }
    return PEL_OK;
}

static pel_result x265_type(TlmSpan s, uint8_t *picture, uint8_t *flags)
{
    char type;

    if (s.len != 7u || memcmp(s.ptr + 1u, "-SLICE", 6u) != 0) {
        return PEL_ERR_INVALID;
    }
    type = s.ptr[0];
    if (type != 'I' && type != 'P' && type != 'B' && type != 'b') {
        return PEL_ERR_INVALID;
    }
    *picture = type == 'I' ? PEL_PICTURE_I : (type == 'P' ? PEL_PICTURE_P : PEL_PICTURE_B);
    *flags = PEL_TLM_FRAME_SHOWN;
    if (type == 'I') {
        *flags |= PEL_TLM_FRAME_KEY;
    }
    if (type != 'b') {
        *flags |= PEL_TLM_FRAME_REFERENCE;
    }
    return PEL_OK;
}

static pel_result x265_record(TlmSpan line, const X265Cols *cols, uint8_t depth, TlmLocale locale,
                              PelorusEncTelemetryInput *out)
{
    TlmSpan field[TLM_CSV_FIELDS_MAX];
    uint32_t bits = 0;
    uint32_t cut = 0;
    size_t count = 0;
    uint64_t caps = 0;
    pel_result rc = csv_fields(line, field, &count);

    if (rc != PEL_OK) {
        return rc;
    }
    if (count != cols->fields) {
        return count < cols->fields ? PEL_ERR_TRUNCATED : PEL_ERR_INVALID;
    }
    (void)memset(out, 0, sizeof(*out));
    rc = pel_tlm_adapter_caps(PEL_TLM_ADAPTER_X265_CSV, PEL_TLM_CODEC_HEVC, &caps);
    out->frame.present_mask = caps;
    out->frame.codec = PEL_TLM_CODEC_HEVC;
    out->frame.qp_scale = PEL_QP_SCALE_SLICE_QP;
    out->frame.granularity = PEL_TLM_GRAN_FRAME;
    out->frame.coded_bit_depth = depth;
    out->frame.adapter = PEL_TLM_ADAPTER_X265_CSV;
    if (rc == PEL_OK) {
        rc = span_u32(field[cols->poc], &out->frame.display_index);
    }
    if (rc == PEL_OK) {
        rc = span_u32(field[cols->encode_order], &out->frame.decode_index);
    }
    if (rc == PEL_OK) {
        rc = span_u32(field[cols->bits], &bits);
    }
    /* x265 4.3 e9b88125: frameencoder.cpp assigns access-unit `bytes << 3`,
     * encoder.cpp copies that value to frameStats->bits, and encoder/api.cpp
     * writes frameStats->bits to the CSV `Bits` column. */
    if (rc == PEL_OK && (bits & 7u) != 0u) {
        rc = PEL_ERR_INVALID;
    }
    out->frame.frame_bytes = bits / 8u;
    if (rc == PEL_OK) {
        rc = span_float(field[cols->qp], &out->frame.avg_qp, locale);
    }
    if (rc == PEL_OK) {
        rc = span_u32(field[cols->scenecut], &cut);
    }
    if (rc == PEL_OK && cut > 1u) {
        rc = PEL_ERR_RANGE;
    }
    if (rc == PEL_OK) {
        rc = x265_type(field[cols->type], &out->frame.picture_type, &out->frame.frame_flags);
    }
    out->frame.frame_flags |= cut != 0u ? PEL_TLM_FRAME_SCENE_CUT : 0u;
    if (rc == PEL_OK) {
        rc = pel_qp_normalize(out->frame.qp_scale, depth, out->frame.avg_qp,
                              &out->frame.avg_qp_norm);
    }
    return rc == PEL_OK ? pel_enc_telemetry_validate(out) : rc;
}

static pel_result x265_walk(const char *text, size_t len, uint8_t depth, TlmLocale locale,
                            PelorusEncTelemetryInput *out, size_t *out_count)
{
    X265Cols cols;
    size_t offset = 0;
    size_t count = 0;
    size_t iteration;
    int have_header = 0;
    int have_line = 0;
    pel_result rc = PEL_OK;

    /* len is capped by adapter_args(); one-byte lines give at most len + 1 calls. */
    for (iteration = 0u; rc == PEL_OK && iteration <= len && iteration <= TLM_TEXT_TOTAL_MAX;
         iteration++) {
        TlmSpan line;
        PelorusEncTelemetryInput record;

        rc = next_line(text, len, &offset, &line, &have_line);
        if (rc != PEL_OK || !have_line) {
            break;
        }
        if (line.len == 0u) {
            continue;
        }
        if (!have_header) {
            rc = x265_header(line, &cols);
            have_header = rc == PEL_OK;
        } else if (span_eq(line, "Summary")) {
            break;
        } else {
            rc = x265_record(line, &cols, depth, locale, &record);
            if (rc == PEL_OK && out != NULL) {
                out[count] = record;
            }
            if (rc == PEL_OK) {
                count++;
            }
        }
    }
    *out_count = count;
    if (rc == PEL_OK && !have_header) {
        return PEL_ERR_ABSENT;
    }
    return rc;
}

static void cursor_space(TlmCursor *c)
{
    while (c->pos < c->line.len && ascii_space(c->line.ptr[c->pos])) {
        c->pos++;
    }
}

static pel_result cursor_expect(TlmCursor *c, const char *literal)
{
    const size_t n = strlen(literal);

    cursor_space(c);
    if (c->line.len - c->pos < n) {
        return PEL_ERR_TRUNCATED;
    }
    if (memcmp(c->line.ptr + c->pos, literal, n) != 0) {
        return PEL_ERR_INVALID;
    }
    c->pos += n;
    return PEL_OK;
}

static pel_result cursor_number(TlmCursor *c, TlmSpan *out)
{
    size_t start;

    cursor_space(c);
    start = c->pos;
    while (c->pos < c->line.len && !ascii_space(c->line.ptr[c->pos]) &&
           c->line.ptr[c->pos] != ',' && c->line.ptr[c->pos] != ']' && c->line.ptr[c->pos] != ')') {
        c->pos++;
    }
    out->ptr = c->line.ptr + start;
    out->len = c->pos - start;
    return out->len == 0u ? PEL_ERR_TRUNCATED : PEL_OK;
}

static pel_result cursor_u32(TlmCursor *c, uint32_t *out)
{
    TlmSpan token;
    pel_result rc = cursor_number(c, &token);

    return rc == PEL_OK ? span_u32(token, out) : rc;
}

static pel_result cursor_float(TlmCursor *c, float *out)
{
    TlmSpan token;
    pel_result rc = cursor_number(c, &token);

    return rc == PEL_OK ? span_float(token, out, c->locale) : rc;
}

static pel_result cursor_metric(TlmCursor *c, const char *name, const char *unit, int comma,
                                float *out)
{
    pel_result rc = cursor_expect(c, name);

    if (rc == PEL_OK) {
        rc = cursor_float(c, out);
    }
    if (rc == PEL_OK && unit != NULL) {
        rc = cursor_expect(c, unit);
    }
    if (rc == PEL_OK && comma) {
        rc = cursor_expect(c, ",");
    }
    return rc;
}

static pel_result svt_prefix(TlmCursor *c, SvtValues *v)
{
    pel_result rc = cursor_expect(c, "Picture Number:");

    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->picture);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "TL:");
    }
    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->temporal_layer);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "QP:");
    }
    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->qp);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "Average QP:");
    }
    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->avg_qp);
    }
    return rc == PEL_OK ? cursor_expect(c, "[") : rc;
}

static pel_result svt_quality_metrics(TlmCursor *c, SvtValues *v)
{
    pel_result rc = cursor_metric(c, "PSNR-Y:", "dB", 1, &v->psnr[0]);

    if (rc == PEL_OK) {
        rc = cursor_metric(c, "PSNR-U:", "dB", 1, &v->psnr[1]);
    }
    if (rc == PEL_OK) {
        rc = cursor_metric(c, "PSNR-V:", "dB", 1, &v->psnr[2]);
    }
    if (rc == PEL_OK) {
        rc = cursor_metric(c, "MSE-Y:", NULL, 1, &v->mse[0]);
    }
    if (rc == PEL_OK) {
        rc = cursor_metric(c, "MSE-U:", NULL, 1, &v->mse[1]);
    }
    if (rc == PEL_OK) {
        rc = cursor_metric(c, "MSE-V:", NULL, 1, &v->mse[2]);
    }
    return rc;
}

static pel_result svt_tail(TlmCursor *c, SvtValues *v)
{
    pel_result rc = cursor_metric(c, "SSIM-Y:", NULL, 1, &v->ssim[0]);

    if (rc == PEL_OK) {
        rc = cursor_metric(c, "SSIM-U:", NULL, 1, &v->ssim[1]);
    }
    if (rc == PEL_OK) {
        rc = cursor_metric(c, "SSIM-V:", NULL, 0, &v->ssim[2]);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "]");
    }
    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->frame_bytes);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "bytes");
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "VBV delay:");
    }
    if (rc == PEL_OK) {
        rc = cursor_float(c, &v->vbv_delay);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "s");
    }
    cursor_space(c);
    return rc == PEL_OK && c->pos != c->line.len ? PEL_ERR_INVALID : rc;
}

static int svt_values_ok(const SvtValues *v)
{
    unsigned i;

    if (v->temporal_layer > 5u || v->qp > 63u || v->avg_qp > 64u || v->vbv_delay < 0.0f) {
        return 0;
    }
    for (i = 0; i < 3u; i++) {
        if (v->psnr[i] < 0.0f || v->mse[i] < 0.0f || v->ssim[i] < 0.0f || v->ssim[i] > 1.0f) {
            return 0;
        }
    }
    return 1;
}

/* SVT reports round(mean qindex / 4). Invert its encoder-QP map so the public
 * value and scale agree; the stat-file rounding limits reconstruction to 0.5 QP. */
static float svt_avg_encoder_qp(uint32_t avg_qp)
{
    const uint32_t qindex = avg_qp < 64u ? avg_qp * 4u : 255u;
    unsigned qp;

    for (qp = 0u; qp < 63u; qp++) {
        const uint32_t next = av1_encoder_qindex[qp + 1u];

        if (qindex <= next) {
            const uint32_t base = av1_encoder_qindex[qp];

            return (float)qp + (float)(qindex - base) / (float)(next - base);
        }
    }
    return 63.0f;
}

static pel_result svt_record(TlmSpan line, uint8_t depth, TlmLocale locale,
                             PelorusEncTelemetryInput *out)
{
    SvtValues v;
    TlmCursor cursor = {line, 0u, locale};
    float avg_encoder_qp;
    uint64_t caps = 0;
    pel_result rc;

    (void)memset(&v, 0, sizeof(v));
    rc = svt_prefix(&cursor, &v);
    if (rc == PEL_OK) {
        rc = svt_quality_metrics(&cursor, &v);
    }
    if (rc == PEL_OK) {
        rc = svt_tail(&cursor, &v);
    }
    if (rc != PEL_OK || !svt_values_ok(&v)) {
        return rc == PEL_OK ? PEL_ERR_RANGE : rc;
    }
    avg_encoder_qp = svt_avg_encoder_qp(v.avg_qp);
    (void)memset(out, 0, sizeof(*out));
    rc = pel_tlm_adapter_caps(PEL_TLM_ADAPTER_SVTAV1_STAT_FILE, PEL_TLM_CODEC_AV1, &caps);
    out->frame.present_mask = caps;
    out->frame.display_index = v.picture;
    out->frame.frame_bytes = v.frame_bytes;
    out->frame.avg_qp = avg_encoder_qp;
    out->frame.psnr_y = v.psnr[0];
    out->frame.psnr_u = v.psnr[1];
    out->frame.psnr_v = v.psnr[2];
    out->frame.ssim_y = v.ssim[0];
    out->frame.codec = PEL_TLM_CODEC_AV1;
    out->frame.qp_scale = PEL_QP_SCALE_AV1_ENCODER_QP;
    out->frame.granularity = PEL_TLM_GRAN_FRAME;
    out->frame.coded_bit_depth = depth;
    out->frame.metric_source = PEL_TLM_METRIC_ENCODER;
    out->frame.adapter = PEL_TLM_ADAPTER_SVTAV1_STAT_FILE;
    if (rc == PEL_OK) {
        rc = pel_qp_normalize(out->frame.qp_scale, depth, avg_encoder_qp, &out->frame.avg_qp_norm);
    }
    return rc == PEL_OK ? pel_enc_telemetry_validate(out) : rc;
}

static pel_result svt_walk(const char *text, size_t len, uint8_t depth, TlmLocale locale,
                           PelorusEncTelemetryInput *out, size_t *out_count)
{
    size_t offset = 0;
    size_t count = 0;
    size_t iteration;
    int recognized = 0;
    int have_line = 0;
    pel_result rc = PEL_OK;

    /* len is capped by adapter_args(); one-byte lines give at most len + 1 calls. */
    for (iteration = 0u; rc == PEL_OK && iteration <= len && iteration <= TLM_TEXT_TOTAL_MAX;
         iteration++) {
        TlmSpan line;
        PelorusEncTelemetryInput record;

        rc = next_line(text, len, &offset, &line, &have_line);
        if (rc != PEL_OK || !have_line) {
            break;
        }
        if (line.len == 0u) {
            continue;
        }
        if (span_starts(line, "SUMMARY")) {
            recognized = 1;
            break;
        }
        if (!span_starts(line, "Picture Number:")) {
            rc = recognized ? PEL_ERR_INVALID : PEL_ERR_ABSENT;
            break;
        }
        recognized = 1;
        rc = svt_record(line, depth, locale, &record);
        if (rc == PEL_OK && out != NULL) {
            out[count] = record;
        }
        if (rc == PEL_OK) {
            count++;
        }
    }
    *out_count = count;
    return rc == PEL_OK && !recognized ? PEL_ERR_ABSENT : rc;
}

static pel_result adapter_args(const char *text, size_t len, uint8_t depth,
                               PelorusEncTelemetryInput *out_frames, size_t cap, size_t *out_count)
{
    if (out_count == NULL) {
        return PEL_ERR_INVALID;
    }
    *out_count = 0u;
    if (text == NULL || out_frames == NULL || cap == 0u) {
        return PEL_ERR_INVALID;
    }
    if (len > TLM_TEXT_TOTAL_MAX) {
        return PEL_ERR_RANGE;
    }
    return depth == 8u || depth == 10u || depth == 12u ? PEL_OK : PEL_ERR_RANGE;
}

pel_result pel_tlm_parse_x265_csv(const char *text, size_t len, uint8_t coded_bit_depth,
                                  PelorusEncTelemetryInput *out_frames, size_t cap,
                                  size_t *out_count)
{
    size_t count = 0;
    TlmLocale locale;
    pel_result rc = adapter_args(text, len, coded_bit_depth, out_frames, cap, out_count);

    if (rc != PEL_OK) {
        return rc;
    }
    locale = tlm_locale_create();
    if (locale == (TlmLocale)0) {
        return PEL_ERR_NOMEM;
    }
    rc = x265_walk(text, len, coded_bit_depth, locale, NULL, &count);
    if (rc == PEL_OK && count > cap) {
        *out_count = count;
        rc = PEL_ERR_RANGE;
    } else if (rc == PEL_OK) {
        rc = x265_walk(text, len, coded_bit_depth, locale, out_frames, out_count);
    }
    tlm_locale_free(locale);
    return rc;
}

pel_result pel_tlm_parse_svtav1_stat(const char *text, size_t len, uint8_t coded_bit_depth,
                                     PelorusEncTelemetryInput *out_frames, size_t cap,
                                     size_t *out_count)
{
    size_t count = 0;
    TlmLocale locale;
    pel_result rc = adapter_args(text, len, coded_bit_depth, out_frames, cap, out_count);

    if (rc != PEL_OK) {
        return rc;
    }
    locale = tlm_locale_create();
    if (locale == (TlmLocale)0) {
        return PEL_ERR_NOMEM;
    }
    rc = svt_walk(text, len, coded_bit_depth, locale, NULL, &count);
    if (rc == PEL_OK && count > cap) {
        *out_count = count;
        rc = PEL_ERR_RANGE;
    } else if (rc == PEL_OK) {
        rc = svt_walk(text, len, coded_bit_depth, locale, out_frames, out_count);
    }
    tlm_locale_free(locale);
    return rc;
}

static pel_result vvenc_prefix(TlmCursor *c, VvencValues *v)
{
    pel_result rc = cursor_expect(c, "POC");

    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->poc);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "TId:");
    }
    if (rc == PEL_OK) {
        rc = cursor_u32(c, &v->tid);
    }
    if (rc == PEL_OK && v->tid > 6u) {
        rc = PEL_ERR_RANGE;
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "(");
    }
    return rc;
}

static pel_result vvenc_slice_type(TlmCursor *c, uint8_t *picture_type)
{
    char slice_ch;

    cursor_space(c);
    if (c->line.len - c->pos < 7u) {
        return PEL_ERR_TRUNCATED;
    }
    slice_ch = c->line.ptr[c->pos];
    if (memcmp(c->line.ptr + c->pos + 1u, "-SLICE", 6u) != 0) {
        return PEL_ERR_INVALID;
    }
    if (slice_ch == 'I' || slice_ch == 'i') {
        *picture_type = PEL_PICTURE_I;
    } else if (slice_ch == 'P' || slice_ch == 'p') {
        *picture_type = PEL_PICTURE_P;
    } else if (slice_ch == 'B' || slice_ch == 'b') {
        *picture_type = PEL_PICTURE_B;
    } else {
        return PEL_ERR_INVALID;
    }
    c->pos += 7u;
    return PEL_OK;
}

static pel_result vvenc_slice_info(TlmCursor *c, VvencValues *v)
{
    size_t start;
    TlmSpan nal;
    int is_key;
    pel_result rc;

    cursor_space(c);
    start = c->pos;
    while (c->pos < c->line.len && c->line.ptr[c->pos] != ',' && c->line.ptr[c->pos] != ')') {
        c->pos++;
    }
    if (c->pos >= c->line.len) {
        return PEL_ERR_TRUNCATED;
    }
    nal.ptr = c->line.ptr + start;
    nal.len = c->pos - start;
    nal = span_trim(nal);
    if (nal.len == 0u) {
        return PEL_ERR_INVALID;
    }
    is_key = span_starts(nal, "IDR") || span_eq(nal, "CRA") || span_eq(nal, "GDR");
    rc = cursor_expect(c, ",");
    if (rc == PEL_OK) {
        rc = vvenc_slice_type(c, &v->picture_type);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    v->frame_flags = PEL_TLM_FRAME_SHOWN | (is_key ? PEL_TLM_FRAME_KEY : 0u);
    rc = cursor_expect(c, ",");
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "QP");
    }
    if (rc == PEL_OK) {
        rc = cursor_float(c, &v->qp);
    }
    while (rc == PEL_OK && c->pos < c->line.len && c->line.ptr[c->pos] != ')') {
        c->pos++;
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, ")");
    }
    return rc;
}

static pel_result vvenc_psnr_metric(TlmCursor *c, const char *name, const char *unit, float *out)
{
    pel_result rc = cursor_expect(c, name);

    if (rc == PEL_OK) {
        TlmSpan token;

        rc = cursor_number(c, &token);
        if (rc == PEL_OK) {
            if (span_eq(token, "inf")) {
                *out = 100.0f;
            } else {
                rc = span_float(token, out, c->locale);
            }
        }
    }
    if (rc == PEL_OK && unit != NULL) {
        rc = cursor_expect(c, unit);
    }
    return rc;
}

static pel_result vvenc_metrics(TlmCursor *c, VvencValues *v)
{
    pel_result rc = cursor_u32(c, &v->bits);

    if (rc == PEL_OK) {
        rc = cursor_expect(c, "bits");
    }
    if (rc == PEL_OK && (v->bits % 8u) != 0u) {
        return PEL_ERR_INVALID;
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "[");
    }
    if (rc == PEL_OK) {
        rc = vvenc_psnr_metric(c, "Y", "dB", &v->psnr[0]);
    }
    if (rc == PEL_OK) {
        rc = vvenc_psnr_metric(c, "U", "dB", &v->psnr[1]);
    }
    if (rc == PEL_OK) {
        rc = vvenc_psnr_metric(c, "V", "dB", &v->psnr[2]);
    }
    if (rc == PEL_OK) {
        rc = cursor_expect(c, "]");
    }
    return rc;
}

static pel_result vvenc_record(TlmSpan line, uint8_t depth, size_t decode_order, TlmLocale locale,
                               PelorusEncTelemetryInput *out)
{
    VvencValues v;
    TlmCursor cursor = {line, 0u, locale};
    uint64_t caps = 0;
    pel_result rc;

    (void)memset(&v, 0, sizeof(v));
    rc = vvenc_prefix(&cursor, &v);
    if (rc == PEL_OK) {
        rc = vvenc_slice_info(&cursor, &v);
    }
    if (rc == PEL_OK) {
        rc = vvenc_metrics(&cursor, &v);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    (void)memset(out, 0, sizeof(*out));
    rc = pel_tlm_adapter_caps(PEL_TLM_ADAPTER_VVENC_LOG, PEL_TLM_CODEC_VVC, &caps);
    out->frame.present_mask = caps;
    out->frame.display_index = v.poc;
    out->frame.decode_index = (uint32_t)decode_order;
    out->frame.frame_bytes = v.bits / 8u;
    out->frame.avg_qp = v.qp;
    out->frame.psnr_y = v.psnr[0];
    out->frame.psnr_u = v.psnr[1];
    out->frame.psnr_v = v.psnr[2];
    out->frame.picture_type = v.picture_type;
    out->frame.frame_flags = v.frame_flags;
    out->frame.codec = PEL_TLM_CODEC_VVC;
    out->frame.qp_scale = PEL_QP_SCALE_SLICE_QP;
    out->frame.granularity = PEL_TLM_GRAN_FRAME;
    out->frame.coded_bit_depth = depth;
    out->frame.metric_source = PEL_TLM_METRIC_ENCODER;
    out->frame.adapter = PEL_TLM_ADAPTER_VVENC_LOG;
    if (rc == PEL_OK) {
        rc = pel_qp_normalize(out->frame.qp_scale, depth, v.qp, &out->frame.avg_qp_norm);
    }
    return rc == PEL_OK ? pel_enc_telemetry_validate(out) : rc;
}

static pel_result vvenc_walk(const char *text, size_t len, uint8_t depth, TlmLocale locale,
                             PelorusEncTelemetryInput *out, size_t *out_count)
{
    size_t offset = 0;
    size_t count = 0;
    size_t iteration;
    int have_line = 0;
    pel_result rc = PEL_OK;

    /* len is capped by adapter_args(); one-byte lines give at most len + 1 calls. */
    for (iteration = 0u; rc == PEL_OK && iteration <= len && iteration <= TLM_TEXT_TOTAL_MAX;
         iteration++) {
        TlmSpan line;
        PelorusEncTelemetryInput record;

        rc = next_line(text, len, &offset, &line, &have_line);
        if (rc != PEL_OK || !have_line) {
            break;
        }
        if (line.len == 0u) {
            continue;
        }
        if (span_starts(line, "POC ") || span_starts(line, "POC\t") || span_eq(line, "POC")) {
            rc = vvenc_record(line, depth, count, locale, &record);
            if (rc == PEL_OK && out != NULL) {
                out[count] = record;
            }
            if (rc == PEL_OK) {
                count++;
            }
        } else if (span_starts(line, "TId:") || span_contains(line, "-SLICE")) {
            rc = PEL_ERR_INVALID;
            break;
        }
    }
    *out_count = count;
    return rc;
}

pel_result pel_tlm_parse_vvenc_log(const char *text, size_t len, uint8_t coded_bit_depth,
                                   PelorusEncTelemetryInput *out_frames, size_t cap,
                                   size_t *out_count)
{
    size_t count = 0;
    TlmLocale locale;
    pel_result rc = adapter_args(text, len, coded_bit_depth, out_frames, cap, out_count);

    if (rc != PEL_OK) {
        return rc;
    }
    locale = tlm_locale_create();
    if (locale == (TlmLocale)0) {
        return PEL_ERR_NOMEM;
    }
    rc = vvenc_walk(text, len, coded_bit_depth, locale, NULL, &count);
    if (rc == PEL_OK && count > cap) {
        *out_count = count;
        rc = PEL_ERR_RANGE;
    } else if (rc == PEL_OK) {
        rc = vvenc_walk(text, len, coded_bit_depth, locale, out_frames, out_count);
    }
    tlm_locale_free(locale);
    return rc;
}

typedef struct X264Values {
    uint32_t display;
    uint32_t decode;
    uint32_t tex;
    uint32_t mv;
    uint32_t misc;
    float aq;
    uint8_t picture_type;
    uint8_t flags;
} X264Values;

static pel_result x264_u32(TlmCursor *c, const char *name, uint32_t *out)
{
    pel_result rc = cursor_expect(c, name);

    return rc == PEL_OK ? cursor_u32(c, out) : rc;
}

static pel_result x264_char(TlmCursor *c, const char *name, char *out)
{
    pel_result rc = cursor_expect(c, name);

    if (rc != PEL_OK) {
        return rc;
    }
    if (c->pos == c->line.len) {
        return PEL_ERR_TRUNCATED;
    }
    *out = c->line.ptr[c->pos++];
    if (c->pos < c->line.len && !ascii_space(c->line.ptr[c->pos])) {
        return PEL_ERR_INVALID;
    }
    return PEL_OK;
}

static pel_result x264_type(TlmCursor *c, X264Values *v)
{
    char type = '\0';
    pel_result rc = x264_char(c, "type:", &type);

    if (rc != PEL_OK) {
        return rc;
    }
    switch (type) {
    case 'I':
    case 'i':
        v->picture_type = PEL_PICTURE_I;
        break;
    case 'P':
        v->picture_type = PEL_PICTURE_P;
        break;
    case 'B':
    case 'b':
        v->picture_type = PEL_PICTURE_B;
        break;
    default:
        return PEL_ERR_INVALID;
    }
    v->flags = PEL_TLM_FRAME_SHOWN | (type == 'I' ? PEL_TLM_FRAME_KEY : 0u) |
               (type != 'b' ? PEL_TLM_FRAME_REFERENCE : 0u);
    return PEL_OK;
}

/* x264 writes nonnegative int64_t timebase durations, not frame indices. */
static pel_result x264_duration(TlmCursor *c, const char *name)
{
    TlmSpan token;
    char buf[TLM_NUMBER_MAX];
    char *end = NULL;
    size_t len = 0;
    unsigned long long value;
    pel_result rc = cursor_expect(c, name);

    if (rc == PEL_OK) {
        rc = cursor_number(c, &token);
    }
    if (rc == PEL_OK) {
        rc = span_number_copy(token, buf, &len);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    if (!span_u32_chars(buf, len)) {
        return PEL_ERR_INVALID;
    }
    errno = 0;
    value = strtoull(buf, &end, 10);
    if (end != buf + len) {
        return PEL_ERR_INVALID;
    }
    return errno == ERANGE || value > INT64_MAX ? PEL_ERR_RANGE : PEL_OK;
}

static pel_result x264_qp(TlmCursor *c, const char *name, uint8_t depth, float *out)
{
    const float offset = 6.0f * (float)(depth - 8u);
    float qp = 0.0f;
    pel_result rc = cursor_expect(c, name);

    if (rc == PEL_OK) {
        rc = cursor_float(c, &qp);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    if (qp < 0.0f || qp > 51.0f + offset) {
        return PEL_ERR_RANGE;
    }
    *out = qp - offset;
    return PEL_OK;
}

static pel_result x264_prefix(TlmCursor *c, uint8_t depth, X264Values *v)
{
    float rc_qp = 0.0f;
    pel_result rc = x264_u32(c, "in:", &v->display);

    if (rc == PEL_OK) {
        rc = x264_u32(c, "out:", &v->decode);
    }
    if (rc == PEL_OK) {
        rc = x264_type(c, v);
    }
    if (rc == PEL_OK) {
        rc = x264_duration(c, "dur:");
    }
    if (rc == PEL_OK) {
        rc = x264_duration(c, "cpbdur:");
    }
    if (rc == PEL_OK) {
        rc = x264_qp(c, "q:", depth, &rc_qp);
    }
    if (rc == PEL_OK) {
        rc = x264_qp(c, "aq:", depth, &v->aq);
    }
    return rc;
}

static pel_result x264_finish(TlmCursor *c)
{
    pel_result rc = cursor_expect(c, ";");

    cursor_space(c);
    return rc == PEL_OK && c->pos != c->line.len ? PEL_ERR_INVALID : rc;
}

static pel_result x264_weight_number(TlmCursor *c)
{
    const size_t start = c->pos;
    TlmSpan token;
    char buf[TLM_NUMBER_MAX];
    char *end = NULL;
    size_t len = 0;
    size_t sign;
    long long value;
    pel_result rc;

    while (c->pos < c->line.len && !ascii_space(c->line.ptr[c->pos]) &&
           c->line.ptr[c->pos] != ',' && c->line.ptr[c->pos] != ';') {
        c->pos++;
    }
    token.ptr = c->line.ptr + start;
    token.len = c->pos - start;
    if (token.len == 0u && c->pos == c->line.len) {
        return PEL_ERR_TRUNCATED;
    }
    rc = span_number_copy(token, buf, &len);
    if (rc != PEL_OK) {
        return rc;
    }
    sign = buf[0] == '-' ? 1u : 0u;
    if (len == sign || !span_u32_chars(buf + sign, len - sign)) {
        return PEL_ERR_INVALID;
    }
    errno = 0;
    value = strtoll(buf, &end, 10);
    if (end != buf + len) {
        return PEL_ERR_INVALID;
    }
    return errno == ERANGE || value < INT16_MIN || value > INT16_MAX ? PEL_ERR_RANGE : PEL_OK;
}

/* x264 b35605a ratecontrol.c: optional luma (3) or luma/chroma (8) weights. */
static pel_result x264_weights(TlmCursor *c)
{
    size_t i;
    pel_result rc = cursor_expect(c, "w:");

    for (i = 0u; rc == PEL_OK && i < 8u; i++) {
        rc = x264_weight_number(c);
        if (rc != PEL_OK) {
            return rc;
        }
        if ((i == 2u || i == 7u) && (c->pos == c->line.len || c->line.ptr[c->pos] != ',')) {
            return x264_finish(c);
        }
        rc = cursor_expect(c, ",");
    }
    return rc == PEL_OK ? PEL_ERR_INVALID : rc;
}

static pel_result x264_refs(TlmCursor *c)
{
    pel_result rc = cursor_expect(c, "ref:");
    size_t iteration;

    /* At most one token per byte; line is capped at TLM_TEXT_LINE_MAX. */
    for (iteration = 0u; rc == PEL_OK && iteration <= TLM_TEXT_LINE_MAX; iteration++) {
        size_t start;
        uint32_t ignored = 0;
        TlmSpan token;

        cursor_space(c);
        if (c->pos == c->line.len) {
            return PEL_ERR_TRUNCATED;
        }
        if (c->line.ptr[c->pos] == ';') {
            return x264_finish(c);
        }
        if (span_starts((TlmSpan){c->line.ptr + c->pos, c->line.len - c->pos}, "w:")) {
            return x264_weights(c);
        }
        start = c->pos;
        while (c->pos < c->line.len && !ascii_space(c->line.ptr[c->pos]) &&
               c->line.ptr[c->pos] != ';') {
            c->pos++;
        }
        token.ptr = c->line.ptr + start;
        token.len = c->pos - start;
        rc = span_u32(token, &ignored);
    }
    return rc == PEL_OK ? PEL_ERR_RANGE : rc;
}

static pel_result x264_tail(TlmCursor *c, X264Values *v)
{
    static const char *const counts[] = {"imb:", "pmb:", "smb:"};
    char direct = '\0';
    size_t i;
    pel_result rc = x264_u32(c, "tex:", &v->tex);

    if (rc == PEL_OK) {
        rc = x264_u32(c, "mv:", &v->mv);
    }
    if (rc == PEL_OK) {
        rc = x264_u32(c, "misc:", &v->misc);
    }
    for (i = 0u; rc == PEL_OK && i < 3u; i++) {
        uint32_t ignored = 0;

        rc = x264_u32(c, counts[i], &ignored);
    }
    if (rc == PEL_OK) {
        rc = x264_char(c, "d:", &direct);
    }
    if (rc == PEL_OK && direct != '-' && direct != 's' && direct != 't') {
        rc = PEL_ERR_INVALID;
    }
    return rc == PEL_OK ? x264_refs(c) : rc;
}

static pel_result x264_record(TlmSpan line, uint8_t depth, TlmLocale locale,
                              PelorusEncTelemetryInput *out)
{
    X264Values v = {0};
    TlmCursor cursor = {line, 0u, locale};
    uint64_t bits;
    uint64_t headers;
    pel_result rc = x264_prefix(&cursor, depth, &v);

    if (rc == PEL_OK) {
        rc = x264_tail(&cursor, &v);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    headers = (uint64_t)v.mv + v.misc;
    bits = (uint64_t)v.tex + headers;
    if (headers > UINT32_MAX) {
        return PEL_ERR_RANGE;
    }
    if (bits % 8u != 0u) {
        return PEL_ERR_INVALID;
    }
    (void)memset(out, 0, sizeof(*out));
    out->frame.present_mask = PEL_TLM_F_DISPLAY_INDEX | PEL_TLM_F_DECODE_INDEX |
                              PEL_TLM_F_FRAME_BYTES | PEL_TLM_F_HEADER_BITS |
                              PEL_TLM_F_RESIDUAL_BITS | PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM |
                              PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_KEY_FRAME | PEL_TLM_F_REFERENCE |
                              PEL_TLM_F_SHOWN | PEL_TLM_F_CODED_BIT_DEPTH;
    out->frame.display_index = v.display;
    out->frame.decode_index = v.decode;
    out->frame.frame_bytes = (uint32_t)(bits / 8u);
    out->frame.header_bits = (uint32_t)headers;
    out->frame.residual_bits = v.tex;
    out->frame.avg_qp = v.aq;
    out->frame.picture_type = v.picture_type;
    out->frame.frame_flags = v.flags;
    out->frame.codec = PEL_TLM_CODEC_H264;
    out->frame.qp_scale = PEL_QP_SCALE_SLICE_QP;
    out->frame.granularity = PEL_TLM_GRAN_FRAME;
    out->frame.coded_bit_depth = depth;
    out->frame.adapter = PEL_TLM_ADAPTER_EXTERNAL;
    rc = pel_qp_normalize(out->frame.qp_scale, depth, v.aq, &out->frame.avg_qp_norm);
    return rc == PEL_OK ? pel_enc_telemetry_validate(out) : rc;
}

static pel_result x264_walk(const char *text, size_t len, uint8_t depth, TlmLocale locale,
                            PelorusEncTelemetryInput *out, size_t *out_count)
{
    size_t offset = 0;
    size_t count = 0;
    size_t iteration;
    int have_line = 0;
    pel_result rc = PEL_OK;

    for (iteration = 0u; rc == PEL_OK && iteration <= len && iteration <= TLM_TEXT_TOTAL_MAX;
         iteration++) {
        TlmSpan line;
        PelorusEncTelemetryInput record;

        rc = next_line(text, len, &offset, &line, &have_line);
        if (rc != PEL_OK || !have_line) {
            break;
        }
        if (line.len == 0u || span_starts(line, "#options:")) {
            continue;
        }
        rc = x264_record(line, depth, locale, &record);
        if (rc == PEL_OK && out != NULL) {
            out[count] = record;
        }
        if (rc == PEL_OK) {
            count++;
        }
    }
    *out_count = count;
    return rc;
}

pel_result pel_tlm_parse_x264_stats(const char *text, size_t len, uint8_t coded_bit_depth,
                                    PelorusEncTelemetryInput *out_frames, size_t cap,
                                    size_t *out_count)
{
    size_t count = 0;
    TlmLocale locale;
    pel_result rc = adapter_args(text, len, coded_bit_depth, out_frames, cap, out_count);

    if (rc != PEL_OK) {
        return rc;
    }
    locale = tlm_locale_create();
    if (locale == (TlmLocale)0) {
        return PEL_ERR_NOMEM;
    }
    rc = x264_walk(text, len, coded_bit_depth, locale, NULL, &count);
    if (rc == PEL_OK && count > cap) {
        *out_count = count;
        rc = PEL_ERR_RANGE;
    } else if (rc == PEL_OK) {
        rc = x264_walk(text, len, coded_bit_depth, locale, out_frames, out_count);
    }
    tlm_locale_free(locale);
    return rc;
}

static uint32_t ffmpeg_read_u32le(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8u) | ((uint32_t)p[2] << 16u) |
           ((uint32_t)p[3] << 24u);
}

static uint64_t ffmpeg_read_u64le(const uint8_t *p)
{
    return (uint64_t)p[0] | ((uint64_t)p[1] << 8u) | ((uint64_t)p[2] << 16u) |
           ((uint64_t)p[3] << 24u) | ((uint64_t)p[4] << 32u) | ((uint64_t)p[5] << 40u) |
           ((uint64_t)p[6] << 48u) | ((uint64_t)p[7] << 56u);
}

static int ffmpeg_scale_fits_codec(uint8_t scale, uint8_t codec)
{
    switch (scale) {
    case PEL_QP_SCALE_SLICE_QP:
        return codec == PEL_TLM_CODEC_H264 || codec == PEL_TLM_CODEC_HEVC ||
               codec == PEL_TLM_CODEC_VVC;
    case PEL_QP_SCALE_AV1_ENCODER_QP:
        return codec == PEL_TLM_CODEC_AV1;
    case PEL_QP_SCALE_VP9_QINDEX:
        return codec == PEL_TLM_CODEC_VP9;
    default:
        return 0;
    }
}

static pel_result ffmpeg_qstats_check_args(const uint8_t *buf, size_t len, uint8_t codec,
                                           uint8_t qp_scale, uint8_t depth,
                                           const PelorusEncTelemetryInput *out)
{
    size_t expected_len;

    if (buf == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    if (codec < PEL_TLM_CODEC_H264 || codec > PEL_TLM_CODEC_VP9 ||
        !ffmpeg_scale_fits_codec(qp_scale, codec)) {
        return PEL_ERR_INVALID;
    }
    if (depth != 8u && depth != 10u && depth != 12u) {
        return PEL_ERR_RANGE;
    }
    if (len < 8u) {
        return PEL_ERR_TRUNCATED;
    }
    expected_len = 8u + 8u * (size_t)buf[5];
    if (len < expected_len) {
        return PEL_ERR_TRUNCATED;
    }
    return PEL_OK;
}

static pel_result ffmpeg_qstats_parse_qp(uint32_t quality, uint8_t qp_scale, uint8_t depth,
                                         PelorusEncTelemetryInput *rec)
{
    float qp;
    float norm = 0.0f;
    pel_result rc;

    if (quality == 0u) {
        return PEL_OK;
    }
    qp = (float)quality / 118.0f;
    rc = pel_qp_normalize(qp_scale, depth, qp, &norm);
    if (rc != PEL_OK) {
        return rc;
    }
    rec->frame.avg_qp = qp;
    rec->frame.avg_qp_norm = norm;
    rec->frame.qp_scale = qp_scale;
    rec->frame.present_mask |= PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM;
    return PEL_OK;
}

static pel_result ffmpeg_qstats_parse_sse(const uint8_t *buf, uint8_t count, uint8_t depth,
                                          const uint64_t plane_samples[3],
                                          PelorusEncTelemetryInput *rec)
{
    const double peak = (double)((1u << depth) - 1u);
    const unsigned planes = count < 3u ? (unsigned)count : 3u;
    unsigned i;

    for (i = 0u; i < planes; i++) {
        uint64_t sse;
        float psnr;

        if (plane_samples == NULL || plane_samples[i] == 0u) {
            continue;
        }
        sse = ffmpeg_read_u64le(buf + 8u + (size_t)i * 8u);
        if (sse == 0u) {
            psnr = 100.0f;
        } else {
            const double mse = (double)sse / (double)plane_samples[i];
            const double psnr_val = 10.0 * log10((peak * peak) / mse);

            if (!isfinite(psnr_val) || psnr_val < 0.0) {
                return PEL_ERR_RANGE;
            }
            psnr = (float)psnr_val;
            if (psnr > 100.0f) {
                psnr = 100.0f;
            }
        }
        if (i == 0u) {
            rec->frame.psnr_y = psnr;
            rec->frame.present_mask |= PEL_TLM_F_PSNR_Y;
        } else if (i == 1u) {
            rec->frame.psnr_u = psnr;
            rec->frame.present_mask |= PEL_TLM_F_PSNR_U;
        } else {
            rec->frame.psnr_v = psnr;
            rec->frame.present_mask |= PEL_TLM_F_PSNR_V;
        }
    }
    if ((rec->frame.present_mask & (PEL_TLM_F_PSNR_Y | PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V)) !=
        0u) {
        rec->frame.metric_source = PEL_TLM_METRIC_ADAPTER_SSE;
    }
    return PEL_OK;
}

pel_result pel_tlm_parse_ffmpeg_quality_stats(const uint8_t *buf, size_t len, uint8_t codec,
                                              uint8_t qp_scale, uint8_t coded_bit_depth,
                                              const uint64_t plane_samples[3],
                                              PelorusEncTelemetryInput *out)
{
    PelorusEncTelemetryInput rec;
    uint8_t pict_type;
    pel_result rc;

    rc = ffmpeg_qstats_check_args(buf, len, codec, qp_scale, coded_bit_depth, out);
    if (rc != PEL_OK) {
        return rc;
    }
    pict_type = buf[4];
    if (pict_type > 7u) {
        return PEL_ERR_RANGE;
    }
    (void)memset(&rec, 0, sizeof(rec));
    rec.frame.adapter = PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS;
    rec.frame.codec = codec;
    rec.frame.granularity = PEL_TLM_GRAN_FRAME;
    rec.frame.coded_bit_depth = coded_bit_depth;
    rec.frame.present_mask = PEL_TLM_F_CODED_BIT_DEPTH;
    if (pict_type >= 1u && pict_type <= 3u) {
        rec.frame.picture_type = pict_type;
        rec.frame.present_mask |= PEL_TLM_F_PICTURE_TYPE;
    }
    if (codec != PEL_TLM_CODEC_VP9) {
        const uint32_t quality = ffmpeg_read_u32le(buf);
        rc = ffmpeg_qstats_parse_qp(quality, qp_scale, coded_bit_depth, &rec);
    }
    if (rc == PEL_OK) {
        rc = ffmpeg_qstats_parse_sse(buf, buf[5], coded_bit_depth, plane_samples, &rec);
    }
    if (rc == PEL_OK) {
        rc = pel_enc_telemetry_validate(&rec);
    }
    if (rc == PEL_OK) {
        *out = rec;
    }
    return rc;
}

/* libaom v3.15.2 aom/aom_codec.h:288-299 defines the public frame flags.
 * aom/aom_codec.h:226-230 allocates 0x10000-0x800000 to the encoder;
 * av1/av1_cx_iface.c:3279 (get_frame_pkt_flags) sets flags = lib_flags << 16.
 * Validate unknown bits only in 0x0000FFFF; ignore encoder-private bits 16-31. */
#define TLM_AOM_PUBLIC_MASK 0x0000ffffu
#define TLM_AOM_KEY 0x1u
#define TLM_AOM_DROPPABLE 0x2u
#define TLM_AOM_INTRAONLY 0x10u
#define TLM_AOM_SWITCH 0x20u
#define TLM_AOM_ERROR_RESILIENT 0x40u
#define TLM_AOM_DELAYED_RANDOM_ACCESS_POINT 0x80u
#define TLM_AOM_FLAGS                                                                              \
    (TLM_AOM_KEY | TLM_AOM_DROPPABLE | TLM_AOM_INTRAONLY | TLM_AOM_SWITCH |                        \
     TLM_AOM_ERROR_RESILIENT | TLM_AOM_DELAYED_RANDOM_ACCESS_POINT)

static pel_result libaom_psnr(const PelTlmLibaomFrame *in, PelorusEncTelemetryInput *rec)
{
    float planes[3] = {0.0f};

    if (in->has_psnr == 0u) {
        return PEL_OK;
    }
    for (unsigned i = 0u; i < 4u; i++) {
        const double value = in->psnr[i];
        if (isnan(value) || value < 0.0) {
            return PEL_ERR_RANGE;
        }
        if (i > 0u) {
            planes[i - 1u] = value > 100.0 ? 100.0f : (float)value;
        }
    }
    rec->frame.psnr_y = planes[0];
    rec->frame.psnr_u = planes[1];
    rec->frame.psnr_v = planes[2];
    rec->frame.metric_source = PEL_TLM_METRIC_ENCODER;
    rec->frame.present_mask |= PEL_TLM_F_PSNR_Y | PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V;
    return PEL_OK;
}

pel_result pel_tlm_from_libaom(const PelTlmLibaomFrame *in, uint8_t coded_bit_depth,
                               PelorusEncTelemetryInput *out)
{
    PelorusEncTelemetryInput rec;
    pel_result rc;

    if (in == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    if ((in->frame_flags & TLM_AOM_PUBLIC_MASK & ~TLM_AOM_FLAGS) != 0u || in->has_psnr > 1u) {
        return PEL_ERR_INVALID;
    }
    if (in->frame_bytes > UINT32_MAX || in->quantizer_64 < -1 || in->quantizer_64 > 63) {
        return PEL_ERR_RANGE;
    }
    (void)memset(&rec, 0, sizeof(rec));
    rec.frame.adapter = PEL_TLM_ADAPTER_LIBAOM_STATS;
    rec.frame.codec = PEL_TLM_CODEC_AV1;
    rec.frame.granularity = PEL_TLM_GRAN_FRAME;
    rec.frame.coded_bit_depth = coded_bit_depth;
    rec.frame.display_index = in->display_index;
    rec.frame.decode_index = in->decode_index;
    rec.frame.frame_bytes = (uint32_t)in->frame_bytes;
    rec.frame.present_mask = PEL_TLM_F_DISPLAY_INDEX | PEL_TLM_F_DECODE_INDEX |
                             PEL_TLM_F_FRAME_BYTES | PEL_TLM_F_CODED_BIT_DEPTH |
                             PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_KEY_FRAME;
    /* The ABI defines P as every other AV1 frame (interop.h pel_picture_type),
     * not a P/B reference-direction claim. No trustworthy reference/shown
     * status is inferred from this mirror; DROPPABLE is only accepted. */
    rec.frame.picture_type =
        (in->frame_flags & (TLM_AOM_KEY | TLM_AOM_INTRAONLY)) != 0u ? PEL_PICTURE_I : PEL_PICTURE_P;
    rec.frame.frame_flags = (in->frame_flags & TLM_AOM_KEY) != 0u ? PEL_TLM_FRAME_KEY : 0u;
    if (in->quantizer_64 != -1) {
        rec.frame.avg_qp = (float)in->quantizer_64;
        rec.frame.qp_scale = PEL_QP_SCALE_AV1_ENCODER_QP;
        rec.frame.present_mask |= PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM;
        rc = pel_qp_normalize(rec.frame.qp_scale, coded_bit_depth, rec.frame.avg_qp,
                              &rec.frame.avg_qp_norm);
        if (rc != PEL_OK) {
            return rc;
        }
    }
    rc = libaom_psnr(in, &rec);
    if (rc == PEL_OK) {
        rc = pel_enc_telemetry_validate(&rec);
    }
    if (rc == PEL_OK) {
        *out = rec;
    }
    return rc;
}
