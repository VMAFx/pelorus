/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * telemetry.c — validation, sizing and non-allocating packing of the
 * PEL_SEC_ENC_TELEMETRY section, plus its map reader (interop ABI 1.4,
 * ADR-0174, docs/api/encoder-telemetry.md).
 *
 * Not part of the VMAFx mirror at RC4 (ADR-0174 decision 11): the wire layout
 * and pel_blob_map() live in interop.c; this unit is the writer-side contract.
 * Every loop is bounded by a constant table size or by PEL_TLM_MAP_MAX_ELEMS,
 * which the geometry check enforces before any map loop runs (HISS-02).
 */

#include "pelorus/telemetry.h"

#include <float.h>
#include <math.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop.c, VMAFx ADR-1138). */

#define TLM_ALIGN8(x) (((x) + 7u) & ~7u)
#define TLM_NB_MAPS 3u
#define TLM_MAP_BITS (PEL_TLM_F_QP_MAP | PEL_TLM_F_BITS_MAP | PEL_TLM_F_MODE_MAP)
#define TLM_QP_SCALE_BITS (PEL_TLM_F_AVG_QP | PEL_TLM_F_AVG_QP_NORM | PEL_TLM_F_QP_MAP)
#define TLM_METRIC_BITS (PEL_TLM_F_PSNR_Y | PEL_TLM_F_PSNR_U | PEL_TLM_F_PSNR_V | PEL_TLM_F_SSIM_Y)
#define TLM_FRAME_FLAG_MASK 0x0Fu
#define TLM_FRACTION_SUM_MAX 1.001f /* 1 + 1e-3: three rounded shares may overshoot */
#define TLM_BLOCK_LOG2_MIN 2u
#define TLM_BLOCK_LOG2_MAX 7u

enum tlm_kind { TLM_U32 = 0, TLM_F32 = 1, TLM_U8 = 2 };

/* One optional scalar: its presence bit and where it lives in the section. */
typedef struct TlmScalar {
    uint64_t bit;
    size_t offset;
    uint8_t kind;
} TlmScalar;

static const TlmScalar tlm_scalars[] = {
    {PEL_TLM_F_DISPLAY_INDEX, offsetof(PelorusEncTelemetrySection, display_index), TLM_U32},
    {PEL_TLM_F_DECODE_INDEX, offsetof(PelorusEncTelemetrySection, decode_index), TLM_U32},
    {PEL_TLM_F_FRAME_BYTES, offsetof(PelorusEncTelemetrySection, frame_bytes), TLM_U32},
    {PEL_TLM_F_HEADER_BITS, offsetof(PelorusEncTelemetrySection, header_bits), TLM_U32},
    {PEL_TLM_F_RESIDUAL_BITS, offsetof(PelorusEncTelemetrySection, residual_bits), TLM_U32},
    {PEL_TLM_F_AVG_QP, offsetof(PelorusEncTelemetrySection, avg_qp), TLM_F32},
    {PEL_TLM_F_AVG_QP_NORM, offsetof(PelorusEncTelemetrySection, avg_qp_norm), TLM_F32},
    {PEL_TLM_F_PSNR_Y, offsetof(PelorusEncTelemetrySection, psnr_y), TLM_F32},
    {PEL_TLM_F_PSNR_U, offsetof(PelorusEncTelemetrySection, psnr_u), TLM_F32},
    {PEL_TLM_F_PSNR_V, offsetof(PelorusEncTelemetrySection, psnr_v), TLM_F32},
    {PEL_TLM_F_SSIM_Y, offsetof(PelorusEncTelemetrySection, ssim_y), TLM_F32},
    {PEL_TLM_F_INTRA_FRACTION, offsetof(PelorusEncTelemetrySection, intra_fraction), TLM_F32},
    {PEL_TLM_F_INTER_FRACTION, offsetof(PelorusEncTelemetrySection, inter_fraction), TLM_F32},
    {PEL_TLM_F_SKIP_FRACTION, offsetof(PelorusEncTelemetrySection, skip_fraction), TLM_F32},
    {PEL_TLM_F_PICTURE_TYPE, offsetof(PelorusEncTelemetrySection, picture_type), TLM_U8},
    {PEL_TLM_F_CODED_BIT_DEPTH, offsetof(PelorusEncTelemetrySection, coded_bit_depth), TLM_U8},
};

