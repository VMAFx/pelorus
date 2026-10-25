/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_TEST_FLOAT_BITS_H
#define PELORUS_TEST_FLOAT_BITS_H

#include <stdint.h>
#include <string.h>

/*
 * Bit-pattern equality for floating-point values in test assertions.
 * Comparing integer representations via memcpy avoids CodeQL
 * cpp/equality-on-floats without loosening exact expected bit values.
 */
static inline int float_bits_equal(float a, float b)
{
    uint32_t a_bits = 0;
    uint32_t b_bits = 0;

    memcpy(&a_bits, &a, sizeof(a_bits));
    memcpy(&b_bits, &b, sizeof(b_bits));
    return a_bits == b_bits;
}

static inline int double_bits_equal(double a, double b)
{
    uint64_t a_bits = 0;
    uint64_t b_bits = 0;

    memcpy(&a_bits, &a, sizeof(a_bits));
    memcpy(&b_bits, &b, sizeof(b_bits));
    return a_bits == b_bits;
}

#endif /* PELORUS_TEST_FLOAT_BITS_H */
