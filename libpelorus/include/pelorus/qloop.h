/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_QLOOP_H
#define PELORUS_QLOOP_H

#include "pelorus/pelorus.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Maximum number of search iterations permitted per loop session (HISS-02). */
#define PEL_QLOOP_MAX_ITER 16u

/* Controller state returned in PelQloopStep. */
enum pel_qloop_state {
    PEL_QLOOP_PROBE = 1,
    PEL_QLOOP_DONE = 2,
    PEL_QLOOP_UNREACHABLE = 3,
    PEL_QLOOP_EXHAUSTED = 4
};

/*
 * Configuration for one scalar quality-threshold loop session.
 *
 * The knob represents a monotone control variable governing video quality.
 * In adherence to ADR-0142 and ADR-0132, the knob is strictly an encoder-native
 * rate-control parameter (such as CRF) or a Pelorus pre-encode filter strength
 * governed by a filter knob contract (ADR-0196), never a source-side QP map
 * or per-macroblock delta-QP.
 *
 * Search direction:
 *   -1: score falls as knob rises (e.g. CRF: higher knob -> lower quality).
 *   +1: score rises as knob rises (e.g. bitrate multiplier or filter strength).
 */
typedef struct PelQloopConfig {
    double target; /* target quality score (finite) */
    double
        tolerance; /* tolerance band width > 0 (done when score in [target, target + tolerance]) */
    double knob_min;   /* minimum knob bound (finite, < knob_max) */
    double knob_max;   /* maximum knob bound (finite, > knob_min) */
    double knob_start; /* initial probe value in [knob_min, knob_max] */
    double resolution; /* quantization grid step > 0 and <= (knob_max - knob_min) */
    uint32_t max_iter; /* maximum search iterations in [1, PEL_QLOOP_MAX_ITER] */
    int32_t direction; /* search direction: -1 (CRF) or +1 */
} PelQloopConfig;

/* One observed probe in the controller search history. */
typedef struct PelQloopObs {
    double knob;  /* probed knob value */
    double score; /* measured quality score */
} PelQloopObs;

/* Output step emitted by pel_qloop_next. */
typedef struct PelQloopStep {
    uint32_t state;     /* enum pel_qloop_state */
    uint32_t iteration; /* 0-based iteration index */
    double knob;        /* next probe to evaluate (PROBE) or chosen knob (DONE, EXHAUSTED) */
} PelQloopStep;

/*
 * Validate loop configuration parameters.
 *
 * Rejects NULL config, non-finite scalars, tolerance <= 0, knob_min >= knob_max,
 * knob_start outside [knob_min, knob_max], resolution <= 0 or > (knob_max - knob_min),
 * max_iter 0 or > PEL_QLOOP_MAX_ITER, or direction not in {-1, +1} with PEL_ERR_INVALID.
 */
pel_result pel_qloop_validate(const PelQloopConfig *c);

/*
 * Compute the next search step from observation history.
 *
 * Pure, non-allocating, deterministic controller. The controller is completely
 * stateless across calls: all decisions are derived purely from (config, hist).
 * A caller crash or process restart loses nothing, as the loop state is 100%
 * replayable from telemetry logs.
 *
 * The loop operates at temporal window or shot boundaries (e.g. per-shot CRF
 * steering). In adherence to ADR-0142, the controlled knob is an encoder rate-control
 * target or a filter knob contract strength, never a per-block QP map.
 *
 * Search strategy:
 *   1. Initial step (n_hist == 0): returns PROBE at knob_start (iteration 0).
 *   2. Terminal checks:
 *      - An observed score lies in [target, target + tolerance] -> DONE (cheapest chosen).
 *      - Best passing and best failing observed knobs are within resolution -> DONE (passing chosen).
 *      - Best-quality end observed with score < target -> UNREACHABLE.
 *      - Worst-quality end observed with score >= target -> DONE at that end (cheapest allowed).
 *      - n_hist >= max_iter without DONE -> EXHAUSTED (cheapest passing knob if any, else highest score).
 *   3. If a bracket exists, steps via regula falsi with the Illinois modification
 *      on (knob, score - target), clamped strictly inside the bracket and at least
 *      resolution/2 from both ends (or bracket midpoint).
 *   4. Before a bracket exists, steps toward target via secant from the last two
 *      points or by (max - min)/4 from one point, clamped to [knob_min, knob_max].
 *   5. All probes are rounded to an integer multiple of resolution from knob_min.
 *
 * Returns:
 *   PEL_OK on success.
 *   PEL_ERR_RANGE if n_hist > PEL_QLOOP_MAX_ITER.
 *   PEL_ERR_INVALID if c or out is NULL, config validation fails, hist is NULL
 *   when n_hist > 0, history contains non-finite values, duplicate knobs,
 *   out-of-range knobs, or non-monotone observations where a higher-quality knob
 *   scored lower by more than tolerance.
 */
pel_result pel_qloop_next(const PelQloopConfig *c, const PelQloopObs *hist, size_t n_hist,
                          PelQloopStep *out);

#ifdef __cplusplus
}
#endif

#endif /* PELORUS_QLOOP_H */
