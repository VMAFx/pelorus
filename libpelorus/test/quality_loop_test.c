/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/qloop.h"

#include <math.h>
#include <stdint.h>
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

static double model_linear(double crf)
{
    return 100.0 - 1.5 * (crf - 10.0);
}

static double model_convex(double crf)
{
    double d = crf - 10.0;
    return 100.0 - 0.02 * d * d;
}

static double model_step(double crf)
{
    return (crf <= 21.0) ? 92.0 : 88.0;
}

static double model_dir_pos(double knob)
{
    return 50.0 + 2.0 * (knob - 10.0);
}

static void check_cheaper_knob(double (*eval_fn)(double), const PelQloopConfig *cfg, double knob)
{
    if (cfg->direction == -1) {
        double cheaper_knob = knob + cfg->resolution;
        if (cheaper_knob <= cfg->knob_max) {
            CHECK(eval_fn(cheaper_knob) < cfg->target);
        }
    } else {
        double cheaper_knob = knob - cfg->resolution;
        if (cheaper_knob >= cfg->knob_min) {
            CHECK(eval_fn(cheaper_knob) < cfg->target);
        }
    }
}

static void test_monotone_model(const char *name, double (*eval_fn)(double),
                                const PelQloopConfig *cfg, uint32_t expected_iter)
{
    PelQloopObs hist[PEL_QLOOP_MAX_ITER];
    size_t n_hist = 0;
    PelQloopStep step;
    uint32_t loop_count = 0;

    memset(&step, 0, sizeof(step));
    while (loop_count < cfg->max_iter + 2u) {
        pel_result res = pel_qloop_next(cfg, hist, n_hist, &step);
        CHECK(res == PEL_OK);
        if (step.state != PEL_QLOOP_PROBE) {
            break;
        }
        CHECK(n_hist < cfg->max_iter);
        hist[n_hist].knob = step.knob;
        hist[n_hist].score = eval_fn(step.knob);
        n_hist++;
        loop_count++;
    }

    CHECK(loop_count <= cfg->max_iter + 1u);
    CHECK(step.state == PEL_QLOOP_DONE);
    CHECK(step.iteration == expected_iter);

    /* Chosen knob must pass target */
    double chosen_score = eval_fn(step.knob);
    CHECK(chosen_score >= cfg->target);

    /* One step cheaper must fail or be outside range */
    check_cheaper_knob(eval_fn, cfg, step.knob);
    (void)name;
}

static void test_synthetic_models(void)
{
    PelQloopConfig cfg_linear = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    /* Linear model converges at iteration 2 to CRF 16.5 */
    test_monotone_model("linear", model_linear, &cfg_linear, 2u);

    PelQloopConfig cfg_convex = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    /* Convex model converges at iteration 2 to CRF 32.0 */
    test_monotone_model("convex", model_convex, &cfg_convex, 2u);

    PelQloopConfig cfg_step = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    /* Step model converges at iteration 6 to CRF 21.0 */
    test_monotone_model("step", model_step, &cfg_step, 6u);

    PelQloopConfig cfg_dir_pos = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 20.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = 1,
    };
    /* Direction +1 model converges at iteration 2 to knob 30.0 */
    test_monotone_model("dir_pos", model_dir_pos, &cfg_dir_pos, 2u);
}

