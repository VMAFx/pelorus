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
 * Pelorus temporal denoise (NLM-lite joint bilateral + gated temporal walk).
 *
 * Single source of truth for the denoise algorithm. Before FFmpeg 9 this text
 * lived twice: as inline-GLSL strings assembled at runtime inside
 * vf_pelorus_denoise_vulkan.c (helpers + kernel spliced with GLSLD, main()
 * unrolled per plane with GLSLC/GLSLF), and as a standalone reference .comp.
 * FFmpeg 9 removed the runtime GLSL builder, so the shader is compiled to
 * SPIR-V at build time and linked in — retiring that duplication and the whole
 * class of lockstep-drift defects.
 *
 * What the C generator used to const-fold now arrives as specialization
 * constants: the plane count, the `planes` bitmask, the ADR-0134 opt-in
 * shared-memory tiling switch (`tile`) and the semi-planar chroma layout flag.
 * Specialization happens at pipeline creation, so the per-plane loop, the
 * per-component loop and the tile/no-tile branch are folded away exactly as the
 * generated GLSL folded them.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Const-folded by the C side via SPEC_LIST_ADD():
 *   planes     — plane count (the old C-unrolled loop bound),
 *   plane_mask — AVOption `planes` bitmask; unselected planes are copied through,
 *   use_tile   — AVOption `tile` (ADR-0134): cache the spatial window in shared
 *                memory instead of re-reading the image. Bit-identical output,
 *   semi_planar— 1 when the sw_format is semi-planar (NV12/P010/NV16/NV24/P016),
 *                i.e. comp[1].plane == comp[2].plane: plane 1 is a TWO-component
 *                image holding U in .x and V in .y and both must be filtered. */
layout (constant_id = 0) const uint planes      = 0;
layout (constant_id = 1) const uint plane_mask  = 0xf;
layout (constant_id = 2) const uint use_tile    = 0;
layout (constant_id = 3) const uint semi_planar = 0;
layout (constant_id = 4) const uint sample_code_max = 0;

/* PEL_HALO = max patch_radius (3) + the 1-px patch ring.
 * PEL_TILE = 16 (the workgroup dim, see ff_vk_shader_load) + 2 * PEL_HALO. */
#define PEL_HALO 4
#define PEL_TILE 24

/* Mirrors the C `opts` struct byte-for-byte (std430, as before). */
layout (push_constant, std430) uniform pushConstants {
    vec4  sigma_s;
    vec4  sigma_t;
    vec4  strength;
    float blend;
    float temporal_decay;
    float temporal_cut;
    int   patch_radius;
    int   n_prev;
    int   actual_prev;
    int   nb_planes;
    int   planes_mask;
    int   flags;
    uint  frame_idx;
    int   want_meta;
    int   grid_cols;
    int   grid_rows;
    int   cell_w;
    int   cell_h;
    int   chroma_shift_w;
    int   chroma_shift_h;
    float mv_scale;
    int   actual_next;
    float sample_scale;
};

/* Binding order MUST match the C descriptor array exactly (inputs first, output
 * then the forward tap last — the Nin / bwdif binding-order contract):
 * cur=0, prev0-3=1-4, stat=5, mv=6, conf=7, output=8, next0=9. */
layout (set = 0, binding = 0) uniform readonly  image2D cur_images[];
layout (set = 0, binding = 1) uniform readonly  image2D prev0_images[];
layout (set = 0, binding = 2) uniform readonly  image2D prev1_images[];
layout (set = 0, binding = 3) uniform readonly  image2D prev2_images[];
layout (set = 0, binding = 4) uniform readonly  image2D prev3_images[];

/* meta=1 residual accumulators, mirrored by PelorusDenoiseBuf in the C filter.
 * Index [stat * SLICES + slice]; stat 0 = sum |r| luma, 1 = sum |r| U,
 * 2 = sum |r| V, 3 = sum r^2 luma, each in RES_GS fixed point. sum_lo/sum_hi
 * are the low and high words of a 64-bit sum: one 32-bit word cannot hold a
 * fine fixed-point scale at DCI 8K (BUG-016). cnt_* are pixel counts. */