/* One map: presence bit, caller data, element size, and the section's offset/size. */
typedef struct TlmMap {
    uint64_t bit;
    const void *data;
    uint32_t elem_size;
    uint32_t offset;
    uint32_t size;
} TlmMap;

/* Byte placement of the maps after the packed section (blob-relative). */
typedef struct TlmLayout {
    uint32_t offset[TLM_NB_MAPS];
    uint32_t size[TLM_NB_MAPS];
    uint32_t end;    /* total_size of the finished blob */
    size_t blob_len; /* 16 + end */
} TlmLayout;

/* Read one scalar as a double through memcpy (no aliasing or alignment assumption). */
static double tlm_scalar_value(const PelorusEncTelemetrySection *t, const TlmScalar *sc)
{
    uint8_t raw[sizeof(t->present_mask)];
    uint32_t u32 = 0;
    float f32 = 0.0f;

    memcpy(raw, (const uint8_t *)t + sc->offset, sc->kind == TLM_U8 ? 1u : 4u);
    if (sc->kind == TLM_U8) {
        return (double)raw[0];
    }
    if (sc->kind == TLM_U32) {
        memcpy(&u32, raw, sizeof(u32));
        return (double)u32;
    }
    memcpy(&f32, raw, sizeof(f32));
    return (double)f32;
}

/* The map in slot `i` (0 qp, 1 bits, 2 mode), with the input's pointer. */
static TlmMap tlm_map(const PelorusEncTelemetryInput *in, unsigned i)
{
    TlmMap m;

    memset(&m, 0, sizeof(m));
    if (i == 0u) {
        m.bit = PEL_TLM_F_QP_MAP;
        m.data = in->qp_map;
        m.elem_size = (uint32_t)sizeof(int16_t);
        m.offset = in->frame.qp_map_offset;
        m.size = in->frame.qp_map_size;
    } else if (i == 1u) {
        m.bit = PEL_TLM_F_BITS_MAP;
        m.data = in->bits_map;
        m.elem_size = (uint32_t)sizeof(uint32_t);
        m.offset = in->frame.bits_map_offset;
        m.size = in->frame.bits_map_size;
    } else {
        m.bit = PEL_TLM_F_MODE_MAP;
        m.data = in->mode_map;
        m.elem_size = (uint32_t)sizeof(uint8_t);
        m.offset = in->frame.mode_map_offset;
        m.size = in->frame.mode_map_size;
    }
    return m;
}

static int tlm_depth_ok(uint8_t depth)
{
    return depth == 8u || depth == 10u || depth == 12u;
}

/* Whether a QP scale belongs to a codec. */
static int tlm_scale_fits_codec(uint8_t scale, uint8_t codec)
{
    switch (scale) {
    case PEL_QP_SCALE_SLICE_QP:
        return codec == PEL_TLM_CODEC_H264 || codec == PEL_TLM_CODEC_HEVC ||
               codec == PEL_TLM_CODEC_VVC;
    case PEL_QP_SCALE_AV1_QINDEX:
    case PEL_QP_SCALE_AV1_ENCODER_QP:
        return codec == PEL_TLM_CODEC_AV1;
    case PEL_QP_SCALE_VP9_QINDEX:
        return codec == PEL_TLM_CODEC_VP9;
    default:
        return 0;
    }
}

/* Native range of a QP scale. codec 0 = unknown (VVC's upper bound applies); depth 0 =
 * unknown (12-bit's QpBdOffsetY applies). Returns 0 for an unknown scale. */
