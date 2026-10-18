/*
 * Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "libavcodec/aom_film_grain.h"
#include "libavcodec/h274.h"
#include "libavutil/film_grain_params.h"
#include "libavutil/frame.h"
#include "libavutil/imgutils.h"

#include "pelorus/grain_convert.h"
#include "pelorus/interop.h"

enum PlantMode {
    PLANT_NONE = 0,
    PLANT_SCALE = 1,
    PLANT_CUTOFF = 2,
    PLANT_SHIFT = 3,
};

typedef struct Stats {
    double sigma;
    double rho_h;
    double rho_v;
} Stats;

typedef struct Context {
    enum PlantMode plant;
    int failures;
    AVFrame *in;
    AVFrame *out_aom;
    AVFrame *out_h274;
    AVFilmGrainParams *params_aom;
    AVFilmGrainParams *params_h274;
} Context;

static void setup_realistic_aom(PelorusFilmGrainSection *aom)
{
    (void)memset(aom, 0, sizeof(*aom));
    aom->grain_model = PEL_GRAIN_AOM;
    aom->apply = 1;
    aom->seed = 42;
    aom->ar_coeff_lag = 1;
    aom->ar_coeff_shift = 7;
    aom->grain_scale_shift = 0;
    aom->scaling_shift = 10;
    aom->num_y_points = 3;
    aom->y_points[0][0] = 20;
    aom->y_points[0][1] = 10;
    aom->y_points[1][0] = 128;
    aom->y_points[1][1] = 40;
    aom->y_points[2][0] = 230;
    aom->y_points[2][1] = 20;
    aom->ar_coeffs_y[0] = 10;
    aom->ar_coeffs_y[1] = 30;
    aom->ar_coeffs_y[2] = 0;
    aom->ar_coeffs_y[3] = 40;
    aom->chroma_scaling_from_luma = 0;
    aom->num_uv_points[0] = 2;
    aom->uv_points[0][0][0] = 30;
    aom->uv_points[0][0][1] = 15;
    aom->uv_points[0][1][0] = 150;
    aom->uv_points[0][1][1] = 35;
    aom->num_uv_points[1] = 2;
    aom->uv_points[1][0][0] = 40;
    aom->uv_points[1][0][1] = 20;
    aom->uv_points[1][1][0] = 160;
    aom->uv_points[1][1][1] = 30;
    aom->ar_coeffs_uv[0][0] = 5;
    aom->ar_coeffs_uv[0][1] = 20;
    aom->ar_coeffs_uv[0][2] = 0;
    aom->ar_coeffs_uv[0][3] = 25;
    aom->ar_coeffs_uv[1][0] = 5;
    aom->ar_coeffs_uv[1][1] = 20;
    aom->ar_coeffs_uv[1][2] = 0;
    aom->ar_coeffs_uv[1][3] = 25;
}

static void setup_coinciding_h274(PelorusH274GrainParams *h274)
{
    (void)memset(h274, 0, sizeof(*h274));
    h274->model_id = 0;
    h274->blending_mode_id = 0;
    h274->log2_scale_factor = 3;
    h274->component_model_present[0] = 1;
    h274->num_intensity_intervals[0] = 1;
    h274->num_model_values[0] = 3;
    h274->intensity_interval_lower_bound[0][0] = 0;
    h274->intensity_interval_upper_bound[0][0] = 255;
    h274->comp_model_value[0][0][0] = 40;
    h274->comp_model_value[0][0][1] = 8;
    h274->comp_model_value[0][0][2] = 8;
}

/* The memcpy calls below rely on identical array layouts. */
_Static_assert(sizeof(((AVFilmGrainAOMParams *)0)->ar_coeffs_y) ==
                   sizeof(((PelorusFilmGrainSection *)0)->ar_coeffs_y),
               "ar_coeffs_y layout");
