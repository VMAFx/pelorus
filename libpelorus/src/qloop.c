/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/qloop.h"

#include <math.h>
#include <string.h>

pel_result pel_qloop_validate(const PelQloopConfig *c)
{
    if (c == NULL) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(c->target) || !isfinite(c->tolerance) || c->tolerance <= 0.0) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(c->knob_min) || !isfinite(c->knob_max) || c->knob_min >= c->knob_max) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(c->knob_start) || c->knob_start < c->knob_min || c->knob_start > c->knob_max) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(c->resolution) || c->resolution <= 0.0 ||
        c->resolution > (c->knob_max - c->knob_min)) {
        return PEL_ERR_INVALID;
    }
    if (c->max_iter < 1u || c->max_iter > PEL_QLOOP_MAX_ITER) {
        return PEL_ERR_INVALID;
    }
    if (c->direction != -1 && c->direction != 1) {
        return PEL_ERR_INVALID;
    }
    return PEL_OK;
}

static pel_result validate_history(const PelQloopConfig *c, const PelQloopObs *hist, size_t n_hist)
{
    size_t i;
    size_t j;

    if (n_hist > 0 && hist == NULL) {
        return PEL_ERR_INVALID;
    }
    for (i = 0; i < n_hist && i < PEL_QLOOP_MAX_ITER; i++) {
        if (!isfinite(hist[i].knob) || !isfinite(hist[i].score)) {
            return PEL_ERR_INVALID;
        }
        if (hist[i].knob < c->knob_min || hist[i].knob > c->knob_max) {
            return PEL_ERR_INVALID;
        }
    }
    for (i = 0; i < n_hist && i < PEL_QLOOP_MAX_ITER; i++) {
        for (j = i + 1u; j < n_hist && j < PEL_QLOOP_MAX_ITER; j++) {
            double q_diff;
            if (fabs(hist[i].knob - hist[j].knob) < 1e-6 * c->resolution) {
                return PEL_ERR_INVALID;
            }
            q_diff = (c->direction == -1) ? (hist[j].knob - hist[i].knob) :
                                            (hist[i].knob - hist[j].knob);
            if (q_diff > 0.0) {
                if (hist[i].score < hist[j].score - c->tolerance) {
                    return PEL_ERR_INVALID;
                }
            } else if (q_diff < 0.0) {
                if (hist[j].score < hist[i].score - c->tolerance) {
                    return PEL_ERR_INVALID;
                }
            }
        }
    }
    return PEL_OK;
}

static double round_to_grid(double val, double min, double max, double res)
{
    double step = floor((val - min) / res + 0.5);
    double rounded = min + step * res;
    if (rounded < min) {
        rounded = min;
    }
    if (rounded > max) {
        rounded = max;
    }
    return rounded;
}

typedef struct BracketAnalysis {
    int has_pass;
    int has_fail;
    double pass_knob;
    double pass_score;
    double fail_knob;
    double fail_score;
    size_t pass_idx;
    size_t fail_idx;
    int has_in_band;
    double in_band_knob;
    size_t in_band_idx;
    double highest_score_knob;
    double highest_score;
    int best_end_seen;
    double best_end_score;
    size_t best_end_idx;
    int worst_end_seen;
    double worst_end_score;
    size_t worst_end_idx;
} BracketAnalysis;

