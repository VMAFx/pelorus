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

#include "pelorus/grain_table.h"
#include "test_path_safety.h"

#include <inttypes.h>
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

static const char s_fixture[] =
    "filmgrn1\n"
    "E 0 10000000 1 7391 1\n"
    "\tp 3 6 0 8 0 1 128 128 256 128 128 256\n"
    "\tsY 14  10 1 20 2 30 3 40 4 50 5 60 6 70 7 80 8 90 9 100 10 120 11 140 12 160 13 200 14\n"
    "\tsCb 10 15 1 30 2 45 3 60 4 75 5 90 6 105 7 120 8 135 9 150 10\n"
    "\tsCr 10 12 1 25 2 40 3 55 4 70 5 85 6 100 7 115 8 130 9 145 10\n"
    "\tcY 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24\n"
    "\tcCb 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25\n"
    "\tcCr 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25\n"
    "E 10000000 20000000 1 7392 0\n";

static void check_fixture_entry0_scalars(const PelorusGrainTableEntry *e)
{
    CHECK(e->start_time == 0);
    CHECK(e->end_time == 10000000);
    CHECK(e->params.apply == 1);
    CHECK(e->params.seed == 7391);
    CHECK(e->update_parameters == 1);
    CHECK(e->params.grain_model == PEL_GRAIN_AOM);
    CHECK(e->params.ar_coeff_lag == 3);
    CHECK(e->params.num_y_points == 14);
}

static void check_fixture_entry0_uv(const PelorusGrainTableEntry *e)
{
    CHECK(e->params.num_uv_points[0] == 10);
    CHECK(e->params.num_uv_points[1] == 10);
    CHECK(e->params.uv_mult[0] == 0);
    CHECK(e->params.uv_mult_luma[0] == 0);
    CHECK(e->params.uv_offset[0] == 0);
    CHECK(e->params.uv_mult[1] == 0);
    CHECK(e->params.uv_mult_luma[1] == 0);
    CHECK(e->params.uv_offset[1] == 0);
}

static void test_positive_parse(void)
{
    PelorusGrainTableEntry entries[4];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(s_fixture, strlen(s_fixture), entries, 4, &count);
    CHECK(r == PEL_OK);
    CHECK(count == 2);

    check_fixture_entry0_scalars(&entries[0]);
    check_fixture_entry0_uv(&entries[0]);

    CHECK(entries[1].start_time == 10000000);
    CHECK(entries[1].end_time == 20000000);
    CHECK(entries[1].params.apply == 1);
    CHECK(entries[1].params.seed == 7392);
    CHECK(entries[1].update_parameters == 0);
}

static void test_positive_write(void)
{
    PelorusGrainTableEntry entries[4];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(s_fixture, strlen(s_fixture), entries, 4, &count);
    CHECK(r == PEL_OK);

    char buf[4096];
    size_t written = 0;
    r = pel_grain_table_write(entries, count, buf, sizeof(buf), &written);
    CHECK(r == PEL_OK);
    CHECK(written == strlen(s_fixture));
    CHECK(memcmp(buf, s_fixture, written) == 0);

    PelorusGrainTableEntry entries2[4];
    size_t count2 = 0;
    r = pel_grain_table_parse(buf, written, entries2, 4, &count2);
    CHECK(r == PEL_OK);
    CHECK(count2 == count);

    char buf2[4096];
    size_t written2 = 0;
    r = pel_grain_table_write(entries2, count2, buf2, sizeof(buf2), &written2);
    CHECK(r == PEL_OK);
    CHECK(written2 == written);
    CHECK(memcmp(buf, buf2, written) == 0);
}

static void test_file_fixture(const char *path)
{
    if (path == NULL) {
        return;
    }
    FILE *f = test_fopen_safe(path, "rb");
    CHECK(f != NULL);
    if (!f) {
        return;
    }
    char text[4096];
    size_t n = fread(text, 1, sizeof(text) - 1, f);
    int read_err = ferror(f);
    int extra = fgetc(f);
    (void)fclose(f);
    CHECK(!read_err);
    CHECK(extra == EOF);
    CHECK(n > 0);
    if (read_err || extra != EOF || n == 0) {
        return;
    }
    text[n] = '\0';

    PelorusGrainTableEntry entries[4];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(text, n, entries, 4, &count);
    CHECK(r == PEL_OK);
    CHECK(count == 2);

    char out[4096];
    size_t written = 0;
    r = pel_grain_table_write(entries, count, out, sizeof(out), &written);
    CHECK(r == PEL_OK);
    CHECK(written == n);
    CHECK(memcmp(out, text, n) == 0);
}

