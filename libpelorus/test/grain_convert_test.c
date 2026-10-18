/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/grain_convert.h"
#include "grain_convert_internal.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            g_fail++;                                                                              \
        }                                                                                          \
    } while (0)

static int check_bytes_same(const void *a, const void *b, size_t size)
{
    const uint8_t *ba = (const uint8_t *)a;
    const uint8_t *bb = (const uint8_t *)b;
    for (size_t i = 0; i < size; i++) {
        if (ba[i] != bb[i]) {
            return 0;
        }
    }
    return 1;
}

static void test_compare_identical_and_absent(void)
{
    PelorusGrainDescriptor d;
    memset(&d, 0, sizeof(d));
    d.present[0] = 1;
    d.rho_h[0] = 0.5f;
    d.rho_v[0] = 0.4f;
    for (int i = 0; i < 256; i++) {
        d.sigma[0][i] = 2.0f + 0.01f * (float)i;
    }
    PelorusGrainFidelity fid;
    pel_result r = pel_grain_compare(&d, &d, &fid);
    CHECK(r == PEL_OK);
    CHECK(fid.sigma_rel_rms[0] == 0.0);
    CHECK(fid.rho_h_delta[0] == 0.0);
    CHECK(fid.rho_v_delta[0] == 0.0);
    CHECK(fid.loss == 0.0);

    PelorusFilmGrainSection aom_absent;
    memset(&aom_absent, 0, sizeof(aom_absent));
    aom_absent.grain_model = PEL_GRAIN_AOM;
    aom_absent.scaling_shift = 8;
    aom_absent.ar_coeff_shift = 7;
    PelorusH274GrainParams h_out;
    r = pel_grain_aom_to_h274(&aom_absent, &h_out, &fid);
    CHECK(r == PEL_OK);
    CHECK(fid.loss == 0.0);
    CHECK(h_out.component_model_present[0] == 0);
    CHECK(h_out.component_model_present[1] == 0);
    CHECK(h_out.component_model_present[2] == 0);

    PelorusH274GrainParams h_absent;
    memset(&h_absent, 0, sizeof(h_absent));
    h_absent.log2_scale_factor = 2;
    PelorusFilmGrainSection aom_out;
    r = pel_grain_h274_to_aom(&h_absent, 1234, &aom_out, &fid);
    CHECK(r == PEL_OK);
    CHECK(fid.loss == 0.0);
    CHECK(aom_out.num_y_points == 0);
    CHECK(aom_out.num_uv_points[0] == 0);
    CHECK(aom_out.num_uv_points[1] == 0);
}

static void test_white_noise_aom_to_h274(void)
{
    PelorusFilmGrainSection aom;
    memset(&aom, 0, sizeof(aom));
    aom.grain_model = PEL_GRAIN_AOM;
    aom.apply = 1;
    aom.ar_coeff_lag = 0;
    aom.ar_coeff_shift = 7;
    aom.scaling_shift = 10;
    aom.num_y_points = 2;
    aom.y_points[0][0] = 0;
    aom.y_points[0][1] = 20;
    aom.y_points[1][0] = 255;
    aom.y_points[1][1] = 20;

    PelorusH274GrainParams h_out;
    PelorusGrainFidelity fid;
    pel_result r = pel_grain_aom_to_h274(&aom, &h_out, &fid);
    CHECK(r == PEL_OK);
    CHECK(fid.loss > 0.0);
    CHECK(h_out.component_model_present[0] == 1);
    CHECK(h_out.comp_model_value[0][0][1] == 14);
    CHECK(h_out.comp_model_value[0][0][2] == 14);

    PelorusGrainDescriptor desc;
    r = pel_grain_describe_h274(&h_out, &desc);
    CHECK(r == PEL_OK);
}

