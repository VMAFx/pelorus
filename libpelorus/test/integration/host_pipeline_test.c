/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef _WIN32
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700 /* NOLINT(bugprone-reserved-identifier,cert-dcl37-c,cert-dcl51-cpp) */
#endif
#endif

#include "pelorus/deband.h"
#include "pelorus/encode_options.h"
#include "pelorus/encode_record.h"
#include "pelorus/grain_convert.h"
#include "pelorus/grain_table.h"
#include "pelorus/interop.h"
#include "pelorus/pelorus.h"
#include "pelorus/telemetry.h"

#include "../test_float_bits.h"
#include "../test_path_safety.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

static int g_fail;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            g_fail++;                                                                              \
        }                                                                                          \
    } while (0)

typedef union AlignedScratch {
    uint64_t align;
    uint8_t bytes[8192];
} AlignedScratch;

#define FIXTURE_BUF_CAP ((size_t)1024u * (size_t)1024u)
#define READ_CHUNK_SIZE 4096u
#define MAX_READ_ITERS (FIXTURE_BUF_CAP / READ_CHUNK_SIZE)
#define TELEMETRY_FRAME_CAP 32u
#define EXPECTED_X265_FRAME_COUNT 10u
#define GRAIN_ENTRY_CAP 4u
/* PEL_FOURCC('P', 'L', 'R', 'S') as an unsigned literal: the macro's char literals promote to int
 * and trip bugprone-signed-bitwise at every expansion. */
#define PLRS_PRODUCER_ID 0x53524C50u
/* "sha256:" + 64 hex digits, without the terminating NUL. */
#define DIGEST_TEXT_LEN (PEL_DIGEST_TEXT_SIZE - 1u)
#define JSON_DIGEST_KEY "\"digest\":\""

/*
 * Grain round-trip bounds for libaom-3.15.2-fgtest1.tbl (libaom film-grain-test=1, the parameters
 * grain-av1-fgs.ivf carries). AV1 -> H.274 -> AV1 is lossy by design; each bound is the measured
 * value times 1.2, rounded up: compare loss 0.1876, luma sigma_rel_rms 0.0758, AV1 -> H.274 loss
 * 0.0970, H.274 -> AV1 loss 0.1125.
 */
#define GRAIN_RT_LOSS_MAX 0.226
#define GRAIN_RT_SIGMA_Y_MAX 0.091
#define GRAIN_TO_H274_LOSS_MAX 0.117
#define GRAIN_TO_AOM_LOSS_MAX 0.136

static int read_fixture_file(const char *path, char *buf, size_t cap, size_t *out_len)
{
    FILE *fp;
    size_t total = 0;
    size_t iters;

    if (path == NULL || buf == NULL || out_len == NULL || cap == 0u) {
        return -1;
    }
    *out_len = 0;
    fp = test_fopen_safe(path, "rb");
    if (fp == NULL) {
        return -1;
    }

    for (iters = 0; iters < MAX_READ_ITERS && total < cap; iters++) {
        size_t to_read = cap - total;
        size_t n;
        if (feof(fp) || ferror(fp)) {
            break;
        }
        if (to_read > READ_CHUNK_SIZE) {
            to_read = READ_CHUNK_SIZE;
        }
        n = fread(buf + total, 1u, to_read, fp);
        total += n;
        if (n < to_read || feof(fp) || ferror(fp)) {
            break;
        }
    }

    if (ferror(fp) || !feof(fp)) {
        (void)fclose(fp);
        return -1;
    }
    (void)fclose(fp);
    *out_len = total;
    return 0;
}

static void init_deband_filter(PelorusEncodeFilter *filter, PelorusEncodeParam d_params[2],
                               const char *v_range[1], const char *v_softness[1],
                               char s_range[PEL_ENCODE_RECORD_SCALAR_SIZE],
                               char s_softness[PEL_ENCODE_RECORD_SCALAR_SIZE])
{
    PelorusDebandParams deband;
    const char *what = NULL;

    pel_deband_params_default(&deband);
    CHECK(pel_deband_params_validate(&deband, &what) == PEL_OK);

    CHECK(pel_encode_record_int(deband.range, s_range) == PEL_OK);
    CHECK(pel_encode_record_f64((double)deband.softness, s_softness) == PEL_OK);

    v_range[0] = s_range;
    v_softness[0] = s_softness;

    d_params[0].key = "range";
    d_params[0].values = v_range;
    d_params[0].nb_values = 1u;
    d_params[0].is_array = 0u;

    d_params[1].key = "softness";
    d_params[1].values = v_softness;
    d_params[1].nb_values = 1u;
    d_params[1].is_array = 0u;

    memset(filter, 0, sizeof(*filter));
    filter->name = "pelorus_deband_vulkan";
    filter->library = "libpelorus";
    filter->version = pelorus_version_string();
    filter->params = d_params;
    filter->nb_params = 2u;
}

