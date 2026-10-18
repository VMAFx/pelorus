/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/pelorus.h"
#include "pelorus/quality.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>
#endif

#define MAX_INPUT_LINES ((size_t)PEL_SHOT_TARGET_MAX * 4u)

enum zone_format { FMT_NONE = 0, FMT_ZONEFILE, FMT_ZONES, FMT_SEGMENT_PLAN };

static int is_exact_header(const char *line)
{
    static const char hdr[] = "first_frame,last_frame,crf";
    size_t len = sizeof(hdr) - 1u;
    const char *p;
    if (strncmp(line, hdr, len) != 0) {
        return 0;
    }
    p = line + len;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    return (*p == '\0' || *p == '\r' || *p == '\n');
}

static int parse_u32(const char *str, char **endptr, uint32_t *out)
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

static int parse_crf(const char *str, char **endptr, float *out)
{
    double val;
    const char *p;
    errno = 0;
    val = strtod(str, endptr);
    if (errno == ERANGE || *endptr == str || !isfinite(val) || val < 0.0 || val > 63.0) {
        return -1;
    }
    p = *endptr;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p != '\0' && *p != '\r' && *p != '\n') {
        return -1;
    }
    *out = (float)val;
    return 0;
}

static int parse_line(const char *line, size_t line_num, PelShotTarget *shot)
{
    const char *p = line;
    char *end = NULL;
    uint32_t first = 0;
    uint32_t last = 0;
    float crf = 0.0f;

    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p == '#') {
        return 0;
    }
    if (is_exact_header(p)) {
        return 0;
    }
    if (*p < '0' || *p > '9') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: non-numeric line: %s", line_num, line);
        return -1;
    }

    if (parse_u32(p, &end, &first) != 0 || *end != ',') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: malformed first_frame: %s", line_num, line);
        return -1;
    }
    p = end + 1;
    if (*p < '0' || *p > '9') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: non-numeric last_frame: %s", line_num,
                      line);
        return -1;
    }
    if (parse_u32(p, &end, &last) != 0 || *end != ',') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: malformed last_frame: %s", line_num, line);
        return -1;
    }
    p = end + 1;
    if (parse_crf(p, &end, &crf) != 0) {
        (void)fprintf(stderr, "pelorus_zones: line %zu: malformed crf: %s", line_num, line);
        return -1;
    }

    shot->first_frame = first;
    shot->last_frame = last;
    shot->crf = crf;
    return 1;
}

static int read_shots_stdin(PelShotTarget *shots, size_t max_shots, size_t *out_n)
{
    char line[256];
    size_t count = 0;
    size_t line_num = 0;
    size_t iter = 0;

    while (iter < MAX_INPUT_LINES && fgets(line, sizeof(line), stdin) != NULL) {
        size_t slen = strlen(line);
        int has_newline = (slen > 0 && line[slen - 1] == '\n');
        PelShotTarget temp_shot;
        int rc;

        iter++;
        line_num++;

        if (!has_newline && !feof(stdin)) {
            (void)fprintf(stderr, "pelorus_zones: line %zu: line exceeds buffer limit\n", line_num);
            return -1;
        }

        rc = parse_line(line, line_num, &temp_shot);
        if (rc < 0) {
            return -1;
        }
        if (rc > 0) {
            if (count >= max_shots) {
                (void)fprintf(stderr, "pelorus_zones: line %zu: exceeded maximum shots (%u)\n",
                              line_num, (unsigned)max_shots);
                return -1;
            }
            shots[count] = temp_shot;
            count++;
        }
    }

    if (ferror(stdin)) {
        (void)fprintf(stderr, "pelorus_zones: error reading stdin\n");
        return -1;
    }
    if (iter >= MAX_INPUT_LINES && !feof(stdin)) {
        (void)fprintf(stderr, "pelorus_zones: input exceeded maximum line limit (%u)\n",
                      (unsigned)MAX_INPUT_LINES);
        return -1;
    }

    *out_n = count;
    return 0;
}

