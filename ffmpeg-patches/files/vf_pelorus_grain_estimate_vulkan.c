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
 * Pelorus film-grain-synthesis parameter ESTIMATOR, Vulkan compute (zero-copy).
 *
 * Measures the film-grain characteristics of each frame's luma on the GPU and
 * emits film-grain-synthesis (FGS) parameters so a downstream encoder / bitstream
 * filter can RE-SYNTHESIZE the grain (AV1 AOM film_grain OBU / H.274 FGC SEI)
 * instead of spending bitrate coding it as residual. The frame passes through
 * unchanged; only side data is added. Readback follows the vf_scdet_vulkan /
 * vf_pelorus_analyze pattern (GPU reduction into a HOST_VISIBLE SSBO, submit+wait,
 * derive host-side). See <pelorus/interop.h>, docs/metrics/grain_estimate.md, and
 * ADR-0115.
 *
 * Estimator (per luma intensity band — the AV1/H.274 scaling function is
 * intensity-dependent by design): high-pass each pixel against a 3x3 box mean
 * (resid = luma - mean3x3), gate out pixels whose 3x3 range is large (an edge /
 * texture, not grain), and accumulate resid^2 into one of PEL_GRAIN_BANDS luma
 * bins with a per-bin pixel count -> per-band grain stddev. A global lag-1
 * correlation accumulator (resid*resid_right over flat pixels) gives a coarse AR
 * seed. The reduction is sliced (PEL_GRAIN_SLICES) to cut atomic contention.
 *
 * Host mapping: per-band RMS residual -> the AV1 AOM piecewise scaling function
 * y_points[value, scaling] and the lag-1 correlation -> a symmetric lag-1 AR
 * model, both through a table calibrated against FFmpeg's AV1 synthesizer
 * (ADR-0227); AV1-legal shifts. The filter emits BOTH the Pelorus PEL_SEC_FILMGRAIN
 * interop section (codec-neutral intent + AV1 params + H.274 mode scalars) AND a
 * native AV_FRAME_DATA_FILM_GRAIN_PARAMS (AV_FILM_GRAIN_PARAMS_AV1) so a
 * downstream FFmpeg AV1 encoder honours it with no Pelorus-specific BSF. The
 * OBU/SEI bitstream writer is a documented follow-up (ADR-0115).
 *
 * For H.274 (model=h274) the host also maps the pooled residual RMS and the
 * lag-1 correlation to SMPTE RDD 5 model-0 parameters (scale, cutoffs) through
 * a table calibrated against FFmpeg's own synthesizer, and emits them as
 * lavfi.pelorus.h274_* frame metadata for the pelorus_fgs options (ADR-0161).
 *
 * The algorithm lives in vulkan/pelorus_grain_estimate.comp.glsl, compiled to
 * SPIR-V at build time (FFmpeg 9 removed the runtime inline-GLSL builder).
 */

#include "libavutil/buffer.h"
#include "libavutil/common.h"
#include "libavutil/film_grain_params.h"
#include "libavutil/frame.h"
#include "libavutil/mem.h"
#include "libavutil/opt.h"
#include "libavutil/pixdesc.h"
#include "pelorus_vulkan_sample.h"
#include "pelorus_vulkan_input.h"
#include "vulkan_filter.h"

#include "filters.h"

#include <math.h>
#include <string.h>

#include <pelorus/interop.h>

/* Intensity bands and atomic-contention slices. MUST match the std430 SSBO
 * layout in pelorus_grain_estimate.comp.glsl, byte-for-byte. */
#define PEL_GRAIN_BANDS 8
#define PEL_GRAIN_SLICES 32

/* Fixed-point scales shared by shader writes and host reads. With resid
 * clamped to [-0.08,0.08] (lossless for real grain), resid^2 <= 0.0064 and the
 * biased lag-1 product (resid*resid_right + 0.0064) lies in [0, 0.0128], so
 * each per-pixel add is at most 1921 units. Times the largest per-slice pixel
 * count (1105920 at DCI 8K) every accumulator stays under 2^32
 * (scripts/test-grain-accumulator-bounds.py). The shader rounds each add to the
 * nearest unit, so the sums are unbiased; a unit is 1/150000 of a squared
 * normalized sample for the lag-1 product, fine enough to resolve the product
 * of one-code-value grain (ADR-0161).
 * MUST match pelorus_grain_estimate.comp.glsl. */
#define PEL_GRAIN_SUMSQ_GS 300000.0
#define PEL_GRAIN_CORR_GS 150000.0
/* Lag-1 product bias = RES_CLAMP^2, the most negative product; the host
 * subtracts cnt*BIAS. */
#define PEL_GRAIN_CORR_BIAS 0.0064
/* Residual clamp (lossless for real grain); matches the shader RES_CLAMP. */
#define PEL_GRAIN_RES_CLAMP 0.08
/* Frame-size guard: the accumulator bounds above hold up to the DCI 8K pixel
 * count (8192 x 4320 = 35389440 pixels, 1105920 per slice). Larger frames are
 * refused at link configuration instead of wrapping a slice sum
 * (scripts/test-grain-accumulator-bounds.py reads this value). */
#define PEL_GRAIN_MAX_PIXELS (8192LL * 4320LL)

