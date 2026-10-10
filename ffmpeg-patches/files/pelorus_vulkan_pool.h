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

/*
 * Output pools for the Pelorus Vulkan filters that write new frames (issue
 * #103, ADR-0184). `tiling=optimal` (the default) keeps FFmpeg's
 * ff_vk_filter_config_output(): reuse the input frames context when it fits,
 * else allocate OPTIMAL tiling. FFmpeg cannot export OPTIMAL images as
 * DMA-BUF, so `hwmap=derive_device=vaapi` (and QSV through VAAPI) fails on
 * them. `tiling=drm` allocates the pool with
 * VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT and DMA-BUF export instead; the
 * modifier rule is in pelorus_drm_modifier.h. Only Pelorus code changes:
 * libavfilter/vulkan_filter.c and libavutil/hwcontext_vulkan.c stay stock.
 *
 * The pool's usage, flags and Vulkan formats are the ones FFmpeg itself picks
 * for this device and pixel format: a throwaway OPTIMAL frames context is
 * initialised first and read back, because vulkan_frames_init() adds usage
 * bits (TRANSFER_DST, HOST_TRANSFER, VIDEO_ENCODE_SRC) that every listed
 * modifier must support. VK_IMAGE_CREATE_ALIAS_BIT is dropped: ANV rejects it
 * with tiled modifiers and FFmpeg does not depend on it. After init the pool
 * is checked against that read-back, so a different FFmpeg rule fails loudly.
 */

#ifndef AVFILTER_PELORUS_VULKAN_POOL_H
#define AVFILTER_PELORUS_VULKAN_POOL_H

#include <inttypes.h>
#include <string.h>

#include "config.h"

#include "libavutil/buffer.h"
#include "libavutil/frame.h"
#include "libavutil/hwcontext.h"
#include "libavutil/hwcontext_vulkan.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "libavutil/vulkan_loader.h"

#include "filters.h"
#include "pelorus_drm_modifier.h"
#include "vulkan_filter.h"

enum PelVkPoolTiling {
    PEL_VK_POOL_OPTIMAL = 0,
    PEL_VK_POOL_DRM = 1,
};

typedef struct PelVkPoolOpts {
    int tiling;          /* enum PelVkPoolTiling */
    char *drm_modifiers; /* consumer's importable modifiers, '|'-separated */
} PelVkPoolOpts;

/* AVOption rows shared by every filter that writes new frames. */
#define PEL_VK_POOL_OPTIONS(tiling_offset, modifiers_offset, flags)                                 \
    {"tiling",                                                                                      \
     "output pool tiling: optimal (default, FFmpeg's pool) or drm (DRM-format-modifier pool "       \
     "that maps to VAAPI/QSV)",                                                                     \
     (tiling_offset),                                                                               \
     AV_OPT_TYPE_INT,                                                                               \
     {.i64 = PEL_VK_POOL_OPTIMAL},                                                                  \
     PEL_VK_POOL_OPTIMAL,                                                                           \
     PEL_VK_POOL_DRM,                                                                               \
     (flags),                                                                                       \
     .unit = "pel_tiling"},                                                                         \
        {"optimal",                                                                                 \
         "FFmpeg default: reuse the input pool or allocate OPTIMAL tiling",                         \
         0,                                                                                         \
         AV_OPT_TYPE_CONST,                                                                         \
         {.i64 = PEL_VK_POOL_OPTIMAL},                                                              \
         0,                                                                                         \
         0,                                                                                         \
         (flags),                                                                                   \
         .unit = "pel_tiling"},                                                                     \
        {"drm",                                                                                     \
         "DRM format modifier tiling with DMA-BUF export (hwmap to VAAPI, QSV)",                    \
         0,                                                                                         \
         AV_OPT_TYPE_CONST,                                                                         \
         {.i64 = PEL_VK_POOL_DRM},                                                                  \
         0,                                                                                         \
         0,                                                                                         \
         (flags),                                                                                   \
         .unit = "pel_tiling"},                                                                     \
    {                                                                                               \
        "drm_modifiers",                                                                            \
            "with tiling=drm: DRM format modifiers the consumer imports, 0x hex separated by '|'; " \
            "empty (default) = any usable modifier; 0x0 = LINEAR",                                  \
            (modifiers_offset), AV_OPT_TYPE_STRING, {.str = NULL}, 0, 0, (flags)                    \
    }