layout (set = 0, binding = 5, std430) buffer stat_buffer {
    uint sum_lo[64];
    uint sum_hi[64];
    uint cnt_y[16];
    uint cnt_c[16];
};

/* Per-cell quarter-pel MV grid: (uint16 dx) | (uint16 dy << 16). */
layout (set = 0, binding = 6, std430) buffer mv_grid {
    uint mv_packed[];
};

/* Per-cell motion confidence (0..255), one uint per cell. */
layout (set = 0, binding = 7, std430) buffer conf_grid {
    uint conf_packed[];
};

layout (set = 0, binding = 8) uniform writeonly image2D output_images[];

/* Forward-lookahead tap (ADR-0137): the NEXT frame, mirrors prev0_images. */
layout (set = 0, binding = 9) uniform readonly  image2D next0_images[];

const int FLAG_TEMPORAL = 1;
const int FLAG_MOTION_COMP = 2;
const int FLAG_PROTECT_DETAIL = 4;
const float EPS = 1e-6;
/* exp(x) == exp2(x * LOG2E); folded into the loop-invariant weight factors. */
const float LOG2E = 1.4426950408889634;

/* meta=1 residual reduction (BUG-016). Each workgroup sums its own residuals
 * in shared memory, then adds one partial per statistic to its slice of the
 * 64-bit stat_buffer sums. The old per-pixel uint32 adds at a 1e3 scale
 * truncated every residual below ~0.03 to zero in the r^2 sum, so
 * noise_sigma_estimate read 0 on clean content. RES_GS = 2^23 resolves a
 * one-code 10-bit residual squared as 8 units, while a full 16x16 workgroup at
 * |r| <= RES_MAX stays below 2^31. scripts/test-denoise-accumulator-bounds.py
 * checks these constants against the C mirror and the DCI 8K bounds. */
const uint STATS = 4u;
const uint SLICES = 16u;
const float RES_GS = 8388608.0;
const float RES_MAX = 1.0;
shared uint s_res[STATS];
shared uint s_cnt[2];

uint pel_res_fixed(float v) {
    return uint(clamp(v, 0.0, RES_MAX) * RES_GS + 0.5);
}

/* 64-bit add from two 32-bit atomics: the add that wraps sum_lo sees it in its
 * own return value, so the carries are counted exactly once. */
void pel_acc64(uint k, uint v) {
    if (v == 0u)
        return;
    uint old = atomicAdd(sum_lo[k], v);
    if (old > 0xFFFFFFFFu - v)
        atomicAdd(sum_hi[k], 1u);
}

/* `precise` (SPIR-V NoContraction) keeps this product individually rounded.
 * tile=1 caches it in shared memory, so the direct path must not fuse it into a
 * later FMA either; otherwise the two paths drift by 1 code value at 10/12-bit,
 * where sample_scale is not an exact power of two (ADR-0134 bit-identity). */
float pel_to_sample(float value) {
    precise float s = value * sample_scale;
    return s;
}
float pel_to_storage(float value) {
    if (sample_code_max == 0u)
        return value / sample_scale;
    const float code_max = float(sample_code_max);
    return round(clamp(value, 0.0, 1.0) * code_max) / code_max / sample_scale;
}
uint pel_component_count(uint plane) {
    return (semi_planar != 0u && plane == 1u) ? 2u : 1u;
}

/* Pinned linear interpolation. mix() and smoothstep() are GLSL.std.450
 * extended instructions that glslang never decorates NoContraction, so a driver
 * may expand and fuse their internals differently in the tile=0 and tile=1
 * pipelines; the denoise output path spells them out with `precise` instead. */
float pel_lerp(float a, float b, float t) {
    precise float r = a + t * (b - a);
    return r;
}

/* `comp` selects the component WITHIN the plane image: always 0 on a planar
 * plane, 0 (=U) or 1 (=V) on a semi-planar chroma plane. */
