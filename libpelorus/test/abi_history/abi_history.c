/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

/* NOLINTNEXTLINE(bugprone-reserved-identifier,cert-dcl37-c,cert-dcl51-cpp): test_path_safety.h POSIX visibility */
#define _XOPEN_SOURCE 700

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus/interop.h"
#include "../test_path_safety.h"
#include "abi_history_values.h"

static const uint8_t k_carrier_uuid[16] = {0x3f, 0x9b, 0x37, 0xb8, 0xfd, 0x9a, 0x46, 0x21,
                                           0x92, 0x0e, 0x9b, 0x78, 0xb5, 0x5c, 0xf9, 0xb5};

static inline size_t min_size(size_t a, size_t b)
{
    return (a < b) ? a : b;
}

static void fill_banding(PelorusBandingSection *s)
{
    memset(s, 0, sizeof(*s));
    s->global_banding_risk = VAL_BANDING_GLOBAL_RISK;
    s->flat_area_fraction = VAL_BANDING_FLAT_AREA;
    s->contour_strength_mean = VAL_BANDING_CONTOUR_STRENGTH;
    s->dominant_band_luma = VAL_BANDING_DOMINANT_LUMA;
    s->cell_data_offset = 0;
    s->cell_data_size = (uint32_t)sizeof(val_banding_cell_data);
}

static void fill_variance(PelorusVarianceSection *s)
{
    memset(s, 0, sizeof(*s));
    s->global_variance = VAL_VARIANCE_GLOBAL;
    s->edge_density = VAL_VARIANCE_EDGE_DENSITY;
    s->texture_energy = VAL_VARIANCE_TEXTURE_ENERGY;
    s->var_cell_offset = 0;
    s->var_cell_size = (uint32_t)sizeof(val_var_cell_data);
    s->edge_cell_offset = 0;
    s->edge_cell_size = (uint32_t)sizeof(val_edge_cell_data);
}

static void fill_denoise(PelorusDenoiseSection *s)
{
    memset(s, 0, sizeof(*s));
    s->residual_energy_y = VAL_DENOISE_RESIDUAL_Y;
    s->residual_energy_u = VAL_DENOISE_RESIDUAL_U;
    s->residual_energy_v = VAL_DENOISE_RESIDUAL_V;
    s->applied_strength = VAL_DENOISE_APPLIED_STRENGTH;
    s->noise_sigma_estimate = VAL_DENOISE_NOISE_SIGMA;
    s->psnr_vs_input = VAL_DENOISE_PSNR;
    s->denoiser_id = (uint8_t)VAL_DENOISE_ID;
}

static void fill_filmgrain(PelorusFilmGrainSection *s)
{
    memset(s, 0, sizeof(*s));
    s->seed = VAL_GRAIN_SEED;
    s->num_y_points = VAL_GRAIN_NUM_Y_POINTS;
    s->num_uv_points[0] = VAL_GRAIN_NUM_UV_POINTS;
    s->num_uv_points[1] = VAL_GRAIN_NUM_UV_POINTS;
    s->scaling_shift = VAL_GRAIN_SCALING_SHIFT;
    s->ar_coeff_lag = VAL_GRAIN_AR_COEFF_LAG;
    s->ar_coeff_shift = VAL_GRAIN_AR_COEFF_SHIFT;
    s->grain_scale_shift = VAL_GRAIN_GRAIN_SCALE_SHIFT;
    s->apply = (uint8_t)VAL_GRAIN_APPLY;
    s->overlap_flag = (uint8_t)VAL_GRAIN_OVERLAP_FLAG;
    s->limit_output_range = (uint8_t)VAL_GRAIN_LIMIT_OUTPUT_RANGE;
    s->grain_model = (uint8_t)VAL_GRAIN_MODEL;
    s->y_points[0][0] = 16;
    s->y_points[0][1] = 4;
    s->y_points[1][0] = 64;
    s->y_points[1][1] = 8;
    s->uv_points[0][0][0] = 16;
    s->uv_points[0][0][1] = 4;
    s->uv_points[1][0][0] = 16;
    s->uv_points[1][0][1] = 4;
    s->ar_coeffs_y[0] = 1;
    s->ar_coeffs_y[1] = 2;
    s->ar_coeffs_y[2] = 3;
    s->ar_coeffs_uv[0][0] = 1;
    s->ar_coeffs_uv[0][1] = 2;
    s->ar_coeffs_uv[1][0] = 3;
    s->ar_coeffs_uv[1][1] = 4;
}

static void fill_motion(PelorusMotionSection *s)
{
    memset(s, 0, sizeof(*s));
    s->global_motion_x = VAL_MOTION_GLOBAL_X;
    s->global_motion_y = VAL_MOTION_GLOBAL_Y;
    s->motion_magnitude_mean = VAL_MOTION_MAGNITUDE_MEAN;
    s->motion_magnitude_p95 = VAL_MOTION_MAGNITUDE_P95;
    s->motion_entropy = VAL_MOTION_ENTROPY;
    s->mv_field_offset = 0;
    s->mv_field_size = (uint32_t)sizeof(val_mv_field_data);
#if PELORUS_ABI_MINOR >= 4
    s->block_size_log2 = (uint8_t)VAL_MOTION_BLOCK_SIZE_LOG2;
#endif
}

