/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_ENCODE_OPTIONS_INTERNAL_H
#define PELORUS_ENCODE_OPTIONS_INTERNAL_H

#include "pelorus/encode_options.h"

typedef enum pel_opt_val_type {
    PEL_OPT_TYPE_BOOL = 1,
    PEL_OPT_TYPE_INT = 2,
    PEL_OPT_TYPE_FLOAT = 3,
    PEL_OPT_TYPE_STRING = 4
} pel_opt_val_type;

typedef struct PelEncodeOptDef {
    const char *name;
    const char *canonical;
    pel_opt_val_type type;
} PelEncodeOptDef;

const PelEncodeOptDef *pel_encode_options_x264_lookup(const char *name);
const PelEncodeOptDef *pel_encode_options_x265_lookup(const char *name);
const PelEncodeOptDef *pel_encode_options_x264_table(size_t *out_count);
const PelEncodeOptDef *pel_encode_options_x265_table(size_t *out_count);

#endif /* PELORUS_ENCODE_OPTIONS_INTERNAL_H */
