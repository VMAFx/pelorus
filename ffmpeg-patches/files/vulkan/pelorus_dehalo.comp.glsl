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
layout (constant_id = 3) const uint semi_planar = 0;
layout (constant_id = 4) const uint sample_code_max = 0;

layout (push_constant, std430) uniform pushConstants {
    int   blur_r;
    float darkstr;
    float brightstr;
    float lowsens;
    float highsens;
    float edge_thr;
    float ring;
    float sample_scale;
};

layout (set = 0, binding = 0) uniform readonly  image2D input_images[];
layout (set = 0, binding = 1) uniform writeonly image2D output_images[];

const int MAX_R = 8;

/* The DeHalo_alpha Repair window needs the box means and the 3x3 contrast on a
 * 5x5 grid around the pixel (3x3 window x 3x3 / cross neighbourhood), so the
 * deepest read is MAX_R + 2. The edge-mask scan reaches ring + 1 <= MAX_R + 1.
 * PEL_HALO = MAX_R + 2 = 10; PEL_TILE = 32 (workgroup dim) + 2 * PEL_HALO. */
#define PEL_HALO 10
#define PEL_TILE 52
#define PEL_GRID 5

/* Sized by a specialization constant so tile=0 pipelines allocate no shared
 * memory at all (the pre-FFmpeg-9 generator simply did not emit the array). */
shared float s_tile[(tile != 0u) ? (PEL_TILE * PEL_TILE) : 1];

/* `precise` (SPIR-V NoContraction) keeps this product individually rounded.
 * tile=1 caches it in shared memory, so the direct path must not fuse it into a
 * later FMA either; otherwise the two paths drift by 1 code value at 10/12-bit,
 * where sample_scale is not an exact power of two (ADR-0139 bit-identity). */
float pel_to_sample(float value)
{
    precise float s = value * sample_scale;
    return s;
}

float pel_to_storage(float value)
{
    if (sample_code_max == 0u)
        return value / sample_scale;
    const float code_max = float(sample_code_max);
    return round(clamp(value, 0.0, 1.0) * code_max) / code_max / sample_scale;
}

uint pel_component_count(uint plane)
{
    return (semi_planar != 0u && plane == 1u) ? 2u : 1u;
}

/* Shared-memory tiling of the box-blur window (ADR-0139, opt-in tile=1). Every
 * luma fetch in dehalo() — the box-mean grid, the 5x5 contrast grid, the Sobel
 * edge-mask scan — goes through pel_luma against the SAME input plane,
 * re-reading a heavily overlapping window (~850 loads/px at blur=8, ring=8).
 * That is fetch-bound, not ALU-bound (the box means are adds + one divide), so
 * on a bandwidth-limited GPU the workgroup cooperatively
 * loads its output region + a PEL_HALO ring into shared memory once per plane and
 * every read hits shared instead of the image. The tile mirrors pel_luma's edge
 * clamp exactly, so tile=1 is bit-identical to tile=0. pel_load_tile() is called
 * from uniform control flow (the plane loop bound and the tile/plane_mask tests
 * are all specialization constants) so its barriers are workgroup-uniform; the
 * leading barrier protects the prior plane's readers before this plane
 * overwrites s_tile. */
void pel_load_tile(int idx, int comp, ivec2 sz)
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
        s_tile[k] = pel_to_sample(
            imageLoad(input_images[idx],
                      clamp(p, ivec2(0), sz - ivec2(1)))[comp]);
    }
    barrier();
}

/* At tile=1, map the absolute coordinate p into s_tile via the workgroup base.
 * Coordinates inside the loaded window (every dehalo() read at tile=1) hit
 * shared; the clamp mirrors the image-edge clamp so the result matches the
 * direct path exactly. At tile=0 this reads the image directly — `tile` is a
 * specialization constant, so only one of the two branches survives. */
float pel_luma(int idx, int comp, ivec2 p, ivec2 sz)
{
    ivec2 cp = clamp(p, ivec2(0), sz - ivec2(1));
    if (tile != 0u) {
        ivec2 base = ivec2(gl_WorkGroupID.xy) * ivec2(gl_WorkGroupSize.xy) - PEL_HALO;
        ivec2 lc = cp - base;
        return s_tile[lc.y * PEL_TILE + lc.x];
    }
    return pel_to_sample(imageLoad(input_images[idx], cp)[comp]);
}

