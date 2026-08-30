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
 * Pelorus smart-deband, Vulkan compute (zero-copy, pre-encode).
 *
 * A psychovisual debander modeled on flash3kyuu_deband (f3kdb). The flat-test
 * is the vf_deband.c algorithm (4-tap rotated reference sampling, per-plane
 * threshold, average-or-keep) extended with TPDF/blue-noise grain injection, a
 * local-variance detail-protection mask, and a soft blend — run entirely in
 * VRAM so the frame never leaves the GPU on its way to a hardware encoder.
 *
 * Interop: when `meta=1` the filter links libpelorus and attaches a Pelorus
 * side-data blob (AV_FRAME_DATA_SEI_UNREGISTERED, UUID-keyed) announcing that
 * deband ran, so a downstream vmafx vf_libvmaf* can weight its score and avoid
 * double-counting. See <pelorus/interop.h> and docs/api/interop-abi.md.
 */

#include "libavutil/buffer.h"
#include "libavutil/frame.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "vulkan_filter.h"

#include "filters.h"
#include "video.h"

#include <string.h>

#include <pelorus/deband.h>
#include <pelorus/interop.h>

typedef struct PelorusDebandVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;

    /* push constants — mirror the GLSL std430 block below, byte-for-byte */
    struct {
        float thr[4];      /* vec4  : per-plane normalized threshold        */
        float grain[4];    /* vec4  : per-plane normalized grain amplitude  */
        int32_t range;     /* sampling radius                               */
        int32_t sample_mode;
        int32_t blur_mode;
        int32_t dither_mode;
        float softness;
        float detail_thr;
        int32_t flags;     /* enum pel_deband_flags                         */
        uint32_t frame_seed;
    } opts;

    /* AVOption-backed scalar mirrors (broadcast to all planes at init) */
    double opt_thr_y, opt_thr_c;
    double opt_grain_y, opt_grain_c;
    int planes;     /* plane bitmask to process (const-folded into shader)   */
    int dynamic_grain;
    int protect_detail;
    int meta;       /* attach Pelorus interop side data                     */
    int64_t frame_idx;
} PelorusDebandVulkanContext;

/* The deband algorithm now lives in vulkan/pelorus_deband.comp.glsl, compiled
 * to SPIR-V at build time and linked in here. FFmpeg 9 removed the runtime
 * GLSL builder (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also retires the
 * old inline-vs-reference lockstep duplication. */
