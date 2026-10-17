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
    PEL_OPT_TYPE_STRING = 4,
    PEL_OPT_TYPE_UINT = 5
} pel_opt_val_type;

typedef struct PelEncodeOptDef {
    const char *name;
    const char *canonical;
    pel_opt_val_type type;
} PelEncodeOptDef;

const PelEncodeOptDef *pel_encode_options_x264_lookup(const char *name);
const PelEncodeOptDef *pel_encode_options_x265_lookup(const char *name);
const PelEncodeOptDef *pel_encode_options_svtav1_lookup(const char *name);
const PelEncodeOptDef *pel_encode_options_aom_lookup(const char *name);
const PelEncodeOptDef *pel_encode_options_x264_table(size_t *out_count);
const PelEncodeOptDef *pel_encode_options_x265_table(size_t *out_count);
const PelEncodeOptDef *pel_encode_options_svtav1_table(size_t *out_count);
const PelEncodeOptDef *pel_encode_options_aom_table(size_t *out_count);

/* One FFmpeg encoder's AVOption table (ADR-0200), generated into
 * encode_avoptions_tables.c. dialect is 0 and params_key NULL for an encoder
 * without an option string; names are sorted by bytes. */
typedef struct PelAvoptEncoder {
    const char *encoder;
    pel_encode_options_dialect dialect;
    const char *params_key;
    const char *const *names;
    uint32_t nb_names;
} PelAvoptEncoder;

const PelAvoptEncoder *pel_encode_avoptions_encoders(size_t *out_count);

/* Shared with encode_avoptions.c: store key/val under params (unknown = 0) or
 * unknown_params (unknown = 1), last value wins; sort both arrays by key. */
pel_result pel_encode_options_store(PelorusEncodeOptions *out, int unknown, const char *key,
                                    size_t klen, const char *val, size_t vlen);
void pel_encode_options_sort(PelorusEncodeOptions *out);

#endif /* PELORUS_ENCODE_OPTIONS_INTERNAL_H */