_Static_assert(sizeof(((AVFilmGrainAOMParams *)0)->ar_coeffs_uv) ==
                   sizeof(((PelorusFilmGrainSection *)0)->ar_coeffs_uv),
               "ar_coeffs_uv layout");
_Static_assert(sizeof(((AVFilmGrainH274Params *)0)->comp_model_value) ==
                   sizeof(((PelorusH274GrainParams *)0)->comp_model_value),
               "comp_model_value layout");
_Static_assert(sizeof(((AVFilmGrainH274Params *)0)->intensity_interval_lower_bound) ==
                   sizeof(((PelorusH274GrainParams *)0)->intensity_interval_lower_bound),
               "interval bound layout");

static void map_aom_params(const PelorusFilmGrainSection *aom, AVFilmGrainParams *out)
{
    (void)memset(out, 0, sizeof(*out));
    out->type = AV_FILM_GRAIN_PARAMS_AV1;
    out->seed = aom->seed;
    out->width = 512;
    out->height = 512;
    out->subsampling_x = 1;
    out->subsampling_y = 1;
    out->color_range = AVCOL_RANGE_UNSPECIFIED;
    out->bit_depth_luma = 8;
    out->bit_depth_chroma = 8;

    AVFilmGrainAOMParams *a = &out->codec.aom;
    /* Piecewise-linear luma scaling curve points */
    a->num_y_points = aom->num_y_points;
    for (int i = 0; i < 14; i++) {
        a->y_points[i][0] = aom->y_points[i][0];
        a->y_points[i][1] = aom->y_points[i][1];
    }
    /* Chroma scaling mode and piecewise-linear chroma points */
    a->chroma_scaling_from_luma = aom->chroma_scaling_from_luma;
    a->num_uv_points[0] = aom->num_uv_points[0];
    a->num_uv_points[1] = aom->num_uv_points[1];
    for (int uv = 0; uv < 2; uv++) {
        for (int i = 0; i < 10; i++) {
            a->uv_points[uv][i][0] = aom->uv_points[uv][i][0];
            a->uv_points[uv][i][1] = aom->uv_points[uv][i][1];
        }
    }
    /* Quantization and causal AR filter tap parameters */
    a->scaling_shift = aom->scaling_shift;
    a->ar_coeff_lag = aom->ar_coeff_lag;
    (void)memcpy(a->ar_coeffs_y, aom->ar_coeffs_y, sizeof(a->ar_coeffs_y));
    (void)memcpy(a->ar_coeffs_uv, aom->ar_coeffs_uv, sizeof(a->ar_coeffs_uv));
    a->ar_coeff_shift = aom->ar_coeff_shift;
    a->grain_scale_shift = aom->grain_scale_shift;
    /* Chroma-from-luma coupling coordinates and block flags */
    a->uv_mult[0] = aom->uv_mult[0];
    a->uv_mult[1] = aom->uv_mult[1];
    a->uv_mult_luma[0] = aom->uv_mult_luma[0];
    a->uv_mult_luma[1] = aom->uv_mult_luma[1];
    a->uv_offset[0] = aom->uv_offset[0];
    a->uv_offset[1] = aom->uv_offset[1];
    a->overlap_flag = aom->overlap_flag;
    a->limit_output_range = aom->limit_output_range;
}

