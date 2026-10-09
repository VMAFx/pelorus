/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * encode_record.h — the encode provenance record (ADR-0175,
 * docs/api/encode-record.md): canonical JSON (RFC 8785 subset, every scalar a
 * string) and its SHA-256 digest "sha256:" + 64 lower-case hex digits, the exact
 * form VMAFx stores as a score's encode_record.
 *
 * Every function writes into caller buffers and allocates nothing; none keeps a
 * pointer it was given. No global or static mutable state: concurrent calls on
 * distinct buffers are safe. Records are bounded (HISS-02): text at most
 * PEL_ENCODE_RECORD_MAX_BYTES, objects at most PEL_ENCODE_RECORD_MAX_MEMBERS
 * entries, arrays at most PEL_ENCODE_RECORD_MAX_ARRAY, PEL_ENCODE_RECORD_MAX_DEPTH
 * containers below the root, keys at most PEL_ENCODE_RECORD_MAX_KEY bytes and
 * values at most PEL_ENCODE_RECORD_MAX_VALUE bytes. Anything beyond a bound is
 * rejected with PEL_ERR_INVALID, never truncated.
 */
#ifndef PELORUS_ENCODE_RECORD_H
#define PELORUS_ENCODE_RECORD_H

#include <stddef.h>
#include <stdint.h>

#include "pelorus/interop.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PEL_ENCODE_RECORD_SCHEMA "pelorus/encode-record/1"
#define PEL_ENCODE_RECORD_MAX_BYTES 65536u
#define PEL_ENCODE_RECORD_MAX_MEMBERS 256u /* entries per object (params)     */
#define PEL_ENCODE_RECORD_MAX_ARRAY 64u    /* entries per array (filters)     */
#define PEL_ENCODE_RECORD_MAX_DEPTH 4u     /* containers below the root object */
#define PEL_ENCODE_RECORD_MAX_KEY 64u
#define PEL_ENCODE_RECORD_MAX_VALUE 1024u
#define PEL_ENCODE_RECORD_DIGEST_SIZE 32u
/* Buffer size for one pel_encode_record_int / _f64 text, NUL included. */
#define PEL_ENCODE_RECORD_SCALAR_SIZE 24u

/* One parameter: a key and either one scalar text or an array of them (a
 * per-plane value). Scalar texts come from pel_encode_record_int / _f64, are
 * "true" / "false", or are the string itself. Keys are printable ASCII
 * (0x21..0x7e without '"' and '\\'), unique per object, 1 to 64 bytes. */
typedef struct PelorusEncodeParam {
    const char *key;
    const char *const *values; /* nb_values NUL-terminated UTF-8 texts            */
    uint32_t nb_values;        /* 1 for a scalar; 1..PEL_ENCODE_RECORD_MAX_ARRAY  */
    uint8_t is_array;          /* 1 => written as a JSON array                    */
} PelorusEncodeParam;

/* One pre-encode filter, in chain order. params are the validated contract
 * values (deband.h, denoise.h, ...), not the option text. */
typedef struct PelorusEncodeFilter {
    const char *name;    /* e.g. "pelorus_deband_vulkan"            */
    const char *library; /* "libpelorus" or the host library        */
    const char *version;
    const PelorusEncodeParam *params;
    uint32_t nb_params;
    const PelorusEncodeParam *unknown_params;
    uint32_t nb_unknown_params;
} PelorusEncodeFilter;

/* The members of one record (docs/api/encode-record.md, "Members"). Required
 * strings are non-NULL and non-empty; NULL optional strings are omitted. */
