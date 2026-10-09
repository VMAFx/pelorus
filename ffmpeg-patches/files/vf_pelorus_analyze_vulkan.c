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
 * Pelorus frame-statistics analyzer, Vulkan compute (zero-copy).
 *
 * Reduces each frame's luma to per-frame banding / variance / edge statistics on
 * the GPU, reads them back, and attaches them as the *measured* Pelorus interop
 * sections (PEL_SEC_BANDING + PEL_SEC_VARIANCE) — the maps a downstream vmafx
 * vf_libvmaf* uses for perceptually-weighted scoring, and the data the deband
 * filter only approximates today. The frame passes through unchanged; only side
 * data is added. Readback follows the vf_scdet_vulkan pattern. See
 * <pelorus/interop.h> and docs/metrics/analyze.md.
 *
 * The GPU reduction is *per cell* (one compute workgroup == one `cell` x `cell`
 * tile, 32 by default): each cell's luma mean / variance / edge-density /
 * low-amplitude-gradient is written to a position-preserving SSBO indexed by
 * (tile_y * grid_cols + tile_x). The frame-scalar PEL_SEC_* summaries are derived
 * host-side by summing over all cells, and with `maps=1` (default) the per-cell
 * banding / variance / edge values ride the same blob as the maps the ABI 1.0
 * sections already point at (issue #219, ADR-0177, pelorus_analyze_maps.h). With
 * `roi=1` the same per-cell signals drive an *auto-detected*
 * banding-prone ROI map emitted as AV_FRAME_DATA_REGIONS_OF_INTEREST — a negative
 * qoffset on flat-but-not-constant tiles that variance-AQ starves (ADR-0114
 * Tier 0; bench-results.md v0.4 proved the concept at −36% banding iso-bitrate
 * vs x265 aq-mode 2). libx265/libx264/QSV/VAAPI honor it with no patch.
 */

#include "libavutil/buffer.h"
#include "libavutil/common.h"
#include "libavutil/dict.h"
#include "libavutil/frame.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "libavutil/rational.h"
#include "pelorus_analyze_maps.h"
#include "pelorus_sidedata.h"
#include "pelorus_vulkan_sample.h"
#include "vulkan_filter.h"

#include "filters.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <pelorus/interop.h>

/* Fixed-point scale shared by shader writes and host reads. */
#define PEL_GS PEL_AN_GS

/* Max ROI rectangles we coalesce banding tiles into (keeps side data compact;
 * one AVRegionOfInterest is 24 bytes). */
#define PEL_MAX_ROI 32

typedef struct PelorusAnalyzeVulkanContext {
    FFVulkanContext vkctx; /* MUST be first */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;
    AVBufferPool *stat_buf_pool;

    double flat_thr; /* per-tile variance below which a tile is banding-prone */

    /* ROI auto-detection (ADR-0114 Tier 0). */
    int roi;             /* emit AV_FRAME_DATA_REGIONS_OF_INTEREST           */
    double roi_strength; /* max |qoffset| as a fraction of the QP range      */
    double grad_lo;      /* min tile gradient to count as a real gradient    */
    double grad_hi;      /* gradient above which a tile is "textured", score 0 */

    int cell; /* cell edge, luma pixels: a power of two in 8..64 */
    int maps; /* attach the per-cell maps (ADR-0177)               */

    /* Grid and per-grid buffers, sized by analyze_size_grid() at link setup and
     * again only if the frame size changes: nothing is allocated per frame for
     * the maps or the blob (HISS-03). */
    int width;
    int height;
    int grid_cols;
    int grid_rows;
    PelAnMaps cells;              /* per-cell score + the three maps       */
    AVRegionOfInterest *roi_runs; /* coalesce_roi scratch, one run per cell */
    AVRegionOfInterest roi_detected[PEL_MAX_ROI];
    AVBufferPool *sd_pool; /* side-data blobs of sd_len bytes */
    size_t sd_len;

    /* Per-frame complexity scalar (ADR-0132), EMA-smoothed across frames and
     * reset on a scene cut. complexity_seen guards the first frame. */
    float complexity_ema;
    int complexity_seen;
} PelorusAnalyzeVulkanContext;

/* The reduction now lives in vulkan/pelorus_analyze.comp.glsl, compiled to
 * SPIR-V at build time and linked in here. FFmpeg 9 removed the runtime GLSL
 * builder (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also retires the old
 * inline-vs-reference lockstep duplication. */
extern const unsigned char ff_pelorus_analyze_comp_spv_data[];
extern const unsigned int ff_pelorus_analyze_comp_spv_len;

/* The push block of vulkan/pelorus_analyze.comp.glsl, byte for byte (std430,
 * 2 * int + 2 * float = 16 bytes). */
typedef struct PelorusAnalyzePush {
    int32_t grid_cols;
    int32_t ntiles;
    float grad_lo;
    float sample_scale;
} PelorusAnalyzePush;

/* Descriptor set in the order of the shader's bindings, then the push block. */
static void analyze_shader_layout(FFVulkanContext *vkctx, FFVulkanShader *shd, int planes)
{
    FFVulkanDescriptorSetBinding desc[] = {
        {
            .name = "input_images",
            .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
            .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format, FF_VK_REP_FLOAT),
            .mem_quali = "readonly",
            .dimensions = 2,
            .elems = planes,
            .stages = VK_SHADER_STAGE_COMPUTE_BIT,
        },
        {
            .name = "tile_buffer",
            .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .mem_layout = "std430",
            .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            /* One runtime-sized array (GLSL allows only one, and last);
             * struct-of-arrays by field: tile[k*ntiles + idx], k in 0..4
             * for var/edge/grad/valid/mean. Host reads the five contiguous
             * spans (mean feeds the coarse inter-tile banding scale). */
            .buf_content = "uint tile[];",
        },
    };

    ff_vk_shader_add_descriptor_set(vkctx, shd, desc, 2, 0);
    ff_vk_shader_add_push_const(shd, 0, sizeof(PelorusAnalyzePush), VK_SHADER_STAGE_COMPUTE_BIT);
}

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err = 0;
    PelorusAnalyzeVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd;
    const int planes = av_pix_fmt_count_planes(vkctx->input_format);
    const uint32_t wg = pel_an_workgroup((uint32_t)s->cell);

    s->qf = ff_vk_qf_find(vkctx, VK_QUEUE_COMPUTE_BIT, 0);
    if (!s->qf) {
        av_log(ctx, AV_LOG_ERROR, "Device has no compute queues!\n");
        return AVERROR(ENOTSUP);
    }

    RET(ff_vk_exec_pool_init(vkctx, s->qf, &s->e, s->qf->num * 4, 0, 0, 0, NULL));
    /* A literal input_images[0] lets glslc collapse the unsized descriptor
     * array to a fixed one-element SPIR-V array, diverging from the FFmpeg
     * descriptor layout below on multi-plane frames. Keep the luma index as a
     * specialization constant so the runtime descriptor-array contract is
     * retained. Constant 1 is the per-invocation span: one wg x wg workgroup
     * covers one cell x cell cell. */
    SPEC_LIST_CREATE(sl, 2, 2 * sizeof(uint32_t))
    SPEC_LIST_ADD(sl, 0, 32, 0u);
    SPEC_LIST_ADD(sl, 1, 32, (uint32_t)s->cell / wg);
    ff_vk_shader_load(shd, VK_SHADER_STAGE_COMPUTE_BIT, sl, (uint32_t[]){wg, wg, 1}, 0);
    analyze_shader_layout(vkctx, shd, planes);

    RET(ff_vk_shader_link(vkctx, shd, ff_pelorus_analyze_comp_spv_data,
                          ff_pelorus_analyze_comp_spv_len, "main"));
    RET(ff_vk_shader_register_exec(vkctx, &s->e, shd));

    s->initialized = 1;

