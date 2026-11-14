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

/* Upper bounds for bounded loops during option parsing (HISS-02). */
#define PELORUS_TOOL_MAX_OPTS 32u
#define PELORUS_TOOL_MAX_ARGS 256u
#define PELORUS_TOOL_MAX_CHOICES 32u

/* Option kinds supported by the table-driven parser. */
typedef enum PelorusOptKind {
    PELORUS_OPT_DOUBLE,
    PELORUS_OPT_FLOAT,
    PELORUS_OPT_U32,
    PELORUS_OPT_I32,
    PELORUS_OPT_CHOICE,
    PELORUS_OPT_STRING,
} PelorusOptKind;

/* The members below are read in pelorus_tool_common.c. cppcheck checks a
 * header on its own and reports every member as unused there
 * (unusedStructMember); the translation unit check reports none. */
/* Descriptor for a long option. */
typedef struct PelorusOpt {
    // cppcheck-suppress unusedStructMember
    const char *name; /* long option flag, e.g. "--target" */
    // cppcheck-suppress unusedStructMember
    PelorusOptKind kind; /* argument value type */
    // cppcheck-suppress unusedStructMember
    void *dest; /* destination pointer for parsed value */
    // cppcheck-suppress unusedStructMember
    int required; /* 1 if required, 0 if optional */
    // cppcheck-suppress unusedStructMember
    const char *const *choices; /* for PELORUS_OPT_CHOICE: NULL-terminated string list */
    // cppcheck-suppress unusedStructMember
    const int *choice_values; /* optional: int values for choices, or NULL for 0..N-1 */
} PelorusOpt;

/* Descriptor for a single positional argument slot. */
typedef struct PelorusPositional {
    // cppcheck-suppress unusedStructMember
    const char *name; /* descriptive name, e.g. "<x265.csv>" */
    // cppcheck-suppress unusedStructMember
    const char **dest; /* destination pointer for positional string */
    // cppcheck-suppress unusedStructMember
    int required; /* 1 if required, 0 if optional */
} PelorusPositional;

/* Table-driven command-line argument parser for demonstration tools.
 * Parses argv (skipping argv[0]), matching against opts and optional single pos slot.
 * On error, prints diagnostics with tool_name prefix to stderr and returns -1.
 * Returns 0 on success. */
int pelorus_tool_parse_args(const char *tool_name, int argc, char **argv, const PelorusOpt *opts,
                            size_t n_opts, const PelorusPositional *pos);

/* A tool's main body, called with a UTF-8 argv. */
typedef int (*pel_tool_run_fn)(int argc, char **argv);

/* Parse a decimal digit prefix into uint32_t; refuse leading blanks, signs,
 * missing digits, tokens over 128 chars and overflow past UINT32_MAX (-1).
 * Return 0 on success; non-NULL *endptr points past the digits only on success.
 * Callers requiring a complete decimal must refuse suffixes, including hex. */
int pelorus_tool_parse_u32(const char *str, char **endptr, uint32_t *out);

/* Parse a decimal int32_t prefix with optional minus; refuse leading blanks,
 * plus, missing digits, tokens over 128 chars and range overflow (-1).
 * Return 0 on success; non-NULL *endptr points past the digits only on success.
 * Callers requiring a complete decimal must refuse suffixes, including hex. */
int pelorus_tool_parse_i32(const char *str, char **endptr, int32_t *out);

/* Parse finite decimal double with optional minus, fraction and exponent;
 * refuse leading blanks, leading plus, hex, inf/nan, missing digits, tokens over
 * 128 chars and ERANGE (-1). Return 0 on success; non-NULL *endptr points past
 * the token only on success; an exponent without digits remains unconsumed. */
int pelorus_tool_parse_double(const char *str, char **endptr, double *out);

/* Parse finite decimal float with optional minus, fraction and exponent;
 * refuse leading blanks, leading plus, hex, inf/nan, missing digits, tokens over
 * 128 chars and ERANGE (-1). Return 0 on success; non-NULL *endptr points past
 * the token only on success; an exponent without digits remains unconsumed. */
int pelorus_tool_parse_float(const char *str, char **endptr, float *out);

/* Parse decimal CRF in [0.0, 63.0], using the float grammar and 128-char cap;
 * refuse leading blanks, leading plus, hex, inf/nan, range errors and trailing
 * garbage (-1); trailing blanks/CR/LF remain allowed. Return 0 on success;
 * non-NULL *endptr points past the number only on success. */
int pelorus_tool_parse_crf(const char *str, char **endptr, float *out);

/* Read one line into buf. Returns 1 for a line, 0 at end of input, -1 on a read
 * error or a line longer than cap; errors are printed with the tool_name prefix. */
int pelorus_tool_read_line(FILE *in, char *buf, size_t cap, size_t *line_num,
                           const char *tool_name);

/* Run run_fn with argv; on Windows argv is rebuilt as UTF-8 first (ADR-0149) and
 * err_msg is printed when that fails. */
int pelorus_tool_main(int argc, char **argv, const char *err_msg, pel_tool_run_fn run_fn);

#endif /* PELORUS_TOOL_COMMON_H */
