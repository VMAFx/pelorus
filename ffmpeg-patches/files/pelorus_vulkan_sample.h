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

#ifndef AVFILTER_PELORUS_VULKAN_SAMPLE_H
#define AVFILTER_PELORUS_VULKAN_SAMPLE_H

#include <stdint.h>

#include "libavutil/pixdesc.h"

typedef struct PelVkSampleDomain {
    float scale;
    uint32_t code_max;
} PelVkSampleDomain;

/* FF_VK_REP_FLOAT storage images normalize integer samples against their
 * Vulkan view's storage container.  That differs from the logical sample
 * range for LSB-aligned sub-16-bit formats.  Return the multiplier that maps a
 * storage-image load into the format's logical [0, 1] sample domain.
 *
 * This helper deliberately accepts only the layouts the filter family can
 * prove from AVPixFmtDescriptor alone: planar or single-component integer
 * formats whose first component occupies an 8- or 16-bit storage lane.  The
 * Vulkan views for supported semi-planar formats have the same depth/shift in
 * both chroma components, so the first component describes every lane.  Packed,
 * float, bitstream, palette, hardware, Bayer, malformed, or wider layouts fall
 * back to 1.0 rather than inventing a representation rule. */
static inline PelVkSampleDomain pel_vk_sample_domain_from_desc(const AVPixFmtDescriptor *desc)
{
    const AVComponentDescriptor *comp;
    PelVkSampleDomain domain = {1.0f, 0u};
    unsigned int container_bits;
    unsigned int code_max;
    unsigned int storage_max;
    unsigned int sample_max;

    if (!desc || desc->nb_components < 1 ||
        (desc->flags & (AV_PIX_FMT_FLAG_FLOAT | AV_PIX_FMT_FLAG_BITSTREAM | AV_PIX_FMT_FLAG_PAL |
                        AV_PIX_FMT_FLAG_HWACCEL | AV_PIX_FMT_FLAG_BAYER)) ||
        (!(desc->flags & AV_PIX_FMT_FLAG_PLANAR) && desc->nb_components != 1))
        return domain;

    comp = &desc->comp[0];
    if (comp->step == 1)
        container_bits = 8;
    else if (comp->step == 2)
        container_bits = 16;
    else
        return domain;

    if (comp->depth < 1 || comp->shift < 0 || (unsigned int)comp->depth > container_bits ||
        (unsigned int)comp->shift >= container_bits ||
        (unsigned int)comp->depth + (unsigned int)comp->shift > container_bits)
        return domain;

    storage_max = (1u << container_bits) - 1u;
    code_max = (1u << (unsigned int)comp->depth) - 1u;
    sample_max = code_max << (unsigned int)comp->shift;
    if (sample_max == 0u)
        return domain;

    domain.scale = (float)storage_max / (float)sample_max;
    domain.code_max = code_max;
    return domain;
}

static inline float pel_vk_sample_scale_from_desc(const AVPixFmtDescriptor *desc)
{
    return pel_vk_sample_domain_from_desc(desc).scale;
}

static inline uint32_t pel_vk_sample_code_max_from_desc(const AVPixFmtDescriptor *desc)
{
    return pel_vk_sample_domain_from_desc(desc).code_max;
}

static inline float pel_vk_sample_scale(enum AVPixelFormat format)
{
    return pel_vk_sample_scale_from_desc(av_pix_fmt_desc_get(format));
}

static inline uint32_t pel_vk_sample_code_max(enum AVPixelFormat format)
{
    return pel_vk_sample_code_max_from_desc(av_pix_fmt_desc_get(format));
}

#endif /* AVFILTER_PELORUS_VULKAN_SAMPLE_H */
