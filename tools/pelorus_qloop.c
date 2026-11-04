/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/pelorus.h"
#include "pelorus/qloop.h"
#include "pelorus_tool_common.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_INPUT_LINES (PEL_QLOOP_MAX_ITER + 2u)

/* Parse a history line from line into knob and score observations for obs, skipping blank and comment lines.
 * Returns 1 on success, 0 for empty or comment lines, or -1 on syntax error. */
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
    if (pelorus_tool_parse_double(p, &end, &knob) != 0) {
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
    if (pelorus_tool_parse_double(p, &end, &score) != 0) {
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

/* Run the pelorus_qloop CLI tool using the arguments in argv to read history from stdin and emit the next step.
 * Returns EXIT_SUCCESS on success or EXIT_FAILURE on error. */
static int run(int argc, char **argv)
{
    PelQloopConfig cfg;
    PelQloopObs hist[PEL_QLOOP_MAX_ITER];
    PelQloopStep step;
    size_t n_hist = 0;
    pel_result res;
    const PelorusOpt opts[] = {
        {"--target", PELORUS_OPT_DOUBLE, &cfg.target, 1, NULL, NULL},
        {"--tolerance", PELORUS_OPT_DOUBLE, &cfg.tolerance, 1, NULL, NULL},
        {"--min", PELORUS_OPT_DOUBLE, &cfg.knob_min, 1, NULL, NULL},
        {"--max", PELORUS_OPT_DOUBLE, &cfg.knob_max, 1, NULL, NULL},
        {"--start", PELORUS_OPT_DOUBLE, &cfg.knob_start, 1, NULL, NULL},
        {"--resolution", PELORUS_OPT_DOUBLE, &cfg.resolution, 1, NULL, NULL},
        {"--max-iter", PELORUS_OPT_U32, &cfg.max_iter, 1, NULL, NULL},
        {"--direction", PELORUS_OPT_I32, &cfg.direction, 1, NULL, NULL},
    };

    memset(&cfg, 0, sizeof(cfg));
    if (pelorus_tool_parse_args("pelorus_qloop", argc, argv, opts, sizeof(opts) / sizeof(opts[0]),
                                NULL) != 0) {
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
