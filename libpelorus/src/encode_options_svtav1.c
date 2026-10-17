/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Option table for SVT-AV1.
 * Upstream repository: https://gitlab.com/AOMediaCodec/SVT-AV1.git
 * Tag: v4.2.0 (9292ec8e32bce26f781f277ec8739b53426c4300)
 * Source function: svt_av1_enc_parse_parameter (Source/Lib/Globals/enc_settings.c:2125-2456)
 */

#include "encode_options_internal.h"

#include <stdlib.h>
#include <string.h>

static const PelEncodeOptDef k_svtav1_options[] = {
    {"ac-bias", "ac-bias", PEL_OPT_TYPE_FLOAT},
    {"adaptive-film-grain", "adaptive-film-grain", PEL_OPT_TYPE_BOOL},
    {"aq-mode", "aq-mode", PEL_OPT_TYPE_UINT},
    {"asm", "asm", PEL_OPT_TYPE_STRING},
    {"avif", "avif", PEL_OPT_TYPE_BOOL},
    {"buf-initial-sz", "buf-initial-sz", PEL_OPT_TYPE_INT},
    {"buf-optimal-sz", "buf-optimal-sz", PEL_OPT_TYPE_INT},
    {"buf-sz", "buf-sz", PEL_OPT_TYPE_INT},
    {"chroma-qindex-offsets", "chroma-qindex-offsets", PEL_OPT_TYPE_STRING},
    {"chroma-qm-max", "chroma-qm-max", PEL_OPT_TYPE_UINT},
    {"chroma-qm-min", "chroma-qm-min", PEL_OPT_TYPE_UINT},
    {"chroma-sample-position", "chroma-sample-position", PEL_OPT_TYPE_STRING},
    {"chroma-u-ac-qindex-offset", "chroma-u-ac-qindex-offset", PEL_OPT_TYPE_INT},
    {"chroma-u-dc-qindex-offset", "chroma-u-dc-qindex-offset", PEL_OPT_TYPE_INT},
    {"chroma-v-ac-qindex-offset", "chroma-v-ac-qindex-offset", PEL_OPT_TYPE_INT},
    {"chroma-v-dc-qindex-offset", "chroma-v-dc-qindex-offset", PEL_OPT_TYPE_INT},
    {"color-format", "color-format", PEL_OPT_TYPE_STRING},
    {"color-primaries", "color-primaries", PEL_OPT_TYPE_STRING},
    {"color-range", "color-range", PEL_OPT_TYPE_STRING},
    {"content-light", "content-light", PEL_OPT_TYPE_STRING},
    {"cqp", "cqp", PEL_OPT_TYPE_FLOAT},
    {"crf", "crf", PEL_OPT_TYPE_FLOAT},
    {"enable-cdef", "enable-cdef", PEL_OPT_TYPE_INT},
    {"enable-dg", "enable-dg", PEL_OPT_TYPE_BOOL},
    {"enable-dlf", "enable-dlf", PEL_OPT_TYPE_UINT},
    {"enable-force-key-frames", "enable-force-key-frames", PEL_OPT_TYPE_BOOL},
    {"enable-intrabc", "enable-intrabc", PEL_OPT_TYPE_BOOL},
    {"enable-kf-tf", "enable-kf-tf", PEL_OPT_TYPE_BOOL},
    {"enable-mfmv", "enable-mfmv", PEL_OPT_TYPE_INT},
    {"enable-overlays", "enable-overlays", PEL_OPT_TYPE_BOOL},
    {"enable-qm", "enable-qm", PEL_OPT_TYPE_BOOL},
    {"enable-restoration", "enable-restoration", PEL_OPT_TYPE_INT},
    {"enable-stat-report", "enable-stat-report", PEL_OPT_TYPE_UINT},
    {"enable-tf", "enable-tf", PEL_OPT_TYPE_UINT},
    {"enable-variance-boost", "enable-variance-boost", PEL_OPT_TYPE_BOOL},
    {"fast-decode", "fast-decode", PEL_OPT_TYPE_UINT},
    {"film-grain", "film-grain", PEL_OPT_TYPE_UINT},
    {"film-grain-denoise", "film-grain-denoise", PEL_OPT_TYPE_UINT},
    {"forced-max-frame-height", "forced-max-frame-height", PEL_OPT_TYPE_UINT},
    {"forced-max-frame-width", "forced-max-frame-width", PEL_OPT_TYPE_UINT},
    {"fps-denom", "fps-denom", PEL_OPT_TYPE_UINT},
    {"fps-num", "fps-num", PEL_OPT_TYPE_UINT},
    {"frame-resz-denoms", "frame-resz-denoms", PEL_OPT_TYPE_STRING},
    {"frame-resz-events", "frame-resz-events", PEL_OPT_TYPE_STRING},
    {"frame-resz-kf-denoms", "frame-resz-kf-denoms", PEL_OPT_TYPE_STRING},
    {"gop-constraint-rc", "gop-constraint-rc", PEL_OPT_TYPE_BOOL},
    {"h", "height", PEL_OPT_TYPE_UINT},
    {"hbd-mds", "hbd-mds", PEL_OPT_TYPE_INT},
    {"height", "height", PEL_OPT_TYPE_UINT},
    {"hierarchical-levels", "hierarchical-levels", PEL_OPT_TYPE_UINT},
    {"input-depth", "input-depth", PEL_OPT_TYPE_UINT},
    {"intra-period", "intra-period", PEL_OPT_TYPE_INT},
    {"irefresh-type", "irefresh-type", PEL_OPT_TYPE_STRING},
    {"key-frame-chroma-qindex-offset", "key-frame-chroma-qindex-offset", PEL_OPT_TYPE_INT},
    {"key-frame-qindex-offset", "key-frame-qindex-offset", PEL_OPT_TYPE_INT},
    {"keyint", "keyint", PEL_OPT_TYPE_STRING},
    {"lambda-scale-factors", "lambda-scale-factors", PEL_OPT_TYPE_STRING},
    {"level", "level", PEL_OPT_TYPE_UINT},
    {"lookahead", "lookahead", PEL_OPT_TYPE_UINT},
    {"lossless", "lossless", PEL_OPT_TYPE_BOOL},
    {"lp", "lp", PEL_OPT_TYPE_UINT},
    {"luma-y-dc-qindex-offset", "luma-y-dc-qindex-offset", PEL_OPT_TYPE_INT},
    {"luminance-qp-bias", "luminance-qp-bias", PEL_OPT_TYPE_UINT},
    {"mastering-display", "mastering-display", PEL_OPT_TYPE_STRING},
    {"matrix-coefficients", "matrix-coefficients", PEL_OPT_TYPE_STRING},
    {"max-inter-bitrate-pct", "max-inter-bitrate-pct", PEL_OPT_TYPE_UINT},
    {"max-intra-bitrate-pct", "max-intra-bitrate-pct", PEL_OPT_TYPE_UINT},
    {"max-qp", "max-qp", PEL_OPT_TYPE_UINT},
    {"max-tx-size", "max-tx-size", PEL_OPT_TYPE_UINT},
    {"maxsection-pct", "maxsection-pct", PEL_OPT_TYPE_UINT},
    {"mbr", "mbr", PEL_OPT_TYPE_STRING},
    {"mbr-overshoot-pct", "mbr-overshoot-pct", PEL_OPT_TYPE_UINT},
    {"min-qp", "min-qp", PEL_OPT_TYPE_UINT},
    {"minsection-pct", "minsection-pct", PEL_OPT_TYPE_UINT},
    {"overshoot-pct", "overshoot-pct", PEL_OPT_TYPE_UINT},
    {"pass", "pass", PEL_OPT_TYPE_INT},
    {"pred-struct", "pred-struct", PEL_OPT_TYPE_STRING},
    {"preset", "preset", PEL_OPT_TYPE_INT},
    {"profile", "profile", PEL_OPT_TYPE_STRING},
    {"q", "qp", PEL_OPT_TYPE_UINT},
    {"qindex-offsets", "qindex-offsets", PEL_OPT_TYPE_STRING},
    {"qm-max", "qm-max", PEL_OPT_TYPE_UINT},
    {"qm-min", "qm-min", PEL_OPT_TYPE_UINT},
    {"qp", "qp", PEL_OPT_TYPE_UINT},
    {"qp-scale-compress-strength", "qp-scale-compress-strength", PEL_OPT_TYPE_UINT},
    {"rc", "rc", PEL_OPT_TYPE_STRING},
    {"recode-loop", "recode-loop", PEL_OPT_TYPE_UINT},
    {"resize-denom", "resize-denom", PEL_OPT_TYPE_UINT},
    {"resize-kf-denom", "resize-kf-denom", PEL_OPT_TYPE_UINT},
    {"resize-mode", "resize-mode", PEL_OPT_TYPE_UINT},
    {"rtc", "rtc", PEL_OPT_TYPE_BOOL},
    {"scd", "scd", PEL_OPT_TYPE_UINT},
    {"scm", "scm", PEL_OPT_TYPE_UINT},
    {"sframe-dist", "sframe-dist", PEL_OPT_TYPE_INT},
    {"sframe-mode", "sframe-mode", PEL_OPT_TYPE_STRING},
    {"sframe-posi", "sframe-posi", PEL_OPT_TYPE_STRING},
    {"sframe-qp", "sframe-qp", PEL_OPT_TYPE_STRING},
    {"sframe-qp-offset", "sframe-qp-offset", PEL_OPT_TYPE_STRING},
    {"sharpness", "sharpness", PEL_OPT_TYPE_INT},
    {"startup-mg-size", "startup-mg-size", PEL_OPT_TYPE_UINT},
    {"startup-qp-offset", "startup-qp-offset", PEL_OPT_TYPE_INT},
    {"superres-denom", "superres-denom", PEL_OPT_TYPE_UINT},
    {"superres-kf-denom", "superres-kf-denom", PEL_OPT_TYPE_UINT},
    {"superres-kf-qthres", "superres-kf-qthres", PEL_OPT_TYPE_UINT},
    {"superres-mode", "superres-mode", PEL_OPT_TYPE_UINT},
    {"superres-qthres", "superres-qthres", PEL_OPT_TYPE_UINT},
    {"tbr", "tbr", PEL_OPT_TYPE_STRING},
    {"tf-strength", "tf-strength", PEL_OPT_TYPE_UINT},
    {"tier", "tier", PEL_OPT_TYPE_UINT},
    {"tile-columns", "tile-columns", PEL_OPT_TYPE_INT},
    {"tile-rows", "tile-rows", PEL_OPT_TYPE_INT},
    {"transfer-characteristics", "transfer-characteristics", PEL_OPT_TYPE_STRING},
    {"tune", "tune", PEL_OPT_TYPE_UINT},
    {"undershoot-pct", "undershoot-pct", PEL_OPT_TYPE_UINT},
    {"use-fixed-qindex-offsets", "use-fixed-qindex-offsets", PEL_OPT_TYPE_UINT},
    {"use-q-file", "use-q-file", PEL_OPT_TYPE_BOOL},
    {"variance-boost-curve", "variance-boost-curve", PEL_OPT_TYPE_UINT},
    {"variance-boost-strength", "variance-boost-strength", PEL_OPT_TYPE_UINT},
    {"variance-octile", "variance-octile", PEL_OPT_TYPE_UINT},
    {"w", "width", PEL_OPT_TYPE_UINT},
    {"width", "width", PEL_OPT_TYPE_UINT},
};

static int opt_def_cmp(const void *a, const void *b)
{
    const char *key = (const char *)a;
    const PelEncodeOptDef *def = (const PelEncodeOptDef *)b;
    return strcmp(key, def->name);
}

const PelEncodeOptDef *pel_encode_options_svtav1_lookup(const char *name)
{
    if (!name) {
        return NULL;
    }
    return (const PelEncodeOptDef *)bsearch(name, k_svtav1_options,
                                            sizeof(k_svtav1_options) / sizeof(k_svtav1_options[0]),
                                            sizeof(k_svtav1_options[0]), opt_def_cmp);
}

const PelEncodeOptDef *pel_encode_options_svtav1_table(size_t *out_count)
{
    if (out_count) {
        *out_count = sizeof(k_svtav1_options) / sizeof(k_svtav1_options[0]);
    }
    return k_svtav1_options;
}