fail:
    return err;
}

/* Release the grid-sized buffers and the side-data pool. Blobs still riding
 * frames keep the pool alive until they are freed (av_buffer_pool_uninit). */
static void analyze_free_grid(PelorusAnalyzeVulkanContext *s)
{
    av_freep(&s->cells.score);
    av_freep(&s->cells.band);
    av_freep(&s->cells.var);
    av_freep(&s->cells.edge);
    av_freep(&s->roi_runs);
    av_buffer_pool_uninit(&s->sd_pool);
    s->sd_len = 0;
    s->width = 0;
    s->height = 0;
    s->grid_cols = 0;
    s->grid_rows = 0;
}

/* Size the cell grid, the per-cell buffers and the side-data pool for a frame
 * size. Runs at link setup and again only when the frame size changes, so the
 * per-frame path allocates nothing for the maps or the blob (HISS-03). A grid
 * above PEL_AN_MAX_CELLS (or an invalid `cell`) is refused here. */
static int analyze_size_grid(AVFilterContext *ctx, int width, int height)
{
    PelorusAnalyzeVulkanContext *s = ctx->priv;
    PelorusSideData meta;
    uint16_t cols = 0, rows = 0;
    size_t cells;
    pel_result rc;

    if (width == s->width && height == s->height && s->sd_pool)
        return 0;
    analyze_free_grid(s);
    rc = pel_an_grid((uint32_t)FFMAX(width, 0), (uint32_t)FFMAX(height, 0), (uint32_t)s->cell,
                     &cols, &rows);
    if (rc == PEL_ERR_RANGE) {
        av_log(ctx, AV_LOG_ERROR,
               "%dx%d at cell=%d exceeds the grid limit of %u cells and 65535 per side; "
               "use a larger cell\n",
               width, height, s->cell, PEL_AN_MAX_CELLS);
        return AVERROR(ERANGE);
    }
    if (rc != PEL_OK) {
        av_log(ctx, AV_LOG_ERROR, "cell=%d is not a power of two in %u..%u\n", s->cell,
               PEL_AN_CELL_MIN, PEL_AN_CELL_MAX);
        return AVERROR(EINVAL);
    }
    cells = (size_t)cols * rows;
    s->cells.score = av_calloc(cells, sizeof(*s->cells.score));
    s->cells.band = av_calloc(cells, sizeof(*s->cells.band));
    s->cells.var = av_calloc(cells, sizeof(*s->cells.var));
    s->cells.edge = av_calloc(cells, sizeof(*s->cells.edge));
    if (s->roi) /* coalesce_roi() runs only with roi=1 */
        s->roi_runs = av_calloc(cells, sizeof(*s->roi_runs));
    memset(&meta, 0, sizeof(meta));
    meta.grid_cols = cols;
    meta.grid_rows = rows;
    rc = pel_an_blob_size(&meta, s->maps, &s->sd_len);
    if (rc == PEL_OK)
        s->sd_pool = av_buffer_pool_init(s->sd_len, NULL);
    if (!s->cells.score || !s->cells.band || !s->cells.var || !s->cells.edge ||
        (s->roi && !s->roi_runs) || !s->sd_pool) {
        analyze_free_grid(s);
        return rc == PEL_OK || rc == PEL_ERR_NOMEM ? AVERROR(ENOMEM) : AVERROR(EINVAL);
    }
    s->width = width;
    s->height = height;
    s->grid_cols = cols;
    s->grid_rows = rows;
    return 0;
}

