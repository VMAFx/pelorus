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
 * Forward-lookahead (ADR-0137): with lookahead=1 (opt-in; the default is 0) the
 * temporal walk is bidirectional — output is delayed by one frame (buffered
 * .activate lifecycle) so the denoise can also sample the NEXT frame
 * (same-coordinate, tcut-gated like the prev taps), closing the leading-frame
 * gap of a held animation drawing. This adds a 1-frame latency + an EOF flush of
 * the last held frame. lookahead=0 (the default) is the original purely-causal
 * behaviour: process each frame immediately, no latency, no flush —
 * bit-identical to pre-ADR-0137.
 *
 * Interop: when `meta=1` the filter free-rides a residual reduction in the same
 * dispatch (per-workgroup sums of |in-out| and (in-out)^2, added atomically to
 * 64-bit fixed-point slices of a HOST_VISIBLE SSBO) and attaches a Pelorus
 * PEL_SEC_DENOISE blob (AV_FRAME_DATA_SEI_UNREGISTERED, UUID-keyed) so a
 * downstream vmafx vf_libvmaf* can react. See <pelorus/interop.h> and
 * docs/metrics/denoise.md.
 *
 * The algorithm lives in vulkan/pelorus_denoise.comp.glsl, compiled to SPIR-V
 * at build time and linked in — a single source of truth (FFmpeg 9 removed the
 * runtime inline-GLSL builder, retiring the old lockstep duplication).
 */

#include "libavutil/buffer.h"
#include "libavutil/frame.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "pelorus_sidedata.h"
#include "pelorus_sidedata_attach.h"
#include "pelorus_vulkan_input.h"
#include "pelorus_vulkan_sample.h"
#include "pelorus_vulkan_pool.h"
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
#define PEL_DENOISE_MAX_CELLS ((uint64_t)8192u * 8192u)

/* meta=1 residual statistics, in the shader's RES_GS fixed point. 2^23 resolves
 * a one-code 10-bit residual squared as 8 units, while a full 16x16 workgroup
 * partial stays below 2^31 (BUG-016). The shader mirrors these constants;
 * scripts/test-denoise-accumulator-bounds.py checks both sides and the DCI 8K
 * bounds. */
#define PEL_DENOISE_STATS 4
#define PEL_DENOISE_RES_GS 8388608.0

enum pel_denoise_stat {
    PEL_DENOISE_STAT_ABS_Y = 0, /* sum |in-out|, luma   */
    PEL_DENOISE_STAT_ABS_U = 1, /* sum |in-out|, U      */
    PEL_DENOISE_STAT_ABS_V = 2, /* sum |in-out|, V      */
    PEL_DENOISE_STAT_SQ_Y = 3,  /* sum (in-out)^2, luma */
};

/* Host-readback accumulator for the meta=1 residual free-ride. Each workgroup
 * reduces its residuals in shared memory and adds one partial per statistic to
 * slice (workgroup index % PEL_SLICES). sum_lo/sum_hi are the low and high
 * words of a 64-bit sum per [stat * PEL_SLICES + slice]; summed host-side. */
typedef struct PelorusDenoiseBuf {
    uint32_t sum_lo[PEL_DENOISE_STATS * PEL_SLICES];
    uint32_t sum_hi[PEL_DENOISE_STATS * PEL_SLICES];
    uint32_t cnt_y[PEL_SLICES]; /* luma pixel count                        */
    uint32_t cnt_c[PEL_SLICES]; /* chroma pixel count (per chroma plane)   */
} PelorusDenoiseBuf;

typedef struct PelorusDenoiseVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;
    AVBufferPool *stat_buf_pool;

    /* push constants — mirror the std430 block in the .comp.glsl, byte-for-byte */
    struct {
        float sigma_s[4];       /* vec4 : per-plane spatial range sigma    @0  */
        float sigma_t[4];       /* vec4 : per-plane temporal gate sigma    @16 */
        float strength[4];      /* vec4 : per-plane dry/wet mix            @32 */
        float blend;            /*                                         @48 */
        float temporal_decay;   /*                                         @52 */
        float temporal_cut;     /*                                         @56 */
        int32_t patch_radius;   /*                                         @60 */
        int32_t n_prev;         /* configured temporal depth (0..MAX_PREV) @64 */
        int32_t actual_prev;    /* valid previous frames this dispatch     @68 */
        int32_t nb_planes;      /*                                         @72 */
        int32_t planes_mask;    /* plane bitmask to process                @76 */
        int32_t flags;          /* enum pel_denoise_flags                  @80 */
        uint32_t frame_idx;     /*                                         @84 */
        int32_t want_meta;      /* 1 => accumulate residual into the SSBO  @88 */
        int32_t grid_cols;      /* MV/conf grid cols (0 => no MC this frame)@92 */
        int32_t grid_rows;      /*                                         @96 */
        int32_t cell_w;         /* producer block edge (luma px)           @100*/
        int32_t cell_h;         /* producer block edge (luma px)           @104*/
        int32_t chroma_shift_w; /* log2_chroma_w (MV luma->plane scale)  @108*/
        int32_t chroma_shift_h; /*                                       @112*/
        float mv_scale;         /* stored MV -> luma px (0.25 = quarter-pel)@116*/
        int32_t actual_next;    /* forward taps this dispatch (0 or 1)     @120*/
        float sample_scale;     /* storage UNORM -> logical sample domain  @124*/
    } opts;

    /* AVOption-backed scalar mirrors (broadcast to all planes at init) */
    double opt_sigma_y, opt_sigma_c; /* spatial range sigma (sigma_s)         */
    double opt_sigma_t;              /* temporal gate sigma                   */
    double opt_strength_y, opt_strength_c;
    double opt_blend, opt_tdecay, opt_tcut;
    int patch_radius;
    int n_prev;
    int protect_detail;
    int planes;    /* plane bitmask to process (const-folded into shader)        */
    int meta;      /* attach Pelorus interop side data                          */
    int mc;        /* motion-compensated temporal taps (consume pelorus_mc)     */
    int tile;      /* shared-memory tile the spatial window (ADR-0134, opt-in)   */
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
    int mc_grid_warned; /* the unmappable-grid warning was logged once */
    int mc_grid_assumed_warned; /* the assumed-default-bsize warning was logged once */

    /* Causal previous-frame ring (clones — refcount bumps, no pixel copy). */
    AVFrame *ring[PEL_DENOISE_MAX_PREV];
    int ring_count; /* valid entries in ring[] (0..n_prev)                    */
    PelVkPoolOpts pool; /* output pool tiling (ADR-0184) */
} PelorusDenoiseVulkanContext;