static int tlm_qp_range(uint8_t scale, uint8_t codec, uint8_t depth, float *lo, float *hi)
{
    switch (scale) {
    case PEL_QP_SCALE_SLICE_QP:
        *lo = depth >= 8u ? -6.0f * (float)(depth - 8u) : -24.0f;
        *hi = (codec == PEL_TLM_CODEC_H264 || codec == PEL_TLM_CODEC_HEVC) ? 51.0f : 63.0f;
        return 1;
    case PEL_QP_SCALE_AV1_QINDEX:
    case PEL_QP_SCALE_VP9_QINDEX:
        *lo = 0.0f;
        *hi = 255.0f;
        return 1;
    case PEL_QP_SCALE_AV1_ENCODER_QP:
        *lo = 0.0f;
        *hi = 63.0f;
        return 1;
    default:
        return 0;
    }
}

/* Element count of a row or block grid, or 0 when the geometry is out of bounds. */
static uint32_t tlm_grid_elems(const PelorusEncTelemetrySection *t)
{
    uint32_t elems = (uint32_t)t->map_cols * (uint32_t)t->map_rows;

    if (t->map_cols == 0u || t->map_rows == 0u || elems > PEL_TLM_MAP_MAX_ELEMS) {
        return 0u;
    }
    if (t->block_size_log2 < TLM_BLOCK_LOG2_MIN || t->block_size_log2 > TLM_BLOCK_LOG2_MAX) {
        return 0u;
    }
    return elems;
}

static int tlm_is_map_granularity(uint8_t granularity)
{
    return granularity == PEL_TLM_GRAN_ROW || granularity == PEL_TLM_GRAN_BLOCK;
}

/* Enumerations, the presence mask, picture_type and the frame flags. */
static pel_result tlm_check_enums(const PelorusEncTelemetrySection *t)
{
    const uint64_t m = t->present_mask;
    unsigned i;

    if (t->codec < PEL_TLM_CODEC_H264 || t->codec > PEL_TLM_CODEC_VP9 ||
        t->granularity < PEL_TLM_GRAN_FRAME || t->granularity > PEL_TLM_GRAN_BLOCK ||
        t->adapter < PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS ||
        t->adapter > PEL_TLM_ADAPTER_EXTERNAL) {
        return PEL_ERR_INVALID;
    }
    if ((m & ~PEL_TLM_KNOWN_MASK) != 0u) {
        return PEL_ERR_INVALID;
    }
    if ((m & PEL_TLM_F_PICTURE_TYPE) != 0u &&
        (t->picture_type < PEL_PICTURE_I || t->picture_type > PEL_PICTURE_B)) {
        return PEL_ERR_INVALID;
    }
    if (((unsigned)t->frame_flags & ~TLM_FRAME_FLAG_MASK) != 0u) {
        return PEL_ERR_INVALID;
    }
    /* Flag bit i (KEY, REFERENCE, SHOWN, SCENE_CUT) needs presence bit 15 + i. */
    for (i = 0; i < 4u; i++) {
        if (((unsigned)t->frame_flags & (1u << i)) != 0u &&
            (m & (PEL_TLM_F_KEY_FRAME << i)) == 0u) {
            return PEL_ERR_INVALID;
        }
    }
    return PEL_OK;
}

/* "Not reported" is a clear bit with a zero value: a value under a clear bit is a lie. */
static pel_result tlm_check_absent_zero(const PelorusEncTelemetrySection *t)
{
    size_t i;

    for (i = 0; i < sizeof(tlm_scalars) / sizeof(tlm_scalars[0]); i++) {
        if ((t->present_mask & tlm_scalars[i].bit) == 0u &&
            tlm_scalar_value(t, &tlm_scalars[i]) != 0.0) {
            return PEL_ERR_INVALID;
        }
    }
    return PEL_OK;
}

/* qp_scale and metric_source accompany exactly their bits; avg_qp and the bit depth. */
static pel_result tlm_check_qp(const PelorusEncTelemetrySection *t)
{
    const uint64_t m = t->present_mask;
    float lo = 0.0f;
    float hi = 0.0f;

    if ((m & TLM_QP_SCALE_BITS) != 0u ? !tlm_scale_fits_codec(t->qp_scale, t->codec) :
                                        t->qp_scale != 0u) {
        return PEL_ERR_INVALID;
    }
    if ((m & TLM_METRIC_BITS) != 0u ? (t->metric_source < PEL_TLM_METRIC_ENCODER ||
                                       t->metric_source > PEL_TLM_METRIC_ADAPTER_SSE) :
                                      t->metric_source != 0u) {
        return PEL_ERR_INVALID;
    }
    if ((m & PEL_TLM_F_CODED_BIT_DEPTH) != 0u && !tlm_depth_ok(t->coded_bit_depth)) {
        return PEL_ERR_RANGE;
    }
    if ((m & PEL_TLM_F_AVG_QP) != 0u) {
        (void)tlm_qp_range(t->qp_scale, t->codec, t->coded_bit_depth, &lo, &hi);
        if (!isfinite(t->avg_qp) || t->avg_qp < lo || t->avg_qp > hi) {
            return PEL_ERR_RANGE;
        }
    }
    return PEL_OK;
}