static void setup_realistic_aom(PelorusFilmGrainSection *aom)
{
    memset(aom, 0, sizeof(*aom));
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

static void test_realistic_round_trip(void)
{
    PelorusFilmGrainSection aom_in;
    setup_realistic_aom(&aom_in);

    PelorusH274GrainParams h_out;
    PelorusGrainFidelity fid1;
    pel_result r1 = pel_grain_aom_to_h274(&aom_in, &h_out, &fid1);
    CHECK(r1 == PEL_OK);
    CHECK(fid1.loss > 0.0);

    PelorusFilmGrainSection aom_rt;
    PelorusGrainFidelity fid2;
    pel_result r2 = pel_grain_h274_to_aom(&h_out, 42, &aom_rt, &fid2);
    CHECK(r2 == PEL_OK);
    CHECK(fid2.loss > 0.0);

    PelorusGrainDescriptor desc_in;
    PelorusGrainDescriptor desc_rt;
    CHECK(pel_grain_describe_aom(&aom_in, &desc_in) == PEL_OK);
    CHECK(pel_grain_describe_aom(&aom_rt, &desc_rt) == PEL_OK);

    PelorusGrainFidelity fid_rt;
    CHECK(pel_grain_compare(&desc_in, &desc_rt, &fid_rt) == PEL_OK);
    CHECK(fid_rt.loss > 0.0);
    /* Measured sigma_rel_rms[0] is ~0.0211; tightened to 0.026 (0.0211 * 1.2 rounded up) */
    CHECK(fid_rt.sigma_rel_rms[0] < 0.026);
}

static void test_h274_single_interval_to_aom(void)
{
    PelorusH274GrainParams h274;
    memset(&h274, 0, sizeof(h274));
    h274.model_id = 0;
    h274.blending_mode_id = 0;
    h274.log2_scale_factor = 3;
    h274.component_model_present[0] = 1;
    h274.num_intensity_intervals[0] = 1;
    h274.num_model_values[0] = 3;
    h274.intensity_interval_lower_bound[0][0] = 0;
    h274.intensity_interval_upper_bound[0][0] = 255;
    h274.comp_model_value[0][0][0] = 40;
    h274.comp_model_value[0][0][1] = 8;
    h274.comp_model_value[0][0][2] = 8;

    PelorusFilmGrainSection aom;
    PelorusGrainFidelity fid;
    pel_result r = pel_grain_h274_to_aom(&h274, 999, &aom, &fid);
    CHECK(r == PEL_OK);

    PelorusGrainDescriptor desc_h;
    PelorusGrainDescriptor desc_a;
    CHECK(pel_grain_describe_h274(&h274, &desc_h) == PEL_OK);
    CHECK(pel_grain_describe_aom(&aom, &desc_a) == PEL_OK);

    float s_h = desc_h.sigma[0][128];
    float s_a = pel_quantized_sigma(desc_a.sigma[0][128]);
    double rel_diff = fabs((double)s_a - (double)s_h) / (double)s_h;
    /* Measured diff is 0.00031 (0.031%); tightened to 0.0005 (0.05%) */
    CHECK(rel_diff < 0.0005);
}

static void test_h274_range_planted_cases(void)
{
    PelorusH274GrainParams h274;
    PelorusGrainDescriptor desc;
    uint8_t guard[sizeof(PelorusGrainDescriptor)];
    memset(guard, 0xA5, sizeof(guard));

    /* model_id 1 */
    memset(&h274, 0, sizeof(h274));
    h274.log2_scale_factor = 2;
    h274.model_id = 1;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* blending 1 */
    h274.model_id = 0;
    h274.blending_mode_id = 1;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* log2 1 and 8 */
    h274.blending_mode_id = 0;
    h274.log2_scale_factor = 1;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));
    h274.log2_scale_factor = 8;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* intervals 0 when present */
    h274.log2_scale_factor = 2;
    h274.component_model_present[0] = 1;
    h274.num_intensity_intervals[0] = 0;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* intervals 257 */
    h274.num_intensity_intervals[0] = 257;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* num_model_values 7 */
    h274.num_intensity_intervals[0] = 1;
    h274.num_model_values[0] = 7;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));
}

static void test_h274_interval_ranges_planted(void)
{
    PelorusH274GrainParams h274;
    PelorusGrainDescriptor desc;
    uint8_t guard[sizeof(PelorusGrainDescriptor)];
    memset(guard, 0xA5, sizeof(guard));
    memset(&h274, 0, sizeof(h274));
    h274.log2_scale_factor = 2;
    h274.component_model_present[0] = 1;
    h274.num_intensity_intervals[0] = 1;
    h274.num_model_values[0] = 3;

    /* lower > upper */
    h274.intensity_interval_lower_bound[0][0] = 100;
    h274.intensity_interval_upper_bound[0][0] = 50;
    h274.comp_model_value[0][0][1] = 8;
    h274.comp_model_value[0][0][2] = 8;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* scale 256 and -1 */
    h274.intensity_interval_lower_bound[0][0] = 0;
    h274.intensity_interval_upper_bound[0][0] = 100;
    h274.comp_model_value[0][0][0] = 256;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));
    h274.comp_model_value[0][0][0] = -1;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* cutoff 1 and 15 */
    h274.comp_model_value[0][0][0] = 40;
    h274.comp_model_value[0][0][1] = 1;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));
    h274.comp_model_value[0][0][1] = 15;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* overlapping intervals [0,100], [100,200] */
    h274.comp_model_value[0][0][1] = 8;
    h274.num_intensity_intervals[0] = 2;
    h274.intensity_interval_lower_bound[0][0] = 0;
    h274.intensity_interval_upper_bound[0][0] = 100;
    h274.intensity_interval_lower_bound[0][1] = 100;
    h274.intensity_interval_upper_bound[0][1] = 200;
    h274.comp_model_value[0][1][0] = 40;
    h274.comp_model_value[0][1][1] = 8;
    h274.comp_model_value[0][1][2] = 8;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));
}

