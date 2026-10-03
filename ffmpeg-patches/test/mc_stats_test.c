/* Copyright 2026 Lusoris. BSD-2-Clause-Patent. */

/*
 * Unit tests for ffmpeg-patches/files/pelorus_mc_stats.h, the host-side motion
 * vector arithmetic of vf_pelorus_mc_vulkan.
 *
 * BUG-009: the shader writes Q2 quarter-pel vectors but searches on the integer
 * pel grid. The host must round the Q2 field to integer pel before it seeds the
 * next frame's search. The predictor checks below fail if the Q2 values pass
 * through unconverted (the old behaviour).
 *
 * BUG-014: motion_magnitude_p95 used an O(n^2) counting scan per frame. The
 * replacement is an O(n) radix select. It must return the same value, bit for
 * bit, as the old scan; ref_p95_quadratic() below is that scan with the
 * libavutil clip macros spelled out.
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus_mc_stats.h"

/* All mutable test state lives here and is passed explicitly (no globals). */
typedef struct TestCtx {
    uint64_t rng;
    int failures;
} TestCtx;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);         \
            t->failures++;                                                                         \
        }                                                                                          \
    } while (0)

enum { GEN_SEARCH_MV, GEN_TINY_MV, GEN_ZERO, GEN_EQUAL, GEN_BITS, GEN_ASC, GEN_DESC, GEN_COUNT };

/* xorshift64*: deterministic, no libc rand(). */
static uint32_t rng_u32(TestCtx *t)
{
    t->rng ^= t->rng >> 12u;
    t->rng ^= t->rng << 25u;
    t->rng ^= t->rng >> 27u;
    return (uint32_t)((t->rng * 0x2545F4914F6CDD1Dull) >> 32u);
}

static int32_t rng_range(TestCtx *t, int32_t lo, int32_t hi)
{
    return lo + (int32_t)(rng_u32(t) % (uint32_t)(hi - lo + 1));
}

/* The pre-fix p95 selection from vf_pelorus_mc_vulkan.c attach_motion(). */
static float ref_p95_quadratic(const float *mags, int nblocks)
{
    int rank = (int)ceil(0.95 * (double)(nblocks > 1 ? nblocks : 1)) - 1;
    int hi = nblocks - 1 > 0 ? nblocks - 1 : 0;
    float p95 = 0.0f;
    int k;

    rank = rank < 0 ? 0 : (rank > hi ? hi : rank);
    for (k = 0; k < nblocks; k++) {
        int below = 0;
        int j;
        for (j = 0; j < nblocks; j++) {
            if (mags[j] < mags[k] || (mags[j] == mags[k] && j < k)) {
                below++;
            }
        }
        if (below == rank) {
            p95 = mags[k];
            break;
        }
    }
    return p95;
}

static uint32_t float_bits(float f)
{
    uint32_t bits;

    memcpy(&bits, &f, sizeof(bits));
    return bits;
}

static int same_bits(float a, float b)
{
    return float_bits(a) == float_bits(b);
}

static void check_p95(TestCtx *t, const char *what, const float *v, int n)
{
    float want = ref_p95_quadratic(v, n);
    float got = pel_mc_p95_nonneg(v, n);

    if (!same_bits(want, got)) {
        (void)fprintf(stderr, "p95 mismatch (%s, n=%d): quadratic %.9g, radix %.9g\n", what, n,
                      (double)want, (double)got);
        t->failures++;
    }
}

static int cmp_float(const void *a, const void *b)
{
    float x;
    float y;

    memcpy(&x, a, sizeof(x));
    memcpy(&y, b, sizeof(y));
    return (x > y) - (x < y);
}

/* A random non-negative, non-NaN float: any finite bit pattern with the sign
 * cleared (subnormals included), or +inf about once in 64 draws. */
static float random_nonneg_float(TestCtx *t)
{
    uint32_t bits = rng_u32(t) & 0x7fffffffu;
    float f;

    if ((rng_u32(t) & 63u) == 0u) {
        bits = 0x7f800000u;
    } else if ((bits & 0x7f800000u) == 0x7f800000u) {
        bits &= 0x7f7fffffu;
    }
    memcpy(&f, &bits, sizeof(f));
    return f;
}