/* A present float is finite and inside [lo, hi]; an absent one passes. */
static int tlm_float_ok(uint64_t mask, uint64_t bit, float v, float lo, float hi)
{
    return (mask & bit) == 0u || (isfinite(v) && v >= lo && v <= hi);
}

/* PSNR, SSIM, the area shares and the bit split. */
static pel_result tlm_check_metrics(const PelorusEncTelemetrySection *t)
{
    const uint64_t m = t->present_mask;
    const uint64_t split = PEL_TLM_F_FRAME_BYTES | PEL_TLM_F_HEADER_BITS | PEL_TLM_F_RESIDUAL_BITS;

    if (!tlm_float_ok(m, PEL_TLM_F_PSNR_Y, t->psnr_y, 0.0f, FLT_MAX) ||
        !tlm_float_ok(m, PEL_TLM_F_PSNR_U, t->psnr_u, 0.0f, FLT_MAX) ||
        !tlm_float_ok(m, PEL_TLM_F_PSNR_V, t->psnr_v, 0.0f, FLT_MAX) ||
        !tlm_float_ok(m, PEL_TLM_F_SSIM_Y, t->ssim_y, 0.0f, 1.0f) ||
        !tlm_float_ok(m, PEL_TLM_F_AVG_QP_NORM, t->avg_qp_norm, -FLT_MAX, FLT_MAX)) {
        return PEL_ERR_RANGE;
    }
    if (!tlm_float_ok(m, PEL_TLM_F_INTRA_FRACTION, t->intra_fraction, 0.0f, 1.0f) ||
        !tlm_float_ok(m, PEL_TLM_F_INTER_FRACTION, t->inter_fraction, 0.0f, 1.0f) ||
        !tlm_float_ok(m, PEL_TLM_F_SKIP_FRACTION, t->skip_fraction, 0.0f, 1.0f)) {
        return PEL_ERR_RANGE;
    }
    /* Absent shares are zero (tlm_check_absent_zero), so the sum covers the present ones. */
    if (t->intra_fraction + t->inter_fraction + t->skip_fraction > TLM_FRACTION_SUM_MAX) {
        return PEL_ERR_RANGE;
    }
    if ((m & split) == split &&
        (uint64_t)t->header_bits + t->residual_bits > (uint64_t)t->frame_bytes * 8u) {
        return PEL_ERR_RANGE;
    }
    return PEL_OK;
}

/* Granularity against the map bits and the grid bounds. */
static pel_result tlm_check_geometry(const PelorusEncTelemetrySection *t)
{
    const uint64_t maps = t->present_mask & TLM_MAP_BITS;

    if (t->granularity == PEL_TLM_GRAN_FRAME) {
        if (maps != 0u || t->map_cols != 0u || t->map_rows != 0u || t->block_size_log2 != 0u) {
            return PEL_ERR_INVALID;
        }
        return PEL_OK;
    }
    if (maps == 0u) {
        return PEL_ERR_INVALID; /* row or block granularity says maps follow */
    }
    if (t->granularity == PEL_TLM_GRAN_ROW && t->map_cols != 1u) {
        return PEL_ERR_INVALID;
    }
    return tlm_grid_elems(t) == 0u ? PEL_ERR_RANGE : PEL_OK;
}