/* Dual gate (ADR-0229). The shader also measures every band at a wide gate,
 * WIDE_GATE x edge_thr, and counts every band pixel. A band takes the wide
 * statistics when the default gate binds (kept fraction below GATE_FMAX), the
 * band holds at least GATE_MINCNT gated pixels, and its gated RMS over edge_thr
 * reaches GATE_RATIO of the white Gaussian grain curve at its kept fraction.
 * Texture sits at 0.79 of the curve or below, grain at 0.96 or above
 * (docs/research/0411-grain-dual-gate.md). */
#define PEL_GRAIN_WIDE_GATE 2.0
#define PEL_GRAIN_GATE_FMAX 0.95
#define PEL_GRAIN_GATE_MINCNT 1024
#define PEL_GRAIN_GATE_RATIO 0.875

/* H.274 model 0 as synthesized by FFmpeg's SMPTE RDD 5 implementation
 * (libavcodec/h274.c). log2_scale_factor 2 lies in the RDD 5 range 2..7 and is
 * the pelorus_fgs default under its RDD 5 profile (ADR-0155). The scale is in 8-bit code values at every bit depth:
 * RDD 5 limits it to 8 bits, and HM/VTM shift the grain left by bitdepth-8
 * before blending (ITU-T H.Sup21 (01/2025) 7.3.1.1, 7.3.2.1). */
#define PEL_GRAIN_H274_MODEL_ID 0
#define PEL_GRAIN_H274_LOG2_SCALE 2

/* Calibration of FFmpeg's model-0 grain (cutoff_h == cutoff_v, log2_scale 2)
 * through this estimator's arithmetic with the edge gate open: rho_r is the
 * residual lag-1 correlation, gain_r the residual RMS in 8-bit code values per
 * unit of comp_model_value[0]. Cutoffs 2..5 are omitted: their residual
 * correlation (0.22..0.49) repeats values that cutoffs 6..8 already take, so the
 * inversion would be ambiguous. Regenerate with
 * scripts/gen-h274-grain-calibration.py (ADR-0161). */
static const struct {
    uint8_t cutoff;
    float rho_r;
    float gain_r;
} pel_h274_calib[] = {
    {6, 0.5043f, 0.04239f},  {7, 0.4302f, 0.05699f},  {8, 0.3536f, 0.06905f},
    {9, 0.2563f, 0.09227f},  {10, 0.1914f, 0.10847f}, {11, 0.1431f, 0.13099f},
    {12, 0.0728f, 0.14908f}, {13, 0.0513f, 0.16799f}, {14, -0.0115f, 0.18554f},
};

/* Calibration of FFmpeg's AV1 luma grain (8-bit, ar_coeff_lag 1, ar_coeff_shift
 * 6, ar_coeffs_y {0, a, 0, a}, scaling_shift 8, overlap on) through this
 * estimator's arithmetic with the edge gate open: rho_r is the residual lag-1
 * correlation, gain_r the residual RMS in 8-bit code values per unit of the
 * scaling function. rho_r rises with a, so the table inverts by interpolation.
 * Regenerate with scripts/gen-av1-grain-calibration.py (ADR-0227). */
static const struct {
    uint8_t a;
    float rho_r;
    float gain_r;
} pel_aom_calib[] = {
    {0, -0.1547f, 0.11946f},  {4, -0.1212f, 0.11737f}, {8, -0.0915f, 0.11548f},
    {12, -0.0640f, 0.11406f}, {16, -0.0345f, 0.11284f}, {20, -0.0083f, 0.11173f},
    {24, 0.0190f, 0.11102f},  {28, 0.0474f, 0.11052f},
};

/* White Gaussian grain through this estimator's arithmetic at the default
 * gate: f is the kept fraction of the band, q the gated RMS over edge_thr.
 * Sorted by f. Regenerate with scripts/gen-grain-gate-curve.py (ADR-0229). */
static const struct {
    float f;
    float q;
} pel_gate_curve[] = {
    {0.0029f, 0.3009f}, {0.0083f, 0.2987f}, {0.0252f, 0.2932f}, {0.0487f, 0.2920f},
    {0.0931f, 0.2874f}, {0.1797f, 0.2806f}, {0.2469f, 0.2762f}, {0.3361f, 0.2717f},
    {0.4519f, 0.2645f}, {0.5867f, 0.2570f}, {0.7366f, 0.2451f}, {0.8677f, 0.2303f},
    {0.9164f, 0.2212f}, {0.9549f, 0.2099f}, {0.9787f, 0.1976f}, {0.9920f, 0.1842f},
};

/* Host-readback accumulator. Matches the GLSL std430 GrainBuf layout exactly. */
typedef struct PelorusGrainBuf {
    uint32_t sumsq[PEL_GRAIN_BANDS * PEL_GRAIN_SLICES]; /* Σ resid^2 * GS       */
    uint32_t cnt[PEL_GRAIN_BANDS * PEL_GRAIN_SLICES];   /* flat-pixel count     */
    uint32_t corr[PEL_GRAIN_SLICES];                    /* Σ (resid*resid_right + BIAS) * GS   */
    uint32_t corr_cnt[PEL_GRAIN_SLICES];                /* lag-1 sample count                  */
    uint32_t sumsq_w[PEL_GRAIN_BANDS * PEL_GRAIN_SLICES]; /* Σ resid^2 * GS, wide gate */
    uint32_t cnt_w[PEL_GRAIN_BANDS * PEL_GRAIN_SLICES];   /* pixel count, wide gate    */
    uint32_t total[PEL_GRAIN_BANDS * PEL_GRAIN_SLICES];   /* every band pixel          */
} PelorusGrainBuf;

