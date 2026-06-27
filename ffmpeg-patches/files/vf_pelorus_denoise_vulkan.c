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

/**
 * @file
 * Pelorus temporal denoise, Vulkan compute (zero-copy, pre-encode).
 *
 * An edge-preserving spatio-temporal denoiser (PEL_DENOISER_BILATERAL_TEMPORAL)
 * over a CAUSAL window [cur, prev_1 .. prev_n] held in VRAM. The spatial term is
 * an NLM-lite joint bilateral whose range weight is a 3x3-patch SSD (a real edge
 * has high patch SSD, collapsing the weight, so the filter refuses to average
 * across edges while flattening flat-region noise). The temporal term averages
 * the same-coordinate sample of each previous frame, gated per pixel by a
 * similarity threshold (a delta above `tcut` breaks the walk so motion /
 * scene-cuts cannot ghost) and decayed by `tdecay` per frame. No motion
 * compensation in v0.x — same-coordinate taps only. Runs entirely in VRAM so the
 * frame never leaves the GPU on its way to a hardware encoder.
 *
 * One-output-per-input: each input frame yields exactly one output. The filter
 * keeps a ring of up to PEL_DENOISE_MAX_PREV previous frames as cheap
 * av_frame_clone refcount bumps on the hwframe images (no pixel copy).
 *
 * Forward-lookahead (ADR-0137): with lookahead=1 (default) the temporal walk is
 * bidirectional — output is delayed by one frame (buffered .activate lifecycle)
 * so the denoise can also sample the NEXT frame (same-coordinate, tcut-gated like
 * the prev taps), closing the leading-frame gap of a held animation drawing. This
 * adds a 1-frame latency + an EOF flush of the last held frame. lookahead=0 is
 * the original purely-causal behaviour: process each frame immediately, no
 * latency, no flush — bit-identical to pre-ADR-0137.
 *
 * Interop: when `meta=1` the filter free-rides a residual reduction in the same
 * dispatch (atomic accumulation of |in-out| and (in-out)^2 into a HOST_VISIBLE
 * SSBO) and attaches a Pelorus PEL_SEC_DENOISE blob
 * (AV_FRAME_DATA_SEI_UNREGISTERED, UUID-keyed) so a downstream vmafx vf_libvmaf*
 * can react. See <pelorus/interop.h> and docs/metrics/denoise.md.
 *
 * The algorithm is kept in lockstep with the standalone reference shader
 * libpelorus/shaders/pelorus_denoise.comp (AGENTS hard rule 4).
 */

#include "libavutil/buffer.h"
#include "libavutil/frame.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "libavutil/vulkan_spirv.h"
#include "vulkan_filter.h"

#include "filters.h"
#include "video.h"

#include <math.h>
#include <string.h>

#include <pelorus/denoise.h>
#include <pelorus/interop.h>

#define PEL_SLICES 16

/* Upper bound on the MV/conf grid cell count accepted from untrusted side data.
 * grid_cols/grid_rows are each uint16 on the wire (up to 65535), so the raw
 * product can reach ~4.29e9 — past INT_MAX and a multi-GB allocation. Cap it to a
 * value that still covers any realistic frame grid (8K at a small block edge)
 * while rejecting absurd dimensions before the cell count is used for sizing. */
#define PEL_DENOISE_MAX_CELLS (8192u * 8192u)

/* Host-readback accumulator for the meta=1 residual free-ride. Sliced to spread
 * atomic contention, exactly as vf_pelorus_analyze does; summed host-side. */
typedef struct PelorusDenoiseBuf {
    uint32_t abs_sum_y[PEL_SLICES];  /* sum |in-out| * GS, luma                 */
    uint32_t abs_sum_u[PEL_SLICES];
    uint32_t abs_sum_v[PEL_SLICES];
    uint32_t sq_sum_y[PEL_SLICES];   /* sum (in-out)^2 * GS, luma               */
    uint32_t cnt_y[PEL_SLICES];      /* luma pixel count                        */
    uint32_t cnt_c[PEL_SLICES];      /* chroma pixel count (per chroma plane)   */
} PelorusDenoiseBuf;

typedef struct PelorusDenoiseVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;
    AVBufferPool *stat_buf_pool;

    /* push constants — mirror the GLSL std430 block below, byte-for-byte */
    struct {
        float sigma_s[4];     /* vec4 : per-plane spatial range sigma    @0  */
        float sigma_t[4];     /* vec4 : per-plane temporal gate sigma    @16 */
        float strength[4];    /* vec4 : per-plane dry/wet mix            @32 */
        float blend;          /*                                         @48 */
        float temporal_decay; /*                                         @52 */
        float temporal_cut;   /*                                         @56 */
        int32_t patch_radius; /*                                         @60 */
        int32_t n_prev;       /* configured temporal depth (0..MAX_PREV) @64 */
        int32_t actual_prev;  /* valid previous frames this dispatch     @68 */
        int32_t nb_planes;    /*                                         @72 */
        int32_t planes_mask;  /* plane bitmask to process                @76 */
        int32_t flags;        /* enum pel_denoise_flags                  @80 */
        uint32_t frame_idx;   /*                                         @84 */
        int32_t want_meta;    /* 1 => accumulate residual into the SSBO  @88 */
        int32_t grid_cols;    /* MV/conf grid cols (0 => no MC this frame)@92 */
        int32_t grid_rows;    /*                                         @96 */
        int32_t cell_w;       /* ceil(lumaW / grid_cols)                 @100*/
        int32_t cell_h;       /* ceil(lumaH / grid_rows)                 @104*/
        int32_t chroma_shift_w; /* log2_chroma_w (MV luma->plane scale)  @108*/
        int32_t chroma_shift_h; /*                                       @112*/
        float mv_scale;       /* stored MV -> luma px (0.25 = quarter-pel)@116*/
        int32_t actual_next;  /* forward taps this dispatch (0 or 1)     @120*/
        int32_t _pad[1];      /* pad to a 16-byte multiple (128 bytes)   @124*/
    } opts;

    /* AVOption-backed scalar mirrors (broadcast to all planes at init) */
    double opt_sigma_y, opt_sigma_c; /* spatial range sigma (sigma_s)         */
    double opt_sigma_t;              /* temporal gate sigma                   */
    double opt_strength_y, opt_strength_c;
    double opt_blend, opt_tdecay, opt_tcut;
    int patch_radius;
    int n_prev;
    int protect_detail;
    int planes; /* plane bitmask to process (const-folded into shader)        */
    int meta;   /* attach Pelorus interop side data                          */
    int mc;     /* motion-compensated temporal taps (consume pelorus_mc)     */
    int tile;   /* shared-memory tile the spatial window (ADR-0134, opt-in)   */
    int lookahead; /* forward-lookahead depth: 0 = causal, 1 = bidirectional  */
    int64_t frame_idx;

    /* Forward-lookahead buffered-filter state (ADR-0137): the held input frame
     * awaiting its forward tap (the NEXT frame). NULL when lookahead==0 or no
     * frame is currently held. */
    AVFrame *held;

    /* Per-frame MV (quarter-pel int16 (dx,dy)) + confidence (uint8) grids,
     * parsed from PEL_SEC_MOTION / PEL_SEC_MOTION_CONF and uploaded as SSBOs. */
    AVBufferPool *mv_buf_pool;
    AVBufferPool *conf_buf_pool;

    /* Causal previous-frame ring (clones — refcount bumps, no pixel copy). */
    AVFrame *ring[PEL_DENOISE_MAX_PREV];
    int ring_count; /* valid entries in ring[] (0..n_prev)                    */
} PelorusDenoiseVulkanContext;

