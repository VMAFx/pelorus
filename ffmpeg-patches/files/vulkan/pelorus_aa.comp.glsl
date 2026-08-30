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
 * Pelorus anime warp anti-aliasing + line-darkening compute shader.
 *
 * Single source of truth for the warp-AA algorithm. Before FFmpeg 9 this text
 * lived twice: as inline-GLSL strings assembled at runtime inside
 * vf_pelorus_aa_vulkan.c, and as a standalone reference .comp. FFmpeg 9
 * removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and ff_vk_shader_init),
 * so the shader is now compiled to SPIR-V at build time and linked in — which
 * retires that duplication and the whole class of lockstep-drift defects.
 *
 *   emask = blur( |Sobel(luma)| )           // edge-strength (the warp field)
 *   disp  = depth * grad(emask)             // displace toward stronger edges
 *   warp  = bilinear(luma, pos + disp)      // pull samples onto the line
 *   line-darken: where warp is on the dark side near an edge, deepen by darkstr.
 *
 * Works in FF_VK_REP_FLOAT (UNORM) space, so samples are already in [0,1] and
 * the shader is bit-depth agnostic.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Const-folded by the C side via SPEC_LIST_ADD(). `planes` is the plane count;
 * `plane_mask` is the AVOption `planes` bitmask selecting which get warped (the
 * rest are copied through); `fast` is the opt-in shared-memory sobel hoist
 * (ADR-0140). All three were C-side codegen decisions before FFmpeg 9. */
layout (constant_id = 0) const uint planes     = 0;
layout (constant_id = 1) const uint plane_mask = 0x1;
layout (constant_id = 2) const uint fast       = 0;

layout (push_constant, std430) uniform pushConstants {
    int   blur;
    float depth;
    float thresh;
    float darkstr;
    float edge_thr;
};

layout (set = 0, binding = 0) uniform readonly  image2D input_images[];
layout (set = 0, binding = 1) uniform writeonly image2D output_images[];

const int MAX_R = 8;

/* PEL_HALO = MAX_R (emask reach 8) + 1 (the central-difference offset).
 * PEL_TILE = 32 (workgroup dim) + 2*PEL_HALO = 50 => s_sobel[2500] floats =
 * 10000 bytes (< 49152, the Arc A380 shared-mem limit; the smallest of the
 * three dev GPUs). Sized off `fast` so the default path pays nothing. */
const int PEL_HALO = MAX_R + 1;
const int PEL_TILE = 32 + 2 * PEL_HALO;
shared float s_sobel[fast != 0u ? PEL_TILE * PEL_TILE : 1];

float pel_luma(int idx, ivec2 p, ivec2 sz) {
    return imageLoad(input_images[idx], clamp(p, ivec2(0), sz - ivec2(1))).x;
}

float sobel_mag(int idx, ivec2 p, ivec2 sz) {
    float gx = 0.0; float gy = 0.0;
    float kx[9] = float[9](-1.0, 0.0, 1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0);
    float ky[9] = float[9](-1.0,-2.0,-1.0,  0.0, 0.0, 0.0,  1.0, 2.0, 1.0);
    int k = 0;
    for (int dy = -1; dy <= 1; dy++) {
        for (int dx = -1; dx <= 1; dx++) {
            float v = pel_luma(idx, p + ivec2(dx, dy), sz);
            gx += v * kx[k]; gy += v * ky[k]; k++;
        }
    }
    return sqrt(gx * gx + gy * gy);
}

/* Shared-memory hoist of the edge-strength map (fast=1, opt-in; ADR-0140, the
 * ADR-0134 idiom but caching the sobel_mag RESULT, not the raw pixel). aa is
 * ALU-bound: at blur=8 the 4 central-difference emask() calls each reduce a
 * 17x17 window of sobel_mag(), so the SAME sobel_mag(cell) is recomputed ~4x
 * within a pixel and again across every neighbour — ~1156 sobel evaluations/px.
 * Here each workgroup computes sobel_mag once per cell (its 32x32 output region
 * plus a PEL_HALO ring) into s_sobel, then emask() reduces from the cache: ~1
 * sobel/cell amortized. The cooperative compute runs in uniform control flow
 * (outside the bounds guard, under spec-constant-only conditions) so its
 * barriers are workgroup-uniform. Bit-identical to fast=0 by construction: the
 * cached value is the exact output of the same sobel_mag() — same float ops in
 * the same order; only the redundant recompute is removed. */
void pel_load_sobel(int idx, ivec2 sz) {
    ivec2 wgsz = ivec2(gl_WorkGroupSize.xy);
    ivec2 base = ivec2(gl_WorkGroupID.xy) * wgsz - PEL_HALO;
    uint n = uint(PEL_TILE * PEL_TILE);
    uint stride = gl_WorkGroupSize.x * gl_WorkGroupSize.y;
    barrier();
    for (uint k = gl_LocalInvocationIndex; k < n; k += stride) {
        ivec2 t = ivec2(int(k) - (int(k) / PEL_TILE) * PEL_TILE,
                        int(k) / PEL_TILE);
        s_sobel[k] = sobel_mag(idx, base + t, sz);
    }
    barrier();
}

