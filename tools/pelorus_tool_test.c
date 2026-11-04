/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus_tool_common.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(cond, fails)                                                                         \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            (*(fails))++;                                                                          \
        }                                                                                          \
    } while (0)

/* Test parsing of PELORUS_OPT_U32 options for valid numbers, overflow, negative values, and trailing garbage.
 * Increments *fails for each assertion that fails. */
static void test_u32_kind(int *fails)
{
    uint32_t val = 0;
    const PelorusOpt opts[] = {
        {"--iter", PELORUS_OPT_U32, &val, 0, NULL, NULL},
    };
    char *argv_ok[] = {"tool", "--iter", "4294967295"};
    char *argv_oflow[] = {"tool", "--iter", "4294967296"};
    char *argv_neg[] = {"tool", "--iter", "-1"};
    char *argv_trail[] = {"tool", "--iter", "1x"};
    char *argv_empty[] = {"tool", "--iter", ""};

    CHECK(pelorus_tool_parse_args("tool", 3, argv_ok, opts, 1, NULL) == 0, fails);
    CHECK(val == 4294967295u, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_oflow, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_neg, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_trail, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_empty, opts, 1, NULL) == -1, fails);
}

/* Test parsing of PELORUS_OPT_I32 options for boundary values, overflow, underflow, and malformed strings.
 * Increments *fails for each assertion that fails. */
static void test_i32_kind(int *fails)
{
    int32_t val = 0;
    const PelorusOpt opts[] = {
        {"--dir", PELORUS_OPT_I32, &val, 0, NULL, NULL},
    };
    char *argv_max[] = {"tool", "--dir", "2147483647"};
    char *argv_min[] = {"tool", "--dir", "-2147483648"};
    char *argv_oflow[] = {"tool", "--dir", "2147483648"};
    char *argv_uflow[] = {"tool", "--dir", "-2147483649"};
    char *argv_trail[] = {"tool", "--dir", "1x"};
    char *argv_empty[] = {"tool", "--dir", ""};

    CHECK(pelorus_tool_parse_args("tool", 3, argv_max, opts, 1, NULL) == 0, fails);
    CHECK(val == 2147483647, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_min, opts, 1, NULL) == 0, fails);
    CHECK(val == (-2147483647 - 1), fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_oflow, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_uflow, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_trail, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_empty, opts, 1, NULL) == -1, fails);
}

/* Test parsing of PELORUS_OPT_DOUBLE options for valid floats, empty values, trailing garbage, and non-finite inputs.
 * Increments *fails for each assertion that fails. */
static void test_double_kind(int *fails)
{
    double val = 0.0;
    const PelorusOpt opts[] = {
        {"--score", PELORUS_OPT_DOUBLE, &val, 0, NULL, NULL},
    };
    char *argv_ok[] = {"tool", "--score", "92.125"};
    char *argv_trail[] = {"tool", "--score", "1x"};
    char *argv_empty[] = {"tool", "--score", ""};
    char *argv_nan[] = {"tool", "--score", "nan"};
    char *argv_inf[] = {"tool", "--score", "inf"};
    char *argv_ninf[] = {"tool", "--score", "-inf"};

    CHECK(pelorus_tool_parse_args("tool", 3, argv_ok, opts, 1, NULL) == 0, fails);
    CHECK(fabs(val - 92.125) < 1e-9, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_trail, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_empty, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_nan, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_inf, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_ninf, opts, 1, NULL) == -1, fails);
}

/* Test parsing of PELORUS_OPT_FLOAT options for valid floats, trailing garbage, empty values, and non-finite inputs.
 * Increments *fails for each assertion that fails. */
static void test_float_kind(int *fails)
{
    float val = 0.0f;
    const PelorusOpt opts[] = {
        {"--crf", PELORUS_OPT_FLOAT, &val, 0, NULL, NULL},
    };
    char *argv_ok[] = {"tool", "--crf", "23.5"};
    char *argv_trail[] = {"tool", "--crf", "1x"};
    char *argv_empty[] = {"tool", "--crf", ""};
    char *argv_nan[] = {"tool", "--crf", "nan"};
    char *argv_inf[] = {"tool", "--crf", "inf"};

    CHECK(pelorus_tool_parse_args("tool", 3, argv_ok, opts, 1, NULL) == 0, fails);
    CHECK(fabsf(val - 23.5f) < 1e-4f, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_trail, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_empty, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_nan, opts, 1, NULL) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_inf, opts, 1, NULL) == -1, fails);
}

