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
 * Pelorus film-grain-synthesis (FGS) parameter estimator compute shader.
 *
 * Single source of truth for the estimator. Before FFmpeg 9 this text lived
 * twice: as an inline-GLSL string built at runtime inside
 * vf_pelorus_grain_estimate_vulkan.c, and as a standalone reference .comp.
 * FFmpeg 9 removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and
 * ff_vk_shader_init), so the shader is now compiled to SPIR-V at build time
 * and linked in — which retires that duplication and the whole class of
 * lockstep-drift defects.
 *
 * Method (per luma intensity band — the AV1/H.274 scaling function is
 * intensity-dependent by design):
 *   1. high-pass:  resid = luma - mean3x3   (box low-pass removes structure)
 *   2. edge gate:  skip pixels whose 3x3 range exceeds edge_thr (that residual
 *                  is an edge / texture, not grain); a chain of wider gates
 *                  (GATE_STEP[g] x edge_thr) and the band pixel total let the
 *                  host recover heavy grain the default gate truncates
 *                  (ADR-0229, ADR-0230)
 *   3. bin:        accumulate resid^2 into one of BANDS luma bins with a
 *                  per-bin pixel count -> per-band grain stddev
 *   4. AR proxy:   accumulate the lag-1 product resid*resid_right per band
 *                  and gate (a spatial-correlation scalar -> AR seed / H.274
 *                  cutoff)
 * The reduction is sliced (SLICES) to cut atomic contention; the host sums the
 * slices and fits the AV1 piecewise scaling function + AR seed (ADR-0115) and
 * the H.274 model-0 parameters (ADR-0161).
 *
 * FF_VK_REP_FLOAT normalizes against the storage container, so every load is
 * converted to the logical sample domain before the estimator sees it. The
 * fixed-point scales and residual clamp keep every uint32 accumulator
 * overflow-safe to DCI 8K (scripts/test-grain-accumulator-bounds.py); they
 * MUST match the C-side PEL_GRAIN_* defines byte-for-byte. Each per-pixel add
 * is rounded to the nearest fixed-point unit: truncation biases every add by
 * half a unit, which skews the sums of small residuals (ADR-0161).
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

layout (push_constant, std430) uniform pushConstants {
    int   width;
    int   height;
    float edge_thr;
    float sample_scale;
};

layout (set = 0, binding = 0) uniform readonly image2D input_images[];

/* Mirrors PelorusGrainBuf in the filter, byte-for-byte (std430). The gated
 * members hold one [BANDS * SLICES] block per gate, gate 0 first. */
layout (set = 0, binding = 1, std430) buffer grain_buffer {
    uint sumsq[768];    /* Σ resid^2 * SUMSQ_GS,   [GATES * BANDS * SLICES] */
    uint cnt[768];      /* gated-pixel count,      [GATES * BANDS * SLICES] */
    uint corr[768];     /* Σ (resid*resid_right + CORR_BIAS) * CORR_GS      */
    uint corr_cnt[768]; /* lag-1 sample count,     [GATES * BANDS * SLICES] */
    uint total[256];    /* every pixel of the band, gate or not             */
};

float pel_to_sample(float value)
{
    return value * sample_scale;
}

void main()
{
    const float SUMSQ_GS = 300000.0;
    /* The lag-1 product lies in [-RES_CLAMP^2, RES_CLAMP^2], so a bias of
     * RES_CLAMP^2 makes it non-negative without wasting accumulator range. */
    const float CORR_GS = 150000.0;
    const float CORR_BIAS = 0.0064;
    const float RES_CLAMP = 0.08;
    /* Gate g = GATE_STEP[g] * edge_thr; matches pel_grain_gate_step. */
    const int GATES = 3;
    const float GATE_STEP[3] = float[3](1.0, 2.0, 4.0);
    const int BANDS = 8;
    const uint SLICES = 32u;
    ivec2 size = ivec2(width, height);
    int x = int(gl_GlobalInvocationID.x);
    int y = int(gl_GlobalInvocationID.y);
    if (x >= size.x || y >= size.y)
        return;
    float c = pel_to_sample(imageLoad(input_images[0], ivec2(x, y)).x);
    float mean = 0.0; float lo = 1.0; float hi = 0.0;
    for (int dy = -1; dy <= 1; dy++) {
        for (int dx = -1; dx <= 1; dx++) {
            ivec2 p = clamp(ivec2(x + dx, y + dy), ivec2(0), size - ivec2(1));
            float v = pel_to_sample(imageLoad(input_images[0], p).x);
            mean += v; lo = min(lo, v); hi = max(hi, v);
        }
    }
    mean /= 9.0;
    int band = clamp(int(mean * float(BANDS)), 0, BANDS - 1);
    uint slice = (uint(y) * uint(size.x) + uint(x)) % SLICES;
    uint bidx = uint(band) * SLICES + slice;
    /* The band total and the gate chain let the host tell heavy grain, which
     * the default gate truncates, from texture (ADR-0229, ADR-0230). Each
     * gate counts every pixel within it, so the gates nest. */
    atomicAdd(total[bidx], 1u);
    float range = hi - lo;
    if (range > GATE_STEP[GATES - 1] * edge_thr)
        return;
    float resid = clamp(c - mean, -RES_CLAMP, RES_CLAMP);
    for (int g = 0; g < GATES; g++) {
        if (range <= GATE_STEP[g] * edge_thr) {
            uint gidx = uint(g * BANDS) * SLICES + bidx;
            atomicAdd(sumsq[gidx], uint(resid * resid * SUMSQ_GS + 0.5));
            atomicAdd(cnt[gidx], 1u);
        }
    }
    /* lag-1 spatial correlation (AR proxy), banded by this pixel: a pair
     * counts at a gate only when the right neighbour is within it too, so the
     * product reflects grain, not an edge transition. */
    ivec2 rp = clamp(ivec2(x + 1, y), ivec2(0), size - ivec2(1));
    float cr = pel_to_sample(imageLoad(input_images[0], rp).x);
    float meanR = 0.0; float loR = 1.0; float hiR = 0.0;
    for (int dy = -1; dy <= 1; dy++) {
        for (int dx = -1; dx <= 1; dx++) {
            ivec2 p = clamp(ivec2(x + 1 + dx, y + dy), ivec2(0), size - ivec2(1));
            float v = pel_to_sample(imageLoad(input_images[0], p).x);
            meanR += v; loR = min(loR, v); hiR = max(hiR, v);
        }
    }
    meanR /= 9.0;
    float pair = max(range, hiR - loR);
    float residR = clamp(cr - meanR, -RES_CLAMP, RES_CLAMP);
    float prod = resid * residR;
    for (int g = 0; g < GATES; g++) {
        if (pair <= GATE_STEP[g] * edge_thr) {
            uint gidx = uint(g * BANDS) * SLICES + bidx;
            atomicAdd(corr[gidx], uint(max(prod + CORR_BIAS, 0.0) * CORR_GS + 0.5));
            atomicAdd(corr_cnt[gidx], 1u);
        }
    }
}