typedef struct PelorusGrainEstimateVulkanContext {
    FFVulkanContext vkctx; /* MUST be first — generic init casts priv to this */

    int initialized;
    FFVkExecPool e;
    AVVulkanDeviceQueueFamily *qf;
    FFVulkanShader shd;
    AVBufferPool *stat_buf_pool;

    double edge_thr;   /* 3x3 range above which a pixel is an edge (skipped)     */
    double strength;   /* maps per-band RMS residual to the AV1 scaling [0,255]  */
    int grain_model;   /* enum pel_grain_model: 1=AOM (default), 2=H274          */
    int attach_native; /* also attach native AV_FRAME_DATA_FILM_GRAIN_PARAMS    */
    int adapt;         /* take wide-gate statistics on grain-consistent bands    */
} PelorusGrainEstimateVulkanContext;

/* The estimator now lives in vulkan/pelorus_grain_estimate.comp.glsl, compiled
 * to SPIR-V at build time and linked in here. FFmpeg 9 removed the runtime
 * GLSL builder (GLSLC/GLSLF/GLSLD + ff_vk_shader_init), which also retires the
 * old inline-vs-reference lockstep duplication. */
extern const unsigned char ff_pelorus_grain_estimate_comp_spv_data[];
extern const unsigned int ff_pelorus_grain_estimate_comp_spv_len;

/* Declare the shader's descriptors: binding order MUST match the .comp.glsl. */
static void add_descriptors(FFVulkanContext *vkctx, FFVulkanShader *shd)
{
    FFVulkanDescriptorSetBinding desc[] = {
        {
            .name = "input_images",
            .type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE,
            .mem_layout = ff_vk_shader_rep_fmt(vkctx->input_format, FF_VK_REP_FLOAT),
            .mem_quali = "readonly",
            .dimensions = 2,
            .elems = av_pix_fmt_count_planes(vkctx->input_format),
            .stages = VK_SHADER_STAGE_COMPUTE_BIT,
        },
        {
            .name = "grain_buffer",
            .type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .mem_layout = "std430",
            .stages = VK_SHADER_STAGE_COMPUTE_BIT,
            /* Mirrors PelorusGrainBuf / the .comp.glsl grain_buffer block. */
            .buf_content = "uint sumsq[256]; uint cnt[256]; "
                           "uint corr[32]; uint corr_cnt[32]; "
                           "uint sumsq_w[256]; uint cnt_w[256]; uint total[256];",
        },
    };
    ff_vk_shader_add_descriptor_set(vkctx, shd, desc, 2, 0);
}

static av_cold int init_filter(AVFilterContext *ctx)
{
    int err;
    PelorusGrainEstimateVulkanContext *s = ctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanShader *shd = &s->shd;

    s->qf = ff_vk_qf_find(vkctx, VK_QUEUE_COMPUTE_BIT, 0);
    if (!s->qf) {
        av_log(ctx, AV_LOG_ERROR, "Device has no compute queues!\n");
        return AVERROR(ENOTSUP);
    }

    err = ff_vk_exec_pool_init(vkctx, s->qf, &s->e, s->qf->num * 4, 0, 0, 0, NULL);
    if (err < 0)
        return err;

    /* The estimator reads only the luma plane and needs no C-generated GLSL, so
     * no specialization constants are required (spec == NULL). */
    err = ff_vk_shader_load(shd, VK_SHADER_STAGE_COMPUTE_BIT, NULL, (uint32_t[]){16, 16, 1}, 0);
    if (err < 0)
        return err;

    /* Mirrors the GLSL std430 push-constant block, byte-for-byte. */
    err = ff_vk_shader_add_push_const(shd, 0, 2 * sizeof(int) + 2 * sizeof(float),
                                      VK_SHADER_STAGE_COMPUTE_BIT);
    if (err < 0)
        return err;

    add_descriptors(vkctx, shd);

    err = ff_vk_shader_link(vkctx, shd, ff_pelorus_grain_estimate_comp_spv_data,
                            ff_pelorus_grain_estimate_comp_spv_len, "main");
    if (err < 0)
        return err;
    err = ff_vk_shader_register_exec(vkctx, &s->e, shd);
    if (err < 0)
        return err;

    s->initialized = 1;
    return 0;
}

static void pel_sd_free(void *opaque, uint8_t *data)
{
    (void)opaque;
    pel_blob_free(data);
}

/* Per-band RMS residual (grain stddev at that intensity), in [0,1], plus the
 * global lag-1 autocorrelation coefficient. Derived from the read-back SSBO. */
typedef struct PelorusGrainStats {
    float band_rms[PEL_GRAIN_BANDS]; /* RMS residual per intensity band, [0,1]  */
    uint64_t band_cnt[PEL_GRAIN_BANDS];
    float rms_all;       /* RMS residual pooled over all flat pixels, [0,1]     */
    float lag1;          /* lag-1 autocorrelation coefficient, [-1,1]           */
    uint64_t flat_total; /* total flat pixels measured                          */
    int wide_mask;       /* bit b set: band b took the wide-gate statistics     */
} PelorusGrainStats;

/* H.274 model-0 parameters for one full-range luma interval. */
typedef struct PelorusH274Model {
    int cutoff; /* comp_model_value[0][0][1] and [2]: cutoff_h == cutoff_v  */
    int scale;  /* comp_model_value[0][0][0], 8-bit code values, 0..255     */
} PelorusH274Model;

/* Emit a scalar as lavfi.pelorus.* frame metadata (ADR-0136 pattern), so the
 * tune=auto content router can read the grain estimate without parsing the
 * PEL_SEC_FILMGRAIN side-data blob. */