static int analyze_vulkan_config_input(AVFilterLink *inlink)
{
    int err = ff_vk_filter_config_input(inlink);

    if (err < 0)
        return err;
    return analyze_size_grid(inlink->dst, inlink->w, inlink->h);
}

/* Map a per-cell banding score to an AVRational qoffset (negative => more bits /
 * lower QP). Magnitude = score * roi_strength, clamped to [-roi_strength, 0].
 * Encoded as num/1000 so libx265's qoffset.num/qoffset.den read is exact. */
static AVRational score_to_qoffset(const PelorusAnalyzeVulkanContext *s, float score)
{
    int milli = (int)lrintf(-score * (float)s->roi_strength * 1000.0f);
    milli = av_clip(milli, -1000, 0);
    return av_make_q(milli, 1000);
}

/* qsort comparator: order coalesced runs by descending banding strength. A
 * stronger banding cell gets a more-negative qoffset.num (down to -1000), so the
 * strongest run is the most negative one — sort ascending by qoffset.num. */
static int roi_strength_cmp(const void *pa, const void *pb)
{
    const AVRegionOfInterest *a = pa;
    const AVRegionOfInterest *b = pb;
    if (a->qoffset.num < b->qoffset.num)
        return -1; /* a is stronger banding -> earlier */
    if (a->qoffset.num > b->qoffset.num)
        return 1;
    return 0;
}

/* Greedy row-run coalescing: scan cells row-major, merge horizontally adjacent
 * banding cells of the same qoffset bucket into one rectangle. ALL runs are
 * collected into s->roi_runs (at most one per cell, sized with the grid), then
 * ranked by banding strength (|qoffset|) and the top `max` are kept (drop the
 * lowest-score remainder once full). Per-cell rectangles fall out naturally when
 * neighbours differ. Writes into out[] and returns the rectangle count. */
