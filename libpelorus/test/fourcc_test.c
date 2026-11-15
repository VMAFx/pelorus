/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * fourcc_test.c — PEL_FOURCC value, type and shift-count spelling (issue #582).
 *
 * Compile time: every producer_id fourcc a Pelorus stage writes keeps its wire
 * value, each byte lands in its position, and the result type is uint32_t.
 * Run time: every shift count in the macro's expansion is an unsigned integer
 * literal, so clang-tidy's bugprone-signed-bitwise finds no signed operand at an
 * expansion. The expansion text comes from the preprocessor (stringizing), so the
 * check runs on every compiler without clang-tidy. The checker first rejects a
 * planted signed spelling, so a checker that accepts everything fails too.
 * Exit non-zero on any failure.
 */

#include "pelorus/interop.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

/* Wire values (little-endian, first character in the low byte). */
_Static_assert(PEL_FOURCC('P', 'L', 'R', 'S') == 0x53524C50u, "PLRS: deband");
_Static_assert(PEL_FOURCC('P', 'L', 'R', 'A') == 0x41524C50u, "PLRA: analyze");
_Static_assert(PEL_FOURCC('P', 'L', 'R', 'D') == 0x44524C50u, "PLRD: denoise");
_Static_assert(PEL_FOURCC('P', 'L', 'R', 'G') == 0x47524C50u, "PLRG: grain estimate");
_Static_assert(PEL_FOURCC('P', 'L', 'M', 'C') == 0x434D4C50u, "PLMC: motion compensation");

/* Byte positions and the 0 / 0xFF bounds. */
_Static_assert(PEL_FOURCC(0, 0, 0, 0) == 0u, "all-zero fourcc");
_Static_assert(PEL_FOURCC(0x01, 0, 0, 0) == 0x00000001u, "byte a is bits 0-7");
_Static_assert(PEL_FOURCC(0, 0x01, 0, 0) == 0x00000100u, "byte b is bits 8-15");
_Static_assert(PEL_FOURCC(0, 0, 0x01, 0) == 0x00010000u, "byte c is bits 16-23");
_Static_assert(PEL_FOURCC(0, 0, 0, 0x01) == 0x01000000u, "byte d is bits 24-31");
_Static_assert(PEL_FOURCC(0xFF, 0xFF, 0xFF, 0xFF) == 0xFFFFFFFFu, "all-ones fourcc");

/* Result type: uint32_t, the producer_id field type. */
_Static_assert(_Generic(PEL_FOURCC('P', 'L', 'R', 'S'), uint32_t: 1, default: 0),
               "PEL_FOURCC yields uint32_t");

#define FOURCC_STR_(x) #x
#define FOURCC_STR(x) FOURCC_STR_(x)

/* The macro has three shifts; the text is a few dozen bytes. */
#define FOURCC_SHIFTS 3u
#define FOURCC_TEXT_CAP 512u

/* The macro's expansion with placeholder arguments, as the compiler sees it. */
static const char k_expansion[] = FOURCC_STR(PEL_FOURCC(a, b, c, d));

/* The pre-#582 spelling: plain int shift counts. The checker must reject it. */
static const char k_planted_signed[] =
    "((uint32_t)(a) | ((uint32_t)(b) << 8) | ((uint32_t)(c) << 16) | ((uint32_t)(d) << 24))";

static int is_digit(char ch)
{
    return ch >= '0' && ch <= '9';
}

static int is_ident(char ch)
{
    return is_digit(ch) || ch == '_' || (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

/* 1 when text[pos] starts an unsigned decimal literal (digits, then a u or U suffix,
 * then the end of the token); spaces before it are skipped. */
static int unsigned_literal_at(const char *text, size_t len, size_t pos)
{
    size_t digits = 0;

    while (pos < len && text[pos] == ' ') {
        pos++;
    }
    while (pos < len && is_digit(text[pos])) {
        pos++;
        digits++;
    }
    if (digits == 0u || pos >= len || (text[pos] != 'u' && text[pos] != 'U')) {
        return 0;
    }
    pos++;
    return pos >= len || !is_ident(text[pos]);
}

/* 1 when the len bytes of text hold exactly FOURCC_SHIFTS left shifts and each
 * count is an unsigned literal. The caller passes the length of a string literal
 * (sizeof - 1), so no read depends on a terminating NUL. */
static int shift_counts_unsigned(const char *text, size_t len)
{
    size_t shifts = 0;

    if (len > FOURCC_TEXT_CAP) {
        return 0;
    }
    for (size_t i = 0; i + 1u < len; i++) {
        if (text[i] != '<' || text[i + 1u] != '<') {
            continue;
        }
        shifts++;
        if (!unsigned_literal_at(text, len, i + 2u)) {
            return 0;
        }
    }
    return shifts == FOURCC_SHIFTS;
}

int main(void)
{
    int failed = 0;

    if (shift_counts_unsigned(k_planted_signed, sizeof(k_planted_signed) - 1u)) {
        (void)fprintf(stderr, "FAIL: checker accepted the planted signed spelling\n");
        failed = 1;
    }
    if (!shift_counts_unsigned(k_expansion, sizeof(k_expansion) - 1u)) {
        (void)fprintf(stderr, "FAIL: PEL_FOURCC shift count not an unsigned literal: %s\n",
                      k_expansion);
        failed = 1;
    }
    if (failed != 0) {
        return 1;
    }
    (void)printf("PASS: PEL_FOURCC values, type and unsigned shift counts\n");
    return 0;
}
