/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/grain_convert.h"
#include "grain_aom_internal.h"
#include "grain_convert_internal.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* Impulse position on the 64x64 AR grid: row = maximum lag, column centred so
 * the up-right taps (dy < 0, dx > 0) spread left without hitting the edge. */
#define PEL_AR_IMPULSE_ROW 3
#define PEL_AR_IMPULSE_COL 32

static pel_result validate_h274_intervals(const PelorusH274GrainParams *h274, int c)
{
    uint16_t num_intervals = h274->num_intensity_intervals[c];
    if (num_intervals == 0 || num_intervals > 256) {
        return PEL_ERR_RANGE;
    }
    uint8_t num_mv = h274->num_model_values[c];
    if (num_mv == 0 || num_mv > 6) {
        return PEL_ERR_RANGE;
    }
    for (int s = 0; s < (int)num_intervals && s < 256; s++) {
        uint8_t low = h274->intensity_interval_lower_bound[c][s];
        uint8_t high = h274->intensity_interval_upper_bound[c][s];
        if (low > high) {
            return PEL_ERR_RANGE;
        }
        int16_t scale = h274->comp_model_value[c][s][0];
        if (scale < 0 || scale > 255) {
            return PEL_ERR_RANGE;
        }
        if (num_mv >= 2) {
            int16_t cut_h = h274->comp_model_value[c][s][1];
            if (cut_h < 2 || cut_h > 14) {
                return PEL_ERR_RANGE;
            }
        }
        if (num_mv >= 3) {
            int16_t cut_v = h274->comp_model_value[c][s][2];
            if (cut_v < 2 || cut_v > 14) {
                return PEL_ERR_RANGE;
            }
        }
    }
    return PEL_OK;
}

static pel_result validate_h274_overlaps(const PelorusH274GrainParams *h274, int c)
{
    int num = (int)h274->num_intensity_intervals[c];
    if (num > 256) {
        return PEL_ERR_RANGE;
    }
    for (int i = 0; i < num && i < 256; i++) {
        uint8_t low_i = h274->intensity_interval_lower_bound[c][i];
        uint8_t high_i = h274->intensity_interval_upper_bound[c][i];
        for (int j = i + 1; j < num && j < 256; j++) {
            uint8_t low_j = h274->intensity_interval_lower_bound[c][j];
            uint8_t high_j = h274->intensity_interval_upper_bound[c][j];
            uint8_t max_low = (low_i > low_j) ? low_i : low_j;
            uint8_t min_high = (high_i < high_j) ? high_i : high_j;
            if (max_low <= min_high) {
                return PEL_ERR_RANGE;
            }
        }
    }
    return PEL_OK;
}

static pel_result validate_h274(const PelorusH274GrainParams *h274)
{
    if (h274 == NULL) {
        return PEL_ERR_INVALID;
    }
    if (h274->model_id != 0 || h274->blending_mode_id != 0) {
        return PEL_ERR_RANGE;
    }
    if (h274->log2_scale_factor < 2 || h274->log2_scale_factor > 7) {
        return PEL_ERR_RANGE;
    }
    for (int c = 0; c < 3; c++) {
        if (h274->component_model_present[c] > 1) {
            return PEL_ERR_RANGE;
        }
        if (h274->component_model_present[c]) {
            pel_result r = validate_h274_intervals(h274, c);
            if (r != PEL_OK) {
                return r;
            }
            r = validate_h274_overlaps(h274, c);
            if (r != PEL_OK) {
                return r;
            }
        }
    }
    return PEL_OK;
}

static void calc_grid_covariances(float grid[64][64], float *g_ar_out, float *rho_h_out,
                                  float *rho_v_out)
{
    double sum_sq = 0.0;
    double sum_h = 0.0;
    double sum_v = 0.0;
    for (int y = 0; y < 64; y++) {
        for (int x = 0; x < 64; x++) {
            double v = (double)grid[y][x];
            sum_sq += v * v;
            if (x < 63) {
                sum_h += v * (double)grid[y][x + 1];
            }
            if (y < 63) {
                sum_v += v * (double)grid[y + 1][x];
            }
        }
    }
    *g_ar_out = (float)sqrt(sum_sq);
    *rho_h_out = (sum_sq > 0.0) ? (float)(sum_h / sum_sq) : 0.0f;
    *rho_v_out = (sum_sq > 0.0) ? (float)(sum_v / sum_sq) : 0.0f;
}