static void pel_set_meta_f(AVFrame *frame, const char *key, float v)
{
    char buf[32];
    (void)snprintf(buf, sizeof(buf), "%.6f", v);
    av_dict_set(&frame->metadata, key, buf, 0);
}

static void pel_set_meta_i(AVFrame *frame, const char *key, int v)
{
    av_dict_set_int(&frame->metadata, key, v, 0);
}

/* Gated RMS over edge_thr that white Gaussian grain gives at kept fraction f
 * (linear between rows, clamped to the table ends). */
static double pel_gate_curve_q(double f)
{
    int n = (int)FF_ARRAY_ELEMS(pel_gate_curve), i;

    if (f <= pel_gate_curve[0].f)
        return pel_gate_curve[0].q;
    for (i = 1; i < n; i++) {
        if (f <= pel_gate_curve[i].f) {
            double t = (f - pel_gate_curve[i - 1].f) / (pel_gate_curve[i].f - pel_gate_curve[i - 1].f);
            return pel_gate_curve[i - 1].q + t * (pel_gate_curve[i].q - pel_gate_curve[i - 1].q);
        }
    }
    return pel_gate_curve[n - 1].q;
}

/* Sum one band's slices of a per-band accumulator. */
static uint64_t pel_band_sum(const uint32_t *v, int b)
{
    uint64_t sum = 0;
    int sl;

    for (sl = 0; sl < PEL_GRAIN_SLICES; sl++)
        sum += v[b * PEL_GRAIN_SLICES + sl];
    return sum;
}

/* Dual gate (ADR-0229): does band b's default-gate measurement lie on the
 * white Gaussian grain curve while the gate drops part of the band? */
static int pel_band_takes_wide(const PelorusGrainBuf *acc, int b, double edge_thr)
{
    uint64_t n = pel_band_sum(acc->cnt, b), total = pel_band_sum(acc->total, b);
    double f, q;

    if (n < PEL_GRAIN_GATE_MINCNT || total == 0 || edge_thr <= 0.0)
        return 0;
    f = (double)n / (double)total;
    if (f >= PEL_GRAIN_GATE_FMAX)
        return 0;
    q = sqrt((double)pel_band_sum(acc->sumsq, b) / PEL_GRAIN_SUMSQ_GS / (double)n) / edge_thr;
    return q >= PEL_GRAIN_GATE_RATIO * pel_gate_curve_q(f);
}

/* Lag-1 coefficient: the mean product over the default-gate pixels, normalized
 * by the default-gate residual variance it was measured against. */
static float pel_lag1(const PelorusGrainBuf *acc, double var_def)
{
    double corr_sum = 0.0;
    uint64_t corr_n = 0;
    int sl;

    for (sl = 0; sl < PEL_GRAIN_SLICES; sl++) {
        corr_sum += acc->corr[sl];
        corr_n += acc->corr_cnt[sl];
    }
    if (corr_n == 0 || var_def <= 0.0)
        return 0.0f;
    /* mean lag-1 product = (Σ biased / CORR_GS - n*BIAS) / n */
    return (float)av_clipd(
        (corr_sum / PEL_GRAIN_CORR_GS - (double)corr_n * PEL_GRAIN_CORR_BIAS) / (double)corr_n / var_def,
        -1.0, 1.0);
}

static void reduce_stats(const PelorusGrainEstimateVulkanContext *s, const PelorusGrainBuf *acc,
                         PelorusGrainStats *st)
{
    double var_sum = 0.0, def_sum = 0.0;
    uint64_t def_n = 0;
    int b;

    memset(st, 0, sizeof(*st));

    for (b = 0; b < PEL_GRAIN_BANDS; b++) {
        int wide = s->adapt && pel_band_takes_wide(acc, b, s->edge_thr);
        uint64_t n = pel_band_sum(wide ? acc->cnt_w : acc->cnt, b);
        double sq = (double)pel_band_sum(wide ? acc->sumsq_w : acc->sumsq, b) / PEL_GRAIN_SUMSQ_GS;

        def_sum += (double)pel_band_sum(acc->sumsq, b) / PEL_GRAIN_SUMSQ_GS;
        def_n += pel_band_sum(acc->cnt, b);
        st->wide_mask |= wide << b;
        st->band_cnt[b] = n;
        st->flat_total += n;
        if (n > 0) {
            st->band_rms[b] = (float)sqrt(sq / (double)n);
            var_sum += sq;
        }
    }

    if (st->flat_total > 0)
        st->rms_all = (float)sqrt(var_sum / (double)st->flat_total);
    st->lag1 = pel_lag1(acc, def_n > 0 ? def_sum / (double)def_n : 0.0);
}

/* Invert pel_aom_calib: the AR coefficient a whose calibrated residual lag-1
 * correlation equals the measured one (linear between rows, clamped to the
 * table ends) and the residual gain of that model. */
static void pel_aom_invert(float rho, float *a, float *gain)
{
    size_t i = 1;
    float t;

    while (i < FF_ARRAY_ELEMS(pel_aom_calib) - 1 && rho > pel_aom_calib[i].rho_r)
        i++;
    t = (rho - pel_aom_calib[i - 1].rho_r) / (pel_aom_calib[i].rho_r - pel_aom_calib[i - 1].rho_r);
    t = av_clipf(t, 0.0f, 1.0f);
    *a = (float)pel_aom_calib[i - 1].a + t * (float)(pel_aom_calib[i].a - pel_aom_calib[i - 1].a);
    *gain = pel_aom_calib[i - 1].gain_r + t * (pel_aom_calib[i].gain_r - pel_aom_calib[i - 1].gain_r);
}

