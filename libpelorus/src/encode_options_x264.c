/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Option table for x264.
 * Upstream repository: https://code.videolan.org/videolan/x264.git
 * Commit: b35605ace3ddf7c1a5d67a2eb553f034aef41d55
 * Source function: x264_param_parse (common/base.c:924-1407)
 */

#include "encode_options_internal.h"

#include <stdlib.h>
#include <string.h>

static const PelEncodeOptDef k_x264_options[] = {
    {"8x8dct", "8x8dct", PEL_OPT_TYPE_BOOL},
    {"alternative-transfer", "alternative-transfer", PEL_OPT_TYPE_STRING},
    {"analyse", "analyse", PEL_OPT_TYPE_STRING},
    {"annexb", "annexb", PEL_OPT_TYPE_BOOL},
    {"aq-mode", "aq-mode", PEL_OPT_TYPE_INT},
    {"aq-strength", "aq-strength", PEL_OPT_TYPE_FLOAT},
    {"asm", "asm", PEL_OPT_TYPE_STRING},
    {"aud", "aud", PEL_OPT_TYPE_BOOL},
    {"avcintra-class", "avcintra-class", PEL_OPT_TYPE_INT},
    {"avcintra-flavor", "avcintra-flavor", PEL_OPT_TYPE_STRING},
    {"b-adapt", "b-adapt", PEL_OPT_TYPE_INT},
    {"b-bias", "b-bias", PEL_OPT_TYPE_INT},
    {"b-pyramid", "b-pyramid", PEL_OPT_TYPE_STRING},
    {"bff", "bff", PEL_OPT_TYPE_BOOL},
    {"bframes", "bframes", PEL_OPT_TYPE_INT},
    {"bitrate", "bitrate", PEL_OPT_TYPE_INT},
    {"bluray-compat", "bluray-compat", PEL_OPT_TYPE_BOOL},
    {"cabac", "cabac", PEL_OPT_TYPE_BOOL},
    {"cabac-idc", "cabac-idc", PEL_OPT_TYPE_INT},
    {"chroma-me", "chroma-me", PEL_OPT_TYPE_BOOL},
    {"chroma-qp-offset", "chroma-qp-offset", PEL_OPT_TYPE_INT},
    {"chromaloc", "chromaloc", PEL_OPT_TYPE_INT},
    {"cll", "cll", PEL_OPT_TYPE_STRING},
    {"colormatrix", "colormatrix", PEL_OPT_TYPE_STRING},
    {"colorprim", "colorprim", PEL_OPT_TYPE_STRING},
    {"constrained-intra", "constrained-intra", PEL_OPT_TYPE_BOOL},
    {"cplx-blur", "cplxblur", PEL_OPT_TYPE_FLOAT},
    {"cplxblur", "cplxblur", PEL_OPT_TYPE_FLOAT},
    {"cpu-independent", "cpu-independent", PEL_OPT_TYPE_BOOL},
    {"cqm", "cqm", PEL_OPT_TYPE_STRING},
    {"cqm4", "cqm4", PEL_OPT_TYPE_STRING},
    {"cqm4i", "cqm4i", PEL_OPT_TYPE_STRING},
    {"cqm4ic", "cqm4ic", PEL_OPT_TYPE_STRING},
    {"cqm4iy", "cqm4iy", PEL_OPT_TYPE_STRING},
    {"cqm4p", "cqm4p", PEL_OPT_TYPE_STRING},
    {"cqm4pc", "cqm4pc", PEL_OPT_TYPE_STRING},
    {"cqm4py", "cqm4py", PEL_OPT_TYPE_STRING},
    {"cqm8", "cqm8", PEL_OPT_TYPE_STRING},
    {"cqm8i", "cqm8i", PEL_OPT_TYPE_STRING},
    {"cqm8p", "cqm8p", PEL_OPT_TYPE_STRING},
    {"cqmfile", "cqmfile", PEL_OPT_TYPE_STRING},
    {"crf", "crf", PEL_OPT_TYPE_FLOAT},
    {"crf-max", "crf-max", PEL_OPT_TYPE_FLOAT},
    {"crop-rect", "crop-rect", PEL_OPT_TYPE_STRING},
    {"dct-decimate", "dct-decimate", PEL_OPT_TYPE_BOOL},
    {"deadzone-inter", "deadzone-inter", PEL_OPT_TYPE_INT},
    {"deadzone-intra", "deadzone-intra", PEL_OPT_TYPE_INT},
    {"deblock", "filter", PEL_OPT_TYPE_STRING},
    {"deterministic", "deterministic", PEL_OPT_TYPE_BOOL},
    {"direct", "direct", PEL_OPT_TYPE_STRING},
    {"direct-pred", "direct", PEL_OPT_TYPE_STRING},
    {"dpb-size", "dpb-size", PEL_OPT_TYPE_INT},
    {"dump-yuv", "dump-yuv", PEL_OPT_TYPE_STRING},
    {"fake-interlaced", "fake-interlaced", PEL_OPT_TYPE_BOOL},
    {"fast-pskip", "fast-pskip", PEL_OPT_TYPE_BOOL},
    {"filler", "filler", PEL_OPT_TYPE_BOOL},
    {"filter", "filter", PEL_OPT_TYPE_STRING},
    {"force-cfr", "force-cfr", PEL_OPT_TYPE_BOOL},
    {"fps", "fps", PEL_OPT_TYPE_STRING},
    {"frame-packing", "frame-packing", PEL_OPT_TYPE_INT},
    {"frameref", "ref", PEL_OPT_TYPE_INT},
    {"fullrange", "fullrange", PEL_OPT_TYPE_STRING},
    {"global-header", "global-header", PEL_OPT_TYPE_BOOL},
    {"interlaced", "interlaced", PEL_OPT_TYPE_BOOL},
    {"intra-refresh", "intra-refresh", PEL_OPT_TYPE_BOOL},
    {"ip-factor", "ipratio", PEL_OPT_TYPE_FLOAT},
    {"ipratio", "ipratio", PEL_OPT_TYPE_FLOAT},
    {"keyint", "keyint", PEL_OPT_TYPE_STRING},
    {"keyint-min", "min-keyint", PEL_OPT_TYPE_INT},
    {"level", "level", PEL_OPT_TYPE_STRING},
    {"level-idc", "level", PEL_OPT_TYPE_STRING},
    {"log", "log", PEL_OPT_TYPE_INT},
    {"lookahead-threads", "lookahead-threads", PEL_OPT_TYPE_STRING},
    {"mastering-display", "mastering-display", PEL_OPT_TYPE_STRING},
    {"mbtree", "mbtree", PEL_OPT_TYPE_BOOL},
    {"me", "me", PEL_OPT_TYPE_STRING},
    {"me-range", "merange", PEL_OPT_TYPE_INT},
    {"merange", "merange", PEL_OPT_TYPE_INT},
    {"min-keyint", "min-keyint", PEL_OPT_TYPE_INT},
    {"mixed-refs", "mixed-refs", PEL_OPT_TYPE_BOOL},
    {"mv-range", "mvrange", PEL_OPT_TYPE_INT},
    {"mv-range-thread", "mvrange-thread", PEL_OPT_TYPE_INT},
    {"mvrange", "mvrange", PEL_OPT_TYPE_INT},
    {"mvrange-thread", "mvrange-thread", PEL_OPT_TYPE_INT},
    {"n-deterministic", "deterministic", PEL_OPT_TYPE_BOOL},
    {"nal-hrd", "nal-hrd", PEL_OPT_TYPE_STRING},
    {"nf", "nf", PEL_OPT_TYPE_BOOL},
    {"nr", "nr", PEL_OPT_TYPE_INT},
    {"open-gop", "open-gop", PEL_OPT_TYPE_BOOL},
    {"opencl", "opencl", PEL_OPT_TYPE_BOOL},
    {"opencl-clbin", "opencl-clbin", PEL_OPT_TYPE_STRING},
    {"opencl-device", "opencl-device", PEL_OPT_TYPE_INT},
    {"overscan", "overscan", PEL_OPT_TYPE_STRING},
    {"partitions", "analyse", PEL_OPT_TYPE_STRING},
    {"pass", "pass", PEL_OPT_TYPE_INT},
    {"pb-factor", "pbratio", PEL_OPT_TYPE_FLOAT},
    {"pbratio", "pbratio", PEL_OPT_TYPE_FLOAT},
    {"pic-struct", "pic-struct", PEL_OPT_TYPE_BOOL},
    {"psnr", "psnr", PEL_OPT_TYPE_BOOL},
    {"psy", "psy", PEL_OPT_TYPE_BOOL},
    {"psy-rd", "psy-rd", PEL_OPT_TYPE_STRING},
    {"qblur", "qblur", PEL_OPT_TYPE_FLOAT},
    {"qcomp", "qcomp", PEL_OPT_TYPE_FLOAT},
    {"qp", "qp", PEL_OPT_TYPE_INT},
    {"qp-constant", "qp", PEL_OPT_TYPE_INT},
    {"qp-max", "qpmax", PEL_OPT_TYPE_INT},
    {"qp-min", "qpmin", PEL_OPT_TYPE_INT},
    {"qp-step", "qpstep", PEL_OPT_TYPE_INT},
    {"qpmax", "qpmax", PEL_OPT_TYPE_INT},
    {"qpmin", "qpmin", PEL_OPT_TYPE_INT},
    {"qpstep", "qpstep", PEL_OPT_TYPE_INT},
    {"ratetol", "ratetol", PEL_OPT_TYPE_STRING},
    {"rc-lookahead", "rc-lookahead", PEL_OPT_TYPE_INT},
    {"ref", "ref", PEL_OPT_TYPE_INT},
    {"repeat-headers", "repeat-headers", PEL_OPT_TYPE_BOOL},
    {"sar", "sar", PEL_OPT_TYPE_STRING},
    {"scenecut", "scenecut", PEL_OPT_TYPE_STRING},
    {"slice-max-mbs", "slice-max-mbs", PEL_OPT_TYPE_INT},
    {"slice-max-size", "slice-max-size", PEL_OPT_TYPE_INT},
    {"slice-min-mbs", "slice-min-mbs", PEL_OPT_TYPE_INT},
    {"sliced-threads", "sliced-threads", PEL_OPT_TYPE_BOOL},
    {"slices", "slices", PEL_OPT_TYPE_INT},
    {"slices-max", "slices-max", PEL_OPT_TYPE_INT},
    {"sps-id", "sps-id", PEL_OPT_TYPE_INT},
    {"ssim", "ssim", PEL_OPT_TYPE_BOOL},
    {"stats", "stats", PEL_OPT_TYPE_STRING},
    {"stitchable", "stitchable", PEL_OPT_TYPE_BOOL},
    {"subme", "subme", PEL_OPT_TYPE_INT},
    {"subq", "subme", PEL_OPT_TYPE_INT},
    {"sync-lookahead", "sync-lookahead", PEL_OPT_TYPE_STRING},
    {"tff", "tff", PEL_OPT_TYPE_BOOL},
    {"threads", "threads", PEL_OPT_TYPE_STRING},
    {"transfer", "transfer", PEL_OPT_TYPE_STRING},
    {"trellis", "trellis", PEL_OPT_TYPE_INT},
    {"vbv-bufsize", "vbv-bufsize", PEL_OPT_TYPE_INT},
    {"vbv-init", "vbv-init", PEL_OPT_TYPE_FLOAT},
    {"vbv-maxrate", "vbv-maxrate", PEL_OPT_TYPE_INT},
    {"videoformat", "videoformat", PEL_OPT_TYPE_STRING},
    {"weight-b", "weightb", PEL_OPT_TYPE_BOOL},
    {"weightb", "weightb", PEL_OPT_TYPE_BOOL},
    {"weightp", "weightp", PEL_OPT_TYPE_INT},
    {"zones", "zones", PEL_OPT_TYPE_STRING},
};

static int opt_def_cmp(const void *a, const void *b)
{
    const char *key = (const char *)a;
    const PelEncodeOptDef *def = (const PelEncodeOptDef *)b;
    return strcmp(key, def->name);
}

const PelEncodeOptDef *pel_encode_options_x264_lookup(const char *name)
{
    if (!name) {
        return NULL;
    }
    return (const PelEncodeOptDef *)bsearch(name, k_x264_options,
                                            sizeof(k_x264_options) / sizeof(k_x264_options[0]),
                                            sizeof(k_x264_options[0]), opt_def_cmp);
}

const PelEncodeOptDef *pel_encode_options_x264_table(size_t *out_count)
{
    if (out_count) {
        *out_count = sizeof(k_x264_options) / sizeof(k_x264_options[0]);
    }
    return k_x264_options;
}
