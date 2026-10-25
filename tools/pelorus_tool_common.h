/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_TOOL_COMMON_H
#define PELORUS_TOOL_COMMON_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

/* Shared helpers of the libpelorus CLI demonstrators in tools/; not installed. */

/* A tool's main body, called with a UTF-8 argv. */
typedef int (*pel_tool_run_fn)(int argc, char **argv);

/* Parse a decimal uint32_t; 0 on success, -1 on no digits, overflow or a value above
 * UINT32_MAX. *endptr points past the digits, as strtoull sets it. */
int pelorus_tool_parse_u32(const char *str, char **endptr, uint32_t *out);

/* Read one line into buf. Returns 1 for a line, 0 at end of input, -1 on a read
 * error or a line longer than cap; errors are printed with the tool_name prefix. */
int pelorus_tool_read_line(FILE *in, char *buf, size_t cap, size_t *line_num,
                           const char *tool_name);

/* Run run_fn with argv; on Windows argv is rebuilt as UTF-8 first (ADR-0149) and
 * err_msg is printed when that fails. */
int pelorus_tool_main(int argc, char **argv, const char *err_msg, pel_tool_run_fn run_fn);

#endif /* PELORUS_TOOL_COMMON_H */