static void test_aom_range_and_null_rejections(void)
{
    PelorusFilmGrainSection aom;
    memset(&aom, 0, sizeof(aom));
    aom.grain_model = PEL_GRAIN_AOM;
    aom.apply = 1;
    aom.ar_coeff_lag = 1;
    aom.ar_coeff_shift = 7;
    aom.scaling_shift = 10;
    aom.num_y_points = 2;
    aom.y_points[0][0] = 0;
    aom.y_points[0][1] = 20;
    aom.y_points[1][0] = 255;
    aom.y_points[1][1] = 20;

    PelorusGrainDescriptor desc;
    uint8_t guard[sizeof(PelorusGrainDescriptor)];
    memset(guard, 0xA5, sizeof(guard));

    /* scaling_shift 7 */
    aom.scaling_shift = 7;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_aom(&aom, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* ar_coeff_lag 4 */
    aom.scaling_shift = 10;
    aom.ar_coeff_lag = 4;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_aom(&aom, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* non-increasing points */
    aom.ar_coeff_lag = 1;
    aom.y_points[1][0] = 0;
    memset(&desc, 0xA5, sizeof(desc));
    CHECK(pel_grain_describe_aom(&aom, &desc) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&desc, guard, sizeof(desc)));

    /* NULL arguments */
    CHECK(pel_grain_describe_aom(NULL, &desc) == PEL_ERR_INVALID);
    CHECK(pel_grain_describe_aom(&aom, NULL) == PEL_ERR_INVALID);
    PelorusH274GrainParams h274;
    memset(&h274, 0, sizeof(h274));
    CHECK(pel_grain_describe_h274(NULL, &desc) == PEL_ERR_INVALID);
    CHECK(pel_grain_describe_h274(&h274, NULL) == PEL_ERR_INVALID);
    PelorusGrainFidelity fid;
    CHECK(pel_grain_compare(NULL, &desc, &fid) == PEL_ERR_INVALID);
    CHECK(pel_grain_compare(&desc, NULL, &fid) == PEL_ERR_INVALID);
    CHECK(pel_grain_compare(&desc, &desc, NULL) == PEL_ERR_INVALID);
    CHECK(pel_grain_aom_to_h274(NULL, &h274, &fid) == PEL_ERR_INVALID);
    CHECK(pel_grain_aom_to_h274(&aom, NULL, &fid) == PEL_ERR_INVALID);
    CHECK(pel_grain_h274_to_aom(NULL, 0, &aom, &fid) == PEL_ERR_INVALID);
    CHECK(pel_grain_h274_to_aom(&h274, 0, NULL, &fid) == PEL_ERR_INVALID);
}

static void test_boundaries_accepted(void)
{
    PelorusH274GrainParams h274;
    memset(&h274, 0, sizeof(h274));
    h274.component_model_present[0] = 1;
    h274.num_intensity_intervals[0] = 1;
    h274.num_model_values[0] = 3;
    h274.intensity_interval_lower_bound[0][0] = 0;
    h274.intensity_interval_upper_bound[0][0] = 255;
    h274.comp_model_value[0][0][0] = 0;
    h274.comp_model_value[0][0][1] = 2;
    h274.comp_model_value[0][0][2] = 2;
    h274.log2_scale_factor = 2;

    PelorusGrainDescriptor desc;
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_OK);

    h274.log2_scale_factor = 7;
    h274.comp_model_value[0][0][0] = 255;
    h274.comp_model_value[0][0][1] = 14;
    h274.comp_model_value[0][0][2] = 14;
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_OK);

    /* 256 one-wide intervals */
    h274.num_intensity_intervals[0] = 256;
    for (int i = 0; i < 256; i++) {
        h274.intensity_interval_lower_bound[0][i] = (uint8_t)i;
        h274.intensity_interval_upper_bound[0][i] = (uint8_t)i;
        h274.comp_model_value[0][i][0] = (int16_t)(i % 256);
        h274.comp_model_value[0][i][1] = 8;
        h274.comp_model_value[0][i][2] = 8;
    }
    CHECK(pel_grain_describe_h274(&h274, &desc) == PEL_OK);
}

