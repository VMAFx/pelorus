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
 * Pelorus block-matching motion estimator (ADR-0113 / ADR-0115), with a
 * subgroup SAD reduction (ADR-0133) and a parabolic quarter-pel refinement
 * (ADR-0130).
 *
 * Single source of truth for the search. Before FFmpeg 9 this text lived twice:
 * as an inline-GLSL string built at runtime inside vf_pelorus_mc_vulkan.c, and
 * as a standalone reference .comp. FFmpeg 9 removed the runtime GLSL builder
 * (GLSLC/GLSLD and ff_vk_shader_init), so the shader is compiled to SPIR-V at
 * build time and linked in — retiring that duplication and the whole class of
 * lockstep-drift defects.
 *
 * ONE WORKGROUP PER BLOCK: the workgroup's invocations cooperatively SAD the
 * current block against the reference block displaced by a candidate MV. The
 * search is a predictor-seeded diamond descent transcribed from FFmpeg's
 * libavfilter/motion_estimation.c (ff_me_search_epzs / _ds); every predictor is
 * resolved BEFORE the dispatch, so there is no cross-workgroup intra-frame
 * neighbour race.
 */

#pragma shader_stage(compute)

#extension GL_EXT_shader_image_load_formatted : require
#extension GL_EXT_nonuniform_qualifier : require
#extension GL_KHR_shader_subgroup_basic : require
#extension GL_KHR_shader_subgroup_arithmetic : require

/* Workgroup-size IDs 253/254/255 are reserved by ff_vk_shader_load(); the C
 * side loads them with (PEL_MC_BLOCK_DIM, PEL_MC_BLOCK_DIM, 1). */
layout (local_size_x_id = 253, local_size_y_id = 254, local_size_z_id = 255) in;

/* Max supported block edge, specialized from the C PEL_MC_BLOCK_DIM so a change
 * there propagates into the shared-array sizing instead of silently diverging
 * (this replaces the old PEL_MC_SAD_LANES_STR string splice). The active block
 * edge is the `bsize` push constant (<= block_dim), which gates which lanes
 * contribute, so one pipeline serves every bsize. */
layout (constant_id = 0) const uint block_dim = 32;

layout (push_constant, std430) uniform pushConstants {
    int   width;
    int   height;
    int   grid_cols;
    int   grid_rows;
    int   bsize;
    int   search;
    int   gpred_x;
    int   gpred_y;
    int   has_prev;
    int   _pad0;
};

/* Binding order MUST match the C descriptor array in init_filter(). The image
 * bindings are per-plane arrays (.elems = planes, the ADR-0129 fix): the shader
 * only ever reads plane 0 (luma), but ff_vk_shader_update_img_array() writes one
 * descriptor per plane, so the array must hold every plane. */
layout (set = 0, binding = 0) uniform readonly image2D cur_image[];
layout (set = 0, binding = 1) uniform readonly image2D ref_image[];

layout (set = 0, binding = 2, std430) buffer mv_x_buf {
    int mv_x[];
};

layout (set = 0, binding = 3, std430) buffer mv_y_buf {
    int mv_y[];
};

layout (set = 0, binding = 4, std430) buffer sad_buf {
    uint sad_out[];
};

layout (set = 0, binding = 5, std430) readonly buffer prev_mv_buf {
    int prev_mv[];
};

const float SAD_SCALE = 256.0;

/* One slot per subgroup (worst case subgroupSize==1 => block_dim*block_dim
 * subgroups). gl_SubgroupID indexes this, so a smaller size OOB-writes. */
shared float s_part[block_dim * block_dim];
shared float s_sad0; /* block-SAD reduction result                  */
shared int   s_best_x;
shared int   s_best_y;
shared float s_best_cost;

float fetchCur(int px, int py) {
    px = clamp(px, 0, width  - 1);
    py = clamp(py, 0, height - 1);
    return imageLoad(cur_image[0], ivec2(px, py)).x;
}

float fetchRef(int px, int py) {
    px = clamp(px, 0, width  - 1);
    py = clamp(py, 0, height - 1);
    return imageLoad(ref_image[0], ivec2(px, py)).x;
}

float block_sad(int blk_x, int blk_y, int mvx, int mvy, uint lidx,
                int lx, int ly) {
    float d = 0.0;
    if (lx < bsize && ly < bsize) {
        int cx = blk_x + lx;
        int cy = blk_y + ly;
        float c = fetchCur(cx, cy);
        float r = fetchRef(cx + mvx, cy + mvy);
        d = abs(c - r);
    }
    /* Two-level reduction: sum within each subgroup (one op, no shared traffic),
     * then lane 0 combines the per-subgroup partials. Replaces the 10-step
     * shared-memory barrier tree — mc is the throughput bottleneck and block_sad
     * runs once per search candidate. */
    float sg = subgroupAdd(d);
    if (subgroupElect()) s_part[gl_SubgroupID] = sg;
    barrier();
    if (lidx == 0u) {
        float t = 0.0;
        for (uint i = 0u; i < gl_NumSubgroups; i++) t += s_part[i];
        s_sad0 = t;
    }
    barrier();
    return s_sad0;
}