static void build_provenance_json(char **out_json, size_t *out_len)
{
    PelorusEncodeOptions *opts;
    char s_range[PEL_ENCODE_RECORD_SCALAR_SIZE];
    char s_softness[PEL_ENCODE_RECORD_SCALAR_SIZE];
    const char *v_range[1];
    const char *v_softness[1];
    PelorusEncodeParam d_params[2];
    PelorusEncodeFilter filter;
    PelorusEncodeRecordInput rec_in;
    const char *opt_str = "crf=22:preset=slow:aq-mode=3";
    size_t need = 0;
    size_t built = 0;
    char *json;

    opts = calloc(1u, sizeof(*opts));
    CHECK(opts != NULL);
    if (opts == NULL) {
        return;
    }
    CHECK(pel_encode_options_parse(PEL_ENCODE_OPTIONS_X265, opt_str, strlen(opt_str), opts) ==
          PEL_OK);

    init_deband_filter(&filter, d_params, v_range, v_softness, s_range, s_softness);

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

static void create_scenario1_record_section(const char *json, size_t json_len,
                                            PelorusEncodeRecordSection *rec_sec)
{
    uint8_t digest[32];

    CHECK(pel_encode_record_hash(json, json_len, digest) == PEL_OK);
    memset(rec_sec, 0, sizeof(*rec_sec));
    memcpy(rec_sec->digest, digest, sizeof(digest));
    rec_sec->digest_alg = PEL_DIGEST_ALG_SHA256;
    rec_sec->record_major = PEL_ENCODE_RECORD_MAJOR;
}

static int read_scenario1_grain(const char *grain_table_path, PelorusFilmGrainSection *out_grain0)
{
    char *tbl_buf = malloc(FIXTURE_BUF_CAP);
    size_t tbl_len = 0;
    PelorusGrainTableEntry entries[GRAIN_ENTRY_CAP];
    size_t grain_count = 0;
    pel_result r = PEL_ERR_INVALID;

    memset(entries, 0, sizeof(entries));
    CHECK(tbl_buf != NULL);
    if (tbl_buf == NULL) {
        return -1;
    }
    if (read_fixture_file(grain_table_path, tbl_buf, FIXTURE_BUF_CAP, &tbl_len) == 0) {
        r = pel_grain_table_parse(tbl_buf, tbl_len, entries, GRAIN_ENTRY_CAP, &grain_count);
    }
    free(tbl_buf);
    CHECK(r == PEL_OK);
    CHECK(grain_count == 1u);
    if (r != PEL_OK || grain_count != 1u) {
        return -1;
    }
    *out_grain0 = entries[0].params;
    return 0;
}

static void pack_scenario1_carrier(const uint8_t *blob, size_t blob_len, uint8_t **out_carrier,
                                   size_t *out_carrier_len)
{
    size_t c_need = 0;
    size_t c_len = 0;
    uint8_t *carrier;
    size_t i;

    CHECK(pel_blob_carrier_encode(blob, blob_len, NULL, 0u, &c_need) == PEL_ERR_RANGE);
    CHECK(c_need > 0u);
    if (c_need == 0u) {
        return;
    }
    carrier = malloc(c_need);
    CHECK(carrier != NULL);
    if (carrier == NULL) {
        return;
    }
    CHECK(pel_blob_carrier_encode(blob, blob_len, carrier, c_need, &c_len) == PEL_OK);
    CHECK(c_len == c_need);
    for (i = 0; i < c_len; i++) {
        CHECK(carrier[i] != 0x00u);
    }
    *out_carrier = carrier;
    *out_carrier_len = c_len;
}

static void produce_scenario1_blob(const char *grain_table_path, const char *json, size_t json_len,
                                   uint8_t **out_blob, size_t *out_blob_len, uint8_t **out_carrier,
                                   size_t *out_carrier_len, PelorusFilmGrainSection *out_grain0)
{
    PelorusEncodeRecordSection rec_sec;
    PelorusSideData meta;
    PelorusPackSection secs[2];

    create_scenario1_record_section(json, json_len, &rec_sec);
    if (read_scenario1_grain(grain_table_path, out_grain0) != 0) {
        return;
    }

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 42u;
    meta.bit_depth = 10u;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.producer_id = PLRS_PRODUCER_ID;

    secs[0].id = PEL_SEC_ENCODE_RECORD;
    secs[0].data = &rec_sec;
    secs[0].size = (uint32_t)sizeof(rec_sec);
    secs[1].id = PEL_SEC_FILMGRAIN;
    secs[1].data = out_grain0;
    secs[1].size = (uint32_t)sizeof(*out_grain0);

    CHECK(pel_blob_pack(&meta, secs, 2, out_blob, out_blob_len) == PEL_OK);
    CHECK(*out_blob != NULL);
    if (*out_blob == NULL) {
        return;
    }

    pack_scenario1_carrier(*out_blob, *out_blob_len, out_carrier, out_carrier_len);
}

static void verify_grain_roundtrip(const PelorusFilmGrainSection *grain_sec,
                                   PelorusFilmGrainSection *aom_rt)
{
    PelorusH274GrainParams h274;
    PelorusGrainFidelity fid_h;
    PelorusGrainFidelity fid_aom;
    PelorusGrainDescriptor desc_in;
    PelorusGrainDescriptor desc_rt;
    PelorusGrainFidelity fid_comp;

    memset(aom_rt, 0, sizeof(*aom_rt));
    CHECK(pel_grain_aom_to_h274(grain_sec, &h274, &fid_h) == PEL_OK);
    CHECK(pel_grain_h274_to_aom(&h274, grain_sec->seed, aom_rt, &fid_aom) == PEL_OK);
    CHECK(isfinite(fid_h.loss) && fid_h.loss > 0.0 && fid_h.loss < GRAIN_TO_H274_LOSS_MAX);
    CHECK(isfinite(fid_aom.loss) && fid_aom.loss > 0.0 && fid_aom.loss < GRAIN_TO_AOM_LOSS_MAX);

    CHECK(pel_grain_describe_aom(grain_sec, &desc_in) == PEL_OK);
    CHECK(pel_grain_describe_aom(aom_rt, &desc_rt) == PEL_OK);
    CHECK(pel_grain_compare(&desc_in, &desc_rt, &fid_comp) == PEL_OK);
    /* isfinite first: a NaN sigma drops out of the loss maximum and reads as zero loss. */
    CHECK(isfinite(fid_comp.sigma_rel_rms[0]) && fid_comp.sigma_rel_rms[0] < GRAIN_RT_SIGMA_Y_MAX);
    CHECK(isfinite(fid_comp.loss) && fid_comp.loss > 0.0 && fid_comp.loss < GRAIN_RT_LOSS_MAX);
}

static void check_grain_fields(const PelorusFilmGrainSection *grain_sec)
{
    /* libaom-3.15.2-fgtest1.tbl entry 0, as libdav1d exports it from grain-av1-fgs.ivf frame 0. */
    CHECK(grain_sec->apply == 1u);
    CHECK(grain_sec->seed == 45231u);
    CHECK(grain_sec->grain_model == PEL_GRAIN_AOM);
    CHECK(grain_sec->ar_coeff_lag == 2);
    CHECK(grain_sec->ar_coeff_shift == 8);
    CHECK(grain_sec->scaling_shift == 11);
    CHECK(grain_sec->num_y_points == 14);
    CHECK(grain_sec->num_uv_points[0] == 8);
    CHECK(grain_sec->num_uv_points[1] == 9);
    CHECK(grain_sec->uv_offset[0] == -238);
}

static void check_grain_rt_identity(const PelorusFilmGrainSection *grain_sec,
                                    const PelorusFilmGrainSection *aom_rt)
{
    /* The conversion is lossy, so only the identity fields must survive the round trip. */
    CHECK(aom_rt->grain_model == PEL_GRAIN_AOM);
    CHECK(aom_rt->seed == grain_sec->seed);
    CHECK(aom_rt->apply == grain_sec->apply);
}

static void verify_consumed_grain(const uint8_t *unwrapped, size_t unwrapped_len,
                                  const PelorusFilmGrainSection *expected_grain)
{
    const void *grain_ptr = NULL;
    size_t grain_size = 0;
    PelorusFilmGrainSection grain_sec;
    PelorusFilmGrainSection aom_rt;

    CHECK(pel_blob_find_section(unwrapped, unwrapped_len, PEL_SEC_FILMGRAIN,
                                sizeof(PelorusFilmGrainSection), &grain_ptr,
                                &grain_size) == PEL_OK);
    CHECK(grain_ptr != NULL && grain_size >= sizeof(PelorusFilmGrainSection));
    if (grain_ptr == NULL || grain_size < sizeof(PelorusFilmGrainSection)) {
        return;
    }
    memcpy(&grain_sec, grain_ptr, sizeof(grain_sec));
    CHECK(memcmp(&grain_sec, expected_grain, sizeof(grain_sec)) == 0);

    verify_grain_roundtrip(&grain_sec, &aom_rt);
    check_grain_fields(&grain_sec);
    check_grain_rt_identity(&grain_sec, &aom_rt);
}

static void verify_unwrapped_blob(const uint8_t *carrier, size_t carrier_len,
                                  AlignedScratch *scratch, const uint8_t *packed_blob,
                                  size_t packed_len, const uint8_t **out_unwrapped,
                                  size_t *out_unwrapped_len)
{
    PelorusSideData hdr;

    CHECK(carrier_len <= sizeof(scratch->bytes));
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch->bytes, sizeof(scratch->bytes),
                          out_unwrapped, out_unwrapped_len) == PEL_OK);
    CHECK(*out_unwrapped != NULL && *out_unwrapped_len == packed_len);
    if (*out_unwrapped == NULL || *out_unwrapped_len != packed_len ||
        packed_len < PELORUS_SIDEDATA_UUID_LEN + sizeof(hdr)) {
        *out_unwrapped = NULL;
        return;
    }
    CHECK(memcmp(*out_unwrapped, packed_blob, packed_len) == 0);
    CHECK(memcmp(*out_unwrapped, pelorus_sidedata_uuid, PELORUS_SIDEDATA_UUID_LEN) == 0);

    /* The header follows the UUID unaligned; copy it out instead of casting. */
    memcpy(&hdr, *out_unwrapped + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    CHECK(hdr.frame_pts == 42u);
    CHECK(hdr.bit_depth == 10u);
    CHECK(hdr.producer_id == PLRS_PRODUCER_ID);
}

