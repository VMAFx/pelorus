/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "encode_options_internal.h"
#include "pelorus/encode_options.h"
#include "pelorus/encode_record.h"

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

static PelorusEncodeOptions *alloc_opts(void)
{
    PelorusEncodeOptions *opts = (PelorusEncodeOptions *)calloc(1, sizeof(PelorusEncodeOptions));
    CHECK(opts != NULL);
    return opts;
}

static pel_result build_record(const PelorusEncodeOptions *opts, char *json, size_t cap,
                               size_t *out_len)
{
    PelorusEncodeRecordInput in;

    (void)memset(&in, 0, sizeof(in));
    in.encoder_name = "libx264";
    in.encoder_version = "0.164.x";
    in.encoder_codec = "h264";
    in.encoder_params = opts->params;
    in.nb_encoder_params = opts->nb_params;
    in.encoder_unknown_params = opts->unknown_params;
    in.nb_encoder_unknown_params = opts->nb_unknown_params;
    in.input_digest = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    return pel_encode_record_build(&in, json, cap, out_len);
}

static pel_result build_digest(const PelorusEncodeOptions *opts, char digest[72])
{
    char json[PEL_ENCODE_RECORD_MAX_BYTES];
    size_t out_len = 0;
    pel_result r;
    uint8_t raw_hash[32];
    size_t i;
    static const char hex[] = "0123456789abcdef";

    r = build_record(opts, json, sizeof(json), &out_len);
    if (r != PEL_OK) {
        return r;
    }
    r = pel_encode_record_hash(json, out_len, raw_hash);
    if (r != PEL_OK) {
        return r;
    }
    (void)memcpy(digest, "sha256:", 7);
    for (i = 0; i < 32; i++) {
        digest[7 + 2 * i] = hex[((unsigned int)raw_hash[i] >> 4u) & 0x0fu];
        digest[7 + 2 * i + 1] = hex[(unsigned int)raw_hash[i] & 0x0fu];
    }
    digest[71] = '\0';
    return PEL_OK;
}

static void test_sizeof_options(void)
{
    CHECK(sizeof(PelorusEncodeOptions) == 278552);
}

static void test_ordering_and_sorting(void)
{
    PelorusEncodeOptions *opts1 = alloc_opts();
    PelorusEncodeOptions *opts2 = alloc_opts();
    char d1[72];
    char d2[72];
    const char s1[] = "ref=3:bframes=2:crf=23";
    const char s2[] = "crf=23:ref=3:bframes=2";

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s1, strlen(s1), opts1) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s2, strlen(s2), opts2) == PEL_OK);
    CHECK(opts1->nb_params == 3);
    CHECK(opts2->nb_params == 3);

    CHECK(strcmp(opts1->params[0].key, "bframes") == 0);
    CHECK(strcmp(opts1->params[1].key, "crf") == 0);
    CHECK(strcmp(opts1->params[2].key, "ref") == 0);

    CHECK(strcmp(opts2->params[0].key, "bframes") == 0);
    CHECK(strcmp(opts2->params[1].key, "crf") == 0);
    CHECK(strcmp(opts2->params[2].key, "ref") == 0);

    CHECK(build_digest(opts1, d1) == PEL_OK);
    CHECK(build_digest(opts2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);
    free(opts1);
    free(opts2);
}

static void test_normalisation_aliases(void)
{
    PelorusEncodeOptions *opts1 = alloc_opts();
    PelorusEncodeOptions *opts2 = alloc_opts();
    char d1[72];
    char d2[72];

    const char s1[] = "b_adapt=2";
    const char s2[] = "b-adapt=2";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s1, strlen(s1), opts1) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s2, strlen(s2), opts2) == PEL_OK);
    CHECK(opts1->nb_params == 1 && opts2->nb_params == 1);
    CHECK(strcmp(opts1->params[0].key, "b-adapt") == 0);
    CHECK(strcmp(opts2->params[0].key, "b-adapt") == 0);
    CHECK(build_digest(opts1, d1) == PEL_OK);
    CHECK(build_digest(opts2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);

    const char s3[] = "ref=3";
    const char s4[] = "frameref=3";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s3, strlen(s3), opts1) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s4, strlen(s4), opts2) == PEL_OK);
    CHECK(strcmp(opts1->params[0].key, "ref") == 0);
    CHECK(strcmp(opts2->params[0].key, "ref") == 0);
    CHECK(build_digest(opts1, d1) == PEL_OK);
    CHECK(build_digest(opts2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);

    const char s5[] = "ipratio=1.4";
    const char s6[] = "ip-factor=1.4";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, s5, strlen(s5), opts1) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, s6, strlen(s6), opts2) == PEL_OK);
    CHECK(strcmp(opts1->params[0].key, "ipratio") == 0);
    CHECK(strcmp(opts2->params[0].key, "ipratio") == 0);
    CHECK(build_digest(opts1, d1) == PEL_OK);
    CHECK(build_digest(opts2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);
    free(opts1);
    free(opts2);
}

static void test_boolean_and_integer_spellings(void)
{
    PelorusEncodeOptions *opts1 = alloc_opts();
    PelorusEncodeOptions *opts2 = alloc_opts();
    PelorusEncodeOptions *opts3 = alloc_opts();
    char d1[72];
    char d2[72];
    char d3[72];

    const char s1[] = "cabac=1";
    const char s2[] = "cabac=yes";
    const char s3[] = "cabac=true";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s1, strlen(s1), opts1) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s2, strlen(s2), opts2) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s3, strlen(s3), opts3) == PEL_OK);
    CHECK(strcmp(opts1->params[0].values[0], "true") == 0);
    CHECK(strcmp(opts2->params[0].values[0], "true") == 0);
    CHECK(strcmp(opts3->params[0].values[0], "true") == 0);
    CHECK(build_digest(opts1, d1) == PEL_OK);
    CHECK(build_digest(opts2, d2) == PEL_OK);
    CHECK(build_digest(opts3, d3) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);
    CHECK(strcmp(d1, d3) == 0);

    const char s_int8[] = "ref=8";
    const char s_int08[] = "ref=08";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_int8, strlen(s_int8), opts1) ==
          PEL_OK);
    CHECK(opts1->nb_params == 1 && opts1->nb_unknown_params == 0);
    CHECK(strcmp(opts1->params[0].values[0], "8") == 0);

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_int08, strlen(s_int08), opts2) ==
          PEL_OK);
    CHECK(opts2->nb_params == 0 && opts2->nb_unknown_params == 1);
    if (opts2->nb_unknown_params > 0) {
        CHECK(strcmp(opts2->unknown_params[0].key, "ref") == 0);
        CHECK(strcmp(opts2->unknown_params[0].values[0], "08") == 0);
    }

    free(opts1);
    free(opts2);
    free(opts3);
}