/* Each map bit has its pointer and nothing else; a clear bit leaves offset and size 0. */
static pel_result tlm_check_map_slots(const PelorusEncTelemetryInput *in)
{
    unsigned i;

    for (i = 0; i < TLM_NB_MAPS; i++) {
        const TlmMap m = tlm_map(in, i);
        const int present = (in->frame.present_mask & m.bit) != 0u;

        if (present != (m.data != NULL)) {
            return PEL_ERR_INVALID;
        }
        if (!present && (m.offset != 0u || m.size != 0u)) {
            return PEL_ERR_INVALID;
        }
    }
    return PEL_OK;
}

/* Map elements: block modes are enum values, QP elements stay inside the scale. */
static pel_result tlm_check_map_values(const PelorusEncTelemetryInput *in)
{
    const PelorusEncTelemetrySection *t = &in->frame;
    const uint32_t n = tlm_is_map_granularity(t->granularity) ? tlm_grid_elems(t) : 0u;
    float lo = 0.0f;
    float hi = 0.0f;
    uint32_t i;

    for (i = 0; in->mode_map != NULL && i < n; i++) {
        if (in->mode_map[i] > PEL_BLOCK_MODE_SKIP) {
            return PEL_ERR_INVALID;
        }
    }
    if (in->qp_map == NULL || n == 0u) {
        return PEL_OK;
    }
    (void)tlm_qp_range(t->qp_scale, t->codec, t->coded_bit_depth, &lo, &hi);
    for (i = 0; i < n; i++) {
        const float q = (float)in->qp_map[i]; /* Q2: native QP x 4 */
        if (q < lo * 4.0f || q > hi * 4.0f) {
            return PEL_ERR_RANGE;
        }
    }
    return PEL_OK;
}

/* A record's bits are a subset of its adapter's capability mask. */
static pel_result tlm_check_caps(const PelorusEncTelemetrySection *t)
{
    uint64_t caps = 0;
    pel_result rc;

    if (t->adapter == PEL_TLM_ADAPTER_EXTERNAL) {
        return PEL_OK;
    }
    rc = pel_tlm_adapter_caps(t->adapter, t->codec, &caps);
    if (rc != PEL_OK && rc != PEL_ERR_UNSUPPORTED) {
        return rc;
    }
    /* An adapter without a table (UNSUPPORTED) has caps 0: only an empty record passes. */
    return (t->present_mask & ~caps) != 0u ? PEL_ERR_INVALID : PEL_OK;
}

pel_result pel_enc_telemetry_validate(const PelorusEncTelemetryInput *in)
{
    pel_result rc;

    if (in == NULL) {
        return PEL_ERR_INVALID;
    }
    rc = tlm_check_enums(&in->frame);
    if (rc == PEL_OK) {
        rc = tlm_check_absent_zero(&in->frame);
    }
    if (rc == PEL_OK) {
        rc = tlm_check_qp(&in->frame);
    }
    if (rc == PEL_OK) {
        rc = tlm_check_metrics(&in->frame);
    }
    if (rc == PEL_OK) {
        rc = tlm_check_geometry(&in->frame);
    }
    if (rc == PEL_OK) {
        rc = tlm_check_map_slots(in);
    }
    if (rc == PEL_OK) {
        rc = tlm_check_map_values(in);
    }
    if (rc == PEL_OK) {
        rc = tlm_check_caps(&in->frame);
    }
    return rc;
}

/* Place the maps after the packed section. `in` is valid, so the sizes are bounded by
 * PEL_TLM_MAP_MAX_ELEMS * 7 and the sum cannot wrap. */