void eval_candidate(int blk_x, int blk_y, int cand_x, int cand_y, uint lidx,
                    int lx, int ly) {
    cand_x = clamp(cand_x, -search, search);
    cand_y = clamp(cand_y, -search, search);
    float cost = block_sad(blk_x, blk_y, cand_x, cand_y, lidx, lx, ly);
    if (lidx == 0u && cost < s_best_cost) {
        s_best_cost = cost;
        s_best_x = cand_x;
        s_best_y = cand_y;
    }
    barrier();
}

void main()
{
    int bx = int(gl_WorkGroupID.x);
    int by = int(gl_WorkGroupID.y);
    if (bx >= grid_cols || by >= grid_rows)
        return;
    int bidx  = by * grid_cols + bx;
    int blk_x = bx * bsize;
    int blk_y = by * bsize;
    uint lidx = gl_LocalInvocationIndex;
    int  lx   = int(gl_LocalInvocationID.x);
    int  ly   = int(gl_LocalInvocationID.y);
    /* Frame 0 / no reference: emit a zero MV, no search. has_prev is a push
     * constant, so the whole workgroup takes this branch uniformly and returns
     * together — the lane-0-only writes before the barrier-free return are safe
     * ONLY because the exit is uniform (do not gate on a non-uniform cond). */
    if (has_prev == 0) {
        if (lidx == 0u) {
            mv_x[bidx]    = 0;
            mv_y[bidx]    = 0;
            sad_out[bidx] = 0u;
        }
        return;
    }
    if (lidx == 0u) {
        s_best_cost = 1e30;
        s_best_x = 0;
        s_best_y = 0;
    }
    barrier();
    eval_candidate(blk_x, blk_y, 0, 0, lidx, lx, ly);
    eval_candidate(blk_x, blk_y, gpred_x, gpred_y, lidx, lx, ly);
    eval_candidate(blk_x, blk_y, prev_mv[2 * bidx], prev_mv[2 * bidx + 1], lidx, lx, ly);
    int step = max(search / 2, 1);
    int guard = 0;
    while (step > 0 && guard < 64) {
        int cx = s_best_x;
        int cy = s_best_y;
        eval_candidate(blk_x, blk_y, cx - step, cy,        lidx, lx, ly);
        eval_candidate(blk_x, blk_y, cx + step, cy,        lidx, lx, ly);
        eval_candidate(blk_x, blk_y, cx,        cy - step, lidx, lx, ly);
        eval_candidate(blk_x, blk_y, cx,        cy + step, lidx, lx, ly);
        if (s_best_x == cx && s_best_y == cy)
            step = step >> 1;
        guard++;
    }
    /* Sub-pel refinement: fit a parabola to the SAD surface across the integer
     * minimum and its 4 axis-neighbours; the vertex gives a [-0.5,0.5] offset
     * per axis. Emit the MV in QUARTER-PEL fixed-point (stored = round(pel*4)).
     * The four extra block_sad() calls are cooperative (whole workgroup, barrier
     * internally); the leading barrier publishes the search's s_best_* to all
     * lanes, and a barrier after each SAD prevents a WAR race on s_sad0. */
    barrier();
    int   bxi = s_best_x;
    int   byi = s_best_y;
    float sc  = s_best_cost;
    float sl  = block_sad(blk_x, blk_y, bxi - 1, byi,     lidx, lx, ly);
    barrier();
    float sr  = block_sad(blk_x, blk_y, bxi + 1, byi,     lidx, lx, ly);
    barrier();
    float st  = block_sad(blk_x, blk_y, bxi,     byi - 1, lidx, lx, ly);
    barrier();
    float sb  = block_sad(blk_x, blk_y, bxi,     byi + 1, lidx, lx, ly);
    if (lidx == 0u) {
        float dx2 = sl - 2.0 * sc + sr;
        float dy2 = st - 2.0 * sc + sb;
        float sx  = (dx2 > 1e-6) ? clamp(0.5 * (sl - sr) / dx2, -0.5, 0.5) : 0.0;
        float sy  = (dy2 > 1e-6) ? clamp(0.5 * (st - sb) / dy2, -0.5, 0.5) : 0.0;
        mv_x[bidx]    = int(round((float(bxi) + sx) * 4.0));
        mv_y[bidx]    = int(round((float(byi) + sy) * 4.0));
        sad_out[bidx] = uint(clamp(sc, 0.0, 1e6) * SAD_SCALE);
    }
}