static void verify_consumed_encode_record(const uint8_t *unwrapped, size_t unwrapped_len,
                                          const char *json, size_t json_len,
                                          PelorusEncodeRecordSection *out_sec_rec)
{
    const void *rec_ptr = NULL;
    size_t rec_size = 0;
    char digest_text[PEL_DIGEST_TEXT_SIZE];
    const char *dj;
    const char *json_digest;

    CHECK(pel_blob_find_section(unwrapped, unwrapped_len, PEL_SEC_ENCODE_RECORD,
                                sizeof(PelorusEncodeRecordSection), &rec_ptr, &rec_size) == PEL_OK);
    CHECK(rec_ptr != NULL && rec_size >= sizeof(PelorusEncodeRecordSection));
    if (rec_ptr == NULL || rec_size < sizeof(PelorusEncodeRecordSection)) {
        return;
    }
    memcpy(out_sec_rec, rec_ptr, sizeof(*out_sec_rec));

    CHECK(pel_encode_record_digest_text(out_sec_rec, rec_size, digest_text, sizeof(digest_text)) ==
          PEL_OK);
    CHECK(strlen(digest_text) == DIGEST_TEXT_LEN);
    /* The record names its own digest; the consumer's text must match it byte for byte. */
    dj = strstr(json, JSON_DIGEST_KEY "sha256:");
    CHECK(dj != NULL);
    if (dj != NULL) {
        json_digest = dj + strlen(JSON_DIGEST_KEY);
        CHECK(strncmp(digest_text, json_digest, DIGEST_TEXT_LEN) == 0);
        CHECK(json_digest[DIGEST_TEXT_LEN] == '"');
    }
    CHECK(pel_encode_record_verify(json, json_len, out_sec_rec->digest) == PEL_OK);
}