#if PELORUS_ABI_MINOR >= 3
static void fill_qpreport(PelorusQpReportSection *s)
{
    memset(s, 0, sizeof(*s));
    s->avg_qp = VAL_QPREPORT_AVG_QP;
    s->psnr_y = VAL_QPREPORT_PSNR_Y;
    s->psnr_u = VAL_QPREPORT_PSNR_U;
    s->psnr_v = VAL_QPREPORT_PSNR_V;
    s->total_bits = VAL_QPREPORT_TOTAL_BITS;
    s->num_intra_blocks = VAL_QPREPORT_NUM_INTRA;
    s->num_inter_blocks = VAL_QPREPORT_NUM_INTER;
    s->num_skipped_blocks = VAL_QPREPORT_NUM_SKIPPED;
    s->honored_fraction = VAL_QPREPORT_HONORED_FRACTION;
    s->report_source = (uint8_t)VAL_QPREPORT_REPORT_SOURCE;
    s->block_size_log2 = (uint8_t)VAL_QPREPORT_BLOCK_SIZE_LOG2;
    s->qp_valid = (uint8_t)VAL_QPREPORT_QP_VALID;
    s->qp_cell_offset = 0;
    s->qp_cell_size = (uint32_t)sizeof(val_qp_cell_data);
}

static void fill_motion_conf(PelorusMotionConfSection *s)
{
    memset(s, 0, sizeof(*s));
    s->conf_metric = (uint8_t)VAL_MOTION_CONF_METRIC;
    s->conf_field_offset = 0;
    s->conf_field_size = (uint32_t)sizeof(val_conf_field_data);
}

static void fill_complexity(PelorusComplexitySection *s)
{
    memset(s, 0, sizeof(*s));
    s->complexity = VAL_COMPLEXITY_VALUE;
    s->texture_energy = VAL_COMPLEXITY_TEXTURE;
    s->motion_component = VAL_COMPLEXITY_MOTION;
}
#endif

#if PELORUS_ABI_MINOR >= 4
static void fill_enc_telemetry(PelorusEncTelemetrySection *s)
{
    memset(s, 0, sizeof(*s));
    s->present_mask = VAL_TLM_PRESENT_MASK;
    s->display_index = VAL_TLM_DISPLAY_INDEX;
    s->decode_index = VAL_TLM_DECODE_INDEX;
    s->frame_bytes = VAL_TLM_FRAME_BYTES;
    s->avg_qp = VAL_TLM_AVG_QP;
    s->map_cols = (uint16_t)VAL_TLM_MAP_COLS;
    s->map_rows = (uint16_t)VAL_TLM_MAP_ROWS;
    s->codec = (uint8_t)VAL_TLM_CODEC;
    s->qp_scale = (uint8_t)VAL_TLM_QP_SCALE;
    s->picture_type = (uint8_t)VAL_TLM_PICTURE_TYPE;
    s->frame_flags = (uint8_t)VAL_TLM_FRAME_FLAGS;
    s->granularity = (uint8_t)VAL_TLM_GRANULARITY;
    s->block_size_log2 = (uint8_t)VAL_TLM_BLOCK_SIZE_LOG2;
    s->coded_bit_depth = (uint8_t)VAL_TLM_CODED_BIT_DEPTH;
    s->adapter = (uint8_t)VAL_TLM_ADAPTER;
    s->qp_map_offset = 0;
    s->qp_map_size = (uint32_t)sizeof(val_tlm_qp_map_data);
    s->mode_map_offset = 0;
    s->mode_map_size = (uint32_t)sizeof(val_tlm_mode_map_data);
}

static void fill_encode_record(PelorusEncodeRecordSection *s)
{
    memset(s, 0, sizeof(*s));
    memcpy(s->digest, val_record_digest_data, sizeof(s->digest));
    s->digest_alg = (uint8_t)VAL_RECORD_DIGEST_ALG;
    s->locator_kind = (uint8_t)VAL_RECORD_LOCATOR_KIND;
    s->record_major = (uint8_t)VAL_RECORD_MAJOR;
}
#endif

#if PELORUS_ABI_MINOR >= 6
static void fill_quality(PelorusQualitySection *s)
{
    memset(s, 0, sizeof(*s));
    s->window_id = VAL_QUALITY_WINDOW_ID;
    s->pts_start = VAL_QUALITY_PTS_START;
    s->pts_end = VAL_QUALITY_PTS_END;
    s->tb_num = VAL_QUALITY_TB_NUM;
    s->tb_den = VAL_QUALITY_TB_DEN;
    s->provenance_id = VAL_QUALITY_PROVENANCE_ID;
    s->score = VAL_QUALITY_SCORE;
    s->metric_id = (uint32_t)VAL_QUALITY_METRIC_ID;
    s->latency_frames = VAL_QUALITY_LATENCY_FRAMES;
    s->frame_count = VAL_QUALITY_FRAME_COUNT;
    memcpy(s->model_hash, val_quality_model_hash_data, sizeof(s->model_hash));
}
#endif