static void map_h274_params(const PelorusH274GrainParams *h274, uint64_t seed,
                            AVFilmGrainParams *out)
{
    (void)memset(out, 0, sizeof(*out));
    out->type = AV_FILM_GRAIN_PARAMS_H274;
    out->seed = seed;
    out->width = 512;
    out->height = 512;
    out->subsampling_x = 1;
    out->subsampling_y = 1;
    out->color_range = AVCOL_RANGE_UNSPECIFIED;
    out->bit_depth_luma = 8;
    out->bit_depth_chroma = 8;

    AVFilmGrainH274Params *h = &out->codec.h274;
    /* Model 0 frequency filtering, additive blending */
    h->model_id = h274->model_id;
    h->blending_mode_id = h274->blending_mode_id;
    h->log2_scale_factor = h274->log2_scale_factor;
    for (int c = 0; c < 3; c++) {
        h->component_model_present[c] = h274->component_model_present[c];
        h->num_intensity_intervals[c] = h274->num_intensity_intervals[c];
        h->num_model_values[c] = h274->num_model_values[c];
        (void)memcpy(h->intensity_interval_lower_bound[c], h274->intensity_interval_lower_bound[c],
                     256);
        (void)memcpy(h->intensity_interval_upper_bound[c], h274->intensity_interval_upper_bound[c],
                     256);
        (void)memcpy(h->comp_model_value[c], h274->comp_model_value[c],
                     sizeof(h->comp_model_value[c]));
    }
}

static void fill_frame(AVFrame *f, uint8_t y_val, uint8_t u_val, uint8_t v_val)
{
    for (int y = 0; y < f->height; y++) {
        (void)memset(f->data[0] + (ptrdiff_t)y * f->linesize[0], y_val, (size_t)f->width);
    }
    for (int y = 0; y < f->height / 2; y++) {
        (void)memset(f->data[1] + (ptrdiff_t)y * f->linesize[1], u_val, (size_t)(f->width / 2));
        (void)memset(f->data[2] + (ptrdiff_t)y * f->linesize[2], v_val, (size_t)(f->width / 2));
    }
}

static void measure_plane(const AVFrame *out, const AVFrame *in, int plane, Stats *s)
{
    const int w = (plane == 0) ? out->width : out->width / 2;
    const int h = (plane == 0) ? out->height : out->height / 2;
    const int border = 16;
    const int x0 = border;
    const int x1 = w - border;
    const int y0 = border;
    const int y1 = h - border;
    const int crop_w = x1 - x0;
    const int crop_h = y1 - y0;
    const int count = crop_w * crop_h;

    const uint8_t *out_p = out->data[plane];
    const uint8_t *in_p = in->data[plane];
    const int stride_out = out->linesize[plane];
    const int stride_in = in->linesize[plane];

    double sum = 0.0;
    for (int y = y0; y < y1; y++) {
        for (int x = x0; x < x1; x++) {
            sum += (double)out_p[y * stride_out + x] - (double)in_p[y * stride_in + x];
        }
    }
    const double mean = sum / (double)count;

    double sum_sq = 0.0;
    double sum_h = 0.0;
    double sum_v = 0.0;
    for (int y = y0; y < y1; y++) {
        for (int x = x0; x < x1; x++) {
            const double d0 =
                ((double)out_p[y * stride_out + x] - (double)in_p[y * stride_in + x]) - mean;
            sum_sq += d0 * d0;
            if (x + 1 < x1) {
                const double d_r = ((double)out_p[y * stride_out + (x + 1)] -
                                    (double)in_p[y * stride_in + (x + 1)]) -
                                   mean;
                sum_h += d0 * d_r;
            }
            if (y + 1 < y1) {
                const double d_d = ((double)out_p[(y + 1) * stride_out + x] -
                                    (double)in_p[(y + 1) * stride_in + x]) -
                                   mean;
                sum_v += d0 * d_d;
            }
        }
    }

    const double var = sum_sq / (double)count;
    const double cov_h = sum_h / (double)((crop_w - 1) * crop_h);
    const double cov_v = sum_v / (double)(crop_w * (crop_h - 1));
    s->sigma = sqrt(var);
    s->rho_h = (var > 1e-9) ? (cov_h / var) : 0.0;
    s->rho_v = (var > 1e-9) ? (cov_v / var) : 0.0;
}

static void check_assert(Context *ctx, const char *name, double measured, double bound)
{
    if (measured <= bound) {
        (void)printf("[PASS] %s: measured=%.5f, bound=%.5f\n", name, measured, bound);
    } else {
        (void)printf("[FAIL] %s: measured=%.5f, bound=%.5f\n", name, measured, bound);
        ctx->failures++;
    }
}