static void consume_scenario1_carrier(const uint8_t *carrier, size_t carrier_len,
                                      const uint8_t *packed_blob, size_t packed_len,
                                      const char *json, size_t json_len,
                                      const PelorusFilmGrainSection *expected_grain,
                                      PelorusEncodeRecordSection *out_sec_rec)
{
    AlignedScratch scratch;
    const uint8_t *unwrapped = NULL;
    size_t unwrapped_len = 0;

    verify_unwrapped_blob(carrier, carrier_len, &scratch, packed_blob, packed_len, &unwrapped,
                          &unwrapped_len);
    if (unwrapped != NULL && unwrapped_len > 0u) {
        verify_consumed_encode_record(unwrapped, unwrapped_len, json, json_len, out_sec_rec);
        verify_consumed_grain(unwrapped, unwrapped_len, expected_grain);
    }
}

static void test_negative_flipped_json(const char *json, size_t len, const uint8_t digest[32])
{
    char *mutated;

    CHECK(json != NULL && len > 5u);
    if (len <= 5u) {
        return;
    }
    mutated = malloc(len + 1u);
    CHECK(mutated != NULL);
    if (mutated == NULL) {
        return;
    }
    memcpy(mutated, json, len + 1u);
    mutated[2] = (char)((unsigned char)mutated[2] ^ 0x01u);
    CHECK(pel_encode_record_verify(mutated, len, digest) == PEL_ERR_MISMATCH);
    free(mutated);
}