static uint32_t append_map(uint8_t *blob, size_t cap, uint32_t *end, const void *data,
                           uint32_t size)
{
    uint32_t off = (*end + 7u) & ~7u;
    if ((size_t)PELORUS_SIDEDATA_UUID_LEN + off + size > cap) {
        return 0;
    }
    memcpy(blob + PELORUS_SIDEDATA_UUID_LEN + off, data, size);
    *end = off + size;
    return off;
}

static void patch_section(uint8_t *blob, const void *p, const void *data, size_t size)
{
    if (blob != NULL && p != NULL && data != NULL) {
        size_t off = (size_t)((const uint8_t *)p - blob);
        memcpy(blob + off, data, size);
    }
}

static void pack_sections_setup(PelorusPackSection *secs, int *out_nb, PelorusBandingSection *band,
                                PelorusVarianceSection *var, PelorusDenoiseSection *den,
                                PelorusFilmGrainSection *grain, PelorusMotionSection *mo)
{
    int nb = 0;
    fill_banding(band);
    secs[nb].id = PEL_SEC_BANDING;
    secs[nb].data = band;
    secs[nb].size = (uint32_t)sizeof(*band);
    nb++;
    fill_variance(var);
    secs[nb].id = PEL_SEC_VARIANCE;
    secs[nb].data = var;
    secs[nb].size = (uint32_t)sizeof(*var);
    nb++;
    fill_denoise(den);
    secs[nb].id = PEL_SEC_DENOISE;
    secs[nb].data = den;
    secs[nb].size = (uint32_t)sizeof(*den);
    nb++;
    fill_filmgrain(grain);
    secs[nb].id = PEL_SEC_FILMGRAIN;
    secs[nb].data = grain;
    secs[nb].size = (uint32_t)sizeof(*grain);
    nb++;
    fill_motion(mo);
    secs[nb].id = PEL_SEC_MOTION;
    secs[nb].data = mo;
    secs[nb].size = (uint32_t)sizeof(*mo);
    nb++;
    *out_nb = nb;
}

#if PELORUS_ABI_MINOR >= 3
static void pack_sections_minor3(PelorusPackSection *secs, int *nb, PelorusQpReportSection *qp,
                                 PelorusMotionConfSection *conf, PelorusComplexitySection *cx)
{
    int i = *nb;
    fill_qpreport(qp);
    secs[i].id = PEL_SEC_QPREPORT;
    secs[i].data = qp;
    secs[i].size = (uint32_t)sizeof(*qp);
    i++;
    fill_motion_conf(conf);
    secs[i].id = PEL_SEC_MOTION_CONF;
    secs[i].data = conf;
    secs[i].size = (uint32_t)sizeof(*conf);
    i++;
    fill_complexity(cx);
    secs[i].id = PEL_SEC_COMPLEXITY;
    secs[i].data = cx;
    secs[i].size = (uint32_t)sizeof(*cx);
    i++;
    *nb = i;
}
#endif

#if PELORUS_ABI_MINOR >= 4
static void pack_sections_minor4(PelorusPackSection *secs, int *nb, PelorusEncTelemetrySection *tlm,
                                 PelorusEncodeRecordSection *rec)
{
    int i = *nb;
    fill_enc_telemetry(tlm);
    secs[i].id = PEL_SEC_ENC_TELEMETRY;
    secs[i].data = tlm;
    secs[i].size = (uint32_t)sizeof(*tlm);
    i++;
    fill_encode_record(rec);
    secs[i].id = PEL_SEC_ENCODE_RECORD;
    secs[i].data = rec;
    secs[i].size = (uint32_t)sizeof(*rec);
    i++;
    *nb = i;
}
#endif

#if PELORUS_ABI_MINOR >= 6
static void pack_sections_minor6(PelorusPackSection *secs, int *nb, PelorusQualitySection *qual)
{
    int i = *nb;
    fill_quality(qual);
    secs[i].id = PEL_SEC_QUALITY;
    secs[i].data = qual;
    secs[i].size = (uint32_t)sizeof(*qual);
    i++;
    *nb = i;
}
#endif

static void append_base_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                             PelorusBandingSection *band, PelorusVarianceSection *var,
                             PelorusMotionSection *mo)
{
    const void *p = NULL;
    size_t got = 0;
    band->cell_data_offset =
        append_map(blob, cap, end, val_banding_cell_data, (uint32_t)sizeof(val_banding_cell_data));
    (void)pel_blob_find_section(blob, packed_len, PEL_SEC_BANDING, sizeof(*band), &p, &got);
    patch_section(blob, p, band, sizeof(*band));

    var->var_cell_offset =
        append_map(blob, cap, end, val_var_cell_data, (uint32_t)sizeof(val_var_cell_data));
    var->edge_cell_offset =
        append_map(blob, cap, end, val_edge_cell_data, (uint32_t)sizeof(val_edge_cell_data));
    (void)pel_blob_find_section(blob, packed_len, PEL_SEC_VARIANCE, sizeof(*var), &p, &got);
    patch_section(blob, p, var, sizeof(*var));

    mo->mv_field_offset =
        append_map(blob, cap, end, val_mv_field_data, (uint32_t)sizeof(val_mv_field_data));
    (void)pel_blob_find_section(blob, packed_len, PEL_SEC_MOTION, sizeof(*mo), &p, &got);
    patch_section(blob, p, mo, sizeof(*mo));
}