/* Pure GLSL helpers + the denoise() function, spliced verbatim (avoids the
 * comma-in-C()-macro hazard). Kept in lockstep with the standalone reference
 * shader libpelorus/shaders/pelorus_denoise.comp. Normalized-float domain:
 * imageLoad returns [0,1] directly (no /65535). The current frame's plane is
 * cur_images[idx]; previous frames are prev0_images[idx]..prev3_images[idx],
 * indexed by the temporal walk via pel_prev(). */
static const char denoise_helpers_glsl[] =
    "const int FLAG_TEMPORAL = 1;\n"
    "const int FLAG_MOTION_COMP = 2;\n"
    "const int FLAG_PROTECT_DETAIL = 4;\n"
    "const float EPS = 1e-6;\n"
    "float pel_cur(int idx, ivec2 p, ivec2 sz) {\n"
    "    return imageLoad(cur_images[idx], clamp(p, ivec2(0), sz - ivec2(1))).x;\n"
    "}\n"
    "float pel_prev(int t, int idx, ivec2 p, ivec2 sz) {\n"
    "    ivec2 c = clamp(p, ivec2(0), sz - ivec2(1));\n"
    "    if (t == 1) return imageLoad(prev0_images[idx], c).x;\n"
    "    if (t == 2) return imageLoad(prev1_images[idx], c).x;\n"
    "    if (t == 3) return imageLoad(prev2_images[idx], c).x;\n"
    "    return imageLoad(prev3_images[idx], c).x;\n"
    "}\n"
    /* --- motion-compensated previous-frame fetch (ADR-0113) --- */
    "int pel_se16(uint v) { return int(v << 16) >> 16; }\n" /* sign-extend low 16 */
    "ivec2 pel_cell(ivec2 lpos) {\n"
    "    return clamp(lpos / ivec2(cell_w, cell_h), ivec2(0),\n"
    "                 ivec2(grid_cols - 1, grid_rows - 1));\n"
    "}\n"
    "vec2 pel_mc_mv(ivec2 lpos) {\n" /* nearest-cell quarter-pel MV, luma px */
    "    ivec2 cell = pel_cell(lpos);\n"
    "    uint packed = mv_packed[cell.y * grid_cols + cell.x];\n"
    "    return vec2(pel_se16(packed & 0xFFFFu), pel_se16(packed >> 16)) * mv_scale;\n"
    "}\n"
    "float pel_mc_conf(ivec2 lpos) {\n" /* nearest-cell confidence [0,1] */
    "    ivec2 cell = pel_cell(lpos);\n"
    "    return float(conf_packed[cell.y * grid_cols + cell.x] & 0xFFu) / 255.0;\n"
    "}\n"
    "float pel_prev_mc(int t, int idx, ivec2 pos, ivec2 sz) {\n"
    "    int cw = (idx > 0) ? chroma_shift_w : 0;\n"
    "    int ch = (idx > 0) ? chroma_shift_h : 0;\n"
    "    ivec2 lpos = pos << ivec2(cw, ch);\n"
    "    vec2 mvl = pel_mc_mv(lpos);\n"               /* MV in luma pixels */
    "    vec2 mvp = vec2(mvl.x / float(1 << cw), mvl.y / float(1 << ch));\n"
    "    vec2 sp = vec2(pos) + mvp;\n"                /* sub-pel sample point */
    "    ivec2 ip = ivec2(floor(sp));\n"
    "    vec2 f = sp - vec2(ip);\n"
    "    float p00 = pel_prev(t, idx, ip + ivec2(0, 0), sz);\n"
    "    float p10 = pel_prev(t, idx, ip + ivec2(1, 0), sz);\n"
    "    float p01 = pel_prev(t, idx, ip + ivec2(0, 1), sz);\n"
    "    float p11 = pel_prev(t, idx, ip + ivec2(1, 1), sz);\n"
    "    return mix(mix(p00, p10, f.x), mix(p01, p11, f.x), f.y);\n"
    "}\n";

/* Shared-memory tiling of the current-frame spatial window (the NLM range term
 * re-reads an overlapping (2*patchR+3)^2 window ~9x per pixel — fetch-bound, not
 * ALU-bound). Each workgroup cooperatively loads its window (the 16x16 tile plus
 * a PEL_HALO ring, clamped) into s_tile once per plane, then every spatial read
 * hits shared memory instead of the image. PEL_HALO = max patchR (3) + the 1-px
 * patch ring. pel_load_tile() runs in uniform control flow (outside the
 * IS_WITHIN guard) so its barriers are workgroup-uniform; the leading barrier
 * protects the prior plane's readers before this plane overwrites s_tile. Kept
 * in lockstep with the standalone reference shader (AGENTS rule 4). */
static const char tile_helpers_glsl[] =
    "void pel_load_tile(int idx, ivec2 sz) {\n"
    "    ivec2 wgsz = ivec2(gl_WorkGroupSize.xy);\n"
    "    ivec2 base = ivec2(gl_WorkGroupID.xy) * wgsz - PEL_HALO;\n"
    "    uint n = uint(PEL_TILE * PEL_TILE);\n"
    "    uint stride = gl_WorkGroupSize.x * gl_WorkGroupSize.y;\n"
    "    barrier();\n"
    "    for (uint k = gl_LocalInvocationIndex; k < n; k += stride) {\n"
    "        ivec2 t = ivec2(int(k) - (int(k) / PEL_TILE) * PEL_TILE,\n"
    "                        int(k) / PEL_TILE);\n"
    "        s_tile[k] = pel_cur(idx, base + t, sz);\n"
    "    }\n"
    "    barrier();\n"
    "}\n"
    "float tcur(ivec2 off) {\n"
    "    ivec2 lc = ivec2(gl_LocalInvocationID.xy) + PEL_HALO + off;\n"
    "    return s_tile[lc.y * PEL_TILE + lc.x];\n"
    "}\n";

/* The denoise() kernel itself — a second array so neither concatenated string
 * literal exceeds the C99 4095-char limit (emitted right after the helpers). */
