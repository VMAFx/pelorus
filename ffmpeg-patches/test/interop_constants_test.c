/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Unit tests for interop constant equality between libpelorus/include/pelorus/interop.h
 * and ffmpeg-patches/files/pelorus_sei_fit.h (issue #500).
 */

#ifndef _WIN32
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700
#endif
#endif

#include <stdio.h>
#include <string.h>

#include "pelorus/interop.h"
#include "pelorus_sei_fit.h"

_Static_assert(PEL_SEI_MAGIC_LEN == PELORUS_MAGIC_LEN, "PEL_SEI_MAGIC_LEN mirror");
_Static_assert(PEL_SEI_ABI_MAJOR == PELORUS_ABI_MAJOR, "PEL_SEI_ABI_MAJOR mirror");
_Static_assert(PEL_SEI_HDR_BYTES == sizeof(PelorusSideData), "PEL_SEI_HDR_BYTES mirror");
_Static_assert(sizeof(PEL_SEI_MAGIC) == sizeof(PELORUS_MAGIC_STR), "magic string length mirror");

int main(void)
{
    if (memcmp(PEL_SEI_MAGIC, PELORUS_MAGIC_STR, PELORUS_MAGIC_LEN) != 0) {
        (void)fprintf(stderr, "FAIL: PEL_SEI_MAGIC mismatch\n");
        return 1;
    }
    (void)printf("PASS: interop constants mirror verified\n");
    return 0;
}