float pel_cur(int idx, int comp, ivec2 p, ivec2 sz) {
    return pel_to_sample(
        imageLoad(cur_images[idx], clamp(p, ivec2(0), sz - ivec2(1)))[comp]);
}
float pel_prev(int t, int idx, int comp, ivec2 p, ivec2 sz) {
    ivec2 c = clamp(p, ivec2(0), sz - ivec2(1));
    if (t == 1) return pel_to_sample(imageLoad(prev0_images[idx], c)[comp]);
    if (t == 2) return pel_to_sample(imageLoad(prev1_images[idx], c)[comp]);
    if (t == 3) return pel_to_sample(imageLoad(prev2_images[idx], c)[comp]);
    return pel_to_sample(imageLoad(prev3_images[idx], c)[comp]);
}

/* --- motion-compensated previous-frame fetch (ADR-0113) --- */
int pel_se16(uint v) { return int(v << 16) >> 16; } /* sign-extend low 16 */
ivec2 pel_cell(ivec2 lpos) {
    return clamp(lpos / ivec2(cell_w, cell_h), ivec2(0),
                 ivec2(grid_cols - 1, grid_rows - 1));
}
vec2 pel_mc_mv(ivec2 lpos) { /* nearest-cell quarter-pel MV, luma px */
    ivec2 cell = pel_cell(lpos);
    uint packed = mv_packed[cell.y * grid_cols + cell.x];
    return vec2(pel_se16(packed & 0xFFFFu), pel_se16(packed >> 16)) * mv_scale;
}
float pel_mc_conf(ivec2 lpos) { /* nearest-cell confidence [0,1] */
    ivec2 cell = pel_cell(lpos);
    precise float c = float(conf_packed[cell.y * grid_cols + cell.x] & 0xFFu) *
                      (1.0 / 255.0);
    return c;
}
float pel_prev_mc(int t, int idx, int comp, ivec2 pos, ivec2 sz) {
    int cw = (idx > 0) ? chroma_shift_w : 0;
    int ch = (idx > 0) ? chroma_shift_h : 0;
    ivec2 lpos = pos << ivec2(cw, ch);
    vec2 mvl = pel_mc_mv(lpos);                /* MV in luma pixels */
    /* Power-of-two divisors are exact, so the sub-pel point is exact too. */
    precise vec2 mvp = vec2(mvl.x / float(1 << cw), mvl.y / float(1 << ch));
    precise vec2 sp = vec2(pos) + mvp;         /* sub-pel sample point */
    ivec2 ip = ivec2(floor(sp));
    precise vec2 f = sp - vec2(ip);
    float p00 = pel_prev(t, idx, comp, ip + ivec2(0, 0), sz);
    float p10 = pel_prev(t, idx, comp, ip + ivec2(1, 0), sz);
    float p01 = pel_prev(t, idx, comp, ip + ivec2(0, 1), sz);
    float p11 = pel_prev(t, idx, comp, ip + ivec2(1, 1), sz);
    return pel_lerp(pel_lerp(p00, p10, f.x), pel_lerp(p01, p11, f.x), f.y);
}

/* Shared-memory tiling of the current-frame spatial window (the NLM range term
 * re-reads an overlapping (2*patchR+3)^2 window ~9x per pixel — fetch-bound, not
 * ALU-bound). Each workgroup cooperatively loads its window (the 16x16 tile plus
 * a PEL_HALO ring, clamped) into s_tile once per plane, then every spatial read
 * hits shared memory instead of the image. pel_load_tile() runs in uniform
 * control flow (outside the bounds guard, under spec-constant-only conditions)
 * so its barriers are workgroup-uniform; the leading barrier protects the prior
 * plane's readers before this plane overwrites s_tile. On a semi-planar chroma
 * plane the tile is reloaded once per component (the loop bound is
 * specialization-constant derived, so it stays workgroup-uniform). When
 * use_tile == 0 the whole path is specialized away and s_tile shrinks to a
 * single element. */
shared float s_tile[(use_tile != 0u) ? (PEL_TILE * PEL_TILE) : 1];