static void test_negative_flipped_digest(const char *json, size_t len,
                                         const PelorusEncodeRecordSection *sec)
{
    PelorusEncodeRecordSection flipped_sec;
    char flipped_text[PEL_DIGEST_TEXT_SIZE];
    const char *dj;
    const char *json_digest;

    memcpy(&flipped_sec, sec, sizeof(flipped_sec));
    flipped_sec.digest[0] ^= 0x01u;

    CHECK(pel_encode_record_digest_text(&flipped_sec, sizeof(flipped_sec), flipped_text,
                                        sizeof(flipped_text)) == PEL_OK);

    dj = strstr(json, JSON_DIGEST_KEY "sha256:");
    CHECK(dj != NULL);
    if (dj != NULL) {
        json_digest = dj + strlen(JSON_DIGEST_KEY);
        CHECK(strncmp(flipped_text, json_digest, DIGEST_TEXT_LEN) != 0);
    }

    CHECK(pel_encode_record_verify(json, len, flipped_sec.digest) == PEL_ERR_MISMATCH);
}

static void test_negative_carrier_corrupt(const uint8_t *carrier, size_t carrier_len)
{
    AlignedScratch scratch;
    const uint8_t *out = NULL;
    size_t out_len = 0;
    uint8_t *corrupted;

    CHECK(carrier_len > 17u);
    if (carrier_len <= 17u) {
        return;
    }
    corrupted = malloc(carrier_len + 1u);
    CHECK(corrupted != NULL);
    if (corrupted == NULL) {
        return;
    }

    /* A 0x00 byte cannot occur in a COBS stream. */
    memcpy(corrupted, carrier, 17u);
    corrupted[17] = 0x00u;
    memcpy(corrupted + 18, carrier + 17, carrier_len - 17u);
    CHECK(pel_blob_unwrap(corrupted, carrier_len + 1u, scratch.bytes, sizeof(scratch.bytes), &out,
                          &out_len) == PEL_ERR_ABI);
    CHECK(out == NULL);

    /* A foreign UUID is no Pelorus carrier at all. */
    memcpy(corrupted, carrier, carrier_len);
    corrupted[0] = (uint8_t)(corrupted[0] ^ 0x01u);
    CHECK(pel_blob_unwrap(corrupted, carrier_len, scratch.bytes, sizeof(scratch.bytes), &out,
                          &out_len) == PEL_ERR_ABSENT);
    free(corrupted);

    /* Cut inside the first COBS block: its code byte runs past the end. */
    CHECK(pel_blob_unwrap(carrier, PELORUS_SIDEDATA_UUID_LEN + 4u, scratch.bytes,
                          sizeof(scratch.bytes), &out, &out_len) == PEL_ERR_ABI);
    CHECK(out == NULL);
    /* Shorter than the UUID. */
    CHECK(pel_blob_unwrap(carrier, PELORUS_SIDEDATA_UUID_LEN - 1u, scratch.bytes,
                          sizeof(scratch.bytes), &out, &out_len) == PEL_ERR_ABSENT);
    CHECK(out == NULL);
}

static void test_negative_short_section_size(const PelorusEncodeRecordSection *sec)
{
    char out_text[PEL_DIGEST_TEXT_SIZE];
    CHECK(pel_encode_record_digest_text(sec, 8u, out_text, sizeof(out_text)) == PEL_ERR_INVALID);
}

static void test_negative_header_only_blob(void)
{
    PelorusSideData meta;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *ptr = NULL;
    size_t sz = 0;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 100u;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 10u;
    meta.producer_id = PLRS_PRODUCER_ID;

    CHECK(pel_blob_pack(&meta, NULL, 0, &blob, &len) == PEL_OK);
    CHECK(blob != NULL);
    if (blob != NULL) {
        CHECK(pel_blob_find_section(blob, len, PEL_SEC_ENCODE_RECORD,
                                    sizeof(PelorusEncodeRecordSection), &ptr,
                                    &sz) == PEL_ERR_ABSENT);
        pel_blob_free(blob);
    }
}

