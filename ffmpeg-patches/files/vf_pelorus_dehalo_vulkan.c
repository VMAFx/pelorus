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
 * Pelorus anime/2D dehalo + dering, Vulkan compute (zero-copy, pre-encode).
 *
 * A single-pass GPU port of HAvsFunc DeHalo_alpha + FineDehalo — the de-facto
 * VapourSynth anime halo remover. Halos are the bright/dark ringing left in the
 * flat band next to hard line-art by upstream compression and sharpening; they
 * cost the encoder bits and read as artefacts. We compute a strong blur of luma
 * (the halo-free target), a sensitivity mask from the local contrast the blur
 * removed (DeHalo_alpha lowsens/highsens shaping), pull halos toward the flat
 * remove-only and asymmetrically (darkstr/brightstr), and gate the whole thing
 * to the halo RING via a Sobel edge mask (FineDehalo) so line-art and open
 * gradients are protected. Luma only; chroma passes through. Runs entirely in
 * VRAM so the frame never leaves the GPU on its way to a hardware encoder.
 *
 * Foundation of the anime `tune` pipeline (ADR-0123).
 */

#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "vulkan_filter.h"

#include "filters.h"
#include "video.h"

#include <string.h>

typedef struct PelorusDehaloVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;

    /* push constants — mirror the GLSL std430 block below, byte-for-byte */
    struct {
        int32_t blur_r;     /* halo-blur radius in pixels (1..MAX_R)         */
        float darkstr;      /* pull strength for dark halos   [0,1]          */
        float brightstr;    /* pull strength for bright halos [0,1]          */
        float lowsens;      /* sensitivity floor (normalized)                */
        float highsens;     /* sensitivity gain  (normalized)                */
        float edge_thr;     /* Sobel magnitude above which a pixel is a line */
        float ring;         /* edge-mask dilation (ring half-width, pixels)  */
    } opts;

    int planes; /* plane bitmask to process (luma-only by default)           */
    int tile;   /* shared-memory tile the box-blur window (ADR-0139, opt-in)  */
} PelorusDehaloVulkanContext;

/* The dehalo algorithm — including the ADR-0139 shared-memory box-blur tiling
 * path — now lives in vulkan/pelorus_dehalo.comp.glsl, compiled to SPIR-V at
 * build time and linked in here. FFmpeg 9 removed the runtime GLSL builder
 * (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also retires the old
 * inline-vs-reference lockstep duplication. */
extern const unsigned char ff_pelorus_dehalo_comp_spv_data[];
extern const unsigned int  ff_pelorus_dehalo_comp_spv_len;

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    PelorusDehaloVulkanContext *s = ctx->priv;
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

    /* The plane count, the plane bitmask and the tile switch were const-folded
     * into the generated GLSL before FFmpeg 9 (the C side unrolled the
     * per-plane loop and emitted either the direct or the tiled pel_luma).
     * With precompiled SPIR-V they become specialization constants, so the
     * driver still folds them at pipeline-creation time — including the shared
     * tile array, which is sized from `tile` and therefore costs nothing at
     * the default tile=0. */
    SPEC_LIST_CREATE(sl, 3, 3 * sizeof(uint32_t))
    SPEC_LIST_ADD(sl, 0, 32, (uint32_t)planes);
    SPEC_LIST_ADD(sl, 1, 32, (uint32_t)s->planes);
    SPEC_LIST_ADD(sl, 2, 32, (uint32_t)s->tile);

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
                          ff_pelorus_dehalo_comp_spv_data,
                          ff_pelorus_dehalo_comp_spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    return err;
}

static int pelorus_dehalo_vulkan_filter_frame(AVFilterLink *link, AVFrame *in)
{
    int err;
    AVFrame *out = NULL;
    AVFilterContext *ctx = link->dst;
    PelorusDehaloVulkanContext *s = ctx->priv;
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

static void pelorus_dehalo_vulkan_uninit(AVFilterContext *avctx)
{
    PelorusDehaloVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusDehaloVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_dehalo_vulkan_options[] = {
    { "blur", "halo-blur radius in pixels", OFFSET(opts.blur_r),
      AV_OPT_TYPE_INT, { .i64 = 2 }, 1, 8, FLAGS },
    { "darkstr", "pull strength for dark halos", OFFSET(opts.darkstr),
      AV_OPT_TYPE_FLOAT, { .dbl = 1.0 }, 0.0, 1.0, FLAGS },
    { "brightstr", "pull strength for bright halos", OFFSET(opts.brightstr),
      AV_OPT_TYPE_FLOAT, { .dbl = 1.0 }, 0.0, 1.0, FLAGS },
    { "lowsens", "sensitivity floor (normalized)", OFFSET(opts.lowsens),
      AV_OPT_TYPE_FLOAT, { .dbl = 0.0625 }, 0.0, 1.0, FLAGS },
    { "highsens", "sensitivity gain (normalized)", OFFSET(opts.highsens),
      AV_OPT_TYPE_FLOAT, { .dbl = 0.5 }, 0.0, 4.0, FLAGS },
    { "edge", "Sobel magnitude above which a pixel is line-art", OFFSET(opts.edge_thr),
      AV_OPT_TYPE_FLOAT, { .dbl = 0.08 }, 0.0, 1.0, FLAGS },
    { "ring", "edge-mask dilation (ring half-width, pixels)", OFFSET(opts.ring),
      AV_OPT_TYPE_FLOAT, { .dbl = 2.0 }, 1.0, 8.0, FLAGS },
    { "planes", "planes to process (bitmask; default luma only)", OFFSET(planes),
      AV_OPT_TYPE_INT, { .i64 = 0x1 }, 0, 0xF, FLAGS },
    { "tile", "shared-memory tile the box-blur window (faster on bandwidth-limited GPUs; bit-identical)",
      OFFSET(tile), AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, FLAGS },
    { NULL }
};

AVFILTER_DEFINE_CLASS(pelorus_dehalo_vulkan);

static const AVFilterPad pelorus_dehalo_vulkan_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &pelorus_dehalo_vulkan_filter_frame,
        .config_props = &ff_vk_filter_config_input,
    },
};

static const AVFilterPad pelorus_dehalo_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_dehalo_vulkan = {
    .p.name = "pelorus_dehalo_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus anime dehalo + dering (Vulkan)"),
    .p.priv_class = &pelorus_dehalo_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusDehaloVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &pelorus_dehalo_vulkan_uninit,
    FILTER_INPUTS(pelorus_dehalo_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_dehalo_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