void pel_load_tile(int idx, int comp, ivec2 sz) {
    ivec2 wgsz = ivec2(gl_WorkGroupSize.xy);
    ivec2 base = ivec2(gl_WorkGroupID.xy) * wgsz - PEL_HALO;
    uint n = uint(PEL_TILE * PEL_TILE);
    uint stride = gl_WorkGroupSize.x * gl_WorkGroupSize.y;
    barrier();
    for (uint k = gl_LocalInvocationIndex; k < n; k += stride) {
        ivec2 t = ivec2(int(k) - (int(k) / PEL_TILE) * PEL_TILE,
                        int(k) / PEL_TILE);
        s_tile[k] = pel_cur(idx, comp, base + t, sz);
    }
    barrier();
}
float tcur(ivec2 off) {
    ivec2 lc = ivec2(gl_LocalInvocationID.xy) + PEL_HALO + off;
    return s_tile[lc.y * PEL_TILE + lc.x];
}

/* The spatial fetch, either way. Was a C-selected #define PEL_SPATIAL(o); the
 * use_tile specialization constant folds this branch at pipeline creation. */
float pel_spatial(int idx, int comp, ivec2 pos, ivec2 sz, ivec2 o) {
    if (use_tile != 0u)
        return tcur(o);
    return pel_cur(idx, comp, pos + o, sz);
}
#define PEL_SPATIAL(o) pel_spatial(idx, comp, pos, sz, o)

/* ADR-0134 promises tile=0 and tile=1 are bit-identical. The two paths share
 * this one function and differ only in where PEL_SPATIAL() reads from, but they
 * are two separately specialized pipelines, and a driver may optimize each one
 * differently. Two such freedoms broke the identity on NVIDIA (BUG-027):
 *
 *  - a division by a loop-invariant divisor (the old `-ssd / hs2` inside the
 *    window scan) was lowered differently per pipeline, for example a hoisted
 *    reciprocal in one and a per-iteration divide in the other, and
 *  - sums and products without `precise` were contracted or reassociated
 *    differently.
 *
 * Hence every floating-point value on the output path is `precise` (SPIR-V
 * NoContraction: stated order, no implicit FMA fusion); no division sits inside
 * a loop (the loop-invariant factors kr, kd and kt are formed once, with exp(x)
 * folded to exp2(x * LOG2E)); explicit fma() marks the intended fused
 * accumulations; and mix()/smoothstep() are spelled out (see pel_lerp()). */
