/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * encode_avoptions.c — FFmpeg encoder AVOptions into encode record
 * parameters (ADR-0200). The per-encoder name tables are generated into
 * encode_avoptions_tables.c; the ignore list and the clash pairs below are
 * read from FFmpeg n9.0.2 and the pinned encoder sources.
 */

#include "encode_options_internal.h"

#include <string.h>

#define PEL_AVOPT_PREFIX "ffmpeg:"
#define PEL_AVOPT_PREFIX_LEN 7u

/* Host settings that the record does not carry: thread layout, the stats-file
 * path (the second pass reads its content, the name is a host path), the GPU
 * index, and diagnostics or limits that do not change the encoder settings. */
static const char *const k_ignored[] = {
    "debug",       "dump_separator", "err_detect",  "export_side_data", "gpu",        "max_pixels",
    "passlogfile", "stats",          "thread_type", "threads",          "x265-stats",
};

/* AVOption -> option-string key the encoder applies later to the same
 * fields, so the string value is the one that encodes (ADR-0200 lists the
 * source lines): libx264.c:1123-1128 then x264_param_parse (libx264.c:1383,
 * x264 common/base.c:1313-1322); libx265.c:431-443 then x265 param_parse
 * (libx265.c:520, x265 common/param.cpp:857-866); libsvtav1.c:256-258 then
 * svt_av1_enc_parse_parameter (libsvtav1.c:348, SVT-AV1 enc_settings.c:1499);
 * AOME_SET_CPUUSED (libaomenc.c:983) then aom-params (libaomenc.c:1035,
 * libaom av1_cx_iface.c:4474). */
typedef struct PelAvoptClash {
    const char *encoder;
    const char *avoption;
    const char *string_key;
} PelAvoptClash;

static const PelAvoptClash k_clashes[] = {
    {"libx264", "crf", "crf"}, {"libx264", "qp", "qp"},     {"libx265", "crf", "crf"},
    {"libx265", "qp", "qp"},   {"libsvtav1", "crf", "crf"}, {"libaom-av1", "cpu-used", "cpu-used"},
};

static int name_in(const char *const *names, size_t count, const char *name)
{
    size_t i;
    for (i = 0; i < count; i++) {
        if (strcmp(names[i], name) == 0) {
            return 1;
        }
    }
    return 0;
}

static const PelAvoptEncoder *find_encoder(const char *encoder)
{
    size_t count = 0;
    const PelAvoptEncoder *encs = pel_encode_avoptions_encoders(&count);
    size_t i;
    for (i = 0; i < count; i++) {
        if (strcmp(encs[i].encoder, encoder) == 0) {
            return &encs[i];
        }
    }
    return NULL;
}

static int has_param(const PelorusEncodeOptions *out, const char *key)
{
    uint32_t i;
    for (i = 0; i < out->nb_params; i++) {
        if (strcmp(out->params[i].key, key) == 0) {
            return 1;
        }
    }
    return 0;
}

/* The option string's valid value for this AVOption's clash key wins. */
static int overridden(const PelAvoptEncoder *enc, const char *name, const PelorusEncodeOptions *out)
{
    size_t i;
    for (i = 0; i < sizeof(k_clashes) / sizeof(k_clashes[0]); i++) {
        if (strcmp(k_clashes[i].encoder, enc->encoder) == 0 &&
            strcmp(k_clashes[i].avoption, name) == 0) {
            return has_param(out, k_clashes[i].string_key);
        }
    }
    return 0;
}

static int prefixed(const PelorusEncodeParam *p, uint32_t count)
{
    uint32_t i;
    for (i = 0; i < count; i++) {
        if (strncmp(p[i].key, PEL_AVOPT_PREFIX, PEL_AVOPT_PREFIX_LEN) == 0) {
            return 1;
        }
    }
    return 0;
}

static pel_result check_options(const PelorusEncodeOption *opts, uint32_t nb_opts)
{
    uint32_t i;
    for (i = 0; i < nb_opts; i++) {
        if (!opts[i].key || !opts[i].value || opts[i].key[0] == '\0' || opts[i].value[0] == '\0' ||
            strchr(opts[i].key, ':') != NULL) {
            return PEL_ERR_INVALID;
        }
    }
    return PEL_OK;
}

