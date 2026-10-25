/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus_tool_common.h"

#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>

static void tool_free_argv(char **argv, int argc)
{
    int i;

    if (argv == NULL) {
        return;
    }
    for (i = 0; i < argc; i++) {
        free(argv[i]);
    }
    free((void *)argv); /* explicit: bugprone-multi-level-implicit-pointer-conversion */
}

/* Windows hands main() its arguments in the ANSI code page, but libpelorus
 * paths are UTF-8 (ADR-0149): rebuild argv as UTF-8 from the UTF-16 command
 * line, so non-ASCII paths reach tools intact. Returns NULL on failure
 * (including an argument with an unpaired surrogate). */
static char **tool_utf8_argv(int *argc)
{
    int wargc = 0;
    wchar_t **wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
    char **argv = NULL;
    int i;

    if (wargv != NULL && wargc > 0) {
        argv = (char **)calloc((size_t)wargc + 1u, sizeof(*argv));
    }
    for (i = 0; argv != NULL && i < wargc; i++) {
        const int n =
            WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wargv[i], -1, NULL, 0, NULL, NULL);

        argv[i] = (n > 0) ? malloc((size_t)n) : NULL;
        if (argv[i] == NULL || WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wargv[i], -1,
                                                   argv[i], n, NULL, NULL) != n) {
            tool_free_argv(argv, i + 1);
            argv = NULL;
        }
    }
    if (wargv != NULL) {
        (void)LocalFree((HLOCAL)wargv);
    }
    if (argv != NULL) {
        *argc = wargc;
    }
    return argv;
}
#endif

int pelorus_tool_parse_u32(const char *str, char **endptr, uint32_t *out)
{
    unsigned long long val;

    errno = 0;
    val = strtoull(str, endptr, 10);
    if (errno == ERANGE || *endptr == str || val > UINT32_MAX) {
        return -1;
    }
    *out = (uint32_t)val;
    return 0;
}

int pelorus_tool_read_line(FILE *in, char *buf, size_t cap, size_t *line_num, const char *tool_name)
{
    size_t slen;
    int has_newline;

    if (in == NULL || buf == NULL || cap == 0u || line_num == NULL) {
        return -1;
    }
    if (cap > (size_t)INT_MAX) {
        return -1;
    }
    if (fgets(buf, (int)cap, in) == NULL) {
        if (ferror(in)) {
            (void)fprintf(stderr, "%s: error reading stdin\n", tool_name);
            return -1;
        }
        return 0;
    }
    (*line_num)++;
    slen = strlen(buf);
    has_newline = (slen > 0 && buf[slen - 1] == '\n');
    if (!has_newline && !feof(in)) {
        (void)fprintf(stderr, "%s: line %zu: line exceeds buffer limit\n", tool_name, *line_num);
        return -1;
    }
    return 1;
}

int pelorus_tool_main(int argc, char **argv, const char *err_msg, pel_tool_run_fn run_fn)
{
#ifdef _WIN32
    char **utf8 = tool_utf8_argv(&argc);
    int rc;

    (void)argv;
    if (utf8 == NULL) {
        (void)fprintf(stderr, "%s\n", err_msg);
        return EXIT_FAILURE;
    }
    rc = run_fn(argc, utf8);
    tool_free_argv(utf8, argc);
    return rc;
#else
    (void)err_msg;
    return run_fn(argc, argv);
#endif
}