#define PEL_VK_POOL_MAX_VIEWS 8

typedef struct PelVkPoolProbe {
    VkFormat format[AV_NUM_DATA_POINTERS]; /* per-image formats FFmpeg picked */
    int nb_images;
    VkImageUsageFlags usage;
    VkImageCreateFlags flags;
    VkFormat views[PEL_VK_POOL_MAX_VIEWS]; /* image + plane view formats */
    uint32_t nb_views;
    uint32_t qfs[64]; /* the image queue families hwcontext_vulkan.c uses */
    uint32_t nb_qfs;
    uint32_t format_planes; /* memory planes per image, no auxiliary plane */
} PelVkPoolProbe;

/* Owned by the frames context (user_opaque) because create_pnext is read on
 * every pool allocation, which may outlive the filter. */
typedef struct PelVkPoolChain {
    VkImageDrmFormatModifierListCreateInfoEXT mod_list;
    VkImageFormatListCreateInfo fmt_list;
    uint64_t modifiers[PEL_DRM_MOD_MAX];
    VkFormat views[PEL_VK_POOL_MAX_VIEWS];
} PelVkPoolChain;

static inline void pel_vk_pool_add_view(PelVkPoolProbe *p, VkFormat fmt)
{
    for (uint32_t i = 0; i < p->nb_views; i++)
        if (p->views[i] == fmt)
            return;
    if (fmt != VK_FORMAT_UNDEFINED && p->nb_views < PEL_VK_POOL_MAX_VIEWS)
        p->views[p->nb_views++] = fmt;
}

static inline void pel_vk_pool_probe_fill(PelVkPoolProbe *p, const AVVulkanFramesContext *vkfc,
                                          const AVVulkanDeviceContext *dev, enum AVPixelFormat sw)
{
    const VkFormat *planes = av_vkfmt_from_pixfmt(sw);

    p->nb_images = 0;
    for (int i = 0; i < AV_NUM_DATA_POINTERS && vkfc->format[i] != VK_FORMAT_UNDEFINED; i++) {
        p->format[i] = vkfc->format[i];
        p->nb_images++;
        pel_vk_pool_add_view(p, vkfc->format[i]);
    }
    for (int i = 0; planes && i < AV_NUM_DATA_POINTERS && planes[i] != VK_FORMAT_UNDEFINED; i++)
        pel_vk_pool_add_view(p, planes[i]);
    p->usage = vkfc->usage;
    p->flags = vkfc->img_flags & ~VK_IMAGE_CREATE_ALIAS_BIT;
    p->format_planes = p->nb_images == 1 ? (uint32_t)av_pix_fmt_count_planes(sw) : 1;
    p->nb_qfs = 0;
    for (int i = 0; i < dev->nb_qf && p->nb_qfs < 64; i++) {
        int seen = 0;
        for (uint32_t j = 0; j < p->nb_qfs; j++)
            seen |= p->qfs[j] == (uint32_t)dev->qf[i].idx;
        if (!seen)
            p->qfs[p->nb_qfs++] = dev->qf[i].idx;
    }
}

/* Read back what vulkan_frames_init() picks for an OPTIMAL pool. */
static inline int pel_vk_pool_probe(AVFilterContext *avctx, AVBufferRef *device_ref, int w, int h,
                                    enum AVPixelFormat sw, PelVkPoolProbe *p)
{
    AVBufferRef *ref = av_hwframe_ctx_alloc(device_ref);
    AVHWFramesContext *frames;
    AVVulkanFramesContext *vkfc;
    int err;