/* Map the measured per-band stats to AV1 AOM film-grain params (ADR-0227). The
 * measured lag-1 correlation selects a symmetric lag-1 AR model (up and left
 * taps equal: the estimator measures horizontal correlation only, so the grain
 * is taken as isotropic). The piecewise scaling function y_points[value,
 * scaling]: each populated band contributes a (value, scaling) point — value =
 * band centre on [0,255], scaling = band RMS over that model's calibrated gain,
 * times `strength` (1 = reproduce the measured grain). Points must be strictly
 * increasing in value (AV1), so we emit one per band centre (already
 * increasing) and skip empty bands. */
static void map_aom(const PelorusGrainEstimateVulkanContext *s, const PelorusGrainStats *st,
                    PelorusFilmGrainSection *g)
{
    int b, np = 0;
    int8_t ar;
    float a, gain;

    g->seed = 0;           /* deterministic; a real seed is set by the synth stage */
    g->scaling_shift = 8;  /* AV1 [8,11] */
    g->ar_coeff_lag = 1;   /* the lag-1 measurement fits a lag-1 model */
    g->ar_coeff_shift = 6; /* AV1 [6,9]; pel_aom_calib is calibrated at 6 */
    g->grain_scale_shift = 0;
    g->chroma_scaling_from_luma = 1; /* derive chroma from luma (v0.x) */
    g->overlap_flag = 1;
    g->limit_output_range = 0;
    g->grain_model = (uint8_t)s->grain_model;

    /* H.274 mode scalars: the SMPTE RDD 5 profile FFmpeg synthesizes (model 0 =
     * frequency filtering, additive blending, log2_scale_factor 2). The model
     * values themselves go out as frame metadata (map_h274, ADR-0161). */
    g->h274_model_id = PEL_GRAIN_H274_MODEL_ID;
    g->h274_blending_mode = 0; /* additive */
    g->h274_log2_scale = PEL_GRAIN_H274_LOG2_SCALE;

    pel_aom_invert(st->lag1, &a, &gain);
    for (b = 0; b < PEL_GRAIN_BANDS && np < 14; b++) {
        int value, scaling;
        if (st->band_cnt[b] == 0)
            continue;
        /* band centre on [0,255] */
        value = av_clip((b * 255 + 128) / PEL_GRAIN_BANDS, 0, 255);
        /* RMS residual in 8-bit codes over the model's gain -> [0,255] */
        scaling = av_clip(
            (int)lrintf(st->band_rms[b] * 255.0f / gain * (float)s->strength), 0, 255);
        if (scaling == 0)
            continue; /* no measurable grain in this band */
        g->y_points[np][0] = (uint8_t)value;
        g->y_points[np][1] = (uint8_t)scaling;
        np++;
    }
    g->num_y_points = np;
    g->apply = np > 0 ? 1 : 0;

    /* AV1 lag-1 order (generate_grain_y_c, libavcodec/aom_film_grain_template.c):
     * [0] up-left, [1] up, [2] up-right, [3] left. The calibrated model sets the up and left taps to a / 64. */
    memset(g->ar_coeffs_y, 0, sizeof(g->ar_coeffs_y));
    ar = (int8_t)av_clip((int)lrintf(a), 0, 127);
    g->ar_coeffs_y[1] = ar;
    g->ar_coeffs_y[3] = ar;
}

/* Map the measurement to H.274 model 0 (ADR-0161). The cutoff is the one whose
 * calibrated residual lag-1 correlation is nearest the measured one; the scale
 * then makes FFmpeg's grain at that cutoff reproduce the measured pooled
 * residual RMS. The result reproduces white and RDD 5-like grain; grain with
 * more low-frequency energy than a band-limited pattern of the same residual
 * correlation comes back with a lower total standard deviation. */