static void test_boundary_carrier_encode(const uint8_t *blob, size_t blob_len)
{
    size_t need = 0;
    uint8_t *buf;
    size_t out_len = 0;

    CHECK(pel_blob_carrier_encode(blob, blob_len, NULL, 0u, &need) == PEL_ERR_RANGE);
    CHECK(need > 0u);
    if (need == 0u) {
        return;
    }

    buf = malloc(need);
    CHECK(buf != NULL);
    if (buf == NULL) {
        return;
    }

    CHECK(pel_blob_carrier_encode(blob, blob_len, buf, need - 1u, &out_len) == PEL_ERR_RANGE);
    CHECK(out_len == need);

    out_len = 0;
    CHECK(pel_blob_carrier_encode(blob, blob_len, buf, need, &out_len) == PEL_OK);
    CHECK(out_len == need);

    free(buf);
}

static void test_boundary_unwrap_scratch(const uint8_t *carrier, size_t carrier_len)
{
    AlignedScratch scratch;
    const uint8_t *out = NULL;
    size_t out_len = 0;

    CHECK(carrier_len <= sizeof(scratch.bytes));
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch.bytes, carrier_len - 1u, &out, &out_len) ==
          PEL_ERR_RANGE);

    out = NULL;
    out_len = 0;
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch.bytes, carrier_len, &out, &out_len) ==
          PEL_OK);
    CHECK(out != NULL);
}

static void test_boundary_telemetry_pack(const PelorusEncTelemetryInput *frame)
{
    PelorusSideData meta;
    size_t need = 0;
    uint8_t *buf;
    size_t out_len = 0;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 0u;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 8u;
    meta.producer_id = PLRS_PRODUCER_ID;

    CHECK(pel_enc_telemetry_blob_size(frame, &need) == PEL_OK);
    CHECK(need > 0u);
    if (need == 0u) {
        return;
    }

    buf = malloc(need);
    CHECK(buf != NULL);
    if (buf == NULL) {
        return;
    }

    CHECK(pel_enc_telemetry_pack(&meta, frame, buf, need - 1u, &out_len) == PEL_ERR_RANGE);
    CHECK(out_len == need);

    out_len = 0;
    CHECK(pel_enc_telemetry_pack(&meta, frame, buf, need, &out_len) == PEL_OK);
    CHECK(out_len == need);

    free(buf);
}

static void check_telemetry_headers(const PelorusEncTelemetrySection *got,
                                    const PelorusEncTelemetrySection *exp)
{
    CHECK(got->present_mask == exp->present_mask);
    CHECK(got->codec == exp->codec);
    CHECK(got->qp_scale == exp->qp_scale);
    CHECK(got->granularity == exp->granularity);
    CHECK(got->adapter == exp->adapter);
    CHECK(got->block_size_log2 == exp->block_size_log2);
    CHECK(got->metric_source == exp->metric_source);
}

static void check_telemetry_indices_and_bytes(const PelorusEncTelemetrySection *got,
                                              const PelorusEncTelemetrySection *exp)
{
    uint64_t mask = exp->present_mask;

    if (mask & PEL_TLM_F_DISPLAY_INDEX) {
        CHECK(got->display_index == exp->display_index);
    }
    if (mask & PEL_TLM_F_DECODE_INDEX) {
        CHECK(got->decode_index == exp->decode_index);
    }
    if (mask & PEL_TLM_F_FRAME_BYTES) {
        CHECK(got->frame_bytes == exp->frame_bytes);
    }
    if (mask & PEL_TLM_F_HEADER_BITS) {
        CHECK(got->header_bits == exp->header_bits);
    }
    if (mask & PEL_TLM_F_RESIDUAL_BITS) {
        CHECK(got->residual_bits == exp->residual_bits);
    }
}

static void check_telemetry_metrics(const PelorusEncTelemetrySection *got,
                                    const PelorusEncTelemetrySection *exp)
{
    uint64_t mask = exp->present_mask;

    if (mask & PEL_TLM_F_AVG_QP) {
        CHECK(float_bits_equal(got->avg_qp, exp->avg_qp));
    }
    if (mask & PEL_TLM_F_AVG_QP_NORM) {
        CHECK(float_bits_equal(got->avg_qp_norm, exp->avg_qp_norm));
    }
    if (mask & PEL_TLM_F_PSNR_Y) {
        CHECK(float_bits_equal(got->psnr_y, exp->psnr_y));
    }
    if (mask & PEL_TLM_F_PSNR_U) {
        CHECK(float_bits_equal(got->psnr_u, exp->psnr_u));
    }
    if (mask & PEL_TLM_F_PSNR_V) {
        CHECK(float_bits_equal(got->psnr_v, exp->psnr_v));
    }
    if (mask & PEL_TLM_F_SSIM_Y) {
        CHECK(float_bits_equal(got->ssim_y, exp->ssim_y));
    }
}