static void test_negatives(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    const char s_unk[] = "some-new-option=1";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_unk, strlen(s_unk), opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    CHECK(strcmp(opts->unknown_params[0].key, "some-new-option") == 0);
    CHECK(strcmp(opts->unknown_params[0].values[0], "1") == 0);

    const char s_bad[] = "crf=abc";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_bad, strlen(s_bad), opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    CHECK(strcmp(opts->unknown_params[0].key, "crf") == 0);
    CHECK(strcmp(opts->unknown_params[0].values[0], "abc") == 0);

    /* malformed strings: empty key, missing '=', empty value */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "crf", 3, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "=3", 2, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "crf=", 4, opts) == PEL_ERR_INVALID);

    /* NULL args and bad dialect */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, NULL, 5, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "crf=23", 6, NULL) == PEL_ERR_INVALID);
    /* NOLINTNEXTLINE(clang-analyzer-optin.core.EnumCastOutOfRange): ADR-0198; test invalid enum dialect */
    CHECK(pel_encode_options_parse((pel_encode_options_dialect)99, "crf=23", 6, opts) ==
          PEL_ERR_INVALID);
    free(opts);
}

static void test_boundary_keys_values(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    char long_buf[4096];
    size_t i;
    size_t pos;

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "", 0, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 0);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "", 1, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 0);

    const char dup_str[] = "crf=20:crf=24";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, dup_str, strlen(dup_str), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 1);
    CHECK(strcmp(opts->params[0].key, "crf") == 0);
    CHECK(strcmp(opts->params[0].values[0], "f64:4038000000000000") == 0);

    const char dup_alias[] = "b_adapt=1:b-adapt=2";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, dup_alias, strlen(dup_alias), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 1);
    CHECK(strcmp(opts->params[0].key, "b-adapt") == 0);
    CHECK(strcmp(opts->params[0].values[0], "2") == 0);

    const char s_col[] = "sar=16\\:9";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_col, strlen(s_col), opts) == PEL_OK);
    CHECK(opts->nb_params == 1);
    CHECK(strcmp(opts->params[0].key, "sar") == 0);
    CHECK(strcmp(opts->params[0].values[0], "16:9") == 0);

    pos = 0;
    for (i = 0; i < 64; i++) {
        long_buf[pos++] = 'k';
    }
    long_buf[pos++] = '=';
    long_buf[pos++] = '1';
    long_buf[pos] = '\0';
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, long_buf, pos, opts) == PEL_OK);
    CHECK(opts->nb_unknown_params == 1);
    CHECK(strlen(opts->unknown_params[0].key) == 64);

    pos = 0;
    for (i = 0; i < 65; i++) {
        long_buf[pos++] = 'k';
    }
    long_buf[pos++] = '=';
    long_buf[pos++] = '1';
    long_buf[pos] = '\0';
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, long_buf, pos, opts) == PEL_ERR_RANGE);
    free(opts);
}

static void test_boundary_param_counts(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    char long_buf[4096];
    size_t i;
    size_t pos = 0;
    char *huge;

    long_buf[pos++] = 'k';
    long_buf[pos++] = '=';
    for (i = 0; i < 1024; i++) {
        long_buf[pos++] = 'v';
    }
    long_buf[pos] = '\0';
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, long_buf, pos, opts) == PEL_OK);
    CHECK(opts->nb_unknown_params == 1);
    CHECK(strlen(opts->unknown_params[0].values[0]) == 1024);

    pos = 0;
    long_buf[pos++] = 'k';
    long_buf[pos++] = '=';
    for (i = 0; i < 1025; i++) {
        long_buf[pos++] = 'v';
    }
    long_buf[pos] = '\0';
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, long_buf, pos, opts) == PEL_ERR_RANGE);

    huge = (char *)malloc((size_t)257 * 16);
    CHECK(huge != NULL);
    if (huge) {
        pos = 0;
        for (i = 0; i < 256; i++) {
            int written = snprintf(huge + pos, 16, "%su%zu=1", i > 0 ? ":" : "", i);
            CHECK(written > 0);
            pos += (size_t)written;
        }
        CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, huge, pos, opts) == PEL_OK);
        CHECK(opts->nb_unknown_params == 256);

        int written = snprintf(huge + pos, 16, ":u256=1");
        CHECK(written > 0);
        pos += (size_t)written;
        CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, huge, pos, opts) == PEL_ERR_RANGE);
        free(huge);
    }
    free(opts);
}

static void test_negation_extended(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    /* x264: nocabac=1 and no-cabac=1 -> cabac=false */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "nocabac=1", 9, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    CHECK(strcmp(opts->params[0].key, "cabac") == 0);
    CHECK(strcmp(opts->params[0].values[0], "false") == 0);

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "no-cabac=1", 10, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    CHECK(strcmp(opts->params[0].key, "cabac") == 0);
    CHECK(strcmp(opts->params[0].values[0], "false") == 0);

    /* x265: no-sao=1 -> sao=false */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "no-sao=1", 8, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    CHECK(strcmp(opts->params[0].key, "sao") == 0);
    CHECK(strcmp(opts->params[0].values[0], "false") == 0);

    /* prefix stripped before lookup, as x265 does: no-tskip-fast -> tskip-fast */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "no-tskip-fast=1", 15, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "tskip-fast") == 0);
        CHECK(strcmp(opts->params[0].values[0], "false") == 0);
    }

    /*
     * non-BOOL negation stays unknown under the normalised key: the encoders
     * pass "true"/"false" to the base option, which the record does not type.
     */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "no-scenecut=1", 13, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "no-scenecut") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "1") == 0);
    }

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "no_b_adapt=1", 12, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "no-b-adapt") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "1") == 0);
    }

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "noweightp=1", 11, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "noweightp") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "1") == 0);
    }

    free(opts);
}

