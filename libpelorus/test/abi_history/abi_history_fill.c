/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

/**
 * Deterministic test value population functions and static assertions for
 * all PelorusSideData sections across release ABI minors.
 */

#include <string.h>

#include "abi_history.h"
#include "abi_history_values.h"

/**
 * Populate a PelorusBandingSection with deterministic test values.
 */
void abih_fill_banding(PelorusBandingSection *s)
{
    memset(s, 0, sizeof(*s));
    s->global_banding_risk = VAL_BANDING_GLOBAL_RISK;
    s->flat_area_fraction = VAL_BANDING_FLAT_AREA;
    s->contour_strength_mean = VAL_BANDING_CONTOUR_STRENGTH;
    s->dominant_band_luma = VAL_BANDING_DOMINANT_LUMA;
    s->cell_data_offset = 0;
    s->cell_data_size = (uint32_t)sizeof(val_banding_cell_data);
}

/**
 * Populate a PelorusVarianceSection with deterministic test values.
 */
void abih_fill_variance(PelorusVarianceSection *s)
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

/**
 * Populate a PelorusDenoiseSection with deterministic test values.
 */
void abih_fill_denoise(PelorusDenoiseSection *s)
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

/**
 * Populate a PelorusFilmGrainSection with deterministic test values.
 */
void abih_fill_filmgrain(PelorusFilmGrainSection *s)
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

/**
 * Populate a PelorusMotionSection with deterministic test values.
 */
void abih_fill_motion(PelorusMotionSection *s)
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
/**
 * Populate a PelorusQpReportSection with deterministic test values.
 */
void abih_fill_qpreport(PelorusQpReportSection *s)
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

/**
 * Populate a PelorusMotionConfSection with deterministic test values.
 */
void abih_fill_motion_conf(PelorusMotionConfSection *s)
{
    memset(s, 0, sizeof(*s));
    s->conf_metric = (uint8_t)VAL_MOTION_CONF_METRIC;
    s->conf_field_offset = 0;
    s->conf_field_size = (uint32_t)sizeof(val_conf_field_data);
}

/**
 * Populate a PelorusComplexitySection with deterministic test values.
 */
void abih_fill_complexity(PelorusComplexitySection *s)
{
    memset(s, 0, sizeof(*s));
    s->complexity = VAL_COMPLEXITY_VALUE;
    s->texture_energy = VAL_COMPLEXITY_TEXTURE;
    s->motion_component = VAL_COMPLEXITY_MOTION;
}
#endif

#if PELORUS_ABI_MINOR >= 4
_Static_assert(sizeof(val_record_digest_data) == sizeof(((PelorusEncodeRecordSection *)0)->digest),
               "val_record_digest_data size matches digest");

/**
 * Populate a PelorusEncTelemetrySection with deterministic test values.
 */
void abih_fill_enc_telemetry(PelorusEncTelemetrySection *s)
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

/**
 * Populate a PelorusEncodeRecordSection with deterministic test values.
 */
void abih_fill_encode_record(PelorusEncodeRecordSection *s)
{
    memset(s, 0, sizeof(*s));
    memcpy(s->digest, val_record_digest_data, sizeof(s->digest));
    s->digest_alg = (uint8_t)VAL_RECORD_DIGEST_ALG;
    s->locator_kind = (uint8_t)VAL_RECORD_LOCATOR_KIND;
    s->record_major = (uint8_t)VAL_RECORD_MAJOR;
}
#endif

#if PELORUS_ABI_MINOR >= 6
_Static_assert(sizeof(val_quality_model_hash_data) ==
                   sizeof(((PelorusQualitySection *)0)->model_hash),
               "val_quality_model_hash_data size matches model_hash");

/**
 * Populate a PelorusQualitySection with deterministic test values.
 */
void abih_fill_quality(PelorusQualitySection *s)
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