static_assert(sizeof(((PelorusDenoiseVulkanContext *)0)->opts) <= 128,
              "denoise push constants exceed Vulkan's guaranteed minimum");

/* The denoise algorithm now lives in vulkan/pelorus_denoise.comp.glsl, compiled
 * to SPIR-V at build time and linked in here. FFmpeg 9 removed the runtime GLSL
 * builder (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also retires the old
 * inline-vs-reference lockstep duplication. What the C generator used to
 * const-fold (plane count, the `planes` bitmask, the ADR-0134 `tile` switch)
 * is now passed as specialization constants instead. */
extern const unsigned char ff_pelorus_denoise_comp_spv_data[];
extern const unsigned int ff_pelorus_denoise_comp_spv_len;

static void denoise_init_opts(PelorusDenoiseVulkanContext *s, int planes, int *semi_planar)
{
    FFVulkanContext *vkctx = &s->vkctx;
    int i;
    const AVPixFmtDescriptor *pd;

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

    pd = av_pix_fmt_desc_get(vkctx->output_format);
    s->opts.chroma_shift_w = pd ? pd->log2_chroma_w : 1;
    s->opts.chroma_shift_h = pd ? pd->log2_chroma_h : 1;
    /* Semi-planar (NV12/P010/NV16/NV24/P016): U and V share plane 1, so
     * that plane's image has TWO components and both carry picture data.
     * The shader must filter both and must never synthesise the stored
     * texel, or the second component (V) is written as a constant. */
    *semi_planar =
        (pd && pd->nb_components >= 3 && pd->comp[1].plane == pd->comp[2].plane) ? 1 : 0;

    /* MV/conf grid dims are set per-frame in the dispatch; 0 => no MC. */
    s->opts.grid_cols = 0;
    s->opts.grid_rows = 0;
    s->opts.cell_w = 0;
    s->opts.cell_h = 0;
    s->opts.mv_scale = 0.25f; /* mc emits quarter-pel (Q2) */
    s->opts.actual_next = 0;  /* set per-dispatch; 0 => forward tap never fires */
    s->opts.sample_scale = pel_vk_sample_scale(vkctx->input_format);
}

