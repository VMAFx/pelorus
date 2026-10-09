/*
 * Copyright 2026 Lusoris
 *
 * This file is part of FFmpeg.
 *
 * FFmpeg is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at
 * your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
 * License for more details.
 */

/*
 * Host-side motion-vector arithmetic for vf_pelorus_mc_vulkan. Plain C with no
 * libav* dependency, so Pelorus's fast suite compiles and tests it directly
 * (ffmpeg-patches/test/mc_stats_test.c).
 *
 * MV unit contract. Every MV-carrying field names its unit:
 *
 *   shader output mv_x[] / mv_y[]      Q2 quarter-pel luma, stored round(pel * 4)
 *   PEL_SEC_MOTION grid (interop)      Q2 quarter-pel luma, int16 per component
 *   PelorusMotionSection scalars       luma pixels (float)
 *   shader input prev_mv[] (temporal)  INTEGER luma pel
 *   push constant gpred_x / gpred_y    INTEGER luma pel
 *
 * The shader searches on the integer-pel grid and refines the winner to Q2 only
 * on output. The host is the single conversion boundary: when it rolls this
 * frame's Q2 field into the next frame's predictors, it rounds to integer pel
 * with pel_mc_build_predictors(). Feeding the Q2 values straight back put the
 * predictors four times too far from the true motion.
 */

#ifndef AVFILTER_PELORUS_MC_STATS_H
#define AVFILTER_PELORUS_MC_STATS_H

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Q2 quarter-pel -> integer pel, rounding half away from zero. This is the
 * rounding the NVENC ME-hint consumer applies to the same grid, (q +/- 2) / 4,
 * so both consumers agree on every vector. The 64-bit intermediate keeps the
 * INT32_MIN / INT32_MAX edges from overflowing. */
static inline int32_t pel_mc_q2_to_pel(int32_t q2)
{
    const int64_t q = q2;

    return (int32_t)((q >= 0 ? q + 2 : q - 2) / 4);
}

/* Mean of n Q2 components (their sum is sum_q2) as an integer pel, rounding the
 * exact mean half away from zero. Returns 0 for an empty field. */
static inline int32_t pel_mc_mean_q2_to_pel(int64_t sum_q2, int n)
{
    if (n <= 0)
        return 0;
    return (int32_t)lround((double)sum_q2 / (4.0 * (double)n));
}

/* Build the next frame's search predictors from this frame's Q2 field:
 * prev_mv_pel receives the interleaved integer-pel (dx, dy) per block (2 * n
 * entries) and *gpred_x / *gpred_y the integer-pel mean vector. */
static inline void pel_mc_build_predictors(const int32_t *mvx_q2, const int32_t *mvy_q2, int n,
                                           int32_t *prev_mv_pel, int32_t *gpred_x, int32_t *gpred_y)
{
    int64_t sum_x = 0;
    int64_t sum_y = 0;
    int i;

    for (i = 0; i < n; i++) {
        prev_mv_pel[2 * i + 0] = pel_mc_q2_to_pel(mvx_q2[i]);
        prev_mv_pel[2 * i + 1] = pel_mc_q2_to_pel(mvy_q2[i]);
        sum_x += mvx_q2[i];
        sum_y += mvy_q2[i];
    }
    *gpred_x = pel_mc_mean_q2_to_pel(sum_x, n);
    *gpred_y = pel_mc_mean_q2_to_pel(sum_y, n);
}

/* Block MV magnitude in luma pixels from a Q2 vector. The result is always
 * >= +0.0f and never NaN, which pel_mc_select_nonneg() relies on. */
static inline float pel_mc_mv_mag_px(int32_t mvx_q2, int32_t mvy_q2)
{
    return (float)(0.25 * sqrt((double)mvx_q2 * mvx_q2 + (double)mvy_q2 * mvy_q2));
}

/* The k-th smallest (0-based) of n floats that are all >= +0.0f and not NaN.
 * For such values the IEEE-754 bit pattern, read as an unsigned integer, sorts
 * exactly like the value, so this is a most-significant-byte-first radix
 * select: four 8-bit passes, each counting only the elements that share the
 * bytes already fixed. O(n), no allocation, input left untouched. Returns 0.0f
 * when v is NULL, n is 0, or k >= n. */
static inline float pel_mc_select_nonneg(const float *v, size_t n, size_t k)
{
    uint32_t prefix = 0;
    uint32_t mask = 0;
    float out;
    unsigned int pass;

    if (!v || n == 0 || k >= n)
        return 0.0f;
    for (pass = 0; pass < 4u; pass++) {
        const unsigned int shift = 24u - 8u * pass;
        size_t hist[256] = {0};
        unsigned int d;
        size_t i;

        for (i = 0; i < n; i++) {
            uint32_t bits;
            memcpy(&bits, &v[i], sizeof(bits));
            if ((bits & mask) == prefix)
                hist[(bits >> shift) & 0xffu]++;
        }
        /* Invariant: k < the number of elements matching prefix/mask, so the
         * scan stops at a non-empty byte bucket at or before 255. */
        for (d = 0; d < 255u && k >= hist[d]; d++)
            k -= hist[d];
        prefix |= (uint32_t)d << shift;
        mask |= 0xffu << shift;
    }
    memcpy(&out, &prefix, sizeof(out));
    return out;
}

/* PEL_SEC_MOTION's motion_magnitude_p95: the ceil(0.95 * n)-th smallest value
 * (0-based rank ceil(0.95 * n) - 1, clipped to [0, n - 1]); 0.0f for n <= 0.
 * Same contract as pel_mc_select_nonneg() for the values. */
static inline float pel_mc_p95_nonneg(const float *v, int n)
{
    int rank;

    if (n <= 0)
        return 0.0f;
    rank = (int)ceil(0.95 * (double)n) - 1;
    if (rank < 0)
        rank = 0;
    if (rank > n - 1)
        rank = n - 1;
    return pel_mc_select_nonneg(v, (size_t)n, (size_t)rank);
}

/* PelorusMotionSection.block_size_log2 for a bsize option value (interop ABI
 * 1.4, #218): log2 of the block edge when it is a power of two in the option
 * range 8..32 (3, 4 or 5), else 0, "not reported". The bsize option also
 * accepts edges such as 12 that a log2 cannot express; a consumer then infers
 * the edge from the grid, as it does for an ABI 1.3 blob. */
static inline uint8_t pel_mc_bsize_log2(int bsize)
{
    uint8_t n;

    for (n = 3; n <= 5; n++) {
        if (bsize == 1 << n)
            return n;
    }
    return 0;
}

#endif /* AVFILTER_PELORUS_MC_STATS_H */
