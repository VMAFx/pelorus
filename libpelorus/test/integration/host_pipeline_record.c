/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "host_pipeline.h"

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

typedef struct DebandFilterStorage {
    char s_range[PEL_ENCODE_RECORD_SCALAR_SIZE];
    char s_softness[PEL_ENCODE_RECORD_SCALAR_SIZE];
    const char *v_range[1];
    const char *v_softness[1];
    PelorusEncodeParam params[2];
} DebandFilterStorage;

/* Initialize deband filter description and string scalar values. */
static void init_deband_filter(PelorusEncodeFilter *filter, DebandFilterStorage *store)
{
    PelorusDebandParams deband;
    const char *what = NULL;

    pel_deband_params_default(&deband);
    CHECK(pel_deband_params_validate(&deband, &what) == PEL_OK);

    CHECK(pel_encode_record_int(deband.range, store->s_range) == PEL_OK);
    CHECK(pel_encode_record_f64((double)deband.softness, store->s_softness) == PEL_OK);

    store->v_range[0] = store->s_range;
    store->v_softness[0] = store->s_softness;

    store->params[0].key = "range";
    store->params[0].values = store->v_range;
    store->params[0].nb_values = 1u;
    store->params[0].is_array = 0u;

    store->params[1].key = "softness";
    store->params[1].values = store->v_softness;
    store->params[1].nb_values = 1u;
    store->params[1].is_array = 0u;

    memset(filter, 0, sizeof(*filter));
    filter->name = "pelorus_deband_vulkan";
    filter->library = "libpelorus";
    filter->version = pelorus_version_string();
    filter->params = store->params;
    filter->nb_params = 2u;
}

/* Build provenance record JSON string describing encoding filters and parameters. */
void hp_build_provenance_json(char **out_json, size_t *out_len)
{
    static const char opt_str[] = "crf=22:preset=slow:aq-mode=3";
    PelorusEncodeOptions *opts;
    DebandFilterStorage store;
    PelorusEncodeFilter filter;
    PelorusEncodeRecordInput rec_in;
    size_t need = 0;
    size_t built = 0;
    char *json;

    opts = calloc(1u, sizeof(*opts));
    CHECK(opts != NULL);
    if (opts == NULL) {
        return;
    }
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, opt_str, sizeof(opt_str) - 1u, opts) ==
          PEL_OK);

    init_deband_filter(&filter, &store);

    memset(&rec_in, 0, sizeof(rec_in));
    rec_in.encoder_name = "libx265";
    rec_in.encoder_version = "4.3";
    rec_in.encoder_codec = "hevc";
    rec_in.encoder_params = opts->params;
    rec_in.nb_encoder_params = opts->nb_params;
    rec_in.encoder_unknown_params = opts->unknown_params;
    rec_in.nb_encoder_unknown_params = opts->nb_unknown_params;
    rec_in.input_id = "integration-clip";
    rec_in.hardware_path = "vulkan";
    rec_in.filters = &filter;
    rec_in.nb_filters = 1u;

    CHECK(pel_encode_record_build(&rec_in, NULL, 0u, &need) == PEL_ERR_RANGE);
    CHECK(need > 0u);
    if (need == 0u) {
        free(opts);
        return;
    }
    json = malloc(need + 1u);
    CHECK(json != NULL);
    if (json != NULL) {
        CHECK(pel_encode_record_build(&rec_in, json, need + 1u, &built) == PEL_OK);
        CHECK(built == need);
        *out_json = json;
        *out_len = built;
    }
    free(opts);
}

/* Populate encode record section directly with SHA-256 hash of provenance JSON. */
void hp_create_record_section(const char *json, size_t json_len,
                              PelorusEncodeRecordSection *rec_sec)
{
    memset(rec_sec, 0, sizeof(*rec_sec));
    CHECK(pel_encode_record_hash(json, json_len, rec_sec->digest) == PEL_OK);
    rec_sec->digest_alg = PEL_DIGEST_ALG_SHA256;
    rec_sec->record_major = PEL_ENCODE_RECORD_MAJOR;
}