#if PELORUS_ABI_MINOR >= 3
static void append_minor3_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                               PelorusQpReportSection *qp, PelorusMotionConfSection *conf)
{
    const void *p = NULL;
    size_t got = 0;
    qp->qp_cell_offset =
        append_map(blob, cap, end, val_qp_cell_data, (uint32_t)sizeof(val_qp_cell_data));
    (void)pel_blob_find_section(blob, packed_len, PEL_SEC_QPREPORT, sizeof(*qp), &p, &got);
    patch_section(blob, p, qp, sizeof(*qp));

    conf->conf_field_offset =
        append_map(blob, cap, end, val_conf_field_data, (uint32_t)sizeof(val_conf_field_data));
    (void)pel_blob_find_section(blob, packed_len, PEL_SEC_MOTION_CONF, sizeof(*conf), &p, &got);
    patch_section(blob, p, conf, sizeof(*conf));
}
#endif

#if PELORUS_ABI_MINOR >= 4
static void append_minor4_maps(uint8_t *blob, size_t cap, size_t packed_len, uint32_t *end,
                               PelorusEncTelemetrySection *tlm)
{
    const void *p = NULL;
    size_t got = 0;
    tlm->qp_map_offset =
        append_map(blob, cap, end, val_tlm_qp_map_data, (uint32_t)sizeof(val_tlm_qp_map_data));
    tlm->mode_map_offset =
        append_map(blob, cap, end, val_tlm_mode_map_data, (uint32_t)sizeof(val_tlm_mode_map_data));
    (void)pel_blob_find_section(blob, packed_len, PEL_SEC_ENC_TELEMETRY, sizeof(*tlm), &p, &got);
    patch_section(blob, p, tlm, sizeof(*tlm));
}
#endif

typedef struct HistorySections {
    PelorusBandingSection band;
    PelorusVarianceSection var;
    PelorusDenoiseSection den;
    PelorusFilmGrainSection grain;
    PelorusMotionSection mo;
#if PELORUS_ABI_MINOR >= 3
    PelorusQpReportSection qp;
    PelorusMotionConfSection conf;
    PelorusComplexitySection cx;
#endif
#if PELORUS_ABI_MINOR >= 4
    PelorusEncTelemetrySection tlm;
    PelorusEncodeRecordSection rec;
#endif
#if PELORUS_ABI_MINOR >= 6
    PelorusQualitySection qual;
#endif
} HistorySections;

static void append_all_maps(uint8_t *blob_out, size_t cap, size_t packed_len, uint32_t *end,
                            HistorySections *s)
{
    append_base_maps(blob_out, cap, packed_len, end, &s->band, &s->var, &s->mo);
#if PELORUS_ABI_MINOR >= 3
    append_minor3_maps(blob_out, cap, packed_len, end, &s->qp, &s->conf);
#endif
#if PELORUS_ABI_MINOR >= 4
    append_minor4_maps(blob_out, cap, packed_len, end, &s->tlm);
#endif
}

static void setup_all_sections(PelorusPackSection *secs, int *nb, HistorySections *s)
{
    pack_sections_setup(secs, nb, &s->band, &s->var, &s->den, &s->grain, &s->mo);
#if PELORUS_ABI_MINOR >= 3
    pack_sections_minor3(secs, nb, &s->qp, &s->conf, &s->cx);
#endif
#if PELORUS_ABI_MINOR >= 4
    pack_sections_minor4(secs, nb, &s->tlm, &s->rec);
#endif
#if PELORUS_ABI_MINOR >= 6
    pack_sections_minor6(secs, nb, &s->qual);
#endif
}

static void init_meta(PelorusSideData *meta)
{
    memset(meta, 0, sizeof(*meta));
    meta->frame_pts = VAL_FRAME_PTS;
    meta->plane_layout = (uint8_t)VAL_PLANE_LAYOUT;
    meta->bit_depth = (uint8_t)VAL_BIT_DEPTH;
    meta->grid_cols = (uint16_t)VAL_GRID_COLS;
    meta->grid_rows = (uint16_t)VAL_GRID_ROWS;
    meta->producer_id = VAL_PRODUCER_ID;
}

static pel_result invoke_pack(const PelorusSideData *meta, const PelorusPackSection *secs, int nb,
                              uint8_t *blob_out, size_t cap, size_t *out_packed_len)
{
    uint8_t *packed = NULL;
    size_t packed_len = 0;
    pel_result rc = pel_blob_pack(meta, secs, nb, &packed, &packed_len);
    if (rc != PEL_OK) {
        return rc;
    }
    if (packed_len > cap) {
        pel_blob_free(packed);
        return PEL_ERR_RANGE;
    }
    memset(blob_out, 0, cap);
    memcpy(blob_out, packed, packed_len);
    pel_blob_free(packed);
    *out_packed_len = packed_len;
    return PEL_OK;
}