static void analyze_history(const PelQloopConfig *c, const PelQloopObs *hist, size_t n_hist,
                            BracketAnalysis *a)
{
    double best_end = (c->direction == -1) ? c->knob_min : c->knob_max;
    double worst_end = (c->direction == -1) ? c->knob_max : c->knob_min;
    size_t i;

    memset(a, 0, sizeof(*a));
    for (i = 0; i < n_hist && i < PEL_QLOOP_MAX_ITER; i++) {
        double k = hist[i].knob;
        double s = hist[i].score;

        if (i == 0 || s > a->highest_score) {
            a->highest_score = s;
            a->highest_score_knob = k;
        }
        if (fabs(k - best_end) < 1e-6 * c->resolution) {
            a->best_end_seen = 1;
            a->best_end_score = s;
            a->best_end_idx = i;
        }
        if (fabs(k - worst_end) < 1e-6 * c->resolution) {
            a->worst_end_seen = 1;
            a->worst_end_score = s;
            a->worst_end_idx = i;
        }
        if (s >= c->target && s <= c->target + c->tolerance) {
            int update = !a->has_in_band;
            if (a->has_in_band) {
                update = (c->direction == -1) ? (k > a->in_band_knob) : (k < a->in_band_knob);
            }
            if (update) {
                a->has_in_band = 1;
                a->in_band_knob = k;
                a->in_band_idx = i;
            }
        }
        if (s >= c->target) {
            int update = !a->has_pass;
            if (a->has_pass) {
                update = (c->direction == -1) ? (k > a->pass_knob) : (k < a->pass_knob);
            }
            if (update) {
                a->has_pass = 1;
                a->pass_knob = k;
                a->pass_score = s;
                a->pass_idx = i;
            }
        } else {
            int update = !a->has_fail;
            if (a->has_fail) {
                update = (c->direction == -1) ? (k < a->fail_knob) : (k > a->fail_knob);
            }
            if (update) {
                a->has_fail = 1;
                a->fail_knob = k;
                a->fail_score = s;
                a->fail_idx = i;
            }
        }
    }
}

static int check_terminal(const PelQloopConfig *c, const BracketAnalysis *a, size_t n_hist,
                          PelQloopStep *out)
{
    if (a->has_in_band) {
        out->state = (uint32_t)PEL_QLOOP_DONE;
        out->knob = a->in_band_knob;
        out->iteration = (uint32_t)(n_hist - 1u);
        return 1;
    }
    if (a->worst_end_seen && a->worst_end_score >= c->target) {
        out->state = (uint32_t)PEL_QLOOP_DONE;
        out->knob = (c->direction == -1) ? c->knob_max : c->knob_min;
        out->iteration = (uint32_t)(n_hist - 1u);
        return 1;
    }
    if (a->best_end_seen && a->best_end_score < c->target) {
        out->state = (uint32_t)PEL_QLOOP_UNREACHABLE;
        out->knob = (c->direction == -1) ? c->knob_min : c->knob_max;
        out->iteration = (uint32_t)(n_hist - 1u);
        return 1;
    }
    if (a->has_pass && a->has_fail) {
        double gap = fabs(a->pass_knob - a->fail_knob);
        if (gap <= c->resolution + 1e-9) {
            out->state = (uint32_t)PEL_QLOOP_DONE;
            out->knob = a->pass_knob;
            out->iteration = (uint32_t)(n_hist - 1u);
            return 1;
        }
    }
    if (n_hist >= c->max_iter) {
        out->state = (uint32_t)PEL_QLOOP_EXHAUSTED;
        out->knob = a->has_pass ? a->pass_knob : a->highest_score_knob;
        out->iteration = c->max_iter;
        return 1;
    }
    return 0;
}

static double compute_bracketed_probe(const PelQloopConfig *c, const PelQloopObs *hist,
                                      size_t n_hist, const BracketAnalysis *a)
{
    double k_low = fmin(a->pass_knob, a->fail_knob);
    double k_high = fmax(a->pass_knob, a->fail_knob);
    double F_pass = a->pass_score - c->target;
    double F_fail = a->fail_score - c->target;
    double denom;
    double k_cand;
    double k_left;
    double k_right;
    double k_probe;

    if (n_hist >= 2u) {
        double f_last = hist[n_hist - 1u].score - c->target;
        double f_prev = hist[n_hist - 2u].score - c->target;
        if (f_last < 0.0 && f_prev < 0.0) {
            F_pass *= 0.5;
        } else if (f_last >= 0.0 && f_prev >= 0.0) {
            F_fail *= 0.5;
        }
    }

    denom = F_fail - F_pass;
    if (fabs(denom) < 1e-12) {
        k_cand = (k_low + k_high) / 2.0;
    } else {
        k_cand = a->fail_knob - F_fail * (a->fail_knob - a->pass_knob) / denom;
    }

    k_left = k_low + c->resolution / 2.0;
    k_right = k_high - c->resolution / 2.0;
    if (k_cand < k_left || k_cand > k_right || !isfinite(k_cand)) {
        k_cand = (k_low + k_high) / 2.0;
    }

    k_probe = round_to_grid(k_cand, c->knob_min, c->knob_max, c->resolution);
    if (k_probe <= k_low) {
        k_probe = k_low + c->resolution;
    } else if (k_probe >= k_high) {
        k_probe = k_high - c->resolution;
    }
    return k_probe;
}

