/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "host_pipeline.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

/* Fill the scaling points and AR coefficients of the decoded stream into expected. */
static void fill_expected_grain_arrays(PelorusFilmGrainSection *expected)
{
    static const uint8_t exp_y_points[14][2] = {
        {16u, 0u},    {25u, 136u},  {33u, 144u},  {41u, 160u}, {48u, 168u},
        {56u, 136u},  {67u, 128u},  {82u, 144u},  {97u, 152u}, {113u, 144u},
        {128u, 176u}, {143u, 168u}, {158u, 176u}, {178u, 184u}};
    static const uint8_t exp_uv_points0[8][2] = {{16u, 0u},    {20u, 64u},  {28u, 88u},
                                                 {60u, 104u},  {90u, 136u}, {105u, 160u},
                                                 {134u, 168u}, {168u, 208u}};
    static const uint8_t exp_uv_points1[9][2] = {{16u, 0u},    {28u, 96u},   {56u, 80u},
                                                 {66u, 96u},   {80u, 104u},  {108u, 96u},
                                                 {122u, 112u}, {137u, 112u}, {169u, 176u}};
    static const int8_t exp_ar_coeffs_y[12] = {0, 0, -58, 0, 0, 0, -76, 100, -43, 0, -51, 82};
    static const int8_t exp_ar_coeffs_uv0[13] = {0, 0, -49, 0, 0, 0, -36, 22, -30, 0, -38, 7, 39};
    static const int8_t exp_ar_coeffs_uv1[13] = {0,  0,   -47, 0,   0,  0,   -31,
                                                 31, -25, 0,   -32, 13, -100};

    for (size_t i = 0; i < 14u; i++) {
        expected->y_points[i][0] = exp_y_points[i][0];
        expected->y_points[i][1] = exp_y_points[i][1];
    }
    for (size_t i = 0; i < 8u; i++) {
        expected->uv_points[0][i][0] = exp_uv_points0[i][0];
        expected->uv_points[0][i][1] = exp_uv_points0[i][1];
    }
    for (size_t i = 0; i < 9u; i++) {
        expected->uv_points[1][i][0] = exp_uv_points1[i][0];
        expected->uv_points[1][i][1] = exp_uv_points1[i][1];
    }
    for (size_t i = 0; i < 12u; i++) {
        expected->ar_coeffs_y[i] = exp_ar_coeffs_y[i];
    }
    for (size_t i = 0; i < 13u; i++) {
        expected->ar_coeffs_uv[0][i] = exp_ar_coeffs_uv0[i];
        expected->ar_coeffs_uv[1][i] = exp_ar_coeffs_uv1[i];
    }
}

/* Populate expected film grain parameters from independent reference values. */
void hp_build_expected_grain(PelorusFilmGrainSection *expected)
{
    memset(expected, 0, sizeof(*expected));
    expected->seed = 45231u;
    expected->apply = 1u;
    expected->grain_model = PEL_GRAIN_AOM;
    expected->limit_output_range = 0u;
    expected->h274_model_id = 0u;
    expected->h274_blending_mode = 0u;
    expected->h274_log2_scale = 0u;

    expected->num_y_points = 14;
    expected->num_uv_points[0] = 8;
    expected->num_uv_points[1] = 9;
    expected->scaling_shift = 11;
    expected->ar_coeff_lag = 2;
    expected->ar_coeff_shift = 8;
    expected->grain_scale_shift = 0;

    expected->uv_mult[0] = 119;
    expected->uv_mult[1] = 101;
    expected->uv_mult_luma[0] = 64;
    expected->uv_mult_luma[1] = 64;
    expected->uv_offset[0] = -238;
    expected->uv_offset[1] = -202;
    expected->chroma_scaling_from_luma = 0u;
    expected->overlap_flag = 0u;

    fill_expected_grain_arrays(expected);
}

/* Read film grain table fixture and parse initial frame grain parameters. */
int hp_read_scenario1_grain(const char *grain_table_path, PelorusFilmGrainSection *out_grain0)
{
    char *tbl_buf = malloc(HP_FIXTURE_BUF_CAP);
    size_t tbl_len = 0;
    PelorusGrainTableEntry entries[HP_GRAIN_ENTRY_CAP];
    size_t grain_count = 0;
    pel_result r = PEL_ERR_INVALID;

    memset(entries, 0, sizeof(entries));
    CHECK(tbl_buf != NULL);
    if (tbl_buf == NULL) {
        return -1;
    }
    if (hp_read_fixture(grain_table_path, tbl_buf, HP_FIXTURE_BUF_CAP, &tbl_len) == 0) {
        r = pel_grain_table_parse(tbl_buf, tbl_len, entries, HP_GRAIN_ENTRY_CAP, &grain_count);
    }
    free(tbl_buf);
    CHECK(r == PEL_OK);
    CHECK(grain_count == 1u);
    if (r != PEL_OK || grain_count != 1u) {
        return -1;
    }
    *out_grain0 = entries[0].params;
    return 0;
}

/* Compare metadata scalars of a film grain section against expected values. */
static void check_grain_scalars_meta(const PelorusFilmGrainSection *got,
                                     const PelorusFilmGrainSection *expected)
{
    CHECK(got->seed == expected->seed);
    CHECK(got->apply == expected->apply);
    CHECK(got->grain_model == expected->grain_model);
    CHECK(got->limit_output_range == expected->limit_output_range);
    CHECK(got->num_y_points == expected->num_y_points);
    CHECK(got->num_uv_points[0] == expected->num_uv_points[0]);
    CHECK(got->num_uv_points[1] == expected->num_uv_points[1]);
    CHECK(got->chroma_scaling_from_luma == expected->chroma_scaling_from_luma);
    CHECK(got->overlap_flag == expected->overlap_flag);
}

