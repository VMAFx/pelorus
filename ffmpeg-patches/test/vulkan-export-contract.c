/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Export contract of the Pelorus Vulkan output pools (issue #104,
 * docs/api/vulkan-export-contract.md). A consumer that imports a Pelorus
 * frame into another API reads the contract from AVVulkanFramesContext and
 * AVVulkanDeviceContext; no Pelorus option or side data carries it. For each
 * pool this test asserts:
 *   tiling     AVVulkanFramesContext.tiling and AVVkFrame.tiling are OPTIMAL
 *              for tiling=optimal and DRM_FORMAT_MODIFIER_EXT for tiling=drm
 *   planes     format[] lists one multi-plane image, or one image per plane
 *              on a device opened with disable_multiplane=1 (that option
 *              leaves AV_VK_FRAME_FLAG_DISABLE_MULTIPLANE clear)
 *   extension  the device enabled VK_KHR_external_memory_fd (and
 *              VK_EXT_external_memory_dma_buf for a DRM pool)
 *   export     the export info matches the expectation; only an exportable
 *              pool is exported, and vkGetMemoryFdKHR then returns an fd for
 *              every image's memory with the handle type stock FFmpeg derives
 *              from tiling: OPAQUE_FD for OPTIMAL, DMA_BUF for
 *              DRM_FORMAT_MODIFIER_EXT
 * Export info of an OPTIMAL pool: the image format query stock n9.0.2 makes
 * before it allocates (try_export_flags(), hwcontext_vulkan.c:2766: format[0],
 * tiling, usage, ALIAS_BIT only, OPAQUE_FD). A multi-plane storage image fails
 * that query, so the memory carries no export handle type and calling
 * vkGetMemoryFdKHR on it is invalid usage. A Pelorus DRM pool lists only
 * modifiers whose DMA_BUF export the driver accepts with the real flags
 * (pel_vk_pool_mod_image(), pelorus_vulkan_pool.h), so it needs no query.
 * Cases: optimal-nv12 (positive: no export handle, never exported), drm-nv12
 * (positive: exported), split-641x361 (boundary: odd size, one image per plane,
 * exported), wrong-tiling, no-fd-ext and export-expected (planted: the checker
 * must refuse a pool whose tiling is not the expected one, a device list
 * without VK_KHR_external_memory_fd, and an optimal multi-plane pool expected
 * to be exportable).
 *
 * Env: VULKAN_DEVICE  device index or name for av_hwdevice_ctx_create
 *                     (default: FFmpeg's first device)
 * Exit: 0 pass, 1 fail, 77 skip (reason on a "SKIP: " line).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <libavfilter/avfilter.h>
#include <libavfilter/buffersink.h>
#include <libavutil/frame.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vulkan.h>
#include <libavutil/pixdesc.h>

#define FD_EXT "VK_KHR_external_memory_fd"
#define DMABUF_EXT "VK_EXT_external_memory_dma_buf"
#define MAX_FILTERS 16

typedef struct Expect {
    VkImageTiling tiling;
    int split;      /* one image per plane */
    int exportable; /* export info offers the handle type */
} Expect;

typedef struct Pool {
    const AVVulkanFramesContext *vkfc;
    const AVVulkanDeviceContext *dev;
    const AVVkFrame *frame;
    enum AVPixelFormat sw_format;
} Pool;

static int has_ext(const char *const *exts, int nb, const char *name)
{
    for (int i = 0; i < nb && i < 1024; i++) {
        if (strcmp(exts[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

static int nb_images(const AVVulkanFramesContext *vkfc)
{
    int n = 0;
    while (n < AV_NUM_DATA_POINTERS && vkfc->format[n] != VK_FORMAT_UNDEFINED) {
        n++;
    }
    return n;
}

static int check_planes(const char *name, const Pool *p, const Expect *e)
{
    /* format[] is the image count; the device option disable_multiplane=1
     * splits the planes without setting AV_VK_FRAME_FLAG_DISABLE_MULTIPLANE. */
    const int n = nb_images(p->vkfc);
    const int want = e->split ? av_pix_fmt_count_planes(p->sw_format) : 1;

    if (n != want) {
        (void)fprintf(stderr, "%s: %d image(s), expected %d\n", name, n, want);
        return 1;
    }
    return 0;
}

static int check_tiling(const char *name, const Pool *p, const Expect *e)
{
    if (p->vkfc->tiling != e->tiling || p->frame->tiling != e->tiling) {
        (void)fprintf(stderr, "%s: tiling %d (frame %d), expected %d\n", name, (int)p->vkfc->tiling,
                      (int)p->frame->tiling, (int)e->tiling);
        return 1;
    }
    return 0;
}

static int check_exts(const char *name, const char *const *exts, int nb, VkImageTiling tiling)
{
    if (!has_ext(exts, nb, FD_EXT)) {
        (void)fprintf(stderr, "%s: device did not enable %s\n", name, FD_EXT);
        return 1;
    }
    if (tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT && !has_ext(exts, nb, DMABUF_EXT)) {
        (void)fprintf(stderr, "%s: device did not enable %s\n", name, DMABUF_EXT);
        return 1;
    }
    return 0;
}

/* 1 when the export info of p offers its handle type; see the header comment. */
static int export_info(const Pool *p)
{
    const PFN_vkGetPhysicalDeviceImageFormatProperties2 query =
        (PFN_vkGetPhysicalDeviceImageFormatProperties2)p->dev->get_proc_addr(
            p->dev->inst, "vkGetPhysicalDeviceImageFormatProperties2");
    VkExternalImageFormatProperties eprops = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES,
    };
    VkImageFormatProperties2 props = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2,
        .pNext = &eprops,
    };
    const VkPhysicalDeviceExternalImageFormatInfo ext = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT,
    };
    const VkPhysicalDeviceImageFormatInfo2 info = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
        .pNext = &ext,
        .format = p->vkfc->format[0],
        .type = VK_IMAGE_TYPE_2D,
        .tiling = p->vkfc->tiling,
        .usage = p->vkfc->usage,
        .flags = VK_IMAGE_CREATE_ALIAS_BIT,
    };

    if (p->vkfc->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT) {
        return 1;
    }
    if (!query || query(p->dev->phys_dev, &info, &props) != VK_SUCCESS) {
        return 0;
    }
    return (eprops.externalMemoryProperties.externalMemoryFeatures &
            VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT) != 0;
}

static int export_fds(const char *name, const Pool *p)
{
    PFN_vkGetDeviceProcAddr gdpa =
        (PFN_vkGetDeviceProcAddr)p->dev->get_proc_addr(p->dev->inst, "vkGetDeviceProcAddr");
    PFN_vkGetMemoryFdKHR get_fd =
        gdpa ? (PFN_vkGetMemoryFdKHR)gdpa(p->dev->act_dev, "vkGetMemoryFdKHR") : NULL;
    const VkExternalMemoryHandleTypeFlagBits type =
        p->vkfc->tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT ?
            VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT :
            VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
    const int n = nb_images(p->vkfc);

    if (!get_fd) {
        (void)fprintf(stderr, "%s: vkGetMemoryFdKHR not loadable\n", name);
        return 1;
    }
    for (int i = 0; i < n; i++) {
        VkMemoryGetFdInfoKHR info = {
            .sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR,
            .memory = p->frame->mem[i],
            .handleType = type,
        };
        int fd = -1;
        const VkResult ret = get_fd(p->dev->act_dev, &info, &fd);
        if (ret != VK_SUCCESS || fd < 0) {
            (void)fprintf(stderr, "%s: image %d fd export (handle type 0x%x) failed: %d\n", name, i,
                          (unsigned)type, (int)ret);
            return 1;
        }
        (void)close(fd);
    }
    return 0;
}

static int check_export(const char *name, const Pool *p, const Expect *e)
{
    const int info = export_info(p);

    if (info != e->exportable) {
        (void)fprintf(stderr, "%s: export info %d, expected %d\n", name, info, e->exportable);
        return 1;
    }
    return info ? export_fds(name, p) : 0;
}

/* The contract checker; exts is passed apart so a planted list can drop one. */
static int check_pool(const char *name, const Pool *p, const Expect *e, const char *const *exts,
                      int nb_exts)
{
    int bad = check_tiling(name, p, e);
    bad += check_planes(name, p, e);
    bad += check_exts(name, exts, nb_exts, e->tiling);
    if (!bad) {
        bad += check_export(name, p, e);
    }
    return bad;
}

static int attach_device(AVFilterGraph *g, AVBufferRef *dev)
{
    for (unsigned i = 0; i < g->nb_filters && i < MAX_FILTERS; i++) {
        g->filters[i]->hw_device_ctx = av_buffer_ref(dev);
        if (!g->filters[i]->hw_device_ctx) {
            return AVERROR(ENOMEM);
        }
    }
    return 0;
}

static AVFilterContext *find_sink(const AVFilterGraph *g)
{
    for (unsigned i = 0; i < g->nb_filters && i < MAX_FILTERS; i++) {
        if (strcmp(g->filters[i]->filter->name, "buffersink") == 0) {
            return g->filters[i];
        }
    }
    return NULL;
}

/* Builds desc in g with dev attached before init: hwupload reads the
 * device in its init callback, which avfilter_graph_parse_ptr() runs. */
static int build_graph(AVFilterGraph *g, AVBufferRef *dev, const char *desc)
{
    AVFilterGraphSegment *seg = NULL;
    AVFilterInOut *ins = NULL;
    AVFilterInOut *outs = NULL;
    int ret = avfilter_graph_segment_parse(g, desc, 0, &seg);

    if (ret >= 0) {
        ret = avfilter_graph_segment_create_filters(seg, 0);
    }
    if (ret >= 0) {
        ret = attach_device(g, dev);
    }
    if (ret >= 0) {
        ret = avfilter_graph_segment_apply_opts(seg, 0);
    }
    if (ret >= 0) {
        ret = avfilter_graph_segment_init(seg, 0);
    }
    if (ret >= 0) {
        ret = avfilter_graph_segment_link(seg, 0, &ins, &outs);
    }
    avfilter_inout_free(&ins);
    avfilter_inout_free(&outs);
    avfilter_graph_segment_free(&seg);
    return ret;
}

/* Runs one frame through desc on dev; returns it in *out. */
static int run_graph(AVBufferRef *dev, const char *desc, AVFrame *out)
{
    AVFilterGraph *g = avfilter_graph_alloc();
    AVFilterContext *sink = NULL;
    int ret = g ? build_graph(g, dev, desc) : AVERROR(ENOMEM);

    if (ret >= 0) {
        ret = avfilter_graph_config(g, NULL);
    }
    if (ret >= 0) {
        sink = find_sink(g);
        ret = sink ? av_buffersink_get_frame(sink, out) : AVERROR_BUG;
    }
    avfilter_graph_free(&g);
    return ret;
}

static void pool_of(const AVFrame *f, Pool *p)
{
    const AVHWFramesContext *fc = (const AVHWFramesContext *)f->hw_frames_ctx->data;
    p->vkfc = fc->hwctx;
    p->dev = fc->device_ctx->hwctx;
    p->frame = (const AVVkFrame *)f->data[0];
    p->sw_format = fc->sw_format;
}

static int open_device(AVBufferRef **dev, const char *name, int split)
{
    AVDictionary *opts = NULL;
    int ret = 0;

    if (split) {
        ret = av_dict_set(&opts, "disable_multiplane", "1", 0);
    }
    if (ret >= 0) {
        ret = av_hwdevice_ctx_create(dev, AV_HWDEVICE_TYPE_VULKAN, name, opts, 0);
    }
    av_dict_free(&opts);
    return ret;
}

static void print_device(const AVBufferRef *dev)
{
    const AVHWDeviceContext *hw = (const AVHWDeviceContext *)dev->data;
    const AVVulkanDeviceContext *vk = hw->hwctx;
    const PFN_vkGetPhysicalDeviceProperties get_props =
        (PFN_vkGetPhysicalDeviceProperties)vk->get_proc_addr(vk->inst,
                                                             "vkGetPhysicalDeviceProperties");
    VkPhysicalDeviceProperties props;

    if (get_props) {
        get_props(vk->phys_dev, &props);
        (void)printf("device: %s\n", props.deviceName);
    }
}

typedef struct Case {
    const char *name;
    const char *size;
    const char *filter_args;
    Expect expect;
} Case;

/* Runs c; positive cases pass when the checker accepts, planted ones when it refuses. */
static int run_case(const Case *c, AVBufferRef *dev, int *failures)
{
    char desc[256];
    AVFrame *f = av_frame_alloc();
    Pool p;
    int ret;

    (void)snprintf(desc, sizeof(desc),
                   "testsrc2=s=%s:r=25:d=1,format=nv12,hwupload,pelorus_deband_vulkan%s,"
                   "buffersink",
                   c->size, c->filter_args);
    ret = f ? run_graph(dev, desc, f) : AVERROR(ENOMEM);
    if (ret < 0) {
        (void)printf("FAIL %s: graph '%s' returned %d\n", c->name, desc, ret);
        (*failures)++;
        av_frame_free(&f);
        return ret;
    }
    pool_of(f, &p);
    if (check_pool(c->name, &p, &c->expect, p.dev->enabled_dev_extensions,
                   p.dev->nb_enabled_dev_extensions)) {
        (void)printf("FAIL %s: contract not met (see stderr)\n", c->name);
        (*failures)++;
    } else {
        (void)printf("PASS %s: tiling %d, %d image(s), %s\n", c->name, (int)p.vkfc->tiling,
                     nb_images(p.vkfc),
                     c->expect.exportable ? "fd export per image" :
                                            "no export handle, not exported");
    }
    av_frame_free(&f);
    return 0;
}

/* Planted cases on an optimal pool: the checker must refuse all three. */
static void run_planted(AVBufferRef *dev, int *failures)
{
    static const Expect drm = {VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT, 0, 1};
    static const Expect optimal = {VK_IMAGE_TILING_OPTIMAL, 0, 0};
    static const Expect exported = {VK_IMAGE_TILING_OPTIMAL, 0, 1};
    const char *exts[64];
    int nb = 0;
    AVFrame *f = av_frame_alloc();
    Pool p;

    if (!f || run_graph(dev,
                        "testsrc2=s=64x64:r=25:d=1,format=nv12,hwupload,"
                        "pelorus_deband_vulkan,buffersink",
                        f) < 0) {
        (void)printf("FAIL planted: no optimal pool to plant into\n");
        (*failures)++;
        av_frame_free(&f);
        return;
    }
    pool_of(f, &p);
    if (check_pool("wrong-tiling", &p, &drm, p.dev->enabled_dev_extensions,
                   p.dev->nb_enabled_dev_extensions)) {
        (void)printf("PASS wrong-tiling: checker refused an OPTIMAL pool expected as DRM\n");
    } else {
        (void)printf("FAIL wrong-tiling: checker accepted the planted tiling\n");
        (*failures)++;
    }
    for (int i = 0; i < p.dev->nb_enabled_dev_extensions && nb < 64; i++) {
        if (strcmp(p.dev->enabled_dev_extensions[i], FD_EXT) != 0) {
            exts[nb++] = p.dev->enabled_dev_extensions[i];
        }
    }
    if (check_pool("no-fd-ext", &p, &optimal, exts, nb)) {
        (void)printf("PASS no-fd-ext: checker refused a device without %s\n", FD_EXT);
    } else {
        (void)printf("FAIL no-fd-ext: checker accepted a device without %s\n", FD_EXT);
        (*failures)++;
    }
    if (check_pool("export-expected", &p, &exported, p.dev->enabled_dev_extensions,
                   p.dev->nb_enabled_dev_extensions)) {
        (void)printf("PASS export-expected: checker refused an export claim the export info "
                     "does not offer\n");
    } else {
        (void)printf("FAIL export-expected: checker accepted an optimal multi-plane pool as "
                     "exportable\n");
        (*failures)++;
    }
    av_frame_free(&f);
}

/* 1 when dev can allocate and export a DRM pool; otherwise names the gap in *why. */
static int drm_capable(const AVBufferRef *dev, const char **why)
{
    const AVHWDeviceContext *dc = (const AVHWDeviceContext *)dev->data;
    const AVVulkanDeviceContext *vk = dc->hwctx;
    static const char *const need[] = {"VK_EXT_image_drm_format_modifier", DMABUF_EXT};

    for (size_t i = 0; i < sizeof(need) / sizeof(need[0]); i++) {
        if (!has_ext(vk->enabled_dev_extensions, vk->nb_enabled_dev_extensions, need[i])) {
            *why = need[i];
            return 0;
        }
    }
    return 1;
}

int main(void)
{
    static const Case optimal = {"optimal-nv12", "640x360", "", {VK_IMAGE_TILING_OPTIMAL, 0, 0}};
    static const Case drm = {
        "drm-nv12", "640x360", "=tiling=drm", {VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT, 0, 1}};
    static const Case split = {"split-641x361", "641x361", "", {VK_IMAGE_TILING_OPTIMAL, 1, 1}};
    /* NOLINTNEXTLINE(concurrency-mt-unsafe): read once in main before any thread exists */
    const char *name = getenv("VULKAN_DEVICE");
    const char *gap = NULL;
    AVBufferRef *dev = NULL;
    int failures = 0;

    if (open_device(&dev, name, 0) < 0) {
        (void)printf("SKIP: no Vulkan device\n");
        return 77;
    }
    print_device(dev);
    (void)run_case(&optimal, dev, &failures);
    if (drm_capable(dev, &gap)) {
        (void)run_case(&drm, dev, &failures);
    }
    run_planted(dev, &failures);
    av_buffer_unref(&dev);
    if (open_device(&dev, name, 1) < 0) {
        (void)printf("FAIL %s: no Vulkan device with disable_multiplane=1\n", split.name);
        return 1;
    }
    (void)run_case(&split, dev, &failures);
    av_buffer_unref(&dev);
    if (failures) {
        (void)printf("FAIL: %d failure(s)\n", failures);
        return 1;
    }
    if (gap) {
        (void)printf("SKIP: drm-nv12 not run, the device did not enable %s\n", gap);
        return 77;
    }
    (void)printf("OK: export contract held on every pool\n");
    return 0;
}