static void test_unknown_key_normalised(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    const char s_x264[] = "foo_bar=1:foo-bar=2";
    const char s_x265[] = "--foo=1:foo=2";

    /* unknown keys collapse on the normalised spelling; last wins */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_x264, strlen(s_x264), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "foo-bar") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "2") == 0);
    }

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, s_x265, strlen(s_x265), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "foo") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "2") == 0);
    }

    free(opts);
}

static void test_unknown_spellings_last_wins(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    const char s_alias[] = "b_adapt=x:b-adapt=y";

    /* a valid value drops an earlier invalid negated spelling (last wins) */
    const char s_neg_then_valid[] = "no-cabac=zzz:cabac=1";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_neg_then_valid,
                                   strlen(s_neg_then_valid), opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    const char s_bare_then_bad[] = "nocabac=zzz:cabac=yyy";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_bare_then_bad,
                                   strlen(s_bare_then_bad), opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "cabac") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "yyy") == 0);
    }

    /* known key with invalid values collapses on the canonical key */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_alias, strlen(s_alias), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "b-adapt") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "y") == 0);
    }

    free(opts);
}

static void test_int_base_prefixes(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    const char s_hex[] = "bframes=0x10";
    const char s_oct[] = "bframes=010";

    /* base 0, as x264 and x265 parse integers: hex and octal prefixes */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_hex, strlen(s_hex), opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "bframes") == 0);
        CHECK(strcmp(opts->params[0].values[0], "16") == 0);
    }

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, s_oct, strlen(s_oct), opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "bframes") == 0);
        CHECK(strcmp(opts->params[0].values[0], "8") == 0);
    }

    free(opts);
}

static void test_quotes_and_whitespace(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    /* whitespace around keys and values trimmed */
    const char s_trim[] = " crf = 23 ";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_trim, strlen(s_trim), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "crf") == 0);
        CHECK(strcmp(opts->params[0].values[0], "f64:4037000000000000") == 0);
    }

    /* quotes preserve inner whitespace */
    const char s_quotes[] = " 'asm' = ' avx2 ' ";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_quotes, strlen(s_quotes), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "asm") == 0);
        CHECK(strcmp(opts->params[0].values[0], " avx2 ") == 0);
    }

    /* unterminated quote: rest copied literally (per FFmpeg av_get_token) */
    const char s_unterminated[] = "a='b";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_unterminated, strlen(s_unterminated),
                                   opts) == PEL_OK);
    CHECK(opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "a") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "b") == 0);
    }

    /* trailing backslash kept literally (per FFmpeg av_get_token) */
    const char s_trailing_bs[] = "a=b\\";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_trailing_bs, strlen(s_trailing_bs),
                                   opts) == PEL_OK);
    CHECK(opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "a") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "b\\") == 0);
    }

    free(opts);
}

static void test_invalid_after_valid_dup(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    /* subme=2:subq=zzz -> subme removed from params, subme=zzz (canonical) in unknown_params */
    const char s_alias_dup[] = "subme=2:subq=zzz";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_alias_dup, strlen(s_alias_dup),
                                   opts) == PEL_OK);
    CHECK(opts->nb_params == 0);
    CHECK(opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "subme") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "zzz") == 0);
    }

    /* cabac=1:no-cabac=zzz -> cabac removed from params, no-cabac=zzz in unknown_params */
    const char s_neg_dup[] = "cabac=1:no-cabac=zzz";
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_neg_dup, strlen(s_neg_dup), opts) ==
          PEL_OK);
    CHECK(opts->nb_params == 0);
    CHECK(opts->nb_unknown_params == 1);
    if (opts->nb_unknown_params > 0) {
        CHECK(strcmp(opts->unknown_params[0].key, "no-cabac") == 0);
        CHECK(strcmp(opts->unknown_params[0].values[0], "zzz") == 0);
    }

    free(opts);
}

static void test_arena_exhaustion(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    char *big = (char *)malloc(300000);
    size_t pos = 0;
    size_t i;

    CHECK(big != NULL);
    if (big) {
        for (i = 0; i < 256; i++) {
            int w = snprintf(big + pos, 1050, "%su%zu=", i > 0 ? ":" : "", i);
            pos += (size_t)w;
            memset(big + pos, 'x', 1020);
            pos += 1020;
        }
        big[pos] = '\0';
        CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, big, pos, opts) == PEL_ERR_RANGE);
        CHECK(opts->nb_params == 0);
        CHECK(opts->nb_unknown_params == 0);
        CHECK(opts->arena_used == 0);
        free(big);
    }
    free(opts);
}

static void test_257_known_params(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    size_t i;
    char key[32];
    pel_result r = PEL_OK;

    /* Simulate 256 known params already in opts->params */
    opts->nb_params = PEL_ENCODE_RECORD_MAX_MEMBERS;
    /* Adding the 257th parameter via parse or store_entry must fail with PEL_ERR_RANGE */
    for (i = 0; i < PEL_ENCODE_RECORD_MAX_MEMBERS; i++) {
        (void)snprintf(key, sizeof(key), "k%zu", i);
        opts->params[i].key = "k";
        opts->params[i].values = &opts->param_val_ptrs[i];
        opts->params[i].nb_values = 1;
    }
    /* Calling parse with another valid pair when capacity is full triggers boundary check */
    r = pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "crf=23", 6, opts);
    CHECK(r == PEL_OK);
    CHECK(opts->nb_params == 1);
    free(opts);
}