/* Box means of the (2r+1)^2 window around every offset of the 5x5 grid centred
 * on pos, row-major in hg[] (index (dy + 2) * PEL_GRID + (dx + 2)). Each of the
 * 2r+5 rows of the union window yields its five horizontal window sums with one
 * sliding sum, and each sum is added to the grid rows whose window covers that
 * row: (2r+5)(2r+9) reads instead of 25 (2r+1)^2. Every loop bound is a
 * constant, so the grid stays in registers (no dynamically indexed array). */
void box_grid(int idx, int comp, ivec2 pos, int r, ivec2 sz, out float hg[PEL_GRID * PEL_GRID])
{
    precise float acc[PEL_GRID * PEL_GRID];
    for (int g = 0; g < PEL_GRID * PEL_GRID; g++)
        acc[g] = 0.0;
    for (int y = -MAX_R - 2; y <= MAX_R + 2; y++) {
        if (y < -r - 2 || y > r + 2)
            continue;
        precise float s = 0.0;
        for (int dx = -MAX_R; dx <= MAX_R; dx++) {
            if (dx < -r || dx > r)
                continue;
            s += pel_luma(idx, comp, pos + ivec2(dx - 2, y), sz);
        }
        precise float rs[PEL_GRID];
        rs[0] = s;
        for (int i = 1; i < PEL_GRID; i++) {
            s += pel_luma(idx, comp, pos + ivec2(i - 2 + r, y), sz)
               - pel_luma(idx, comp, pos + ivec2(i - 3 - r, y), sz);
            rs[i] = s;
        }
        for (int j = 0; j < PEL_GRID; j++) {
            if (abs(y - (j - 2)) > r)
                continue;
            for (int i = 0; i < PEL_GRID; i++)
                acc[j * PEL_GRID + i] += rs[i];
        }
    }
    const float n = float((2 * r + 1) * (2 * r + 1));
    for (int g = 0; g < PEL_GRID * PEL_GRID; g++)
        hg[g] = acc[g] / n;
}

/* Sobel magnitude in step units: an ideal step of height s reads s (the raw
 * 3x3 Sobel reads 4s), so `edge` compares against a [0,1] sample difference. */
float edge_step(int idx, int comp, ivec2 p, ivec2 sz)
{
    const float kx[9] = float[9](-1.0, 0.0, 1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0);
    const float ky[9] = float[9](-1.0, -2.0, -1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 1.0);
    precise float gx = 0.0; precise float gy = 0.0;
    int k = 0;
    for (int dy = -1; dy <= 1; dy++) {
        for (int dx = -1; dx <= 1; dx++) {
            float v = pel_luma(idx, comp, p + ivec2(dx, dy), sz);
            gx += v * kx[k]; gy += v * ky[k]; k++;
        }
    }
    precise float mag = sqrt(gx * gx + gy * gy);
    return 0.25 * mag;
}