static double compute_unbracketed_probe(const PelQloopConfig *c, const PelQloopObs *hist,
                                        size_t n_hist)
{
    double step = (c->knob_max - c->knob_min) / 4.0;
    double k_cand;
    double k_probe;

    if (n_hist == 1u) {
        double f0 = hist[0].score - c->target;
        double dir_step = -(double)c->direction * ((f0 < 0.0) ? -1.0 : 1.0) * step;
        k_cand = hist[0].knob + dir_step;
        k_probe = round_to_grid(k_cand, c->knob_min, c->knob_max, c->resolution);
        if (fabs(k_probe - hist[0].knob) < 1e-6 * c->resolution) {
            double min_step = -(double)c->direction * ((f0 < 0.0) ? -c->resolution : c->resolution);
            k_probe =
                round_to_grid(hist[0].knob + min_step, c->knob_min, c->knob_max, c->resolution);
        }
        return k_probe;
    }

    double k1 = hist[n_hist - 2u].knob;
    double f1 = hist[n_hist - 2u].score - c->target;
    double k2 = hist[n_hist - 1u].knob;
    double f2 = hist[n_hist - 1u].score - c->target;
    double df = f2 - f1;
    int wrong_slope = (c->direction == -1) ? ((k2 > k1 && f2 > f1) || (k2 < k1 && f2 < f1)) :
                                             ((k2 > k1 && f2 < f1) || (k2 < k1 && f2 > f1));

    if (fabs(df) < 1e-9 || wrong_slope) {
        double dir_step = -(double)c->direction * ((f2 < 0.0) ? -1.0 : 1.0) * step;
        k_cand = k2 + dir_step;
    } else {
        k_cand = k2 - f2 * (k2 - k1) / df;
    }

    k_probe = round_to_grid(k_cand, c->knob_min, c->knob_max, c->resolution);
    if (fabs(k_probe - k2) < 1e-6 * c->resolution) {
        double min_step = -(double)c->direction * ((f2 < 0.0) ? -c->resolution : c->resolution);
        k_probe = round_to_grid(k2 + min_step, c->knob_min, c->knob_max, c->resolution);
    }
    return k_probe;
}

pel_result pel_qloop_next(const PelQloopConfig *c, const PelQloopObs *hist, size_t n_hist,
                          PelQloopStep *out)
{
    BracketAnalysis a;
    pel_result res;

    if (c == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    res = pel_qloop_validate(c);
    if (res != PEL_OK) {
        return res;
    }
    if (n_hist > PEL_QLOOP_MAX_ITER) {
        return PEL_ERR_RANGE;
    }
    res = validate_history(c, hist, n_hist);
    if (res != PEL_OK) {
        return res;
    }

    if (n_hist == 0) {
        out->state = (uint32_t)PEL_QLOOP_PROBE;
        out->iteration = 0u;
        out->knob = c->knob_start;
        return PEL_OK;
    }

    analyze_history(c, hist, n_hist, &a);
    if (check_terminal(c, &a, n_hist, out)) {
        return PEL_OK;
    }

    out->state = (uint32_t)PEL_QLOOP_PROBE;
    out->iteration = (uint32_t)n_hist;
    if (a.has_pass && a.has_fail) {
        out->knob = compute_bracketed_probe(c, hist, n_hist, &a);
    } else {
        out->knob = compute_unbracketed_probe(c, hist, n_hist);
    }
    return PEL_OK;
}