static void map_h274(const PelorusGrainStats *st, PelorusH274Model *m)
{
    size_t i, best = 0;
    float best_d = fabsf(pel_h274_calib[0].rho_r - st->lag1);

    for (i = 1; i < FF_ARRAY_ELEMS(pel_h274_calib); i++) {
        float d = fabsf(pel_h274_calib[i].rho_r - st->lag1);
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    m->cutoff = pel_h274_calib[best].cutoff;
    m->scale = av_clip((int)lrintf(st->rms_all * 255.0f / pel_h274_calib[best].gain_r), 0, 255);
}

/* Attach a native AV_FRAME_DATA_FILM_GRAIN_PARAMS (AV1) so a downstream FFmpeg
 * AV1 encoder honours the estimate with no Pelorus-specific BSF (the analyze
 * filter took the same standard-side-data-channel path with ROI). */
static int attach_native_aom(PelorusGrainEstimateVulkanContext *s, AVFrame *frame,
                             const PelorusFilmGrainSection *g)
{
    const AVPixFmtDescriptor *d = av_pix_fmt_desc_get(s->vkctx.output_format);
    AVFilmGrainParams *fgp;
    AVFilmGrainAOMParams *aom;
    int i;

    fgp = av_film_grain_params_create_side_data(frame);
    if (!fgp)
        return AVERROR(ENOMEM);

    fgp->type = AV_FILM_GRAIN_PARAMS_AV1;
    fgp->seed = g->seed;
    fgp->width = frame->width;
    fgp->height = frame->height;
    if (d) {
        fgp->subsampling_x = d->log2_chroma_w;
        fgp->subsampling_y = d->log2_chroma_h;
        fgp->bit_depth_luma = d->comp[0].depth;
        fgp->bit_depth_chroma = d->comp[0].depth;
    }

    aom = &fgp->codec.aom;
    aom->num_y_points = g->num_y_points;
    for (i = 0; i < g->num_y_points && i < 14; i++) {
        aom->y_points[i][0] = g->y_points[i][0];
        aom->y_points[i][1] = g->y_points[i][1];
    }
    aom->chroma_scaling_from_luma = g->chroma_scaling_from_luma;
    aom->scaling_shift = g->scaling_shift;
    aom->ar_coeff_lag = g->ar_coeff_lag;
    aom->ar_coeff_shift = g->ar_coeff_shift;
    aom->grain_scale_shift = g->grain_scale_shift;
    aom->overlap_flag = g->overlap_flag;
    aom->limit_output_range = g->limit_output_range;
    memcpy(aom->ar_coeffs_y, g->ar_coeffs_y, sizeof(aom->ar_coeffs_y));
    return 0;
}

/* Pack the PEL_SEC_FILMGRAIN interop section and attach it to the frame. */
static int attach_interop(PelorusGrainEstimateVulkanContext *s, AVFrame *frame,
                          const PelorusFilmGrainSection *g)
{
    const AVPixFmtDescriptor *d = av_pix_fmt_desc_get(s->vkctx.output_format);
    PelorusSideData meta;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    AVBufferRef *buf;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = (uint64_t)frame->pts;
    meta.bit_depth = d ? (uint8_t)d->comp[0].depth : 0;
    meta.plane_layout = (d && d->log2_chroma_w == 0 && d->log2_chroma_h == 0) ?
                            PEL_LAYOUT_444 :
                            ((d && d->log2_chroma_h == 0) ? PEL_LAYOUT_422 : PEL_LAYOUT_420);
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'G');

    sec.id = PEL_SEC_FILMGRAIN;
    sec.data = g;
    sec.size = (uint32_t)sizeof(*g);

    if (pel_blob_pack(&meta, &sec, 1, &blob, &len) != PEL_OK || !blob)
        return AVERROR(ENOMEM);

    buf = av_buffer_create(blob, len, pel_sd_free, NULL, 0);
    if (!buf) {
        pel_blob_free(blob);
        return AVERROR(ENOMEM);
    }
    if (!av_frame_new_side_data_from_buf(frame, AV_FRAME_DATA_SEI_UNREGISTERED, buf)) {
        av_buffer_unref(&buf);
        return AVERROR(ENOMEM);
    }
    return 0;
}

/* Record one buffer barrier on the accumulator SSBO, optionally together with
 * the input image barriers. */
static void buffer_barrier(FFVulkanFunctions *vk, FFVkExecContext *exec, const FFVkBuffer *buf_vk,
                           VkPipelineStageFlags2 src_stage, VkAccessFlags2 src_access,
                           VkPipelineStageFlags2 dst_stage, VkAccessFlags2 dst_access,
                           const VkImageMemoryBarrier2 *img_bar, int nb_img_bar)
{
    vk->CmdPipelineBarrier2(exec->buf,
                            &(VkDependencyInfo){
                                .sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO,
                                .pImageMemoryBarriers = img_bar,
                                .imageMemoryBarrierCount = nb_img_bar,
                                .pBufferMemoryBarriers =
                                    &(VkBufferMemoryBarrier2){
                                        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2,
                                        .srcStageMask = src_stage,
                                        .srcAccessMask = src_access,
                                        .dstStageMask = dst_stage,
                                        .dstAccessMask = dst_access,
                                        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                                        .buffer = buf_vk->buf,
                                        .size = buf_vk->size,
                                        .offset = 0,
                                    },
                                .bufferMemoryBarrierCount = 1,
                            });
}

/* Record the accumulator clear, the estimator dispatch and the host-read
 * barrier into an execution context that already holds the input frame. */
static int record_estimator(PelorusGrainEstimateVulkanContext *s, FFVkExecContext *exec,
                            AVFrame *in, FFVkBuffer *buf_vk)
{
    FFVulkanContext *vkctx = &s->vkctx;
    FFVulkanFunctions *vk = &vkctx->vkfn;
    VkImageView views[AV_NUM_DATA_POINTERS];
    VkImageMemoryBarrier2 img_bar[8];
    int nb_img_bar = 0;
    int err;
    struct {
        int32_t width;
        int32_t height;
        float edge_thr;
        float sample_scale;
    } pc = {in->width, in->height, (float)s->edge_thr, pel_vk_sample_scale(vkctx->input_format)};

    err = ff_vk_create_imageviews(vkctx, exec, views, in, FF_VK_REP_FLOAT);
    if (err < 0)
        return err;
    ff_vk_shader_update_img_array(vkctx, exec, &s->shd, in, views, 0, 0, VK_IMAGE_LAYOUT_GENERAL,
                                  VK_NULL_HANDLE);
    ff_vk_frame_barrier(vkctx, exec, in, img_bar, &nb_img_bar, VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
                        VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT, VK_ACCESS_SHADER_READ_BIT,
                        VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED);

    /* zero the accumulators */
    buffer_barrier(vk, exec, buf_vk, VK_PIPELINE_STAGE_2_NONE, 0, VK_PIPELINE_STAGE_2_TRANSFER_BIT,
                   VK_ACCESS_TRANSFER_WRITE_BIT, NULL, 0);
    vk->CmdFillBuffer(exec->buf, buf_vk->buf, 0, buf_vk->size, 0x0);
    buffer_barrier(vk, exec, buf_vk, VK_PIPELINE_STAGE_2_TRANSFER_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,
                   VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                   VK_ACCESS_2_SHADER_STORAGE_READ_BIT | VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                   img_bar, nb_img_bar);

    err = ff_vk_shader_update_desc_buffer(vkctx, exec, &s->shd, 0, 1, 0, buf_vk, 0, buf_vk->size,
                                          VK_FORMAT_UNDEFINED);
    if (err < 0)
        return err;
    ff_vk_exec_bind_shader(vkctx, exec, &s->shd);
    ff_vk_shader_update_push_const(vkctx, exec, &s->shd, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(pc),
                                   &pc);

    vk->CmdDispatch(exec->buf, FFALIGN(in->width, s->shd.lg_size[0]) / s->shd.lg_size[0],
                    FFALIGN(in->height, s->shd.lg_size[1]) / s->shd.lg_size[1], s->shd.lg_size[2]);

    buffer_barrier(vk, exec, buf_vk, VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
                   VK_ACCESS_2_SHADER_STORAGE_READ_BIT | VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT,
                   VK_PIPELINE_STAGE_2_HOST_BIT, VK_ACCESS_HOST_READ_BIT, NULL, 0);
    return 0;
}