static float draw(TestCtx *t, int gen)
{
    switch (gen) {
    case GEN_SEARCH_MV: /* the filter's own path: a search=24 Q2 field */
    case GEN_ASC:
    case GEN_DESC:
        return pel_mc_mv_mag_px(rng_range(t, -98, 98), rng_range(t, -98, 98));
    case GEN_TINY_MV: /* heavy ties */
        return pel_mc_mv_mag_px(rng_range(t, -2, 2), rng_range(t, -2, 2));
    case GEN_ZERO:
        return pel_mc_mv_mag_px(0, 0);
    case GEN_EQUAL:
        return pel_mc_mv_mag_px(8, 0);
    default:
        return random_nonneg_float(t);
    }
}

static void fill(TestCtx *t, float *v, int n, int gen)
{
    int i;

    for (i = 0; i < n; i++) {
        v[i] = draw(t, gen);
    }
    if (gen == GEN_ASC || gen == GEN_DESC) {
        qsort(v, (size_t)n, sizeof(*v), cmp_float);
    }
    if (gen == GEN_DESC) {
        for (i = 0; i < n / 2; i++) {
            float tmp = v[i];
            v[i] = v[n - 1 - i];
            v[n - 1 - i] = tmp;
        }
    }
}

static void test_p95_matches_quadratic(TestCtx *t, float *v)
{
    static const int sizes[] = {0,  1,  2,   3,   4,   19,  20,   21,   39,
                                40, 41, 100, 255, 256, 257, 1000, 4097, 8160};
    size_t s;
    int gen;
    int trial;
    int n;

    for (s = 0; s < sizeof(sizes) / sizeof(sizes[0]); s++) {
        for (gen = 0; gen < GEN_COUNT; gen++) {
            fill(t, v, sizes[s], gen);
            check_p95(t, "fixed sizes", v, sizes[s]);
        }
    }
    /* Many small random draws: every size 1..64, all generators. */
    for (trial = 0; trial < 40; trial++) {
        for (n = 1; n <= 64; n++) {
            for (gen = 0; gen < GEN_COUNT; gen++) {
                fill(t, v, n, gen);
                check_p95(t, "small sizes", v, n);
            }
        }
    }
}

/* pel_mc_select_nonneg() against a full sort of v[0..n), for every rank. */
static void check_every_rank(TestCtx *t, const float *v, float *sorted, size_t n)
{
    size_t k;

    memcpy(sorted, v, n * sizeof(*v));
    qsort(sorted, n, sizeof(*sorted), cmp_float);
    for (k = 0; k < n; k++) {
        CHECK(same_bits(pel_mc_select_nonneg(v, n, k), sorted[k]));
    }
}

static void test_select_every_rank(TestCtx *t)
{
    float v[97];
    float sorted[97];
    int trial;
    int gen;
    size_t n;

    for (trial = 0; trial < 20; trial++) {
        for (gen = 0; gen < GEN_COUNT; gen++) {
            for (n = 1; n <= sizeof(v) / sizeof(v[0]); n += 8) {
                fill(t, v, (int)n, gen);
                check_every_rank(t, v, sorted, n);
            }
        }
    }
    CHECK(same_bits(pel_mc_select_nonneg(NULL, 4, 0), 0.0f));
    CHECK(same_bits(pel_mc_select_nonneg(v, 0, 0), 0.0f));
    CHECK(same_bits(pel_mc_select_nonneg(v, 3, 3), 0.0f));
    CHECK(same_bits(pel_mc_p95_nonneg(v, -1), 0.0f));
}

