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
 * constants: the plane count, the `planes` bitmask, and the ADR-0134 opt-in
 * shared-memory tiling switch (`tile`). Specialization happens at pipeline
 * creation, so the per-plane loop and the tile/no-tile branch are folded away
 * exactly as the generated GLSL folded them.
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
 *                memory instead of re-reading the image. Bit-identical output. */
layout (constant_id = 0) const uint planes     = 0;
layout (constant_id = 1) const uint plane_mask = 0xf;
layout (constant_id = 2) const uint use_tile   = 0;

/* PEL_HALO = max patch_radius (3) + the 1-px patch ring.
 * PEL_TILE = 16 (the workgroup dim, see ff_vk_shader_load) + 2 * PEL_HALO. */
#define PEL_HALO 4
#define PEL_TILE 24

/* Mirrors the C `opts` struct byte-for-byte (std430, as before). The C struct
 * has a trailing int32_t _pad[1] the block does not need to declare. */
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
};

/* Binding order MUST match the C descriptor array exactly (inputs first, output
 * then the forward tap last — the Nin / bwdif binding-order contract):
 * cur=0, prev0-3=1-4, stat=5, mv=6, conf=7, output=8, next0=9. */
layout (set = 0, binding = 0) uniform readonly  image2D cur_images[];
layout (set = 0, binding = 1) uniform readonly  image2D prev0_images[];
layout (set = 0, binding = 2) uniform readonly  image2D prev1_images[];
layout (set = 0, binding = 3) uniform readonly  image2D prev2_images[];
layout (set = 0, binding = 4) uniform readonly  image2D prev3_images[];

