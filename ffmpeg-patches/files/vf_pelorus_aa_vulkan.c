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
 * Pelorus anime warp anti-aliasing + line-darkening, Vulkan compute (zero-copy).
 *
 * A single-pass GPU port of awarpsharp2 (warp-AA) + optional FastLineDarken.
 * Anime line-art accumulates aliasing ("jaggies") through repeated scale/encode;
 * awarpsharp builds a blurred edge-strength map and *warps* each pixel toward
 * the nearest edge by the gradient of that map, pulling stair-stepped samples
 * onto the line (anti-aliasing + thinning) without the ringing a sharpen adds.
 * Line-darkening then deepens the dark side of edges so lines stay crisp.
 *
 * Luma only; chroma passes through. Zero-copy in VRAM (FF_VK_REP_FLOAT UNORM,
 * bit-depth-agnostic). Part of the anime `tune` pipeline (ADR-0124). The
 * algorithm lives in vulkan/pelorus_aa.comp.glsl, compiled to SPIR-V at build
 * time and linked in here.
 */

#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "vulkan_filter.h"

#include "filters.h"
#include "video.h"

#include <string.h>

typedef struct PelorusAaVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;

    /* push constants — mirror the std430 block in
     * vulkan/pelorus_aa.comp.glsl, byte-for-byte */
    struct {
        int32_t blur;     /* edge-map blur radius (0..MAX_R)                 */
        float depth;      /* warp displacement scale (pixels per gradient)   */
        float thresh;     /* edge-map clamp ceiling (normalized)             */
        float darkstr;    /* line-darkening strength [0,1] (0 = off)         */
        float edge_thr;   /* Sobel magnitude that counts as a line           */
    } opts;

    int planes; /* plane bitmask to process (luma-only by default)           */
    int fast;   /* hoist sobel_mag into shared mem (opt-in; default 0)        */
} PelorusAaVulkanContext;

/* The warp-AA algorithm now lives in vulkan/pelorus_aa.comp.glsl, compiled to
 * SPIR-V at build time and linked in here. FFmpeg 9 removed the runtime GLSL
 * builder (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also retires the old
 * inline-vs-reference lockstep duplication. The shader's plane count, plane
 * bitmask and the `fast` shared-memory hoist — all previously C-side codegen
 * decisions — are now specialization constants. */
extern const unsigned char ff_pelorus_aa_comp_spv_data[];
extern const unsigned int  ff_pelorus_aa_comp_spv_len;

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    PelorusAaVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd;
    const int planes = av_pix_fmt_count_planes(vkctx->output_format);

    s->qf = ff_vk_qf_find(vkctx, VK_QUEUE_COMPUTE_BIT, 0);
    if (!s->qf) {
        av_log(ctx, AV_LOG_ERROR, "Device has no compute queues!\n");
        err = AVERROR(ENOTSUP);
        goto fail;
    }

    RET(ff_vk_exec_pool_init(vkctx, s->qf, &s->e, s->qf->num * 4, 0, 0, 0, NULL));

    /* Plane count, the plane bitmask and the `fast` shared-memory hoist were
     * const-folded into the generated GLSL before FFmpeg 9 unrolled the
     * per-plane loop in C. With precompiled SPIR-V they become specialization
     * constants, folded at pipeline-compile time to the same single variant. */
    SPEC_LIST_CREATE(sl, 3, 3 * sizeof(uint32_t))
    SPEC_LIST_ADD(sl, 0, 32, (uint32_t)planes);
    SPEC_LIST_ADD(sl, 1, 32, (uint32_t)s->planes);
    SPEC_LIST_ADD(sl, 2, 32, (uint32_t)(s->fast != 0));

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
                          ff_pelorus_aa_comp_spv_data,
                          ff_pelorus_aa_comp_spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    return err;
}

static int pelorus_aa_vulkan_filter_frame(AVFilterLink *link, AVFrame *in)
{
    int err;
    AVFrame *out = NULL;
    AVFilterContext *ctx = link->dst;
    PelorusAaVulkanContext *s = ctx->priv;
    AVFilterLink *outlink = ctx->outputs[0];

    out = ff_get_video_buffer(outlink, outlink->w, outlink->h);
    if (!out) {
        err = AVERROR(ENOMEM);
        goto fail;
    }

    if (!s->initialized)
        RET(init_filter(ctx));

    RET(ff_vk_filter_process_simple(&s->vkctx, &s->e, &s->shd, out, in,
                                    VK_NULL_HANDLE, 1, &s->opts, sizeof(s->opts)));

    err = av_frame_copy_props(out, in);
    if (err < 0)
        goto fail;

    av_frame_free(&in);
    return ff_filter_frame(outlink, out);

fail:
    av_frame_free(&in);
    av_frame_free(&out);
    return err;
}

static void pelorus_aa_vulkan_uninit(AVFilterContext *avctx)
{
    PelorusAaVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusAaVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_aa_vulkan_options[] = {
    { "blur", "edge-map blur radius in pixels", OFFSET(opts.blur),
      AV_OPT_TYPE_INT, { .i64 = 2 }, 0, 8, FLAGS },
    { "depth", "warp displacement scale (pixels per unit gradient)", OFFSET(opts.depth),
      AV_OPT_TYPE_FLOAT, { .dbl = 8.0 }, 0.0, 64.0, FLAGS },
    { "thresh", "edge-map clamp ceiling (normalized)", OFFSET(opts.thresh),
      AV_OPT_TYPE_FLOAT, { .dbl = 0.5 }, 0.0, 1.0, FLAGS },
    { "darkstr", "line-darkening strength (0 = off)", OFFSET(opts.darkstr),
      AV_OPT_TYPE_FLOAT, { .dbl = 0.0 }, 0.0, 1.0, FLAGS },
    { "edge", "Sobel magnitude that counts as a line (for darkening)", OFFSET(opts.edge_thr),
      AV_OPT_TYPE_FLOAT, { .dbl = 0.08 }, 0.0, 1.0, FLAGS },
    { "planes", "planes to process (bitmask; default luma only)", OFFSET(planes),
      AV_OPT_TYPE_INT, { .i64 = 0x1 }, 0, 0xF, FLAGS },
    { "fast", "hoist the redundant sobel-mag into shared memory — bit-identical, "
              "a large ALU-bound speedup (ADR-0140); opt-in, default off", OFFSET(fast),
      AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(pelorus_aa_vulkan);

static const AVFilterPad pelorus_aa_vulkan_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &pelorus_aa_vulkan_filter_frame,
        .config_props = &ff_vk_filter_config_input,
    },
};

static const AVFilterPad pelorus_aa_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_aa_vulkan = {
    .p.name = "pelorus_aa_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus anime warp-AA + line-darkening (Vulkan)"),
    .p.priv_class = &pelorus_aa_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusAaVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &pelorus_aa_vulkan_uninit,
    FILTER_INPUTS(pelorus_aa_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_aa_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