typedef struct PelorusEncodeRecordInput {
    const char *encoder_name;                 /* required, e.g. "libx265"                         */
    const char *encoder_version;              /* required                                         */
    const char *encoder_codec;                /* required: h264, hevc, vvc, av1, vp9              */
    const PelorusEncodeParam *encoder_params; /* normalised known options    */
    uint32_t nb_encoder_params;               /* 0..256                      */
    const PelorusEncodeParam *encoder_unknown_params; /* unknown options, verbatim   */
    uint32_t nb_encoder_unknown_params;               /* 0..256                      */
    const char *input_digest;           /* "sha256:" + 64 lower-case hex digits, or NULL    */
    const char *input_id;               /* opaque caller id, or NULL; one of the two is set */
    const PelorusEncodeFilter *filters; /* chain order                               */
    uint32_t nb_filters;                /* 0..64                                     */
    const char *hardware_path;          /* cpu, vulkan, nvenc, qsv, amf, vaapi, videotoolbox */
    const char *hardware_device;        /* as the API reports it, or NULL                   */
    const char *hardware_driver;        /* as the API reports it, or NULL                   */
    uint64_t elapsed_ns;                /* wall time; outside the digest                    */
    uint8_t has_elapsed_ns;             /* 1 => write elapsed_ns                            */
} PelorusEncodeRecordInput;

/* Integer scalar text: plain decimal, no '+', no leading zeros. Returns PEL_OK
 * or PEL_ERR_INVALID (NULL out). */
pel_result pel_encode_record_int(int64_t v, char out[PEL_ENCODE_RECORD_SCALAR_SIZE]);

/* Real scalar text: "f64:" + the 16 lower-case hex digits of the IEEE-754
 * binary64 bits. Pass a float widened to double. -0.0 is written as +0.0.
 * Returns PEL_OK, PEL_ERR_INVALID (NULL out) or PEL_ERR_RANGE (NaN, infinity). */
pel_result pel_encode_record_f64(double v, char out[PEL_ENCODE_RECORD_SCALAR_SIZE]);

/*
 * Build the canonical text of a record with its `digest` member filled in.
 * buf receives *out_len bytes plus a NUL. buf NULL with cap 0 is a size query:
 * PEL_ERR_RANGE with the length in *out_len, as for pel_blob_pack_into. Returns
 * PEL_OK, PEL_ERR_INVALID (NULL in/out_len, NULL buf with cap > 0, a missing
 * required member, a malformed input_digest, an unknown hardware_path, an empty,
 * bad or duplicate key, invalid UTF-8, a bound overrun), or PEL_ERR_RANGE (cap
 * shorter than the text plus NUL; *out_len holds the length).
 */
pel_result pel_encode_record_build(const PelorusEncodeRecordInput *in, char *buf, size_t cap,
                                   size_t *out_len);

/*
 * The canonical text of any record (`json`, `len` bytes of UTF-8 JSON whose
 * root is an object). buf receives *out_len bytes plus a NUL; buf NULL with cap
 * 0 is a size query (PEL_ERR_RANGE, length in *out_len). Returns PEL_OK,
 * PEL_ERR_INVALID (NULL json/out_len, NULL buf with cap > 0, malformed JSON, a
 * duplicate or empty key, a non-string scalar, invalid UTF-8 or a lone
 * surrogate, a key outside printable ASCII, a bound overrun) or PEL_ERR_RANGE
 * (cap shorter than the text plus NUL; *out_len holds the length).
 */
pel_result pel_encode_record_canonicalize(const char *json, size_t len, char *buf, size_t cap,
                                          size_t *out_len);

/*
 * The raw SHA-256 of the canonical text of `json` without its top-level
 * `digest` and `elapsed_ns` members. The whole record is validated as by
 * pel_encode_record_canonicalize. Returns PEL_OK or PEL_ERR_INVALID.
 */
pel_result pel_encode_record_hash(const char *json, size_t len,
                                  uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE]);

/*
 * Recompute the digest of `json` and compare it with the record's own
 * top-level `digest` member and, when non-NULL, with `expected`. Returns PEL_OK,
 * PEL_ERR_MISMATCH (either differs), PEL_ERR_ABSENT (no `digest` member and no
 * `expected`: nothing to verify against) or PEL_ERR_INVALID.
 */
pel_result pel_encode_record_verify(const char *json, size_t len,
                                    const uint8_t expected[PEL_ENCODE_RECORD_DIGEST_SIZE]);

#ifdef __cplusplus
}
#endif
#endif /* PELORUS_ENCODE_RECORD_H */