static int emit_output(enum zone_format fmt, const PelShotTarget *shots, size_t n, float base_crf)
{
    size_t need = 0;
    char *buf = NULL;
    pel_result res = PEL_OK;

    if (fmt == FMT_ZONEFILE) {
        res = pel_zones_x265_zonefile(shots, n, NULL, 0, &need);
    } else if (fmt == FMT_ZONES) {
        res = pel_zones_x265_param(shots, n, base_crf, NULL, 0, &need);
    } else if (fmt == FMT_SEGMENT_PLAN) {
        res = pel_zones_segment_plan(shots, n, NULL, 0, &need);
    }
    if (res != PEL_OK) {
        (void)fprintf(stderr, "pelorus_zones: format query failed: %s\n", pel_result_str(res));
        return EXIT_FAILURE;
    }

    buf = (char *)malloc(need);
    if (buf == NULL) {
        (void)fprintf(stderr, "pelorus_zones: out of memory\n");
        return EXIT_FAILURE;
    }

    if (fmt == FMT_ZONEFILE) {
        res = pel_zones_x265_zonefile(shots, n, buf, need, &need);
    } else if (fmt == FMT_ZONES) {
        res = pel_zones_x265_param(shots, n, base_crf, buf, need, &need);
    } else if (fmt == FMT_SEGMENT_PLAN) {
        res = pel_zones_segment_plan(shots, n, buf, need, &need);
    }
    if (res != PEL_OK) {
        (void)fprintf(stderr, "pelorus_zones: format failed: %s\n", pel_result_str(res));
        free(buf);
        return EXIT_FAILURE;
    }

    if (fputs(buf, stdout) == EOF) {
        (void)fprintf(stderr, "pelorus_zones: stdout write error\n");
        free(buf);
        return EXIT_FAILURE;
    }
    if (fmt == FMT_ZONES) {
        if (fputc('\n', stdout) == EOF) {
            (void)fprintf(stderr, "pelorus_zones: stdout write error\n");
            free(buf);
            return EXIT_FAILURE;
        }
    }
    free(buf);
    if (fflush(stdout) != 0 || ferror(stdout)) {
        (void)fprintf(stderr, "pelorus_zones: stdout flush error\n");
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int run(int argc, char **argv)
{
    enum zone_format fmt = FMT_NONE;
    float base_crf = 23.0f;
    PelShotTarget shots[PEL_SHOT_TARGET_MAX];
    size_t n_shots = 0;
    int i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--format") == 0 && i + 1 < argc) {
            i++;
            if (strcmp(argv[i], "zonefile") == 0) {
                fmt = FMT_ZONEFILE;
            } else if (strcmp(argv[i], "zones") == 0) {
                fmt = FMT_ZONES;
            } else if (strcmp(argv[i], "segment-plan") == 0) {
                fmt = FMT_SEGMENT_PLAN;
            } else {
                (void)fprintf(stderr, "pelorus_zones: unknown format: %s\n", argv[i]);
                return EXIT_FAILURE;
            }
        } else if (strcmp(argv[i], "--base-crf") == 0 && i + 1 < argc) {
            char *end = NULL;
            i++;
            if (parse_crf(argv[i], &end, &base_crf) != 0) {
                (void)fprintf(stderr, "pelorus_zones: invalid base-crf: %s\n", argv[i]);
                return EXIT_FAILURE;
            }
        } else {
            (void)fprintf(stderr, "Usage: pelorus_zones --format <zonefile|zones|segment-plan> "
                                  "[--base-crf <float>]\n");
            return EXIT_FAILURE;
        }
    }
    if (fmt == FMT_NONE) {
        (void)fprintf(stderr, "pelorus_zones: --format is required\n");
        return EXIT_FAILURE;
    }

    if (read_shots_stdin(shots, PEL_SHOT_TARGET_MAX, &n_shots) != 0 || n_shots == 0) {
        (void)fprintf(stderr, "pelorus_zones: no valid shots read on stdin\n");
        return EXIT_FAILURE;
    }

    return emit_output(fmt, shots, n_shots, base_crf);
}

#ifdef _WIN32
static void free_argv(char **argv, int argc)
{
    int i;
    if (argv == NULL) {
        return;
    }
    for (i = 0; i < argc; i++) {
        free(argv[i]);
    }
    free((void *)argv);
}

static char **utf8_argv(int *argc)
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
            free_argv(argv, i + 1);
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

int main(int argc, char **argv)
{
#ifdef _WIN32
    char **utf8 = utf8_argv(&argc);
    int rc;

    (void)argv;
    if (utf8 == NULL) {
        (void)fprintf(stderr, "pelorus_zones: cannot read command line as UTF-8\n");
        return EXIT_FAILURE;
    }
    rc = run(argc, utf8);
    free_argv(utf8, argc);
    return rc;
#else
    return run(argc, argv);
#endif
}