static int coalesce_roi(const PelorusAnalyzeVulkanContext *s, const float *score,
                        AVRegionOfInterest *out, int max)
{
    const int cols = s->grid_cols, rows = s->grid_rows, cell = s->cell;
    AVRegionOfInterest *runs = s->roi_runs;
    int n = 0;
    int ty, tx;

    if (max <= 0)
        return 0;

    /* Collect every coalesced run (uncapped). */
    for (ty = 0; ty < rows; ty++) {
        tx = 0;
        while (tx < cols) {
            float sc = score[ty * cols + tx];
            int q0, run, x;
            if (sc <= 0.0f) {
                tx++;
                continue;
            }
            /* qoffset bucket (milli) of the run head. */
            q0 = score_to_qoffset(s, sc).num;
            run = 1;
            for (x = tx + 1; x < cols; x++) {
                float scn = score[ty * cols + x];
                if (scn <= 0.0f || score_to_qoffset(s, scn).num != q0)
                    break;
                run++;
            }
            runs[n] = (AVRegionOfInterest){
                .self_size = sizeof(AVRegionOfInterest),
                .top = ty * cell,
                .bottom = FFMIN((ty + 1) * cell, s->vkctx.output_height),
                .left = tx * cell,
                .right = FFMIN((tx + run) * cell, s->vkctx.output_width),
                .qoffset = av_make_q(q0, 1000),
            };
            n++;
            tx += run;
        }
    }

    /* Rank by banding strength and keep the strongest `max`. */
    if (n > 1)
        qsort(runs, (size_t)n, sizeof(*runs), roi_strength_cmp);
    if (n > max)
        n = max;
    memcpy(out, runs, (size_t)n * sizeof(*out));
    return n;
}

/* Attach the banding-prone cells as the standard AV_FRAME_DATA_REGIONS_OF_INTEREST
 * side data (ADR-0114 Tier 0). The per-cell score is max(fine, coarse) from
 * pel_an_cell_maps(): a cell flagged by EITHER the fine (per-cell) or the coarse
 * (inter-cell mean ramp) scale is banding-prone (CAMBI multi-scale, ADR-0133).
 * Mirrors vf_addroi's attach mechanics: one buffer,
 * nb_regions*sizeof(AVRegionOfInterest), each region's self_size set. Appends to
 * any pre-existing ROI side data. */
static int attach_roi(PelorusAnalyzeVulkanContext *s, AVFrame *frame)
{
    AVRegionOfInterest *roi;
    AVFrameSideData *sd_old, *sd_new;
    AVBufferRef *roi_ref;
    int ndet, nb_old = 0, nb_total, i;
    uint32_t old_size = sizeof(AVRegionOfInterest);

    ndet = coalesce_roi(s, s->cells.score, s->roi_detected, PEL_MAX_ROI);
    if (ndet == 0)
        return 0; /* no banding-prone cells — leave the frame to the encoder */

    /* Append to existing ROI side data (e.g. a manual vf_addroi upstream). */
    sd_old = av_frame_get_side_data(frame, AV_FRAME_DATA_REGIONS_OF_INTEREST);
    if (sd_old) {
        const AVRegionOfInterest *o = (const AVRegionOfInterest *)sd_old->data;
        old_size = o->self_size;
        if (!old_size || sd_old->size % old_size != 0)
            return AVERROR(EINVAL);
        nb_old = sd_old->size / old_size;
    }

    nb_total = nb_old + ndet;
    roi_ref = av_buffer_alloc(sizeof(AVRegionOfInterest) * (size_t)nb_total);
    if (!roi_ref)
        return AVERROR(ENOMEM);
    roi = (AVRegionOfInterest *)roi_ref->data;

    /* Copy the pre-existing regions first (they take precedence on overlap:
     * libx265 iterates in reverse, so earlier-in-array wins). */
    for (i = 0; i < nb_old; i++) {
        const AVRegionOfInterest *o =
            (const AVRegionOfInterest *)(sd_old->data + (size_t)old_size * i);
        roi[i] = (AVRegionOfInterest){
            .self_size = sizeof(AVRegionOfInterest),
            .top = o->top,
            .bottom = o->bottom,
            .left = o->left,
            .right = o->right,
            .qoffset = o->qoffset,
        };
    }
    for (i = 0; i < ndet; i++)
        roi[nb_old + i] = s->roi_detected[i];

    if (sd_old)
        av_frame_remove_side_data(frame, AV_FRAME_DATA_REGIONS_OF_INTEREST);

    sd_new = av_frame_new_side_data_from_buf(frame, AV_FRAME_DATA_REGIONS_OF_INTEREST, roi_ref);
    if (!sd_new) {
        av_buffer_unref(&roi_ref);
        return AVERROR(ENOMEM);
    }
    return 0;
}

