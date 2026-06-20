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
 * Foundation of the anime `tune` pipeline (ADR-0123). Kept in lockstep with the
 * reference shader libpelorus/shaders/pelorus_dehalo.comp (AGENTS hard rule 4).
 */

#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "libavutil/vulkan_spirv.h"
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
} PelorusDehaloVulkanContext;

/* Pure GLSL helpers + the dehalo() function, spliced verbatim (avoids the
 * comma-in-C()-macro hazard). Kept in lockstep with the standalone reference
 * shader libpelorus/shaders/pelorus_dehalo.comp — the only intended difference
 * is the working domain: the .comp reads r16ui and normalizes by 65535, this
 * inline form reads FF_VK_REP_FLOAT (UNORM) storage images already in [0,1]. */
static const char dehalo_glsl[] =
    "const int MAX_R = 8;\n"
    "float pel_luma(int idx, ivec2 p, ivec2 sz) {\n"
    "    return imageLoad(input_images[idx], clamp(p, ivec2(0), sz - ivec2(1))).x;\n"
    "}\n"
    "float box_blur(int idx, ivec2 c, int r, ivec2 sz) {\n"
    "    float acc = 0.0; float n = 0.0;\n"
    "    for (int dy = -MAX_R; dy <= MAX_R; dy++) {\n"
    "        if (dy < -r || dy > r) continue;\n"
    "        for (int dx = -MAX_R; dx <= MAX_R; dx++) {\n"
    "            if (dx < -r || dx > r) continue;\n"
    "            acc += pel_luma(idx, c + ivec2(dx, dy), sz); n += 1.0;\n"
    "        }\n"
    "    }\n"
    "    return acc / max(n, 1.0);\n"
    "}\n"
    "void dehalo(ivec2 pos, int idx) {\n"
    "    ivec2 sz = imageSize(output_images[idx]);\n"
    "    int r = clamp(blur_r, 1, MAX_R);\n"
    "    float c = pel_luma(idx, pos, sz);\n"
    "    float h  = box_blur(idx, pos,                r, sz);\n"
    "    float hl = box_blur(idx, pos + ivec2(-1, 0), r, sz);\n"
    "    float hr = box_blur(idx, pos + ivec2( 1, 0), r, sz);\n"
    "    float hu = box_blur(idx, pos + ivec2( 0,-1), r, sz);\n"
    "    float hd = box_blur(idx, pos + ivec2( 0, 1), r, sz);\n"
    "    float oMax = c; float oMin = c;\n"
    "    for (int dy = -1; dy <= 1; dy++) {\n"
    "        for (int dx = -1; dx <= 1; dx++) {\n"
    "            float v = pel_luma(idx, pos + ivec2(dx, dy), sz);\n"
    "            oMax = max(oMax, v); oMin = min(oMin, v);\n"
    "        }\n"
    "    }\n"
    "    float are  = oMax - oMin;\n"
    "    float ugly = max(max(max(h, hl), max(hr, hu)), hd)\n"
    "               - min(min(min(h, hl), min(hr, hu)), hd);\n"
    "    const float EPS = 0.0039;\n"
    "    float frac = (are - ugly) / (are + EPS);\n"
    "    float so   = clamp((frac - lowsens) * (1.0 + highsens), 0.0, 1.0);\n"
    "    float lets = mix(c, h, so);\n"
    "    float out_v;\n"
    "    if (lets < c) out_v = c - (c - lets) * brightstr;\n"
    "    else          out_v = c - (c - lets) * darkstr;\n"
    "    float gx = 0.0; float gy = 0.0;\n"
    "    float kx[9] = float[9](-1.0, 0.0, 1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0);\n"
    "    float ky[9] = float[9](-1.0,-2.0,-1.0,  0.0, 0.0, 0.0,  1.0, 2.0, 1.0);\n"
    "    int k = 0;\n"
    "    for (int dy = -1; dy <= 1; dy++) {\n"
    "        for (int dx = -1; dx <= 1; dx++) {\n"
    "            float v = pel_luma(idx, pos + ivec2(dx, dy), sz);\n"
    "            gx += v * kx[k]; gy += v * ky[k]; k++;\n"
    "        }\n"
    "    }\n"
    "    float edge = sqrt(gx * gx + gy * gy);\n"
    "    bool on_line = edge > edge_thr;\n"
    "    int rr = clamp(int(ring + 0.5), 1, MAX_R);\n"
    "    bool near_line = false;\n"
    "    for (int d = 1; d <= MAX_R; d++) {\n"
    "        if (d > rr) break;\n"
    "        float em = max(max(pel_luma(idx, pos + ivec2(d, 0), sz),\n"
    "                           pel_luma(idx, pos + ivec2(-d, 0), sz)),\n"
    "                       max(pel_luma(idx, pos + ivec2(0, d), sz),\n"
    "                           pel_luma(idx, pos + ivec2(0,-d), sz)));\n"
    "        if (abs(em - c) > edge_thr) near_line = true;\n"
    "    }\n"
    "    float ring_mask = (near_line && !on_line) ? 1.0 : 0.0;\n"
    "    float result = mix(c, out_v, ring_mask);\n"
    "    imageStore(output_images[idx], pos, vec4(clamp(result, 0.0, 1.0)));\n"
    "}\n";

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    int i;
    PelorusDehaloVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd; /* GLSL macros require a var named `shd` */
    FFVkSPIRVCompiler *spv;
    uint8_t *spv_data = NULL;
    size_t spv_len = 0;
    void *spv_opaque = NULL;
    const int planes = av_pix_fmt_count_planes(vkctx->output_format);

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
    RET(ff_vk_shader_init(vkctx, shd, "pelorus_dehalo",
                          VK_SHADER_STAGE_COMPUTE_BIT, NULL, 0, 32, 32, 1, 0));

    GLSLC(0, layout(push_constant, std430) uniform pushConstants {            );
    GLSLC(1,     int   blur_r;                                                );
    GLSLC(1,     float darkstr;                                               );
    GLSLC(1,     float brightstr;                                             );
    GLSLC(1,     float lowsens;                                               );
    GLSLC(1,     float highsens;                                              );
    GLSLC(1,     float edge_thr;                                              );
    GLSLC(1,     float ring;                                                  );
    GLSLC(0, };                                                               );
    GLSLC(0,                                                                  );
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
        RET(ff_vk_shader_add_descriptor_set(vkctx, shd, desc_set, 2, 0, 0));
    }

    GLSLD(dehalo_glsl);
    GLSLC(0, void main()                                                      );
    GLSLC(0, {                                                                );
    GLSLC(1,     ivec2 size;                                                  );
    GLSLC(1,     const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);           );
    for (i = 0; i < planes; i++) {
        GLSLC(0,                                                              );
        GLSLF(1, size = imageSize(output_images[%i]);                       ,i);
        GLSLC(1, if (!IS_WITHIN(pos, size)) return;                           );
        if (s->planes & (1 << i)) {
            GLSLF(1, dehalo(pos, %i);                                       ,i);
        } else {
            GLSLF(1, imageStore(output_images[%i], pos,                      ,i);
            GLSLF(2,             imageLoad(input_images[%i], pos));          ,i);
        }
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
                                    VK_NULL_HANDLE, &s->opts, sizeof(s->opts)));

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
