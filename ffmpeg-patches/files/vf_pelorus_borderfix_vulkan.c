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
 * Pelorus dirty-line / border repair, Vulkan compute (zero-copy, pre-encode).
 *
 * Cropped, telecined, and analog-captured sources carry garbage rows/columns at
 * the frame edge (half-pixels, clamp lines, head-switching noise) that cost the
 * encoder bits and read as a dirty border. The GPU equivalent of FFmpeg's
 * fillborders=smear: each pixel inside the dirty band is replaced by the nearest
 * clean interior pixel, smearing the good edge outward. Runs in VRAM so the
 * Pelorus pipeline never has to drop to a CPU fillborders + the hwdownload round
 * trip it would cost. Bit-depth-agnostic (FF_VK_REP_FLOAT UNORM); processes all
 * planes by default (the dirty band is in luma and chroma alike).
 *
 * The algorithm lives in vulkan/pelorus_borderfix.comp.glsl, compiled to
 * SPIR-V at build time and linked in here.
 * The band widths are interpreted in each plane's own pixels.
 */

#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "vulkan_filter.h"

#include "filters.h"
#include "video.h"

#include <string.h>

typedef struct PelorusBorderfixVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;

    /* push constants — mirror the std430 block in the .comp.glsl, byte-for-byte */
    struct {
        int32_t left;
        int32_t right;
        int32_t top;
        int32_t bottom;
    } opts;

    int planes; /* plane bitmask to process (all planes by default)          */
} PelorusBorderfixVulkanContext;

/* The borderfix algorithm now lives in vulkan/pelorus_borderfix.comp.glsl,
 * compiled to SPIR-V at build time and linked in here. FFmpeg 9 removed the
 * runtime GLSL builder (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also
 * retires the old inline-vs-reference lockstep duplication. */
extern const unsigned char ff_pelorus_borderfix_comp_spv_data[];
extern const unsigned int  ff_pelorus_borderfix_comp_spv_len;

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    PelorusBorderfixVulkanContext *s = ctx->priv;
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
                          ff_pelorus_borderfix_comp_spv_data,
                          ff_pelorus_borderfix_comp_spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    return err;
}

static int pelorus_borderfix_vulkan_filter_frame(AVFilterLink *link, AVFrame *in)
{
    int err;
    AVFrame *out = NULL;
    AVFilterContext *ctx = link->dst;
    PelorusBorderfixVulkanContext *s = ctx->priv;
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

static void pelorus_borderfix_vulkan_uninit(AVFilterContext *avctx)
{
    PelorusBorderfixVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusBorderfixVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_borderfix_vulkan_options[] = {
    { "left", "dirty band width on the left edge (this plane's px)", OFFSET(opts.left),
      AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 4096, FLAGS },
    { "right", "dirty band width on the right edge", OFFSET(opts.right),
      AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 4096, FLAGS },
    { "top", "dirty band height on the top edge", OFFSET(opts.top),
      AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 4096, FLAGS },
    { "bottom", "dirty band height on the bottom edge", OFFSET(opts.bottom),
      AV_OPT_TYPE_INT, { .i64 = 0 }, 0, 4096, FLAGS },
    { "planes", "planes to process (bitmask; default all)", OFFSET(planes),
      AV_OPT_TYPE_INT, { .i64 = 0xF }, 0, 0xF, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(pelorus_borderfix_vulkan);

static const AVFilterPad pelorus_borderfix_vulkan_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &pelorus_borderfix_vulkan_filter_frame,
        .config_props = &ff_vk_filter_config_input,
    },
};

static const AVFilterPad pelorus_borderfix_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_borderfix_vulkan = {
    .p.name = "pelorus_borderfix_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus dirty-line / border repair (Vulkan)"),
    .p.priv_class = &pelorus_borderfix_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusBorderfixVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &pelorus_borderfix_vulkan_uninit,
    FILTER_INPUTS(pelorus_borderfix_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_borderfix_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