static void test_x265_case_sensitive_bool(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    /* x265 is case-sensitive: "annexb=True" fails atobool, lands in unknown_params */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "annexb=True", 11, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 1);
    CHECK(strcmp(opts->unknown_params[0].key, "annexb") == 0);
    CHECK(strcmp(opts->unknown_params[0].values[0], "True") == 0);

    /* x265 accepts lowercase "true" */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "annexb=true", 11, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    CHECK(strcmp(opts->params[0].key, "annexb") == 0);
    CHECK(strcmp(opts->params[0].values[0], "true") == 0);

    /* x264 is case-insensitive: "annexb=True" normalises to "true" */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "annexb=True", 11, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    CHECK(strcmp(opts->params[0].key, "annexb") == 0);
    CHECK(strcmp(opts->params[0].values[0], "true") == 0);

    free(opts);
}

static void test_int_overflow_range(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    /* bitrate is INT; |v| > INT32_MAX must return PEL_ERR_RANGE and zero out */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "bitrate=3000000000", 18, opts) ==
          PEL_ERR_RANGE);
    CHECK(opts->nb_params == 0);
    CHECK(opts->nb_unknown_params == 0);
    CHECK(opts->arena_used == 0);

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "bitrate=2147483648", 18, opts) ==
          PEL_ERR_RANGE);
    CHECK(opts->nb_params == 0);
    CHECK(opts->nb_unknown_params == 0);
    CHECK(opts->arena_used == 0);

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "bitrate=-2147483649", 19, opts) ==
          PEL_ERR_RANGE);
    CHECK(opts->nb_params == 0);
    CHECK(opts->nb_unknown_params == 0);
    CHECK(opts->arena_used == 0);

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, "bitrate=99999999999999999999", 29,
                                   opts) == PEL_ERR_RANGE);
    CHECK(opts->nb_params == 0);
    CHECK(opts->nb_unknown_params == 0);
    CHECK(opts->arena_used == 0);

    free(opts);
}

static void test_repeated_duplicates(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    char *buf = (char *)malloc((size_t)300 * 8);
    size_t pos = 0;
    size_t i;

    CHECK(buf != NULL);
    if (buf != NULL) {
        for (i = 0; i < 300; i++) {
            int w = snprintf(buf + pos, 8, "%scrf=1", i > 0 ? ":" : "");
            pos += (size_t)w;
        }
        buf[pos] = '\0';
        CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, buf, pos, opts) == PEL_OK);
        CHECK(opts->nb_params == 1);
        CHECK(opts->nb_unknown_params == 0);
        CHECK(strcmp(opts->params[0].key, "crf") == 0);
        CHECK(opts->arena_used < 100);
        free(buf);
    }
    free(opts);
}

/* Sorted, the expected size, and every alias names a canonical row of the
 * same type. */
static void check_av1_table(const PelEncodeOptDef *(*table)(size_t *),
                            const PelEncodeOptDef *(*lookup)(const char *), size_t want)
{
    size_t count = 0;
    const PelEncodeOptDef *t = table(&count);
    size_t i;

    CHECK(t != NULL && count == want);
    if (t == NULL) {
        return;
    }
    for (i = 0; i < count; i++) {
        const PelEncodeOptDef *c = lookup(t[i].canonical);
        CHECK(i == 0 || strcmp(t[i - 1].name, t[i].name) < 0);
        CHECK(c != NULL && c->type == t[i].type && strcmp(c->canonical, t[i].canonical) == 0);
    }
}

static void test_table_sorting(void)
{
    size_t count_x264 = 0;
    size_t count_x265 = 0;
    const PelEncodeOptDef *t264 = pel_encode_options_x264_table(&count_x264);
    const PelEncodeOptDef *t265 = pel_encode_options_x265_table(&count_x265);
    size_t i;

    CHECK(t264 != NULL && count_x264 == 142);
    if (t264 != NULL) {
        for (i = 1; i < count_x264; i++) {
            CHECK(strcmp(t264[i - 1].name, t264[i].name) < 0);
        }
    }

    CHECK(t265 != NULL && count_x265 == 233);
    if (t265 != NULL) {
        for (i = 1; i < count_x265; i++) {
            CHECK(strcmp(t265[i - 1].name, t265[i].name) < 0);
        }
    }
    check_av1_table(pel_encode_options_svtav1_table, pel_encode_options_svtav1_lookup, 121);
    check_av1_table(pel_encode_options_aom_table, pel_encode_options_aom_lookup, 122);
}

static void test_x265_types(void)
{
    PelorusEncodeOptions *opts = alloc_opts();

    /* x265: aq-mode is INT */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "aq-mode=2", 9, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "aq-mode") == 0);
        CHECK(strcmp(opts->params[0].values[0], "2") == 0);
    }

    /* x265: level-idc and level are STRING (allows "5.1" or "51") */
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "level=5.1", 9, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "level-idc") == 0);
        CHECK(strcmp(opts->params[0].values[0], "5.1") == 0);
    }

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, "level-idc=5.1", 13, opts) == PEL_OK);
    CHECK(opts->nb_params == 1 && opts->nb_unknown_params == 0);
    if (opts->nb_params > 0) {
        CHECK(strcmp(opts->params[0].key, "level-idc") == 0);
        CHECK(strcmp(opts->params[0].values[0], "5.1") == 0);
    }

    free(opts);
}

static void check_one(const PelorusEncodeParam *arr, uint32_t n, const char *key, const char *val)
{
    uint32_t i;
    for (i = 0; i < n; i++) {
        if (strcmp(arr[i].key, key) == 0) {
            CHECK(strcmp(arr[i].values[0], val) == 0);
            return;
        }
    }
    (void)fprintf(stderr, "FAIL missing key %s\n", key);
    g_fail++;
}

static pel_result parse_str(pel_encode_options_dialect d, const char *s, PelorusEncodeOptions *o)
{
    return pel_encode_options_parse(d, s, strlen(s), o);
}