/* Compare coefficients and scale shifts of a film grain section against expected values. */
static void check_grain_scalars_coeffs(const PelorusFilmGrainSection *got,
                                       const PelorusFilmGrainSection *expected)
{
    CHECK(got->scaling_shift == expected->scaling_shift);
    CHECK(got->ar_coeff_lag == expected->ar_coeff_lag);
    CHECK(got->ar_coeff_shift == expected->ar_coeff_shift);
    CHECK(got->grain_scale_shift == expected->grain_scale_shift);
    CHECK(got->uv_mult[0] == expected->uv_mult[0]);
    CHECK(got->uv_mult[1] == expected->uv_mult[1]);
    CHECK(got->uv_mult_luma[0] == expected->uv_mult_luma[0]);
    CHECK(got->uv_mult_luma[1] == expected->uv_mult_luma[1]);
    CHECK(got->uv_offset[0] == expected->uv_offset[0]);
    CHECK(got->uv_offset[1] == expected->uv_offset[1]);
}

/* Compare array fields and full memory layout against expected reference values. */
static void check_grain_arrays(const PelorusFilmGrainSection *got,
                               const PelorusFilmGrainSection *expected)
{
    CHECK(memcmp(got->y_points, expected->y_points, sizeof(got->y_points)) == 0);
    CHECK(memcmp(got->uv_points, expected->uv_points, sizeof(got->uv_points)) == 0);
    CHECK(memcmp(got->ar_coeffs_y, expected->ar_coeffs_y, sizeof(got->ar_coeffs_y)) == 0);
    CHECK(memcmp(got->ar_coeffs_uv, expected->ar_coeffs_uv, sizeof(got->ar_coeffs_uv)) == 0);
    CHECK(memcmp(got, expected, sizeof(*got)) == 0);
}

/* Compare a film grain section against expected reference values. */
void hp_compare_grain_full(const PelorusFilmGrainSection *got,
                           const PelorusFilmGrainSection *expected)
{
    check_grain_scalars_meta(got, expected);
    check_grain_scalars_coeffs(got, expected);
    check_grain_arrays(got, expected);
}

/* Verify that lossy roundtrip preserved essential identity fields. */
static void check_grain_rt_identity(const PelorusFilmGrainSection *grain_sec,
                                    const PelorusFilmGrainSection *aom_rt)
{
    CHECK(aom_rt->grain_model == PEL_GRAIN_AOM);
    CHECK(aom_rt->seed == grain_sec->seed);
    CHECK(aom_rt->apply == grain_sec->apply);
}

/* Verify AV1 -> H.274 -> AV1 roundtrip fidelity and bounds. */
static void verify_grain_roundtrip(const PelorusFilmGrainSection *grain_sec,
                                   PelorusFilmGrainSection *aom_rt)
{
    PelorusH274GrainParams h274;
    PelorusGrainFidelity fid_h;
    PelorusGrainFidelity fid_aom;
    PelorusGrainDescriptor desc_in;
    PelorusGrainDescriptor desc_rt;
    PelorusGrainFidelity fid_comp;

    memset(aom_rt, 0, sizeof(*aom_rt));
    CHECK(pel_grain_aom_to_h274(grain_sec, &h274, &fid_h) == PEL_OK);
    CHECK(pel_grain_h274_to_aom(&h274, grain_sec->seed, aom_rt, &fid_aom) == PEL_OK);
    CHECK(isfinite(fid_h.loss) && fid_h.loss > 0.0 && fid_h.loss < GRAIN_TO_H274_LOSS_MAX);
    CHECK(isfinite(fid_aom.loss) && fid_aom.loss > 0.0 && fid_aom.loss < GRAIN_TO_AOM_LOSS_MAX);

    CHECK(pel_grain_describe_aom(grain_sec, &desc_in) == PEL_OK);
    CHECK(pel_grain_describe_aom(aom_rt, &desc_rt) == PEL_OK);
    CHECK(pel_grain_compare(&desc_in, &desc_rt, &fid_comp) == PEL_OK);
    /* isfinite first: a NaN sigma drops out of the loss maximum and reads as zero loss. */
    CHECK(isfinite(fid_comp.sigma_rel_rms[0]) && fid_comp.sigma_rel_rms[0] < GRAIN_RT_SIGMA_Y_MAX);
    CHECK(isfinite(fid_comp.loss) && fid_comp.loss > 0.0 && fid_comp.loss < GRAIN_RT_LOSS_MAX);
}

/* Verify unwrapped film grain section against expected and parsed structures. */
void hp_verify_consumed_grain(const uint8_t *unwrapped, size_t unwrapped_len,
                              const PelorusFilmGrainSection *expected_grain,
                              const PelorusFilmGrainSection *parsed_grain)
{
    const void *grain_ptr = NULL;
    size_t grain_size = 0;
    PelorusFilmGrainSection aom_rt;

    CHECK(pel_blob_find_section(unwrapped, unwrapped_len, PEL_SEC_FILMGRAIN,
                                sizeof(PelorusFilmGrainSection), &grain_ptr,
                                &grain_size) == PEL_OK);
    CHECK(grain_ptr != NULL && grain_size >= sizeof(PelorusFilmGrainSection));
    if (grain_ptr == NULL || grain_size < sizeof(PelorusFilmGrainSection)) {
        return;
    }
    {
        const PelorusFilmGrainSection *grain_sec = grain_ptr;
        hp_compare_grain_full(grain_sec, expected_grain);
        CHECK(memcmp(grain_sec, parsed_grain, sizeof(*grain_sec)) == 0);
        verify_grain_roundtrip(grain_sec, &aom_rt);
        check_grain_rt_identity(grain_sec, &aom_rt);
    }
}

/* NOLINTEND(modernize-use-nullptr) */