layout (set = 0, binding = 5, std430) buffer stat_buffer {
    uint abs_sum_y[16];
    uint abs_sum_u[16];
    uint abs_sum_v[16];
    uint sq_sum_y[16];
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

float pel_cur(int idx, ivec2 p, ivec2 sz) {
    return imageLoad(cur_images[idx], clamp(p, ivec2(0), sz - ivec2(1))).x;
}
float pel_prev(int t, int idx, ivec2 p, ivec2 sz) {
    ivec2 c = clamp(p, ivec2(0), sz - ivec2(1));
    if (t == 1) return imageLoad(prev0_images[idx], c).x;
    if (t == 2) return imageLoad(prev1_images[idx], c).x;
    if (t == 3) return imageLoad(prev2_images[idx], c).x;
    return imageLoad(prev3_images[idx], c).x;
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
    return float(conf_packed[cell.y * grid_cols + cell.x] & 0xFFu) / 255.0;
}
float pel_prev_mc(int t, int idx, ivec2 pos, ivec2 sz) {
    int cw = (idx > 0) ? chroma_shift_w : 0;
    int ch = (idx > 0) ? chroma_shift_h : 0;
    ivec2 lpos = pos << ivec2(cw, ch);
    vec2 mvl = pel_mc_mv(lpos);                /* MV in luma pixels */
    vec2 mvp = vec2(mvl.x / float(1 << cw), mvl.y / float(1 << ch));
    vec2 sp = vec2(pos) + mvp;                 /* sub-pel sample point */
    ivec2 ip = ivec2(floor(sp));
    vec2 f = sp - vec2(ip);
    float p00 = pel_prev(t, idx, ip + ivec2(0, 0), sz);
    float p10 = pel_prev(t, idx, ip + ivec2(1, 0), sz);
    float p01 = pel_prev(t, idx, ip + ivec2(0, 1), sz);
    float p11 = pel_prev(t, idx, ip + ivec2(1, 1), sz);
    return mix(mix(p00, p10, f.x), mix(p01, p11, f.x), f.y);
}

/* Shared-memory tiling of the current-frame spatial window (the NLM range term
 * re-reads an overlapping (2*patchR+3)^2 window ~9x per pixel — fetch-bound, not
 * ALU-bound). Each workgroup cooperatively loads its window (the 16x16 tile plus
 * a PEL_HALO ring, clamped) into s_tile once per plane, then every spatial read
 * hits shared memory instead of the image. pel_load_tile() runs in uniform
 * control flow (outside the bounds guard, under spec-constant-only conditions)
 * so its barriers are workgroup-uniform; the leading barrier protects the prior
 * plane's readers before this plane overwrites s_tile. When use_tile == 0 the
 * whole path is specialized away and s_tile shrinks to a single element. */
shared float s_tile[(use_tile != 0u) ? (PEL_TILE * PEL_TILE) : 1];

void pel_load_tile(int idx, ivec2 sz) {
    ivec2 wgsz = ivec2(gl_WorkGroupSize.xy);
    ivec2 base = ivec2(gl_WorkGroupID.xy) * wgsz - PEL_HALO;
    uint n = uint(PEL_TILE * PEL_TILE);
    uint stride = gl_WorkGroupSize.x * gl_WorkGroupSize.y;
    barrier();
    for (uint k = gl_LocalInvocationIndex; k < n; k += stride) {
        ivec2 t = ivec2(int(k) - (int(k) / PEL_TILE) * PEL_TILE,
                        int(k) / PEL_TILE);
        s_tile[k] = pel_cur(idx, base + t, sz);
    }
    barrier();
}
float tcur(ivec2 off) {
    ivec2 lc = ivec2(gl_LocalInvocationID.xy) + PEL_HALO + off;
    return s_tile[lc.y * PEL_TILE + lc.x];
}

/* The spatial fetch, either way. Was a C-selected #define PEL_SPATIAL(o); the
 * use_tile specialization constant folds this branch at pipeline creation. */
float pel_spatial(int idx, ivec2 pos, ivec2 sz, ivec2 o) {
    if (use_tile != 0u)
        return tcur(o);
    return pel_cur(idx, pos + o, sz);
}
#define PEL_SPATIAL(o) pel_spatial(idx, pos, sz, o)

float denoise(const ivec2 pos, const int idx,
              float sigmaS, float sigmaT, float strength_p) {
    ivec2 sz = imageSize(output_images[idx]);
    float C = PEL_SPATIAL(ivec2(0, 0));
    /* --- spatial NLM-lite joint bilateral over the current frame --- */
    float numS = C; float denS = 1.0;
    if (patch_radius > 0) {
        float hs2 = sigmaS * sigmaS + EPS;
        float sd2 = float(patch_radius * patch_radius) + EPS;
        for (int dy = -patch_radius; dy <= patch_radius; dy++) {
            for (int dx = -patch_radius; dx <= patch_radius; dx++) {
                if (dx == 0 && dy == 0) continue;
                float ssd = 0.0;
                for (int ky = -1; ky <= 1; ky++) {
                    for (int kx = -1; kx <= 1; kx++) {
                        float a = PEL_SPATIAL(ivec2(kx, ky));
                        float b = PEL_SPATIAL(ivec2(dx + kx, dy + ky));
                        ssd += (a - b) * (a - b);
                    }
                }
                ssd /= 9.0;
                float wr = exp(-ssd / hs2);
                float wd = exp(-float(dx * dx + dy * dy) / (2.0 * sd2));
                float w = wr * wd;
                numS += w * PEL_SPATIAL(ivec2(dx, dy));
                denS += w;
            }
        }
    }
    /* --- temporal gated averaging over previous frames (same coord) --- */
    float numT = C; float denT = 1.0;
    if ((flags & FLAG_TEMPORAL) != 0) {
        float ht2 = sigmaT * sigmaT + EPS;
        float decay = 1.0;
        for (int t = 1; t <= actual_prev; t++) {
            float p;
            if ((flags & FLAG_MOTION_COMP) != 0 && grid_cols != 0) {
                /* blend same-coord <-> motion-warped by per-block confidence;
                 * low conf (noise-matched MV) falls back toward the same-coord
                 * sample, and the temporal_cut gate below still rejects
                 * bad/occluded taps either way. */
                int cw = (idx > 0) ? chroma_shift_w : 0;
                int chh = (idx > 0) ? chroma_shift_h : 0;
                float conf = pel_mc_conf(pos << ivec2(cw, chh));
                p = mix(pel_prev(t, idx, pos, sz),
                        pel_prev_mc(t, idx, pos, sz), conf);
            } else {
                p = pel_prev(t, idx, pos, sz);
            }
            float delta = abs(C - p);
            if (delta > temporal_cut) break;
            decay *= temporal_decay;
            float w = exp(-(delta * delta) / ht2) * decay;
            numT += w * p;
            denT += w;
        }
        /* --- forward-lookahead tap (ADR-0137): one same-coord NEXT-frame
         * sample, tcut-gated like the prev taps. Recovers the leading frame of
         * a held animation drawing (the trailing frame already gets the causal
         * prev). --- */
        if (actual_next > 0) {
            float p = imageLoad(next0_images[idx], clamp(pos, ivec2(0), sz - ivec2(1))).x;
            float delta = abs(C - p);
            if (delta <= temporal_cut) {
                float w = exp(-(delta * delta) / ht2) * temporal_decay;
                numT += w * p; denT += w;
            }
        }
    }
    /* --- combine, then dry/wet --- */
    float num = (1.0 - blend) * numS + blend * numT;
    float den = (1.0 - blend) * denS + blend * denT;
    float filtered = num / max(den, EPS);
    float strength = strength_p;
    if ((flags & FLAG_PROTECT_DETAIL) != 0 && patch_radius > 0) {
        float mean = 0.0;
        for (int dy = -1; dy <= 1; dy++)
            for (int dx = -1; dx <= 1; dx++)
                mean += PEL_SPATIAL(ivec2(dx, dy));
        mean /= 9.0;
        float varr = 0.0;
        for (int dy = -1; dy <= 1; dy++) {
            for (int dx = -1; dx <= 1; dx++) {
                float d = PEL_SPATIAL(ivec2(dx, dy)) - mean;
                varr += d * d;
            }
        }
        float activity = sqrt(varr / 9.0);
        float protect = smoothstep(sigmaS, sigmaS * 3.0 + EPS, activity);
        strength *= (1.0 - protect);
    }
    float outv = mix(C, filtered, clamp(strength, 0.0, 1.0));
    return clamp(outv, 0.0, 1.0);
}

void main()
{
    /* Fixed-point scale for the uint32 residual accumulators. Kept at 1e3 (not
     * 1e6) so the per-slice sum cannot overflow uint32: a slice covers ~W*H/16
     * pixels (2.07M at 8K) and the worst-case sum is 2.07M*1.0*1e3 = 2.07e9 <
     * UINT32_MAX. GS=1e6 silently wrapped at >=8K (and at 4K for residuals
     * >=0.008). residual_energy (the mean) stays accurate; sq_sum/sigma_est are
     * a COARSE estimate at 1e3 (a precise sigma would need a 64-bit atomic
     * accumulator — a documented follow-up). meta=1 telemetry only; no pixel
     * effect. Mirror the divisor in attach_interop(). */
    const float GS = 1000.0;
    ivec2 size;
    const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);
    /* slice = wg_index & 15 — equivalent to % 16u (PEL_SLICES is a power of
     * two). */
    uint wg = gl_WorkGroupID.y * gl_NumWorkGroups.x + gl_WorkGroupID.x;
    uint slice = wg & 15u;

    /* Preserves the pre-FFmpeg-9 unrolled semantics exactly: the C generator
     * emitted a per-plane `if (IS_WITHIN(pos, size)) { ... }` block — NOT an
     * early return — so a position outside a subsampled chroma plane simply
     * skips that plane and the loop continues to the next one. */
    for (uint i = 0; i < planes; i++) {
        const int idx = int(i);
        size = imageSize(output_images[idx]);

        /* Cooperative tile load runs in uniform control flow (all invocations,
         * before the per-thread bounds guard) so its barriers are valid. Both
         * conditions are specialization constants, hence workgroup-uniform. */
        if (use_tile != 0u && (plane_mask & (1u << i)) != 0u)
            pel_load_tile(idx, size);

        if (all(lessThan(pos, size))) {
            if ((plane_mask & (1u << i)) != 0u) {
                float inv = imageLoad(cur_images[idx], pos).x;
                float ov = denoise(pos, idx, sigma_s[i], sigma_t[i], strength[i]);
                imageStore(output_images[idx], pos, vec4(ov, 0.0, 0.0, 1.0));
                /* meta=1 residual free-ride: the luma plane drives the sigma /
                 * PSNR estimate; chroma planes feed the U/V residual energy. */
                if (i == 0u) {
                    if (want_meta != 0) {
                        float r = abs(inv - ov);
                        atomicAdd(abs_sum_y[slice], uint(r * GS));
                        atomicAdd(sq_sum_y[slice],  uint(r * r * GS));
                        atomicAdd(cnt_y[slice],     1u);
                    }
                } else if (i == 1u) {
                    if (want_meta != 0) {
                        atomicAdd(abs_sum_u[slice], uint(abs(inv - ov) * GS));
                        atomicAdd(cnt_c[slice],     1u);
                    }
                } else if (i == 2u) {
                    if (want_meta != 0) {
                        atomicAdd(abs_sum_v[slice], uint(abs(inv - ov) * GS));
                    }
                }
            } else {
                imageStore(output_images[idx], pos, imageLoad(cur_images[idx], pos));
            }
        }
    }
}
