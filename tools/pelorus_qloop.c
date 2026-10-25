/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/pelorus.h"
#include "pelorus/qloop.h"
#include "pelorus_tool_common.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_INPUT_LINES (PEL_QLOOP_MAX_ITER + 2u)

static int parse_double_strict(const char *str, char **endptr, double *out)
{
    double val;
    errno = 0;
    val = strtod(str, endptr);
    if (errno == ERANGE || *endptr == str || !isfinite(val)) {
        return -1;
    }
    *out = val;
    return 0;
}

static int parse_i32_strict(const char *str, char **endptr, int32_t *out)
{
    long long val;
    errno = 0;
    val = strtoll(str, endptr, 10);
    if (errno == ERANGE || *endptr == str || val < INT32_MIN || val > INT32_MAX) {
        return -1;
    }
    *out = (int32_t)val;
    return 0;
}

static int parse_history_line(const char *line, size_t line_num, PelQloopObs *obs)
{
    const char *p = line;
    char *end = NULL;
    double knob = 0.0;
    double score = 0.0;

    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p == '#' || *p == '\0' || *p == '\r' || *p == '\n') {
        return 0;
    }
    if (parse_double_strict(p, &end, &knob) != 0) {
        (void)fprintf(stderr, "pelorus_qloop: line %zu: invalid knob value\n", line_num);
        return -1;
    }
    p = end;
    if (*p != ' ' && *p != '\t') {
        (void)fprintf(stderr, "pelorus_qloop: line %zu: missing delimiter between knob and score\n",
                      line_num);
        return -1;
    }
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (parse_double_strict(p, &end, &score) != 0) {
        (void)fprintf(stderr, "pelorus_qloop: line %zu: invalid score value\n", line_num);
        return -1;
    }
    p = end;
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p != '\0' && *p != '\r' && *p != '\n') {
        (void)fprintf(stderr, "pelorus_qloop: line %zu: trailing garbage after score\n", line_num);
        return -1;
    }
    obs->knob = knob;
    obs->score = score;
    return 1;
}

static int read_history_stdin(PelQloopObs *hist, size_t max_obs, size_t *out_n)
{
    size_t count = 0;
    size_t line_no = 0;
    char line[256];

    for (size_t iter = 0; iter < MAX_INPUT_LINES; iter++) {
        PelQloopObs obs;
        int rc = pelorus_tool_read_line(stdin, line, sizeof(line), &line_no, "pelorus_qloop");

        if (rc == 0) {
            break;
        }
        if (rc < 0) {
            return -1;
        }
        rc = parse_history_line(line, line_no, &obs);
        if (rc < 0) {
            return -1;
        }
        if (rc > 0) {
            if (count >= max_obs) {
                (void)fprintf(stderr, "pelorus_qloop: exceeded maximum history entries (%u)\n",
                              (unsigned)max_obs);
                return -1;
            }
            hist[count] = obs;
            count++;
        }
    }

    *out_n = count;
    return 0;
}

static int format_knob_4f(char *buf, size_t cap, double val)
{
    int sign = (val < 0.0) ? -1 : 1;
    double abs_val = fabs(val);
    uint64_t units_10000 = (uint64_t)floor(abs_val * 10000.0 + 0.5);
    uint64_t whole = units_10000 / 10000u;
    uint64_t frac = units_10000 % 10000u;
    if (sign < 0 && units_10000 > 0) {
        return snprintf(buf, cap, "-%llu.%04llu", (unsigned long long)whole,
                        (unsigned long long)frac);
    }
    return snprintf(buf, cap, "%llu.%04llu", (unsigned long long)whole, (unsigned long long)frac);
}