static void denoise_add_descriptors(FFVulkanContext *vkctx, FFVulkanShader *shd, int planes)
{
    const char *rep_in = ff_vk_shader_rep_fmt(vkctx->input_format, FF_VK_REP_FLOAT);
    const char *rep_out = ff_vk_shader_rep_fmt(vkctx->output_format, FF_VK_REP_FLOAT);

    /* Descriptor set 0. Inputs FIRST, output then the forward tap LAST (the Nin /
     * bwdif binding-order contract): binding 0 = current frame, 1..MAX_PREV =
     * previous frames, then the residual SSBO, then the MV/conf SSBOs, then the
     * output image, then the forward-lookahead next0 image (ADR-0137). Each image
     * binding is a per-plane array (.elems = planes), float representation,
     * storage image (sampler = VK_NULL_HANDLE, GENERAL layout, imageLoad). Final
     * binding map: cur=0, prev0-3=1-4, stat=5, mv=6, conf=7, output=8, next0=9. */
    FFVulkanDescriptorSetBinding desc_set[] = {
        {.name = "cur_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_in, .mem_quali = "readonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
        {.name = "prev0_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_in, .mem_quali = "readonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
        {.name = "prev1_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_in, .mem_quali = "readonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
        {.name = "prev2_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_in, .mem_quali = "readonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
        {.name = "prev3_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_in, .mem_quali = "readonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
        {.name = "stat_buffer", .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
         .mem_layout = "std430", .stages = VK_SHADER_STAGE_COMPUTE_BIT,
         .buf_content = "uint sum_lo[64]; uint sum_hi[64]; uint cnt_y[16]; uint cnt_c[16];"},
        {/* Per-cell quarter-pel MV grid: (uint16 dx)|(uint16 dy<<16). */
         .name = "mv_grid", .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
         .mem_layout = "std430", .stages = VK_SHADER_STAGE_COMPUTE_BIT,
         .buf_content = "uint mv_packed[];"},
        {/* Per-cell motion confidence (0..255), one uint per cell. */
         .name = "conf_grid", .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
         .mem_layout = "std430", .stages = VK_SHADER_STAGE_COMPUTE_BIT,
         .buf_content = "uint conf_packed[];"},
        {.name = "output_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_out, .mem_quali = "writeonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
        {/* Forward-lookahead tap (ADR-0137): the NEXT frame, mirrors
          * prev0_images exactly. Read same-coordinate, tcut-gated. */
         .name = "next0_images", .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
         .mem_layout = rep_in, .mem_quali = "readonly", .dimensions = 2,
         .elems = planes, .stages = VK_SHADER_STAGE_COMPUTE_BIT},
    };
    ff_vk_shader_add_descriptor_set(vkctx, shd, desc_set, 10, 0);
}

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    PelorusDenoiseVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd;
    const int planes = av_pix_fmt_count_planes(vkctx->output_format);
    const uint32_t sample_code_max = pel_vk_sample_code_max(vkctx->output_format);
    int semi_planar = 0;

    denoise_init_opts(s, planes, &semi_planar);

    RET(pel_vk_init_compute_exec(ctx, vkctx, &s->qf, &s->e));
    /* The plane count, the `planes` bitmask and the ADR-0134 `tile` switch were
     * const-folded into the generated GLSL before FFmpeg 9 (the per-plane main()
     * was unrolled in C). With precompiled SPIR-V they become specialization
     * constants, resolved at pipeline creation — the same const-folding, one step
     * later. `semi_planar` joins them so the shader knows plane 1 carries two
     * components. The push-constant block itself now lives in the .comp.glsl. */
    SPEC_LIST_CREATE(sl, 5 + PEL_VK_SPEC_FRAME_SIZE_COUNT,
                     (5 + PEL_VK_SPEC_FRAME_SIZE_COUNT) * sizeof(uint32_t))
    SPEC_LIST_ADD(sl, 0, 32, (uint32_t)planes);
    SPEC_LIST_ADD(sl, 1, 32, (uint32_t)s->planes);
    SPEC_LIST_ADD(sl, 2, 32, (uint32_t)(s->tile ? 1 : 0));
    SPEC_LIST_ADD(sl, 3, 32, (uint32_t)semi_planar);
    SPEC_LIST_ADD(sl, 4, 32, sample_code_max);
    pel_vk_spec_add_frame_size(sl, vkctx);

    ff_vk_shader_load(shd, VK_SHADER_STAGE_COMPUTE_BIT, sl, (uint32_t[]){16, 16, 1}, 0);

    ff_vk_shader_add_push_const(shd, 0, sizeof(s->opts), VK_SHADER_STAGE_COMPUTE_BIT);

    denoise_add_descriptors(vkctx, shd, planes);

    RET(ff_vk_shader_link(vkctx, shd, ff_pelorus_denoise_comp_spv_data,
                          ff_pelorus_denoise_comp_spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    return err;
}

/* Sum one statistic's 64-bit slices and return it in sample units. A slice is
 * below 2^45 at DCI 8K, so the uint64 total cannot wrap. */
static double pel_denoise_stat(const PelorusDenoiseBuf *acc, enum pel_denoise_stat stat)
{
    uint64_t total = 0;
    int i;

    for (i = 0; i < PEL_SLICES; i++) {
        const int k = (int)stat * PEL_SLICES + i;
        total += ((uint64_t)acc->sum_hi[k] << 32) | acc->sum_lo[k];
    }
    return (double)total / PEL_DENOISE_RES_GS;
}

static void denoise_fill_section(const PelorusDenoiseVulkanContext *s, const PelorusDenoiseBuf *acc,
                                 PelorusDenoiseSection *den)
{
    const double abs_y = pel_denoise_stat(acc, PEL_DENOISE_STAT_ABS_Y);
    const double abs_u = pel_denoise_stat(acc, PEL_DENOISE_STAT_ABS_U);
    const double abs_v = pel_denoise_stat(acc, PEL_DENOISE_STAT_ABS_V);
    const double sq_y = pel_denoise_stat(acc, PEL_DENOISE_STAT_SQ_Y);
    uint64_t cnt_y = 0, cnt_c = 0;
    float res_y = 0.0f, res_u = 0.0f, res_v = 0.0f;
    float sigma_est = 0.0f, psnr = 0.0f, msq = 0.0f;
    int i;

    for (i = 0; i < PEL_SLICES; i++) {
        cnt_y += acc->cnt_y[i];
        cnt_c += acc->cnt_c[i];
    }

    if (cnt_y > 0) {
        res_y = (float)(abs_y / (double)cnt_y);
        msq = (float)(sq_y / (double)cnt_y);
        sigma_est = sqrtf(msq);
        /* denoised-vs-input PSNR on a [0,1] domain: peak^2 / mean-square = 1/msq */
        psnr = msq > 0.0f ? (float)(10.0 * log10(1.0 / (double)msq)) : 0.0f;
    }
    if (cnt_c > 0) {
        res_u = (float)(abs_u / (double)cnt_c);
        res_v = (float)(abs_v / (double)cnt_c);
    }

    memset(den, 0, sizeof(*den));
    den->residual_energy_y = res_y;
    den->residual_energy_u = res_u;
    den->residual_energy_v = res_v;
    den->applied_strength = s->opts.strength[0];
    den->noise_sigma_estimate = sigma_est;
    den->psnr_vs_input = psnr;
    den->denoiser_id = PEL_DENOISER_BILATERAL_TEMPORAL;
    /* den._pad already zeroed by memset */
}

/* Derive the denoise residual summaries from the read-back accumulators and
 * attach a measured PEL_SEC_DENOISE section to the output frame. */
static int attach_interop(PelorusDenoiseVulkanContext *s, AVFrame *out,
                          const PelorusDenoiseBuf *acc)
{
    PelorusSideData meta;
    PelorusDenoiseSection den;
    PelorusPackSection sec;

    denoise_fill_section(s, acc, &den);

    pel_sidedata_init_meta(&meta, out, s->vkctx.output_format, PEL_FOURCC('P', 'L', 'R', 'D'));

    sec.id = PEL_SEC_DENOISE;
    sec.data = &den;
    sec.size = (uint32_t)sizeof(den);

    return pel_sidedata_pack_and_attach(out, &meta, &sec, 1);
}

typedef struct DenoiseViews {
    VkImageView cur[AV_NUM_DATA_POINTERS];
    VkImageView prev[PEL_DENOISE_MAX_PREV][AV_NUM_DATA_POINTERS];
    VkImageView next[AV_NUM_DATA_POINTERS];
    VkImageView out[AV_NUM_DATA_POINTERS];
} DenoiseViews;

static int denoise_check_motion_fit(PelorusDenoiseVulkanContext *s, const PelorusMotionSection *mo,
                                   size_t msz, int width, int height, int gc, int gr, int *pitch)
{
    /* The producer's block edge (named since ABI 1.4), not ceil(W/gc). */
    int fit = pelorus_mc_block_pitch(mo, msz, width, height, gc, gr, pitch);
    if (fit == 2 && !s->mc_grid_assumed_warned) {
        s->mc_grid_assumed_warned = 1;
        av_log(&s->vkctx, AV_LOG_WARNING,
               "motion grid %dx%d fits several block sizes for a %dx%d frame; "
               "assuming the mc default bsize=%d\n",
               gc, gr, width, height, PEL_SD_MC_BSIZE_DEFAULT);
    }
    if (!fit && !s->mc_grid_warned) {
        s->mc_grid_warned = 1;
        av_log(&s->vkctx, AV_LOG_WARNING,
               "motion grid %dx%d does not map to a unique block size for a %dx%d "
               "frame; motion compensation disabled for such frames\n",
               gc, gr, width, height);
    }
    return fit;
}

static void denoise_parse_motion_grid(PelorusDenoiseVulkanContext *s, const AVFrame *cur,
                                      int width, int height, const uint8_t **mv_field_out,
                                      const uint8_t **conf_field_out, int *gc_out, int *gr_out,
                                      int *pitch_out, uint64_t *cells_out)
{
    const AVFrameSideData *sd = NULL;
    const void *mp = NULL, *cp = NULL;
    size_t msz = 0, csz = 0;
    const uint8_t *mv_field = NULL, *conf_field = NULL;
    int gc = 0, gr = 0, pitch = 0;
    uint64_t cells = 0;

    /* Newest Pelorus blob carrying PEL_SEC_MOTION (every producer appends its
     * own SEI_UNREGISTERED entry, so av_frame_get_side_data's first entry can
     * be another producer's). The confidence section and the grid dimensions
     * must come from that same blob: the grid belongs to its header. */
    if (s->mc)
        sd = pelorus_sd_find_section(cur, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &mp, &msz);
    if (sd &&
        pel_blob_find_section(sd->data, sd->size, PEL_SEC_MOTION_CONF,
                              sizeof(PelorusMotionConfSection), &cp, &csz) == PEL_OK &&
        cp && PEL_SD_FIELD_OK(msz, PelorusMotionSection, mv_field_size) &&
        PEL_SD_FIELD_OK(csz, PelorusMotionConfSection, conf_field_size)) {
        const PelorusMotionSection *mo = mp;
        const PelorusMotionConfSection *mcs = cp;
        const PelorusSideData *bh = (const PelorusSideData *)(sd->data + PELORUS_SIDEDATA_UUID_LEN);
        gc = bh->grid_cols;
        gr = bh->grid_rows;
        /* grid dims are untrusted uint16; the product can exceed INT_MAX, so
         * compute in 64-bit and bound it before any sizing/loop use. */
        cells = (uint64_t)gc * (uint64_t)gr;
        /* Validate against the untrusted side-data length before deref. */
        if (gc > 0 && gr > 0 && cells <= PEL_DENOISE_MAX_CELLS &&
            mo->mv_field_size == cells * 4 && mcs->conf_field_size == cells &&
            (size_t)PELORUS_SIDEDATA_UUID_LEN + mo->mv_field_offset + mo->mv_field_size <= sd->size &&
            (size_t)PELORUS_SIDEDATA_UUID_LEN + mcs->conf_field_offset + mcs->conf_field_size <= sd->size) {
            if (denoise_check_motion_fit(s, mo, msz, width, height, gc, gr, &pitch)) {
                mv_field = sd->data + PELORUS_SIDEDATA_UUID_LEN + mo->mv_field_offset;
                conf_field = sd->data + PELORUS_SIDEDATA_UUID_LEN + mcs->conf_field_offset;
            }
        }
    }

    *mv_field_out = mv_field;
    *conf_field_out = conf_field;
    *gc_out = gc;
    *gr_out = gr;
    *pitch_out = pitch;
    *cells_out = cells;
}

static int denoise_alloc_motion_buffers(PelorusDenoiseVulkanContext *s, const uint8_t *mv_field,
                                       const uint8_t *conf_field, int gc, int gr, int pitch,
                                       uint64_t cells, AVBufferRef **mvbuf_out,
                                       AVBufferRef **confbuf_out)
{
    int err;
    uint64_t ncells = mv_field ? cells : 1;
    uint64_t j;
    FFVkBuffer *mvbuf_vk, *confbuf_vk;

    err = ff_vk_get_pooled_buffer(
        &s->vkctx, &s->mv_buf_pool, mvbuf_out, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
        (size_t)ncells * sizeof(uint32_t),
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
            VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (err < 0)
        return err;

    err = ff_vk_get_pooled_buffer(
        &s->vkctx, &s->conf_buf_pool, confbuf_out, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
        (size_t)ncells * sizeof(uint32_t),
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
            VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (err < 0)
        return err;

    mvbuf_vk = (FFVkBuffer *)(*mvbuf_out)->data;
    confbuf_vk = (FFVkBuffer *)(*confbuf_out)->data;

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
        s->opts.cell_w = pitch;
        s->opts.cell_h = pitch;
    } else {
        ((uint32_t *)mvbuf_vk->mapped_mem)[0] = 0;
        ((uint32_t *)confbuf_vk->mapped_mem)[0] = 0;
    }

    return 0;
}

static int denoise_setup_buffers(PelorusDenoiseVulkanContext *s, const AVFrame *cur,
                                int width, int height, AVBufferRef **buf,
                                AVBufferRef **mvbuf, AVBufferRef **confbuf)
{
    int err;
    const uint8_t *mv_field = NULL, *conf_field = NULL;
    int gc = 0, gr = 0, pitch = 0;
    uint64_t cells = 0;

    /* The shader statically references the binding-5 stat SSBO regardless of
     * want_meta (the meta accumulators are gated by a runtime push-const branch,
     * not removed from the SPIR-V), so a valid buffer MUST be bound at 5 on every
     * dispatch — Vulkan forbids a used-but-unbound descriptor. Allocate it
     * unconditionally; the zero-fill + host readback below stay gated on
     * want_meta (when meta=0 the SSBO is never written, so its contents are
     * don't-care — it only has to be a live, bound descriptor). */
    err = ff_vk_get_pooled_buffer(
        &s->vkctx, &s->stat_buf_pool, buf,
        VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
        sizeof(PelorusDenoiseBuf),
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
            VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (err < 0)
        return err;

    /* --- motion-compensated taps: parse the MV + confidence grids from cur's
     * Pelorus side data and stage them into host-visible SSBOs. Something must
     * be bound at 6/7 every dispatch (Vulkan forbids an unbound used
     * descriptor); grid_cols == 0 makes the shader take the same-coord path. */
    s->opts.grid_cols = 0;
    denoise_parse_motion_grid(s, cur, width, height, &mv_field, &conf_field, &gc, &gr,
                             &pitch, &cells);
    return denoise_alloc_motion_buffers(s, mv_field, conf_field, gc, gr, pitch, cells,
                                        mvbuf, confbuf);
}

static int denoise_add_dep_frames(FFVulkanContext *vkctx, FFVkExecContext *exec, AVFrame *out,
                                  AVFrame *cur, AVFrame *const bound_prev[], int actual_prev,
                                  AVFrame *next, AVFrame *bound_next, DenoiseViews *views)
{
    int err, i;

    /* Dependencies: output, current frame, every bound previous frame. */
    err = ff_vk_exec_add_dep_frame(vkctx, exec, out, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                  VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
    if (err < 0)
        return err;
    err = ff_vk_create_imageviews(vkctx, exec, views->out, out, FF_VK_REP_FLOAT);
    if (err < 0)
        return err;

    err = ff_vk_exec_add_dep_frame(vkctx, exec, cur, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                  VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
    if (err < 0)
        return err;
    err = ff_vk_create_imageviews(vkctx, exec, views->cur, cur, FF_VK_REP_FLOAT);
    if (err < 0)
        return err;

    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++) {
        /* The filler current-frame slots are already a dependency; only add the
         * real previous frames once each (clones share the underlying frame). */
        if (i < actual_prev) {
            err = ff_vk_exec_add_dep_frame(vkctx, exec, bound_prev[i],
                                          VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                          VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
            if (err < 0)
                return err;
        }
        err = ff_vk_create_imageviews(vkctx, exec, views->prev[i], bound_prev[i], FF_VK_REP_FLOAT);
        if (err < 0)
            return err;
    }

    /* Forward-lookahead next frame: add as a dep only when it is a real frame
     * (the cur filler is already a dependency); always build its image views. */
    if (next) {
        err = ff_vk_exec_add_dep_frame(vkctx, exec, bound_next,
                                      VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                                      VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
        if (err < 0)
            return err;
    }
    err = ff_vk_create_imageviews(vkctx, exec, views->next, bound_next, FF_VK_REP_FLOAT);
    if (err < 0)
        return err;

    return 0;
}

static int denoise_bind_descriptors(PelorusDenoiseVulkanContext *s, FFVkExecContext *exec,
                                    AVFrame *out, AVFrame *cur, AVFrame *const bound_prev[],
                                    AVFrame *bound_next, const DenoiseViews *views,
                                    FFVkBuffer *buf_vk, AVBufferRef **mvbuf, AVBufferRef **confbuf)
{
    int err, i;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVkBuffer *mvbuf_vk = (FFVkBuffer *)(*mvbuf)->data;
    FFVkBuffer *confbuf_vk = (FFVkBuffer *)(*confbuf)->data;

    /* Bind: cur=0, prev0..prev3 = 1..4, stat_buffer=5, mv_grid=6, conf_grid=7,
     * output=8, next0=9 (forward-lookahead, LAST). The MV/conf buffers are handed
     * to the exec as deps so they outlive an async (meta=0) submit. */
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, cur, views->cur, 0, 0,
                                  VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++)
        ff_vk_shader_update_img_array(vkctx, exec, &s->shd, bound_prev[i], views->prev[i], 0, 1 + i,
                                      VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    /* Bind the stat SSBO at 5 on EVERY dispatch (the shader statically uses it);
     * the zero-fill + readback stay gated on want_meta below. */
    err = ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 5, 0, buf_vk, 0, buf_vk->size,
                                         VK_FORMAT_UNDEFINED);
    if (err < 0)
        return err;
    err = ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 6, 0, mvbuf_vk, 0, mvbuf_vk->size,
                                         VK_FORMAT_UNDEFINED);
    if (err < 0)
        return err;
    err = ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 7, 0, confbuf_vk, 0,
                                         confbuf_vk->size, VK_FORMAT_UNDEFINED);
    if (err < 0)
        return err;
    err = ff_vk_exec_add_dep_buf(vkctx, exec, mvbuf, 1, 0);
    if (err < 0)
        return err;
    *mvbuf = NULL;
    err = ff_vk_exec_add_dep_buf(vkctx, exec, confbuf, 1, 0);
    if (err < 0)
        return err;
    *confbuf = NULL;
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, out, views->out, 0, 8,
                                  VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, bound_next, views->next, 0, 9,
                                  VK_IMAGE_LAYOUT_GENERAL, VK_NULL_HANDLE);
    return 0;
}

static void denoise_collect_barriers(FFVulkanContext *vkctx, FFVkExecContext *exec, AVFrame *out,
                                     AVFrame *cur, AVFrame *const bound_prev[], int actual_prev,
                                     AVFrame *next, AVFrame *bound_next,
                                     VkImageMemoryBarrier2 *img_bar, int *nb_img_bar)
{
    int i;

    /* Image barriers: output writable, all inputs readable. */
    ff_vk_frame_barrier(vkctx, exec, out, img_bar, nb_img_bar,
                        VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                        VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_WRITE_BIT,
                        VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED);
    ff_vk_frame_barrier(vkctx, exec, cur, img_bar, nb_img_bar,
                        VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                        VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,
                        VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED);
    for (i = 0; i < actual_prev; i++)
        ff_vk_frame_barrier(vkctx, exec, bound_prev[i], img_bar, nb_img_bar,
                            VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                            VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,
                            VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED);
    /* Only a real next frame needs its own read barrier; the cur filler already
     * has one above (a duplicate barrier on the same image would be redundant). */
    if (next)
        ff_vk_frame_barrier(vkctx, exec, bound_next, img_bar, nb_img_bar,
                            VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                            VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,
                            VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED);
}

static void denoise_pre_dispatch_barrier(FFVulkanFunctions *vk, FFVkExecContext *exec,
                                        FFVkBuffer *buf_vk, VkImageMemoryBarrier2 *img_bar,
                                        int nb_img_bar, int want_meta)
{
    if (want_meta) {
        /* Zero the accumulators, then sync TRANSFER->COMPUTE before the dispatch
         * reads/writes the SSBO; also flush the image barriers in the same call. */
        vk->CmdPipelineBarrier2(exec->buf,
                                &(VkDependencyInfo){
                                    .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                    .pBufferMemoryBarriers =
                                        &(VkBufferMemoryBarrier2){
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
        vk->CmdPipelineBarrier2(exec->buf,
                                &(VkDependencyInfo){
                                    .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                    .pImageMemoryBarriers = img_bar,
                                    .imageMemoryBarrierCount = nb_img_bar,
                                    .pBufferMemoryBarriers =
                                        &(VkBufferMemoryBarrier2){
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
        vk->CmdPipelineBarrier2(exec->buf, &(VkDependencyInfo){
                                               .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                               .pImageMemoryBarriers = img_bar,
                                               .imageMemoryBarrierCount = nb_img_bar,
                                           });
    }
}

static void denoise_cmd_dispatch(PelorusDenoiseVulkanContext *s, FFVkExecContext *exec,
                                int width, int height)
{
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanFunctions *vk = &vkctx->vkfn;

    ff_vk_exec_bind_shader(vkctx, exec, &s->shd);
    ff_vk_shader_update_push_const(vkctx, exec, &s->shd, VK_SHADER_STAGE_COMPUTE_BIT, 0,
                                   sizeof(s->opts), &s->opts);
    vk->CmdDispatch(exec->buf, FFALIGN(width, s->shd.lg_size[0]) / s->shd.lg_size[0],
                    FFALIGN(height, s->shd.lg_size[1]) / s->shd.lg_size[1], s->shd.lg_size[2]);
}

static void denoise_post_dispatch_barrier(FFVulkanFunctions *vk, FFVkExecContext *exec,
                                         FFVkBuffer *buf_vk)
{
    vk->CmdPipelineBarrier2(exec->buf,
                            &(VkDependencyInfo){
                                .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                .pBufferMemoryBarriers =
                                    &(VkBufferMemoryBarrier2){
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
}

static int denoise_finish_exec(PelorusDenoiseVulkanContext *s, FFVkExecContext *exec,
                               FFVkBuffer *buf_vk, AVBufferRef **buf,
                               const PelorusDenoiseBuf **acc_out, AVBufferRef **buf_out)
{
    int err;
    FFVulkanContext *vkctx = &s->vkctx;

    if (s->meta) {
        denoise_post_dispatch_barrier(&vkctx->vkfn, exec, buf_vk);
        err = ff_vk_exec_submit(vkctx, exec);
        if (err < 0)
            return err;
        ff_vk_exec_wait(vkctx, exec);
        *acc_out = (const PelorusDenoiseBuf *)buf_vk->mapped_mem;
        *buf_out = *buf;
        return 0;
    }

    /* meta=0: no readback, no sync point — just submit and return. The stat
     * buffer bound at 5 is never read back, but it must outlive the async submit
     * (same treatment as the mv/conf buffers above). */
    err = ff_vk_exec_add_dep_buf(vkctx, exec, buf, 1, 0);
    if (err < 0)
        return err;
    *buf = NULL;
    return ff_vk_exec_submit(vkctx, exec);
}

/* Bind the current frame, the actual_prev valid previous frames, the (optional)
 * residual SSBO, the output and the (optional) forward-lookahead next frame via
 * the hand-rolled explicit exec path, dispatch, and (when meta) submit+wait
 * reading the residual back. Inputs first, output then next0 LAST. Unused prev
 * slots are bound to the current frame as harmless filler so every array
 * descriptor is populated; the shader only reads 1..actual_prev. `next` is NULL
 * when there is no forward frame (lookahead==0 or EOF flush) — then next0 is
 * bound to cur as filler and actual_next is 0 (the forward tap never fires). */
static int denoise_dispatch(PelorusDenoiseVulkanContext *s, AVFrame *out, AVFrame *cur,
                            AVFrame *const prev[], int actual_prev, AVFrame *next,
                            const PelorusDenoiseBuf **acc_out, AVBufferRef **buf_out)
{
    int err = 0, i;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVkExecContext *exec = NULL;
    AVFrame *bound_prev[PEL_DENOISE_MAX_PREV], *bound_next = next ? next : cur;
    DenoiseViews views;
    VkImageMemoryBarrier2 img_bar[64];
    int nb_img_bar = 0;
    AVBufferRef *buf = NULL, *mvbuf = NULL, *confbuf = NULL;
    FFVkBuffer *buf_vk;

    *acc_out = NULL;
    *buf_out = NULL;

    /* Every prev array descriptor must be populated; fill the slots past
     * actual_prev with the current frame (never read by the shader). */
    for (i = 0; i < PEL_DENOISE_MAX_PREV; i++)
        bound_prev[i] = (i < actual_prev) ? prev[i] : cur;

    s->opts.actual_prev = actual_prev;
    s->opts.actual_next = (next != NULL) ? 1 : 0;
    s->opts.want_meta = s->meta;

    RET(denoise_setup_buffers(s, cur, out->width, out->height, &buf, &mvbuf, &confbuf));
    buf_vk = (FFVkBuffer *)buf->data;

    exec = ff_vk_exec_get(vkctx, &s->e);
    ff_vk_exec_start(vkctx, exec);

    RET(denoise_add_dep_frames(vkctx, exec, out, cur, bound_prev, actual_prev, next, bound_next,
                               &views));
    RET(denoise_bind_descriptors(s, exec, out, cur, bound_prev, bound_next, &views, buf_vk, &mvbuf,
                                 &confbuf));

    denoise_collect_barriers(vkctx, exec, out, cur, bound_prev, actual_prev, next, bound_next,
                             img_bar, &nb_img_bar);
    denoise_pre_dispatch_barrier(&vkctx->vkfn, exec, buf_vk, img_bar, nb_img_bar, s->meta);
    denoise_cmd_dispatch(s, exec, out->width, out->height);

    RET(denoise_finish_exec(s, exec, buf_vk, &buf, acc_out, buf_out));
    return 0;

fail:
    if (exec)
        ff_vk_exec_discard_deps(vkctx, exec);
    av_buffer_unref(&buf);
    av_buffer_unref(&mvbuf);
    av_buffer_unref(&confbuf);
    return err;
}

static int denoise_run(AVFilterContext *ctx, AVFrame *out, AVFrame *cur, AVFrame *next)
{
    int err;
    PelorusDenoiseVulkanContext *s = ctx->priv;
    AVFrame *const *prev = (AVFrame *const *)s->ring;
    const PelorusDenoiseBuf *acc = NULL;
    AVBufferRef *buf = NULL;
    int actual_prev;

    if (!s->initialized) {
        err = init_filter(ctx);
        if (err < 0)
            return err;
    }

    s->opts.frame_idx = (uint32_t)(s->frame_idx++);

    /* Causal window: only the frames already seen, capped at the configured
     * depth. ring[0] is the most recent previous frame. */
    actual_prev = FFMIN(s->n_prev, s->ring_count);

    err = denoise_dispatch(s, out, cur, prev, actual_prev, next, &acc, &buf);
    if (err < 0)
        return err;

    err = av_frame_copy_props(out, cur);
    if (err < 0) {
        av_buffer_unref(&buf);
        return err;
    }

    if (s->meta && acc) {
        err = attach_interop(s, out, acc);
        if (err < 0) {
            av_buffer_unref(&buf);
            return err;
        }
    }
    av_buffer_unref(&buf);
    return 0;
}

static int denoise_ring_push(PelorusDenoiseVulkanContext *s, AVFrame *cur)
{
    /* Push the processed frame into the ring (cheap clone — refcount bump on the
     * hwframe images, no pixel copy), evicting the oldest. */
    AVFrame *clone = av_frame_clone(cur);
    int i;

    if (!clone)
        return AVERROR(ENOMEM);

    if (s->ring_count == s->n_prev && s->n_prev > 0)
        av_frame_free(&s->ring[s->n_prev - 1]);
    for (i = FFMIN(s->ring_count, s->n_prev - 1); i > 0; i--)
        s->ring[i] = s->ring[i - 1];
    if (s->n_prev > 0) {
        s->ring[0] = clone;
        if (s->ring_count < s->n_prev)
            s->ring_count++;
    } else {
        av_frame_free(&clone); /* depth 0: temporal disabled, keep no ring */
    }

    return 0;
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
    AVFrame *out = NULL;
    AVFilterLink *outlink = ctx->outputs[0];

    out = ff_get_video_buffer(outlink, outlink->w, outlink->h);
    if (!out) {
        av_frame_free(&cur);
        return AVERROR(ENOMEM);
    }

    err = denoise_run(ctx, out, cur, next);
    if (err < 0) {
        av_frame_free(&cur);
        av_frame_free(&out);
        return err;
    }

    err = denoise_ring_push(ctx->priv, cur);
    if (err < 0) {
        av_frame_free(&cur);
        av_frame_free(&out);
        return err;
    }

    av_frame_free(&cur);
    return ff_filter_frame(outlink, out);
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
        if ((ret = pel_vk_refuse_interlaced(ctx, in)) < 0) {
            av_frame_free(&in);
            return ret;
        }
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

static int pelorus_denoise_vulkan_config_output(AVFilterLink *outlink)
{
    PelorusDenoiseVulkanContext *s = outlink->src->priv;

    return pel_vk_pool_config_output(outlink, &s->pool);
}

#define OFFSET(x) offsetof(PelorusDenoiseVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_denoise_vulkan_options[] = {
    {"sigma",
     "luma spatial range sigma (edge sensitivity, normalized)",
     OFFSET(opt_sigma_y),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.03},
     0.0,
     0.5,
     FLAGS},
    {"sigmac",
     "chroma spatial range sigma (normalized)",
     OFFSET(opt_sigma_c),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.04},
     0.0,
     0.5,
     FLAGS},
    {"sigmat",
     "temporal gate bandwidth (normalized)",
     OFFSET(opt_sigma_t),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.05},
     0.0,
     0.5,
     FLAGS},
    {"strength",
     "luma dry/wet mix (0..1)",
     OFFSET(opt_strength_y),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.30},
     0.0,
     1.0,
     FLAGS},
    {"strengthc",
     "chroma dry/wet mix (0..1)",
     OFFSET(opt_strength_c),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.20},
     0.0,
     1.0,
     FLAGS},
    {"blend",
     "spatial<->temporal blend (0=spatial, 1=temporal)",
     OFFSET(opt_blend),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.6},
     0.0,
     1.0,
     FLAGS},
    {"tdecay",
     "per-frame temporal trust falloff",
     OFFSET(opt_tdecay),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.8},
     0.0,
     1.0,
     FLAGS},
    {"tcut",
     "per-pixel scene-cut/fast-motion clamp (normalized)",
     OFFSET(opt_tcut),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.10},
     0.0,
     0.5,
     FLAGS},
    {"patch",
     "spatial window radius (0 = temporal-only)",
     OFFSET(patch_radius),
     AV_OPT_TYPE_INT,
     {.i64 = 1},
     0,
     3,
     FLAGS},
    {"prev",
     "causal temporal depth (previous frames held in VRAM)",
     OFFSET(n_prev),
     AV_OPT_TYPE_INT,
     {.i64 = 3},
     0,
     PEL_DENOISE_MAX_PREV,
     FLAGS},
    {"protect",
     "damp strength on textured / edge regions",
     OFFSET(protect_detail),
     AV_OPT_TYPE_BOOL,
     {.i64 = 1},
     0,
     1,
     FLAGS},
    {"planes",
     "planes to process (bitmask)",
     OFFSET(planes),
     AV_OPT_TYPE_INT,
     {.i64 = 0xF},
     0,
     0xF,
     FLAGS},
    {"meta",
     "attach Pelorus interop side data",
     OFFSET(meta),
     AV_OPT_TYPE_BOOL,
     {.i64 = 0},
     0,
     1,
     FLAGS},
    {"mc",
     "motion-compensated temporal taps (consume an upstream pelorus_mc "
     "PEL_SEC_MOTION field; requires mc before denoise)",
     OFFSET(mc),
     AV_OPT_TYPE_BOOL,
     {.i64 = 0},
     0,
     1,
     FLAGS},
    {"tile",
     "cache the spatial search window in shared memory (large win on "
     "bandwidth-limited GPUs, ~neutral on cache-rich ones; output is "
     "bit-identical)",
     OFFSET(tile),
     AV_OPT_TYPE_BOOL,
     {.i64 = 0},
     0,
     1,
     FLAGS},
    {"lookahead",
     "forward-lookahead temporal depth (0 = causal, the default, "
     "bit-identical; 1 = bidirectional: delay output 1 frame so the "
     "temporal walk also samples the NEXT frame, tcut-gated — recovers "
     "the leading frame of a held animation drawing, ADR-0137; opt-in "
     "for cadence/animation content)",
     OFFSET(lookahead),
     AV_OPT_TYPE_INT,
     {.i64 = 0},
     0,
     1,
     FLAGS},
    PEL_VK_POOL_OPTIONS(OFFSET(pool.tiling), OFFSET(pool.drm_modifiers), FLAGS),
    {NULL}};

AVFILTER_DEFINE_CLASS(pelorus_denoise_vulkan);

static const AVFilterPad pelorus_denoise_vulkan_inputs[] = {
    {
        /* No .filter_frame: the buffered forward-lookahead lifecycle is driven by
         * the .activate callback below (ADR-0137). */
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &pel_vk_config_input_yuv_rgba,
    },
};

static const AVFilterPad pelorus_denoise_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &pelorus_denoise_vulkan_config_output,
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