/* Emit a per-frame scalar as FFmpeg frame metadata (the vf_scdet idiom). Lets a
 * host-side pass read analyze's signals via `ffprobe -show_frames` or the
 * `metadata=print` filter without decoding the interop blob — the extraction
 * path for per-shot CRF steering, the autotune loop, and debugging (ADR-0136). */
static void pel_set_meta_f(AVFrame *frame, const char *key, float v)
{
    char buf[32];
    snprintf(buf, sizeof(buf), "%.6f", (double)v);
    av_dict_set(&frame->metadata, key, buf, 0);
}

/* Frame scalars over the valid cells: mean variance, mean edge density, and the
 * share of flat (banding-prone) cells. Summing over all cells reproduces the
 * frame scalars the old hashed-slice scheme emitted. Returns the valid-cell
 * count; 0 leaves the outputs untouched. */
static uint64_t frame_scalars(const PelorusAnalyzeVulkanContext *s, const PelAnTiles *tv,
                              float *gvar, float *gedge, float *flat_frac)
{
    const int ntiles = tv->cols * tv->rows;
    double var_sum = 0.0, edge_sum = 0.0;
    uint64_t flat = 0, total = 0;
    int i;

    for (i = 0; i < ntiles; i++) {
        if (!tv->valid[i])
            continue;
        var_sum += tv->var[i];
        edge_sum += tv->edge[i];
        if ((double)(tv->var[i] / PEL_GS) < s->flat_thr)
            flat++;
        total++;
    }
    if (total == 0)
        return 0;
    *gvar = (float)(var_sum / PEL_GS / (double)total);
    *gedge = (float)(edge_sum / PEL_GS / (double)total);
    *flat_frac = (float)((double)flat / (double)total);
    return total;
}

/* Per-frame complexity (ADR-0132): a normalized texture energy from the
 * variance + edge aggregates, folding in motion when an upstream pelorus_mc
 * attached PEL_SEC_MOTION, EMA-smoothed and reset on a scene cut. The per-shot
 * CRF steering maps this to a qoffset (autotune-learned). Also mirrors the
 * per-frame scalars as frame metadata (host-readable). */
static void complexity_section(PelorusAnalyzeVulkanContext *s, AVFrame *frame, float gvar,
                               float gedge, float flat_frac, PelorusComplexitySection *cx)
{
    float texture = av_clipf(0.5f * FFMIN(gvar / 0.05f, 1.0f) + 0.5f * gedge, 0.0f, 1.0f);
    float motion = 0.0f;
    int scene_cut = 0;
    const void *mp = NULL;
    size_t msz = 0;
    float craw, cema;

    /* Newest Pelorus blob carrying PEL_SEC_MOTION (every producer appends its
     * own entry); msz is the producer/consumer minimum (R4), so each field is
     * read only when an older, shorter producer section still covers it. */
    if (pelorus_sd_find_section(frame, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &mp, &msz)) {
        const PelorusMotionSection *mo = mp;
        if (PEL_SD_FIELD_OK(msz, PelorusMotionSection, motion_magnitude_mean))
            motion = av_clipf(mo->motion_magnitude_mean / 8.0f, 0.0f, 1.0f);
        if (PEL_SD_FIELD_OK(msz, PelorusMotionSection, has_scene_cut))
            scene_cut = mo->has_scene_cut ? 1 : 0;
    }

    craw = av_clipf(0.7f * texture + 0.3f * motion, 0.0f, 1.0f);
    if (!s->complexity_seen || scene_cut)
        cema = craw; /* reset on first frame / scene cut */
    else
        cema = 0.3f * craw + 0.7f * s->complexity_ema;
    s->complexity_ema = cema;
    s->complexity_seen = 1;

    memset(cx, 0, sizeof(*cx));
    cx->complexity = cema;
    cx->texture_energy = texture;
    cx->motion_component = motion;
    cx->has_scene_cut = (uint8_t)scene_cut;

    pel_set_meta_f(frame, "lavfi.pelorus.complexity", cema);
    pel_set_meta_f(frame, "lavfi.pelorus.texture", texture);
    pel_set_meta_f(frame, "lavfi.pelorus.motion", motion);
    pel_set_meta_f(frame, "lavfi.pelorus.variance", gvar);
    pel_set_meta_f(frame, "lavfi.pelorus.edge", gedge);
    pel_set_meta_f(frame, "lavfi.pelorus.banding", flat_frac);
    av_dict_set(&frame->metadata, "lavfi.pelorus.scene_cut", scene_cut ? "1" : "0", 0);
}