static pel_result pack_history_blob(uint8_t *blob_out, size_t cap, size_t *out_len)
{
    PelorusSideData meta;
    PelorusSideData hdr;
    PelorusPackSection secs[16];
    HistorySections s;
    size_t packed_len = 0;
    int nb = 0;
    uint32_t end;
    pel_result rc;

    init_meta(&meta);
    setup_all_sections(secs, &nb, &s);
    rc = invoke_pack(&meta, secs, nb, blob_out, cap, &packed_len);
    if (rc != PEL_OK) {
        return rc;
    }
    end = (uint32_t)(packed_len - PELORUS_SIDEDATA_UUID_LEN);
    append_all_maps(blob_out, cap, packed_len, &end, &s);

    memcpy(&hdr, blob_out + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    hdr.total_size = end;
    memcpy(blob_out + PELORUS_SIDEDATA_UUID_LEN, &hdr, sizeof(hdr));
    *out_len = (size_t)PELORUS_SIDEDATA_UUID_LEN + end;
    return PEL_OK;
}

static void emit_hex(const uint8_t *data, size_t len)
{
    static const char hex_chars[] = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        (void)putchar(hex_chars[((unsigned int)data[i] >> 4u) & 0x0fu]);
        (void)putchar(hex_chars[(unsigned int)data[i] & 0x0fu]);
    }
    (void)putchar('\n');
}

static int parse_uint32(const char *s, uint32_t *out)
{
    uint64_t val = 0;
    if (s == NULL || *s == '\0') {
        return 0;
    }
    for (size_t i = 0; s[i] != '\0' && i < 16; i++) {
        if (s[i] < '0' || s[i] > '9') {
            return 0;
        }
        val = val * 10 + (uint64_t)(s[i] - '0');
        if (val > UINT32_MAX) {
            return 0;
        }
    }
    *out = (uint32_t)val;
    return 1;
}

static int check_map_bytes(const uint8_t *blob, size_t len, uint32_t offset, uint32_t size,
                           uint32_t elem_count, uint32_t elem_size, const void *expected,
                           const char *name)
{
#if PELORUS_ABI_MINOR >= 4
    const void *mapped = NULL;
    pel_result rc = pel_blob_map(blob, len, offset, size, elem_count, elem_size, &mapped);
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: map %s pel_blob_map error %d\n", name, (int)rc);
        return 1;
    }
    if (memcmp(mapped, expected, size) != 0) {
        (void)fprintf(stderr, "FAIL: map %s data mismatch\n", name);
        return 1;
    }
    return 0;
#else
    PelorusSideData hdr;
    if (blob == NULL || expected == NULL || elem_count == 0u || elem_size == 0u) {
        return 1;
    }
    if ((uint64_t)elem_count * (uint64_t)elem_size != (uint64_t)size) {
        (void)fprintf(stderr, "FAIL: map %s bad size\n", name);
        return 1;
    }
    if ((offset & 7u) != 0u) {
        (void)fprintf(stderr, "FAIL: map %s misaligned offset\n", name);
        return 1;
    }
    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    if ((uint64_t)offset <
        (uint64_t)hdr.header_size + (uint64_t)hdr.section_count * sizeof(PelorusSectionDir)) {
        (void)fprintf(stderr, "FAIL: map %s inside header/dir\n", name);
        return 1;
    }
    if (offset > hdr.total_size || size > hdr.total_size - offset) {
        (void)fprintf(stderr, "FAIL: map %s outside total_size\n", name);
        return 1;
    }
    if (memcmp(blob + PELORUS_SIDEDATA_UUID_LEN + offset, expected, size) != 0) {
        (void)fprintf(stderr, "FAIL: map %s data mismatch\n", name);
        return 1;
    }
    (void)len;
    return 0;
#endif
}

static int check_banding(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusBandingSection expected;
    const PelorusBandingSection *actual;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: banding section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusBandingSection)) {
        (void)fprintf(stderr, "FAIL: banding size %zu != %zu\n", got,
                      sizeof(PelorusBandingSection));
        return 1;
    }
    fill_banding(&expected);
    actual = (const PelorusBandingSection *)p;
    expected.cell_data_offset = actual->cell_data_offset;
    expected.cell_data_size = actual->cell_data_size;
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, actual, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: banding fields mismatch\n");
        return 1;
    }
    return check_map_bytes(blob, len, actual->cell_data_offset, actual->cell_data_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_banding_cell_data,
                           "banding");
}

static int check_variance(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusVarianceSection expected;
    const PelorusVarianceSection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_VARIANCE,
                                          sizeof(PelorusVarianceSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: variance section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusVarianceSection)) {
        (void)fprintf(stderr, "FAIL: variance size %zu != %zu\n", got,
                      sizeof(PelorusVarianceSection));
        return 1;
    }
    fill_variance(&expected);
    actual = (const PelorusVarianceSection *)p;
    expected.var_cell_offset = actual->var_cell_offset;
    expected.var_cell_size = actual->var_cell_size;
    expected.edge_cell_offset = actual->edge_cell_offset;
    expected.edge_cell_size = actual->edge_cell_size;
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, actual, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: variance fields mismatch\n");
        return 1;
    }
    if (check_map_bytes(blob, len, actual->var_cell_offset, actual->var_cell_size, VAL_CELL_COUNT,
                        (uint32_t)sizeof(float), val_var_cell_data, "variance_var") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->edge_cell_offset, actual->edge_cell_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_edge_cell_data,
                           "variance_edge");
}