static void calc_ar_impulse(int lag, int shift, const int8_t *coeffs, float *g_ar_out,
                            float *rho_h_out, float *rho_v_out)
{
    if (lag == 0) {
        *g_ar_out = 1.0f;
        *rho_h_out = 0.0f;
        *rho_v_out = 0.0f;
        return;
    }
    float grid[64][64];
    memset(grid, 0, sizeof(grid));
    grid[PEL_AR_IMPULSE_ROW][PEL_AR_IMPULSE_COL] = 1.0f;
    float inv_shift = 1.0f / (float)(1 << shift);

    for (int y = PEL_AR_IMPULSE_ROW; y < 64; y++) {
        for (int x = 0; x < 64; x++) {
            if (y == PEL_AR_IMPULSE_ROW && x <= PEL_AR_IMPULSE_COL) {
                continue;
            }
            float sum = 0.0f;
            int idx = 0;
            for (int dy = -lag; dy <= 0; dy++) {
                for (int dx = -lag; dx <= lag; dx++) {
                    if (dy == 0 && dx == 0) {
                        break;
                    }
                    int ny = y + dy;
                    int nx = x + dx;
                    if (ny >= 0 && ny < 64 && nx >= 0 && nx < 64) {
                        sum += ((float)coeffs[idx] * inv_shift) * grid[ny][nx];
                    }
                    idx++;
                }
            }
            grid[y][x] = sum;
        }
    }
    calc_grid_covariances(grid, g_ar_out, rho_h_out, rho_v_out);
}

static void generate_scaling_8bit(const uint8_t points[][2], int num, uint8_t scaling[256])
{
    if (num <= 0) {
        memset(scaling, 0, 256);
        return;
    }
    int max_val = points[num - 1][0];
    memset(scaling, points[0][1], (size_t)points[0][0]);
    for (int i = 0; i < num - 1 && i < 14; i++) {
        int bx = points[i][0];
        int by = points[i][1];
        int ex = points[i + 1][0];
        int ey = points[i + 1][1];
        int dx = ex - bx;
        int dy = ey - by;
        if (dx > 0) {
            int32_t delta = (int32_t)dy * (int32_t)((0x10000 + (dx >> 1)) / dx);
            int32_t d = 0x8000;
            for (int x = 0; x < dx && bx + x < 256; x++) {
                scaling[bx + x] = (uint8_t)(by + (d >> 16));
                d += delta;
            }
        }
    }
    if (max_val < 256) {
        memset(&scaling[max_val], points[num - 1][1], (size_t)(256 - max_val));
    }
}

static int get_aom_intensity_index(const PelorusFilmGrainSection *aom, int c, int i)
{
    if (c == 0 || aom->chroma_scaling_from_luma) {
        return i;
    }
    int32_t mult = aom->uv_mult[c - 1];
    int32_t mult_luma = aom->uv_mult_luma[c - 1];
    int32_t offset = aom->uv_offset[c - 1];
    int32_t val = ((i * mult_luma + i * mult) >> 6) + offset;
    if (val < 0) {
        val = 0;
    } else if (val > 255) {
        val = 255;
    }
    return (int)val;
}

/* A component whose sigma is zero at every intensity synthesises no grain;
 * treat it as absent so the two codecs' "no grain" spellings compare equal. */
static void drop_silent_components(PelorusGrainDescriptor *desc)
{
    for (int c = 0; c < 3; c++) {
        int silent = 1;
        for (int i = 0; i < 256; i++) {
            if (desc->sigma[c][i] != 0.0f) {
                silent = 0;
                break;
            }
        }
        if (silent) {
            desc->present[c] = 0;
            desc->rho_h[c] = 0.0f;
            desc->rho_v[c] = 0.0f;
        }
    }
}