/* Derive the frame summaries from the per-cell records and attach the measured
 * PEL_SEC_VARIANCE + PEL_SEC_BANDING (+ PEL_SEC_COMPLEXITY) sections, with the
 * per-cell maps unless maps=0, as one blob from the grid-sized pool. */
static int attach_stats(AVFilterContext *ctx, AVFrame *frame, const PelAnTiles *tv)
{
    PelorusAnalyzeVulkanContext *s = ctx->priv;
    const AVPixFmtDescriptor *d = av_pix_fmt_desc_get(s->vkctx.output_format);
    PelorusSideData meta;
    PelAnSections sec;
    AVBufferRef *buf;
    size_t len = 0;
    float gvar = 0.0f, gedge = 0.0f, flat_frac = 0.0f;
    pel_result rc;

    if (frame_scalars(s, tv, &gvar, &gedge, &flat_frac) == 0)
        return 0; /* nothing to report (degenerate frame) */

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = (uint64_t)frame->pts;
    meta.bit_depth = d ? (uint8_t)d->comp[0].depth : 0;
    meta.plane_layout = (d && d->log2_chroma_w == 0 && d->log2_chroma_h == 0) ?
                            PEL_LAYOUT_444 :
                            ((d && d->log2_chroma_h == 0) ? PEL_LAYOUT_422 : PEL_LAYOUT_420);
    meta.grid_cols = (uint16_t)s->grid_cols;
    meta.grid_rows = (uint16_t)s->grid_rows;
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'A');

    memset(&sec, 0, sizeof(sec));
    sec.var.global_variance = gvar;
    sec.var.edge_density = gedge;
    sec.var.texture_energy = gedge; /* edge density is the v0.1 texture proxy */
    sec.band.flat_area_fraction = flat_frac;
    sec.band.global_banding_risk = flat_frac; /* coarse: low-variance area share */
    sec.band.contour_strength_mean = gvar;
    complexity_section(s, frame, gvar, gedge, flat_frac, &sec.cx);

    buf = av_buffer_pool_get(s->sd_pool);
    if (!buf)
        return AVERROR(ENOMEM);
    rc = pel_an_pack(&meta, &sec, s->maps ? &s->cells : NULL, buf->data, (size_t)buf->size, &len);
    if (rc != PEL_OK || len != (size_t)buf->size) {
        av_log(ctx, AV_LOG_ERROR, "side-data pack failed (pel_result %d, %zu of %zu bytes)\n",
               (int)rc, len, (size_t)buf->size);
        av_buffer_unref(&buf);
        return rc == PEL_ERR_NOMEM ? AVERROR(ENOMEM) : AVERROR(EINVAL);
    }
    if (!av_frame_new_side_data_from_buf(frame, AV_FRAME_DATA_SEI_UNREGISTERED, buf)) {
        av_buffer_unref(&buf);
        return AVERROR(ENOMEM);
    }
    return 0;
}

/* Zero the tile SSBO and order it, and the input frame, before the dispatch. */
static void analyze_record_clear(FFVulkanFunctions *vk, FFVkExecContext *exec,
                                 const FFVkBuffer *buf_vk, const VkImageMemoryBarrier2 *img_bar,
                                 int nb_img_bar)
{
    vk->CmdPipelineBarrier2(exec->buf,
                            &(VkDependencyInfo){
                                .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                .pBufferMemoryBarriers =
                                    &(VkBufferMemoryBarrier2){
                                        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                                        .srcStageMask = VK_PIPELINE_STAGE_2_NONE,
                                        .dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                                        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .buffer = buf_vk->buf,
                                        .size = buf_vk->size,
                                        .offset = 0,
                                    },
                                .bufferMemoryBarrierCount = 1,
                            });
    vk->CmdFillBuffer(exec->buf, buf_vk->buf, 0, buf_vk->size, 0x0);
    vk->CmdPipelineBarrier2(exec->buf,
                            &(VkDependencyInfo){
                                .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                .pImageMemoryBarriers = img_bar,
                                .imageMemoryBarrierCount = nb_img_bar,
                                .pBufferMemoryBarriers =
                                    &(VkBufferMemoryBarrier2){
                                        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                                        .srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                                        .dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                                        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                                        .dstAccessMask = VK_ACCESS_2_SHADER_STORAGE_READ_BIT |
                                                         VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                                        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .buffer = buf_vk->buf,
                                        .size = buf_vk->size,
                                        .offset = 0,
                                    },
                                .bufferMemoryBarrierCount = 1,
                            });
}