    if (!ref)
        return AVERROR(ENOMEM);
    frames = (AVHWFramesContext *)ref->data;
    frames->format = AV_PIX_FMT_VULKAN;
    frames->sw_format = sw;
    frames->width = w;
    frames->height = h;
    vkfc = frames->hwctx;
    vkfc->tiling = VK_IMAGE_TILING_OPTIMAL;
    vkfc->usage =
        VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
    err = av_hwframe_ctx_init(ref);
    if (err < 0) {
        av_log(avctx, AV_LOG_ERROR, "tiling=drm: probing the %s pool failed\n",
               av_get_pix_fmt_name(sw));
        av_buffer_unref(&ref);
        return err;
    }
    pel_vk_pool_probe_fill(p, vkfc, frames->device_ctx->hwctx, sw);
    av_buffer_unref(&ref);
    return 0;
}

/* Fill props with the driver's modifiers for fmt; returns their count. The
 * v1 list (VkFormatFeatureFlags, 32 bits) is enough: the only feature tested
 * is VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT. The 64-bit List2 would be needed for
 * the *_WITHOUT_FORMAT storage features, which the filters do not rely on
 * (their storage views always name a format). */
static inline uint32_t pel_vk_pool_mod_list(FFVulkanContext *s, VkFormat fmt,
                                            VkDrmFormatModifierPropertiesEXT *props)
{
    VkDrmFormatModifierPropertiesListEXT list = {
        .sType = VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT,
        .drmFormatModifierCount = PEL_DRM_MOD_MAX,
        .pDrmFormatModifierProperties = props,
    };
    VkFormatProperties2 fp = {
        .sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2,
        .pNext = &list,
    };

    s->vkfn.GetPhysicalDeviceFormatProperties2(s->hwctx->phys_dev, fmt, &fp);
    return FFMIN(list.drmFormatModifierCount, PEL_DRM_MOD_MAX);
}

/* The modifier is listed for fmt with storage-image support. */
static inline int pel_vk_pool_mod_storage(FFVulkanContext *s, VkFormat fmt, uint64_t mod)
{
    VkDrmFormatModifierPropertiesEXT props[PEL_DRM_MOD_MAX];
    const uint32_t n = pel_vk_pool_mod_list(s, fmt, props);

    for (uint32_t i = 0; i < n; i++)
        if (props[i].drmFormatModifier == mod)
            return !!(props[i].drmFormatModifierTilingFeatures &
                      VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT);
    return 0;
}

/* The exact pool image (usage, flags, view formats, DMA-BUF export, size) is
 * supported with this modifier. */
static inline int pel_vk_pool_mod_image(FFVulkanContext *s, const PelVkPoolProbe *p, VkFormat fmt,
                                        uint64_t mod, int w, int h)
{
    VkImageFormatListCreateInfo fmt_list = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO,
        .viewFormatCount = p->nb_views,
        .pViewFormats = p->views,
    };
    VkPhysicalDeviceImageDrmFormatModifierInfoEXT mod_info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT,
        .pNext = &fmt_list,
        .drmFormatModifier = mod,
        .sharingMode = p->nb_qfs > 1 ? VK_SHARING_MODE_CONCURRENT : VK_SHARING_MODE_EXCLUSIVE,
        .queueFamilyIndexCount = p->nb_qfs,
        .pQueueFamilyIndices = p->qfs,
    };
    VkPhysicalDeviceExternalImageFormatInfo ext_info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
        .pNext = &mod_info,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
    };
    VkPhysicalDeviceImageFormatInfo2 info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .pNext = &ext_info,
        .format = fmt,
        .type = VK_IMAGE_TYPE_2D,
        .tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT,
        .usage = p->usage,
        .flags = p->flags,
    };
    VkExternalImageFormatProperties ext_props = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES,
    };
    VkImageFormatProperties2 props = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
        .pNext = &ext_props,
    };

    if (s->vkfn.GetPhysicalDeviceImageFormatProperties2(s->hwctx->phys_dev, &info, &props) !=
        VK_SUCCESS)
        return 0;
    if (!(ext_props.externalMemoryProperties.externalMemoryFeatures &
          VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT))
        return 0;
    return props.imageFormatProperties.maxExtent.width >= (uint32_t)w &&
           props.imageFormatProperties.maxExtent.height >= (uint32_t)h;
}