static int test_determinism(Context *ctx)
{
    PelorusFilmGrainSection aom;
    setup_realistic_aom(&aom);
    map_aom_params(&aom, ctx->params_aom);
    fill_frame(ctx->in, 128, 128, 128);

    if (ff_aom_apply_film_grain(ctx->out_aom, ctx->in, ctx->params_aom) < 0) {
        return 2;
    }
    const size_t plane_bytes = (size_t)ctx->out_aom->linesize[0] * (size_t)ctx->out_aom->height;
    uint8_t *copy = (uint8_t *)malloc(plane_bytes);
    if (!copy) {
        return 2;
    }
    (void)memcpy(copy, ctx->out_aom->data[0], plane_bytes);

    if (ff_aom_apply_film_grain(ctx->out_aom, ctx->in, ctx->params_aom) < 0) {
        free(copy);
        return 2;
    }
    const int same = (memcmp(copy, ctx->out_aom->data[0], plane_bytes) == 0);
    free(copy);
    check_assert(ctx, "determinism_byte_identical_aom", same ? 0.0 : 1.0, 0.0);
    return 0;
}

static void apply_plants(Context *ctx, PelorusFilmGrainSection *aom, PelorusH274GrainParams *h274)
{
    if (ctx->plant == PLANT_SCALE) {
        /* Plant: multiply H.274 scale by 2 */
        for (int c = 0; c < 3; c++) {
            for (int i = 0; i < h274->num_intensity_intervals[c]; i++) {
                h274->comp_model_value[c][i][0] = (int16_t)(h274->comp_model_value[c][i][0] * 2);
            }
        }
    } else if (ctx->plant == PLANT_CUTOFF) {
        /* Plant: force H.274 frequency cutoffs to 2/2 */
        for (int c = 0; c < 3; c++) {
            for (int i = 0; i < h274->num_intensity_intervals[c]; i++) {
                h274->comp_model_value[c][i][1] = 2;
                h274->comp_model_value[c][i][2] = 2;
            }
        }
    } else if (ctx->plant == PLANT_SHIFT) {
        /* Plant: increase AV1 scaling_shift by 1 */
        aom->scaling_shift += 1;
    }
}

static int test_fixture_a_luma(Context *ctx, const PelorusGrainDescriptor *desc_a,
                               const PelorusGrainDescriptor *desc_h)
{
    const int levels[3] = {64, 128, 192};
    for (int i = 0; i < 3; i++) {
        const int lvl = levels[i];
        fill_frame(ctx->in, (uint8_t)lvl, 128, 128);

        if (ff_aom_apply_film_grain(ctx->out_aom, ctx->in, ctx->params_aom) < 0 ||
            ff_h274_apply_film_grain(ctx->out_h274, ctx->in, ctx->params_h274) < 0) {
            return 2;
        }

        Stats s_aom;

        Stats s_h274;
        measure_plane(ctx->out_aom, ctx->in, 0, &s_aom);
        measure_plane(ctx->out_h274, ctx->in, 0, &s_h274);

        const double pred_a = (double)desc_a->sigma[0][lvl];
        const double pred_h = (double)desc_h->sigma[0][lvl];

        char label[128];
        (void)snprintf(label, sizeof(label), "fixture_a_luma_%d_aom_sigma_vs_desc", lvl);
        /* Measured worst luma rel_err is ~0.0944; bound set to 0.120 (0.0944 * 1.2 rounded up) */
        check_assert(ctx, label, fabs(s_aom.sigma - pred_a) / pred_a, 0.120);

        (void)snprintf(label, sizeof(label), "fixture_a_luma_%d_h274_sigma_vs_desc", lvl);
        /* Measured worst luma rel_err is ~0.0217; tightened to 0.026 (0.0217 * 1.2 rounded up, ADR-0216) */
        check_assert(ctx, label, fabs(s_h274.sigma - pred_h) / pred_h, 0.026);

        (void)snprintf(label, sizeof(label), "fixture_a_luma_%d_aom_vs_h274_sigma", lvl);
        /* Measured worst luma rel_diff is ~0.0299; bound set to 0.036 (0.0299 * 1.2 rounded up) */
        check_assert(ctx, label, fabs(s_aom.sigma - s_h274.sigma) / s_aom.sigma, 0.036);

        (void)snprintf(label, sizeof(label), "fixture_a_luma_%d_rho_h_delta", lvl);
        /* Measured worst luma |rho_h delta| is ~0.0455; bound set to 0.055 (0.0455 * 1.2 rounded up) */
        check_assert(ctx, label, fabs(s_aom.rho_h - s_h274.rho_h), 0.055);

        (void)snprintf(label, sizeof(label), "fixture_a_luma_%d_rho_v_delta", lvl);
        /* Measured worst luma |rho_v delta| is ~0.0607; bound set to 0.075 (0.0607 * 1.2 rounded up) */
        check_assert(ctx, label, fabs(s_aom.rho_v - s_h274.rho_v), 0.075);
    }
    return 0;
}