/* Make the shader's SSBO writes visible to the host read after the wait. */
static void analyze_record_host_barrier(FFVulkanFunctions *vk, FFVkExecContext *exec,
                                        const FFVkBuffer *buf_vk)
{
    vk->CmdPipelineBarrier2(exec->buf,
                            &(VkDependencyInfo){
                                .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                .pBufferMemoryBarriers =
                                    &(VkBufferMemoryBarrier2){
                                        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                                        .srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                                        .dstStageMask = VK_PIPELINE_STAGE_2_HOST_BIT,
                                        .srcAccessMask = VK_ACCESS_2_SHADER_STORAGE_READ_BIT |
                                                         VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                                        .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
                                        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .buffer = buf_vk->buf,
                                        .size = buf_vk->size,
                                        .offset = 0,
                                    },
                                .bufferMemoryBarrierCount = 1,
                            });
}

/* Record, submit and wait for one reduction: one workgroup per cell (the
 * shader's span covers cells wider than the workgroup). The cell records are
 * in buf_vk->mapped_mem on success. */
static int analyze_dispatch(PelorusAnalyzeVulkanContext *s, AVFrame *in, FFVkBuffer *buf_vk,
                            PelorusAnalyzePush *pc)
{
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanFunctions *vk = &vkctx->vkfn;
    VkImageView views[AV_NUM_DATA_POINTERS];
    VkImageMemoryBarrier2 img_bar[8];
    int nb_img_bar = 0;
    FFVkExecContext *exec = ff_vk_exec_get(vkctx, &s->e);
    int err;

    ff_vk_exec_start(vkctx, exec);
    err = ff_vk_exec_add_dep_frame(vkctx, exec, in, VK_PIPELINE_STAGE_2_NONE,
                                   VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
    if (err >= 0)
        err = ff_vk_create_imageviews(vkctx, exec, views, in, FF_VK_REP_FLOAT);
    if (err < 0) {
        ff_vk_exec_discard_deps(vkctx, exec);
        return err;
    }
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, in, views, 0, 0, VK_IMAGE_LAYOUT_GENERAL,
                                  VK_NULL_HANDLE);
    ff_vk_frame_barrier(vkctx, exec, in, img_bar, &nb_img_bar, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                        VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,
                        VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED);
    analyze_record_clear(vk, exec, buf_vk, img_bar, nb_img_bar);

    err = ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 1, 0, buf_vk, 0, buf_vk->size,
                                          VK_FORMAT_UNDEFINED);
    if (err < 0) {
        ff_vk_exec_discard_deps(vkctx, exec);
        return err;
    }
    ff_vk_exec_bind_shader(vkctx, exec, &s->shd);
    ff_vk_shader_update_push_const(vkctx, exec, &s->shd, VK_SHADER_STAGE_COMPUTE_BIT, 0,
                                   sizeof(*pc), pc);
    vk->CmdDispatch(exec->buf, (uint32_t)s->grid_cols, (uint32_t)s->grid_rows, 1);
    analyze_record_host_barrier(vk, exec, buf_vk);

    err = ff_vk_exec_submit(vkctx, exec); /* discards its dependencies on failure */
    if (err < 0)
        return err;
    ff_vk_exec_wait(vkctx, exec);
    return 0;
}

/* Run the reduction into a pooled host-visible buffer (five uint spans,
 * var/edge/grad/valid/mean, each grid_cols * grid_rows long) and point `tv` at
 * them. The caller keeps *buf alive while it reads `tv`. */
