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
 * Pelorus anime/2D dehalo + dering compute shader (HAvsFunc DeHalo_alpha +
 * FineDehalo, single pass).
 *
 * Single source of truth for the dehalo algorithm. Before FFmpeg 9 this text
 * lived twice: as inline-GLSL strings assembled at runtime inside
 * vf_pelorus_dehalo_vulkan.c, and as a standalone reference .comp. FFmpeg 9
 * removed the runtime GLSL builder (GLSLC/GLSLF/GLSLD and ff_vk_shader_init),
 * so the shader is now compiled to SPIR-V at build time and linked in — which
 * retires that duplication and the whole class of lockstep-drift defects.
 *
 * The three things the C side used to const-fold into the generated source —
 * the plane count, the `planes` bitmask, and the ADR-0139 shared-memory tiling
 * switch — are specialization constants now, so the driver still folds them at
 * pipeline-creation time and the tile=0 path keeps costing nothing.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Const-folded by the C side via SPEC_LIST_ADD(). `planes` is the plane count;
 * `plane_mask` is the AVOption `planes` bitmask selecting which get dehaloed
 * (the rest are copied through); `tile` is the AVOption `tile` (ADR-0139). All
 * three were C-side generator inputs before. */
layout (constant_id = 0) const uint planes     = 0;
layout (constant_id = 1) const uint plane_mask = 0x1;
layout (constant_id = 2) const uint tile       = 0;

layout (push_constant, std430) uniform pushConstants {
    int   blur_r;
    float darkstr;
    float brightstr;
    float lowsens;
    float highsens;
    float edge_thr;
    float ring;
};

layout (set = 0, binding = 0) uniform readonly  image2D input_images[];
layout (set = 0, binding = 1) uniform writeonly image2D output_images[];

const int MAX_R = 8;

/* PEL_HALO = MAX_R (8, the max box reach) + 1 (the box-blur cross offset
 * extends the union by one px); PEL_TILE = 32 (workgroup dim) + 2 * PEL_HALO. */
#define PEL_HALO 9
#define PEL_TILE 50

/* Sized by a specialization constant so tile=0 pipelines allocate no shared
 * memory at all (the pre-FFmpeg-9 generator simply did not emit the array). */
shared float s_tile[(tile != 0u) ? (PEL_TILE * PEL_TILE) : 1];

/* Shared-memory tiling of the box-blur window (ADR-0139, opt-in tile=1). Every
 * luma fetch in dehalo() — the box_blur (2r+1)^2 window read 5x per pixel at the
 * centre + 4 cross offsets, the Sobel/contrast 3x3, the ring scan — goes through
 * pel_luma against the SAME input plane, re-reading a heavily overlapping window
 * (~1.5k loads/px at blur=8). That is fetch-bound, not ALU-bound (the box mean is
 * adds + one divide), so on a bandwidth-limited GPU the workgroup cooperatively
 * loads its output region + a PEL_HALO ring into shared memory once per plane and
 * every read hits shared instead of the image. The tile mirrors pel_luma's edge
 * clamp exactly, so tile=1 is bit-identical to tile=0. pel_load_tile() is called
 * from uniform control flow (the plane loop bound and the tile/plane_mask tests
 * are all specialization constants) so its barriers are workgroup-uniform; the
 * leading barrier protects the prior plane's readers before this plane
 * overwrites s_tile. */
void pel_load_tile(int idx, ivec2 sz)
{
    ivec2 wgsz = ivec2(gl_WorkGroupSize.xy);
    ivec2 base = ivec2(gl_WorkGroupID.xy) * wgsz - PEL_HALO;
    uint n = uint(PEL_TILE * PEL_TILE);
    uint stride = gl_WorkGroupSize.x * gl_WorkGroupSize.y;
    barrier();
    for (uint k = gl_LocalInvocationIndex; k < n; k += stride) {
        ivec2 t = ivec2(int(k) - (int(k) / PEL_TILE) * PEL_TILE,
                        int(k) / PEL_TILE);
        ivec2 p = base + t;
        s_tile[k] = imageLoad(input_images[idx],
                              clamp(p, ivec2(0), sz - ivec2(1))).x;
    }
    barrier();
}

/* At tile=1, map the absolute coordinate p into s_tile via the workgroup base.
 * Coordinates inside the loaded window (every dehalo() read at tile=1) hit
 * shared; the clamp mirrors the image-edge clamp so the result matches the
 * direct path exactly. At tile=0 this reads the image directly — `tile` is a
 * specialization constant, so only one of the two branches survives. */
