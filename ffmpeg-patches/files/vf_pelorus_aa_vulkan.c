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
 * bit-depth-agnostic). Part of the anime `tune` pipeline (ADR-0124); kept in
 * lockstep with libpelorus/shaders/pelorus_aa.comp (AGENTS hard rule 4).
 */

#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "libavutil/vulkan_spirv.h"
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

    /* push constants — mirror the GLSL std430 block below, byte-for-byte */
    struct {
        int32_t blur;     /* edge-map blur radius (0..MAX_R)                 */
        float depth;      /* warp displacement scale (pixels per gradient)   */
        float thresh;     /* edge-map clamp ceiling (normalized)             */
        float darkstr;    /* line-darkening strength [0,1] (0 = off)         */
        float edge_thr;   /* Sobel magnitude that counts as a line           */
    } opts;

    int planes; /* plane bitmask to process (luma-only by default)           */
} PelorusAaVulkanContext;

/* Pure GLSL helpers + the aa() function, spliced verbatim. Kept in lockstep with
 * the reference shader libpelorus/shaders/pelorus_aa.comp — the only intended
 * difference is the working domain: the .comp reads r16ui and normalizes by
 * 65535, this inline form reads FF_VK_REP_FLOAT (UNORM) images already in [0,1]. */
static const char aa_glsl[] =
    "const int MAX_R = 8;\n"
    "float pel_luma(int idx, ivec2 p, ivec2 sz) {\n"
    "    return imageLoad(input_images[idx], clamp(p, ivec2(0), sz - ivec2(1))).x;\n"
    "}\n"
    "float sobel_mag(int idx, ivec2 p, ivec2 sz) {\n"
    "    float gx = 0.0; float gy = 0.0;\n"
    "    float kx[9] = float[9](-1.0, 0.0, 1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0);\n"
    "    float ky[9] = float[9](-1.0,-2.0,-1.0,  0.0, 0.0, 0.0,  1.0, 2.0, 1.0);\n"
    "    int k = 0;\n"
    "    for (int dy = -1; dy <= 1; dy++) {\n"
    "        for (int dx = -1; dx <= 1; dx++) {\n"
    "            float v = pel_luma(idx, p + ivec2(dx, dy), sz);\n"
    "            gx += v * kx[k]; gy += v * ky[k]; k++;\n"
    "        }\n"
    "    }\n"
    "    return sqrt(gx * gx + gy * gy);\n"
    "}\n"
    "float emask(int idx, ivec2 p, ivec2 sz, int r, float thr) {\n"
    "    float acc = 0.0; float n = 0.0;\n"
    "    for (int dy = -MAX_R; dy <= MAX_R; dy++) {\n"
    "        if (dy < -r || dy > r) continue;\n"
    "        for (int dx = -MAX_R; dx <= MAX_R; dx++) {\n"
    "            if (dx < -r || dx > r) continue;\n"
    "            acc += min(sobel_mag(idx, p + ivec2(dx, dy), sz), thr); n += 1.0;\n"
    "        }\n"
    "    }\n"
    "    return acc / max(n, 1.0);\n"
    "}\n"
    "float bilinear(int idx, float fx, float fy, ivec2 sz) {\n"
    "    int x0 = int(floor(fx)); int y0 = int(floor(fy));\n"
    "    float tx = fx - float(x0); float ty = fy - float(y0);\n"
    "    float a = pel_luma(idx, ivec2(x0,     y0),     sz);\n"
    "    float b = pel_luma(idx, ivec2(x0 + 1, y0),     sz);\n"
    "    float c = pel_luma(idx, ivec2(x0,     y0 + 1), sz);\n"
    "    float d = pel_luma(idx, ivec2(x0 + 1, y0 + 1), sz);\n"
    "    return mix(mix(a, b, tx), mix(c, d, tx), ty);\n"
    "}\n"
    "void aa(ivec2 pos, int idx) {\n"
    "    ivec2 sz = imageSize(output_images[idx]);\n"
    "    int r = clamp(blur, 0, MAX_R);\n"
    "    float thr = max(thresh, 0.0001);\n"
    "    float gx = (emask(idx, pos + ivec2(1, 0), sz, r, thr)\n"
    "              - emask(idx, pos + ivec2(-1, 0), sz, r, thr)) * 0.5;\n"
    "    float gy = (emask(idx, pos + ivec2(0, 1), sz, r, thr)\n"
    "              - emask(idx, pos + ivec2(0,-1), sz, r, thr)) * 0.5;\n"
    "    float fx = float(pos.x) + depth * gx;\n"
    "    float fy = float(pos.y) + depth * gy;\n"
    "    float warp = bilinear(idx, fx, fy, sz);\n"
    "    if (darkstr > 0.0) {\n"
    "        float e = sobel_mag(idx, pos, sz);\n"
    "        if (e > edge_thr) {\n"
    "            float lo = warp;\n"
    "            for (int dy = -1; dy <= 1; dy++)\n"
    "                for (int dx = -1; dx <= 1; dx++)\n"
    "                    lo = min(lo, pel_luma(idx, pos + ivec2(dx, dy), sz));\n"
    "            warp = mix(warp, lo, darkstr);\n"
    "        }\n"
    "    }\n"
    "    imageStore(output_images[idx], pos, vec4(clamp(warp, 0.0, 1.0)));\n"
    "}\n";

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    int i;
    PelorusAaVulkanContext *s = ctx->priv;
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
    RET(ff_vk_shader_init(vkctx, shd, "pelorus_aa",
                          VK_SHADER_STAGE_COMPUTE_BIT, NULL, 0, 32, 32, 1, 0));

    GLSLC(0, layout(push_constant, std430) uniform pushConstants {            );
    GLSLC(1,     int   blur;                                                  );
    GLSLC(1,     float depth;                                                 );
    GLSLC(1,     float thresh;                                                );
    GLSLC(1,     float darkstr;                                               );
    GLSLC(1,     float edge_thr;                                              );
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

    GLSLD(aa_glsl);
    GLSLC(0, void main()                                                      );
    GLSLC(0, {                                                                );
    GLSLC(1,     ivec2 size;                                                  );
    GLSLC(1,     const ivec2 pos = ivec2(gl_GlobalInvocationID.xy);           );
    for (i = 0; i < planes; i++) {
        GLSLC(0,                                                              );
        GLSLF(1, size = imageSize(output_images[%i]);                       ,i);
        GLSLC(1, if (!IS_WITHIN(pos, size)) return;                           );
        if (s->planes & (1 << i)) {
            GLSLF(1, aa(pos, %i);                                           ,i);
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