static void test_fixture_a_chroma(Context *ctx, const PelorusGrainDescriptor *desc_a,
                                  const PelorusGrainDescriptor *desc_h)
{
    for (int p = 1; p <= 2; p++) {
        Stats s_aom;
        Stats s_h274;
        measure_plane(ctx->out_aom, ctx->in, p, &s_aom);
        measure_plane(ctx->out_h274, ctx->in, p, &s_h274);
        const double pred_a = (double)desc_a->sigma[p][128];
        const double pred_h = (double)desc_h->sigma[p][128];

        char label[128];
        (void)snprintf(label, sizeof(label), "fixture_a_chroma_%d_aom_sigma_vs_desc", p);
        /* Measured worst chroma aom rel_err is ~0.0712; bound set to 0.090 */
        check_assert(ctx, label, fabs(s_aom.sigma - pred_a) / pred_a, 0.090);

        (void)snprintf(label, sizeof(label), "fixture_a_chroma_%d_h274_sigma_vs_desc", p);
        /* Measured worst chroma h274 rel_err is ~0.0392; tightened to 0.048 (0.03919 * 1.2 rounded up, ADR-0216) */
        check_assert(ctx, label, fabs(s_h274.sigma - pred_h) / pred_h, 0.048);

        (void)snprintf(label, sizeof(label), "fixture_a_chroma_%d_aom_vs_h274_sigma", p);
        /* Measured ~0.1837, same open chroma gap (ADR-0211 note); bound 0.230 */
        check_assert(ctx, label, fabs(s_aom.sigma - s_h274.sigma) / s_aom.sigma, 0.230);

        (void)snprintf(label, sizeof(label), "fixture_a_chroma_%d_rho_h_delta", p);
        /* Measured worst chroma |rho_h delta| is ~0.0690; bound set to 0.085 */
        check_assert(ctx, label, fabs(s_aom.rho_h - s_h274.rho_h), 0.085);

        (void)snprintf(label, sizeof(label), "fixture_a_chroma_%d_rho_v_delta", p);
        /* Measured worst chroma |rho_v delta| is ~0.1312; bound set to 0.160 */
        check_assert(ctx, label, fabs(s_aom.rho_v - s_h274.rho_v), 0.160);
    }
}