/* Parses the last option-string entry, the one FFmpeg's dictionary keeps. */
static pel_result parse_option_string(const PelAvoptEncoder *enc, const PelorusEncodeOption *opts,
                                      uint32_t nb_opts, PelorusEncodeOptions *out)
{
    const char *text = NULL;
    uint32_t i;
    pel_result r;

    if (!enc->params_key) {
        return PEL_OK;
    }
    for (i = 0; i < nb_opts; i++) {
        if (strcmp(opts[i].key, enc->params_key) == 0) {
            text = opts[i].value;
        }
    }
    if (!text) {
        return PEL_OK;
    }
    r = pel_encode_options_parse(enc->dialect, text, strlen(text), out);
    if (r != PEL_OK) {
        return r;
    }
    if (prefixed(out->params, out->nb_params) ||
        prefixed(out->unknown_params, out->nb_unknown_params)) {
        return PEL_ERR_INVALID;
    }
    return PEL_OK;
}

static pel_result store_avoption(const PelAvoptEncoder *enc, const PelorusEncodeOption *opt,
                                 PelorusEncodeOptions *out)
{
    char key[PEL_ENCODE_RECORD_MAX_KEY + 1];
    size_t nlen = strlen(opt->key);
    size_t vlen = strlen(opt->value);

    if (PEL_AVOPT_PREFIX_LEN + nlen > PEL_ENCODE_RECORD_MAX_KEY ||
        vlen > PEL_ENCODE_RECORD_MAX_VALUE) {
        return PEL_ERR_RANGE;
    }
    (void)memcpy(key, PEL_AVOPT_PREFIX, PEL_AVOPT_PREFIX_LEN);
    (void)memcpy(key + PEL_AVOPT_PREFIX_LEN, opt->key, nlen + 1);
    return pel_encode_options_store(out, !name_in(enc->names, enc->nb_names, opt->key), key,
                                    PEL_AVOPT_PREFIX_LEN + nlen, opt->value, vlen);
}

static pel_result store_avoptions(const PelAvoptEncoder *enc, const PelorusEncodeOption *opts,
                                  uint32_t nb_opts, PelorusEncodeOptions *out)
{
    uint32_t i;
    pel_result r;

    for (i = 0; i < nb_opts; i++) {
        const char *name = opts[i].key;
        if ((enc->params_key && strcmp(name, enc->params_key) == 0) ||
            name_in(k_ignored, sizeof(k_ignored) / sizeof(k_ignored[0]), name) ||
            overridden(enc, name, out)) {
            continue;
        }
        r = store_avoption(enc, &opts[i], out);
        if (r != PEL_OK) {
            return r;
        }
    }
    return PEL_OK;
}

pel_result pel_encode_options_from_ffmpeg(const char *encoder, const PelorusEncodeOption *opts,
                                          uint32_t nb_opts, PelorusEncodeOptions *out)
{
    const PelAvoptEncoder *enc;
    pel_result r;

    if (!out) {
        return PEL_ERR_INVALID;
    }
    out->nb_params = 0;
    out->nb_unknown_params = 0;
    out->arena_used = 0;
    if (!encoder || (!opts && nb_opts > 0)) {
        return PEL_ERR_INVALID;
    }
    enc = find_encoder(encoder);
    if (!enc) {
        return PEL_ERR_UNSUPPORTED;
    }
    r = check_options(opts, nb_opts);
    if (r == PEL_OK) {
        r = parse_option_string(enc, opts, nb_opts, out);
    }
    if (r == PEL_OK) {
        r = store_avoptions(enc, opts, nb_opts, out);
    }
    if (r != PEL_OK) {
        out->nb_params = 0;
        out->nb_unknown_params = 0;
        out->arena_used = 0;
        return r;
    }
    pel_encode_options_sort(out);
    return PEL_OK;
}