static const char denoise_glsl[] =
    "float denoise(const ivec2 pos, const int idx,\n"
    "              float sigmaS, float sigmaT, float strength_p) {\n"
    "    ivec2 sz = imageSize(output_images[idx]);\n"
    "    float C = PEL_SPATIAL(ivec2(0, 0));\n"
    "    /* --- spatial NLM-lite joint bilateral over the current frame --- */\n"
    "    float numS = C; float denS = 1.0;\n"
    "    if (patch_radius > 0) {\n"
    "        float hs2 = sigmaS * sigmaS + EPS;\n"
    "        float sd2 = float(patch_radius * patch_radius) + EPS;\n"
    "        for (int dy = -patch_radius; dy <= patch_radius; dy++) {\n"
    "            for (int dx = -patch_radius; dx <= patch_radius; dx++) {\n"
    "                if (dx == 0 && dy == 0) continue;\n"
    "                float ssd = 0.0;\n"
    "                for (int ky = -1; ky <= 1; ky++) {\n"
    "                    for (int kx = -1; kx <= 1; kx++) {\n"
    "                        float a = PEL_SPATIAL(ivec2(kx, ky));\n"
    "                        float b = PEL_SPATIAL(ivec2(dx + kx, dy + ky));\n"
    "                        ssd += (a - b) * (a - b);\n"
    "                    }\n"
    "                }\n"
    "                ssd /= 9.0;\n"
    "                float wr = exp(-ssd / hs2);\n"
    "                float wd = exp(-float(dx * dx + dy * dy) / (2.0 * sd2));\n"
    "                float w = wr * wd;\n"
    "                numS += w * PEL_SPATIAL(ivec2(dx, dy));\n"
    "                denS += w;\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    /* --- temporal gated averaging over previous frames (same coord) --- */\n"
    "    float numT = C; float denT = 1.0;\n"
    "    if ((flags & FLAG_TEMPORAL) != 0) {\n"
    "        float ht2 = sigmaT * sigmaT + EPS;\n"
    "        float decay = 1.0;\n"
    "        for (int t = 1; t <= actual_prev; t++) {\n"
    "            float p;\n"
    "            if ((flags & FLAG_MOTION_COMP) != 0 && grid_cols != 0) {\n"
    /* blend same-coord <-> motion-warped by per-block confidence; low conf
     * (noise-matched MV) falls back toward the same-coord sample, and the
     * temporal_cut gate below still rejects bad/occluded taps either way. */
    "                int cw = (idx > 0) ? chroma_shift_w : 0;\n"
    "                int chh = (idx > 0) ? chroma_shift_h : 0;\n"
    "                float conf = pel_mc_conf(pos << ivec2(cw, chh));\n"
    "                p = mix(pel_prev(t, idx, pos, sz),\n"
    "                        pel_prev_mc(t, idx, pos, sz), conf);\n"
    "            } else {\n"
    "                p = pel_prev(t, idx, pos, sz);\n"
    "            }\n"
    "            float delta = abs(C - p);\n"
    "            if (delta > temporal_cut) break;\n"
    "            decay *= temporal_decay;\n"
    "            float w = exp(-(delta * delta) / ht2) * decay;\n"
    "            numT += w * p;\n"
    "            denT += w;\n"
    "        }\n"
    /* --- forward-lookahead tap (ADR-0137): one same-coord NEXT-frame sample,
     * tcut-gated like the prev taps. Recovers the leading frame of a held
     * animation drawing (the trailing frame already gets the causal prev). --- */
    "        if (actual_next > 0) {\n"
    "            float p = imageLoad(next0_images[idx], clamp(pos, ivec2(0), sz - ivec2(1))).x;\n"
    "            float delta = abs(C - p);\n"
    "            if (delta <= temporal_cut) {\n"
    "                float w = exp(-(delta * delta) / ht2) * temporal_decay;\n"
    "                numT += w * p; denT += w;\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    /* --- combine, then dry/wet --- */\n"
    "    float num = (1.0 - blend) * numS + blend * numT;\n"
    "    float den = (1.0 - blend) * denS + blend * denT;\n"
    "    float filtered = num / max(den, EPS);\n"
    "    float strength = strength_p;\n"
    "    if ((flags & FLAG_PROTECT_DETAIL) != 0 && patch_radius > 0) {\n"
    "        float mean = 0.0;\n"
    "        for (int dy = -1; dy <= 1; dy++)\n"
    "            for (int dx = -1; dx <= 1; dx++)\n"
    "                mean += PEL_SPATIAL(ivec2(dx, dy));\n"
    "        mean /= 9.0;\n"
    "        float varr = 0.0;\n"
    "        for (int dy = -1; dy <= 1; dy++) {\n"
    "            for (int dx = -1; dx <= 1; dx++) {\n"
    "                float d = PEL_SPATIAL(ivec2(dx, dy)) - mean;\n"
    "                varr += d * d;\n"
    "            }\n"
    "        }\n"
    "        float activity = sqrt(varr / 9.0);\n"
    "        float protect = smoothstep(sigmaS, sigmaS * 3.0 + EPS, activity);\n"
    "        strength *= (1.0 - protect);\n"
    "    }\n"
    "    float outv = mix(C, filtered, clamp(strength, 0.0, 1.0));\n"
    "    return clamp(outv, 0.0, 1.0);\n"
    "}\n";

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    int i;
    PelorusDenoiseVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd; /* GLSL macros require a var named `shd` */
    FFVkSPIRVCompiler *spv;
    uint8_t *spv_data = NULL;
    size_t spv_len = 0;
    void *spv_opaque = NULL;
    const int planes = av_pix_fmt_count_planes(vkctx->output_format);

    /* Broadcast luma/chroma scalars into the per-plane vec4s ({Y,Cb,Cr,A}). */
    s->opts.sigma_s[0] = (float)s->opt_sigma_y;
    s->opts.strength[0] = (float)s->opt_strength_y;
    for (i = 1; i < 4; i++) {
        s->opts.sigma_s[i] = (float)s->opt_sigma_c;
        s->opts.strength[i] = (float)s->opt_strength_c;
    }
    for (i = 0; i < 4; i++)
        s->opts.sigma_t[i] = (float)s->opt_sigma_t;
    s->opts.blend = (float)s->opt_blend;
    s->opts.temporal_decay = (float)s->opt_tdecay;
    s->opts.temporal_cut = (float)s->opt_tcut;
    s->opts.patch_radius = s->patch_radius;
    s->opts.n_prev = s->n_prev;
    s->opts.nb_planes = planes;
    s->opts.planes_mask = s->planes;
    s->opts.flags = PEL_DENOISE_FLAG_TEMPORAL;
    if (s->protect_detail)
        s->opts.flags |= PEL_DENOISE_FLAG_PROTECT_DETAIL;
    if (s->mc)
        s->opts.flags |= PEL_DENOISE_FLAG_MOTION_COMP;
    {
        const AVPixFmtDescriptor *pd = av_pix_fmt_desc_get(vkctx->output_format);
        s->opts.chroma_shift_w = pd ? pd->log2_chroma_w : 1;
        s->opts.chroma_shift_h = pd ? pd->log2_chroma_h : 1;
    }
    /* MV/conf grid dims are set per-frame in the dispatch; 0 => no MC. */
    s->opts.grid_cols = 0;
    s->opts.grid_rows = 0;
    s->opts.cell_w = 0;
    s->opts.cell_h = 0;
    s->opts.mv_scale = 0.25f; /* mc emits quarter-pel (Q2) */
    s->opts.actual_next = 0;  /* set per-dispatch; 0 => forward tap never fires */
    s->opts._pad[0] = 0;

    spv = ff_vk_spirv_init();
    if (!spv) {
        av_log(ctx, AV_LOG_ERROR, "Unable to initialize SPIR-V compiler!\n");
        return AVERROR_EXTERNAL;
    }

    s->qf = ff_vk_qf_find(vkctx, VK_QUEUE_COMPUTE_BIT, 0);
    if (!s->qf) {
        av_log(ctx, AV_LOG_ERROR, "Device has no compute queues!\n");
        err = AVERROR(ENOTSUP);
        goto fail;
    }

    RET(ff_vk_exec_pool_init(vkctx, s->qf, &s->e, s->qf->num * 4, 0, 0, 0, NULL));
    RET(ff_vk_shader_init(vkctx, shd, "pelorus_denoise",
                          VK_SHADER_STAGE_COMPUTE_BIT, NULL, 0, 16, 16, 1, 0));

    GLSLC(0, layout(push_constant, std430) uniform pushConstants {            );
    GLSLC(1,     vec4  sigma_s;                                               );
    GLSLC(1,     vec4  sigma_t;                                               );
    GLSLC(1,     vec4  strength;                                              );
    GLSLC(1,     float blend;                                                 );
    GLSLC(1,     float temporal_decay;                                        );
    GLSLC(1,     float temporal_cut;                                          );
    GLSLC(1,     int   patch_radius;                                          );
    GLSLC(1,     int   n_prev;                                                );
    GLSLC(1,     int   actual_prev;                                           );
    GLSLC(1,     int   nb_planes;                                             );
    GLSLC(1,     int   planes_mask;                                           );
    GLSLC(1,     int   flags;                                                 );
    GLSLC(1,     uint  frame_idx;                                             );
    GLSLC(1,     int   want_meta;                                             );
    GLSLC(1,     int   grid_cols;                                             );
    GLSLC(1,     int   grid_rows;                                             );
    GLSLC(1,     int   cell_w;                                                );
    GLSLC(1,     int   cell_h;                                                );
    GLSLC(1,     int   chroma_shift_w;                                        );
    GLSLC(1,     int   chroma_shift_h;                                        );
    GLSLC(1,     float mv_scale;                                              );
    GLSLC(1,     int   actual_next;                                           );
    GLSLC(0, };                                                               );
    GLSLC(0,                                                                  );
    ff_vk_shader_add_push_const(shd, 0, sizeof(s->opts),
                                VK_SHADER_STAGE_COMPUTE_BIT);

    /* Descriptor set 0. Inputs FIRST, output then the forward tap LAST (the Nin /
     * bwdif binding-order contract): binding 0 = current frame, 1..MAX_PREV =
     * previous frames, then the residual SSBO, then the MV/conf SSBOs, then the
     * output image, then the forward-lookahead next0 image (ADR-0137). Each image
     * binding is a per-plane array (.elems = planes), float representation,
     * storage image (sampler = VK_NULL_HANDLE, GENERAL layout, imageLoad). Final
     * binding map: cur=0, prev0-3=1-4, stat=5, mv=6, conf=7, output=8, next0=9. */
    {
        FFVulkanDescriptorSetBinding desc_set[] = {
            {
                .name = "cur_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
            {
                .name = "prev0_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
            {
                .name = "prev1_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
            {
                .name = "prev2_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
            {
                .name = "prev3_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
            {
                .name = "stat_buffer",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                .mem_layout = "std430",
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
                .buf_content = "uint abs_sum_y[16]; uint abs_sum_u[16]; "
                               "uint abs_sum_v[16]; uint sq_sum_y[16]; "
                               "uint cnt_y[16]; uint cnt_c[16];",
            },
            {
                /* Per-cell quarter-pel MV grid: (uint16 dx)|(uint16 dy<<16). */
                .name = "mv_grid",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                .mem_layout = "std430",
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
                .buf_content = "uint mv_packed[];",
            },
            {
                /* Per-cell motion confidence (0..255), one uint per cell. */
                .name = "conf_grid",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                .mem_layout = "std430",
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
                .buf_content = "uint conf_packed[];",
            },
            {
                .name = "output_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->output_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "writeonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
            {
                /* Forward-lookahead tap (ADR-0137): the NEXT frame, mirrors
                 * prev0_images exactly. Read same-coordinate, tcut-gated. */
                .name = "next0_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            },
        };
        RET(ff_vk_shader_add_descriptor_set(vkctx, shd, desc_set, 10, 0, 0));
    }

    GLSLD(denoise_helpers_glsl);
    /* tile=1 caches the current-frame spatial window in shared memory (a large
     * win on bandwidth-limited GPUs, ~neutral on cache-rich ones — see
     * ADR-0134); default tile=0 reads the image directly (bit-identical, the
     * flagship-first default). PEL_SPATIAL(o) is the spatial fetch either way;
     * GLSLD keeps the comma in pel_cur(idx, ...) out of the GLSLC C() macro. */
    if (s->tile) {
        GLSLC(0, #define PEL_HALO 4 /* max patchR (3) + 1-px patch ring */    );
        GLSLC(0, #define PEL_TILE 24 /* 16 (workgroup dim) + 2 * PEL_HALO */   );
        GLSLC(0, shared float s_tile[PEL_TILE * PEL_TILE];                     );
        GLSLD(tile_helpers_glsl);
        GLSLD("#define PEL_SPATIAL(o) tcur(o)");
    } else {
        GLSLD("#define PEL_SPATIAL(o) pel_cur(idx, pos + (o), sz)");
    }
    GLSLD(denoise_glsl);
    GLSLC(0, void main()                                                      );
    GLSLC(0, {                                                                );
    GLSLC(1,     const float GS = 1000000.0;                                  );
    GLSLC(1,     ivec2 size;                                                  );
    GLSLC(1,     const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);           );
    /* slice = wg_index & 15 — equivalent to % 16u (PEL_SLICES is a power of
     * two) and avoids a bare `%` in the av_bprintf format string. Kept on ONE
     * paren-balanced line: an unbalanced `(` in a GLSLC/GLSLF argument makes the
     * C preprocessor swallow the following macro call into this one's string. */
    GLSLC(1,     uint wg = gl_WorkGroupID.y * gl_NumWorkGroups.x + gl_WorkGroupID.x; );
    GLSLC(1,     uint slice = wg & 15u;                                       );
    for (i = 0; i < planes; i++) {
        GLSLC(0,                                                              );
        GLSLF(1, size = imageSize(output_images[%i]);                       ,i);
        /* Cooperative tile load runs in uniform control flow (all invocations,
         * before the per-thread IS_WITHIN guard) so its barriers are valid. */
        if (s->tile && (s->planes & (1 << i)))
            GLSLF(1, pel_load_tile(%i, size);                               ,i);
        GLSLC(1, if (IS_WITHIN(pos, size)) {                                  );
        if (s->planes & (1 << i)) {
            GLSLF(2, float inv = imageLoad(cur_images[%i], pos).x;          ,i);
            GLSLF(2, float ov = denoise(pos, %i, sigma_s[%i], sigma_t[%i], strength[%i]); ,i, i, i, i);
            GLSLF(2, imageStore(output_images[%i], pos, vec4(ov, 0.0, 0.0, 1.0));,i);
            if (i == 0) {
                /* meta=1 residual free-ride: luma plane drives the sigma /
                 * PSNR estimate; chroma planes feed the U/V residual energy. */
                GLSLC(2, if (want_meta != 0) {                                );
                GLSLC(3,     float r = abs(inv - ov);                         );
                GLSLC(3,     atomicAdd(abs_sum_y[slice], uint(r * GS));       );
                GLSLC(3,     atomicAdd(sq_sum_y[slice],  uint(r * r * GS));   );
                GLSLC(3,     atomicAdd(cnt_y[slice],     1u);                 );
                GLSLC(2, }                                                    );
            } else if (i == 1) {
                GLSLC(2, if (want_meta != 0) {                                );
                GLSLC(3,     atomicAdd(abs_sum_u[slice], uint(abs(inv - ov) * GS)););
                GLSLC(3,     atomicAdd(cnt_c[slice],     1u);                 );
                GLSLC(2, }                                                    );
            } else if (i == 2) {
                GLSLC(2, if (want_meta != 0) {                                );
                GLSLC(3,     atomicAdd(abs_sum_v[slice], uint(abs(inv - ov) * GS)););
                GLSLC(2, }                                                    );
            }
        } else {
            GLSLF(2, imageStore(output_images[%i], pos, imageLoad(cur_images[%i], pos)); ,i, i);
        }
        GLSLC(1, }                                                            );
    }
    GLSLC(0, }                                                                );

    RET(spv->compile_shader(vkctx, spv, shd, &spv_data, &spv_len, "main",
                            &spv_opaque));
    RET(ff_vk_shader_link(vkctx, shd, spv_data, spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    if (spv_opaque)
        spv->free_shader(spv, &spv_opaque);
    if (spv)
        spv->uninit(&spv);
    return err;
}

static void pel_sd_free(void *opaque, uint8_t *data)
{
    pel_blob_free(data);
}

/* Derive the denoise residual summaries from the read-back accumulators and
 * attach a measured PEL_SEC_DENOISE section to the output frame. */
static int attach_interop(PelorusDenoiseVulkanContext *s, AVFrame *out,
                          const PelorusDenoiseBuf *acc)
{
    const AVPixFmtDescriptor *d = av_pix_fmt_desc_get(s->vkctx.output_format);
    PelorusSideData meta;
    PelorusDenoiseSection den;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    AVBufferRef *buf;
    double abs_y = 0.0, abs_u = 0.0, abs_v = 0.0, sq_y = 0.0;
    uint64_t cnt_y = 0, cnt_c = 0;
    float res_y = 0.0f, res_u = 0.0f, res_v = 0.0f;
    float sigma_est = 0.0f, psnr = 0.0f, msq = 0.0f;
    int i;

    for (i = 0; i < PEL_SLICES; i++) {
        abs_y += acc->abs_sum_y[i];
        abs_u += acc->abs_sum_u[i];
        abs_v += acc->abs_sum_v[i];
        sq_y += acc->sq_sum_y[i];
        cnt_y += acc->cnt_y[i];
        cnt_c += acc->cnt_c[i];
    }

    if (cnt_y > 0) {
        res_y = (float)(abs_y / 1e6 / (double)cnt_y);
        msq = (float)(sq_y / 1e6 / (double)cnt_y);
        sigma_est = sqrtf(msq);
        /* denoised-vs-input PSNR on a [0,1] domain: peak^2 / mean-square = 1/msq */
        psnr = msq > 0.0f ? (float)(10.0 * log10(1.0 / (double)msq)) : 0.0f;
    }
    if (cnt_c > 0) {
        res_u = (float)(abs_u / 1e6 / (double)cnt_c);
        res_v = (float)(abs_v / 1e6 / (double)cnt_c);
    }

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = (uint64_t)out->pts;
    meta.bit_depth = d ? (uint8_t)d->comp[0].depth : 0;
    meta.plane_layout = (d && d->log2_chroma_w == 0 && d->log2_chroma_h == 0)
                            ? PEL_LAYOUT_444
                            : ((d && d->log2_chroma_h == 0) ? PEL_LAYOUT_422
                                                            : PEL_LAYOUT_420);
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'D');

    memset(&den, 0, sizeof(den));
    den.residual_energy_y = res_y;
    den.residual_energy_u = res_u;
    den.residual_energy_v = res_v;
    den.applied_strength = s->opts.strength[0];
    den.noise_sigma_estimate = sigma_est;
    den.psnr_vs_input = psnr;
    den.denoiser_id = PEL_DENOISER_BILATERAL_TEMPORAL;
    /* den._pad already zeroed by memset */

    sec.id = PEL_SEC_DENOISE;
    sec.data = &den;
    sec.size = (uint32_t)sizeof(den);

    if (pel_blob_pack(&meta, &sec, 1, &blob, &len) != PEL_OK || !blob)
        return AVERROR(ENOMEM);

    buf = av_buffer_create(blob, len, pel_sd_free, NULL, 0);
    if (!buf) {
        pel_blob_free(blob);
        return AVERROR(ENOMEM);
    }
    if (!av_frame_new_side_data_from_buf(out, AV_FRAME_DATA_SEI_UNREGISTERED,
                                         buf)) {
        av_buffer_unref(&buf);
        return AVERROR(ENOMEM);
    }
    return 0;
}

/* Bind the current frame, the actual_prev valid previous frames, the (optional)
 * residual SSBO, the output and the (optional) forward-lookahead next frame via
 * the hand-rolled explicit exec path, dispatch, and (when meta) submit+wait
 * reading the residual back. Inputs first, output then next0 LAST. Unused prev
 * slots are bound to the current frame as harmless filler so every array
 * descriptor is populated; the shader only reads 1..actual_prev. `next` is NULL
 * when there is no forward frame (lookahead==0 or EOF flush) — then next0 is
 * bound to cur as filler and actual_next is 0 (the forward tap never fires). */
static int denoise_dispatch(PelorusDenoiseVulkanContext *s, AVFrame *out,
                            AVFrame *cur, AVFrame *const prev[], int actual_prev,
                            AVFrame *next,
                            const PelorusDenoiseBuf **acc_out, AVBufferRef **buf_out)
{
    int err = 0;
    int i;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanFunctions *vk = &vkctx->vkfn;
    FFVkExecContext *exec = NULL;
    AVFrame *bound_prev[PEL_DENOISE_MAX_PREV];
    AVFrame *bound_next;
    VkImageView cur_views[AV_NUM_DATA_POINTERS];
    VkImageView prev_views[PEL_DENOISE_MAX_PREV][AV_NUM_DATA_POINTERS];
    VkImageView next_views[AV_NUM_DATA_POINTERS];
    VkImageView out_views[AV_NUM_DATA_POINTERS];
    VkImageMemoryBarrier2 img_bar[64];
    int nb_img_bar = 0;
    AVBufferRef *buf = NULL;
    FFVkBuffer *buf_vk = NULL;
    AVBufferRef *mvbuf = NULL, *confbuf = NULL;
    FFVkBuffer *mvbuf_vk = NULL, *confbuf_vk = NULL;
    int want_meta = s->meta;

    *acc_out = NULL;
    *buf_out = NULL;

    /* Every prev array descriptor must be populated; fill the slots past
     * actual_prev with the current frame (never read by the shader). */
    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++)
        bound_prev[i] = (i < actual_prev) ? prev[i] : cur;

    /* Forward-lookahead next0: bind the next frame when present, else cur as
     * harmless filler (never read; actual_next == 0 gates the forward tap). */
    bound_next = next ? next : cur;

    s->opts.actual_prev = actual_prev;
    s->opts.actual_next = (next != NULL) ? 1 : 0;
    s->opts.want_meta = want_meta;

    /* The shader statically references the binding-5 stat SSBO regardless of
     * want_meta (the meta accumulators are gated by a runtime push-const branch,
     * not removed from the SPIR-V), so a valid buffer MUST be bound at 5 on every
     * dispatch — Vulkan forbids a used-but-unbound descriptor. Allocate it
     * unconditionally; the zero-fill + host readback below stay gated on
     * want_meta (when meta=0 the SSBO is never written, so its contents are
     * don't-care — it only has to be a live, bound descriptor). */
    RET(ff_vk_get_pooled_buffer(vkctx, &s->stat_buf_pool, &buf,
                                VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                                    VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
                                NULL, sizeof(PelorusDenoiseBuf),
                                VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT |
                                    VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                    VK_MEMORY_PROPERTY_HOST_COHERENT_BIT));
    buf_vk = (FFVkBuffer *)buf->data;

    /* --- motion-compensated taps: parse the MV + confidence grids from cur's
     * Pelorus side data and stage them into host-visible SSBOs. Something must
     * be bound at 6/7 every dispatch (Vulkan forbids an unbound used
     * descriptor); grid_cols == 0 makes the shader take the same-coord path. */
    s->opts.grid_cols = 0;
    {
        AVFrameSideData *sd = s->mc
            ? av_frame_get_side_data(cur, AV_FRAME_DATA_SEI_UNREGISTERED)
            : NULL;
        const void *mp = NULL, *cp = NULL;
        size_t msz = 0, csz = 0;
        const uint8_t *mv_field = NULL, *conf_field = NULL;
        int gc = 0, gr = 0;
        uint64_t cells = 0, ncells;
        uint64_t j;

        if (sd &&
            pel_blob_find_section(sd->data, sd->size, PEL_SEC_MOTION,
                                  sizeof(PelorusMotionSection), &mp, &msz) == PEL_OK &&
            pel_blob_find_section(sd->data, sd->size, PEL_SEC_MOTION_CONF,
                                  sizeof(PelorusMotionConfSection), &cp, &csz) == PEL_OK) {
            const PelorusMotionSection *mo = mp;
            const PelorusMotionConfSection *mcs = cp;
            const PelorusSideData *bh =
                (const PelorusSideData *)(sd->data + PELORUS_SIDEDATA_UUID_LEN);
            gc = bh->grid_cols;
            gr = bh->grid_rows;
            /* grid dims are untrusted uint16; the product can exceed INT_MAX, so
             * compute in 64-bit and bound it before any sizing/loop use. */
            cells = (uint64_t)gc * (uint64_t)gr;
            /* Validate against the untrusted side-data length before deref. */
            if (gc > 0 && gr > 0 && cells <= PEL_DENOISE_MAX_CELLS &&
                mo->mv_field_size == cells * 4 &&
                mcs->conf_field_size == cells &&
                (size_t)PELORUS_SIDEDATA_UUID_LEN + mo->mv_field_offset +
                        mo->mv_field_size <= sd->size &&
                (size_t)PELORUS_SIDEDATA_UUID_LEN + mcs->conf_field_offset +
                        mcs->conf_field_size <= sd->size) {
                mv_field = sd->data + PELORUS_SIDEDATA_UUID_LEN + mo->mv_field_offset;
                conf_field =
                    sd->data + PELORUS_SIDEDATA_UUID_LEN + mcs->conf_field_offset;
            }
        }

        ncells = mv_field ? cells : 1;
        RET(ff_vk_get_pooled_buffer(vkctx, &s->mv_buf_pool, &mvbuf,
                                    VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
                                    (size_t)ncells * sizeof(uint32_t),
                                    VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                        VK_MEMORY_PROPERTY_HOST_COHERENT_BIT));
        RET(ff_vk_get_pooled_buffer(vkctx, &s->conf_buf_pool, &confbuf,
                                    VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
                                    (size_t)ncells * sizeof(uint32_t),
                                    VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                        VK_MEMORY_PROPERTY_HOST_COHERENT_BIT));
        mvbuf_vk = (FFVkBuffer *)mvbuf->data;
        confbuf_vk = (FFVkBuffer *)confbuf->data;

        if (mv_field) {
            uint32_t *md = (uint32_t *)mvbuf_vk->mapped_mem;
            uint32_t *cd = (uint32_t *)confbuf_vk->mapped_mem;
            for (j = 0; j < cells; j++) {
                int16_t dx, dy;
                memcpy(&dx, mv_field + (size_t)j * 4 + 0, sizeof(int16_t));
                memcpy(&dy, mv_field + (size_t)j * 4 + 2, sizeof(int16_t));
                md[j] = (uint32_t)(uint16_t)dx | ((uint32_t)(uint16_t)dy << 16);
                cd[j] = conf_field[j];
            }
            s->opts.grid_cols = gc;
            s->opts.grid_rows = gr;
            s->opts.cell_w = (out->width + gc - 1) / gc;
            s->opts.cell_h = (out->height + gr - 1) / gr;
        } else {
            ((uint32_t *)mvbuf_vk->mapped_mem)[0] = 0;
            ((uint32_t *)confbuf_vk->mapped_mem)[0] = 0;
        }
    }

    exec = ff_vk_exec_get(vkctx, &s->e);
    ff_vk_exec_start(vkctx, exec);

    /* Dependencies: output, current frame, every bound previous frame. */
    RET(ff_vk_exec_add_dep_frame(vkctx, exec, out,
                                 VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                 VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT));
    RET(ff_vk_create_imageviews(vkctx, exec, out_views, out, FF_VK_REP_FLOAT));
    RET(ff_vk_exec_add_dep_frame(vkctx, exec, cur,
                                 VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                 VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT));
    RET(ff_vk_create_imageviews(vkctx, exec, cur_views, cur, FF_VK_REP_FLOAT));
    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++) {
        /* The filler current-frame slots are already a dependency; only add the
         * real previous frames once each (clones share the underlying frame). */
        if (i < actual_prev)
            RET(ff_vk_exec_add_dep_frame(vkctx, exec, bound_prev[i],
                                         VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                         VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT));
        RET(ff_vk_create_imageviews(vkctx, exec, prev_views[i], bound_prev[i],
                                    FF_VK_REP_FLOAT));
    }
    /* Forward-lookahead next frame: add as a dep only when it is a real frame
     * (the cur filler is already a dependency); always build its image views. */
    if (next)
        RET(ff_vk_exec_add_dep_frame(vkctx, exec, bound_next,
                                     VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                     VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT));
    RET(ff_vk_create_imageviews(vkctx, exec, next_views, bound_next,
                                FF_VK_REP_FLOAT));

    /* Bind: cur=0, prev0..prev3 = 1..4, stat_buffer=5, mv_grid=6, conf_grid=7,
     * output=8, next0=9 (forward-lookahead, LAST). The MV/conf buffers are handed
     * to the exec as deps so they outlive an async (meta=0) submit. */
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, cur, cur_views, 0, 0,
                                  VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++)
        ff_vk_shader_update_img_array(vkctx, exec, &s->shd, bound_prev[i],
                                      prev_views[i], 0, 1 + i,
                                      VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    /* Bind the stat SSBO at 5 on EVERY dispatch (the shader statically uses it);
     * the zero-fill + readback stay gated on want_meta below. */
    RET(ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 5, 0,
                                        buf_vk, 0, buf_vk->size,
                                        VK_FORMAT_UNDEFINED));
    RET(ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 6, 0,
                                        mvbuf_vk, 0, mvbuf_vk->size,
                                        VK_FORMAT_UNDEFINED));
    RET(ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 7, 0,
                                        confbuf_vk, 0, confbuf_vk->size,
                                        VK_FORMAT_UNDEFINED));
    RET(ff_vk_exec_add_dep_buf(vkctx, exec, &mvbuf, 1, 0));
    mvbuf = NULL;
    RET(ff_vk_exec_add_dep_buf(vkctx, exec, &confbuf, 1, 0));
    confbuf = NULL;
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, out, out_views, 0, 8,
                                  VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, bound_next, next_views, 0,
                                  9, VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);

    /* Image barriers: output writable, all inputs readable. */
    ff_vk_frame_barrier(vkctx, exec, out, img_bar, &nb_img_bar,
                        VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                        VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                        VK_ACCESS_SHADER_WRITE_BIT, VK_IMAGE_LAYOUT_GENERAL,
                        VK_QUEUE_FAMILY_IGNORED);
    ff_vk_frame_barrier(vkctx, exec, cur, img_bar, &nb_img_bar,
                        VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                        VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                        VK_ACCESS_SHADER_READ_BIT, VK_IMAGE_LAYOUT_GENERAL,
                        VK_QUEUE_FAMILY_IGNORED);
    for (i = 0; i < actual_prev; i++)
        ff_vk_frame_barrier(vkctx, exec, bound_prev[i], img_bar, &nb_img_bar,
                            VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                            VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                            VK_ACCESS_SHADER_READ_BIT, VK_IMAGE_LAYOUT_GENERAL,
                            VK_QUEUE_FAMILY_IGNORED);
    /* Only a real next frame needs its own read barrier; the cur filler already
     * has one above (a duplicate barrier on the same image would be redundant). */
    if (next)
        ff_vk_frame_barrier(vkctx, exec, bound_next, img_bar, &nb_img_bar,
                            VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                            VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                            VK_ACCESS_SHADER_READ_BIT, VK_IMAGE_LAYOUT_GENERAL,
                            VK_QUEUE_FAMILY_IGNORED);

    if (want_meta) {
        /* Zero the accumulators, then sync TRANSFER->COMPUTE before the dispatch
         * reads/writes the SSBO; also flush the image barriers in the same call. */
        vk->CmdPipelineBarrier2(exec->buf, &(VkDependencyInfo) {
            .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
            .pBufferMemoryBarriers = &(VkBufferMemoryBarrier2) {
                .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                .srcStageMask = VK_PIPELINE_STAGE_2_NONE,
                .dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .buffer = buf_vk->buf,
                .size = buf_vk->size,
                .offset = 0,
            },
            .bufferMemoryBarrierCount = 1,
        });
        vk->CmdFillBuffer(exec->buf, buf_vk->buf, 0, buf_vk->size, 0x0);
        vk->CmdPipelineBarrier2(exec->buf, &(VkDependencyInfo) {
            .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
            .pImageMemoryBarriers = img_bar,
            .imageMemoryBarrierCount = nb_img_bar,
            .pBufferMemoryBarriers = &(VkBufferMemoryBarrier2) {
                .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                .srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                .dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                .dstAccessMask = VK_ACCESS_2_SHADER_STORAGE_READ_BIT |
                                 VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .buffer = buf_vk->buf,
                .size = buf_vk->size,
                .offset = 0,
            },
            .bufferMemoryBarrierCount = 1,
        });
    } else {
        vk->CmdPipelineBarrier2(exec->buf, &(VkDependencyInfo) {
            .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
            .pImageMemoryBarriers = img_bar,
            .imageMemoryBarrierCount = nb_img_bar,
        });
    }

    ff_vk_exec_bind_shader(vkctx, exec, &s->shd);
    ff_vk_shader_update_push_const(vkctx, exec, &s->shd,
                                   VK_SHADER_STAGE_COMPUTE_BIT, 0,
                                   sizeof(s->opts), &s->opts);

    vk->CmdDispatch(exec->buf,
                    FFALIGN(out->width, s->shd.lg_size[0]) / s->shd.lg_size[0],
                    FFALIGN(out->height, s->shd.lg_size[1]) / s->shd.lg_size[1],
                    s->shd.lg_size[2]);

    if (want_meta) {
        vk->CmdPipelineBarrier2(exec->buf, &(VkDependencyInfo) {
            .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
            .pBufferMemoryBarriers = &(VkBufferMemoryBarrier2) {
                .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                .srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                .dstStageMask = VK_PIPELINE_STAGE_2_HOST_BIT,
                .srcAccessMask = VK_ACCESS_2_SHADER_STORAGE_READ_BIT |
                                 VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .buffer = buf_vk->buf,
                .size = buf_vk->size,
                .offset = 0,
            },
            .bufferMemoryBarrierCount = 1,
        });

        RET(ff_vk_exec_submit(vkctx, exec));
        ff_vk_exec_wait(vkctx, exec);

        *acc_out = (const PelorusDenoiseBuf *)buf_vk->mapped_mem;
        *buf_out = buf;
        return 0;
    }

    /* meta=0: no readback, no sync point — just submit and return. The stat
     * buffer bound at 5 is never read back, but it must outlive the async submit
     * (same treatment as the mv/conf buffers above). */
    RET(ff_vk_exec_add_dep_buf(vkctx, exec, &buf, 1, 0));
    buf = NULL;
    RET(ff_vk_exec_submit(vkctx, exec));
    return 0;

fail:
    if (exec)
        ff_vk_exec_discard_deps(vkctx, exec);
    av_buffer_unref(&buf);
    av_buffer_unref(&mvbuf);
    av_buffer_unref(&confbuf);
    return err;
}

/* Denoise one frame and emit it. Takes ownership of `cur` (consumed: cloned into
 * the causal ring, then freed). `next` is the optional forward-lookahead tap and
 * is only borrowed (the caller keeps owning it — typically as the new held
 * frame); pass NULL for the causal (lookahead==0) and EOF-flush paths. The output
 * is pushed downstream via ff_filter_frame. The causal prev-ring push always
 * happens for the PROCESSED frame so the temporal history stays intact. */
static int denoise_process_one(AVFilterContext *ctx, AVFrame *cur, AVFrame *next)
{
    int err;
    int i;
    AVFrame *out = NULL;
    AVFrame *clone = NULL;
    PelorusDenoiseVulkanContext *s = ctx->priv;
    AVFilterLink *outlink = ctx->outputs[0];
    AVFrame *const *prev = (AVFrame *const *)s->ring;
    const PelorusDenoiseBuf *acc = NULL;
    AVBufferRef *buf = NULL;
    int actual_prev;

    out = ff_get_video_buffer(outlink, outlink->w, outlink->h);
    if (!out) {
        err = AVERROR(ENOMEM);
        goto fail;
    }

    if (!s->initialized)
        RET(init_filter(ctx));

    s->opts.frame_idx = (uint32_t)(s->frame_idx++);

    /* Causal window: only the frames already seen, capped at the configured
     * depth. ring[0] is the most recent previous frame. */
    actual_prev = FFMIN(s->n_prev, s->ring_count);

    RET(denoise_dispatch(s, out, cur, prev, actual_prev, next, &acc, &buf));

    err = av_frame_copy_props(out, cur);
    if (err < 0)
        goto fail;

    if (s->meta && acc) {
        err = attach_interop(s, out, acc);
        if (err < 0)
            goto fail;
    }
    av_buffer_unref(&buf);

    /* Push the processed frame into the ring (cheap clone — refcount bump on the
     * hwframe images, no pixel copy), evicting the oldest. */
    clone = av_frame_clone(cur);
    if (!clone) {
        err = AVERROR(ENOMEM);
        goto fail;
    }
    if (s->ring_count == s->n_prev && s->n_prev > 0)
        av_frame_free(&s->ring[s->n_prev - 1]);
    for (i = FFMIN(s->ring_count, s->n_prev - 1); i > 0; i--)
        s->ring[i] = s->ring[i - 1];
    if (s->n_prev > 0) {
        s->ring[0] = clone;
        clone = NULL;
        if (s->ring_count < s->n_prev)
            s->ring_count++;
    } else {
        av_frame_free(&clone); /* depth 0: temporal disabled, keep no ring */
    }

    av_frame_free(&cur);
    return ff_filter_frame(outlink, out);

fail:
    av_buffer_unref(&buf);
    av_frame_free(&clone);
    av_frame_free(&cur);
    av_frame_free(&out);
    return err;
}

/* Buffered-filter lifecycle (ADR-0137). lookahead==0 processes each frame
 * immediately (cur=in, next=NULL) — bit-identical to the old causal filter_frame.
 * lookahead==1 delays output by one frame: hold the incoming frame, and when the
 * NEXT one arrives process the held frame with the new frame as its forward tap,
 * then hold the new frame. At input EOF, flush any held frame (next=NULL) before
 * forwarding the status, so the output count equals the input count. */
static int denoise_vulkan_activate(AVFilterContext *ctx)
{
    PelorusDenoiseVulkanContext *s = ctx->priv;
    AVFilterLink *inlink = ctx->inputs[0];
    AVFilterLink *outlink = ctx->outputs[0];
    AVFrame *in = NULL;
    int ret, status;
    int64_t pts;

    FF_FILTER_FORWARD_STATUS_BACK(outlink, inlink);

    ret = ff_inlink_consume_frame(inlink, &in);
    if (ret < 0)
        return ret;
    if (ret > 0) {
        if (!s->lookahead)
            return denoise_process_one(ctx, in, NULL); /* causal: emit now */

        /* lookahead: process the previously held frame with `in` as its forward
         * tap, then hold `in`. The first frame simply becomes the held frame. */
        if (s->held) {
            AVFrame *cur = s->held;
            s->held = in; /* hold the new frame before we may re-enter */
            return denoise_process_one(ctx, cur, in);
        }
        s->held = in;
        ff_filter_set_ready(ctx, 100); /* re-enter to pull the next frame */
        return 0;
    }

    if (ff_inlink_acknowledge_status(inlink, &status, &pts)) {
        /* EOF: flush the last held frame (no forward tap available) so output
         * count == input count, then forward the status. */
        if (s->held) {
            AVFrame *cur = s->held;
            s->held = NULL;
            ret = denoise_process_one(ctx, cur, NULL);
            if (ret < 0)
                return ret;
        }
        ff_outlink_set_status(outlink, status, pts);
        return 0;
    }

    FF_FILTER_FORWARD_WANTED(outlink, inlink);

    return FFERROR_NOT_READY;
}

static void denoise_vulkan_uninit(AVFilterContext *avctx)
{
    int i;
    PelorusDenoiseVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++)
        av_frame_free(&s->ring[i]);
    s->ring_count = 0;
    av_frame_free(&s->held); /* forward-lookahead: free any un-flushed held frame */

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    av_buffer_pool_uninit(&s->stat_buf_pool);
    av_buffer_pool_uninit(&s->mv_buf_pool);
    av_buffer_pool_uninit(&s->conf_buf_pool);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusDenoiseVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_denoise_vulkan_options[] = {
    { "sigma", "luma spatial range sigma (edge sensitivity, normalized)",
      OFFSET(opt_sigma_y), AV_OPT_TYPE_DOUBLE, { .dbl = 0.03 }, 0.0, 0.5, FLAGS },
    { "sigmac", "chroma spatial range sigma (normalized)",
      OFFSET(opt_sigma_c), AV_OPT_TYPE_DOUBLE, { .dbl = 0.04 }, 0.0, 0.5, FLAGS },
    { "sigmat", "temporal gate bandwidth (normalized)",
      OFFSET(opt_sigma_t), AV_OPT_TYPE_DOUBLE, { .dbl = 0.05 }, 0.0, 0.5, FLAGS },
    { "strength", "luma dry/wet mix (0..1)", OFFSET(opt_strength_y),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.30 }, 0.0, 1.0, FLAGS },
    { "strengthc", "chroma dry/wet mix (0..1)", OFFSET(opt_strength_c),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.20 }, 0.0, 1.0, FLAGS },
    { "blend", "spatial<->temporal blend (0=spatial, 1=temporal)",
      OFFSET(opt_blend), AV_OPT_TYPE_DOUBLE, { .dbl = 0.6 }, 0.0, 1.0, FLAGS },
    { "tdecay", "per-frame temporal trust falloff", OFFSET(opt_tdecay),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.8 }, 0.0, 1.0, FLAGS },
    { "tcut", "per-pixel scene-cut/fast-motion clamp (normalized)",
      OFFSET(opt_tcut), AV_OPT_TYPE_DOUBLE, { .dbl = 0.10 }, 0.0, 0.5, FLAGS },
    { "patch", "spatial window radius (0 = temporal-only)", OFFSET(patch_radius),
      AV_OPT_TYPE_INT, { .i64 = 1 }, 0, 3, FLAGS },
    { "prev", "causal temporal depth (previous frames held in VRAM)",
      OFFSET(n_prev), AV_OPT_TYPE_INT, { .i64 = 3 }, 0, PEL_DENOISE_MAX_PREV, FLAGS },
    { "protect", "damp strength on textured / edge regions", OFFSET(protect_detail),
      AV_OPT_TYPE_BOOL, { .i64 = 1 }, 0, 1, FLAGS },
    { "planes", "planes to process (bitmask)", OFFSET(planes),
      AV_OPT_TYPE_INT, { .i64 = 0xF }, 0, 0xF, FLAGS },
    { "meta", "attach Pelorus interop side data", OFFSET(meta),
      AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, FLAGS },
    { "mc", "motion-compensated temporal taps (consume an upstream pelorus_mc "
            "PEL_SEC_MOTION field; requires mc before denoise)", OFFSET(mc),
      AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, FLAGS },
    { "tile", "cache the spatial search window in shared memory (large win on "
              "bandwidth-limited GPUs, ~neutral on cache-rich ones; output is "
              "bit-identical)", OFFSET(tile),
      AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, FLAGS },
    { "lookahead", "forward-lookahead temporal depth (0 = causal, the default, "
                   "bit-identical; 1 = bidirectional: delay output 1 frame so the "
                   "temporal walk also samples the NEXT frame, tcut-gated — recovers "
                   "the leading frame of a held animation drawing, ADR-0137; opt-in "
                   "for cadence/animation content)", OFFSET(lookahead),
      AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 1, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(pelorus_denoise_vulkan);

static const AVFilterPad pelorus_denoise_vulkan_inputs[] = {
    {
        /* No .filter_frame: the buffered forward-lookahead lifecycle is driven by
         * the .activate callback below (ADR-0137). */
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_input,
    },
};

static const AVFilterPad pelorus_denoise_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_denoise_vulkan = {
    .p.name = "pelorus_denoise_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus temporal denoise (Vulkan)"),
    .p.priv_class = &pelorus_denoise_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusDenoiseVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &denoise_vulkan_uninit,
    .activate = &denoise_vulkan_activate,
    FILTER_INPUTS(pelorus_denoise_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_denoise_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