float denoise(const ivec2 pos, const int idx, const int comp,
              float sigmaS, float sigmaT, float strength_p) {
    ivec2 sz = imageSize(output_images[idx]);
    precise float C = PEL_SPATIAL(ivec2(0, 0));
    /* --- spatial NLM-lite joint bilateral over the current frame --- */
    precise float numS = C;
    precise float denS = 1.0;
    if (patch_radius > 0) {
        /* wr = exp(-(ssd / 9) / hs2) and wd = exp(-r^2 / (2 * sd2)), with the
         * loop-invariant divisors folded once into kr and kd. */
        precise float hs2 = sigmaS * sigmaS + EPS;
        precise float sd2 = float(patch_radius * patch_radius) + EPS;
        precise float kr = -LOG2E / (9.0 * hs2);
        precise float kd = -LOG2E / (2.0 * sd2);
        for (int dy = -patch_radius; dy <= patch_radius; dy++) {
            for (int dx = -patch_radius; dx <= patch_radius; dx++) {
                if (dx == 0 && dy == 0) continue;
                precise float ssd = 0.0;
                for (int ky = -1; ky <= 1; ky++) {
                    for (int kx = -1; kx <= 1; kx++) {
                        precise float d = PEL_SPATIAL(ivec2(kx, ky)) -
                                          PEL_SPATIAL(ivec2(dx + kx, dy + ky));
                        ssd = fma(d, d, ssd);
                    }
                }
                precise float w = exp2(ssd * kr) *
                                  exp2(float(dx * dx + dy * dy) * kd);
                numS = fma(w, PEL_SPATIAL(ivec2(dx, dy)), numS);
                denS += w;
            }
        }
    }
    /* --- temporal gated averaging over previous frames (same coord) --- */
    precise float numT = C;
    precise float denT = 1.0;
    if ((flags & FLAG_TEMPORAL) != 0) {
        /* w = exp(-delta^2 / ht2) * decay, the divisor folded once into kt. */
        precise float kt = -LOG2E / (sigmaT * sigmaT + EPS);
        precise float decay = 1.0;
        precise float conf = 0.0;
        bool mc = (flags & FLAG_MOTION_COMP) != 0 && grid_cols != 0;
        if (mc) {
            int cw = (idx > 0) ? chroma_shift_w : 0;
            int chh = (idx > 0) ? chroma_shift_h : 0;
            conf = pel_mc_conf(pos << ivec2(cw, chh));
        }
        for (int t = 1; t <= actual_prev; t++) {
            precise float p = pel_prev(t, idx, comp, pos, sz);
            if (mc) {
                /* blend same-coord <-> motion-warped by per-block confidence;
                 * low conf (noise-matched MV) falls back toward the same-coord
                 * sample, and the temporal_cut gate below still rejects
                 * bad/occluded taps either way. */
                p = pel_lerp(p, pel_prev_mc(t, idx, comp, pos, sz), conf);
            }
            precise float delta = abs(C - p);
            if (delta > temporal_cut) break;
            decay *= temporal_decay;
            precise float w = exp2(delta * delta * kt) * decay;
            numT = fma(w, p, numT);
            denT += w;
        }
        /* --- forward-lookahead tap (ADR-0137): one same-coord NEXT-frame
         * sample, tcut-gated like the prev taps. Recovers the leading frame of
         * a held animation drawing (the trailing frame already gets the causal
         * prev). --- */
        if (actual_next > 0) {
            precise float p = pel_to_sample(
                imageLoad(next0_images[idx],
                          clamp(pos, ivec2(0), sz - ivec2(1)))[comp]);
            precise float delta = abs(C - p);
            if (delta <= temporal_cut) {
                precise float w = exp2(delta * delta * kt) * temporal_decay;
                numT = fma(w, p, numT);
                denT += w;
            }
        }
    }
    /* --- combine, then dry/wet --- */
    precise float num = (1.0 - blend) * numS + blend * numT;
    precise float den = (1.0 - blend) * denS + blend * denT;
    precise float filtered = num / max(den, EPS);
    precise float strength = strength_p;
    if ((flags & FLAG_PROTECT_DETAIL) != 0 && patch_radius > 0) {
        precise float mean = 0.0;
        for (int dy = -1; dy <= 1; dy++)
            for (int dx = -1; dx <= 1; dx++)
                mean += PEL_SPATIAL(ivec2(dx, dy));
        mean *= (1.0 / 9.0);
        precise float varr = 0.0;
        for (int dy = -1; dy <= 1; dy++) {
            for (int dx = -1; dx <= 1; dx++) {
                precise float d = PEL_SPATIAL(ivec2(dx, dy)) - mean;
                varr = fma(d, d, varr);
            }
        }
        precise float activity = sqrt(varr * (1.0 / 9.0));
        /* smoothstep(sigmaS, 3 * sigmaS + EPS, activity), spelled out. */
        precise float e =
            clamp((activity - sigmaS) / (2.0 * sigmaS + EPS), 0.0, 1.0);
        precise float protect = e * e * (3.0 - 2.0 * e);
        strength *= (1.0 - protect);
    }
    precise float outv = pel_lerp(C, filtered, clamp(strength, 0.0, 1.0));
    return clamp(outv, 0.0, 1.0);
}