float pel_luma(int idx, ivec2 p, ivec2 sz)
{
    ivec2 cp = clamp(p, ivec2(0), sz - ivec2(1));
    if (tile != 0u) {
        ivec2 base = ivec2(gl_WorkGroupID.xy) * ivec2(gl_WorkGroupSize.xy) - PEL_HALO;
        ivec2 lc = cp - base;
        return s_tile[lc.y * PEL_TILE + lc.x];
    }
    return imageLoad(input_images[idx], cp).x;
}

float box_blur(int idx, ivec2 c, int r, ivec2 sz)
{
    float acc = 0.0; float n = 0.0;
    for (int dy = -MAX_R; dy <= MAX_R; dy++) {
        if (dy < -r || dy > r) continue;
        for (int dx = -MAX_R; dx <= MAX_R; dx++) {
            if (dx < -r || dx > r) continue;
            acc += pel_luma(idx, c + ivec2(dx, dy), sz); n += 1.0;
        }
    }
    return acc / max(n, 1.0);
}

void dehalo(ivec2 pos, int idx)
{
    ivec2 sz = imageSize(output_images[idx]);
    int r = clamp(blur_r, 1, MAX_R);
    float c = pel_luma(idx, pos, sz);
    float h  = box_blur(idx, pos,                r, sz);
    float hl = box_blur(idx, pos + ivec2(-1, 0), r, sz);
    float hr = box_blur(idx, pos + ivec2( 1, 0), r, sz);
    float hu = box_blur(idx, pos + ivec2( 0,-1), r, sz);
    float hd = box_blur(idx, pos + ivec2( 0, 1), r, sz);
    float oMax = c; float oMin = c;
    for (int dy = -1; dy <= 1; dy++) {
        for (int dx = -1; dx <= 1; dx++) {
            float v = pel_luma(idx, pos + ivec2(dx, dy), sz);
            oMax = max(oMax, v); oMin = min(oMin, v);
        }
    }
    float are  = oMax - oMin;
    float ugly = max(max(max(h, hl), max(hr, hu)), hd)
               - min(min(min(h, hl), min(hr, hu)), hd);
    const float EPS = 0.0039;
    float frac = (are - ugly) / (are + EPS);
    float so   = clamp((frac - lowsens) * (1.0 + highsens), 0.0, 1.0);
    float lets = mix(c, h, so);
    float out_v;
    if (lets < c) out_v = c - (c - lets) * brightstr;
    else          out_v = c - (c - lets) * darkstr;
    float gx = 0.0; float gy = 0.0;
    float kx[9] = float[9](-1.0, 0.0, 1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0);
    float ky[9] = float[9](-1.0,-2.0,-1.0,  0.0, 0.0, 0.0,  1.0, 2.0, 1.0);
    int k = 0;
    for (int dy = -1; dy <= 1; dy++) {
        for (int dx = -1; dx <= 1; dx++) {
            float v = pel_luma(idx, pos + ivec2(dx, dy), sz);
            gx += v * kx[k]; gy += v * ky[k]; k++;
        }
    }
    float edge = sqrt(gx * gx + gy * gy);
    bool on_line = edge > edge_thr;
    int rr = clamp(int(ring + 0.5), 1, MAX_R);
    bool near_line = false;
    for (int d = 1; d <= MAX_R; d++) {
        if (d > rr) break;
        float em = max(max(pel_luma(idx, pos + ivec2(d, 0), sz),
                           pel_luma(idx, pos + ivec2(-d, 0), sz)),
                       max(pel_luma(idx, pos + ivec2(0, d), sz),
                           pel_luma(idx, pos + ivec2(0,-d), sz)));
        if (abs(em - c) > edge_thr) near_line = true;
    }
    float ring_mask = (near_line && !on_line) ? 1.0 : 0.0;
    float result = mix(c, out_v, ring_mask);
    imageStore(output_images[idx], pos, vec4(clamp(result, 0.0, 1.0)));
}

void main()
{
    ivec2 size;
    const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);

    /* Preserves the pre-FFmpeg-9 unrolled semantics exactly: the C generator
     * emitted, per plane, `size = imageSize(...)`, then the cooperative tile
     * load in uniform control flow, then `if (IS_WITHIN(pos, size)) { ... }`.
     * The guard was a positive block, NOT an early return — an invocation
     * outside a subsampled chroma plane still falls through to the next
     * plane. */
    for (uint i = 0; i < planes; i++) {
        const bool sel = (plane_mask & (1u << i)) != 0u;

        size = imageSize(output_images[i]);

        /* Cooperative tile load runs in uniform control flow (all invocations,
         * before the per-thread IS_WITHIN guard) so its barriers are valid. */
        if (tile != 0u && sel)
            pel_load_tile(int(i), size);

        if (all(lessThan(pos, size))) {
            if (sel)
                dehalo(pos, int(i));
            else
                imageStore(output_images[i], pos,
                           imageLoad(input_images[i], pos));
        }
    }
}