static void test_svtav1_ordering_and_types(void)
{
    PelorusEncodeOptions *o1 = alloc_opts();
    PelorusEncodeOptions *o2 = alloc_opts();
    char d1[72];
    char d2[72];

    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "preset=8:crf=30:tune=0:keyint=5s", o1) == PEL_OK);
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "keyint=5s:tune=0:crf=30:preset=8", o2) == PEL_OK);
    CHECK(o1->nb_params == 4 && o1->nb_unknown_params == 0);
    check_one(o1->params, o1->nb_params, "preset", "8");
    check_one(o1->params, o1->nb_params, "crf", "f64:403e000000000000");
    check_one(o1->params, o1->nb_params, "tune", "0");
    check_one(o1->params, o1->nb_params, "keyint", "5s");
    CHECK(build_digest(o1, d1) == PEL_OK);
    CHECK(build_digest(o2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);

    /* aliases share one field upstream: w/width, h/height, q/qp */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "w=1920:h=1080:q=30:width=1280", o1) == PEL_OK);
    CHECK(o1->nb_params == 3 && o1->nb_unknown_params == 0);
    check_one(o1->params, o1->nb_params, "width", "1280");
    check_one(o1->params, o1->nb_params, "height", "1080");
    check_one(o1->params, o1->nb_params, "qp", "30");

    /* base-0 integers and case-insensitive booleans (str_to_uint, str_to_bool) */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "lp=0x10:enable-overlays=True", o1) == PEL_OK);
    check_one(o1->params, o1->nb_params, "lp", "16");
    check_one(o1->params, o1->nb_params, "enable-overlays", "true");

    /* boundary: empty string and duplicates */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "", o1) == PEL_OK);
    CHECK(o1->nb_params == 0 && o1->nb_unknown_params == 0);
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "preset=4:preset=8", o1) == PEL_OK);
    CHECK(o1->nb_params == 1);
    check_one(o1->params, o1->nb_params, "preset", "8");
    free(o1);
    free(o2);
}

static void test_svtav1_rejects(void)
{
    PelorusEncodeOptions *o = alloc_opts();

    /* no underscore folding and no negation: SVT-AV1 compares names exactly */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1,
                    "enable_overlays=1:no-enable-overlays=1:enable-overlays=0", o) == PEL_OK);
    CHECK(o->nb_params == 1 && o->nb_unknown_params == 2);
    check_one(o->params, o->nb_params, "enable-overlays", "false");
    check_one(o->unknown_params, o->nb_unknown_params, "enable_overlays", "1");
    check_one(o->unknown_params, o->nb_unknown_params, "no-enable-overlays", "1");

    /* unsigned option: a negative value is rejected and kept by name */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "lookahead=-1:bogus=7", o) == PEL_OK);
    CHECK(o->nb_params == 0 && o->nb_unknown_params == 2);
    check_one(o->unknown_params, o->nb_unknown_params, "lookahead", "-1");
    check_one(o->unknown_params, o->nb_unknown_params, "bogus", "7");

    /* unsigned bounds: UINT32_MAX fits, one more is a range error */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "fps-num=4294967295", o) == PEL_OK);
    check_one(o->params, o->nb_params, "fps-num", "4294967295");
    CHECK(parse_str(PEL_ENCODE_OPTIONS_SVTAV1, "fps-num=4294967296", o) == PEL_ERR_RANGE);
    CHECK(o->nb_params == 0 && o->nb_unknown_params == 0 && o->arena_used == 0);
    free(o);
}

static void test_aom_dialect(void)
{
    PelorusEncodeOptions *o1 = alloc_opts();
    PelorusEncodeOptions *o2 = alloc_opts();
    char d1[72];
    char d2[72];

    CHECK(parse_str(PEL_ENCODE_OPTIONS_AOM, "cpu-used=4:tune=ssim:enable-tpl-model=0", o1) ==
          PEL_OK);
    CHECK(parse_str(PEL_ENCODE_OPTIONS_AOM, "enable-tpl-model=0:cpu-used=4:tune=ssim", o2) ==
          PEL_OK);
    CHECK(o1->nb_params == 3 && o1->nb_unknown_params == 0);
    check_one(o1->params, o1->nb_params, "cpu-used", "4");
    check_one(o1->params, o1->nb_params, "tune", "ssim");
    CHECK(build_digest(o1, d1) == PEL_OK);
    CHECK(build_digest(o2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);

    /* base-10 integers (args_helper.c strtoul/strtol base 10); signed int keeps sign */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_AOM, "cpu-used=010:enable-adaptive-sharpness=-1", o1) ==
          PEL_OK);
    check_one(o1->params, o1->nb_params, "cpu-used", "10");
    check_one(o1->params, o1->nb_params, "enable-adaptive-sharpness", "-1");
    CHECK(parse_str(PEL_ENCODE_OPTIONS_AOM, "cpu-used=0x4", o1) == PEL_OK);
    CHECK(o1->nb_params == 0 && o1->nb_unknown_params == 1);
    check_one(o1->unknown_params, o1->nb_unknown_params, "cpu-used", "0x4");

    /* unsigned rejects a sign; no "--" strip, no underscore folding */
    CHECK(parse_str(PEL_ENCODE_OPTIONS_AOM, "cpu-used=-1:--row-mt=1:enable_cdef=0", o1) == PEL_OK);
    CHECK(o1->nb_params == 0 && o1->nb_unknown_params == 3);
    check_one(o1->unknown_params, o1->nb_unknown_params, "cpu-used", "-1");
    check_one(o1->unknown_params, o1->nb_unknown_params, "--row-mt", "1");
    check_one(o1->unknown_params, o1->nb_unknown_params, "enable_cdef", "0");
    free(o1);
    free(o2);
}