static int check_denoise(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusDenoiseSection expected;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_DENOISE, sizeof(PelorusDenoiseSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: denoise section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusDenoiseSection)) {
        (void)fprintf(stderr, "FAIL: denoise size %zu != %zu\n", got,
                      sizeof(PelorusDenoiseSection));
        return 1;
    }
    fill_denoise(&expected);
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, p, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: denoise fields mismatch\n");
        return 1;
    }
    return 0;
}

static int check_filmgrain(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusFilmGrainSection expected;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN,
                                          sizeof(PelorusFilmGrainSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: filmgrain section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusFilmGrainSection)) {
        (void)fprintf(stderr, "FAIL: filmgrain size %zu != %zu\n", got,
                      sizeof(PelorusFilmGrainSection));
        return 1;
    }
    fill_filmgrain(&expected);
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, p, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: filmgrain fields mismatch\n");
        return 1;
    }
    return 0;
}

static int check_motion(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    size_t w_size = (w_minor < 4u) ? 32u : 36u;
    size_t exp_got = min_size(sizeof(PelorusMotionSection), w_size);
    PelorusMotionSection expected;
    const PelorusMotionSection *actual;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p, &got);
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: motion section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != exp_got) {
        (void)fprintf(stderr, "FAIL: motion got %zu != exp %zu\n", got, exp_got);
        return 1;
    }
    fill_motion(&expected);
    actual = (const PelorusMotionSection *)p;
    expected.mv_field_offset = actual->mv_field_offset;
    expected.mv_field_size = actual->mv_field_size;
    if (memcmp(&expected, actual, got) != 0) {
        (void)fprintf(stderr, "FAIL: motion fields mismatch\n");
        return 1;
    }
    return check_map_bytes(blob, len, actual->mv_field_offset, actual->mv_field_size,
                           VAL_CELL_COUNT, (uint32_t)(2u * sizeof(int16_t)), val_mv_field_data,
                           "motion_mv");
}

#if PELORUS_ABI_MINOR >= 3
static int check_qpreport(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusQpReportSection expected;
    const PelorusQpReportSection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_QPREPORT,
                                          sizeof(PelorusQpReportSection), &p, &got);
    if (w_minor < 3u) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: qpreport should be absent for minor %u\n",
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != sizeof(PelorusQpReportSection)) {
        (void)fprintf(stderr, "FAIL: qpreport missing or wrong size (rc=%d got=%zu)\n", (int)rc,
                      got);
        return 1;
    }
    fill_qpreport(&expected);
    actual = (const PelorusQpReportSection *)p;
    expected.qp_cell_offset = actual->qp_cell_offset;
    expected.qp_cell_size = actual->qp_cell_size;
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, actual, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: qpreport fields mismatch\n");
        return 1;
    }
    return check_map_bytes(blob, len, actual->qp_cell_offset, actual->qp_cell_size, VAL_CELL_COUNT,
                           (uint32_t)sizeof(int8_t), val_qp_cell_data, "qp_report");
}

static int check_motion_conf(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusMotionConfSection expected;
    const PelorusMotionConfSection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_MOTION_CONF,
                                          sizeof(PelorusMotionConfSection), &p, &got);
    if (w_minor < 3u) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: motion_conf should be absent for minor %u\n",
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != sizeof(PelorusMotionConfSection)) {
        (void)fprintf(stderr, "FAIL: motion_conf missing or wrong size (rc=%d got=%zu)\n", (int)rc,
                      got);
        return 1;
    }
    fill_motion_conf(&expected);
    actual = (const PelorusMotionConfSection *)p;
    expected.conf_field_offset = actual->conf_field_offset;
    expected.conf_field_size = actual->conf_field_size;
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, actual, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: motion_conf fields mismatch\n");
        return 1;
    }
    return check_map_bytes(blob, len, actual->conf_field_offset, actual->conf_field_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_conf_field_data,
                           "motion_conf");
}

static int check_complexity(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusComplexitySection expected;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_COMPLEXITY,
                                          sizeof(PelorusComplexitySection), &p, &got);
    if (w_minor < 3u) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: complexity should be absent for minor %u\n",
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != sizeof(PelorusComplexitySection)) {
        (void)fprintf(stderr, "FAIL: complexity missing or wrong size (rc=%d got=%zu)\n", (int)rc,
                      got);
        return 1;
    }
    fill_complexity(&expected);
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, p, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: complexity fields mismatch\n");
        return 1;
    }
    return 0;
}
#endif

#if PELORUS_ABI_MINOR >= 4
static int check_enc_telemetry(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusEncTelemetrySection expected;
    const PelorusEncTelemetrySection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_ENC_TELEMETRY,
                                          sizeof(PelorusEncTelemetrySection), &p, &got);
    if (w_minor < 4u) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: telemetry should be absent for minor %u\n",
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != sizeof(PelorusEncTelemetrySection)) {
        (void)fprintf(stderr, "FAIL: telemetry missing or wrong size (rc=%d got=%zu)\n", (int)rc,
                      got);
        return 1;
    }
    fill_enc_telemetry(&expected);
    actual = (const PelorusEncTelemetrySection *)p;
    expected.qp_map_offset = actual->qp_map_offset;
    expected.qp_map_size = actual->qp_map_size;
    expected.mode_map_offset = actual->mode_map_offset;
    expected.mode_map_size = actual->mode_map_size;
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, actual, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: telemetry fields mismatch\n");
        return 1;
    }
    if (check_map_bytes(blob, len, actual->qp_map_offset, actual->qp_map_size, VAL_CELL_COUNT,
                        (uint32_t)sizeof(int16_t), val_tlm_qp_map_data, "tlm_qp") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->mode_map_offset, actual->mode_map_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_tlm_mode_map_data,
                           "tlm_mode");
}

