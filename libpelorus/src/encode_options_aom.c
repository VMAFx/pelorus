/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Option table for libaom.
 * Upstream repository: https://aomedia.googlesource.com/aom
 * Tag: v3.15.2 (af3dc9aadc793c1b00edc003e3fc19a44fdeb574)
 * Source function: encoder_set_option (av1/av1_cx_iface.c:4437-4937), names from av1/arg_defs.c
 */

#include "encode_options_internal.h"

#include <stdlib.h>
#include <string.h>

static const PelEncodeOptDef k_aom_options[] = {
    {"aq-mode", "aq-mode", PEL_OPT_TYPE_UINT},
    {"arnr-maxframes", "arnr-maxframes", PEL_OPT_TYPE_UINT},
    {"arnr-strength", "arnr-strength", PEL_OPT_TYPE_UINT},
    {"auto-alt-ref", "auto-alt-ref", PEL_OPT_TYPE_UINT},
    {"auto-intra-tools-off", "auto-intra-tools-off", PEL_OPT_TYPE_UINT},
    {"auto-tiles", "auto-tiles", PEL_OPT_TYPE_UINT},
    {"cdf-update-mode", "cdf-update-mode", PEL_OPT_TYPE_INT},
    {"coeff-cost-upd-freq", "coeff-cost-upd-freq", PEL_OPT_TYPE_UINT},
    {"color-primaries", "color-primaries", PEL_OPT_TYPE_STRING},
    {"cpu-used", "cpu-used", PEL_OPT_TYPE_UINT},
    {"cq-level", "cq-level", PEL_OPT_TYPE_UINT},
    {"delta-lf-mode", "delta-lf-mode", PEL_OPT_TYPE_UINT},
    {"deltaq-mode", "deltaq-mode", PEL_OPT_TYPE_UINT},
    {"deltaq-strength", "deltaq-strength", PEL_OPT_TYPE_UINT},
    {"denoise-block-size", "denoise-block-size", PEL_OPT_TYPE_UINT},
    {"denoise-noise-level", "denoise-noise-level", PEL_OPT_TYPE_INT},
    {"disable-trellis-quant", "disable-trellis-quant", PEL_OPT_TYPE_UINT},
    {"dist-metric", "dist-metric", PEL_OPT_TYPE_STRING},
    {"dv-cost-upd-freq", "dv-cost-upd-freq", PEL_OPT_TYPE_UINT},
    {"enable-1to4-partitions", "enable-1to4-partitions", PEL_OPT_TYPE_INT},
    {"enable-ab-partitions", "enable-ab-partitions", PEL_OPT_TYPE_INT},
    {"enable-adaptive-sharpness", "enable-adaptive-sharpness", PEL_OPT_TYPE_INT},
    {"enable-angle-delta", "enable-angle-delta", PEL_OPT_TYPE_INT},
    {"enable-cdef", "enable-cdef", PEL_OPT_TYPE_UINT},
    {"enable-cfl-intra", "enable-cfl-intra", PEL_OPT_TYPE_INT},
    {"enable-chroma-deltaq", "enable-chroma-deltaq", PEL_OPT_TYPE_UINT},
    {"enable-diagonal-intra", "enable-diagonal-intra", PEL_OPT_TYPE_INT},
    {"enable-diff-wtd-comp", "enable-diff-wtd-comp", PEL_OPT_TYPE_INT},
    {"enable-directional-intra", "enable-directional-intra", PEL_OPT_TYPE_INT},
    {"enable-dist-wtd-comp", "enable-dist-wtd-comp", PEL_OPT_TYPE_INT},
    {"enable-dnl-denoising", "enable-dnl-denoising", PEL_OPT_TYPE_UINT},
    {"enable-dual-filter", "enable-dual-filter", PEL_OPT_TYPE_UINT},
    {"enable-filter-intra", "enable-filter-intra", PEL_OPT_TYPE_INT},
    {"enable-flip-idtx", "enable-flip-idtx", PEL_OPT_TYPE_INT},
    {"enable-global-motion", "enable-global-motion", PEL_OPT_TYPE_INT},
    {"enable-interinter-wedge", "enable-interinter-wedge", PEL_OPT_TYPE_INT},
    {"enable-interintra-comp", "enable-interintra-comp", PEL_OPT_TYPE_INT},
    {"enable-interintra-wedge", "enable-interintra-wedge", PEL_OPT_TYPE_INT},
    {"enable-intra-edge-filter", "enable-intra-edge-filter", PEL_OPT_TYPE_UINT},
    {"enable-intrabc", "enable-intrabc", PEL_OPT_TYPE_INT},
    {"enable-keyframe-filtering", "enable-keyframe-filtering", PEL_OPT_TYPE_UINT},
    {"enable-masked-comp", "enable-masked-comp", PEL_OPT_TYPE_INT},
    {"enable-obmc", "enable-obmc", PEL_OPT_TYPE_UINT},
    {"enable-onesided-comp", "enable-onesided-comp", PEL_OPT_TYPE_INT},
    {"enable-order-hint", "enable-order-hint", PEL_OPT_TYPE_INT},
    {"enable-overlay", "enable-overlay", PEL_OPT_TYPE_INT},
    {"enable-paeth-intra", "enable-paeth-intra", PEL_OPT_TYPE_INT},
    {"enable-palette", "enable-palette", PEL_OPT_TYPE_INT},
    {"enable-qm", "enable-qm", PEL_OPT_TYPE_UINT},
    {"enable-rate-guide-deltaq", "enable-rate-guide-deltaq", PEL_OPT_TYPE_UINT},
    {"enable-rect-partitions", "enable-rect-partitions", PEL_OPT_TYPE_INT},
    {"enable-rect-tx", "enable-rect-tx", PEL_OPT_TYPE_INT},
    {"enable-ref-frame-mvs", "enable-ref-frame-mvs", PEL_OPT_TYPE_INT},
    {"enable-restoration", "enable-restoration", PEL_OPT_TYPE_UINT},
    {"enable-smooth-interintra", "enable-smooth-interintra", PEL_OPT_TYPE_INT},
    {"enable-smooth-intra", "enable-smooth-intra", PEL_OPT_TYPE_INT},
    {"enable-tpl-model", "enable-tpl-model", PEL_OPT_TYPE_UINT},
    {"enable-tx64", "enable-tx64", PEL_OPT_TYPE_INT},
    {"enable-warped-motion", "enable-warped-motion", PEL_OPT_TYPE_INT},
    {"error-resilient", "error-resilient", PEL_OPT_TYPE_INT},
    {"film-grain-table", "film-grain-table", PEL_OPT_TYPE_STRING},
    {"film-grain-test", "film-grain-test", PEL_OPT_TYPE_INT},
    {"force-max-q", "force-max-q", PEL_OPT_TYPE_UINT},
    {"force-video-mode", "force-video-mode", PEL_OPT_TYPE_UINT},
    {"fp-mt", "fp-mt", PEL_OPT_TYPE_UINT},
    {"frame-boost", "frame-boost", PEL_OPT_TYPE_UINT},
    {"frame-parallel", "frame-parallel", PEL_OPT_TYPE_UINT},
    {"fwd-kf-dist", "fwd-kf-dist", PEL_OPT_TYPE_INT},
    {"gf-cbr-boost", "gf-cbr-boost", PEL_OPT_TYPE_UINT},
    {"gf-max-pyr-height", "gf-max-pyr-height", PEL_OPT_TYPE_UINT},
    {"gf-min-pyr-height", "gf-min-pyr-height", PEL_OPT_TYPE_UINT},
    {"input-chroma-subsampling-x", "input-chroma-subsampling-x", PEL_OPT_TYPE_UINT},
    {"input-chroma-subsampling-y", "input-chroma-subsampling-y", PEL_OPT_TYPE_UINT},
    {"kf-max-pyr-height", "kf-max-pyr-height", PEL_OPT_TYPE_INT},
    {"loopfilter-control", "loopfilter-control", PEL_OPT_TYPE_INT},
    {"lossless", "lossless", PEL_OPT_TYPE_UINT},
    {"matrix-coefficients", "matrix-coefficients", PEL_OPT_TYPE_STRING},
    {"max-gf-interval", "max-gf-interval", PEL_OPT_TYPE_UINT},
    {"max-inter-rate", "max-inter-rate", PEL_OPT_TYPE_UINT},
    {"max-intra-rate", "max-intra-rate", PEL_OPT_TYPE_UINT},
    {"max-partition-size", "max-partition-size", PEL_OPT_TYPE_INT},
    {"max-reference-frames", "max-reference-frames", PEL_OPT_TYPE_INT},
    {"min-cr", "min-cr", PEL_OPT_TYPE_UINT},
    {"min-gf-interval", "min-gf-interval", PEL_OPT_TYPE_UINT},
    {"min-partition-size", "min-partition-size", PEL_OPT_TYPE_INT},
    {"mode-cost-upd-freq", "mode-cost-upd-freq", PEL_OPT_TYPE_UINT},
    {"mtu-size", "mtu-size", PEL_OPT_TYPE_UINT},
    {"mv-cost-upd-freq", "mv-cost-upd-freq", PEL_OPT_TYPE_UINT},
    {"noise-sensitivity", "noise-sensitivity", PEL_OPT_TYPE_UINT},
    {"num-tile-groups", "num-tile-groups", PEL_OPT_TYPE_UINT},
    {"partition-info-path", "partition-info-path", PEL_OPT_TYPE_STRING},
    {"passes", "passes", PEL_OPT_TYPE_INT},
    {"qm-max", "qm-max", PEL_OPT_TYPE_UINT},
    {"qm-min", "qm-min", PEL_OPT_TYPE_UINT},
    {"quant-b-adapt", "quant-b-adapt", PEL_OPT_TYPE_INT},
    {"rate-distribution-info", "rate-distribution-info", PEL_OPT_TYPE_STRING},
    {"reduced-reference-set", "reduced-reference-set", PEL_OPT_TYPE_INT},
    {"reduced-tx-type-set", "reduced-tx-type-set", PEL_OPT_TYPE_INT},
    {"row-mt", "row-mt", PEL_OPT_TYPE_UINT},
    {"sb-qp-sweep", "sb-qp-sweep", PEL_OPT_TYPE_INT},
    {"sb-size", "sb-size", PEL_OPT_TYPE_STRING},
    {"screen-detection-mode", "screen-detection-mode", PEL_OPT_TYPE_INT},
    {"second-pass-log", "second-pass-log", PEL_OPT_TYPE_STRING},
    {"set-tier-mask", "set-tier-mask", PEL_OPT_TYPE_UINT},
    {"sframe-mode", "sframe-mode", PEL_OPT_TYPE_INT},
    {"sharpness", "sharpness", PEL_OPT_TYPE_UINT},
    {"static-thresh", "static-thresh", PEL_OPT_TYPE_UINT},
    {"strict-level-conformance", "strict-level-conformance", PEL_OPT_TYPE_INT},
    {"target-seq-level-idx", "target-seq-level-idx", PEL_OPT_TYPE_INT},
    {"tile-columns", "tile-columns", PEL_OPT_TYPE_UINT},
    {"tile-height", "tile-height", PEL_OPT_TYPE_STRING},
    {"tile-rows", "tile-rows", PEL_OPT_TYPE_UINT},
    {"tile-width", "tile-width", PEL_OPT_TYPE_STRING},
    {"timing-info", "timing-info", PEL_OPT_TYPE_STRING},
    {"tune", "tune", PEL_OPT_TYPE_STRING},
    {"tune-content", "tune-content", PEL_OPT_TYPE_STRING},
    {"two-pass-output", "two-pass-output", PEL_OPT_TYPE_STRING},
    {"use-inter-dct-only", "use-inter-dct-only", PEL_OPT_TYPE_INT},
    {"use-intra-dct-only", "use-intra-dct-only", PEL_OPT_TYPE_INT},
    {"use-intra-default-tx-only", "use-intra-default-tx-only", PEL_OPT_TYPE_INT},
    {"validate-hbd-input", "validate-hbd-input", PEL_OPT_TYPE_INT},
    {"vbr-corpus-complexity-lap", "vbr-corpus-complexity-lap", PEL_OPT_TYPE_UINT},
};

static int opt_def_cmp(const void *a, const void *b)
{
    const char *key = (const char *)a;
    const PelEncodeOptDef *def = (const PelEncodeOptDef *)b;
    return strcmp(key, def->name);
}

const PelEncodeOptDef *pel_encode_options_aom_lookup(const char *name)
{
    if (!name) {
        return NULL;
    }
    return (const PelEncodeOptDef *)bsearch(name, k_aom_options,
                                            sizeof(k_aom_options) / sizeof(k_aom_options[0]),
                                            sizeof(k_aom_options[0]), opt_def_cmp);
}

const PelEncodeOptDef *pel_encode_options_aom_table(size_t *out_count)
{
    if (out_count) {
        *out_count = sizeof(k_aom_options) / sizeof(k_aom_options[0]);
    }
    return k_aom_options;
}