void main()
{
    ivec2 size;
    const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);
    /* slice = wg_index & (SLICES - 1) — equivalent to % SLICES (a power of
     * two). */
    uint wg = gl_WorkGroupID.y * gl_NumWorkGroups.x + gl_WorkGroupID.x;
    uint slice = wg & (SLICES - 1u);

    /* meta=1: zero this workgroup's partial sums. want_meta is a push
     * constant, so this branch and its barrier are workgroup-uniform. */
    if (want_meta != 0) {
        if (gl_LocalInvocationIndex < STATS)
            s_res[gl_LocalInvocationIndex] = 0u;
        if (gl_LocalInvocationIndex < 2u)
            s_cnt[gl_LocalInvocationIndex] = 0u;
        barrier();
    }

    /* Preserves the pre-FFmpeg-9 unrolled semantics exactly: the C generator
     * emitted a per-plane `if (IS_WITHIN(pos, size)) { ... }` block — NOT an
     * early return — so a position outside a subsampled chroma plane simply
     * skips that plane and the loop continues to the next one. */
    for (uint i = 0; i < planes; i++) {
        const int idx = int(i);
        size = imageSize(output_images[idx]);
        /* A semi-planar chroma plane (NV12/P010/NV16/NV24/P016) is a TWO-
         * component image holding U in .x and V in .y — both components are
         * real picture data and both get denoised. sigma_s/sigma_t/strength are
         * indexed by PLANE, not by component, so both components use the chroma
         * lanes at index 1 (index 2 has no plane and must not be used). */
        const uint ncomp = pel_component_count(i);
        const bool sel = (plane_mask & (1u << i)) != 0u;
        const bool inb = all(lessThan(pos, size));

        /* Never synthesise the stored texel. Load it, overwrite only the
         * component(s) actually filtered, and store the whole vec4: a
         * synthesised vec4(ov, 0, 0, 1) writes a constant 0 into the plane's
         * second component, annihilating V on every semi-planar format. */
        vec4 outv = inb ? imageLoad(cur_images[idx], pos) : vec4(0.0);

        for (uint c = 0u; c < ncomp; c++) {
            const int comp = int(c);

            /* Cooperative tile load runs in uniform control flow (all
             * invocations, before the per-thread bounds guard) so its barriers
             * are valid. use_tile, plane_mask and ncomp are all specialization
             * constants, hence workgroup-uniform. */
            if (use_tile != 0u && sel)
                pel_load_tile(idx, comp, size);

            if (inb && sel) {
                float inv = pel_to_sample(outv[comp]);
                float ov = denoise(pos, idx, comp,
                                   sigma_s[i], sigma_t[i], strength[i]);
                outv[comp] = pel_to_storage(ov);
                /* meta=1 residual free-ride: the luma plane drives the sigma /
                 * PSNR estimate; chroma planes feed the U/V residual energy.
                 * On a semi-planar plane the second component IS V, so it folds
                 * into the V sum; cnt_c stays the per-chroma-PLANE pixel count
                 * so attach_interop()'s divisor is unchanged. Each invocation
                 * adds at most once per statistic, which the bounds test
                 * relies on. */
                if (want_meta != 0) {
                    float r = min(abs(inv - ov), RES_MAX);
                    if (i == 0u) {
                        atomicAdd(s_res[0], pel_res_fixed(r));
                        atomicAdd(s_res[3], pel_res_fixed(r * r));
                        atomicAdd(s_cnt[0], 1u);
                    } else if (i == 1u) {
                        if (c == 0u) {
                            atomicAdd(s_res[1], pel_res_fixed(r));
                            atomicAdd(s_cnt[1], 1u);
                        } else {
                            atomicAdd(s_res[2], pel_res_fixed(r));
                        }
                    } else if (i == 2u) {
                        atomicAdd(s_res[2], pel_res_fixed(r));
                    }
                }
            }
        }

        if (inb)
            imageStore(output_images[idx], pos, outv);
    }

    /* meta=1: one global add per statistic per workgroup (uniform branch). */
    if (want_meta != 0) {
        barrier();
        const uint li = gl_LocalInvocationIndex;
        if (li < STATS)
            pel_acc64(li * SLICES + slice, s_res[li]);
        else if (li == STATS && s_cnt[0] != 0u)
            atomicAdd(cnt_y[slice], s_cnt[0]);
        else if (li == STATS + 1u && s_cnt[1] != 0u)
            atomicAdd(cnt_c[slice], s_cnt[1]);
    }
}