/* #265 failing first: a naive split on every ':' breaks "sar=16\:9" into three fields. */
static void test_x265_escaped_colon(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    const char s[] = "sar=16\\:9:crf=20";
    size_t naive_fields = 1;
    size_t i;

    for (i = 0; s[i] != '\0'; i++) {
        naive_fields += s[i] == ':' ? 1u : 0u;
    }
    CHECK(naive_fields == 3);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, s, strlen(s), opts) == PEL_OK);
    CHECK(opts->nb_params == 2 && opts->nb_unknown_params == 0);
    check_one(opts->params, opts->nb_params, "sar", "16:9");
    check_one(opts->params, opts->nb_params, "crf", "f64:4034000000000000");
    free(opts);
}

static void test_one_value_changes_digest(void)
{
    PelorusEncodeOptions *o1 = alloc_opts();
    PelorusEncodeOptions *o2 = alloc_opts();
    char d1[72];
    char d2[72];
    const char s1[] = "crf=23:ref=3:bframes=2";
    const char s2[] = "crf=23:ref=4:bframes=2";

    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s1, strlen(s1), o1) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s2, strlen(s2), o2) == PEL_OK);
    CHECK(build_digest(o1, d1) == PEL_OK);
    CHECK(build_digest(o2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) != 0);
    free(o1);
    free(o2);
}

/* Build, hash and verify the record of `s`; its digest goes to `hash`. */
static pel_result roundtrip(pel_encode_options_dialect d, const char *s, size_t len,
                            uint8_t hash[PEL_ENCODE_RECORD_DIGEST_SIZE])
{
    PelorusEncodeOptions *opts = alloc_opts();
    char json[PEL_ENCODE_RECORD_MAX_BYTES];
    size_t json_len = 0;
    pel_result r = PEL_ERR_NOMEM;

    if (opts != NULL) {
        r = pel_encode_options_parse(d, s, len, opts);
        if (r == PEL_OK) {
            r = build_record(opts, json, sizeof(json), &json_len);
        }
        if (r == PEL_OK) {
            r = pel_encode_record_hash(json, json_len, hash);
        }
        if (r == PEL_OK) {
            r = pel_encode_record_verify(json, json_len, hash);
        }
        free(opts);
    }
    return r;
}

static void test_dropped_key_fails_verify(void)
{
    PelorusEncodeOptions *opts = alloc_opts();
    char json[PEL_ENCODE_RECORD_MAX_BYTES];
    size_t json_len = 0;
    uint8_t full[PEL_ENCODE_RECORD_DIGEST_SIZE];
    const char s_full[] = "crf=23:ref=3:x-future=on";
    const char s_dropped[] = "crf=23:x-future=on";

    CHECK(roundtrip(PEL_ENCODE_OPTIONS_X264, s_full, strlen(s_full), full) == PEL_OK);
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X264, s_dropped, strlen(s_dropped), opts) ==
          PEL_OK);
    check_one(opts->unknown_params, opts->nb_unknown_params, "x-future", "on");
    CHECK(build_record(opts, json, sizeof(json), &json_len) == PEL_OK);
    CHECK(pel_encode_record_verify(json, json_len, NULL) == PEL_OK);
    CHECK(pel_encode_record_verify(json, json_len, full) == PEL_ERR_MISMATCH);
    free(opts);
}

static void test_roundtrip_boundaries(void)
{
    char buf[(size_t)PEL_ENCODE_RECORD_MAX_KEY + 2u + PEL_ENCODE_RECORD_MAX_VALUE];
    uint8_t hash[PEL_ENCODE_RECORD_DIGEST_SIZE];
    size_t pos = 0;
    size_t i;
    int d;

    for (d = (int)PEL_ENCODE_OPTIONS_X264; d <= (int)PEL_ENCODE_OPTIONS_AOM; d++) {
        CHECK(roundtrip((pel_encode_options_dialect)d, "", 0, hash) == PEL_OK);
    }
    for (i = 0; i < PEL_ENCODE_RECORD_MAX_KEY; i++) {
        buf[pos++] = 'k';
    }
    buf[pos++] = '=';
    for (i = 0; i < PEL_ENCODE_RECORD_MAX_VALUE; i++) {
        buf[pos++] = 'v';
    }
    CHECK(roundtrip(PEL_ENCODE_OPTIONS_X264, buf, pos, hash) == PEL_OK);
    CHECK(roundtrip(PEL_ENCODE_OPTIONS_X265, buf, pos, hash) == PEL_OK);
    buf[pos] = 'v';
    CHECK(roundtrip(PEL_ENCODE_OPTIONS_X264, buf, pos + 1u, hash) == PEL_ERR_RANGE);
}

/* AVOption digest for `encoder` with options `o` (ADR-0200); "" on error. */
static pel_result avopt_digest(const char *encoder, const PelorusEncodeOption *o, uint32_t n,
                               char digest[72])
{
    PelorusEncodeOptions *opts = alloc_opts();
    pel_result r = PEL_ERR_NOMEM;

    digest[0] = '\0';
    if (opts != NULL) {
        r = pel_encode_options_from_ffmpeg(encoder, o, n, opts);
        if (r == PEL_OK) {
            r = build_digest(opts, digest);
        }
        free(opts);
    }
    return r;
}

static const char *find_value(const PelorusEncodeParam *p, uint32_t count, const char *key)
{
    uint32_t i;
    for (i = 0; i < count; i++) {
        if (strcmp(p[i].key, key) == 0) {
            return p[i].values[0];
        }
    }
    return NULL;
}

static void test_avopt_tables(void)
{
    size_t count = 0;
    const PelAvoptEncoder *encs = pel_encode_avoptions_encoders(&count);
    size_t i;
    uint32_t j;

    CHECK(count == 16);
    for (i = 0; i < count; i++) {
        for (j = 1; j < encs[i].nb_names; j++) {
            CHECK(strcmp(encs[i].names[j - 1], encs[i].names[j]) < 0);
        }
        for (j = 0; j < encs[i].nb_names; j++) {
            CHECK(strlen(encs[i].names[j]) + 7 <= PEL_ENCODE_RECORD_MAX_KEY);
            CHECK(strchr(encs[i].names[j], ':') == NULL);
        }
        CHECK((encs[i].params_key == NULL) == ((int)encs[i].dialect == 0));
    }
}

