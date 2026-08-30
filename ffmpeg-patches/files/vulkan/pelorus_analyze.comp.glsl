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
 * Pelorus per-tile frame-statistics reduction compute shader.
 *
 * Single source of truth for the analyze reduction. Before FFmpeg 9 this text
 * lived twice: as an inline-GLSL string built at runtime inside
 * vf_pelorus_analyze_vulkan.c, and as a standalone reference .comp. FFmpeg 9
 * removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and ff_vk_shader_init),
 * so the shader is now compiled to SPIR-V at build time and linked in — which
 * retires that duplication and the whole class of lockstep-drift defects.
 *
 * One 32x32 workgroup reduces one tile of the luma plane in shared memory,
 * then invocation 0 writes the tile's variance / edge density / low-amplitude
 * gradient / valid flag / mean into a position-preserving struct-of-arrays
 * SSBO at tile[k * ntiles + (tile_y * grid_cols + tile_x)], k in 0..4. The
 * host sums the spans into the PEL_SEC_* frame scalars and drives the ROI
 * auto-detection off the per-tile values.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(); the C
 * side loads { PEL_TILE, PEL_TILE, 1 }. One workgroup == one tile. */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Mirrors the C `pc` struct byte-for-byte (2 * int + float = 12 bytes). */
layout (push_constant, std430) uniform pushConstants {
    int grid_cols;
    int ntiles;
    float grad_lo;
};

/* Binding order MUST match the C FFVulkanDescriptorSetBinding array. */
layout (set = 0, binding = 0) uniform readonly image2D input_images[];
layout (set = 0, binding = 1, std430) buffer tile_buffer {
    uint tile[];
};

shared uint s_sum;
shared uint s_sumsq;
shared uint s_edge;
shared uint s_grad;
shared uint s_cnt;

void main()
{
    const float TS = 65535.0;
    const float GS = 1000000.0;
    if (gl_LocalInvocationIndex == 0u) {
        s_sum = 0u; s_sumsq = 0u; s_edge = 0u;
        s_grad = 0u; s_cnt = 0u;
    }
    barrier();
    ivec2 size = imageSize(input_images[0]);
    ivec2 pos = ivec2(gl_GlobalInvocationID.xy);
    /* Was IS_WITHIN(pos, size) — the n8 GLSL prelude is gone in FFmpeg 9.
     * NOT an early return: every invocation must reach the barrier below. */
    if (all(lessThan(pos, size))) {
        float l = imageLoad(input_images[0], pos).x;
        ivec2 rp = clamp(pos + ivec2(1, 0), ivec2(0), size - 1);
        ivec2 dp = clamp(pos + ivec2(0, 1), ivec2(0), size - 1);
        float gx = abs(imageLoad(input_images[0], rp).x - l);
        float gy = abs(imageLoad(input_images[0], dp).x - l);
        float g = gx + gy;
        float edge = clamp(g, 0.0, 1.0);
        /* A "real but low-amplitude" step (>= grad_lo) is the banding
         * signature; a dead-flat constant tile (g < grad_lo) and a textured
         * tile (large g) both contribute 0 to the gradient accumulator. The
         * window [grad_lo, 8*grad_lo] isolates the slope that bands. */
        float band_g = (g >= grad_lo && g < grad_lo * 8.0)
                       ? g : 0.0;
        atomicAdd(s_sum,   uint(l * TS));
        atomicAdd(s_sumsq, uint(l * l * TS));
        atomicAdd(s_edge,  uint(edge * TS));
        atomicAdd(s_grad,  uint(band_g * TS));
        atomicAdd(s_cnt,   1u);
    }
    barrier();
    if (gl_LocalInvocationIndex == 0u) {
        uint idx = gl_WorkGroupID.y * uint(grid_cols)
                   + gl_WorkGroupID.x;
        uint N = uint(ntiles);
        if (s_cnt > 0u) {
            float n = float(s_cnt);
            float mean = (float(s_sum) / TS) / n;
            float msq  = (float(s_sumsq) / TS) / n;
            float var  = max(msq - mean * mean, 0.0);
            float edge = (float(s_edge) / TS) / n;
            float grad = (float(s_grad) / TS) / n;
            tile[idx]          = uint(var  * GS);
            tile[N + idx]      = uint(edge * GS);
            tile[2u * N + idx] = uint(grad * GS);
            tile[3u * N + idx] = 1u;
            /* 5th span: the tile-mean luma, for the host coarse (inter-tile)
             * banding scale (ADR-0132/CAMBI alignment) — a shallow gradient
             * spanning many tiles is invisible to per-tile variance but shows
             * as a smooth tile-mean ramp. */
            tile[4u * N + idx] = uint(mean * GS);
        } else {
            tile[idx] = 0u; tile[N + idx] = 0u;
            tile[2u * N + idx] = 0u; tile[3u * N + idx] = 0u;
            tile[4u * N + idx] = 0u;
        }
    }
}