extern const unsigned char ff_pelorus_deband_comp_spv_data[];
extern const unsigned int  ff_pelorus_deband_comp_spv_len;

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    int i;
    PelorusDebandVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd;
    const int planes = av_pix_fmt_count_planes(vkctx->output_format);

    /* Broadcast luma/chroma scalars into the per-plane vec4s. */
    s->opts.thr[0] = (float)s->opt_thr_y;
    s->opts.grain[0] = (float)s->opt_grain_y;
    for (i = 1; i < 4; i++) {
        s->opts.thr[i] = (float)s->opt_thr_c;
        s->opts.grain[i] = (float)s->opt_grain_c;
    }
    s->opts.flags = 0;
    if (s->dynamic_grain)
        s->opts.flags |= PEL_DEBAND_FLAG_DYNAMIC_GRAIN;
    if (s->protect_detail)
        s->opts.flags |= PEL_DEBAND_FLAG_PROTECT_DETAIL;

    s->qf = ff_vk_qf_find(vkctx, VK_QUEUE_COMPUTE_BIT, 0);
    if (!s->qf) {
        av_log(ctx, AV_LOG_ERROR, "Device has no compute queues!\n");
        err = AVERROR(ENOTSUP);
        goto fail;
    }

    RET(ff_vk_exec_pool_init(vkctx, s->qf, &s->e, s->qf->num * 4, 0, 0, 0, NULL));
    /* Plane count and the plane bitmask were const-folded into the generated
     * GLSL before FFmpeg 9 unrolled the per-plane loop in C. With precompiled
     * SPIR-V they become specialization constants instead. */
    SPEC_LIST_CREATE(sl, 2, 2 * sizeof(uint32_t))
    SPEC_LIST_ADD(sl, 0, 32, (uint32_t)planes);
    SPEC_LIST_ADD(sl, 1, 32, (uint32_t)s->planes);

    ff_vk_shader_load(shd, VK_SHADER_STAGE_COMPUTE_BIT, sl,
                      (uint32_t []) { 32, 32, 1 }, 0);

    ff_vk_shader_add_push_const(shd, 0, sizeof(s->opts),
                                VK_SHADER_STAGE_COMPUTE_BIT);

    {
        FFVulkanDescriptorSetBinding desc_set[] = {
            {
                .name = "input_images",
                .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
                .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format,
                                                   FF_VK_REP_FLOAT),
                .mem_quali = "readonly",
                .dimensions = 2,
                .elems = planes,
                .stages = VK_SHADER_STAGE_COMPUTE_BIT,
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
        };
        ff_vk_shader_add_descriptor_set(vkctx, shd, desc_set, 2, 0);
    }

    RET(ff_vk_shader_link(vkctx, shd,
                          ff_pelorus_deband_comp_spv_data,
                          ff_pelorus_deband_comp_spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    return err;
}

/* Attach a Pelorus interop blob announcing deband-applied with these params.
 * Measured per-cell banding maps are produced by vf_pelorus_analyze (a future
 * pass); this v0.1 emission marks the section present with summary scalars so
 * a downstream vmafx scorer can react. */
static void pel_sd_free(void *opaque, uint8_t *data)
{
    pel_blob_free(data);
}

static int attach_interop(PelorusDebandVulkanContext *s, AVFrame *out)
{
    PelorusSideData meta;
    PelorusBandingSection band;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    AVBufferRef *buf;
    AVFrameSideData *sd;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = (uint64_t)out->pts;
    meta.bit_depth = 0; /* depth/layout set by vf_pelorus_analyze; see note */
    meta.plane_layout = PEL_LAYOUT_420;
    meta.grid_cols = 0;
    meta.grid_rows = 0;
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'S');

    memset(&band, 0, sizeof(band));
    /* Configured aggressiveness as a coarse proxy until vf_pelorus_analyze
     * lands a measured map; documented in docs/api/interop-abi.md. */
    band.contour_strength_mean = s->opts.thr[0];

    sec.id = PEL_SEC_BANDING;
    sec.data = &band;
    sec.size = (uint32_t)sizeof(band);

    if (pel_blob_pack(&meta, &sec, 1, &blob, &len) != PEL_OK || !blob)
        return AVERROR(ENOMEM);

    buf = av_buffer_create(blob, len, pel_sd_free, NULL, 0);
    if (!buf) {
        pel_blob_free(blob);
        return AVERROR(ENOMEM);
    }
    sd = av_frame_new_side_data_from_buf(out, AV_FRAME_DATA_SEI_UNREGISTERED, buf);
    if (!sd) {
        av_buffer_unref(&buf);
        return AVERROR(ENOMEM);
    }
    return 0;
}

static int pelorus_deband_vulkan_filter_frame(AVFilterLink *link, AVFrame *in)
{
    int err;
    AVFrame *out = NULL;
    AVFilterContext *ctx = link->dst;
    PelorusDebandVulkanContext *s = ctx->priv;
    AVFilterLink *outlink = ctx->outputs[0];

    out = ff_get_video_buffer(outlink, outlink->w, outlink->h);
    if (!out) {
        err = AVERROR(ENOMEM);
        goto fail;
    }

    if (!s->initialized)
        RET(init_filter(ctx));

    s->opts.frame_seed = (uint32_t)(s->frame_idx++);

    RET(ff_vk_filter_process_simple(&s->vkctx, &s->e, &s->shd, out, in,
                                    VK_NULL_HANDLE, 1, &s->opts, sizeof(s->opts)));

    err = av_frame_copy_props(out, in);
    if (err < 0)
        goto fail;

    if (s->meta) {
        err = attach_interop(s, out);
        if (err < 0)
            goto fail;
    }

    av_frame_free(&in);
    return ff_filter_frame(outlink, out);

fail:
    av_frame_free(&in);
    av_frame_free(&out);
    return err;
}