static int test_fixture_a(Context *ctx)
{
    (void)printf("=== Conformance Fixture A (realistic AV1 parameters) ===\n");
    PelorusFilmGrainSection aom;
    setup_realistic_aom(&aom);

    PelorusH274GrainParams h274;
    PelorusGrainFidelity fid;
    if (pel_grain_aom_to_h274(&aom, &h274, &fid) != PEL_OK) {
        return 2;
    }

    PelorusGrainDescriptor desc_aom;

    PelorusGrainDescriptor desc_h274;
    if (pel_grain_describe_aom(&aom, &desc_aom) != PEL_OK ||
        pel_grain_describe_h274(&h274, &desc_h274) != PEL_OK) {
        return 2;
    }

    apply_plants(ctx, &aom, &h274);
    map_aom_params(&aom, ctx->params_aom);
    map_h274_params(&h274, aom.seed, ctx->params_h274);

    const int rc = test_fixture_a_luma(ctx, &desc_aom, &desc_h274);
    if (rc != 0) {
        return rc;
    }
    test_fixture_a_chroma(ctx, &desc_aom, &desc_h274);
    return 0;
}

static int test_fixture_b_levels(Context *ctx, const PelorusGrainDescriptor *desc_a,
                                 const PelorusGrainDescriptor *desc_h)
{
    const int levels[3] = {64, 128, 192};
    for (int i = 0; i < 3; i++) {
        const int lvl = levels[i];
        fill_frame(ctx->in, (uint8_t)lvl, 128, 128);

        if (ff_aom_apply_film_grain(ctx->out_aom, ctx->in, ctx->params_aom) < 0 ||
            ff_h274_apply_film_grain(ctx->out_h274, ctx->in, ctx->params_h274) < 0) {
            return 2;
        }

        Stats s_aom;

        Stats s_h274;
        measure_plane(ctx->out_aom, ctx->in, 0, &s_aom);
        measure_plane(ctx->out_h274, ctx->in, 0, &s_h274);

        const double pred_a = (double)desc_a->sigma[0][lvl];
        const double pred_h = (double)desc_h->sigma[0][lvl];

        char label[128];
        (void)snprintf(label, sizeof(label), "fixture_b_luma_%d_aom_sigma_vs_desc", lvl);
        /* Coinciding: measured rel_err is ~0.0249; tightened bound set to 0.030 (0.0249 * 1.2) */
        check_assert(ctx, label, fabs(s_aom.sigma - pred_a) / pred_a, 0.030);

        (void)snprintf(label, sizeof(label), "fixture_b_luma_%d_h274_sigma_vs_desc", lvl);
        /* Coinciding: measured rel_err is ~0.0037; tightened to 0.005 (0.0037 * 1.2 rounded up, ADR-0216) */
        check_assert(ctx, label, fabs(s_h274.sigma - pred_h) / pred_h, 0.005);

        (void)snprintf(label, sizeof(label), "fixture_b_luma_%d_aom_vs_h274_sigma", lvl);
        /* Coinciding: measured rel_diff is ~0.0130; tightened bound set to 0.016 (0.0130 * 1.2) */
        check_assert(ctx, label, fabs(s_aom.sigma - s_h274.sigma) / s_aom.sigma, 0.016);

        (void)snprintf(label, sizeof(label), "fixture_b_luma_%d_rho_h_delta", lvl);
        /* Coinciding: measured |rho_h delta| is ~0.0315; tightened bound set to 0.038 (0.0315 * 1.2) */
        check_assert(ctx, label, fabs(s_aom.rho_h - s_h274.rho_h), 0.038);

        (void)snprintf(label, sizeof(label), "fixture_b_luma_%d_rho_v_delta", lvl);
        /* Coinciding: measured |rho_v delta| is ~0.0288; tightened bound set to 0.035 (0.0288 * 1.2) */
        check_assert(ctx, label, fabs(s_aom.rho_v - s_h274.rho_v), 0.035);
    }
    return 0;
}