static int check_encode_record(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusEncodeRecordSection expected;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_ENCODE_RECORD,
                                          sizeof(PelorusEncodeRecordSection), &p, &got);
    if (w_minor < 4u) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: record should be absent for minor %u\n",
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != sizeof(PelorusEncodeRecordSection)) {
        (void)fprintf(stderr, "FAIL: record missing or wrong size (rc=%d got=%zu)\n", (int)rc, got);
        return 1;
    }
    fill_encode_record(&expected);
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, p, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: record fields mismatch\n");
        return 1;
    }
    return 0;
}
#endif

#if PELORUS_ABI_MINOR >= 6
static int check_quality(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusQualitySection expected;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_QUALITY, sizeof(PelorusQualitySection), &p, &got);
    if (w_minor < 6u) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: quality should be absent for minor %u\n",
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != sizeof(PelorusQualitySection)) {
        (void)fprintf(stderr, "FAIL: quality missing or wrong size (rc=%d got=%zu)\n", (int)rc,
                      got);
        return 1;
    }
    fill_quality(&expected);
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(&expected, p, sizeof(expected)) != 0) {
        (void)fprintf(stderr, "FAIL: quality fields mismatch\n");
        return 1;
    }
    return 0;
}
#endif

static int check_all_sections(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    if (check_banding(blob, len, w_minor) != 0 || check_variance(blob, len, w_minor) != 0 ||
        check_denoise(blob, len, w_minor) != 0 || check_filmgrain(blob, len, w_minor) != 0 ||
        check_motion(blob, len, w_minor) != 0) {
        return 1;
    }
#if PELORUS_ABI_MINOR >= 3
    if (check_qpreport(blob, len, w_minor) != 0 || check_motion_conf(blob, len, w_minor) != 0 ||
        check_complexity(blob, len, w_minor) != 0) {
        return 1;
    }
#endif
#if PELORUS_ABI_MINOR >= 4
    if (check_enc_telemetry(blob, len, w_minor) != 0 ||
        check_encode_record(blob, len, w_minor) != 0) {
        return 1;
    }
#endif
#if PELORUS_ABI_MINOR >= 6
    if (check_quality(blob, len, w_minor) != 0) {
        return 1;
    }
#endif
    return 0;
}

static int read_and_verify_blob(const uint8_t *blob, size_t len)
{
    PelorusSideData hdr;
    if (pel_blob_is_present(blob, len) != 1) {
        (void)fprintf(stderr, "FAIL: pel_blob_is_present != 1\n");
        return 1;
    }
    if (len < (size_t)PELORUS_SIDEDATA_UUID_LEN + sizeof(PelorusSideData)) {
        (void)fprintf(stderr, "FAIL: blob too short for header\n");
        return 1;
    }
    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    if (memcmp(hdr.magic, PELORUS_MAGIC_STR, PELORUS_MAGIC_LEN) != 0 ||
        hdr.abi_major != (uint16_t)PELORUS_ABI_MAJOR || hdr.frame_pts != VAL_FRAME_PTS ||
        hdr.plane_layout != (uint8_t)VAL_PLANE_LAYOUT || hdr.bit_depth != (uint8_t)VAL_BIT_DEPTH ||
        hdr.grid_cols != (uint16_t)VAL_GRID_COLS || hdr.grid_rows != (uint16_t)VAL_GRID_ROWS ||
        hdr.producer_id != VAL_PRODUCER_ID || hdr.total_size > len - PELORUS_SIDEDATA_UUID_LEN ||
        hdr.header_size < sizeof(PelorusSideData)) {
        (void)fprintf(stderr, "FAIL: header field mismatch or truncation\n");
        return 1;
    }
    return check_all_sections(blob, len, hdr.abi_minor);
}

static int read_file_to_buf(const char *path, uint8_t *buf, size_t cap, size_t *out_len)
{
    FILE *fp = test_fopen_safe(path, "rb");
    size_t n;
    if (fp == NULL) {
        return 2;
    }
    n = fread(buf, 1, cap, fp);
    if (ferror(fp) || !feof(fp)) {
        (void)fclose(fp);
        return 2;
    }
    (void)fclose(fp);
    *out_len = n;
    return 0;
}

static int check_file_match(const char *path, const uint8_t *expected, size_t expected_len)
{
    uint8_t buf[16384];
    size_t file_len = 0;
    int rc = read_file_to_buf(path, buf, sizeof(buf), &file_len);
    if (rc != 0) {
        (void)fprintf(stderr, "FAIL: missing fixture %s\n", path);
        return 1;
    }
    if (file_len != expected_len || memcmp(buf, expected, expected_len) != 0) {
        (void)fprintf(stderr, "FAIL: %s does not match writer output\n", path);
        return 1;
    }
    return 0;
}