static void test_determinism(void)
{
    PelorusFilmGrainSection aom_in;
    setup_realistic_aom(&aom_in);

    PelorusH274GrainParams h1;
    PelorusH274GrainParams h2;
    CHECK(pel_grain_aom_to_h274(&aom_in, &h1, NULL) == PEL_OK);
    CHECK(pel_grain_aom_to_h274(&aom_in, &h2, NULL) == PEL_OK);
    CHECK(check_bytes_same(&h1, &h2, sizeof(h1)));

    PelorusFilmGrainSection aom1;
    PelorusFilmGrainSection aom2;
    CHECK(pel_grain_h274_to_aom(&h1, 42, &aom1, NULL) == PEL_OK);
    CHECK(pel_grain_h274_to_aom(&h1, 42, &aom2, NULL) == PEL_OK);
    CHECK(check_bytes_same(&aom1, &aom2, sizeof(aom1)));
}

static void setup_flat_aom(PelorusFilmGrainSection *aom)
{
    memset(aom, 0, sizeof(*aom));
    aom->grain_model = PEL_GRAIN_AOM;
    aom->apply = 1;
    aom->ar_coeff_lag = 1;
    aom->ar_coeff_shift = 7;
    aom->scaling_shift = 10;
    aom->num_y_points = 2;
    aom->y_points[0][1] = 20;
    aom->y_points[1][0] = 255;
    aom->y_points[1][1] = 20;
}

static void test_seed_range(void)
{
    PelorusFilmGrainSection aom;
    PelorusGrainDescriptor desc;
    uint8_t guard[sizeof(PelorusFilmGrainSection)];

    setup_flat_aom(&aom);
    aom.seed = 65535;
    CHECK(pel_grain_describe_aom(&aom, &desc) == PEL_OK);
    aom.seed = 65536;
    CHECK(pel_grain_describe_aom(&aom, &desc) == PEL_ERR_RANGE);

    PelorusH274GrainParams h274;
    memset(&h274, 0, sizeof(h274));
    h274.log2_scale_factor = 3;
    h274.component_model_present[0] = 1;
    h274.num_intensity_intervals[0] = 1;
    h274.num_model_values[0] = 1;
    h274.intensity_interval_upper_bound[0][0] = 255;
    h274.comp_model_value[0][0][0] = 40;

    PelorusFilmGrainSection out;
    CHECK(pel_grain_h274_to_aom(&h274, 65535, &out, NULL) == PEL_OK);
    CHECK(out.seed == 65535);
    memset(&out, 0xA5, sizeof(out));
    memset(guard, 0xA5, sizeof(guard));
    CHECK(pel_grain_h274_to_aom(&h274, 65536, &out, NULL) == PEL_ERR_RANGE);
    CHECK(check_bytes_same(&out, guard, sizeof(out)));
}

static void test_h274_chroma_only_to_aom(void)
{
    PelorusH274GrainParams h274;
    memset(&h274, 0, sizeof(h274));
    h274.log2_scale_factor = 3;
    h274.component_model_present[1] = 1;
    h274.num_intensity_intervals[1] = 1;
    h274.num_model_values[1] = 3;
    h274.intensity_interval_upper_bound[1][0] = 255;
    h274.comp_model_value[1][0][0] = 60;
    h274.comp_model_value[1][0][1] = 4;
    h274.comp_model_value[1][0][2] = 4;

    PelorusFilmGrainSection aom;
    PelorusGrainFidelity fid;
    CHECK(pel_grain_h274_to_aom(&h274, 1, &aom, &fid) == PEL_OK);
    /* AV1 4:2:0 codes no chroma points without luma points: a flat zero luma
     * curve carries the chroma grain, and it stays absent in the descriptor. */
    CHECK(aom.num_y_points == 2);
    CHECK(aom.y_points[0][1] == 0 && aom.y_points[1][1] == 0);
    CHECK(aom.num_uv_points[0] == 10);
    CHECK(fid.sigma_rel_rms[0] == 0.0);
    CHECK(fid.sigma_rel_rms[2] == 0.0);
    CHECK(fid.sigma_rel_rms[1] < 0.05);

    PelorusGrainDescriptor desc;
    CHECK(pel_grain_describe_aom(&aom, &desc) == PEL_OK);
    CHECK(desc.present[0] == 0);
    CHECK(desc.present[1] == 1);

    /* 4:2:0 chroma adaptation per FFmpeg h274.c: scale 60 >> 1, cutoffs 4 * 2. */
    PelorusGrainDescriptor dh;
    CHECK(pel_grain_describe_h274(&h274, &dh) == PEL_OK);
    const PelH274GrainStat *st = &pel_h274_grain_stats[(8 - 2) * PEL_H274_CUTOFF_COUNT + (8 - 2)];
    CHECK(st->cut_h == 8 && st->cut_v == 8);
    /* Pinned value moved: master linear 1.76475f (30.0f * st->gain * 0.5f) -> quantized floor 1.788205f (ADR-0216) */
    float sig_linear = 30.0f * st->gain * 0.5f;
    float expected_sig = pel_quantized_sigma(sig_linear);
    CHECK(fabs((double)dh.sigma[1][128] - (double)expected_sig) < 1e-5);
    CHECK(dh.rho_h[1] == st->rho_h);
}

