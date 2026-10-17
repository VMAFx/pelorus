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

/* Input checks shared by the Pelorus Vulkan filters (#188): input the filter
 * cannot process correctly is refused with a message that names the reason,
 * never processed into wrong output. */

#ifndef AVFILTER_PELORUS_VULKAN_INPUT_H
#define AVFILTER_PELORUS_VULKAN_INPUT_H

#include "libavutil/bprint.h"
#include "libavutil/error.h"
#include "libavutil/frame.h"
#include "libavutil/hwcontext.h"
#include "libavutil/log.h"
#include "libavutil/pixdesc.h"

#include "avfilter.h"
#include "filters.h"
#include "vulkan_filter.h"

/* Spatial filters read neighbouring rows, which in an interlaced frame belong
 * to the other field, so their output would mix two instants. */
static inline int pel_vk_refuse_interlaced(AVFilterContext *ctx, const AVFrame *in)
{
    if (!(in->flags & AV_FRAME_FLAG_INTERLACED))
        return 0;
    av_log(ctx, AV_LOG_ERROR,
           "%s: interlaced input (AV_FRAME_FLAG_INTERLACED) is not supported; "
           "deinterlace first, or mark progressive frames with setfield=prog\n",
           ctx->filter->name);
    return AVERROR(EINVAL);
}

/* Software layouts behind AV_PIX_FMT_VULKAN that every Pelorus Vulkan filter
 * is checked on (vulkan-format-matrix.sh): planar and semi-planar YUV at
 * 4:2:0, 4:2:2 and 4:4:4, 8, 10, 12 and 16 bits, plus yuva420p, whose alpha
 * plane passes through unless the planes mask selects it.  The Vulkan
 * hwcontext offers no 9- or 14-bit YUV layout, so none is listed. */
#define PEL_VK_YUV_FORMATS                                                              \
    AV_PIX_FMT_YUV420P, AV_PIX_FMT_YUV420P10, AV_PIX_FMT_YUV420P12, AV_PIX_FMT_YUV420P16, \
    AV_PIX_FMT_YUV422P, AV_PIX_FMT_YUV422P10, AV_PIX_FMT_YUV422P12, AV_PIX_FMT_YUV422P16, \
    AV_PIX_FMT_YUV444P, AV_PIX_FMT_YUV444P10, AV_PIX_FMT_YUV444P12, AV_PIX_FMT_YUV444P16, \
    AV_PIX_FMT_NV12, AV_PIX_FMT_P010, AV_PIX_FMT_P012, AV_PIX_FMT_P016,                   \
    AV_PIX_FMT_NV16, AV_PIX_FMT_P210, AV_PIX_FMT_P212, AV_PIX_FMT_P216,                   \
    AV_PIX_FMT_NV24, AV_PIX_FMT_P410, AV_PIX_FMT_P412, AV_PIX_FMT_P416,                   \
    AV_PIX_FMT_YUVA420P

/* Refuse a software layout outside `allowed` (terminated by AV_PIX_FMT_NONE)
 * by name.  The sample-domain helper maps an unknown layout to scale 1.0, so
 * processing it would give wrong output without any error. */
static inline int pel_vk_check_input_format(AVFilterLink *inlink, const enum AVPixelFormat *allowed)
{
    AVFilterContext *ctx = inlink->dst;
    FilterLink *l = ff_filter_link(inlink);
    const AVHWFramesContext *frames;
    enum AVPixelFormat sw;
    const char *name;
    AVBPrint list;
    int i;

    if (!l->hw_frames_ctx)
        return 0; /* ff_vk_filter_config_input reports the missing context */
    frames = (const AVHWFramesContext *)l->hw_frames_ctx->data;
    sw = frames->sw_format;
    for (i = 0; allowed[i] != AV_PIX_FMT_NONE; i++)
        if (allowed[i] == sw)
            return 0;

    av_bprint_init(&list, 0, AV_BPRINT_SIZE_AUTOMATIC);
    for (i = 0; allowed[i] != AV_PIX_FMT_NONE; i++)
        av_bprintf(&list, "%s%s", i ? " " : "", av_get_pix_fmt_name(allowed[i]));
    name = av_get_pix_fmt_name(sw);
    av_log(ctx, AV_LOG_ERROR, "%s: input format %s is not supported; supported: %s\n",
           ctx->filter->name, name ? name : "unknown", list.str);
    av_bprint_finalize(&list, NULL);
    return AVERROR(EINVAL);
}

/* config_props for filters checked on YUV layouts only. */
static inline int pel_vk_config_input_yuv(AVFilterLink *inlink)
{
    static const enum AVPixelFormat allowed[] = {PEL_VK_YUV_FORMATS, AV_PIX_FMT_NONE};
    int err = pel_vk_check_input_format(inlink, allowed);

    return err < 0 ? err : ff_vk_filter_config_input(inlink);
}

/* config_props for scalar filters that also keep the G/B/A lanes of packed
 * 8-bit RGBA intact (the matrix RGBA lane-preservation rows). */
static inline int pel_vk_config_input_yuv_rgba(AVFilterLink *inlink)
{
    static const enum AVPixelFormat allowed[] = {PEL_VK_YUV_FORMATS, AV_PIX_FMT_RGBA,
                                                 AV_PIX_FMT_NONE};
    int err = pel_vk_check_input_format(inlink, allowed);

    return err < 0 ? err : ff_vk_filter_config_input(inlink);
}

#endif /* AVFILTER_PELORUS_VULKAN_INPUT_H */