/* Test parsing of PELORUS_OPT_CHOICE and PELORUS_OPT_STRING options, verifying mapped values and invalid choices.
 * Increments *fails for each assertion that fails. */
static void test_choice_and_string_kind(int *fails)
{
    int cval = -1;
    const char *sval = NULL;
    static const char *const choices[] = {"alpha", "beta", "gamma", NULL};
    static const int cvals[] = {10, 20, 30};
    const PelorusOpt opts[] = {
        {"--mode", PELORUS_OPT_CHOICE, &cval, 0, choices, cvals},
        {"--name", PELORUS_OPT_STRING, (void *)&sval, 0, NULL, NULL},
    };
    char *argv_ok[] = {"tool", "--mode", "beta", "--name", "pelorus"};
    char *argv_miss[] = {"tool", "--mode", "delta", "--name", "pelorus"};

    CHECK(pelorus_tool_parse_args("tool", 5, argv_ok, opts, 2, NULL) == 0, fails);
    CHECK(cval == 20, fails);
    CHECK(sval != NULL && strcmp(sval, "pelorus") == 0, fails);
    CHECK(pelorus_tool_parse_args("tool", 5, argv_miss, opts, 2, NULL) == -1, fails);
}

/* Test general argument parser rules including missing option values, duplicates, unknown options, and required arguments.
 * Increments *fails for each assertion that fails. */
static void test_parser_rules(int *fails)
{
    const char *pos_val = NULL;
    double dval = 0.0;
    const PelorusOpt opts[] = {
        {"--target", PELORUS_OPT_DOUBLE, &dval, 1, NULL, NULL},
    };
    const PelorusPositional pos = {"<input>", &pos_val, 1};
    char *argv_missing_val[] = {"tool", "<input>", "--target"};
    char *argv_dup[] = {"tool", "--target", "1.0", "--target", "2.0", "<input>"};
    char *argv_unk[] = {"tool", "--target", "1.0", "--unknown", "x", "<input>"};
    char *argv_extra_pos[] = {"tool", "--target", "1.0", "pos1", "pos2"};
    char *argv_no_req_opt[] = {"tool", "<input>"};
    char *argv_no_req_pos[] = {"tool", "--target", "1.0"};

    CHECK(pelorus_tool_parse_args("tool", 3, argv_missing_val, opts, 1, &pos) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 6, argv_dup, opts, 1, &pos) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 6, argv_unk, opts, 1, &pos) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 5, argv_extra_pos, opts, 1, &pos) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 2, argv_no_req_opt, opts, 1, &pos) == -1, fails);
    CHECK(pelorus_tool_parse_args("tool", 3, argv_no_req_pos, opts, 1, &pos) == -1, fails);
}

/* Test pelorus_tool_parse_crf validation across boundaries [0.0, 63.0], invalid numbers, and trailing characters.
 * Increments *fails for each assertion that fails. */
static void test_crf_helper(int *fails)
{
    float val = 0.0f;
    char *end = NULL;

    CHECK(pelorus_tool_parse_crf("0.0", &end, &val) == 0 && fabsf(val - 0.0f) < 1e-4f, fails);
    CHECK(pelorus_tool_parse_crf("63.0", &end, &val) == 0 && fabsf(val - 63.0f) < 1e-4f, fails);
    CHECK(pelorus_tool_parse_crf("23.5", &end, &val) == 0 && fabsf(val - 23.5f) < 1e-4f, fails);
    CHECK(pelorus_tool_parse_crf("-0.1", &end, &val) == -1, fails);
    CHECK(pelorus_tool_parse_crf("63.1", &end, &val) == -1, fails);
    CHECK(pelorus_tool_parse_crf("1x", &end, &val) == -1, fails);
    CHECK(pelorus_tool_parse_crf("nan", &end, &val) == -1, fails);
}

/* Execute all unit test suites for the CLI tool argument parsing helpers.
 * Returns EXIT_SUCCESS if all checks pass, or EXIT_FAILURE if any failure occurred. */
int main(void)
{
    int fails = 0;

    test_u32_kind(&fails);
    test_i32_kind(&fails);
    test_double_kind(&fails);
    test_float_kind(&fails);
    test_choice_and_string_kind(&fails);
    test_parser_rules(&fails);
    test_crf_helper(&fails);

    if (fails > 0) {
        (void)fprintf(stderr, "pelorus_tool_test: %d check(s) failed\n", fails);
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