static void check_telemetry_fractions_and_flags(const PelorusEncTelemetrySection *got,
                                                const PelorusEncTelemetrySection *exp)
{
    uint64_t mask = exp->present_mask;

    if (mask & PEL_TLM_F_INTRA_FRACTION) {
        CHECK(float_bits_equal(got->intra_fraction, exp->intra_fraction));
    }
    if (mask & PEL_TLM_F_INTER_FRACTION) {
        CHECK(float_bits_equal(got->inter_fraction, exp->inter_fraction));
    }
    if (mask & PEL_TLM_F_SKIP_FRACTION) {
        CHECK(float_bits_equal(got->skip_fraction, exp->skip_fraction));
    }
    if (mask & PEL_TLM_F_PICTURE_TYPE) {
        CHECK(got->picture_type == exp->picture_type);
    }
    if (mask &
        (PEL_TLM_F_KEY_FRAME | PEL_TLM_F_REFERENCE | PEL_TLM_F_SHOWN | PEL_TLM_F_SCENE_CUT)) {
        CHECK(got->frame_flags == exp->frame_flags);
    }
    if (mask & PEL_TLM_F_CODED_BIT_DEPTH) {
        CHECK(got->coded_bit_depth == exp->coded_bit_depth);
    }
}

static void check_telemetry_section_match(const PelorusEncTelemetrySection *got,
                                          const PelorusEncTelemetrySection *exp)
{
    check_telemetry_headers(got, exp);
    check_telemetry_indices_and_bytes(got, exp);
    check_telemetry_metrics(got, exp);
    check_telemetry_fractions_and_flags(got, exp);
}

static void run_single_telemetry_frame(const PelorusEncTelemetryInput *frame, uint8_t *blob_buf,
                                       size_t max_blob_size, uint8_t *carrier_buf,
                                       size_t max_carrier_size)
{
    PelorusSideData meta;
    size_t blob_len = 0;
    size_t carrier_len = 0;
    AlignedScratch unwrap_scratch;
    const uint8_t *unwrapped = NULL;
    size_t unwrapped_len = 0;
    const void *sec_ptr = NULL;
    size_t sec_size = 0;
    PelorusEncTelemetrySection got_sec;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = frame->frame.display_index;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 8u;
    meta.producer_id = PLRS_PRODUCER_ID;

    CHECK(pel_enc_telemetry_pack(&meta, frame, blob_buf, max_blob_size, &blob_len) == PEL_OK);
    CHECK(blob_len > 0u);

    CHECK(pel_blob_carrier_encode(blob_buf, blob_len, carrier_buf, max_carrier_size,
                                  &carrier_len) == PEL_OK);
    CHECK(carrier_len > 0u);

    CHECK(pel_blob_unwrap(carrier_buf, carrier_len, unwrap_scratch.bytes,
                          sizeof(unwrap_scratch.bytes), &unwrapped, &unwrapped_len) == PEL_OK);
    CHECK(unwrapped != NULL && unwrapped_len == blob_len);
    if (unwrapped == NULL) {
        return;
    }
    CHECK(memcmp(unwrapped, blob_buf, blob_len) == 0);

    CHECK(pel_blob_find_section(unwrapped, unwrapped_len, PEL_SEC_ENC_TELEMETRY,
                                sizeof(PelorusEncTelemetrySection), &sec_ptr, &sec_size) == PEL_OK);
    CHECK(sec_ptr != NULL && sec_size >= sizeof(PelorusEncTelemetrySection));
    if (sec_ptr == NULL || sec_size < sizeof(PelorusEncTelemetrySection)) {
        return;
    }

    memcpy(&got_sec, sec_ptr, sizeof(got_sec));
    check_telemetry_section_match(&got_sec, &frame->frame);
}

static size_t find_max_telemetry_blob_size(const PelorusEncTelemetryInput *frames, size_t count)
{
    size_t max_blob_size = 0;
    size_t i;

    for (i = 0; i < count && i < TELEMETRY_FRAME_CAP; i++) {
        size_t sz = 0;
        CHECK(pel_enc_telemetry_blob_size(&frames[i], &sz) == PEL_OK);
        if (sz > max_blob_size) {
            max_blob_size = sz;
        }
    }
    return max_blob_size;
}