/* Positive: one bitstream-affecting AVOption changes the digest, for an
 * encoder with an option string and for one without. */
static void test_avopt_one_option_changes_digest(void)
{
    const PelorusEncodeOption a[] = {{"preset", "medium"}, {"b", "4M"}};
    const PelorusEncodeOption b[] = {{"preset", "slow"}, {"b", "4M"}};
    const PelorusEncodeOption c[] = {{"cq", "20"}, {"rc", "vbr"}};
    const PelorusEncodeOption d[] = {{"cq", "21"}, {"rc", "vbr"}};
    char d1[72];
    char d2[72];

    CHECK(avopt_digest("libx264", a, 2, d1) == PEL_OK);
    CHECK(avopt_digest("libx264", b, 2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) != 0);
    CHECK(avopt_digest("hevc_nvenc", c, 2, d1) == PEL_OK);
    CHECK(avopt_digest("hevc_nvenc", d, 2, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) != 0);
}

/* Negative: host settings leave the digest unchanged, on every encoder. */
static void test_avopt_threads_ignored(void)
{
    const PelorusEncodeOption plain[] = {{"g", "120"}};
    const PelorusEncodeOption host[] = {{"threads", "8"},
                                        {"g", "120"},
                                        {"thread_type", "slice"},
                                        {"gpu", "1"},
                                        {"passlogfile", "/tmp/x"}};
    size_t count = 0;
    const PelAvoptEncoder *encs = pel_encode_avoptions_encoders(&count);
    char d1[72];
    char d2[72];
    size_t i;

    for (i = 0; i < count; i++) {
        CHECK(avopt_digest(encs[i].encoder, plain, 1, d1) == PEL_OK);
        CHECK(avopt_digest(encs[i].encoder, host, 5, d2) == PEL_OK);
        CHECK(strcmp(d1, d2) == 0);
    }
}

typedef struct AvoptClashCase {
    const char *encoder;
    const char *avoption;
    const char *params_key;
    const char *string;  /* option string with a valid value */
    const char *invalid; /* option string with a rejected value */
} AvoptClashCase;

/* Boundary: an AVOption and the option-string key the encoder applies after
 * it resolve to the string value; a rejected string value keeps the AVOption.
 */
static void test_avopt_clash_pairs(void)
{
    static const AvoptClashCase cases[] = {
        {"libx264", "crf", "x264-params", "crf=20", "crf=abc"},
        {"libx264", "qp", "x264-params", "qp=20", "qp=abc"},
        {"libx265", "crf", "x265-params", "crf=20", "crf=abc"},
        {"libx265", "qp", "x265-params", "qp=20", "qp=abc"},
        {"libsvtav1", "crf", "svtav1-params", "crf=20", "crf=abc"},
        {"libaom-av1", "cpu-used", "aom-params", "cpu-used=4", "cpu-used=-1"},
    };
    PelorusEncodeOptions *opts = alloc_opts();
    char key[32];
    char d1[72];
    char d2[72];
    size_t i;

    if (!opts) {
        return;
    }
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        const AvoptClashCase *c = &cases[i];
        const PelorusEncodeOption both[] = {{c->avoption, "30"}, {c->params_key, c->string}};
        const PelorusEncodeOption str[] = {{c->params_key, c->string}};
        const PelorusEncodeOption bad[] = {{c->avoption, "30"}, {c->params_key, c->invalid}};

        (void)snprintf(key, sizeof(key), "ffmpeg:%s", c->avoption);
        CHECK(avopt_digest(c->encoder, both, 2, d1) == PEL_OK);
        CHECK(avopt_digest(c->encoder, str, 1, d2) == PEL_OK);
        CHECK(strcmp(d1, d2) == 0);
        CHECK(pel_encode_options_from_ffmpeg(c->encoder, bad, 2, opts) == PEL_OK);
        CHECK(find_value(opts->params, opts->nb_params, key) != NULL);
        CHECK(find_value(opts->unknown_params, opts->nb_unknown_params, c->avoption) != NULL);
    }
    free(opts);
}

/* Boundary: a same-named AVOption outside the clash pairs stays in the record.
 */
static void test_avopt_unproven_pair_kept(void)
{
    const PelorusEncodeOption o[] = {{"aq-mode", "2"}, {"x264-params", "aq-mode=1"}};
    PelorusEncodeOptions *opts = alloc_opts();

    if (!opts) {
        return;
    }
    CHECK(pel_encode_options_from_ffmpeg("libx264", o, 2, opts) == PEL_OK);
    CHECK(opts->nb_params == 2);
    CHECK(strcmp(opts->params[0].key, "aq-mode") == 0);
    CHECK(strcmp(opts->params[0].values[0], "1") == 0);
    CHECK(strcmp(opts->params[1].key, "ffmpeg:aq-mode") == 0);
    CHECK(strcmp(opts->params[1].values[0], "2") == 0);
    free(opts);
}

static void test_avopt_known_unknown_verbatim(void)
{
    const PelorusEncodeOption o[] = {
        {"b", "4M"}, {"trellis", "2"}, {"future-option", "on"}, {"rc", "vbr"}, {"b", "5M"}};
    PelorusEncodeOptions *opts = alloc_opts();

    if (!opts) {
        return;
    }
    CHECK(pel_encode_options_from_ffmpeg("h264_nvenc", o, 5, opts) == PEL_OK);
    CHECK(opts->nb_params == 2);
    CHECK(strcmp(opts->params[0].key, "ffmpeg:b") == 0);
    CHECK(strcmp(opts->params[0].values[0], "5M") == 0);
    CHECK(strcmp(opts->params[1].key, "ffmpeg:rc") == 0);
    CHECK(opts->nb_unknown_params == 2);
    CHECK(strcmp(opts->unknown_params[0].key, "ffmpeg:future-option") == 0);
    /* nvenc does not read avctx->trellis */
    CHECK(strcmp(opts->unknown_params[1].key, "ffmpeg:trellis") == 0);
    free(opts);
}