static void test_q2_to_pel(TestCtx *t)
{
    int32_t q;

    /* Every Q2 value a search radius up to 256 px (+ sub-pel) can produce,
     * against round-half-away-from-zero and the 0008 NVENC ME-hint rounding. */
    for (q = -1100; q <= 1100; q++) {
        double exact = (double)q / 4.0;
        int32_t want = (int32_t)(exact >= 0.0 ? floor(exact + 0.5) : -floor(-exact + 0.5));
        int32_t nvenc = (q >= 0 ? q + 2 : q - 2) / 4;
        CHECK(pel_mc_q2_to_pel(q) == want);
        CHECK(pel_mc_q2_to_pel(q) == nvenc);
    }
}

static void test_q2_to_pel_edges(TestCtx *t)
{
    CHECK(pel_mc_q2_to_pel(8) == 2);
    CHECK(pel_mc_q2_to_pel(-12) == -3);
    CHECK(pel_mc_q2_to_pel(2) == 1);   /* +0.5 rounds away from zero */
    CHECK(pel_mc_q2_to_pel(-2) == -1); /* -0.5 rounds away from zero */
    CHECK(pel_mc_q2_to_pel(1) == 0);
    CHECK(pel_mc_q2_to_pel(-1) == 0);
    CHECK(pel_mc_q2_to_pel(INT32_MAX) == 536870912);
    CHECK(pel_mc_q2_to_pel(INT32_MIN) == -536870912);
}

/* A rigid 2 px right / 3 px up pan, as the shader reports it in Q2. The
 * predictors must come back as (2, -3) integer pel, not (8, -12). */
static void test_predictors_rigid_pan(TestCtx *t)
{
    enum { N = 64 };
    int32_t mvx[N];
    int32_t mvy[N];
    int32_t prev[2 * N];
    int32_t gx = 99;
    int32_t gy = 99;
    int i;

    for (i = 0; i < N; i++) {
        mvx[i] = 8;
        mvy[i] = -12;
    }
    pel_mc_build_predictors(mvx, mvy, N, prev, &gx, &gy);
    CHECK(gx == 2);
    CHECK(gy == -3);
    for (i = 0; i < N; i++) {
        CHECK(prev[2 * i + 0] == 2);
        CHECK(prev[2 * i + 1] == -3);
    }
}

/* Sub-pel scatter: each block rounds on its own; the global predictor rounds
 * the exact mean (31 / 16 = 1.9375 px -> 2). */
static void test_predictors_rounding(TestCtx *t)
{
    const int32_t mvx[4] = {8, 8, 9, 6};
    const int32_t mvy[4] = {-2, -1, 1, 2};
    int32_t prev[8];
    int32_t gx = 99;
    int32_t gy = 99;

    pel_mc_build_predictors(mvx, mvy, 4, prev, &gx, &gy);
    CHECK(prev[0] == 2 && prev[2] == 2 && prev[4] == 2 && prev[6] == 2);
    CHECK(prev[1] == -1 && prev[3] == 0 && prev[5] == 0 && prev[7] == 1);
    CHECK(gx == 2);
    CHECK(gy == 0);

    /* Exact half-pel means round away from zero; an empty field is zero. */
    CHECK(pel_mc_mean_q2_to_pel(2, 1) == 1);
    CHECK(pel_mc_mean_q2_to_pel(-2, 1) == -1);
    CHECK(pel_mc_mean_q2_to_pel(123, 0) == 0);
}

int main(void)
{
    TestCtx ctx = {0x9E3779B97F4A7C15ull, 0};
    float *v = malloc((size_t)8160 * sizeof(*v));

    if (!v) {
        (void)fprintf(stderr, "mc_stats_test: out of memory\n");
        return 1;
    }
    test_q2_to_pel(&ctx);
    test_q2_to_pel_edges(&ctx);
    test_predictors_rigid_pan(&ctx);
    test_predictors_rounding(&ctx);
    test_select_every_rank(&ctx);
    test_p95_matches_quadratic(&ctx, v);
    free(v);
    if (ctx.failures) {
        (void)fprintf(stderr, "mc_stats_test: %d failure(s)\n", ctx.failures);
        return 1;
    }
    (void)printf("mc_stats_test: OK\n");
    return 0;
}