static pel_result tlm_layout(const PelorusSideData *meta, const PelorusEncTelemetryInput *in,
                             TlmLayout *lay)
{
    PelorusPackSection ps;
    size_t need = 0;
    uint32_t elems = 0;
    uint32_t end;
    unsigned i;
    pel_result rc;

    memset(lay, 0, sizeof(*lay));
    ps.id = PEL_SEC_ENC_TELEMETRY;
    ps.data = &in->frame;
    ps.size = (uint32_t)sizeof(in->frame);
    rc = pel_blob_pack_into(meta, &ps, 1, NULL, 0, &need); /* size query */
    if (rc != PEL_ERR_RANGE) {
        return rc == PEL_OK ? PEL_ERR_INVALID : rc;
    }
    end = (uint32_t)(need - PELORUS_SIDEDATA_UUID_LEN);
    if (tlm_is_map_granularity(in->frame.granularity)) {
        elems = tlm_grid_elems(&in->frame);
    }
    for (i = 0; i < TLM_NB_MAPS; i++) {
        const TlmMap m = tlm_map(in, i);
        if ((in->frame.present_mask & m.bit) == 0u) {
            continue;
        }
        lay->offset[i] = TLM_ALIGN8(end);
        lay->size[i] = elems * m.elem_size;
        end = lay->offset[i] + lay->size[i];
    }
    lay->end = end;
    lay->blob_len = (size_t)PELORUS_SIDEDATA_UUID_LEN + (size_t)end;
    return PEL_OK;
}

pel_result pel_enc_telemetry_blob_size(const PelorusEncTelemetryInput *in, size_t *out_len)
{
    PelorusSideData meta;
    TlmLayout lay;
    pel_result rc;

    if (out_len == NULL) {
        return PEL_ERR_INVALID;
    }
    *out_len = 0;
    rc = pel_enc_telemetry_validate(in);
    if (rc != PEL_OK) {
        return rc;
    }
    memset(&meta, 0, sizeof(meta)); /* the layout does not depend on the header fields */
    rc = tlm_layout(&meta, in, &lay);
    if (rc == PEL_OK) {
        *out_len = lay.blob_len;
    }
    return rc;
}

/* Copy the maps to their offsets, zero the alignment gaps, and close total_size. */
static void tlm_write_maps(const PelorusEncTelemetryInput *in, const TlmLayout *lay, uint8_t *buf,
                           uint32_t end)
{
    uint8_t *image = buf + PELORUS_SIDEDATA_UUID_LEN;
    PelorusSideData hdr;
    unsigned i;

    for (i = 0; i < TLM_NB_MAPS; i++) {
        const TlmMap m = tlm_map(in, i);
        if (lay->size[i] == 0u) {
            continue;
        }
        memset(image + end, 0, (size_t)(lay->offset[i] - end));
        memcpy(image + lay->offset[i], m.data, lay->size[i]);
        end = lay->offset[i] + lay->size[i];
    }
    memcpy(&hdr, image, sizeof(hdr));
    hdr.total_size = lay->end;
    memcpy(image, &hdr, sizeof(hdr));
}