static void test_planted_unreachable(void)
{
    PelQloopConfig cfg = {
        .target = 105.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    PelQloopObs hist[PEL_QLOOP_MAX_ITER];
    size_t n_hist = 0;
    PelQloopStep step;
    uint32_t loop_count = 0;

    memset(&step, 0, sizeof(step));
    while (loop_count < cfg.max_iter + 2u) {
        pel_result res = pel_qloop_next(&cfg, hist, n_hist, &step);
        CHECK(res == PEL_OK);
        if (step.state != PEL_QLOOP_PROBE) {
            break;
        }
        CHECK(n_hist < cfg.max_iter);
        hist[n_hist].knob = step.knob;
        hist[n_hist].score = model_linear(step.knob);
        n_hist++;
        loop_count++;
    }

    CHECK(step.state == PEL_QLOOP_UNREACHABLE);
    CHECK(step.knob == cfg.knob_min);
    CHECK(loop_count <= cfg.max_iter + 1u);
}

static void test_planted_oscillating(void)
{
    PelQloopConfig cfg = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    PelQloopObs hist[2];
    PelQloopStep step;

    /* CRF 28 is worse quality than CRF 20, but scored 70 while CRF 20 scored 65 */
    hist[0].knob = 28.0;
    hist[0].score = 70.0;
    hist[1].knob = 20.0;
    hist[1].score = 65.0;

    CHECK(pel_qloop_next(&cfg, hist, 2, &step) == PEL_ERR_INVALID);
}

static void test_planted_exhausted(void)
{
    PelQloopConfig cfg = {
        .target = 95.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 40.0,
        .resolution = 0.5,
        .max_iter = 3,
        .direction = -1,
    };
    PelQloopObs hist[PEL_QLOOP_MAX_ITER];
    size_t n_hist = 0;
    PelQloopStep step;
    uint32_t loop_count = 0;

    memset(hist, 0, sizeof(hist));
    memset(&step, 0, sizeof(step));
    while (loop_count < cfg.max_iter + 2u) {
        pel_result res = pel_qloop_next(&cfg, hist, n_hist, &step);
        CHECK(res == PEL_OK);
        if (step.state != PEL_QLOOP_PROBE) {
            break;
        }
        CHECK(n_hist < cfg.max_iter);
        hist[n_hist].knob = step.knob;
        /* Model that approaches target 95.0 slowly without reaching it in 3 steps */
        hist[n_hist].score = 80.0 + 3.0 * sqrt(fmax(0.0, 45.0 - step.knob));
        n_hist++;
        loop_count++;
    }

    CHECK(step.state == PEL_QLOOP_EXHAUSTED);
    CHECK(step.iteration == 3u);
    CHECK(loop_count == 3u);
    CHECK(loop_count <= cfg.max_iter + 1u);
}

static void test_boundary_iteration_0(void)
{
    PelQloopConfig cfg = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    PelQloopObs hist[1];
    PelQloopStep step;

    /* Initial call with empty history */
    CHECK(pel_qloop_next(&cfg, NULL, 0, &step) == PEL_OK);
    CHECK(step.state == PEL_QLOOP_PROBE);
    CHECK(step.iteration == 0u);
    CHECK(step.knob == 28.0);

    /* Observation lands right inside [90.0, 91.0] */
    hist[0].knob = 28.0;
    hist[0].score = 90.5;

    CHECK(pel_qloop_next(&cfg, hist, 1, &step) == PEL_OK);
    CHECK(step.state == PEL_QLOOP_DONE);
    CHECK(step.iteration == 0u);
    CHECK(step.knob == 28.0);
}

static void test_boundary_worst_end_passing(void)
{
    PelQloopConfig cfg = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    PelQloopObs hist[1];
    PelQloopStep step;

    /* Worst-quality end (knob_max 45.0 for direction -1) already scored 95.0 (> 91.0) */
    hist[0].knob = 45.0;
    hist[0].score = 95.0;

    CHECK(pel_qloop_next(&cfg, hist, 1, &step) == PEL_OK);
    CHECK(step.state == PEL_QLOOP_DONE);
    CHECK(step.knob == 45.0);
}

static void test_config_validation_bounds(PelQloopConfig *c)
{
    c->knob_min = 45.0;
    CHECK(pel_qloop_validate(c) == PEL_ERR_INVALID);
    c->knob_min = 50.0;
    CHECK(pel_qloop_validate(c) == PEL_ERR_INVALID);
    c->knob_min = 10.0;

    c->knob_start = 9.0;
    CHECK(pel_qloop_validate(c) == PEL_ERR_INVALID);
    c->knob_start = 46.0;
    CHECK(pel_qloop_validate(c) == PEL_ERR_INVALID);
    c->knob_start = 28.0;

    c->resolution = 0.0;
    CHECK(pel_qloop_validate(c) == PEL_ERR_INVALID);
    c->resolution = 36.0;
    CHECK(pel_qloop_validate(c) == PEL_ERR_INVALID);
    c->resolution = 0.5;
}

static void test_config_validation(void)
{
    PelQloopConfig c;

    memset(&c, 0, sizeof(c));
    c.target = 90.0;
    c.tolerance = 1.0;
    c.knob_min = 10.0;
    c.knob_max = 45.0;
    c.knob_start = 28.0;
    c.resolution = 0.5;
    c.max_iter = 8;
    c.direction = -1;

    CHECK(pel_qloop_validate(NULL) == PEL_ERR_INVALID);
    CHECK(pel_qloop_validate(&c) == PEL_OK);

    c.target = (double)NAN;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.target = 90.0;

    c.tolerance = 0.0;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.tolerance = -1.0;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.tolerance = 1.0;

    test_config_validation_bounds(&c);

    c.max_iter = 0;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.max_iter = 17;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.max_iter = 8;

    c.direction = 0;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.direction = 2;
    CHECK(pel_qloop_validate(&c) == PEL_ERR_INVALID);
    c.direction = -1;
    CHECK(pel_qloop_validate(&c) == PEL_OK);
}

static void test_history_validation(void)
{
    PelQloopConfig cfg = {
        .target = 90.0,
        .tolerance = 1.0,
        .knob_min = 10.0,
        .knob_max = 45.0,
        .knob_start = 28.0,
        .resolution = 0.5,
        .max_iter = 8,
        .direction = -1,
    };
    PelQloopObs hist[18];
    PelQloopStep step;

    memset(hist, 0, sizeof(hist));
    memset(&step, 0, sizeof(step));
    CHECK(pel_qloop_next(NULL, hist, 0, &step) == PEL_ERR_INVALID);
    CHECK(pel_qloop_next(&cfg, hist, 0, NULL) == PEL_ERR_INVALID);
    CHECK(pel_qloop_next(&cfg, NULL, 1, &step) == PEL_ERR_INVALID);

    /* n_hist == 17 exceeds PEL_QLOOP_MAX_ITER -> PEL_ERR_RANGE */
    CHECK(pel_qloop_next(&cfg, hist, 17, &step) == PEL_ERR_RANGE);

    /* Duplicate knob in history */
    hist[0].knob = 25.0;
    hist[0].score = 80.0;
    hist[1].knob = 25.0;
    hist[1].score = 82.0;
    CHECK(pel_qloop_next(&cfg, hist, 2, &step) == PEL_ERR_INVALID);

    /* NaN in score */
    hist[0].knob = 25.0;
    hist[0].score = (double)NAN;
    CHECK(pel_qloop_next(&cfg, hist, 1, &step) == PEL_ERR_INVALID);

    /* Knob outside [min, max] */
    hist[0].knob = 5.0;
    hist[0].score = 80.0;
    CHECK(pel_qloop_next(&cfg, hist, 1, &step) == PEL_ERR_INVALID);
}

int main(void)
{
    test_config_validation();
    test_history_validation();
    test_boundary_iteration_0();
    test_boundary_worst_end_passing();
    test_synthetic_models();
    test_planted_unreachable();
    test_planted_oscillating();
    test_planted_exhausted();

    if (g_fail != 0) {
        (void)fprintf(stderr, "quality_loop_test: %d failure(s)\n", g_fail);
        return EXIT_FAILURE;
    }
    (void)printf("PASS: quality_loop_test\n");
    return EXIT_SUCCESS;
}
