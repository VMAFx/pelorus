/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus_tool_common.h"

#include <errno.h>
#include <limits.h>
#include <math.h>
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

/* Parse a decimal uint32_t from str into out, updating endptr if non-NULL.
 * Returns 0 on success, or -1 on invalid input, negative values, or overflow past UINT32_MAX. */
int pelorus_tool_parse_u32(const char *str, char **endptr, uint32_t *out)
{
    unsigned long long val;
    char *end = NULL;
    const char *p;

    if (str == NULL || out == NULL) {
        return -1;
    }
    p = str;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p == '-') {
        return -1;
    }
    errno = 0;
    val = strtoull(str, &end, 10);
    if (errno == ERANGE || end == str || val > UINT32_MAX) {
        return -1;
    }
    if (endptr != NULL) {
        *endptr = end;
    }
    *out = (uint32_t)val;
    return 0;
}

/* Parse a decimal int32_t from str into out, updating endptr if non-NULL.
 * Returns 0 on success, or -1 on invalid input, missing digits, or range overflow. */
int pelorus_tool_parse_i32(const char *str, char **endptr, int32_t *out)
{
    long long val;
    char *end = NULL;

    if (str == NULL || out == NULL) {
        return -1;
    }
    errno = 0;
    val = strtoll(str, &end, 10);
    if (errno == ERANGE || end == str || val < INT32_MIN || val > INT32_MAX) {
        return -1;
    }
    if (endptr != NULL) {
        *endptr = end;
    }
    *out = (int32_t)val;
    return 0;
}

/* Parse a finite double precision floating-point number from str into out, updating endptr if non-NULL.
 * Returns 0 on success, or -1 on invalid input, range error, or non-finite values. */
int pelorus_tool_parse_double(const char *str, char **endptr, double *out)
{
    double val;
    char *end = NULL;

    if (str == NULL || out == NULL) {
        return -1;
    }
    errno = 0;
    val = strtod(str, &end);
    if (errno == ERANGE || end == str || !isfinite(val)) {
        return -1;
    }
    if (endptr != NULL) {
        *endptr = end;
    }
    *out = val;
    return 0;
}

/* Parse a finite single-precision float from str into out, updating endptr if non-NULL.
 * Returns 0 on success, or -1 on invalid input, range error, or non-finite values. */
int pelorus_tool_parse_float(const char *str, char **endptr, float *out)
{
    float val;
    char *end = NULL;

    if (str == NULL || out == NULL) {
        return -1;
    }
    errno = 0;
    val = strtof(str, &end);
    if (errno == ERANGE || end == str || !isfinite(val)) {
        return -1;
    }
    if (endptr != NULL) {
        *endptr = end;
    }
    *out = val;
    return 0;
}

/* Parse a CRF value in the range [0.0, 63.0] from str into out, ensuring no trailing non-whitespace characters.
 * Returns 0 on success, or -1 on invalid format, range violation, or trailing garbage. */
int pelorus_tool_parse_crf(const char *str, char **endptr, float *out)
{
    float val = 0.0f;
    char *end = NULL;
    const char *p;

    if (pelorus_tool_parse_float(str, &end, &val) != 0 || val < 0.0f || val > 63.0f) {
        return -1;
    }
    p = end;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p != '\0' && *p != '\r' && *p != '\n') {
        return -1;
    }
    if (endptr != NULL) {
        *endptr = end;
    }
    *out = val;
    return 0;
}

/* Search opts for an option matching name arg and record the matching index in out_idx.
 * Returns 0 on a match or -1 if no matching option name is found. */
static int tool_match_opt(const PelorusOpt *opts, size_t n_opts, const char *arg, size_t *out_idx)
{
    size_t i;

    for (i = 0; i < n_opts && i < PELORUS_TOOL_MAX_OPTS; i++) {
        if (opts[i].name != NULL && strcmp(arg, opts[i].name) == 0) {
            *out_idx = i;
            return 0;
        }
    }
    return -1;
}

/* Match string val against the choices allowed by opt and write the choice integer to its destination.
 * Returns 0 on match or -1 if val is not in the allowed choices list. */
static int tool_parse_choice(const PelorusOpt *opt, const char *val)
{
    size_t c;

    if (opt->choices == NULL) {
        return -1;
    }
    for (c = 0; c < PELORUS_TOOL_MAX_CHOICES && opt->choices[c] != NULL; c++) {
        if (strcmp(val, opt->choices[c]) == 0) {
            if (opt->dest != NULL) {
                int out_val = (opt->choice_values != NULL) ? opt->choice_values[c] : (int)c;
                *(int *)opt->dest = out_val;
            }
            return 0;
        }
    }
    return -1;
}

/* Parse a double from string val without trailing characters and store it in dest if non-NULL.
 * Returns 0 on success or -1 on conversion failure or trailing characters. */
static int tool_store_double(void *dest, const char *val)
{
    char *end = NULL;
    double d = 0.0;

    if (pelorus_tool_parse_double(val, &end, &d) != 0 || *end != '\0') {
        return -1;
    }
    if (dest != NULL) {
        *(double *)dest = d;
    }
    return 0;
}

/* Parse a float from string val without trailing characters and store it in dest if non-NULL.
 * Returns 0 on success or -1 on conversion failure or trailing characters. */
static int tool_store_float(void *dest, const char *val)
{
    char *end = NULL;
    float f = 0.0f;

    if (pelorus_tool_parse_float(val, &end, &f) != 0 || *end != '\0') {
        return -1;
    }
    if (dest != NULL) {
        *(float *)dest = f;
    }
    return 0;
}

