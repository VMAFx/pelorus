/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * encode_options.h — option-string parsers for x264, x265, SVT-AV1 and
 * libaom that fill PelorusEncodeParam arrays for pel_encode_record_build
 * (ADR-0198, docs/api/encode-record.md).
 *
 * Input strings use FFmpeg dict syntax (-x264-params, -x265-params,
 * -svtav1-params, -aom-params): key-value pairs split by ':', keys and values
 * separated by '=', with av_get_token escaping ('\' escapes next char, '...'
 * literal).
 *
 * Parsed parameters are normalised per dialect (x264 and x265: underscores to
 * hyphens and boolean negation unfolding; all: alias canonicalisation) and
 * typed (bool, int, float, verbatim string/enum). Duplicate options resolve
 * to last-wins. Unknown options keep their value verbatim in unknown_params
 * under the normalised key; a known key with a rejected value is kept there
 * under its canonical key.
 * Output arrays are sorted by key bytes. pel_encode_options_from_ffmpeg adds
 * the encoder's other FFmpeg AVOptions under an "ffmpeg:" prefix (ADR-0200).
 *
 * PelorusEncodeOptions is ~272 KiB (measured sizeof(PelorusEncodeOptions) =
 * 278552); callers should allocate on heap.
 */
#ifndef PELORUS_ENCODE_OPTIONS_H
#define PELORUS_ENCODE_OPTIONS_H

#include <stddef.h>
#include <stdint.h>

#include "pelorus/encode_record.h"
#include "pelorus/pelorus.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum pel_encode_options_dialect {
    PEL_ENCODE_OPTIONS_X264 = 1,
    PEL_ENCODE_OPTIONS_X265 = 2,
    PEL_ENCODE_OPTIONS_SVTAV1 = 3,
    PEL_ENCODE_OPTIONS_AOM = 4
} pel_encode_options_dialect;

#define PEL_ENCODE_OPTIONS_ARENA_SIZE 262144u

typedef struct PelorusEncodeOptions {
    PelorusEncodeParam params[PEL_ENCODE_RECORD_MAX_MEMBERS];
    uint32_t nb_params;
    PelorusEncodeParam unknown_params[PEL_ENCODE_RECORD_MAX_MEMBERS];
    uint32_t nb_unknown_params;
    /* private storage: value pointer slots + char arena */
    const char *param_val_ptrs[PEL_ENCODE_RECORD_MAX_MEMBERS];
    const char *unknown_val_ptrs[PEL_ENCODE_RECORD_MAX_MEMBERS];
    size_t arena_used;
    char arena[PEL_ENCODE_OPTIONS_ARENA_SIZE];
} PelorusEncodeOptions;

/*
 * Parse an encoder option string `text` of length `len` into `out`.
 * Caller owns `out` (allocate on heap; ~272 KiB); all pointers in
 * `out->params` and `out->unknown_params` point into `out`. Empty text or
 * len 0 returns PEL_OK with 0 params. Malformed syntax returns PEL_ERR_INVALID.
 * Bound overruns return PEL_ERR_RANGE. On any error return, out->nb_params,
 * out->nb_unknown_params, and out->arena_used are zeroed.
 */
pel_result pel_encode_options_parse(pel_encode_options_dialect d, const char *text, size_t len,
                                    PelorusEncodeOptions *out);

/* One FFmpeg AVOption as the caller set it on the encoder: name without a
 * stream specifier ("b", not "b:v") and the value text as given. */
typedef struct PelorusEncodeOption {
    const char *key;
    const char *value;
} PelorusEncodeOption;

/*
 * Fill `out` from the AVOptions set on FFmpeg encoder `encoder` (ADR-0200,
 * docs/api/encode-record.md). The encoder's option string entry
 * (x264-params, x265-params, svtav1-params, aom-params) is parsed as by
 * pel_encode_options_parse; its keys keep their bare names. Every other
 * AVOption is stored as "ffmpeg:<name>" with its value verbatim: under
 * `params` when the encoder's table lists the name, under `unknown_params`
 * otherwise. Host settings (threads, thread_type, device and stats-file
 * selection, diagnostics) are not stored. An AVOption that the option string
 * overrides in the encoder (a clash pair, for example libx264 crf) is dropped
 * when the string holds a valid value for it. Duplicate names: last wins.
 *
 * Returns PEL_ERR_UNSUPPORTED for an encoder without a table,
 * PEL_ERR_INVALID for NULL arguments, a name that is empty or contains ':',
 * an empty value, or an option-string key that starts with "ffmpeg:", and
 * PEL_ERR_RANGE when a key or value exceeds the record limits or storage
 * runs out. Errors from the option-string parse pass through. On any error
 * return, out->nb_params, out->nb_unknown_params and out->arena_used are
 * zeroed.
 *
 * Keys and values are copied into `out`'s arena; `opts` needs to live only
 * for the call. No allocation, no global state: calls on distinct `out`
 * instances are reentrant.
 */
pel_result pel_encode_options_from_ffmpeg(const char *encoder, const PelorusEncodeOption *opts,
                                          uint32_t nb_opts, PelorusEncodeOptions *out);

#ifdef __cplusplus
}
#endif

#endif /* PELORUS_ENCODE_OPTIONS_H */