static int analyze_reduce(PelorusAnalyzeVulkanContext *s, AVFrame *in, AVBufferRef **buf,
                          PelAnTiles *tv)
{
    FFVulkanContext *vkctx = &s->vkctx;
    const int ntiles = s->grid_cols * s->grid_rows;
    PelorusAnalyzePush pc = {
        .grid_cols = s->grid_cols,
        .ntiles = ntiles,
        .grad_lo = (float)s->grad_lo,
        .sample_scale = pel_vk_sample_scale(vkctx->input_format),
    };
    const uint32_t *base;
    FFVkBuffer *buf_vk;
    int err;

    err = ff_vk_get_pooled_buffer(
        vkctx, &s->stat_buf_pool, buf,
        VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
        (size_t)ntiles * 5 * sizeof(uint32_t),
        VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
            VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (err < 0)
        return err;
    buf_vk = (FFVkBuffer *)(*buf)->data;
    err = analyze_dispatch(s, in, buf_vk, &pc);
    if (err < 0)
        return err;

    base = (const uint32_t *)buf_vk->mapped_mem;
    tv->var = base;
    tv->edge = base + ntiles;
    tv->grad = base + 2 * ntiles;
    tv->valid = base + 3 * ntiles;
    tv->mean = base + 4 * ntiles;
    tv->cols = s->grid_cols;
    tv->rows = s->grid_rows;
    return 0;
}

static int analyze_vulkan_filter_frame(AVFilterLink *link, AVFrame *in)
{
    AVFilterContext *ctx = link->dst;
    PelorusAnalyzeVulkanContext *s = ctx->priv;
    AVBufferRef *buf = NULL;
    PelAnTiles tv;
    int err = 0;

    if (!s->initialized)
        err = init_filter(ctx);
    /* The cell grid was sized for the link (config_input); this only resizes
     * if a frame arrives with another size. */
    if (err >= 0)
        err = analyze_size_grid(ctx, in->width, in->height);
    if (err >= 0)
        err = analyze_reduce(s, in, &buf, &tv);
    if (err >= 0) {
        /* Per-cell score and maps; the score also feeds the ROI detector. */
        const PelAnThresholds thr = {(float)s->flat_thr, (float)s->grad_lo};

        pel_an_cell_maps(&thr, &tv, &s->cells);
        err = attach_stats(ctx, in, &tv);
    }
    if (err >= 0 && s->roi)
        err = attach_roi(s, in);
    av_buffer_unref(&buf);
    if (err < 0) {
        av_frame_free(&in);
        return err;
    }
    return ff_filter_frame(ctx->outputs[0], in);
}

static void analyze_vulkan_uninit(AVFilterContext *avctx)
{
    PelorusAnalyzeVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    av_buffer_pool_uninit(&s->stat_buf_pool);
    analyze_free_grid(s);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusAnalyzeVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_analyze_vulkan_options[] = {
    {"flat",
     "per-tile variance below which a tile is banding-prone",
     OFFSET(flat_thr),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.0015},
     0.0,
     0.25,
     FLAGS},
    {"roi",
     "auto-detect banding-prone tiles and emit ROI side data",
     OFFSET(roi),
     AV_OPT_TYPE_BOOL,
     {.i64 = 0},
     0,
     1,
     FLAGS},
    {"roi_strength",
     "max |qoffset| for a fully banding tile (frac of QP range)",
     OFFSET(roi_strength),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.333},
     0.0,
     1.0,
     FLAGS},
    {"grad_lo",
     "min per-tile gradient counted as a real (banding) slope",
     OFFSET(grad_lo),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.002},
     0.0,
     0.5,
     FLAGS},
    {"grad_hi",
     "tile gradient at which banding risk peaks before texturing",
     OFFSET(grad_hi),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.01},
     0.0,
     0.5,
     FLAGS},
    {"cell",
     "cell edge of the analysis grid and its maps, luma pixels (8, 16, 32 or 64)",
     OFFSET(cell),
     AV_OPT_TYPE_INT,
     {.i64 = 32},
     PEL_AN_CELL_MIN,
     PEL_AN_CELL_MAX,
     FLAGS},
    {"maps",
     "attach the per-cell banding, variance and edge maps to the side data",
     OFFSET(maps),
     AV_OPT_TYPE_BOOL,
     {.i64 = 1},
     0,
     1,
     FLAGS},
    {NULL}};

AVFILTER_DEFINE_CLASS(pelorus_analyze_vulkan);

static const AVFilterPad pelorus_analyze_vulkan_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &analyze_vulkan_filter_frame,
        .config_props = &analyze_vulkan_config_input,
    },
};

static const AVFilterPad pelorus_analyze_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_analyze_vulkan = {
    .p.name = "pelorus_analyze_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus frame analyzer (Vulkan)"),
    .p.priv_class = &pelorus_analyze_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusAnalyzeVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &analyze_vulkan_uninit,
    FILTER_INPUTS(pelorus_analyze_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_analyze_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