/* Parse a uint32_t from string val without trailing characters and store it in dest if non-NULL.
 * Returns 0 on success or -1 on conversion failure or trailing characters. */
static int tool_store_u32(void *dest, const char *val)
{
    char *end = NULL;
    uint32_t u = 0;

    if (pelorus_tool_parse_u32(val, &end, &u) != 0 || *end != '\0') {
        return -1;
    }
    if (dest != NULL) {
        *(uint32_t *)dest = u;
    }
    return 0;
}

/* Parse an int32_t from string val without trailing characters and store it in dest if non-NULL.
 * Returns 0 on success or -1 on conversion failure or trailing characters. */
static int tool_store_i32(void *dest, const char *val)
{
    char *end = NULL;
    int32_t i32 = 0;

    if (pelorus_tool_parse_i32(val, &end, &i32) != 0 || *end != '\0') {
        return -1;
    }
    if (dest != NULL) {
        *(int32_t *)dest = i32;
    }
    return 0;
}

/* Parse value string val according to opt->kind and store the parsed result into opt->dest.
 * Returns 0 on success or -1 on conversion failure, empty non-string value, or unsupported type. */
static int tool_parse_val(const PelorusOpt *opt, const char *val)
{
    if (val == NULL) {
        return -1;
    }
    if (opt->kind != PELORUS_OPT_STRING && val[0] == '\0') {
        return -1;
    }
    switch (opt->kind) {
    case PELORUS_OPT_DOUBLE:
        return tool_store_double(opt->dest, val);
    case PELORUS_OPT_FLOAT:
        return tool_store_float(opt->dest, val);
    case PELORUS_OPT_U32:
        return tool_store_u32(opt->dest, val);
    case PELORUS_OPT_I32:
        return tool_store_i32(opt->dest, val);
    case PELORUS_OPT_CHOICE:
        return tool_parse_choice(opt, val);
    case PELORUS_OPT_STRING:
        if (opt->dest != NULL) {
            *(const char **)opt->dest = val;
        }
        return 0;
    default:
        return -1;
    }
}

static int tool_process_opt(const char *tool_name, int argc, char **argv, int *i,
                            const PelorusOpt *opts, size_t n_opts, uint8_t *seen)
{
    const char *arg = argv[*i];
    size_t idx = 0;

    if (tool_match_opt(opts, n_opts, arg, &idx) != 0) {
        (void)fprintf(stderr, "%s: unknown option '%s'\n", tool_name, arg);
        return -1;
    }
    if (seen[idx]) {
        (void)fprintf(stderr, "%s: duplicate option '%s'\n", tool_name, arg);
        return -1;
    }
    if (*i + 1 >= argc) {
        (void)fprintf(stderr, "%s: option '%s' requires a value\n", tool_name, arg);
        return -1;
    }
    (*i)++;
    if (tool_parse_val(&opts[idx], argv[*i]) != 0) {
        (void)fprintf(stderr, "%s: invalid value for '%s': '%s'\n", tool_name, arg, argv[*i]);
        return -1;
    }
    seen[idx] = 1;
    return 0;
}

static int tool_process_pos(const char *tool_name, const char *arg, const PelorusPositional *pos,
                            int *pos_seen)
{
    if (pos == NULL || *pos_seen) {
        (void)fprintf(stderr, "%s: unexpected argument '%s'\n", tool_name, arg);
        return -1;
    }
    if (pos->dest != NULL) {
        *pos->dest = arg;
    }
    *pos_seen = 1;
    return 0;
}

static int tool_check_required(const char *tool_name, const PelorusOpt *opts, size_t n_opts,
                               const uint8_t *seen, const PelorusPositional *pos, int pos_seen)
{
    size_t j;

    for (j = 0; j < n_opts && j < PELORUS_TOOL_MAX_OPTS; j++) {
        if (opts[j].required && !seen[j]) {
            (void)fprintf(stderr, "%s: %s is required\n", tool_name, opts[j].name);
            return -1;
        }
    }
    if (pos != NULL && pos->required && !pos_seen) {
        (void)fprintf(stderr, "%s: %s is required\n", tool_name, pos->name);
        return -1;
    }
    return 0;
}

int pelorus_tool_parse_args(const char *tool_name, int argc, char **argv, const PelorusOpt *opts,
                            size_t n_opts, const PelorusPositional *pos)
{
    uint8_t seen[PELORUS_TOOL_MAX_OPTS];
    int pos_seen = 0;
    int i = 1;
    size_t step;

    if (tool_name == NULL || argc < 1 || argv == NULL) {
        return -1;
    }
    if (n_opts > PELORUS_TOOL_MAX_OPTS || (n_opts > 0 && opts == NULL)) {
        return -1;
    }
    memset(seen, 0, sizeof(seen));
    for (step = 0; step < PELORUS_TOOL_MAX_ARGS && i < argc; step++) {
        const char *arg = argv[i];
        int rc = (arg[0] == '-' && arg[1] != '\0') ?
                     tool_process_opt(tool_name, argc, argv, &i, opts, n_opts, seen) :
                     tool_process_pos(tool_name, arg, pos, &pos_seen);
        if (rc != 0) {
            return -1;
        }
        i++;
    }
    if (i < argc) {
        (void)fprintf(stderr, "%s: argument list exceeds limit\n", tool_name);
        return -1;
    }
    return tool_check_required(tool_name, opts, n_opts, seen, pos, pos_seen);
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