static int emit_step(const PelQloopStep *step)
{
    char kbuf[64];
    int n;

    if (step->state == (uint32_t)PEL_QLOOP_UNREACHABLE) {
        if (fputs("unreachable\n", stdout) == EOF) {
            (void)fprintf(stderr, "pelorus_qloop: stdout write error\n");
            return EXIT_FAILURE;
        }
    } else {
        const char *label = "probe";
        if (step->state == (uint32_t)PEL_QLOOP_DONE) {
            label = "done";
        } else if (step->state == (uint32_t)PEL_QLOOP_EXHAUSTED) {
            label = "exhausted";
        }
        n = format_knob_4f(kbuf, sizeof(kbuf), step->knob);
        if (n < 0 || (size_t)n >= sizeof(kbuf)) {
            (void)fprintf(stderr, "pelorus_qloop: knob format overflow\n");
            return EXIT_FAILURE;
        }
        if (fprintf(stdout, "%s %s\n", label, kbuf) < 0) {
            (void)fprintf(stderr, "pelorus_qloop: stdout write error\n");
            return EXIT_FAILURE;
        }
    }

    if (fflush(stdout) != 0 || ferror(stdout)) {
        (void)fprintf(stderr, "pelorus_qloop: stdout flush error\n");
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}

static int parse_arg_item(const char *key, const char *val, PelQloopConfig *cfg, unsigned int *mask)
{
    char *end = NULL;

    if (strcmp(key, "--target") == 0) {
        if (parse_double_strict(val, &end, &cfg->target) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 0u;
        return 0;
    }
    if (strcmp(key, "--tolerance") == 0) {
        if (parse_double_strict(val, &end, &cfg->tolerance) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 1u;
        return 0;
    }
    if (strcmp(key, "--min") == 0) {
        if (parse_double_strict(val, &end, &cfg->knob_min) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 2u;
        return 0;
    }
    if (strcmp(key, "--max") == 0) {
        if (parse_double_strict(val, &end, &cfg->knob_max) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 3u;
        return 0;
    }
    if (strcmp(key, "--start") == 0) {
        if (parse_double_strict(val, &end, &cfg->knob_start) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 4u;
        return 0;
    }
    if (strcmp(key, "--resolution") == 0) {
        if (parse_double_strict(val, &end, &cfg->resolution) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 5u;
        return 0;
    }
    if (strcmp(key, "--max-iter") == 0) {
        if (pelorus_tool_parse_u32(val, &end, &cfg->max_iter) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 6u;
        return 0;
    }
    if (strcmp(key, "--direction") == 0) {
        if (parse_i32_strict(val, &end, &cfg->direction) != 0 || *end != '\0')
            return -1;
        *mask |= 1u << 7u;
        return 0;
    }
    return -1;
}

static int parse_cli_args(int argc, char **argv, PelQloopConfig *cfg)
{
    unsigned int mask = 0u;
    int i;

    memset(cfg, 0, sizeof(*cfg));
    i = 1;
    while (i < argc) {
        if (i + 1 >= argc)
            return -1;
        if (parse_arg_item(argv[i], argv[i + 1], cfg, &mask) != 0)
            return -1;
        i += 2;
    }
    if (mask != 0xffu)
        return -1;
    return 0;
}

static int run(int argc, char **argv)
{
    PelQloopConfig cfg;
    PelQloopObs hist[PEL_QLOOP_MAX_ITER];
    PelQloopStep step;
    size_t n_hist = 0;
    pel_result res;

    if (parse_cli_args(argc, argv, &cfg) != 0) {
        (void)fprintf(stderr, "Usage: pelorus_qloop --target T --tolerance D --min A --max B "
                              "--start S --resolution R --max-iter N --direction -1|1\n");
        return EXIT_FAILURE;
    }
    res = pel_qloop_validate(&cfg);
    if (res != PEL_OK) {
        (void)fprintf(stderr, "pelorus_qloop: invalid configuration: %s\n", pel_result_str(res));
        return EXIT_FAILURE;
    }

    if (read_history_stdin(hist, PEL_QLOOP_MAX_ITER, &n_hist) != 0) {
        return EXIT_FAILURE;
    }

    res = pel_qloop_next(&cfg, hist, n_hist, &step);
    if (res != PEL_OK) {
        (void)fprintf(stderr, "pelorus_qloop: step failed: %s\n", pel_result_str(res));
        return EXIT_FAILURE;
    }

    return emit_step(&step);
}

int main(int argc, char **argv)
{
    return pelorus_tool_main(argc, argv, "pelorus_qloop: cannot read command line as UTF-8", run);
}