static int do_check_writer(const char *fixtures_dir)
{
    uint8_t plain[16384];
    size_t plain_len = 0;
    char path[4096];
    int written;
    if (pack_history_blob(plain, sizeof(plain), &plain_len) != PEL_OK) {
        (void)fprintf(stderr, "FAIL: pack_history_blob failed\n");
        return 1;
    }
    written =
        snprintf(path, sizeof(path), "%s/abi-1.%u.bin", fixtures_dir, (unsigned)PELORUS_ABI_MINOR);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return 2;
    }
    if (check_file_match(path, plain, plain_len) != 0) {
        return 1;
    }
#if PELORUS_ABI_MINOR >= 5
    {
        uint8_t carrier[32768];
        size_t carrier_len = 0;
        pel_result cr =
            pel_blob_carrier_encode(plain, plain_len, carrier, sizeof(carrier), &carrier_len);
        if (cr != PEL_OK) {
            (void)fprintf(stderr, "FAIL: carrier encode failed (rc=%d)\n", (int)cr);
            return 1;
        }
        written = snprintf(path, sizeof(path), "%s/abi-1.%u-carrier.bin", fixtures_dir,
                           (unsigned)PELORUS_ABI_MINOR);
        if (written < 0 || (size_t)written >= sizeof(path)) {
            return 2;
        }
        if (check_file_match(path, carrier, carrier_len) != 0) {
            return 1;
        }
    }
#endif
    return 0;
}

static int do_read_blob(const char *path, int has_flip, uint32_t flip_off)
{
    uint8_t buf[32768];
    size_t len = 0;
    int rc = read_file_to_buf(path, buf, sizeof(buf), &len);
    if (rc != 0) {
        (void)fprintf(stderr, "IO error: %s\n", path);
        return 2;
    }
    if (has_flip) {
        if ((size_t)flip_off >= len) {
            (void)fprintf(stderr, "Flip offset %u >= len %zu\n", flip_off, len);
            return 2;
        }
        buf[flip_off] ^= (uint8_t)0x01u;
    }
    if (len >= 16 && memcmp(buf, k_carrier_uuid, 16) == 0) {
#if PELORUS_ABI_MINOR < 5
        const void *p = NULL;
        size_t got = 0;
        if (pel_blob_is_present(buf, len) == 0 &&
            pel_blob_find_section(buf, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                                  &got) != PEL_OK) {
            return 0;
        }
        (void)fprintf(stderr, "FAIL: carrier not ignored by pre-1.5 reader\n");
        return 1;
#else
        const uint8_t *unwrapped = NULL;
        size_t unwrapped_len = 0;
        uint8_t scratch[32768];
        pel_result cr =
            pel_blob_unwrap(buf, len, scratch, sizeof(scratch), &unwrapped, &unwrapped_len);
        if (cr != PEL_OK) {
            (void)fprintf(stderr, "FAIL: pel_blob_unwrap error %d\n", (int)cr);
            return 1;
        }
        return read_and_verify_blob(unwrapped, unwrapped_len);
#endif
    }
    return read_and_verify_blob(buf, len);
}

static int do_emit(int argc, char **argv)
{
    uint8_t plain[16384];
    size_t plain_len = 0;
    if (argc != 3) {
        return 2;
    }
    if (pack_history_blob(plain, sizeof(plain), &plain_len) != PEL_OK) {
        return 1;
    }
    if (strcmp(argv[2], "plain") == 0) {
        emit_hex(plain, plain_len);
        return 0;
    }
    if (strcmp(argv[2], "carrier") == 0) {
#if PELORUS_ABI_MINOR >= 5
        uint8_t carrier[32768];
        size_t carrier_len = 0;
        if (pel_blob_carrier_encode(plain, plain_len, carrier, sizeof(carrier), &carrier_len) !=
            PEL_OK) {
            return 1;
        }
        emit_hex(carrier, carrier_len);
        return 0;
#else
        (void)fprintf(stderr, "carrier mode requires PELORUS_ABI_MINOR >= 5 (current %u)\n",
                      (unsigned)PELORUS_ABI_MINOR);
        return 2;
#endif
    }
    return 2;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        (void)fprintf(stderr, "Usage: abi_history emit|check-writer|read ...\n");
        return 2;
    }
    if (strcmp(argv[1], "emit") == 0) {
        return do_emit(argc, argv);
    }
    if (strcmp(argv[1], "check-writer") == 0) {
        if (argc != 3) {
            return 2;
        }
        return do_check_writer(argv[2]);
    }
    if (strcmp(argv[1], "read") == 0) {
        int has_flip = 0;
        uint32_t flip_off = 0;
        if (argc != 3 && argc != 5) {
            return 2;
        }
        if (argc == 5) {
            if (strcmp(argv[3], "--flip") != 0 || !parse_uint32(argv[4], &flip_off)) {
                return 2;
            }
            has_flip = 1;
        }
        return do_read_blob(argv[2], has_flip, flip_off);
    }
    (void)fprintf(stderr, "Unknown mode: %s\n", argv[1]);
    return 2;
}