float pel_quantized_sigma(float sigma_lin, float bin_offset)
{
    if (sigma_lin <= 0.0f) {
        return 0.0f;
    }
    if (sigma_lin >= 5.0f) {
        return (float)sqrt((double)sigma_lin * (double)sigma_lin + (1.0 / 12.0));
    }
    double s = (double)sigma_lin;
    double inv_s_sqrt2 = 1.0 / (s * 1.41421356237309504880);
    double m = 0.0;
    double m2 = 0.0;
    double bo = (double)bin_offset;
    for (int k = -64; k < 64; k++) {
        double lower = ((double)k - bo) * inv_s_sqrt2;
        double upper = ((double)k + 1.0 - bo) * inv_s_sqrt2;
        double p = 0.5 * (erf(upper) - erf(lower));
        m += (double)k * p;
        m2 += (double)(k * k) * p;
    }
    double var = m2 - m * m;
    if (var <= 0.0) {
        return 0.0f;
    }
    return (float)sqrt(var);
}

static pel_result describe_aom_internal(const PelorusFilmGrainSection *aom,
                                        PelorusGrainDescriptor *out, int quantized)
{
    if (aom == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    pel_result r = pel_grain_aom_validate(aom);
    if (r != PEL_OK) {
        return r;
    }
    PelorusGrainDescriptor desc;
    memset(&desc, 0, sizeof(desc));

    float sigma_w = PEL_AOM_GAUSSIAN_SEQUENCE_RMS / (float)(1 << (4 + aom->grain_scale_shift));
    float inv_scaling_shift = 1.0f / (float)(1 << aom->scaling_shift);

    for (int c = 0; c < 3; c++) {
        int present = (c == 0) ? (aom->num_y_points > 0) :
                                 (aom->chroma_scaling_from_luma || (aom->num_uv_points[c - 1] > 0));
        desc.present[c] = (uint8_t)present;
        if (!present) {
            continue;
        }
        float g_ar = 1.0f;
        float rho_h = 0.0f;
        float rho_v = 0.0f;
        const int8_t *coeffs = (c == 0) ? aom->ar_coeffs_y : aom->ar_coeffs_uv[c - 1];
        calc_ar_impulse(aom->ar_coeff_lag, aom->ar_coeff_shift, coeffs, &g_ar, &rho_h, &rho_v);
        desc.rho_h[c] = rho_h;
        desc.rho_v[c] = rho_v;

        uint8_t scaling[256];
        if (c == 0 || aom->chroma_scaling_from_luma) {
            generate_scaling_8bit(aom->y_points, aom->num_y_points, scaling);
        } else {
            generate_scaling_8bit(aom->uv_points[c - 1], aom->num_uv_points[c - 1], scaling);
        }
        for (int i = 0; i < 256; i++) {
            int idx = get_aom_intensity_index(aom, c, i);
            float sig_lin = (float)scaling[idx] * sigma_w * g_ar * inv_scaling_shift;
            desc.sigma[c][i] =
                quantized ? pel_quantized_sigma(sig_lin, PEL_QUANT_ROUND_OFFSET) : sig_lin;
        }
    }
    drop_silent_components(&desc);
    memcpy(out, &desc, sizeof(*out));
    return PEL_OK;
}

pel_result pel_grain_describe_aom(const PelorusFilmGrainSection *aom, PelorusGrainDescriptor *out)
{
    return describe_aom_internal(aom, out, 1);
}

static void describe_h274_component(const PelorusH274GrainParams *h274, int c, float factor,
                                    PelorusGrainDescriptor *desc, int quantized)
{
    desc->present[c] = 1;
    double sum_w = 0.0;
    double sum_rho_h = 0.0;
    double sum_rho_v = 0.0;
    int num_intervals = (int)h274->num_intensity_intervals[c];

    for (int i = 0; i < 256; i++) {
        int match_s = -1;
        for (int s = 0; s < num_intervals && s < 256; s++) {
            if (h274->intensity_interval_lower_bound[c][s] <= (uint8_t)i &&
                (uint8_t)i <= h274->intensity_interval_upper_bound[c][s]) {
                match_s = s;
                break;
            }
        }
        if (match_s < 0) {
            desc->sigma[c][i] = 0.0f;
            continue;
        }
        int16_t scale = h274->comp_model_value[c][match_s][0];
        int cut_h = (h274->num_model_values[c] >= 2) ? h274->comp_model_value[c][match_s][1] : 8;
        int cut_v = (h274->num_model_values[c] >= 3) ? h274->comp_model_value[c][match_s][2] : 8;
        if (c > 0) {
            scale >>= 1;
            cut_h = (cut_h * 2 > 14) ? 14 : (cut_h * 2);
            cut_v = (cut_v * 2 > 14) ? 14 : (cut_v * 2);
        }
        int row = (cut_v - 2) * 13 + (cut_h - 2);
        const PelH274GrainStat *stat = &pel_h274_grain_stats[row];
        float sig_lin = (float)scale * stat->gain * factor;
        float sig = quantized ? pel_quantized_sigma(sig_lin, PEL_QUANT_FLOOR_OFFSET) : sig_lin;
        desc->sigma[c][i] = sig;

        double w = (double)sig * (double)sig;
        sum_w += w;
        sum_rho_h += w * (double)stat->rho_h;
        sum_rho_v += w * (double)stat->rho_v;
    }
    if (sum_w > 0.0) {
        desc->rho_h[c] = (float)(sum_rho_h / sum_w);
        desc->rho_v[c] = (float)(sum_rho_v / sum_w);
    }
}

static pel_result describe_h274_internal(const PelorusH274GrainParams *h274,
                                         PelorusGrainDescriptor *out, int quantized)
{
    if (h274 == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    pel_result r = validate_h274(h274);
    if (r != PEL_OK) {
        return r;
    }
    PelorusGrainDescriptor desc;
    memset(&desc, 0, sizeof(desc));
    float factor = (float)pow(2.0, (double)(2 - h274->log2_scale_factor));

    for (int c = 0; c < 3; c++) {
        if (!h274->component_model_present[c]) {
            continue;
        }
        describe_h274_component(h274, c, factor, &desc, quantized);
    }
    drop_silent_components(&desc);
    memcpy(out, &desc, sizeof(*out));
    return PEL_OK;
}

pel_result pel_grain_describe_h274(const PelorusH274GrainParams *h274, PelorusGrainDescriptor *out)
{
    return describe_h274_internal(h274, out, 1);
}

pel_result pel_grain_compare(const PelorusGrainDescriptor *a, const PelorusGrainDescriptor *b,
                             PelorusGrainFidelity *out)
{
    if (a == NULL || b == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    for (int c = 0; c < 3; c++) {
        if (a->present[c] > 1 || b->present[c] > 1) {
            return PEL_ERR_RANGE;
        }
    }
    PelorusGrainFidelity fid;
    memset(&fid, 0, sizeof(fid));
    double max_loss = 0.0;

    for (int c = 0; c < 3; c++) {
        if (!a->present[c] && !b->present[c]) {
            fid.sigma_rel_rms[c] = 0.0;
            fid.rho_h_delta[c] = 0.0;
            fid.rho_v_delta[c] = 0.0;
        } else if (a->present[c] != b->present[c]) {
            fid.sigma_rel_rms[c] = 1.0;
            fid.rho_h_delta[c] = fabs((double)a->rho_h[c] - (double)b->rho_h[c]);
            fid.rho_v_delta[c] = fabs((double)a->rho_v[c] - (double)b->rho_v[c]);
        } else {
            double sum_diff_sq = 0.0;
            double sum_a_sq = 0.0;
            for (int i = 0; i < 256; i++) {
                double diff = (double)a->sigma[c][i] - (double)b->sigma[c][i];
                sum_diff_sq += diff * diff;
                sum_a_sq += (double)a->sigma[c][i] * (double)a->sigma[c][i];
            }
            double rms_diff = sqrt(sum_diff_sq / 256.0);
            double rms_a = sqrt(sum_a_sq / 256.0);
            double denom = (rms_a > 1e-9) ? rms_a : 1e-9;
            fid.sigma_rel_rms[c] = rms_diff / denom;
            fid.rho_h_delta[c] = fabs((double)a->rho_h[c] - (double)b->rho_h[c]);
            fid.rho_v_delta[c] = fabs((double)a->rho_v[c] - (double)b->rho_v[c]);
        }
        double comp_loss = fid.sigma_rel_rms[c] + 0.5 * (fid.rho_h_delta[c] + fid.rho_v_delta[c]);
        if (comp_loss > max_loss) {
            max_loss = comp_loss;
        }
    }
    fid.loss = max_loss;
    memcpy(out, &fid, sizeof(*out));
    return PEL_OK;
}

static void find_best_cutoff_luma(float rho_h, float rho_v, int *cut_h_out, int *cut_v_out,
                                  const PelH274GrainStat **stat_out)
{
    double best_dist = 1e30;
    int best_row = 0;
    for (int r = 0; r < 169; r++) {
        double dh = (double)pel_h274_grain_stats[r].rho_h - (double)rho_h;
        double dv = (double)pel_h274_grain_stats[r].rho_v - (double)rho_v;
        double dist = dh * dh + dv * dv;
        if (dist < best_dist) {
            best_dist = dist;
            best_row = r;
        }
    }
    *cut_h_out = pel_h274_grain_stats[best_row].cut_h;
    *cut_v_out = pel_h274_grain_stats[best_row].cut_v;
    *stat_out = &pel_h274_grain_stats[best_row];
}

static void find_best_cutoff_chroma(float rho_h, float rho_v, int *pre_h_out, int *pre_v_out,
                                    const PelH274GrainStat **stat_out)
{
    double best_dist = 1e30;
    int best_pre_h = 2;
    int best_pre_v = 2;
    int best_row = 0;

    for (int pre_v = 2; pre_v <= 7; pre_v++) {
        int eff_v = pre_v * 2;
        if (eff_v > 14) {
            eff_v = 14;
        }
        for (int pre_h = 2; pre_h <= 7; pre_h++) {
            int eff_h = pre_h * 2;
            if (eff_h > 14) {
                eff_h = 14;
            }
            int r = (eff_v - 2) * 13 + (eff_h - 2);
            double dh = (double)pel_h274_grain_stats[r].rho_h - (double)rho_h;
            double dv = (double)pel_h274_grain_stats[r].rho_v - (double)rho_v;
            double dist = dh * dh + dv * dv;
            if (dist < best_dist) {
                best_dist = dist;
                best_pre_h = pre_h;
                best_pre_v = pre_v;
                best_row = r;
            }
        }
    }
    *pre_h_out = best_pre_h;
    *pre_v_out = best_pre_v;
    *stat_out = &pel_h274_grain_stats[best_row];
}

static int pick_h274_log2_scale(const PelorusGrainDescriptor *desc,
                                const PelH274GrainStat *stats_comp[3])
{
    for (int log2 = 7; log2 >= 2; log2--) {
        int all_fit = 1;
        double factor = pow(2.0, (double)(2 - log2));
        for (int c = 0; c < 3; c++) {
            if (!desc->present[c] || stats_comp[c] == NULL) {
                continue;
            }
            double gain_eff = (double)stats_comp[c]->gain * factor;
            double mult = (c > 0) ? 2.0 : 1.0;
            for (int i = 0; i < 256; i++) {
                double s = round(mult * (double)desc->sigma[c][i] / gain_eff);
                if (s > 255.0) {
                    all_fit = 0;
                    break;
                }
            }
            if (!all_fit) {
                break;
            }
        }
        if (all_fit) {
            return log2;
        }
    }
    return 2;
}

static void build_h274_intervals(const PelorusGrainDescriptor *desc, int c, int log2_scale,
                                 int cut_h, int cut_v, const PelH274GrainStat *stat,
                                 PelorusH274GrainParams *out)
{
    int scale_vals[256];
    double factor = pow(2.0, (double)(2 - log2_scale));
    double gain_eff = (double)stat->gain * factor;
    double mult = (c > 0) ? 2.0 : 1.0;

    for (int i = 0; i < 256; i++) {
        double s = round(mult * (double)desc->sigma[c][i] / gain_eff);
        if (s < 0.0) {
            s = 0.0;
        }
        if (s > 255.0) {
            s = 255.0;
        }
        scale_vals[i] = (int)s;
    }

    int num_intervals = 0;
    int i = 0;
    while (i < 256) {
        int cur_scale = scale_vals[i];
        int start = i;
        while (i + 1 < 256 && scale_vals[i + 1] == cur_scale) {
            i++;
        }
        int end = i;
        if (cur_scale > 0 && num_intervals < 256) {
            out->intensity_interval_lower_bound[c][num_intervals] = (uint8_t)start;
            out->intensity_interval_upper_bound[c][num_intervals] = (uint8_t)end;
            out->comp_model_value[c][num_intervals][0] = (int16_t)cur_scale;
            out->comp_model_value[c][num_intervals][1] = (int16_t)cut_h;
            out->comp_model_value[c][num_intervals][2] = (int16_t)cut_v;
            num_intervals++;
        }
        i++;
    }
    if (num_intervals == 0) {
        out->component_model_present[c] = 0;
        out->num_intensity_intervals[c] = 0;
        out->num_model_values[c] = 0;
    } else {
        out->component_model_present[c] = 1;
        out->num_intensity_intervals[c] = (uint16_t)num_intervals;
        out->num_model_values[c] = 3;
    }
}

static pel_result compute_fidelity_aom_to_h274(const PelorusFilmGrainSection *aom,
                                               const PelorusH274GrainParams *h274,
                                               PelorusGrainFidelity *fidelity)
{
    if (fidelity == NULL) {
        return PEL_OK;
    }
    PelorusGrainDescriptor aom_pub;
    PelorusGrainDescriptor h274_pub;
    memset(&aom_pub, 0, sizeof(aom_pub));
    memset(&h274_pub, 0, sizeof(h274_pub));
    pel_result r = pel_grain_describe_aom(aom, &aom_pub);
    if (r != PEL_OK) {
        return r;
    }
    r = pel_grain_describe_h274(h274, &h274_pub);
    if (r != PEL_OK) {
        return r;
    }
    return pel_grain_compare(&aom_pub, &h274_pub, fidelity);
}

pel_result pel_grain_aom_to_h274(const PelorusFilmGrainSection *aom, PelorusH274GrainParams *out,
                                 PelorusGrainFidelity *fidelity)
{
    if (aom == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    PelorusGrainDescriptor aom_desc;
    memset(&aom_desc, 0, sizeof(aom_desc));
    pel_result r = describe_aom_internal(aom, &aom_desc, 0);
    if (r != PEL_OK) {
        return r;
    }
    PelorusH274GrainParams local_h274;
    memset(&local_h274, 0, sizeof(local_h274));
    local_h274.model_id = 0;
    local_h274.blending_mode_id = 0;

    int cut_h[3] = {0};
    int cut_v[3] = {0};
    const PelH274GrainStat *stats_comp[3] = {NULL, NULL, NULL};

    if (aom_desc.present[0]) {
        find_best_cutoff_luma(aom_desc.rho_h[0], aom_desc.rho_v[0], &cut_h[0], &cut_v[0],
                              &stats_comp[0]);
    }
    for (int c = 1; c < 3; c++) {
        if (aom_desc.present[c]) {
            find_best_cutoff_chroma(aom_desc.rho_h[c], aom_desc.rho_v[c], &cut_h[c], &cut_v[c],
                                    &stats_comp[c]);
        }
    }

    local_h274.log2_scale_factor = pick_h274_log2_scale(&aom_desc, stats_comp);
    for (int c = 0; c < 3; c++) {
        if (aom_desc.present[c] && stats_comp[c] != NULL) {
            build_h274_intervals(&aom_desc, c, local_h274.log2_scale_factor, cut_h[c], cut_v[c],
                                 stats_comp[c], &local_h274);
        }
    }

    r = compute_fidelity_aom_to_h274(aom, &local_h274, fidelity);
    if (r != PEL_OK) {
        return r;
    }
    memcpy(out, &local_h274, sizeof(*out));
    return PEL_OK;
}

static void find_h274_dominant_cutoffs(const PelorusH274GrainParams *h274,
                                       const PelorusGrainDescriptor *desc, int c,
                                       float *target_rho_h, float *target_rho_v)
{
    int best_s = 0;
    double best_sum_sq = -1.0;
    int num_intervals = (int)h274->num_intensity_intervals[c];

    for (int s = 0; s < num_intervals && s < 256; s++) {
        int low = h274->intensity_interval_lower_bound[c][s];
        int high = h274->intensity_interval_upper_bound[c][s];
        double sum_sq = 0.0;
        for (int i = low; i <= high; i++) {
            double sig = (double)desc->sigma[c][i];
            sum_sq += sig * sig;
        }
        if (sum_sq > best_sum_sq) {
            best_sum_sq = sum_sq;
            best_s = s;
        }
    }

    int cut_h = (h274->num_model_values[c] >= 2) ? h274->comp_model_value[c][best_s][1] : 8;
    int cut_v = (h274->num_model_values[c] >= 3) ? h274->comp_model_value[c][best_s][2] : 8;
    if (c > 0) {
        cut_h = (cut_h * 2 > 14) ? 14 : (cut_h * 2);
        cut_v = (cut_v * 2 > 14) ? 14 : (cut_v * 2);
    }
    int row = (cut_v - 2) * 13 + (cut_h - 2);
    *target_rho_h = pel_h274_grain_stats[row].rho_h;
    *target_rho_v = pel_h274_grain_stats[row].rho_v;
}

static int8_t clamp_to_i8(double v)
{
    long iv = (long)round(v);
    if (iv < -128) {
        return -128;
    }
    if (iv > 127) {
        return 127;
    }
    return (int8_t)iv;
}

static void setup_aom_ar_coeffs(float rho_h, float rho_v, int8_t coeffs[5])
{
    double a = (double)rho_h;
    double b = (double)rho_v;
    coeffs[0] = clamp_to_i8(-a * b * 128.0);
    coeffs[1] = clamp_to_i8(b * 128.0);
    coeffs[2] = 0;
    coeffs[3] = clamp_to_i8(a * 128.0);
    coeffs[4] = 0;
}

static int pick_aom_scaling_shift(const PelorusGrainDescriptor *desc, const float g_ar[3],
                                  float sigma_w)
{
    for (int shift = 11; shift >= 8; shift--) {
        int all_fit = 1;
        float shift_mult = (float)(1 << shift);
        for (int c = 0; c < 3; c++) {
            if (!desc->present[c]) {
                continue;
            }
            int n_pts = (c == 0) ? 14 : 10;
            float denom = sigma_w * g_ar[c];
            for (int k = 0; k < n_pts; k++) {
                int x_k = (int)round((double)k * 255.0 / (double)(n_pts - 1));
                double y_k =
                    round((double)desc->sigma[c][x_k] * (double)shift_mult / (double)denom);
                if (y_k > 255.0) {
                    all_fit = 0;
                    break;
                }
            }
            if (!all_fit) {
                break;
            }
        }
        if (all_fit) {
            return shift;
        }
    }
    return 8;
}

static void setup_aom_ar_from_h274(const PelorusH274GrainParams *h274,
                                   const PelorusGrainDescriptor *h274_desc,
                                   PelorusFilmGrainSection *aom, float g_ar[3])
{
    g_ar[0] = 1.0f;
    g_ar[1] = 1.0f;
    g_ar[2] = 1.0f;
    for (int c = 0; c < 3; c++) {
        if (!h274_desc->present[c]) {
            continue;
        }
        float rho_h = 0.0f;
        float rho_v = 0.0f;
        find_h274_dominant_cutoffs(h274, h274_desc, c, &rho_h, &rho_v);
        int8_t coeffs[5];
        setup_aom_ar_coeffs(rho_h, rho_v, coeffs);
        if (c == 0) {
            memcpy(aom->ar_coeffs_y, coeffs, 4);
        } else {
            memcpy(aom->ar_coeffs_uv[c - 1], coeffs, 5);
        }
        float dummy_h = 0.0f;
        float dummy_v = 0.0f;
        calc_ar_impulse(1, 7, coeffs, &g_ar[c], &dummy_h, &dummy_v);
    }
}

static void setup_aom_points_from_h274(const PelorusGrainDescriptor *desc, const float g_ar[3],
                                       float sigma_w, int shift, PelorusFilmGrainSection *aom)
{
    float shift_mult = (float)(1 << shift);
    if (desc->present[0]) {
        aom->num_y_points = 14;
        float denom = sigma_w * g_ar[0];
        for (int k = 0; k < 14; k++) {
            int x_k = (int)round((double)k * 255.0 / 13.0);
            double y_k = round((double)desc->sigma[0][x_k] * (double)shift_mult / (double)denom);
            if (y_k < 0.0) {
                y_k = 0.0;
            }
            if (y_k > 255.0) {
                y_k = 255.0;
            }
            aom->y_points[k][0] = (uint8_t)x_k;
            aom->y_points[k][1] = (uint8_t)y_k;
        }
    }
    for (int c = 1; c < 3; c++) {
        if (desc->present[c]) {
            aom->num_uv_points[c - 1] = 10;
            float denom = sigma_w * g_ar[c];
            for (int k = 0; k < 10; k++) {
                int x_k = (int)round((double)k * 255.0 / 9.0);
                double y_k =
                    round((double)desc->sigma[c][x_k] * (double)shift_mult / (double)denom);
                if (y_k < 0.0) {
                    y_k = 0.0;
                }
                if (y_k > 255.0) {
                    y_k = 255.0;
                }
                aom->uv_points[c - 1][k][0] = (uint8_t)x_k;
                aom->uv_points[c - 1][k][1] = (uint8_t)y_k;
            }
        }
    }
}

static void init_absent_aom(uint64_t seed, PelorusFilmGrainSection *aom)
{
    memset(aom, 0, sizeof(*aom));
    aom->seed = seed;
    aom->grain_model = PEL_GRAIN_AOM;
    aom->overlap_flag = 1;
    aom->apply = 0;
    aom->scaling_shift = 8;
    aom->ar_coeff_shift = 7;
    aom->uv_mult[0] = 64;
    aom->uv_mult[1] = 64;
}

static pel_result compute_fidelity_h274_to_aom(const PelorusH274GrainParams *h274,
                                               const PelorusFilmGrainSection *aom,
                                               PelorusGrainFidelity *fidelity)
{
    if (fidelity == NULL) {
        return PEL_OK;
    }
    PelorusGrainDescriptor h274_pub;
    PelorusGrainDescriptor aom_pub;
    memset(&h274_pub, 0, sizeof(h274_pub));
    memset(&aom_pub, 0, sizeof(aom_pub));
    pel_result r = pel_grain_describe_h274(h274, &h274_pub);
    if (r != PEL_OK) {
        return r;
    }
    r = pel_grain_describe_aom(aom, &aom_pub);
    if (r != PEL_OK) {
        return r;
    }
    return pel_grain_compare(&h274_pub, &aom_pub, fidelity);
}

pel_result pel_grain_h274_to_aom(const PelorusH274GrainParams *h274, uint64_t seed,
                                 PelorusFilmGrainSection *out, PelorusGrainFidelity *fidelity)
{
    if (h274 == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    if (seed > 65535) {
        return PEL_ERR_RANGE;
    }
    PelorusGrainDescriptor h274_desc;
    memset(&h274_desc, 0, sizeof(h274_desc));
    pel_result r = describe_h274_internal(h274, &h274_desc, 0);
    if (r != PEL_OK) {
        return r;
    }

    PelorusFilmGrainSection local_aom;
    int any_present = h274_desc.present[0] || h274_desc.present[1] || h274_desc.present[2];
    if (!any_present) {
        init_absent_aom(seed, &local_aom);
        r = compute_fidelity_h274_to_aom(h274, &local_aom, fidelity);
        if (r != PEL_OK) {
            return r;
        }
        memcpy(out, &local_aom, sizeof(*out));
        return PEL_OK;
    }

    init_absent_aom(seed, &local_aom);
    local_aom.apply = 1;
    local_aom.ar_coeff_lag = 1;
    local_aom.grain_scale_shift = 0;

    float g_ar[3];
    setup_aom_ar_from_h274(h274, &h274_desc, &local_aom, g_ar);

    float sigma_w = PEL_AOM_GAUSSIAN_SEQUENCE_RMS / 16.0f;
    int shift = pick_aom_scaling_shift(&h274_desc, g_ar, sigma_w);
    local_aom.scaling_shift = shift;
    setup_aom_points_from_h274(&h274_desc, g_ar, sigma_w, shift, &local_aom);
    if (!h274_desc.present[0]) {
        /* AV1 4:2:0 codes no chroma points when num_y_points is 0 (AV1 spec
         * film_grain_params); a flat zero luma curve keeps chroma grain. */
        local_aom.num_y_points = 2;
        local_aom.y_points[1][0] = 255;
    }

    r = compute_fidelity_h274_to_aom(h274, &local_aom, fidelity);
    if (r != PEL_OK) {
        return r;
    }
    memcpy(out, &local_aom, sizeof(*out));
    return PEL_OK;
}