static inline int pel_vk_pool_mod_usable(FFVulkanContext *s, const PelVkPoolProbe *p,
                                         enum AVPixelFormat sw, uint64_t mod, int w, int h)
{
    const VkFormat *planes = av_vkfmt_from_pixfmt(sw);

    for (int i = 0; i < p->nb_images; i++)
        if (!pel_vk_pool_mod_image(s, p, p->format[i], mod, w, h))
            return 0;
    for (int i = 0; planes && i < AV_NUM_DATA_POINTERS && planes[i] != VK_FORMAT_UNDEFINED; i++)
        if (!pel_vk_pool_mod_storage(s, planes[i], mod))
            return 0;
    return 1;
}

static inline int pel_vk_pool_candidates(AVFilterContext *avctx, FFVulkanContext *s,
                                         const PelVkPoolProbe *p, PelDrmModCandidate *cand)
{
    VkDrmFormatModifierPropertiesEXT props[PEL_DRM_MOD_MAX];
    const uint32_t n = pel_vk_pool_mod_list(s, p->format[0], props);
    char name[PEL_DRM_MOD_NAME_SIZE];

    for (uint32_t i = 0; i < n; i++) {
        cand[i].modifier = props[i].drmFormatModifier;
        cand[i].mem_planes = props[i].drmFormatModifierPlaneCount;
        cand[i].usable = pel_vk_pool_mod_usable(s, p, s->output_format, cand[i].modifier,
                                                s->output_width, s->output_height);
        av_log(avctx, AV_LOG_VERBOSE,
               "tiling=drm: modifier 0x%016" PRIx64 " (%s): %" PRIu32
               " memory plane(s), storage and export %s\n",
               cand[i].modifier, pel_drm_mod_name(cand[i].modifier, name, sizeof(name)),
               cand[i].mem_planes, cand[i].usable ? "supported" : "not supported");
    }
    return (int)n;
}

static inline void pel_vk_pool_free_chain(AVHWFramesContext *frames)
{
    av_freep(&frames->user_opaque);
}

static inline PelVkPoolChain *pel_vk_pool_chain(const PelVkPoolProbe *p, const PelDrmModChoice *c)
{
    PelVkPoolChain *chain = av_mallocz(sizeof(*chain));

    if (!chain)
        return NULL;
    memcpy(chain->modifiers, c->modifiers, sizeof(c->modifiers[0]) * c->nb_modifiers);
    memcpy(chain->views, p->views, sizeof(p->views[0]) * p->nb_views);
    chain->fmt_list = (VkImageFormatListCreateInfo){
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_LIST_CREATE_INFO,
        .viewFormatCount = p->nb_views,
        .pViewFormats = chain->views,
    };
    chain->mod_list = (VkImageDrmFormatModifierListCreateInfoEXT){
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_LIST_CREATE_INFO_EXT,
        .pNext = &chain->fmt_list,
        .drmFormatModifierCount = (uint32_t)c->nb_modifiers,
        .pDrmFormatModifiers = chain->modifiers,
    };
    return chain;
}

