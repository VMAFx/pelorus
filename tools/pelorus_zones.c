/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/pelorus.h"
#include "pelorus/quality.h"
#include "pelorus_tool_common.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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

    if (pelorus_tool_parse_u32(p, &end, &first) != 0 || *end != ',') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: malformed first_frame: %s", line_num, line);
        return -1;
    }
    p = end + 1;
    if (*p < '0' || *p > '9') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: non-numeric last_frame: %s", line_num,
                      line);
        return -1;
    }
    if (pelorus_tool_parse_u32(p, &end, &last) != 0 || *end != ',') {
        (void)fprintf(stderr, "pelorus_zones: line %zu: malformed last_frame: %s", line_num, line);
        return -1;
    }
    p = end + 1;
    if (pelorus_tool_parse_crf(p, &end, &crf) != 0) {
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
    int read_rc = 0;

    while (iter < MAX_INPUT_LINES &&
           (read_rc = pelorus_tool_read_line(stdin, line, sizeof(line), &line_num,
                                             "pelorus_zones")) == 1) {
        PelShotTarget temp_shot;
        int rc;

        iter++;

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

    if (read_rc < 0) {
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
    } else {
        return EXIT_FAILURE;
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
    } else {
        free(buf);
        return EXIT_FAILURE;
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
    int fmt_val = FMT_NONE;
    float base_crf = 23.0f;
    PelShotTarget shots[PEL_SHOT_TARGET_MAX];
    size_t n_shots = 0;
    static const char *const s_formats[] = {"zonefile", "zones", "segment-plan", NULL};
    static const int s_format_vals[] = {FMT_ZONEFILE, FMT_ZONES, FMT_SEGMENT_PLAN};
    const PelorusOpt opts[] = {
        {"--format", PELORUS_OPT_CHOICE, &fmt_val, 1, s_formats, s_format_vals},
        {"--base-crf", PELORUS_OPT_FLOAT, &base_crf, 0, NULL, NULL},
    };

    if (pelorus_tool_parse_args("pelorus_zones", argc, argv, opts, sizeof(opts) / sizeof(opts[0]),
                                NULL) != 0) {
        (void)fprintf(stderr, "Usage: pelorus_zones --format <zonefile|zones|segment-plan> "
                              "[--base-crf <float>]\n");
        return EXIT_FAILURE;
    }
    if (base_crf < 0.0f || base_crf > 63.0f) {
        (void)fprintf(stderr, "pelorus_zones: invalid base-crf: %g\n", (double)base_crf);
        return EXIT_FAILURE;
    }
    fmt = (enum zone_format)fmt_val;

    if (read_shots_stdin(shots, PEL_SHOT_TARGET_MAX, &n_shots) != 0 || n_shots == 0) {
        (void)fprintf(stderr, "pelorus_zones: no valid shots read on stdin\n");
        return EXIT_FAILURE;
    }

    return emit_output(fmt, shots, n_shots, base_crf);
}

int main(int argc, char **argv)
{
    return pelorus_tool_main(argc, argv, "pelorus_zones: cannot read command line as UTF-8", run);
}
