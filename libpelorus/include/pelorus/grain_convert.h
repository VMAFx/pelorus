/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_GRAIN_CONVERT_H
#define PELORUS_GRAIN_CONVERT_H

#include "pelorus/interop.h"
#include "pelorus/pelorus.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * H.274 film-grain parameters (SMPTE RDD 5 model 0 frequency filtering).
 * Mirrors FFmpeg AVFilmGrainH274Params for 8-bit additive grain.
 */
typedef struct PelorusH274GrainParams {
    int32_t model_id;          /* must be 0 (frequency filtering) */
    int32_t blending_mode_id;  /* must be 0 (additive) */
    int32_t log2_scale_factor; /* 2..7 (SMPTE RDD 5) */
    uint8_t component_model_present[3];
    uint16_t num_intensity_intervals[3]; /* 1..256 when present */
    uint8_t num_model_values[3];         /* 1..6 when present; [1], [2] default 8 */
    uint8_t intensity_interval_lower_bound[3][256];
    uint8_t intensity_interval_upper_bound[3][256];
    int16_t comp_model_value[3][256]
                            [6]; /* [0] scale 0..255, [1] cutoff_h 2..14, [2] cutoff_v 2..14 */
} PelorusH274GrainParams;

/**
 * Codec-neutral film-grain descriptor (8-bit code value domain).
 * Captures grain intensity-dependent RMS (sigma) and spatial autocorrelation (rho).
 */
typedef struct PelorusGrainDescriptor {
    uint8_t present[3];
    float sigma[3][256]; /* grain RMS at intensity index i */
    float rho_h[3];      /* lag-1 correlation along x */
    float rho_v[3];      /* lag-1 correlation along y */
} PelorusGrainDescriptor;

/**
 * Metric comparing two grain descriptors.
 */
typedef struct PelorusGrainFidelity {
    double
        sigma_rel_rms[3];  /* sqrt(mean_i (sa-sb)^2) / max(sqrt(mean_i sa^2), 1e-9); 0 if absent */
    double rho_h_delta[3]; /* |rho_h a - b|, 0 if both absent */
    double rho_v_delta[3]; /* |rho_v a - b|, 0 if both absent */
    double loss;           /* max over c of sigma_rel_rms + 0.5*(rho_h_delta + rho_v_delta) */
} PelorusGrainFidelity;

/**
 * Describe AV1 film grain parameters in the codec-neutral descriptor domain.
 *
 * Math (normative):
 * - Component presence: c=0: num_y_points > 0; c=1,2: chroma_scaling_from_luma || num_uv_points > 0.
 * - Piecewise-linear scaling[i] (0..255) generated per FFmpeg aom_film_grain_template.c.
 * - Chroma intensity index: cfl -> i; else clip(((i*uv_mult_luma + i*uv_mult) >> 6) + uv_offset, 0, 255).
 * - White grain RMS sigma_w = 511.5251 / 2^(4 + grain_scale_shift) (FFmpeg gaussian_sequence RMS).
 * - AR gain g_ar and correlations rho_h, rho_v from causal 2D impulse response on 64x64 grid.
 * - sigma[c][i] = scaling[index(i)] * sigma_w * g_ar / 2^scaling_shift.
 */
pel_result pel_grain_describe_aom(const PelorusFilmGrainSection *aom, PelorusGrainDescriptor *out);

/**
 * Describe H.274 model 0 film grain parameters in the codec-neutral descriptor domain.
 *
 * Math (normative):
 * - Component presence: component_model_present[c].
 * - First matching interval [lower, upper] determines scale, cut_h, cut_v.
 * - 4:2:0 chroma adaptation: scale >>= 1, cut_h = min(cut_h*2, 14), cut_v = min(cut_v*2, 14).
 * - sigma[c][i] = scale * stat.gain * 2^(2 - log2_scale_factor).
 * - rho_h[c], rho_v[c] = sigma^2-weighted mean of stat.rho_h / stat.rho_v over matching intervals.
 */
pel_result pel_grain_describe_h274(const PelorusH274GrainParams *h274, PelorusGrainDescriptor *out);

/**
 * Compare two film grain descriptors and compute fidelity loss.
 */
pel_result pel_grain_compare(const PelorusGrainDescriptor *a, const PelorusGrainDescriptor *b,
                             PelorusGrainFidelity *out);

/**
 * Deterministically convert AV1 film grain parameters to H.274 model 0 parameters.
 */
pel_result pel_grain_aom_to_h274(const PelorusFilmGrainSection *aom, PelorusH274GrainParams *out,
                                 PelorusGrainFidelity *fidelity /* may be NULL */);

/**
 * Deterministically convert H.274 model 0 film grain parameters to AV1 parameters.
 */
pel_result pel_grain_h274_to_aom(const PelorusH274GrainParams *h274, uint64_t seed,
                                 PelorusFilmGrainSection *out,
                                 PelorusGrainFidelity *fidelity /* may be NULL */);

#ifdef __cplusplus
}
#endif

#endif /* PELORUS_GRAIN_CONVERT_H */