static void check_parse_range(const char *text)
{
    PelorusGrainTableEntry entries[2];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(text, strlen(text), entries, 2, &count);
    CHECK(r == PEL_ERR_RANGE);
}

static void test_negative_range_header(void)
{
    check_parse_range("filmgrn1\nE 100 50 1 10 0\n");
    check_parse_range("filmgrn1\nE 0 100 2 10 0\n");
    check_parse_range("filmgrn1\nE 0 100 -1 10 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 65536 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 -1 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 2\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 -1\n");
    check_parse_range("filmgrn1\nE 0 99999999999999999999999999999999999999999 1 10 0\n");
}

static void test_negative_range_p_scalars1(void)
{
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp -1 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 4 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 5 0 8 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 10 0 8 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 4 8 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 -1 8 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
}

static void test_negative_range_p_scalars2(void)
{
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 7 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 12 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 2 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 0 2 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 0 1 256 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 0 1 128 128 512 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 0 1 128 128 256 128 128 512\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 0 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
}

static void test_negative_range_points(void)
{
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
        "\tsY 15  0 0 1 1 2 2 3 3 4 4 5 5 6 6 7 7 8 8 9 9 10 10 11 11 12 12 13 13 14 14\n"
        "\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 1  256 0\n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 1  10 256\n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 2  20 0 10 0\n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 2  20 0 20 0\n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 11 0 0 1 1 2 2 3 3 4 4 5 5 6 6 7 7 8 8 9 9 10 10\n"
                      "\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 2 30 1 30 2\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 0\n\tsCr 11 0 0 1 1 2 2 3 3 4 4 5 5 6 6 7 7 8 8 9 9 10 10\n"
                      "\tcY\n\tcCb 0\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 0\n\tsCr 2 40 1 35 2\n\tcY\n\tcCb 0\n\tcCr 0\n");
}

static void test_negative_range_coeffs(void)
{
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY 128 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range(
        "filmgrn1\nE 0 100 1 10 1\n\tp 1 6 0 8 0 1 128 128 256 128 128 256\n"
        "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY -129 0 0 0\n\tcCb 0 0 0 0 0\n\tcCr 0 0 0 0 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 128\n\tcCr 0\n");
    check_parse_range("filmgrn1\nE 0 100 1 10 1\n\tp 0 6 0 8 0 1 128 128 256 128 128 256\n"
                      "\tsY 0 \n\tsCb 0\n\tsCr 0\n\tcY\n\tcCb 0\n\tcCr -129\n");
}

static void test_negative_range_write_scalars(void)
{
    PelorusGrainTableEntry e;
    char buf[512];
    size_t written = 0;

    memset(&e, 0, sizeof(e));
    e.start_time = 100;
    e.end_time = 50;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);

    e.start_time = 0;
    e.end_time = 100;
    e.params.apply = 2;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.apply = 1;

    e.update_parameters = 2;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.update_parameters = 1;

    e.params.seed = 65536;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.seed = 10;

    e.params.ar_coeff_lag = 4;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.ar_coeff_lag = 0;

    e.params.ar_coeff_shift = 5;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.ar_coeff_shift = 6;

    e.params.grain_scale_shift = 4;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.grain_scale_shift = 0;

    e.params.scaling_shift = 12;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.scaling_shift = 8;
}

static void test_negative_range_write_uv_and_points(void)
{
    PelorusGrainTableEntry e;
    char buf[512];
    size_t written = 0;

    memset(&e, 0, sizeof(e));
    e.end_time = 100;
    e.update_parameters = 1;
    e.params.scaling_shift = 8;
    e.params.ar_coeff_shift = 6;

    e.params.uv_mult[0] = 128;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_mult[0] = -129;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_mult[0] = 0;

    e.params.uv_offset[0] = 256;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_offset[0] = -257;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_offset[0] = 0;

    e.params.num_y_points = 15;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.num_y_points = 2;
    e.params.y_points[0][0] = 20;
    e.params.y_points[1][0] = 20;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.num_y_points = 0;

    e.params.num_uv_points[0] = 11;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.num_uv_points[0] = 0;

    e.params.num_uv_points[1] = 11;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
}