/* Run the estimator on `in` and wait for the accumulators in `buf_vk`. On any
 * failure before submission the execution's dependencies are discarded; after
 * a successful submission ff_vk_exec_wait() releases them. */
static int run_estimator(PelorusGrainEstimateVulkanContext *s, AVFrame *in, FFVkBuffer *buf_vk)
{
    FFVulkanContext *vkctx = &s->vkctx;
    FFVkExecContext *exec = ff_vk_exec_get(vkctx, &s->e);
    int err;

    ff_vk_exec_start(vkctx, exec);
    err = ff_vk_exec_add_dep_frame(vkctx, exec, in, VK_PIPELINE_STAGE_2_NONE,
                                   VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT);
    if (err >= 0)
        err = record_estimator(s, exec, in, buf_vk);
    if (err >= 0)
        err = ff_vk_exec_submit(vkctx, exec);
    if (err < 0) {
        ff_vk_exec_discard_deps(vkctx, exec);
        return err;
    }
    ff_vk_exec_wait(vkctx, exec);
    return 0;
}

/* Emit the grain estimate as frame metadata. grain_sigma is the peak RMS
 * residual over populated bands (measured only on edge-gated locally-flat
 * pixels, so structure is excluded and what survives is grain stddev),
 * grain_flat the fraction it was measured over (the estimate's confidence),
 * and grain_lag1 the residual lag-1 correlation. With model=h274 the H.274
 * model-0 values follow, named after the pelorus_fgs options they feed
 * (model_id, log2_scale, scale_y, cutoff_h, cutoff_v; ADR-0161). */
static void emit_metadata(const PelorusGrainEstimateVulkanContext *s, AVFrame *in,
                          const PelorusGrainStats *st)
{
    float gs = 0.0f;
    int b;

    for (b = 0; b < PEL_GRAIN_BANDS; b++)
        if (st->band_cnt[b] > 0 && st->band_rms[b] > gs)
            gs = st->band_rms[b];
    pel_set_meta_f(in, "lavfi.pelorus.grain_sigma", gs);
    pel_set_meta_f(in, "lavfi.pelorus.grain_flat",
                   (float)((double)st->flat_total / ((double)in->width * (double)in->height)));
    pel_set_meta_f(in, "lavfi.pelorus.grain_lag1", st->lag1);
    pel_set_meta_i(in, "lavfi.pelorus.grain_wide_bands", st->wide_mask);

    if (s->grain_model == PEL_GRAIN_H274) {
        PelorusH274Model hm;
        map_h274(st, &hm);
        pel_set_meta_i(in, "lavfi.pelorus.h274_model_id", PEL_GRAIN_H274_MODEL_ID);
        pel_set_meta_i(in, "lavfi.pelorus.h274_log2_scale", PEL_GRAIN_H274_LOG2_SCALE);
        pel_set_meta_i(in, "lavfi.pelorus.h274_scale_y", hm.scale);
        pel_set_meta_i(in, "lavfi.pelorus.h274_cutoff_h", hm.cutoff);
        pel_set_meta_i(in, "lavfi.pelorus.h274_cutoff_v", hm.cutoff);
    }
}

/* Derive the estimate from the read-back accumulators and attach it. */
static int attach_estimate(PelorusGrainEstimateVulkanContext *s, AVFrame *in,
                           const PelorusGrainBuf *acc)
{
    PelorusGrainStats st;
    PelorusFilmGrainSection g;
    int err;

    reduce_stats(s, acc, &st);
    emit_metadata(s, in, &st);

    memset(&g, 0, sizeof(g));
    map_aom(s, &st, &g);

    err = attach_interop(s, in, &g);
    if (err >= 0 && s->attach_native && g.apply)
        err = attach_native_aom(s, in, &g);
    return err;
}