static int test_fixture_b(Context *ctx)
{
    (void)printf("=== Conformance Fixture B (coinciding parameters) ===\n");
    PelorusH274GrainParams h274;
    setup_coinciding_h274(&h274);

    PelorusFilmGrainSection aom;
    PelorusGrainFidelity fid;
    if (pel_grain_h274_to_aom(&h274, 42, &aom, &fid) != PEL_OK) {
        return 2;
    }

    PelorusGrainDescriptor desc_aom;

    PelorusGrainDescriptor desc_h274;
    if (pel_grain_describe_aom(&aom, &desc_aom) != PEL_OK ||
        pel_grain_describe_h274(&h274, &desc_h274) != PEL_OK) {
        return 2;
    }

    map_aom_params(&aom, ctx->params_aom);
    map_h274_params(&h274, 42, ctx->params_h274);

    return test_fixture_b_levels(ctx, &desc_aom, &desc_h274);
}

static int parse_args(int argc, char **argv, enum PlantMode *plant)
{
    *plant = PLANT_NONE;
    if (argc == 1) {
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "--plant") == 0) {
        if (strcmp(argv[2], "scale") == 0) {
            *plant = PLANT_SCALE;
            return 0;
        }
        if (strcmp(argv[2], "cutoff") == 0) {
            *plant = PLANT_CUTOFF;
            return 0;
        }
        if (strcmp(argv[2], "shift") == 0) {
            *plant = PLANT_SHIFT;
            return 0;
        }
        (void)fprintf(stderr, "Unknown plant name: %s\n", argv[2]);
        return 2;
    }
    (void)fprintf(stderr, "Usage: %s [--plant <scale|cutoff|shift>]\n", argv[0]);
    return 2;
}

static int init_context(Context *ctx)
{
    ctx->failures = 0;
    ctx->in = av_frame_alloc();
    ctx->out_aom = av_frame_alloc();
    ctx->out_h274 = av_frame_alloc();
    ctx->params_aom = av_film_grain_params_alloc(NULL);
    ctx->params_h274 = av_film_grain_params_alloc(NULL);

    if (!ctx->in || !ctx->out_aom || !ctx->out_h274 || !ctx->params_aom || !ctx->params_h274) {
        return 2;
    }

    ctx->in->format = AV_PIX_FMT_YUV420P;
    ctx->in->width = 512;
    ctx->in->height = 512;
    if (av_frame_get_buffer(ctx->in, 32) < 0) {
        return 2;
    }

    ctx->out_aom->format = AV_PIX_FMT_YUV420P;
    ctx->out_aom->width = 512;
    ctx->out_aom->height = 512;
    if (av_frame_get_buffer(ctx->out_aom, 32) < 0) {
        return 2;
    }

    ctx->out_h274->format = AV_PIX_FMT_YUV420P;
    ctx->out_h274->width = 512;
    ctx->out_h274->height = 512;
    if (av_frame_get_buffer(ctx->out_h274, 32) < 0) {
        return 2;
    }
    return 0;
}

static void free_context(Context *ctx)
{
    av_free(ctx->params_aom);
    av_free(ctx->params_h274);
    av_frame_free(&ctx->in);
    av_frame_free(&ctx->out_aom);
    av_frame_free(&ctx->out_h274);
}

int main(int argc, char **argv)
{
    Context ctx;
    const int arg_rc = parse_args(argc, argv, &ctx.plant);
    if (arg_rc != 0) {
        return arg_rc;
    }

    const int init_rc = init_context(&ctx);
    if (init_rc != 0) {
        free_context(&ctx);
        return init_rc;
    }

    const int det_rc = test_determinism(&ctx);
    if (det_rc != 0) {
        free_context(&ctx);
        return det_rc;
    }

    const int fa_rc = test_fixture_a(&ctx);
    if (fa_rc != 0) {
        free_context(&ctx);
        return fa_rc;
    }

    const int fb_rc = test_fixture_b(&ctx);
    if (fb_rc != 0) {
        free_context(&ctx);
        return fb_rc;
    }

    free_context(&ctx);
    if (ctx.failures != 0) {
        (void)fprintf(stderr, "grain_conformance: FAILED (%d checks failed)\n", ctx.failures);
        return 1;
    }
    (void)printf("grain_conformance: ALL CHECKS PASSED\n");
    return 0;
}
