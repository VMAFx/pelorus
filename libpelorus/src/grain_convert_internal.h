/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_GRAIN_CONVERT_INTERNAL_H
#define PELORUS_GRAIN_CONVERT_INTERNAL_H

#include <stdint.h>

/* Raw H.274 model-0 luma grain statistics for one cutoff pair, measured on
 * FFmpeg's SMPTE RDD 5 synthesizer (libavcodec/h274.c) at the pinned commit.
 * gain = grain RMS in 8-bit code values per unit of comp_model_value[0] at
 * log2_scale_factor 2; rho_h / rho_v = lag-1 correlation along x / y. */
typedef struct PelH274GrainStat {
    uint8_t cut_h;
    uint8_t cut_v;
    float gain;
    float rho_h;
    float rho_v;
} PelH274GrainStat;

#define PEL_H274_CUTOFF_MIN 2
#define PEL_H274_CUTOFF_MAX 14
#define PEL_H274_CUTOFF_COUNT 13
#define PEL_H274_STATS_LOG2_SCALE 2

/* Row (cut_v - 2) * 13 + (cut_h - 2). */
extern const PelH274GrainStat pel_h274_grain_stats[PEL_H274_CUTOFF_COUNT * PEL_H274_CUTOFF_COUNT];

#define PEL_QUANT_FLOOR_OFFSET 0.0f
#define PEL_QUANT_ROUND_OFFSET 0.5f

/* Population RMS of FFmpeg's 2048-entry gaussian_sequence in
 * libavcodec/aom_film_grain_template.c (pinned n9.0.2). */
#define PEL_AOM_GAUSSIAN_SEQUENCE_RMS 511.5251f

float pel_quantized_sigma(float sigma_lin, float bin_offset);

#endif /* PELORUS_GRAIN_CONVERT_INTERNAL_H */