float dehalo(ivec2 pos, int idx, int comp)
{
    ivec2 sz = imageSize(output_images[idx]);
    float c = pel_luma(idx, comp, pos, sz);

    /* FineDehalo gate. A pixel whose own edge step exceeds `edge` is line-art
     * and is never touched. The halo band is every other pixel with a line
     * within `ring` px along the cross; the scan tests the edge mask itself, so
     * dark and bright halos qualify alike. A pixel with line on both sides of
     * an axis sits between two close edges (a thin line's core, a gap between
     * parallel lines): FineDehalo's exclusion zone, also left alone. */
    if (edge_step(idx, comp, pos, sz) > edge_thr)
        return clamp(c, 0.0, 1.0);
    int rr = clamp(int(ring + 0.5), 1, MAX_R);
    bool el = false; bool er = false; bool eu = false; bool ed = false;
    for (int d = 1; d <= MAX_R; d++) {
        if (d > rr)
            break;
        er = er || edge_step(idx, comp, pos + ivec2( d, 0), sz) > edge_thr;
        el = el || edge_step(idx, comp, pos + ivec2(-d, 0), sz) > edge_thr;
        ed = ed || edge_step(idx, comp, pos + ivec2(0,  d), sz) > edge_thr;
        eu = eu || edge_step(idx, comp, pos + ivec2(0, -d), sz) > edge_thr;
    }
    if (!(el || er || eu || ed) || (el && er) || (eu && ed))
        return clamp(c, 0.0, 1.0);

    /* DeHalo_alpha, ss <= 1 form. For each pixel of the 3x3 Repair window:
     * are = 3x3 contrast of the source, ugly = cross contrast of the box mean,
     * so = lowsens/highsens-shaped (are - ugly) / are, and
     * lets = MaskedMerge(halos, clp, so): the source where the blur would erase
     * detail (so high), the halo-free blur where the blur keeps the structure.
     * remove = Repair(clp, lets, 1) clamps the source into the window's lets
     * range, so only an excursion beyond the blurred envelope is pulled back
     * and the result can only reduce the ring. */
    int r = clamp(blur_r, 1, MAX_R);
    float hg[PEL_GRID * PEL_GRID];
    float cg[PEL_GRID * PEL_GRID];
    box_grid(idx, comp, pos, r, sz, hg);
    for (int j = 0; j < PEL_GRID; j++)
        for (int i = 0; i < PEL_GRID; i++)
            cg[j * PEL_GRID + i] = pel_luma(idx, comp, pos + ivec2(i - 2, j - 2), sz);
    const float EPS = 0.0039;
    float lo = 1.0e30; float hi = -1.0e30;
    for (int dj = -1; dj <= 1; dj++) {
        for (int di = -1; di <= 1; di++) {
            const int g = (dj + 2) * PEL_GRID + (di + 2);
            float oMax = cg[g]; float oMin = cg[g];
            for (int b = -1; b <= 1; b++) {
                for (int a = -1; a <= 1; a++) {
                    float v = cg[g + b * PEL_GRID + a];
                    oMax = max(oMax, v); oMin = min(oMin, v);
                }
            }
            /* `precise` (NoContraction) on the arithmetic keeps tile=0 and
             * tile=1 bit-identical: without it the two specializations may
             * fuse multiply-adds differently and flip a rounding. */
            precise float are  = oMax - oMin;
            float hc = hg[g];
            precise float ugly = max(max(hc, max(hg[g - 1], hg[g + 1])),
                                     max(hg[g - PEL_GRID], hg[g + PEL_GRID]))
                               - min(min(hc, min(hg[g - 1], hg[g + 1])),
                                     min(hg[g - PEL_GRID], hg[g + PEL_GRID]));
            precise float frac = (are - ugly) / (are + EPS);
            precise float so   = clamp((frac - lowsens) * (1.0 + highsens), 0.0, 1.0);
            precise float lets = mix(hc, cg[g], so);
            lo = min(lo, lets); hi = max(hi, lets);
        }
    }
    float remove = clamp(c, lo, hi);
    precise float out_v;
    if (remove < c) out_v = c - (c - remove) * brightstr;
    else            out_v = c - (c - remove) * darkstr;
    return clamp(out_v, 0.0, 1.0);
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
        const bool inb = all(lessThan(pos, size));

        if (sel) {
            const uint ncomp = pel_component_count(i);
            vec4 texel = inb ? imageLoad(input_images[i], pos) : vec4(0.0);
            for (uint c = 0u; c < ncomp; c++) {
                const int comp = int(c);
                /* Cooperative tile load runs in uniform control flow (all
                 * invocations, before the per-thread bounds guard) so its
                 * barriers remain valid for each specialized component. */
                if (tile != 0u)
                    pel_load_tile(int(i), comp, size);
                if (inb)
                    texel[comp] = pel_to_storage(dehalo(pos, int(i), comp));
            }
            if (inb)
                imageStore(output_images[i], pos, texel);
        } else {
            if (inb)
                imageStore(output_images[i], pos,
                           imageLoad(input_images[i], pos));
        }
    }
}