static void test_ar_mirror_symmetry(void)
{
    PelorusFilmGrainSection left;
    PelorusFilmGrainSection right;
    setup_flat_aom(&left);
    setup_flat_aom(&right);
    left.ar_coeffs_y[0] = 90;  /* dy -1, dx -1 */
    right.ar_coeffs_y[2] = 90; /* dy -1, dx +1 */

    PelorusGrainDescriptor dl;
    PelorusGrainDescriptor dr;
    CHECK(pel_grain_describe_aom(&left, &dl) == PEL_OK);
    CHECK(pel_grain_describe_aom(&right, &dr) == PEL_OK);
    CHECK(fabs((double)dl.sigma[0][128] - (double)dr.sigma[0][128]) <
          1e-4 * (double)dl.sigma[0][128]);
    CHECK(fabs((double)dl.rho_h[0] - (double)dr.rho_h[0]) < 1e-4);
    CHECK(fabs((double)dl.rho_v[0] - (double)dr.rho_v[0]) < 1e-4);
}

static void test_floor_vq_minimum(void)
{
    /* Floor Vq has theoretical minimum 0.5 as s -> 0+. */
    float sig_near_zero = pel_quantized_sigma(1e-4f);
    CHECK(fabs((double)sig_near_zero - 0.5) < 1e-3);
    float sig_small = pel_quantized_sigma(0.10f);
    CHECK(fabs((double)sig_small - 0.5000) < 1e-3);
    float sig_point3 = pel_quantized_sigma(0.30f);
    CHECK(fabs((double)sig_point3 - 0.5017) < 1e-3);
}

static void test_large_sigma_asymptotic(void)
{
    /* For s >= 1.5, floor converges within 1% of sqrt(s^2 + 1/12). */
    const float test_sigmas[4] = {1.5f, 2.5f, 4.0f, 6.0f};
    for (int i = 0; i < 4; i++) {
        float s = test_sigmas[i];
        float asymp = (float)sqrt((double)s * (double)s + (1.0 / 12.0));
        float val_floor = pel_quantized_sigma(s);
        CHECK(fabs((double)val_floor - (double)asymp) / (double)asymp < 0.01);
    }
}

static void test_quantized_sigma_monotonicity(void)
{
    /* Floor Vq is non-decreasing on (0, 5]. */
    float prev_floor = 0.0f;
    for (int step = 0; step < 100; step++) {
        float s = 0.01f + (float)step * 0.05f;
        float cur_floor = pel_quantized_sigma(s);
        CHECK(cur_floor >= prev_floor);
        prev_floor = cur_floor;
    }
    float cur_5 = pel_quantized_sigma(5.0f);
    CHECK(cur_5 >= prev_floor);
}

int main(void)
{
    test_compare_identical_and_absent();
    test_white_noise_aom_to_h274();
    test_realistic_round_trip();
    test_h274_single_interval_to_aom();
    test_h274_range_planted_cases();
    test_h274_interval_ranges_planted();
    test_aom_range_and_null_rejections();
    test_boundaries_accepted();
    test_determinism();
    test_seed_range();
    test_h274_chroma_only_to_aom();
    test_ar_mirror_symmetry();
    test_floor_vq_minimum();
    test_large_sigma_asymptotic();
    test_quantized_sigma_monotonicity();

    if (g_fail != 0) {
        (void)fprintf(stderr, "grain_convert_test: %d failures\n", g_fail);
        return 1;
    }
    return 0;
}
