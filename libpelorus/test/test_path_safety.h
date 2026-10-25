/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_TEST_PATH_SAFETY_H
#define PELORUS_TEST_PATH_SAFETY_H

#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

/*
 * Opens a fixture path after resolving it, then refuses the handle unless it
 * is a regular file. The type check runs on the open handle, so nothing can
 * swap the file between check and use.
 * Callers define _XOPEN_SOURCE before their first include (realpath).
 */
static inline FILE *test_fopen_safe(const char *path, const char *mode)
{
    char resolved[4096];
    FILE *fp;

    if (path == NULL || mode == NULL) {
        return NULL;
    }
#ifdef _WIN32
    struct _stat st;

    if (_fullpath(resolved, path, sizeof(resolved)) == NULL) {
        return NULL;
    }
    fp = fopen(resolved, mode);
    if (fp != NULL && (_fstat(_fileno(fp), &st) != 0 || (st.st_mode & _S_IFMT) != _S_IFREG)) {
        (void)fclose(fp);
        fp = NULL;
    }
#else
    struct stat st;

    if (realpath(path, resolved) == NULL) {
        return NULL;
    }
    fp = fopen(resolved, mode);
    if (fp != NULL && (fstat(fileno(fp), &st) != 0 || !S_ISREG(st.st_mode))) {
        (void)fclose(fp);
        fp = NULL;
    }
#endif
    return fp;
}

#endif /* PELORUS_TEST_PATH_SAFETY_H */