/* Verify consumed encode record section matches provenance JSON and digest text. */
void hp_verify_consumed_record(const uint8_t *unwrapped, size_t unwrapped_len, const char *json,
                               size_t json_len, PelorusEncodeRecordSection *out_sec_rec)
{
    const void *rec_ptr = NULL;
    size_t rec_size = 0;
    char digest_text[PEL_DIGEST_TEXT_SIZE];
    const char *dj;

    CHECK(pel_blob_find_section(unwrapped, unwrapped_len, PEL_SEC_ENCODE_RECORD,
                                sizeof(PelorusEncodeRecordSection), &rec_ptr, &rec_size) == PEL_OK);
    CHECK(rec_ptr != NULL && rec_size >= sizeof(PelorusEncodeRecordSection));
    if (rec_ptr == NULL || rec_size < sizeof(PelorusEncodeRecordSection)) {
        return;
    }
    {
        const PelorusEncodeRecordSection *rec = rec_ptr;
        *out_sec_rec = *rec;
    }

    CHECK(pel_encode_record_digest_text(out_sec_rec, rec_size, digest_text, sizeof(digest_text)) ==
          PEL_OK);
    CHECK(memchr(digest_text, '\0', sizeof(digest_text)) == digest_text + DIGEST_TEXT_LEN);

    /* The record names its own digest; the consumer's text must match it byte for byte. */
    dj = strstr(json, JSON_DIGEST_KEY "sha256:");
    CHECK(dj != NULL);
    if (dj != NULL) {
        const char *json_digest = dj + (sizeof(JSON_DIGEST_KEY) - 1u);
        CHECK(strncmp(digest_text, json_digest, DIGEST_TEXT_LEN) == 0);
        CHECK(json_digest[DIGEST_TEXT_LEN] == '"');
    }
    CHECK(pel_encode_record_verify(json, json_len, out_sec_rec->digest) == PEL_OK);
}

/* Verify encode record verification fails on modified JSON, then recovers on restore. */
void hp_test_negative_flipped_json(char *json, size_t len,
                                   const uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE])
{
    CHECK(json != NULL && len > 5u);
    if (json == NULL || len <= 5u) {
        return;
    }
    json[2] = (char)((unsigned char)json[2] ^ 0x01u);
    CHECK(pel_encode_record_verify(json, len, digest) == PEL_ERR_MISMATCH);
    json[2] = (char)((unsigned char)json[2] ^ 0x01u);
    CHECK(pel_encode_record_verify(json, len, digest) == PEL_OK);
}

/* Verify encode record verification fails when section digest is corrupted. */
void hp_test_negative_flipped_digest(const char *json, size_t len,
                                     const PelorusEncodeRecordSection *sec)
{
    PelorusEncodeRecordSection flipped_sec = *sec;
    char flipped_text[PEL_DIGEST_TEXT_SIZE];
    const char *dj;

    flipped_sec.digest[0] ^= 0x01u;

    CHECK(pel_encode_record_digest_text(&flipped_sec, sizeof(flipped_sec), flipped_text,
                                        sizeof(flipped_text)) == PEL_OK);

    dj = strstr(json, JSON_DIGEST_KEY "sha256:");
    CHECK(dj != NULL);
    if (dj != NULL) {
        const char *json_digest = dj + (sizeof(JSON_DIGEST_KEY) - 1u);
        CHECK(strncmp(flipped_text, json_digest, DIGEST_TEXT_LEN) != 0);
    }

    CHECK(pel_encode_record_verify(json, len, flipped_sec.digest) == PEL_ERR_MISMATCH);
}

/* Verify record digest text extraction rejects undersized section buffer. */
void hp_test_negative_short_section_size(const PelorusEncodeRecordSection *sec)
{
    char out_text[PEL_DIGEST_TEXT_SIZE];
    CHECK(pel_encode_record_digest_text(sec, 8u, out_text, sizeof(out_text)) == PEL_ERR_INVALID);
}

/* NOLINTEND(modernize-use-nullptr) */