static void test_avopt_rejects(void)
{
    const PelorusEncodeOption ok[] = {{"g", "60"}};
    const PelorusEncodeOption spec[] = {{"b:v", "4M"}};
    const PelorusEncodeOption empty_val[] = {{"g", ""}};
    const PelorusEncodeOption empty_key[] = {{"", "1"}};
    const PelorusEncodeOption null_val[] = {{"g", NULL}};
    const PelorusEncodeOption guard[] = {{"g", "60"}, {"x264-params", "ffmpeg\\:g=1"}};
    const PelorusEncodeOption bad_str[] = {{"g", "60"}, {"x264-params", "crf"}};
    PelorusEncodeOptions *opts = alloc_opts();

    if (!opts) {
        return;
    }
    CHECK(pel_encode_options_from_ffmpeg("libx264", ok, 1, NULL) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg(NULL, ok, 1, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg("libx264", NULL, 1, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg("libvpx-vp9", ok, 1, opts) == PEL_ERR_UNSUPPORTED);
    CHECK(pel_encode_options_from_ffmpeg("libx264", spec, 1, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg("libx264", empty_val, 1, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg("libx264", empty_key, 1, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg("libx264", null_val, 1, opts) == PEL_ERR_INVALID);
    CHECK(pel_encode_options_from_ffmpeg("libx264", guard, 2, opts) == PEL_ERR_INVALID);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 0 && opts->arena_used == 0);
    CHECK(pel_encode_options_from_ffmpeg("libx264", bad_str, 2, opts) == PEL_ERR_INVALID);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 0 && opts->arena_used == 0);
    free(opts);
}

static void test_avopt_boundaries(void)
{
    char name[PEL_ENCODE_RECORD_MAX_KEY + 2];
    char value[PEL_ENCODE_RECORD_MAX_VALUE + 2];
    PelorusEncodeOption o[1];
    char d1[72];
    char d2[72];
    const PelorusEncodeOption two_strings[] = {{"x264-params", "crf=18"},
                                               {"x264-params", "crf=20"}};
    PelorusEncodeOptions *opts = alloc_opts();

    if (!opts) {
        return;
    }
    CHECK(pel_encode_options_from_ffmpeg("libx264", NULL, 0, opts) == PEL_OK);
    CHECK(opts->nb_params == 0 && opts->nb_unknown_params == 0);

    (void)memset(name, 'n', sizeof(name));
    name[PEL_ENCODE_RECORD_MAX_KEY - 7] = '\0';
    o[0].key = name;
    o[0].value = "1";
    CHECK(pel_encode_options_from_ffmpeg("libx264", o, 1, opts) == PEL_OK);
    CHECK(opts->nb_unknown_params == 1);
    CHECK(strlen(opts->unknown_params[0].key) == PEL_ENCODE_RECORD_MAX_KEY);
    name[PEL_ENCODE_RECORD_MAX_KEY - 7] = 'n';
    name[PEL_ENCODE_RECORD_MAX_KEY - 6] = '\0';
    CHECK(pel_encode_options_from_ffmpeg("libx264", o, 1, opts) == PEL_ERR_RANGE);

    (void)memset(value, 'v', sizeof(value));
    value[PEL_ENCODE_RECORD_MAX_VALUE] = '\0';
    o[0].key = "x264opts";
    o[0].value = value;
    CHECK(pel_encode_options_from_ffmpeg("libx264", o, 1, opts) == PEL_OK);
    CHECK(opts->nb_params == 1);
    value[PEL_ENCODE_RECORD_MAX_VALUE] = 'v';
    value[PEL_ENCODE_RECORD_MAX_VALUE + 1] = '\0';
    CHECK(pel_encode_options_from_ffmpeg("libx264", o, 1, opts) == PEL_ERR_RANGE);

    /* FFmpeg's dictionary keeps the last option-string entry */
    CHECK(pel_encode_options_from_ffmpeg("libx264", two_strings, 2, opts) == PEL_OK);
    CHECK(opts->nb_params == 1);
    CHECK(avopt_digest("libx264", two_strings, 2, d1) == PEL_OK);
    CHECK(avopt_digest("libx264", &two_strings[1], 1, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) == 0);
    CHECK(avopt_digest("libx264", two_strings, 1, d2) == PEL_OK);
    CHECK(strcmp(d1, d2) != 0);
    free(opts);
}

int main(void)
{
    test_sizeof_options();
    test_ordering_and_sorting();
    test_normalisation_aliases();
    test_boolean_and_integer_spellings();
    test_negatives();
    test_boundary_keys_values();
    test_boundary_param_counts();
    test_negation_extended();
    test_unknown_key_normalised();
    test_unknown_spellings_last_wins();
    test_int_base_prefixes();
    test_quotes_and_whitespace();
    test_invalid_after_valid_dup();
    test_arena_exhaustion();
    test_257_known_params();
    test_x265_case_sensitive_bool();
    test_int_overflow_range();
    test_repeated_duplicates();
    test_table_sorting();
    test_x265_types();
    test_svtav1_ordering_and_types();
    test_svtav1_rejects();
    test_aom_dialect();
    test_x265_escaped_colon();
    test_one_value_changes_digest();
    test_dropped_key_fails_verify();
    test_roundtrip_boundaries();
    test_avopt_tables();
    test_avopt_one_option_changes_digest();
    test_avopt_threads_ignored();
    test_avopt_clash_pairs();
    test_avopt_unproven_pair_kept();
    test_avopt_known_unknown_verbatim();
    test_avopt_rejects();
    test_avopt_boundaries();

    if (g_fail > 0) {
        (void)fprintf(stderr, "encode_options_test: %d failure(s)\n", g_fail);
        return 1;
    }
    (void)printf("encode_options_test: all tests passed\n");
    return 0;
}
