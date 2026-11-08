/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef _WIN32
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700 /* NOLINT(bugprone-reserved-identifier,cert-dcl37-c,cert-dcl51-cpp) */
#endif
#endif

#include "host_pipeline.h"

#include "../test_path_safety.h"

#include <stdio.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

static int g_fail;

/* Record a test failure and write failure details to standard error. */
void hp_check_failed(const char *file, int line, const char *cond)
{
    (void)fprintf(stderr, "FAIL %s:%d: %s\n", file, line, cond);
    g_fail++;
}

/* Return the total count of failed checks across all integration test units. */
int hp_fail_count(void)
{
    return g_fail;
}

/* Read at most cap bytes from fp in bounded chunks; return the byte count read. */
static size_t read_chunks(FILE *fp, char *buf, size_t cap)
{
    size_t total = 0;

    for (size_t iters = 0; iters < HP_MAX_READ_ITERS && total < cap; iters++) {
        size_t to_read = cap - total;
        size_t n;
        if (to_read > HP_READ_CHUNK_SIZE) {
            to_read = HP_READ_CHUNK_SIZE;
        }
        n = fread(buf + total, 1u, to_read, fp);
        total += n;
        if (n < to_read || feof(fp) || ferror(fp)) {
            break;
        }
    }
    return total;
}

/* Read a whole fixture file into a caller-supplied buffer; fail if it does not fit. */
int hp_read_fixture(const char *path, char *buf, size_t cap, size_t *out_len)
{
    FILE *fp;

    if (path == NULL || buf == NULL || out_len == NULL || cap == 0u) {
        return -1;
    }
    *out_len = 0;
    fp = test_fopen_safe(path, "rb");
    if (fp == NULL) {
        return -1;
    }

    const size_t total = read_chunks(fp, buf, cap);
    if (ferror(fp) || !feof(fp)) {
        (void)fclose(fp);
        return -1;
    }
    (void)fclose(fp);
    *out_len = total;
    return 0;
}

/* NOLINTEND(modernize-use-nullptr) */