/* The initialised pool matches the probe the modifiers were checked with. */
static inline int pel_vk_pool_matches(const AVVulkanFramesContext *vkfc, const PelVkPoolProbe *p)
{
    if (vkfc->tiling != VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT || vkfc->usage != p->usage ||
        vkfc->img_flags != p->flags)
        return 0;
    for (int i = 0; i < p->nb_images; i++)
        if (vkfc->format[i] != p->format[i])
            return 0;
    return p->nb_images == AV_NUM_DATA_POINTERS ||
           vkfc->format[p->nb_images] == VK_FORMAT_UNDEFINED;
}

static inline int pel_vk_pool_alloc(AVFilterContext *avctx, AVBufferRef *device_ref,
                                    const PelVkPoolProbe *p, const PelDrmModChoice *c,
                                    AVBufferRef **out)
{
    FFVulkanContext *s = avctx->priv;
    PelVkPoolChain *chain = pel_vk_pool_chain(p, c);
    AVBufferRef *ref = chain ? av_hwframe_ctx_alloc(device_ref) : NULL;
    AVHWFramesContext *frames;
    AVVulkanFramesContext *vkfc;
    int err;

    if (!ref) {
        av_free(chain);
        return AVERROR(ENOMEM);
    }
    frames = (AVHWFramesContext *)ref->data;
    frames->format = AV_PIX_FMT_VULKAN;
    frames->sw_format = s->output_format;
    frames->width = s->output_width;
    frames->height = s->output_height;
    frames->user_opaque = chain;
    frames->free = pel_vk_pool_free_chain;
    vkfc = frames->hwctx;
    vkfc->tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
    vkfc->usage = p->usage;
    vkfc->img_flags = p->flags;
    vkfc->create_pnext = &chain->mod_list;
    err = av_hwframe_ctx_init(ref);
    if (err >= 0 && !pel_vk_pool_matches(vkfc, p)) {
        av_log(avctx, AV_LOG_ERROR,
               "tiling=drm: FFmpeg initialised the pool with other usage, flags or formats "
               "than the modifiers were checked for\n");
        err = AVERROR(EINVAL);
    }
    if (err < 0) {
        av_buffer_unref(&ref);
        return err;
    }
    *out = ref;
    return 0;
}

static inline int pel_vk_pool_load(AVFilterContext *avctx, FFVulkanContext *s,
                                   AVBufferRef *device_ref)
{
    const FFVulkanExtensions need = FF_VK_EXT_DRM_MODIFIER_FLAGS |
                                    FF_VK_EXT_EXTERNAL_DMABUF_MEMORY | FF_VK_EXT_EXTERNAL_FD_MEMORY;
    AVHWDeviceContext *dev = (AVHWDeviceContext *)device_ref->data;
    AVVulkanDeviceContext *hw = dev->hwctx;
    int err;

    s->extensions =
        ff_vk_extensions_to_mask(hw->enabled_dev_extensions, hw->nb_enabled_dev_extensions);
    s->extensions |=
        ff_vk_extensions_to_mask(hw->enabled_inst_extensions, hw->nb_enabled_inst_extensions);
    if ((s->extensions & need) != need) {
        av_log(avctx, AV_LOG_ERROR,
               "tiling=drm needs VK_EXT_image_drm_format_modifier, "
               "VK_EXT_external_memory_dma_buf and VK_KHR_external_memory_fd on the device; "
               "refusing to allocate OPTIMAL tiling instead\n");
        return AVERROR(ENOSYS);
    }
    err = ff_vk_load_functions(dev, &s->vkfn, s->extensions, 1, 1);
    if (err < 0)
        return err;
    s->device = dev;
    s->hwctx = hw;
    return 0;
}