static int grain_estimate_vulkan_filter_frame(AVFilterLink *link, AVFrame *in)
{
    AVFilterContext *ctx = link->dst;
    PelorusGrainEstimateVulkanContext *s = ctx->priv;
    AVBufferRef *buf = NULL;
    int err = 0;

    if (!s->initialized)
        err = init_filter(ctx);
    if (err >= 0)
        err = ff_vk_get_pooled_buffer(
            &s->vkctx, &s->stat_buf_pool, &buf,
            VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, NULL,
            sizeof(PelorusGrainBuf),
            VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT | VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (err >= 0) {
        FFVkBuffer *buf_vk = (FFVkBuffer *)buf->data;
        err = run_estimator(s, in, buf_vk);
        if (err >= 0)
            err = attach_estimate(s, in, (const PelorusGrainBuf *)buf_vk->mapped_mem);
    }
    av_buffer_unref(&buf);
    if (err < 0) {
        av_frame_free(&in);
        return err;
    }
    return ff_filter_frame(ctx->outputs[0], in);
}

static void grain_estimate_vulkan_uninit(AVFilterContext *avctx)
{
    PelorusGrainEstimateVulkanContext *s = avctx->priv;
    FFVulkanContext *vkctx = &s->vkctx;

    ff_vk_exec_pool_free(vkctx, &s->e);
    ff_vk_shader_free(vkctx, &s->shd);
    av_buffer_pool_uninit(&s->stat_buf_pool);
    ff_vk_uninit(&s->vkctx);
    s->initialized = 0;
}

#define OFFSET(x) offsetof(PelorusGrainEstimateVulkanContext, x)
#define FLAGS (AV_OPT_FLAG_FILTERING_PARAM | AV_OPT_FLAG_VIDEO_PARAM)
static const AVOption pelorus_grain_estimate_vulkan_options[] = {
    {"edge",
     "3x3 range above which a pixel is an edge (excluded from grain)",
     OFFSET(edge_thr),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 0.06},
     0.0,
     1.0,
     FLAGS},
    {"strength",
     "multiplies the calibrated AV1 scaling function (1 = reproduce the "
     "measured grain)",
     OFFSET(strength),
     AV_OPT_TYPE_DOUBLE,
     {.dbl = 1.0},
     0.0,
     64.0,
     FLAGS},
    {"model",
     "FGS model the estimate targets",
     OFFSET(grain_model),
     AV_OPT_TYPE_INT,
     {.i64 = PEL_GRAIN_AOM},
     PEL_GRAIN_AOM,
     PEL_GRAIN_H274,
     FLAGS,
     .unit = "model"},
    {"aom",
     "AV1 (AOM) film-grain params",
     0,
     AV_OPT_TYPE_CONST,
     {.i64 = PEL_GRAIN_AOM},
     0,
     0,
     FLAGS,
     .unit = "model"},
    {"h274",
     "HEVC/VVC H.274 FGC SEI scalars",
     0,
     AV_OPT_TYPE_CONST,
     {.i64 = PEL_GRAIN_H274},
     0,
     0,
     FLAGS,
     .unit = "model"},
    {"native",
     "also attach native AV_FRAME_DATA_FILM_GRAIN_PARAMS (AV1)",
     OFFSET(attach_native),
     AV_OPT_TYPE_BOOL,
     {.i64 = 1},
     0,
     1,
     FLAGS},
    {"adapt",
     "measure grain-consistent bands at the wide gate (2 x edge) to recover heavy grain",
     OFFSET(adapt),
     AV_OPT_TYPE_BOOL,
     {.i64 = 1},
     0,
     1,
     FLAGS},
    {NULL}};

AVFILTER_DEFINE_CLASS(pelorus_grain_estimate_vulkan);

/* config_props: refuse frames above the proven accumulator bound, then the
 * shared YUV format check. */
static int grain_estimate_vulkan_config_input(AVFilterLink *inlink)
{
    const long long pixels = (long long)inlink->w * (long long)inlink->h;

    if (pixels > PEL_GRAIN_MAX_PIXELS) {
        av_log(inlink->dst, AV_LOG_ERROR,
               "%dx%d (%lld pixels) exceeds the DCI 8K limit of %lld pixels; the "
               "estimator's 32-bit accumulators are only proven up to it\n",
               inlink->w, inlink->h, pixels, PEL_GRAIN_MAX_PIXELS);
        return AVERROR(EINVAL);
    }
    return pel_vk_config_input_yuv(inlink);
}

static const AVFilterPad pelorus_grain_estimate_vulkan_inputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .filter_frame = &grain_estimate_vulkan_filter_frame,
        .config_props = &grain_estimate_vulkan_config_input,
    },
};

static const AVFilterPad pelorus_grain_estimate_vulkan_outputs[] = {
    {
        .name = "default",
        .type = AVMEDIA_TYPE_VIDEO,
        .config_props = &ff_vk_filter_config_output,
    },
};

const FFFilter ff_vf_pelorus_grain_estimate_vulkan = {
    .p.name = "pelorus_grain_estimate_vulkan",
    .p.description = NULL_IF_CONFIG_SMALL("Pelorus film-grain estimator (Vulkan)"),
    .p.priv_class = &pelorus_grain_estimate_vulkan_class,
    .p.flags = AVFILTER_FLAG_HWDEVICE,
    .priv_size = sizeof(PelorusGrainEstimateVulkanContext),
    .init = &ff_vk_filter_init,
    .uninit = &grain_estimate_vulkan_uninit,
    FILTER_INPUTS(pelorus_grain_estimate_vulkan_inputs),
    FILTER_OUTPUTS(pelorus_grain_estimate_vulkan_outputs),
    FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN),
    .flags_internal = FF_FILTER_FLAG_HWFRAME_AWARE,
};