pel_result pel_enc_telemetry_pack(const PelorusSideData *meta, const PelorusEncTelemetryInput *in,
                                  uint8_t *buf, size_t cap, size_t *out_len)
{
    PelorusEncTelemetrySection sec;
    PelorusPackSection ps;
    TlmLayout lay;
    size_t len = 0;
    pel_result rc;

    if (out_len == NULL) {
        return PEL_ERR_INVALID;
    }
    *out_len = 0;
    if (meta == NULL || in == NULL || buf == NULL) {
        return PEL_ERR_INVALID;
    }
    rc = pel_enc_telemetry_validate(in);
    if (rc == PEL_OK) {
        rc = tlm_layout(meta, in, &lay);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    if (cap < lay.blob_len) {
        *out_len = lay.blob_len;
        return PEL_ERR_RANGE; /* never a partial blob: buf is untouched */
    }
    sec = in->frame;
    sec.qp_map_offset = lay.offset[0];
    sec.qp_map_size = lay.size[0];
    sec.bits_map_offset = lay.offset[1];
    sec.bits_map_size = lay.size[1];
    sec.mode_map_offset = lay.offset[2];
    sec.mode_map_size = lay.size[2];
    memset(sec._pad, 0, sizeof(sec._pad));
    ps.id = PEL_SEC_ENC_TELEMETRY;
    ps.data = &sec;
    ps.size = (uint32_t)sizeof(sec);
    rc = pel_blob_pack_into(meta, &ps, 1, buf, cap, &len);
    if (rc != PEL_OK) {
        return rc;
    }
    tlm_write_maps(in, &lay, buf, (uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN));
    *out_len = lay.blob_len;
    return PEL_OK;
}

pel_result pel_tlm_adapter_caps(uint8_t adapter, uint8_t codec, uint64_t *out_mask)
{
    if (out_mask == NULL) {
        return PEL_ERR_INVALID;
    }
    *out_mask = 0;
    if (adapter < PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS || adapter > PEL_TLM_ADAPTER_EXTERNAL ||
        codec < PEL_TLM_CODEC_H264 || codec > PEL_TLM_CODEC_VP9) {
        return PEL_ERR_INVALID;
    }
    if (adapter != PEL_TLM_ADAPTER_EXTERNAL) {
        return PEL_ERR_UNSUPPORTED; /* the table lands with the adapter (#86) */
    }
    *out_mask = PEL_TLM_KNOWN_MASK;
    return PEL_OK;
}

pel_result pel_qp_normalize(uint8_t qp_scale, uint8_t coded_bit_depth, float qp, float *out_norm)
{
    float lo = 0.0f;
    float hi = 0.0f;

    if (out_norm == NULL || !tlm_qp_range(qp_scale, 0u, coded_bit_depth, &lo, &hi)) {
        return PEL_ERR_INVALID;
    }
    if (!tlm_depth_ok(coded_bit_depth) || !isfinite(qp) || qp < lo || qp > hi) {
        return PEL_ERR_RANGE;
    }
    if (qp_scale != PEL_QP_SCALE_SLICE_QP) {
        return PEL_ERR_UNSUPPORTED; /* step-size constants unverified (research 0174, E3) */
    }
    *out_norm = qp; /* H.26x SliceQpY: the identity */
    return PEL_OK;
}

/* The section fields of one map bit; 0 for any other bit. */
static int tlm_map_fields(const PelorusEncTelemetrySection *t, uint64_t map_bit, uint32_t *offset,
                          uint32_t *size, uint32_t *elem_size)
{
    if (map_bit == PEL_TLM_F_QP_MAP) {
        *offset = t->qp_map_offset;
        *size = t->qp_map_size;
        *elem_size = (uint32_t)sizeof(int16_t);
    } else if (map_bit == PEL_TLM_F_BITS_MAP) {
        *offset = t->bits_map_offset;
        *size = t->bits_map_size;
        *elem_size = (uint32_t)sizeof(uint32_t);
    } else if (map_bit == PEL_TLM_F_MODE_MAP) {
        *offset = t->mode_map_offset;
        *size = t->mode_map_size;
        *elem_size = (uint32_t)sizeof(uint8_t);
    } else {
        return 0;
    }
    return 1;
}

pel_result pel_enc_telemetry_map(const uint8_t *blob, size_t len,
                                 const PelorusEncTelemetrySection *t, size_t got, uint64_t map_bit,
                                 const void **out_ptr, uint32_t *out_elems)
{
    uint32_t offset = 0;
    uint32_t size = 0;
    uint32_t elem_size = 0;
    uint32_t elems;
    pel_result rc;

    if (out_ptr != NULL) {
        *out_ptr = NULL;
    }
    if (out_elems != NULL) {
        *out_elems = 0;
    }
    if (blob == NULL || t == NULL || out_ptr == NULL || out_elems == NULL || got < sizeof(*t) ||
        !tlm_map_fields(t, map_bit, &offset, &size, &elem_size)) {
        return PEL_ERR_INVALID;
    }
    /* Check 1: a clear bit or frame granularity means the map is ignored. */
    if ((t->present_mask & map_bit) == 0u || !tlm_is_map_granularity(t->granularity)) {
        return PEL_ERR_ABSENT;
    }
    /* Check 2: the grid geometry, before any size arithmetic. */
    elems = tlm_grid_elems(t);
    if (elems == 0u || (t->granularity == PEL_TLM_GRAN_ROW && t->map_cols != 1u)) {
        return PEL_ERR_ABI;
    }
    /* Checks 3 to 5: size, alignment, bounds (shared with VMAFx through interop.c). */
    rc = pel_blob_map(blob, len, offset, size, elems, elem_size, out_ptr);
    if (rc == PEL_OK) {
        *out_elems = elems;
    }
    return rc;
}

/* NOLINTEND(modernize-use-nullptr) */