static inline int pel_vk_pool_choose(AVFilterContext *avctx, FFVulkanContext *s,
                                     const PelVkPoolOpts *opts, const PelVkPoolProbe *p,
                                     PelDrmModChoice *choice)
{
    PelDrmModCandidate cand[PEL_DRM_MOD_MAX];
    uint64_t consumer[PEL_DRM_MOD_MAX];
    const char *bad = NULL;
    const int nb_consumer =
        pel_drm_mod_parse_list(opts->drm_modifiers, consumer, PEL_DRM_MOD_MAX, &bad);
    const char *fmt = av_get_pix_fmt_name(s->output_format);
    char name[PEL_DRM_MOD_NAME_SIZE];
    int nb_cand;

    if (nb_consumer < 0) {
        av_log(avctx, AV_LOG_ERROR,
               "drm_modifiers: cannot parse \"%.*s\" in \"%s\": want at most %d modifiers, "
               "0x hex or decimal, separated by '|', not DRM_FORMAT_MOD_INVALID\n",
               bad ? (int)strcspn(bad, "| ") : 0, bad ? bad : "", opts->drm_modifiers,
               PEL_DRM_MOD_MAX);
        return AVERROR(EINVAL);
    }
    nb_cand = pel_vk_pool_candidates(avctx, s, p, cand);
    if (pel_drm_mod_choose(cand, nb_cand, p->format_planes, consumer, nb_consumer, choice) < 0) {
        av_log(avctx, AV_LOG_ERROR,
               "tiling=drm: no DRM format modifier, DRM_FORMAT_MOD_LINEAR included, supports "
               "storage images and DMA-BUF export for %s on this device; refusing to "
               "allocate OPTIMAL tiling instead\n",
               fmt);
        return AVERROR(ENOSYS);
    }
    if (choice->linear_fallback)
        av_log(avctx, AV_LOG_WARNING,
               "tiling=drm: %s supports storage images and DMA-BUF export for %s; "
               "substituting %s (0x%016" PRIx64 ")\n",
               nb_consumer > 0 ? "no modifier in drm_modifiers" :
                                 "no tiled DRM format modifier without auxiliary planes",
               fmt, pel_drm_mod_name(PEL_DRM_MOD_LINEAR, name, sizeof(name)),
               (uint64_t)PEL_DRM_MOD_LINEAR);
    return 0;
}

/* Name the modifier the driver chose from the list, from one pool image. */
static inline int pel_vk_pool_report(AVFilterContext *avctx, FFVulkanContext *s,
                                     const PelDrmModChoice *choice)
{
    VkImageDrmFormatModifierPropertiesEXT props = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_DRM_FORMAT_MODIFIER_PROPERTIES_EXT,
    };
    AVFrame *frame = av_frame_alloc();
    char name[PEL_DRM_MOD_NAME_SIZE];
    int err;

    if (!frame)
        return AVERROR(ENOMEM);
    err = av_hwframe_get_buffer(s->frames_ref, frame, 0);
    if (err >= 0 &&
        s->vkfn.GetImageDrmFormatModifierPropertiesEXT(
            s->hwctx->act_dev, ((AVVkFrame *)frame->data[0])->img[0], &props) != VK_SUCCESS)
        err = AVERROR_EXTERNAL;
    av_frame_free(&frame);
    if (err < 0) {
        av_log(avctx, AV_LOG_ERROR, "tiling=drm: cannot read the pool's DRM format modifier\n");
        return err;
    }
    av_log(avctx, AV_LOG_VERBOSE,
           "tiling=drm: %s %dx%d pool uses DRM format modifier 0x%016" PRIx64
           " (%s), chosen by the driver from %d\n",
           av_get_pix_fmt_name(s->output_format), s->output_width, s->output_height,
           (uint64_t)props.drmFormatModifier,
           pel_drm_mod_name(props.drmFormatModifier, name, sizeof(name)), choice->nb_modifiers);
    return 0;
}

