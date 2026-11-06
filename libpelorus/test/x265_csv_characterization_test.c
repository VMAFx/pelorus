/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef _WIN32
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700
#endif
#endif

#include "pelorus/interop.h"
#include "pelorus/telemetry.h"
#include "test_path_safety.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <process.h>
#include <share.h>
#include <sys/stat.h>
static long get_proc_id(void)
{
    return (long)_getpid();
}
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
static long get_proc_id(void)
{
    return (long)getpid();
}
#endif

#define FIXTURE_CAP 8192u
#define FRAME_CAP 32u
#define FIXTURE_FRAME_COUNT 10u

#define CHECK(expr)                                                                                \
    do {                                                                                           \
        if (!(expr)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr);                  \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

#define CHECK_LABEL(expr, label)                                                                   \
    do {                                                                                           \
        if (!(expr)) {                                                                             \
            (void)fprintf(stderr, "FAIL [%s] %s:%d: %s\n", (label), __FILE__, __LINE__, #expr);    \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

static int read_fixture_file(const char *path, char *buf, size_t cap, size_t *out_len)
{
    FILE *fp = test_fopen_safe(path, "rb");
    size_t got;

    if (fp == NULL) {
        return -1;
    }
    got = fread(buf, 1u, cap, fp);
    if (ferror(fp) || got == cap) {
        (void)fclose(fp);
        return -1;
    }
    if (fclose(fp) != 0) {
        return -1;
    }
    *out_len = got;
    return 0;
}

static FILE *create_exclusive(const char *path)
{
    (void)remove(path);
#ifdef _WIN32
    const int flags = _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY;
#ifdef _MSC_VER
    int fd = -1;

    if (_sopen_s(&fd, path, flags, _SH_DENYNO, _S_IREAD | _S_IWRITE) != 0) {
        fd = -1;
    }
#else
    const int fd = _open(path, flags, _S_IREAD | _S_IWRITE);
#endif
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    fp = _fdopen(fd, "wb");
    if (fp == NULL) {
        (void)_close(fd);
    }
    return fp;
#else
    /* O_EXCL fails on any existing name, links included (POSIX open());
     * O_NOFOLLOW states the same intent for the final component. */
    const int fd =
        open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR);
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    fp = fdopen(fd, "wb");
    if (fp == NULL) {
        (void)close(fd);
    }
    return fp;
#endif
}

static int write_temp_file(const char *path, const char *data, size_t len)
{
    FILE *fp = create_exclusive(path);

    if (fp == NULL) {
        return -1;
    }
    if (len > 0u && fwrite(data, 1u, len, fp) != len) {
        (void)fclose(fp);
        return -1;
    }
    if (fclose(fp) != 0) {
        return -1;
    }
    return 0;
}

static int check_frame_match(const PelorusX265Frame *p_frame,
                             const PelorusEncTelemetryInput *t_frame)
{
    int failures = 0;
    uint8_t expected_type =
        p_frame->slice_type == 'I' ?
            (uint8_t)PEL_PICTURE_I :
            (p_frame->slice_type == 'P' ? (uint8_t)PEL_PICTURE_P : (uint8_t)PEL_PICTURE_B);

    CHECK(p_frame->poc == (int32_t)t_frame->frame.display_index);
    CHECK(t_frame->frame.picture_type == expected_type);
    CHECK(fabsf(p_frame->qp - t_frame->frame.avg_qp) < 1e-4f);
    CHECK(p_frame->bits == (uint64_t)t_frame->frame.frame_bytes * 8u);
    return failures;
}

struct csv_test_ctx {
    PelorusX265Frame p_frames[FRAME_CAP];
    PelorusEncTelemetryInput t_frames[FRAME_CAP];
    size_t p_count;
    size_t t_count;
    pel_result p_res;
    pel_result t_res;
};

static int run_csv_parse_pair(const char *tmp_path, const char *label, const char *text,
                              size_t text_len, size_t init_p_count, size_t init_t_count,
                              struct csv_test_ctx *ctx)
{
    (void)memset(ctx->p_frames, 0, sizeof(ctx->p_frames));
    (void)memset(ctx->t_frames, 0, sizeof(ctx->t_frames));
    ctx->p_count = init_p_count;
    ctx->t_count = init_t_count;
    if (write_temp_file(tmp_path, text, text_len) != 0) {
        (void)fprintf(stderr, "%s: write_temp_file failed\n", label);
        return 1;
    }
    /* The library parses a temp copy, so no command-line path reaches its fopen. */
    ctx->p_res = pel_x265_csv_parse(tmp_path, ctx->p_frames, FRAME_CAP, &ctx->p_count);
    ctx->t_res =
        pel_tlm_parse_x265_csv(text, text_len, 8u, ctx->t_frames, FRAME_CAP, &ctx->t_count);
    (void)remove(tmp_path);
    return 0;
}

static int check_matching_csv(const char *tmp_path, const char *label, const char *text,
                              size_t text_len, size_t expected_count)
{
    struct csv_test_ctx ctx;
    size_t i;
    int failures = 0;

    if (run_csv_parse_pair(tmp_path, label, text, text_len, 0u, 0u, &ctx) != 0) {
        return failures + 1;
    }
    CHECK_LABEL(ctx.p_res == PEL_OK, label);
    CHECK_LABEL(ctx.t_res == PEL_OK, label);
    CHECK_LABEL(ctx.p_count == expected_count, label);
    CHECK_LABEL(ctx.t_count == expected_count, label);
    for (i = 0; i < expected_count && i < ctx.p_count && i < ctx.t_count; i++) {
        failures += check_frame_match(&ctx.p_frames[i], &ctx.t_frames[i]);
    }
    return failures;
}

static int check_zero_frames(const char *tmp_path, const char *label, const char *text,
                             size_t text_len, pel_result want)
{
    struct csv_test_ctx ctx;
    int failures = 0;

    if (run_csv_parse_pair(tmp_path, label, text, text_len, 99u, 99u, &ctx) != 0) {
        return failures + 1;
    }
    CHECK_LABEL(ctx.p_res == want, label);
    CHECK_LABEL(ctx.t_res == want, label);
    CHECK_LABEL(ctx.p_count == 0u, label);
    CHECK_LABEL(ctx.t_count == 0u, label);
    return failures;
}

static int check_divergent_single_frame(const char *tmp_path, const char *label, const char *text,
                                        size_t text_len, pel_result want_tlm, float want_qp)
{
    struct csv_test_ctx ctx;
    int failures = 0;

    if (run_csv_parse_pair(tmp_path, label, text, text_len, 0u, 99u, &ctx) != 0) {
        return failures + 1;
    }
    CHECK_LABEL(ctx.p_res == PEL_OK, label);
    CHECK_LABEL(ctx.t_res == want_tlm, label);
    CHECK_LABEL(ctx.p_count == 1u, label);
    CHECK_LABEL(ctx.t_count == 0u, label);
    CHECK_LABEL(ctx.p_frames[0].poc == 0, label);
    CHECK_LABEL(ctx.p_frames[0].slice_type == 'I', label);
    CHECK_LABEL(fabsf(ctx.p_frames[0].qp - want_qp) < 1e-4f, label);
    CHECK_LABEL(ctx.p_frames[0].bits == 15040u, label);
    return failures;
}

static int test_case_a_fixture(const char *fixture_path, const char *tmp_path)
{
    char buf[FIXTURE_CAP];
    size_t len = 0;
    int failures = 0;

    CHECK(read_fixture_file(fixture_path, buf, sizeof(buf), &len) == 0);
    failures += check_matching_csv(tmp_path, "case_a_fixture", buf, len, FIXTURE_FRAME_COUNT);
    return failures;
}

static int test_case_b_header_only(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n";
    return check_zero_frames(tmp_path, "case_b_header_only", text, sizeof(text) - 1u, PEL_OK);
}

static int test_case_c_empty_file(const char *tmp_path)
{
    return check_zero_frames(tmp_path, "case_c_empty_file", "", 0u, PEL_ERR_ABSENT);
}

static int test_case_d_crlf(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\r\n"
                               "0, 0, I-SLICE, 0, 32.00, 15040, 0\r\n"
                               "0, 1, P-SLICE, 3, 30.00, 7016, 0\r\n";
    return check_matching_csv(tmp_path, "case_d_crlf", text, sizeof(text) - 1u, 2u);
}

static int test_case_e_trailing_blanks(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"
                               "0, 0, I-SLICE, 0, 32.00, 15040, 0\n\n\n";
    return check_matching_csv(tmp_path, "case_e_trailing_blanks", text, sizeof(text) - 1u, 1u);
}

static int test_case_f_summary_row(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"
                               "0, 0, I-SLICE, 0, 32.00, 15040, 0\n"
                               "Command, Date/Time, Elapsed Time, FPS, Bitrate\n";
    /* Difference: pel_x265_csv_parse ignores non-frame rows and returns PEL_OK;
     * pel_tlm_parse_x265_csv rejects them with PEL_ERR_TRUNCATED. */
    return check_divergent_single_frame(tmp_path, "case_f_summary_row", text, sizeof(text) - 1u,
                                        PEL_ERR_TRUNCATED, 32.00f);
}

static int test_case_g_missing_column(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"
                               "0, 0, I-SLICE, 0, , 15040, 0\n";
    /* Difference: pel_x265_csv_parse accepts empty fields defaulting to zero (PEL_OK);
     * pel_tlm_parse_x265_csv rejects empty values with PEL_ERR_INVALID. */
    return check_divergent_single_frame(tmp_path, "case_g_missing_column", text, sizeof(text) - 1u,
                                        PEL_ERR_INVALID, 0.0f);
}

static int test_case_h_duplicate_header(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut, QP\n"
                               "0, 0, I-SLICE, 0, 32.00, 15040, 0, 32.00\n";
    /* Difference: pel_x265_csv_parse allows duplicate header columns;
     * pel_tlm_parse_x265_csv rejects duplicate headers with PEL_ERR_INVALID. */
    return check_divergent_single_frame(tmp_path, "case_h_duplicate_header", text,
                                        sizeof(text) - 1u, PEL_ERR_INVALID, 32.00f);
}

static int test_case_i_cap_overflow(const char *tmp_path)
{
    static const char text[] = "Layer, Encode Order, Type, POC, QP, Bits, Scenecut\n"
                               "0, 0, I-SLICE, 0, 32.00, 15040, 0\n"
                               "0, 1, P-SLICE, 1, 32.00, 7016, 0\n";
    PelorusX265Frame p_frames[FRAME_CAP];
    PelorusEncTelemetryInput t_frames[FRAME_CAP];
    size_t p_count = 0;
    size_t t_count = 0;
    int failures = 0;

    (void)memset(p_frames, 0, sizeof(p_frames));
    (void)memset(t_frames, 0, sizeof(t_frames));
    CHECK(write_temp_file(tmp_path, text, sizeof(text) - 1u) == 0);
    /* Difference: on cap overflow, pel_x265_csv_parse fills cap frames and sets count=cap;
     * pel_tlm_parse_x265_csv writes zero frames and sets count=total_rows. */
    CHECK(pel_x265_csv_parse(tmp_path, p_frames, 1u, &p_count) == PEL_ERR_RANGE);
    CHECK(pel_tlm_parse_x265_csv(text, sizeof(text) - 1u, 8u, t_frames, 1u, &t_count) ==
          PEL_ERR_RANGE);
    CHECK(p_count == 1u);
    CHECK(t_count == 2u);
    CHECK(p_frames[0].poc == 0);
    CHECK(p_frames[0].slice_type == 'I');
    CHECK(fabsf(p_frames[0].qp - 32.00f) < 1e-4f);
    CHECK(p_frames[0].bits == 15040u);
    (void)remove(tmp_path);
    return failures;
}

int main(int argc, char **argv)
{
    char tmp_path[128];
    int failures = 0;
    int n;

    if (argc < 2 || argv[1] == NULL) {
        (void)fprintf(stderr, "usage: %s <x265-fixture-csv>\n", argv[0]);
        return EXIT_FAILURE;
    }

    n = snprintf(tmp_path, sizeof(tmp_path), "pelorus_x265_char_%ld.csv", get_proc_id());
    if (n < 0 || (size_t)n >= sizeof(tmp_path)) {
        (void)fprintf(stderr, "temp path formatting failed\n");
        return EXIT_FAILURE;
    }

    failures += test_case_a_fixture(argv[1], tmp_path);
    failures += test_case_b_header_only(tmp_path);
    failures += test_case_c_empty_file(tmp_path);
    failures += test_case_d_crlf(tmp_path);
    failures += test_case_e_trailing_blanks(tmp_path);
    failures += test_case_f_summary_row(tmp_path);
    failures += test_case_g_missing_column(tmp_path);
    failures += test_case_h_duplicate_header(tmp_path);
    failures += test_case_i_cap_overflow(tmp_path);

    if (failures != 0) {
        (void)fprintf(stderr, "x265-csv-characterization: %d check(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