/* sobel-mag at the current pixel + off, from the cache. `off` is relative to
 * the current output pixel; valid for |off| <= PEL_HALO (the emask reach). */
float ssobel(ivec2 off) {
    ivec2 lc = ivec2(gl_LocalInvocationID.xy) + PEL_HALO + off;
    return s_sobel[lc.y * PEL_TILE + lc.x];
}

/* The edge magnitude at base + off. fast=0 recomputes the 9-tap sobel (the
 * original, default path); fast=1 reads the per-workgroup cache populated once
 * per cell. `fast` is a specialization constant, so the branch is folded away
 * at pipeline-compile time exactly as the old C-side #define was.
 * fast=1 ignores `base` because ssobel() encodes the current pixel via
 * gl_LocalInvocationID — valid ONLY when emask is called with base == pos
 * (every call site in aa() satisfies this). */
float pel_sobel(int idx, ivec2 base, ivec2 off, ivec2 sz) {
    if (fast != 0u)
        return ssobel(off);
    return sobel_mag(idx, base + off, sz);
}

/* emask reduces a (2r+1)^2 window of the edge magnitude around base + off0. */
float emask(int idx, ivec2 base, ivec2 off0, ivec2 sz, int r, float thr) {
    float acc = 0.0; float n = 0.0;
    for (int dy = -MAX_R; dy <= MAX_R; dy++) {
        if (dy < -r || dy > r) continue;
        for (int dx = -MAX_R; dx <= MAX_R; dx++) {
            if (dx < -r || dx > r) continue;
            ivec2 off = off0 + ivec2(dx, dy);
            acc += min(pel_sobel(idx, base, off, sz), thr); n += 1.0;
        }
    }
    return acc / max(n, 1.0);
}

float bilinear(int idx, float fx, float fy, ivec2 sz) {
    int x0 = int(floor(fx)); int y0 = int(floor(fy));
    float tx = fx - float(x0); float ty = fy - float(y0);
    float a = pel_luma(idx, ivec2(x0,     y0),     sz);
    float b = pel_luma(idx, ivec2(x0 + 1, y0),     sz);
    float c = pel_luma(idx, ivec2(x0,     y0 + 1), sz);
    float d = pel_luma(idx, ivec2(x0 + 1, y0 + 1), sz);
    return mix(mix(a, b, tx), mix(c, d, tx), ty);
}

void aa(ivec2 pos, int idx) {
    ivec2 sz = imageSize(output_images[idx]);
    int r = clamp(blur, 0, MAX_R);
    float thr = max(thresh, 0.0001);
    float gx = (emask(idx, pos, ivec2(1, 0), sz, r, thr)
              - emask(idx, pos, ivec2(-1, 0), sz, r, thr)) * 0.5;
    float gy = (emask(idx, pos, ivec2(0, 1), sz, r, thr)
              - emask(idx, pos, ivec2(0,-1), sz, r, thr)) * 0.5;
    float fx = float(pos.x) + depth * gx;
    float fy = float(pos.y) + depth * gy;
    float warp = bilinear(idx, fx, fy, sz);
    if (darkstr > 0.0) {
        float e = sobel_mag(idx, pos, sz);
        if (e > edge_thr) {
            float lo = warp;
            for (int dy = -1; dy <= 1; dy++)
                for (int dx = -1; dx <= 1; dx++)
                    lo = min(lo, pel_luma(idx, pos + ivec2(dx, dy), sz));
            warp = mix(warp, lo, darkstr);
        }
    }
    imageStore(output_images[idx], pos, vec4(clamp(warp, 0.0, 1.0)));
}

void main()
{
    const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);
    ivec2 size;

    /* Two shapes, exactly as the pre-FFmpeg-9 C generator emitted them.
     *
     * fast=1: NO early return — the cooperative cache load must run in uniform
     * control flow (all invocations reach its barriers), so every plane is
     * bounds-GUARDED rather than early-returned and the barriers stay
     * workgroup-uniform regardless of plane order.
     *
     * fast=0: `if (!IS_WITHIN(pos, size)) return;` per plane, so the first
     * plane that does not contain `pos` ends the invocation. Subsampled chroma
     * is therefore skipped for positions only valid in luma, by design.
     *
     * `fast`, `planes` and `plane_mask` are specialization constants, so this
     * folds down to the single generated variant, as before. */
    if (fast != 0u) {
        for (uint i = 0; i < planes; i++) {
            size = imageSize(output_images[i]);
            if ((plane_mask & (1u << i)) != 0u) {
                pel_load_sobel(int(i), size);
                if (all(lessThan(pos, size))) {
                    aa(pos, int(i));
                }
            } else {
                if (all(lessThan(pos, size))) {
                    imageStore(output_images[i], pos,
                               imageLoad(input_images[i], pos));
                }
            }
        }
    } else {
        for (uint i = 0; i < planes; i++) {
            size = imageSize(output_images[i]);
            if (!all(lessThan(pos, size)))
                return;

            if ((plane_mask & (1u << i)) != 0u)
                aa(pos, int(i));
            else
                imageStore(output_images[i], pos,
                           imageLoad(input_images[i], pos));
        }
    }
}
