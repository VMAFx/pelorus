/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_ABI_HISTORY_VALUES_H
#define PELORUS_ABI_HISTORY_VALUES_H

#include <stddef.h>
#include <stdint.h>

/* Header framing constants */
#define VAL_FRAME_PTS 1000000ULL
#define VAL_PLANE_LAYOUT 0u
#define VAL_BIT_DEPTH 10u
#define VAL_GRID_COLS 4u
#define VAL_GRID_ROWS 2u
#define VAL_CELL_COUNT 8u
#define VAL_PRODUCER_ID 0x53524c50u /* PEL_FOURCC('P', 'L', 'R', 'S') */

/* (a) Banding section */
#define VAL_BANDING_GLOBAL_RISK 0.25f
#define VAL_BANDING_FLAT_AREA 0.5f
#define VAL_BANDING_CONTOUR_STRENGTH 0.125f
#define VAL_BANDING_DOMINANT_LUMA 0.625f
static const uint8_t val_banding_cell_data[8] = {1, 2, 3, 4, 5, 6, 7, 8};

/* (b) Variance section */
#define VAL_VARIANCE_GLOBAL 0.75f
#define VAL_VARIANCE_EDGE_DENSITY 0.375f
#define VAL_VARIANCE_TEXTURE_ENERGY 0.5f
static const float val_var_cell_data[8] = {0.125f, 0.25f, 0.375f, 0.5f,
                                           0.625f, 0.75f, 0.875f, 1.0f};
static const uint8_t val_edge_cell_data[8] = {10, 20, 30, 40, 50, 60, 70, 80};

/* (c) Denoise section */
#define VAL_DENOISE_RESIDUAL_Y 0.125f
#define VAL_DENOISE_RESIDUAL_U 0.25f
#define VAL_DENOISE_RESIDUAL_V 0.375f
#define VAL_DENOISE_APPLIED_STRENGTH 0.5f
#define VAL_DENOISE_NOISE_SIGMA 0.625f
#define VAL_DENOISE_PSNR 38.5f
#define VAL_DENOISE_ID 1u

/* (d) FilmGrain section */
#define VAL_GRAIN_SEED 0x123456789ABCDEF0ULL
#define VAL_GRAIN_NUM_Y_POINTS 2
#define VAL_GRAIN_NUM_UV_POINTS 1
#define VAL_GRAIN_SCALING_SHIFT 8
#define VAL_GRAIN_AR_COEFF_LAG 1
#define VAL_GRAIN_AR_COEFF_SHIFT 6
#define VAL_GRAIN_GRAIN_SCALE_SHIFT 0
#define VAL_GRAIN_APPLY 1u
#define VAL_GRAIN_OVERLAP_FLAG 1u
#define VAL_GRAIN_LIMIT_OUTPUT_RANGE 1u
#define VAL_GRAIN_MODEL 1u

/* (e) Motion section */
#define VAL_MOTION_GLOBAL_X 0.5f
#define VAL_MOTION_GLOBAL_Y (-0.25f)
#define VAL_MOTION_MAGNITUDE_MEAN 0.625f
#define VAL_MOTION_MAGNITUDE_P95 1.0f
#define VAL_MOTION_ENTROPY 0.375f
#define VAL_MOTION_BLOCK_SIZE_LOG2 3u
static const int16_t val_mv_field_data[8][2] = {{1, 2},   {3, 4},   {5, 6},   {7, 8},
                                                {-1, -2}, {-3, -4}, {-5, -6}, {-7, -8}};

/* (f) QpReport section (ABI 1.1 / Minor >= 3) */
#define VAL_QPREPORT_AVG_QP 28.5f
#define VAL_QPREPORT_PSNR_Y 42.0f
#define VAL_QPREPORT_PSNR_U 43.5f
#define VAL_QPREPORT_PSNR_V 44.25f
#define VAL_QPREPORT_TOTAL_BITS 987654ULL
#define VAL_QPREPORT_NUM_INTRA 12u
#define VAL_QPREPORT_NUM_INTER 34u
#define VAL_QPREPORT_NUM_SKIPPED 56u
#define VAL_QPREPORT_HONORED_FRACTION 0.75f
#define VAL_QPREPORT_REPORT_SOURCE 1u
#define VAL_QPREPORT_BLOCK_SIZE_LOG2 4u
#define VAL_QPREPORT_QP_VALID 1u
static const int8_t val_qp_cell_data[8] = {20, 22, 24, 26, 28, 30, 32, 34};

/* (g) MotionConf section (ABI 1.2 / Minor >= 3) */
#define VAL_MOTION_CONF_METRIC 0u
static const uint8_t val_conf_field_data[8] = {200, 210, 220, 230, 240, 245, 250, 255};

/* (h) Complexity section (ABI 1.3 / Minor >= 3) */
#define VAL_COMPLEXITY_VALUE 0.625f
#define VAL_COMPLEXITY_TEXTURE 0.5f
#define VAL_COMPLEXITY_MOTION 0.25f

/* (i) EncTelemetry section (ABI 1.4 / Minor >= 4) */
#define VAL_TLM_PRESENT_MASK 0x28c027ULL
#define VAL_TLM_DISPLAY_INDEX 4u
#define VAL_TLM_DECODE_INDEX 2u
#define VAL_TLM_FRAME_BYTES 15360u
#define VAL_TLM_AVG_QP 28.5f
#define VAL_TLM_MAP_COLS 4u
#define VAL_TLM_MAP_ROWS 2u
#define VAL_TLM_CODEC 2u
#define VAL_TLM_QP_SCALE 1u
#define VAL_TLM_PICTURE_TYPE 2u
#define VAL_TLM_FRAME_FLAGS 0u
#define VAL_TLM_GRANULARITY 3u
#define VAL_TLM_BLOCK_SIZE_LOG2 4u
#define VAL_TLM_CODED_BIT_DEPTH 10u
#define VAL_TLM_ADAPTER 10u
static const int16_t val_tlm_qp_map_data[8] = {112, 116, 120, 124, 128, 132, 136, 140};
static const uint8_t val_tlm_mode_map_data[8] = {1, 2, 2, 3, 3, 2, 1, 0};

/* (j) EncodeRecord section (ABI 1.4 / Minor >= 4) */
#define VAL_RECORD_DIGEST_ALG 1u
#define VAL_RECORD_LOCATOR_KIND 0u
#define VAL_RECORD_MAJOR 1u
static const uint8_t val_record_digest_data[32] = {1,  2,  3,  4,  5,  6,  7,  8,  9,  10, 11,
                                                   12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
                                                   23, 24, 25, 26, 27, 28, 29, 30, 31, 32};

/* (k) Quality section (ABI 1.6 / Minor >= 6) */
#define VAL_QUALITY_WINDOW_ID 42ULL
#define VAL_QUALITY_PTS_START 1000LL
#define VAL_QUALITY_PTS_END 2000LL
#define VAL_QUALITY_TB_NUM 1u
#define VAL_QUALITY_TB_DEN 25u
#define VAL_QUALITY_PROVENANCE_ID 999ULL
#define VAL_QUALITY_SCORE 94.5
#define VAL_QUALITY_METRIC_ID 1u
#define VAL_QUALITY_LATENCY_FRAMES 2u
#define VAL_QUALITY_FRAME_COUNT 25u
static const uint8_t val_quality_model_hash_data[32] = {
    0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19,
    0x1a, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x29};

#endif /* PELORUS_ABI_HISTORY_VALUES_H */