static void pelorus_deband_vulkan_uninit(AVFilterContext *avctx)
{
    PelorusDebandVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusDebandVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_deband_vulkan_options[] = {
    { "range", "reference-sampling radius in pixels", OFFSET(opts.range),
      AV_OPT_TYPE_INT, { .i64 = 15 }, 1, 31, FLAGS },
    { "thry", "luma threshold (normalized)", OFFSET(opt_thr_y),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.012 }, 0.0, 0.25, FLAGS },
    { "thrc", "chroma threshold (normalized)", OFFSET(opt_thr_c),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.012 }, 0.0, 0.25, FLAGS },
    { "grainy", "luma grain amplitude (normalized)", OFFSET(opt_grain_y),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.006 }, 0.0, 0.4, FLAGS },
    { "grainc", "chroma grain amplitude (normalized)", OFFSET(opt_grain_c),
      AV_OPT_TYPE_DOUBLE, { .dbl = 0.0 }, 0.0, 0.4, FLAGS },
    { "softness", "soft-blend transition width (0=hard switch)",
      OFFSET(opts.softness), AV_OPT_TYPE_FLOAT, { .dbl = 0.5 }, 0.0, 1.0, FLAGS },
    { "detail", "detail-mask activity threshold (normalized)",
      OFFSET(opts.detail_thr), AV_OPT_TYPE_FLOAT, { .dbl = 0.06 }, 0.0, 0.25, FLAGS },
    { "sample", "reference-tap topology", OFFSET(opts.sample_mode),
      AV_OPT_TYPE_INT, { .i64 = 2 }, 1, 4, FLAGS, .unit = "sample" },
        { "column", "2 vertical taps", 0, AV_OPT_TYPE_CONST, { .i64 = 1 }, 0, 0, FLAGS, .unit = "sample" },
        { "square", "4 rotated taps", 0, AV_OPT_TYPE_CONST, { .i64 = 2 }, 0, 0, FLAGS, .unit = "sample" },
        { "row", "2 horizontal taps", 0, AV_OPT_TYPE_CONST, { .i64 = 3 }, 0, 0, FLAGS, .unit = "sample" },
        { "square_rot", "4 taps + per-frame rotation", 0, AV_OPT_TYPE_CONST, { .i64 = 4 }, 0, 0, FLAGS, .unit = "sample" },
    { "blur", "flat-test mode", OFFSET(opts.blur_mode),
      AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 1, FLAGS, .unit = "blur" },
        { "average", "|center-avg| < thr", 0, AV_OPT_TYPE_CONST, { .i64 = 0 }, 0, 0, FLAGS, .unit = "blur" },
        { "allrefs", "every tap within thr", 0, AV_OPT_TYPE_CONST, { .i64 = 1 }, 0, 0, FLAGS, .unit = "blur" },
    { "dither", "grain / dither mode", OFFSET(opts.dither_mode),
      AV_OPT_TYPE_INT, { .i64 = 2 }, 0, 2, FLAGS, .unit = "dither" },
        { "none", "no grain", 0, AV_OPT_TYPE_CONST, { .i64 = 0 }, 0, 0, FLAGS, .unit = "dither" },
        { "bayer8", "ordered 8x8 Bayer", 0, AV_OPT_TYPE_CONST, { .i64 = 1 }, 0, 0, FLAGS, .unit = "dither" },
        { "bluenoise", "hashed TPDF", 0, AV_OPT_TYPE_CONST, { .i64 = 2 }, 0, 0, FLAGS, .unit = "dither" },
    { "dynamic", "re-seed grain each frame", OFFSET(dynamic_grain),
      AV_OPT_TYPE_BOOL, { .i64 = 1 }, 0, 1, FLAGS },
    { "protect", "gate debanding off textured regions", OFFSET(protect_detail),
      AV_OPT_TYPE_BOOL, { .i64 = 1 }, 0, 1, FLAGS },
    { "planes", "planes to process (bitmask)", OFFSET(planes),
      AV_OPT_TYPE_INT, { .i64 = 0xF }, 0, 0xF, FLAGS },
    { "meta", "attach Pelorus interop side data", OFFSET(meta),
      AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(pelorus_deband_vulkan);

static const AVFilterPad pelorus_deband_vulkan_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &pelorus_deband_vulkan_filter_frame,
        .config_props = &ff_vk_filter_config_input,
    },
};

static const AVFilterPad pelorus_deband_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_deband_vulkan = {
    .p.name = "pelorus_deband_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus smart deband (Vulkan)"),
    .p.priv_class = &pelorus_deband_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusDebandVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &pelorus_deband_vulkan_uninit,
    FILTER_INPUTS(pelorus_deband_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_deband_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