static inline int pel_vk_pool_drm(AVFilterContext *avctx, FFVulkanContext *s,
                                  const PelVkPoolOpts *opts, PelDrmModChoice *choice)
{
    AVBufferRef *device_ref;
    AVBufferRef *pool = NULL;
    PelVkPoolProbe probe = {0};
    int err;

    if (!s->input_frames_ref) {
        av_log(avctx, AV_LOG_ERROR,
               "tiling=drm: no input frames context to take the device from\n");
        return AVERROR(EINVAL);
    }
    device_ref = ((AVHWFramesContext *)s->input_frames_ref->data)->device_ref;
    err = pel_vk_pool_load(avctx, s, device_ref);
    if (err >= 0)
        err = pel_vk_pool_probe(avctx, device_ref, s->output_width, s->output_height,
                                s->output_format, &probe);
    if (err >= 0)
        err = pel_vk_pool_choose(avctx, s, opts, &probe, choice);
    if (err >= 0)
        err = pel_vk_pool_alloc(avctx, device_ref, &probe, choice, &pool);
    if (err < 0)
        return err;
    err = ff_vk_filter_init_context(avctx, s, pool, s->output_width, s->output_height,
                                    s->output_format);
    if (err >= 0 && (!s->frames_ref || s->frames_ref->data != pool->data)) {
        av_log(avctx, AV_LOG_ERROR,
               "tiling=drm: the Vulkan filter context did not take the "
               "DRM-modifier pool; refusing OPTIMAL tiling\n");
        err = AVERROR(EINVAL);
    }
    av_buffer_unref(&pool);
    return err;
}

/* Known limits of a working DRM-modifier pool, named instead of silent. */
static inline void pel_vk_pool_warn_limits(AVFilterContext *avctx, const FFVulkanContext *s)
{
    const VkDriverId driver = s->driver_props.driverID;

#if !CONFIG_LIBDRM
    av_log(avctx, AV_LOG_WARNING,
           "tiling=drm: this FFmpeg is built without libdrm, so hwmap cannot map the pool to "
           "DRM PRIME or VAAPI\n");
#endif
    /* research 0184: on RADV with radeonsi the sync_file attached by
     * vulkan_map_to_drm() did not hold VAAPI reads back in 14-17 of 20 frames. */
    if (driver == VK_DRIVER_ID_MESA_RADV || driver == VK_DRIVER_ID_AMD_OPEN_SOURCE ||
        driver == VK_DRIVER_ID_AMD_PROPRIETARY)
        av_log(avctx, AV_LOG_WARNING,
               "tiling=drm on an AMD Vulkan driver (%s): a frame mapped to VAAPI can be read "
               "before the filter finished writing it (silently wrong pixels) until FFmpeg's "
               "map synchronisation is fixed; see docs/backends/vulkan-drm-modifiers.md\n",
               s->driver_props.driverName);
}

/* config_props of the output pad of every Pelorus filter that writes new
 * frames. */
static inline int pel_vk_pool_config_output(AVFilterLink *outlink, const PelVkPoolOpts *opts)
{
    FilterLink *l = ff_filter_link(outlink);
    AVFilterContext *avctx = outlink->src;
    FFVulkanContext *s = avctx->priv;
    PelDrmModChoice choice;
    int err;

    if (opts->tiling != PEL_VK_POOL_DRM) {
        if (opts->drm_modifiers && *opts->drm_modifiers)
            av_log(avctx, AV_LOG_WARNING, "drm_modifiers is ignored without tiling=drm\n");
        return ff_vk_filter_config_output(outlink);
    }
    av_buffer_unref(&l->hw_frames_ctx);
    err = pel_vk_pool_drm(avctx, s, opts, &choice);
    if (err >= 0)
        err = pel_vk_pool_report(avctx, s, &choice);
    if (err < 0)
        return err;
    pel_vk_pool_warn_limits(avctx, s);
    l->hw_frames_ctx = av_buffer_ref(s->frames_ref);
    if (!l->hw_frames_ctx)
        return AVERROR(ENOMEM);
    outlink->w = s->output_width;
    outlink->h = s->output_height;
    return 0;
}

#endif /* AVFILTER_PELORUS_VULKAN_POOL_H */