static int parse_telemetry_fixture(const char *csv_path, PelorusEncTelemetryInput *frames,
                                   size_t *out_count)
{
    char *csv_text = malloc(FIXTURE_BUF_CAP);
    size_t csv_len = 0;
    size_t frame_count = 0;

    CHECK(csv_text != NULL);
    if (csv_text == NULL) {
        return -1;
    }
    CHECK(read_fixture_file(csv_path, csv_text, FIXTURE_BUF_CAP, &csv_len) == 0);
    CHECK(pel_tlm_parse_x265_csv(csv_text, csv_len, 8u, frames, TELEMETRY_FRAME_CAP,
                                 &frame_count) == PEL_OK);
    CHECK(frame_count == EXPECTED_X265_FRAME_COUNT);
    free(csv_text);
    *out_count = frame_count;
    return (frame_count == EXPECTED_X265_FRAME_COUNT) ? 0 : -1;
}

static void test_scenario2_telemetry(const char *csv_path)
{
    PelorusEncTelemetryInput frames[TELEMETRY_FRAME_CAP];
    size_t frame_count = 0;
    size_t max_blob_size = 0;
    uint8_t *blob_buf;
    uint8_t *carrier_buf;
    size_t max_carrier_size;
    size_t i;

    memset(frames, 0, sizeof(frames));
    if (parse_telemetry_fixture(csv_path, frames, &frame_count) != 0) {
        return;
    }

    max_blob_size = find_max_telemetry_blob_size(frames, frame_count);
    CHECK(max_blob_size > 0u);
    if (max_blob_size == 0u) {
        return;
    }
    blob_buf = malloc(max_blob_size);
    CHECK(blob_buf != NULL);
    max_carrier_size = PEL_CARRIER_MAX_LEN(max_blob_size);
    carrier_buf = malloc(max_carrier_size);
    CHECK(carrier_buf != NULL);

    if (blob_buf != NULL && carrier_buf != NULL) {
        for (i = 0; i < frame_count && i < TELEMETRY_FRAME_CAP; i++) {
            run_single_telemetry_frame(&frames[i], blob_buf, max_blob_size, carrier_buf,
                                       max_carrier_size);
        }
        test_boundary_telemetry_pack(&frames[0]);
    }
    free(blob_buf);
    free(carrier_buf);
}

int main(int argc, char **argv)
{
    const char *grain_table_path;
    const char *x265_csv_path;
    char *json = NULL;
    size_t json_len = 0;
    uint8_t *packed_blob = NULL;
    size_t packed_len = 0;
    uint8_t *carrier = NULL;
    size_t carrier_len = 0;
    PelorusFilmGrainSection expected_grain;
    PelorusEncodeRecordSection sec_rec;

    if (argc < 3 || argv[1] == NULL || argv[2] == NULL) {
        (void)fprintf(stderr, "usage: %s <grain.tbl> <x265.csv>\n", argc > 0 ? argv[0] : "test");
        return 1;
    }
    grain_table_path = argv[1];
    x265_csv_path = argv[2];
    memset(&expected_grain, 0, sizeof(expected_grain));
    memset(&sec_rec, 0, sizeof(sec_rec));

    build_provenance_json(&json, &json_len);
    CHECK(json != NULL && json_len > 0u);
    if (json != NULL && json_len > 0u) {
        produce_scenario1_blob(grain_table_path, json, json_len, &packed_blob, &packed_len,
                               &carrier, &carrier_len, &expected_grain);
        CHECK(packed_blob != NULL && carrier != NULL);
        if (packed_blob != NULL && carrier != NULL && packed_len > 0u && carrier_len > 0u) {
            consume_scenario1_carrier(carrier, carrier_len, packed_blob, packed_len, json, json_len,
                                      &expected_grain, &sec_rec);

            test_negative_flipped_json(json, json_len, sec_rec.digest);
            test_negative_flipped_digest(json, json_len, &sec_rec);
            test_negative_carrier_corrupt(carrier, carrier_len);
            test_negative_short_section_size(&sec_rec);
            test_negative_header_only_blob();

            test_boundary_carrier_encode(packed_blob, packed_len);
            test_boundary_unwrap_scratch(carrier, carrier_len);
        }
        free(carrier);
        pel_blob_free(packed_blob);
        free(json);
    }

    test_scenario2_telemetry(x265_csv_path);

    if (g_fail != 0) {
        (void)fprintf(stderr, "host-pipeline: %d checks failed\n", g_fail);
        return 1;
    }
    return 0;
}

/* NOLINTEND(modernize-use-nullptr) */