static void test_negative_syntax_null(void)
{
    PelorusGrainTableEntry e[2];
    size_t count = 0;
    char buf[128];
    size_t written = 0;

    CHECK(pel_grain_table_parse(NULL, 10, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1\n", 9, e, 2, NULL) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1\n", 9, NULL, 2, &count) == PEL_ERR_INVALID);

    CHECK(pel_grain_table_write(NULL, 1, buf, sizeof(buf), &written) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_write(e, 1, buf, sizeof(buf), NULL) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_write(e, 1, NULL, 10, &written) == PEL_ERR_INVALID);
}

static void test_negative_syntax_magic_and_short(void)
{
    PelorusGrainTableEntry e[2];
    size_t count = 0;

    CHECK(pel_grain_table_parse("filmgrn2\n", 9, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1", 8, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmg\n", 6, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1X", 9, e, 2, &count) == PEL_ERR_INVALID);

    CHECK(pel_grain_table_parse("filmgrn1\nE 0 100 1 10", 21, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1\nE 0 100 1 10 1\n\tp 1 6", 31, e, 2, &count) ==
          PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1\ngarbage\n", 17, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1\nE 0 abc 1 10 0\n", 24, e, 2, &count) == PEL_ERR_INVALID);
    CHECK(pel_grain_table_parse("filmgrn1\nE 0 100 1 10 1\n\tbadtag 0\n", 36, e, 2, &count) ==
          PEL_ERR_INVALID);
}

static int check_guard_bytes(const char *buf, size_t from, size_t to, unsigned char expected)
{
    for (size_t k = from; k < to; k++) {
        if ((unsigned char)buf[k] != expected) {
            return 0;
        }
    }
    return 1;
}

static void test_out_capacity(void)
{
    PelorusGrainTableEntry small_out[1];
    size_t small_count = 0;
    pel_result r = pel_grain_table_parse(s_fixture, strlen(s_fixture), small_out, 1, &small_count);
    CHECK(r == PEL_ERR_TRUNCATED);
    CHECK(small_count == 1);

    r = pel_grain_table_parse(s_fixture, strlen(s_fixture), small_out, 0, &small_count);
    CHECK(r == PEL_ERR_TRUNCATED);
    CHECK(small_count == 0);
}

static void test_buf_capacity_needed(void)
{
    PelorusGrainTableEntry entries[4];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(s_fixture, strlen(s_fixture), entries, 4, &count);
    CHECK(r == PEL_OK);

    size_t needed = 0;
    r = pel_grain_table_write(entries, count, NULL, 0, &needed);
    CHECK(r == PEL_ERR_TRUNCATED);
    CHECK(needed == strlen(s_fixture));

    char guard_buf[1024];
    CHECK(needed + 16 < sizeof(guard_buf));
    (void)memset(guard_buf, 0x5A, sizeof(guard_buf));
    size_t written = 0;
    r = pel_grain_table_write(entries, count, guard_buf, needed, &written);
    CHECK(r == PEL_OK);
    CHECK(written == needed);
    CHECK(check_guard_bytes(guard_buf, needed, sizeof(guard_buf), 0x5A));
}

static void test_buf_capacity_sweep(void)
{
    PelorusGrainTableEntry entries[4];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(s_fixture, strlen(s_fixture), entries, 4, &count);
    CHECK(r == PEL_OK);

    size_t needed = 0;
    r = pel_grain_table_write(entries, count, NULL, 0, &needed);
    CHECK(r == PEL_ERR_TRUNCATED);

    char guard_buf[1024];
    CHECK(needed + 16 < sizeof(guard_buf));

    for (size_t cap = 0; cap < needed; cap++) {
        (void)memset(guard_buf, 0x5A, sizeof(guard_buf));
        size_t written = 0;
        r = pel_grain_table_write(entries, count, guard_buf, cap, &written);
        CHECK(r == PEL_ERR_TRUNCATED);
        CHECK(written == needed);
        CHECK(check_guard_bytes(guard_buf, cap, sizeof(guard_buf), 0x5A));
    }
}

static void test_boundary_lag0(void)
{
    const char *text = "filmgrn1\n"
                       "E 0 1000 1 65535 1\n"
                       "\tp 0 6 0 8 0 0 0 0 0 255 255 511\n"
                       "\tsY 0 \n"
                       "\tsCb 0\n"
                       "\tsCr 0\n"
                       "\tcY\n"
                       "\tcCb 127\n"
                       "\tcCr -128\n";

    PelorusGrainTableEntry e[2];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(text, strlen(text), e, 2, &count);
    CHECK(r == PEL_OK);
    CHECK(count == 1);
    CHECK(e[0].params.seed == 65535);
    CHECK(e[0].params.ar_coeff_lag == 0);
    CHECK(e[0].params.num_y_points == 0);
    CHECK(e[0].params.num_uv_points[0] == 0);
    CHECK(e[0].params.num_uv_points[1] == 0);

    char out[1024];
    size_t written = 0;
    r = pel_grain_table_write(e, count, out, sizeof(out), &written);
    CHECK(r == PEL_OK);
    CHECK(written == strlen(text));
    CHECK(memcmp(out, text, written) == 0);
}

static void test_boundary_offsets(void)
{
    const char *text = "filmgrn1\n"
                       "E 0 1000 1 10 1\n"
                       "\tp 0 6 0 8 0 0 0 0 0 255 255 511\n"
                       "\tsY 0 \n"
                       "\tsCb 0\n"
                       "\tsCr 0\n"
                       "\tcY\n"
                       "\tcCb 127\n"
                       "\tcCr -128\n";

    PelorusGrainTableEntry e[2];
    size_t count = 0;
    pel_result r = pel_grain_table_parse(text, strlen(text), e, 2, &count);
    CHECK(r == PEL_OK);
    CHECK(e[0].params.uv_offset[0] == -256);
    CHECK(e[0].params.uv_offset[1] == 255);
    CHECK(e[0].params.uv_mult[0] == -128);
    CHECK(e[0].params.uv_mult[1] == 127);
    CHECK(e[0].params.ar_coeffs_uv[0][0] == 127);
    CHECK(e[0].params.ar_coeffs_uv[1][0] == -128);
}

static void test_boundary_empty_table(void)
{
    const char *empty_text = "filmgrn1\n";
    PelorusGrainTableEntry e[2];
    size_t count = 99;
    pel_result r = pel_grain_table_parse(empty_text, strlen(empty_text), e, 2, &count);
    CHECK(r == PEL_OK);
    CHECK(count == 0);

    char out[64];
    size_t written = 0;
    r = pel_grain_table_write(NULL, 0, out, sizeof(out), &written);
    CHECK(r == PEL_OK);
    CHECK(written == strlen(empty_text));
    CHECK(memcmp(out, empty_text, written) == 0);
}

static void test_negative_range_write_uv_overflow_and_boundaries(void)
{
    PelorusGrainTableEntry e;
    char buf[512];
    size_t written = 0;

    memset(&e, 0, sizeof(e));
    e.end_time = 100;
    e.update_parameters = 1;
    e.params.scaling_shift = 8;
    e.params.ar_coeff_shift = 6;

    e.params.uv_mult[0] = INT32_MAX;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_mult[0] = 0;

    e.params.uv_mult_luma[1] = INT32_MIN;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_mult_luma[1] = 0;

    e.params.uv_offset[0] = INT32_MAX - 100;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    e.params.uv_offset[0] = 0;

    e.params.uv_mult[0] = -128;
    e.params.uv_mult[1] = 127;
    e.params.uv_offset[0] = -256;
    e.params.uv_offset[1] = 255;
    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_OK);
}

static void test_write_written_set_on_failure(void)
{
    PelorusGrainTableEntry e;
    char buf[512];
    size_t written = 12345;

    memset(&e, 0, sizeof(e));
    e.end_time = 100;
    e.update_parameters = 1;
    e.params.scaling_shift = 7;
    e.params.ar_coeff_shift = 6;

    CHECK(pel_grain_table_write(&e, 1, buf, sizeof(buf), &written) == PEL_ERR_RANGE);
    CHECK(written == 0);

    written = 12345;
    CHECK(pel_grain_table_write(NULL, 1, buf, sizeof(buf), &written) == PEL_ERR_INVALID);
    CHECK(written == 0);
}

int main(int argc, char **argv)
{
    test_positive_parse();
    test_positive_write();
    if (argc > 1) {
        test_file_fixture(argv[1]);
    }
    test_negative_range_header();
    test_negative_range_p_scalars1();
    test_negative_range_p_scalars2();
    test_negative_range_points();
    test_negative_range_coeffs();
    test_negative_range_write_scalars();
    test_negative_range_write_uv_and_points();
    test_negative_range_write_uv_overflow_and_boundaries();
    test_write_written_set_on_failure();
    test_negative_syntax_null();
    test_negative_syntax_magic_and_short();
    test_out_capacity();
    test_buf_capacity_needed();
    test_buf_capacity_sweep();
    test_boundary_lag0();
    test_boundary_offsets();
    test_boundary_empty_table();

    if (g_fail != 0) {
        (void)fprintf(stderr, "grain_table_test: %d failures\n", g_fail);
        return 1;
    }
    return 0;
}
