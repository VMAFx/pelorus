/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * encode_record_test.c — the encode provenance record (ADR-0175,
 * docs/api/encode-record.md) and the SHA-256 port behind it.
 *
 *   - SHA-256: the FIPS 180-4 example messages, one million 'a', the padding
 *     boundaries 55..120 bytes and streamed chunks (expected digests from
 *     Python's hashlib, the same vectors as VMAFx core/test/test_vmafx_sha256.c);
 *   - the worked example: its canonical text, its digest (recomputed by the
 *     page's own Python snippet), the `range` mutation digest, both from an
 *     embedded copy and from the page itself (argv[1]);
 *   - build == canonicalize, option order, every parameter change, unknown keys,
 *     a dropped key, a tampered value, and the bounds of the canonical form.
 */

#include "pelorus/encode_record.h"

#include "sha256.h"

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

#define TEXT_CAP 4096u
#define DOC_CAP 65536u

static void to_hex(const uint8_t digest[32], char hex[65])
{
    static const char digits[] = "0123456789abcdef";
    size_t i;

    for (i = 0; i < 32u; i++) {
        const unsigned byte = digest[i];
        hex[2u * i] = digits[byte >> 4u];
        hex[2u * i + 1u] = digits[byte & 0x0Fu];
    }
    hex[64] = '\0';
}

static int sha_matches(const void *data, size_t len, const char *want_hex)
{
    uint8_t digest[32];
    char hex[65];

    if (pel_sha256(data, len, digest) != PEL_OK) {
        return 0;
    }
    to_hex(digest, hex);
    return strcmp(hex, want_hex) == 0;
}

/* ---- SHA-256 (FIPS 180-4) --------------------------------------------- */

typedef struct ShaCase {
    size_t len;
    const char *hex;
} ShaCase;

/* Message byte i is (i * 31 + 7) & 0xff; the length field moves to a second block at 56. */
static const ShaCase boundary_cases[] = {
    {55, "8aa994584139d128848eeebc4e815639ba5ab6e6e39574195a63ac4f14f7c43b"},
    {56, "ad574708f75c044c9b85de64cb568ee7711ff4f36448c6242f053ba8f6cc2b63"},
    {63, "280ed3e8ff1df845b2e7dfe6ac6cee817bef20e783cc65abc41b818b4d2fe076"},
    {64, "c6ab9724ade5b6a7a1edfffb12f3aa9181351355af8fd08c919952ad211339dd"},
    {65, "788367c73c7ddf4c53f65e68cc0d943e6227ab55b0e78ba63ace822b1c6301c0"},
    {119, "3d610547d68216dedf7435a4fb6260353911f6b3fd3f18805ddb8be285d726fe"},
    {120, "1f80156a804cb7862ad113e8200e9d74499723e7c7854d5f48776d3148e09656"},
};

static void test_sha256_fips(void)
{
    static const char m896[] = "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmn"
                               "hijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu";
    static const char m448[] = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    char block[1000];
    uint8_t digest[32];
    char hex[65];
    PelSha256 sha;
    size_t i;

    CHECK(sha_matches(NULL, 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    CHECK(
        sha_matches("abc", 3, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    CHECK(sha_matches(m448, sizeof(m448) - 1u,
                      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));
    CHECK(sha_matches(m896, sizeof(m896) - 1u,
                      "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"));
    /* One million 'a', streamed as 1000 blocks of 1000 (no allocation). */
    memset(block, 'a', sizeof(block));
    pel_sha256_init(&sha);
    for (i = 0; i < 1000u; i++) {
        CHECK(pel_sha256_update(&sha, block, sizeof(block)) == PEL_OK);
    }
    pel_sha256_final(&sha, digest);
    to_hex(digest, hex);
    CHECK(strcmp(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0") == 0);
}

/* NULL data is the empty message only at length 0; with a length it is an error and
 * hashes nothing (c-reviewer: it must never pass for the empty digest). */
static void test_sha256_null_data(void)
{
    static const char empty_hex[] =
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    uint8_t digest[32];
    char hex[65];
    PelSha256 sha;

    memset(digest, 0xA5, sizeof(digest));
    CHECK(pel_sha256(NULL, 5, digest) == PEL_ERR_INVALID);
    CHECK(digest[0] == 0xA5 && digest[31] == 0xA5); /* untouched */
    CHECK(sha_matches(NULL, 0, empty_hex));
    pel_sha256_init(&sha);
    CHECK(pel_sha256_update(&sha, NULL, 3) == PEL_ERR_INVALID);
    CHECK(pel_sha256_update(&sha, NULL, 0) == PEL_OK);
    CHECK(pel_sha256_update(&sha, "abc", 3) == PEL_OK);
    pel_sha256_final(&sha, digest);
    to_hex(digest, hex); /* the rejected update added nothing: still SHA-256("abc") */
    CHECK(strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") == 0);
}

/* `message[0..len)` fed in `chunk`-byte pieces gives `want_hex`. */
static int streamed_matches(const uint8_t *message, size_t len, size_t chunk, const char *want_hex)
{
    PelSha256 sha;
    uint8_t digest[32];
    char hex[65];
    size_t at;
    int ok = 1;

    pel_sha256_init(&sha);
    for (at = 0; at < len; at += chunk) {
        const size_t left = len - at;
        if (pel_sha256_update(&sha, message + at, left < chunk ? left : chunk) != PEL_OK) {
            ok = 0;
        }
    }
    pel_sha256_final(&sha, digest);
    to_hex(digest, hex);
    return ok && strcmp(hex, want_hex) == 0;
}

static void test_sha256_boundaries(void)
{
    static const size_t chunk_sizes[] = {1, 7, 63, 64, 65, 120};
    uint8_t message[120];
    size_t i;
    size_t c;

    for (i = 0; i < sizeof(message); i++) {
        message[i] = (uint8_t)((i * 31u + 7u) & 0xffu);
    }
    for (i = 0; i < sizeof(boundary_cases) / sizeof(boundary_cases[0]); i++) {
        CHECK(sha_matches(message, boundary_cases[i].len, boundary_cases[i].hex));
        for (c = 0; c < sizeof(chunk_sizes) / sizeof(chunk_sizes[0]); c++) {
            CHECK(streamed_matches(message, boundary_cases[i].len, chunk_sizes[c],
                                   boundary_cases[i].hex));
        }
    }
}

/* ---- Scalars ------------------------------------------------------------ */

static void test_scalar_int(void)
{
    char out[PEL_ENCODE_RECORD_SCALAR_SIZE];

    CHECK(pel_encode_record_int(16, out) == PEL_OK && strcmp(out, "16") == 0);
    CHECK(pel_encode_record_int(0, out) == PEL_OK && strcmp(out, "0") == 0);
    CHECK(pel_encode_record_int(-3, out) == PEL_OK && strcmp(out, "-3") == 0);
    CHECK(pel_encode_record_int(INT64_MIN, out) == PEL_OK &&
          strcmp(out, "-9223372036854775808") == 0);
    CHECK(pel_encode_record_int(INT64_MAX, out) == PEL_OK &&
          strcmp(out, "9223372036854775807") == 0);
    CHECK(pel_encode_record_int(1, NULL) == PEL_ERR_INVALID);
}

static void test_scalar_f64(void)
{
    char out[PEL_ENCODE_RECORD_SCALAR_SIZE];

    CHECK(pel_encode_record_f64(24.0, out) == PEL_OK && strcmp(out, "f64:4038000000000000") == 0);
    CHECK(pel_encode_record_f64((double)0.35f, out) == PEL_OK &&
          strcmp(out, "f64:3fd6666660000000") == 0);
    CHECK(pel_encode_record_f64((double)0.2f, out) == PEL_OK &&
          strcmp(out, "f64:3fc99999a0000000") == 0);
    CHECK(pel_encode_record_f64(-0.0, out) == PEL_OK && strcmp(out, "f64:0000000000000000") == 0);
    CHECK(pel_encode_record_f64(-1.5, out) == PEL_OK && strcmp(out, "f64:bff8000000000000") == 0);
    CHECK(pel_encode_record_f64(strtod("nan", NULL), out) == PEL_ERR_RANGE);
    CHECK(pel_encode_record_f64(strtod("inf", NULL), out) == PEL_ERR_RANGE);
    CHECK(pel_encode_record_f64(1.0, NULL) == PEL_ERR_INVALID);
}

/* ---- The worked example (docs/api/encode-record.md) ---------------------- */

static const char worked_json[] =
    "{\n"
    "  \"digest\": \"sha256:b0201cec9782a58a59541103a7757fbd8221f67bd45a32f3b6a8d7f054d244bc\",\n"
    "  \"elapsed_ns\": \"8123456789\",\n"
    "  \"encoder\": {\n"
    "    \"codec\": \"hevc\",\n"
    "    \"name\": \"libx265\",\n"
    "    \"params\": {\"aq-mode\": \"3\", \"bframes\": \"4\", \"crf\": \"f64:4038000000000000\", "
    "\"preset\": \"slow\"},\n"
    "    \"unknown_params\": {\"some-new-option\": \"1\"},\n"
    "    \"version\": \"4.1\"\n"
    "  },\n"
    "  \"filters\": [\n"
    "    {\n"
    "      \"library\": \"libpelorus\",\n"
    "      \"name\": \"pelorus_deband_vulkan\",\n"
    "      \"params\": {\n"
    "        \"range\": \"16\",\n"
    "        \"thr\": [\"f64:3fd6666660000000\", \"f64:3fc99999a0000000\", "
    "\"f64:3fc99999a0000000\", \"f64:0000000000000000\"]\n"
    "      },\n"
    "      \"unknown_params\": {},\n"
    "      \"version\": \"0.3.0\"\n"
    "    }\n"
    "  ],\n"
    "  \"hardware\": {\"device\": \"example GPU\", \"path\": \"vulkan\"},\n"
    "  \"input\": {\"digest\": "
    "\"sha256:6d7233c7036e18b055f7861cd3c9f534093477b15efe8f58a80fecce4993c2ae\"},\n"
    "  \"schema\": \"pelorus/encode-record/1\"\n"
    "}\n";

/* The canonical text of the whole record, 705 bytes (Python json.dumps, sort_keys). */
static const char worked_canonical[] =
    "{\"digest\":\"sha256:b0201cec9782a58a59541103a7757fbd8221f67bd45a32f3b6a8d7f054d244bc\","
    "\"elapsed_ns\":\"8123456789\",\"encoder\":{\"codec\":\"hevc\",\"name\":\"libx265\","
    "\"params\":{\"aq-mode\":\"3\",\"bframes\":\"4\",\"crf\":\"f64:4038000000000000\","
    "\"preset\":\"slow\"},\"unknown_params\":{\"some-new-option\":\"1\"},\"version\":\"4.1\"},"
    "\"filters\":[{\"library\":\"libpelorus\",\"name\":\"pelorus_deband_vulkan\",\"params\":{"
    "\"range\":\"16\",\"thr\":[\"f64:3fd6666660000000\",\"f64:3fc99999a0000000\","
    "\"f64:3fc99999a0000000\",\"f64:0000000000000000\"]},\"unknown_params\":{},"
    "\"version\":\"0.3.0\"}],\"hardware\":{\"device\":\"example GPU\",\"path\":\"vulkan\"},"
    "\"input\":{\"digest\":"
    "\"sha256:6d7233c7036e18b055f7861cd3c9f534093477b15efe8f58a80fecce4993c2ae\"},"
    "\"schema\":\"pelorus/encode-record/1\"}";

static const char worked_digest[] =
    "b0201cec9782a58a59541103a7757fbd8221f67bd45a32f3b6a8d7f054d244bc";
static const char range15_digest[] =
    "f603fbf21c0423dbc7a353da631239283f88e1239c8a913707369441a39cb804";

static int hash_hex_is(const char *json, size_t len, const char *want_hex)
{
    uint8_t digest[32];
    char hex[65];

    if (pel_encode_record_hash(json, len, digest) != PEL_OK) {
        return 0;
    }
    to_hex(digest, hex);
    return strcmp(hex, want_hex) == 0;
}

/* Copy `src` into `dst` with the first `from` replaced by `to` (test-only text surgery). */
static size_t replace_once(char *dst, size_t cap, const char *src, const char *from, const char *to)
{
    const char *hit = strstr(src, from);
    const size_t head = hit != NULL ? (size_t)(hit - src) : 0u;
    const size_t tail = hit != NULL ? strlen(hit + strlen(from)) : 0u;
    const size_t len = head + strlen(to) + tail;

    CHECK(hit != NULL && len < cap);
    if (hit == NULL || len >= cap) {
        dst[0] = '\0';
        return 0;
    }
    memcpy(dst, src, head);
    memcpy(dst + head, to, strlen(to));
    memcpy(dst + head + strlen(to), hit + strlen(from), tail + 1u);
    return len;
}

static void check_worked_record(const char *json, size_t len)
{
    char text[TEXT_CAP];
    size_t out_len = 0;

    CHECK(pel_encode_record_canonicalize(json, len, text, sizeof(text), &out_len) == PEL_OK);
    CHECK(out_len == 705u && strcmp(text, worked_canonical) == 0);
    CHECK(hash_hex_is(json, len, worked_digest));
    CHECK(pel_encode_record_verify(json, len, NULL) == PEL_OK);
    /* Canonical text is a fixed point, and its digest is the same. */
    CHECK(pel_encode_record_canonicalize(text, out_len, text + 1024, sizeof(text) - 1024u,
                                         &out_len) == PEL_OK);
    CHECK(strcmp(text + 1024, worked_canonical) == 0);
    CHECK(hash_hex_is(worked_canonical, sizeof(worked_canonical) - 1u, worked_digest));
}

static void test_worked_example(void)
{
    char mutated[TEXT_CAP];
    uint8_t digest[32];
    size_t n;

    check_worked_record(worked_json, sizeof(worked_json) - 1u);
    /* Changing `range` to "15" gives the page's second digest and fails verification. */
    n = replace_once(mutated, sizeof(mutated), worked_json, "\"range\": \"16\"",
                     "\"range\": \"15\"");
    CHECK(hash_hex_is(mutated, n, range15_digest));
    CHECK(pel_encode_record_verify(mutated, n, NULL) == PEL_ERR_MISMATCH);
    /* elapsed_ns is outside the digest: changing it still verifies. */
    n = replace_once(mutated, sizeof(mutated), worked_json, "8123456789", "1");
    CHECK(pel_encode_record_verify(mutated, n, NULL) == PEL_OK);
    /* An expected digest is compared too. */
    CHECK(pel_encode_record_hash(worked_json, sizeof(worked_json) - 1u, digest) == PEL_OK);
    CHECK(pel_encode_record_verify(worked_json, sizeof(worked_json) - 1u, digest) == PEL_OK);
    digest[31] ^= 1u;
    CHECK(pel_encode_record_verify(worked_json, sizeof(worked_json) - 1u, digest) ==
          PEL_ERR_MISMATCH);
}

/* Find the page's JSON block and check it as the worked record. */
static void check_doc_worked_example(const char *doc)
{
    const char *start;
    const char *end;

    start = strstr(doc, "### Worked example\n\n```json\n");
    CHECK(start != NULL);
    if (start == NULL) {
        return;
    }
    start += strlen("### Worked example\n\n```json\n");
    end = strstr(start, "\n```");
    CHECK(end != NULL);
    if (end != NULL) {
        check_worked_record(start, (size_t)(end - start));
    }
}

/* The page's own JSON block (argv[1] = docs/api/encode-record.md) hashes to the digest
 * the page states, so the specification and the code cannot drift apart. */
static void test_worked_example_from_doc(const char *doc_path)
{
    char *doc = malloc(DOC_CAP);
    size_t n;
    FILE *fp = fopen(doc_path, "rb");

    CHECK(fp != NULL && doc != NULL);
    if (fp == NULL || doc == NULL) {
        if (fp != NULL) {
            CHECK(fclose(fp) == 0);
        }
        free(doc);
        return;
    }
    n = fread(doc, 1, DOC_CAP - 1u, fp);
    CHECK(fclose(fp) == 0);
    doc[n] = '\0';
    check_doc_worked_example(doc);
    free(doc);
}

/* ---- Builder ------------------------------------------------------------ */

static const char *const v_aq[] = {"3"};
static const char *const v_bf[] = {"4"};
static const char *const v_crf[] = {"f64:4038000000000000"};
static const char *const v_slow[] = {"slow"};
static const char *const v_one[] = {"1"};
static const char *const v_range[] = {"16"};
static const char *const v_thr[] = {"f64:3fd6666660000000", "f64:3fc99999a0000000",
                                    "f64:3fc99999a0000000", "f64:0000000000000000"};

/* Deliberately not in key order: the builder sorts (#81, option order does not matter). */
static const PelorusEncodeParam enc_params[] = {
    {"preset", v_slow, 1, 0},
    {"crf", v_crf, 1, 0},
    {"bframes", v_bf, 1, 0},
    {"aq-mode", v_aq, 1, 0},
};
static const PelorusEncodeParam enc_unknown[] = {{"some-new-option", v_one, 1, 0}};
static const PelorusEncodeParam deband_params[] = {
    {"thr", v_thr, 4, 1},
    {"range", v_range, 1, 0},
};

static void worked_input(PelorusEncodeRecordInput *in, PelorusEncodeFilter *filter)
{
    memset(filter, 0, sizeof(*filter));
    filter->name = "pelorus_deband_vulkan";
    filter->library = "libpelorus";
    filter->version = "0.3.0";
    filter->params = deband_params;
    filter->nb_params = 2;
    memset(in, 0, sizeof(*in));
    in->encoder_name = "libx265";
    in->encoder_version = "4.1";
    in->encoder_codec = "hevc";
    in->encoder_params = enc_params;
    in->nb_encoder_params = 4;
    in->encoder_unknown_params = enc_unknown;
    in->nb_encoder_unknown_params = 1;
    in->input_digest = "sha256:6d7233c7036e18b055f7861cd3c9f534093477b15efe8f58a80fecce4993c2ae";
    in->filters = filter;
    in->nb_filters = 1;
    in->hardware_path = "vulkan";
    in->hardware_device = "example GPU";
    in->elapsed_ns = UINT64_C(8123456789);
    in->has_elapsed_ns = 1;
}

static void test_build_matches_worked_example(void)
{
    PelorusEncodeRecordInput in;
    PelorusEncodeFilter filter;
    char text[TEXT_CAP];
    char input_hex[65];
    uint8_t input_digest[32];
    size_t len = 0;

    /* input.digest is the SHA-256 of the bytes "example input bytes". */
    CHECK(pel_sha256("example input bytes", 19, input_digest) == PEL_OK);
    to_hex(input_digest, input_hex);
    CHECK(strcmp(input_hex, "6d7233c7036e18b055f7861cd3c9f534093477b15efe8f58a80fecce4993c2ae") ==
          0);

    worked_input(&in, &filter);
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_OK);
    CHECK(len == 705u && strcmp(text, worked_canonical) == 0);
    CHECK(pel_encode_record_verify(text, len, NULL) == PEL_OK);
    /* The text and its NUL must fit: 705 is one byte short, 706 is exact. */
    CHECK(pel_encode_record_build(&in, text, 705, &len) == PEL_ERR_RANGE && len == 705u);
    CHECK(pel_encode_record_build(&in, text, 706, &len) == PEL_OK && text[705] == '\0');
    /* Size query: no buffer, cap 0 (pel_blob_pack_into's convention). */
    len = 0;
    CHECK(pel_encode_record_build(&in, NULL, 0, &len) == PEL_ERR_RANGE && len == 705u);
    CHECK(pel_encode_record_build(&in, NULL, 706, &len) == PEL_ERR_INVALID);
}

/* #81 item 1: every single encoder option or filter parameter change changes the digest. */
static void test_build_mutations(void)
{
    static const char *const changed[] = {"x"};
    PelorusEncodeParam enc_copy[4];
    PelorusEncodeParam deb_copy[2];
    PelorusEncodeRecordInput in;
    PelorusEncodeFilter filter;
    char text[TEXT_CAP];
    char base_hex[65];
    size_t len = 0;
    size_t i;

    worked_input(&in, &filter);
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_OK);
    CHECK(hash_hex_is(text, len, worked_digest));
    memcpy(base_hex, worked_digest, sizeof(base_hex));
    for (i = 0; i < 6u; i++) {
        memcpy(enc_copy, enc_params, sizeof(enc_copy));
        memcpy(deb_copy, deband_params, sizeof(deb_copy));
        if (i < 4u) {
            enc_copy[i].values = changed;
        } else {
            deb_copy[i - 4u].values = changed;
            deb_copy[i - 4u].nb_values = 1;
            deb_copy[i - 4u].is_array = 0;
        }
        in.encoder_params = enc_copy;
        filter.params = deb_copy;
        CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_OK);
        CHECK(!hash_hex_is(text, len, base_hex));
    }
}

/* #81 item 3: an unknown key is kept (flagged by living in unknown_params); dropping any
 * key from a built record fails verification. */
static void test_build_dropped_and_tampered(void)
{
    PelorusEncodeRecordInput in;
    PelorusEncodeFilter filter;
    char text[TEXT_CAP];
    char edited[TEXT_CAP];
    size_t len = 0;
    size_t n;

    worked_input(&in, &filter);
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_OK);
    CHECK(strstr(text, "\"unknown_params\":{\"some-new-option\":\"1\"}") != NULL);
    n = replace_once(edited, sizeof(edited), text, "\"bframes\":\"4\",", "");
    CHECK(pel_encode_record_verify(edited, n, NULL) == PEL_ERR_MISMATCH);
    n = replace_once(edited, sizeof(edited), text, "\"some-new-option\":\"1\"", "");
    CHECK(pel_encode_record_verify(edited, n, NULL) == PEL_ERR_MISMATCH);
    n = replace_once(edited, sizeof(edited), text, "\"slow\"", "\"slox\"");
    CHECK(pel_encode_record_verify(edited, n, NULL) == PEL_ERR_MISMATCH);
    /* Without its digest member there is nothing to verify against, unless expected. */
    n = replace_once(edited, sizeof(edited), text,
                     "\"digest\":\"sha256:b0201cec9782a58a59541103a7757fbd8221f67bd45a32f3b6a8d7f0"
                     "54d244bc\",",
                     "");
    CHECK(pel_encode_record_verify(edited, n, NULL) == PEL_ERR_ABSENT);
}

/* Required members, the input digest form and the hardware path. */
static void test_build_rejects_members(void)
{
    PelorusEncodeRecordInput in;
    PelorusEncodeFilter filter;
    char text[TEXT_CAP];
    size_t len = 0;

    worked_input(&in, &filter);
    in.encoder_name = NULL;
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    worked_input(&in, &filter);
    in.input_digest = "sha256:6D7233C7036E18B055F7861CD3C9F534093477B15EFE8F58A80FECCE4993C2AE";
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    in.input_digest = NULL; /* neither digest nor id */
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    in.input_id = "clip-0042"; /* an id alone is enough */
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_OK);
    in.hardware_path = "gpu";
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    CHECK(pel_encode_record_build(NULL, text, sizeof(text), &len) == PEL_ERR_INVALID);
    CHECK(pel_encode_record_build(&in, text, sizeof(text), NULL) == PEL_ERR_INVALID);
}

/* Keys, values and counts of the parameter objects. */
static void test_build_rejects_params(void)
{
    static const char *const bad_utf8[] = {"\xc0\xaf"};
    PelorusEncodeParam dup[2];
    PelorusEncodeRecordInput in;
    PelorusEncodeFilter filter;
    char text[TEXT_CAP];
    size_t len = 0;

    worked_input(&in, &filter);
    memcpy(dup, enc_params, sizeof(dup));
    dup[1].key = dup[0].key; /* a repeated key: the parser normalises, the builder refuses */
    in.encoder_params = dup;
    in.nb_encoder_params = 2;
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    dup[1].key = "has space";
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    dup[1].key = ""; /* an empty key names nothing */
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    dup[1].key = "ok";
    dup[1].values = bad_utf8;
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
    worked_input(&in, &filter);
    in.nb_filters = PEL_ENCODE_RECORD_MAX_ARRAY + 1u;
    CHECK(pel_encode_record_build(&in, text, sizeof(text), &len) == PEL_ERR_INVALID);
}

/* ---- Canonical form: escapes and bounds --------------------------------- */

typedef struct CanonCase {
    const char *json;
    pel_result want;
} CanonCase;

static const CanonCase canon_cases[] = {
    {"{\"a\":\"1\",\"a\":\"2\"}", PEL_ERR_INVALID},  /* duplicate key */
    {"{\"a\":1}", PEL_ERR_INVALID},                  /* number */
    {"{\"a\":true}", PEL_ERR_INVALID},               /* boolean */
    {"{\"a\":null}", PEL_ERR_INVALID},               /* null */
    {"{\"a\":\"\xff\"}", PEL_ERR_INVALID},           /* invalid UTF-8 */
    {"{\"a\":\"\xc0\xaf\"}", PEL_ERR_INVALID},       /* overlong */
    {"{\"a\":\"\xed\xa0\x80\"}", PEL_ERR_INVALID},   /* encoded surrogate */
    {"{\"a\":\"\\ud800\"}", PEL_ERR_INVALID},        /* lone surrogate escape */
    {"{\"a\":\"\\udc00\\ud800\"}", PEL_ERR_INVALID}, /* reversed pair */
    {"{\"a\":\"\x01\"}", PEL_ERR_INVALID},           /* raw control character */
    {"{\"a\":\"\\x\"}", PEL_ERR_INVALID},            /* unknown escape */
    {"{\"a b\":\"1\"}", PEL_ERR_INVALID},            /* key outside 0x21..0x7e */
    {"{\"\":\"1\"}", PEL_ERR_INVALID},               /* empty key */
    {"{\"a\":{\"\":\"1\"}}", PEL_ERR_INVALID},       /* empty nested key */
    {"{\"\\u0061\":\"1\"}", PEL_ERR_INVALID},        /* escaped key */
    {"{\"a\":\"1\"} x", PEL_ERR_INVALID},            /* trailing text */
    {"[\"a\"]", PEL_ERR_INVALID},                    /* root is not an object */
    {"{\"a\":[\"1\",]}", PEL_ERR_INVALID},           /* trailing comma */
    {"{\"a\":{\"b\":\"1\"]}", PEL_ERR_INVALID},      /* mismatched bracket */
    {"{\"a\":\"1\"", PEL_ERR_INVALID},               /* unterminated */
    {"", PEL_ERR_INVALID},                           /* empty */
    {"{}", PEL_OK},                                  /* the empty record */
    {"{\"a\":[[[[\"deep\"]]]]}", PEL_OK},            /* 4 containers below the root */
    {"{\"a\":[[[[[\"deep\"]]]]]}", PEL_ERR_INVALID}, /* 5 */
    {"{\"elapsed_ns\":{\"x\":1}}", PEL_ERR_INVALID}, /* excluded members are validated */
};

static void test_canonical_cases(void)
{
    char text[TEXT_CAP];
    size_t len = 0;
    size_t i;

    for (i = 0; i < sizeof(canon_cases) / sizeof(canon_cases[0]); i++) {
        const pel_result got = pel_encode_record_canonicalize(
            canon_cases[i].json, strlen(canon_cases[i].json), text, sizeof(text), &len);
        if (got != canon_cases[i].want) {
            (void)fprintf(stderr, "FAIL canon_cases[%zu]: got %d want %d\n", i, (int)got,
                          (int)canon_cases[i].want);
            g_fail++;
        }
    }
    /* hash validates excluded members too: same verdict as canonicalize. */
    CHECK(pel_encode_record_hash("{\"elapsed_ns\":{\"x\":1}}", 23, (uint8_t *)text) ==
          PEL_ERR_INVALID);
    CHECK(pel_encode_record_canonicalize(NULL, 0, text, sizeof(text), &len) == PEL_ERR_INVALID);
    CHECK(pel_encode_record_hash(NULL, 0, (uint8_t *)text) == PEL_ERR_INVALID);
}

/* Escapes as Python's json.dumps(ensure_ascii=False) writes them, and key byte order. */
static void test_canonical_escapes(void)
{
    static const char in[] = "{\"a\" : \"\\u0041\\/\\u00e9\\ud83d\\ude00\\u001f\\t\\\"\\\\ \\u007f"
                             "\xe2\x80\xa8\", \"B\":[\"x\",{\"z\":\"1\",\"y\":\"2\"}]}";
    static const char want[] = "{\"B\":[\"x\",{\"y\":\"2\",\"z\":\"1\"}],\"a\":\"A/\xc3\xa9"
                               "\xf0\x9f\x98\x80\\u001f\\t\\\"\\\\ \x7f\xe2\x80\xa8\"}";
    char text[TEXT_CAP];
    size_t len = 0;

    CHECK(pel_encode_record_canonicalize(in, sizeof(in) - 1u, text, sizeof(text), &len) == PEL_OK);
    CHECK(len == sizeof(want) - 1u && memcmp(text, want, len) == 0);
    /* Python: sha256 of the same 61 bytes. */
    CHECK(hash_hex_is(in, sizeof(in) - 1u,
                      "367f2cb88cf3b35a491b379cea0a8cb4a377a70cec27883f4c082f1c1d8cf7b7"));
}

/* Writes `{"k":"vvv..."}` with a key of `key_len` and a value of `val_len` bytes. */
static size_t make_kv(char *buf, size_t key_len, size_t val_len)
{
    size_t n = 0;

    buf[n++] = '{';
    buf[n++] = '"';
    memset(buf + n, 'k', key_len);
    n += key_len;
    buf[n++] = '"';
    buf[n++] = ':';
    buf[n++] = '"';
    memset(buf + n, 'v', val_len);
    n += val_len;
    buf[n++] = '"';
    buf[n++] = '}';
    buf[n] = '\0';
    return n;
}

/* Writes an object of `members` distinct keys "m0".."mN" (as many as asked). */
static size_t make_members(char *buf, size_t cap, size_t members)
{
    size_t n = 0;
    size_t i;

    buf[n++] = '{';
    for (i = 0; i < members && n + 32u < cap; i++) {
        n += (size_t)snprintf(buf + n, cap - n, "%s\"m%zu\":\"\"", i ? "," : "", i);
    }
    buf[n++] = '}';
    return n;
}

#define BOUNDS_CAP (PEL_ENCODE_RECORD_MAX_BYTES + 64u)

/* Key, value and member-count bounds on both sides of their limits. */
static void check_entry_bounds(char *big, char *out)
{
    size_t len = 0;
    size_t n;

    n = make_kv(big, PEL_ENCODE_RECORD_MAX_KEY, PEL_ENCODE_RECORD_MAX_VALUE);
    CHECK(pel_encode_record_canonicalize(big, n, out, BOUNDS_CAP, &len) == PEL_OK);
    n = make_kv(big, PEL_ENCODE_RECORD_MAX_KEY + 1u, 1);
    CHECK(pel_encode_record_canonicalize(big, n, out, BOUNDS_CAP, &len) == PEL_ERR_INVALID);
    n = make_kv(big, 1, PEL_ENCODE_RECORD_MAX_VALUE + 1u);
    CHECK(pel_encode_record_canonicalize(big, n, out, BOUNDS_CAP, &len) == PEL_ERR_INVALID);
    n = make_members(big, BOUNDS_CAP, PEL_ENCODE_RECORD_MAX_MEMBERS);
    CHECK(pel_encode_record_canonicalize(big, n, out, BOUNDS_CAP, &len) == PEL_OK);
    n = make_members(big, BOUNDS_CAP, PEL_ENCODE_RECORD_MAX_MEMBERS + 1u);
    CHECK(pel_encode_record_canonicalize(big, n, out, BOUNDS_CAP, &len) == PEL_ERR_INVALID);
}

static void test_canonical_bounds(void)
{
    char *big = malloc(BOUNDS_CAP);
    char *out = malloc(BOUNDS_CAP);
    size_t len = 0;

    CHECK(big != NULL && out != NULL);
    if (big == NULL || out == NULL) {
        free(big);
        free(out);
        return;
    }
    check_entry_bounds(big, out);
    /* Text bound: one byte over 64 KiB is refused, not truncated. */
    memset(big, ' ', BOUNDS_CAP);
    big[0] = '{';
    big[1] = '}';
    CHECK(pel_encode_record_canonicalize(big, PEL_ENCODE_RECORD_MAX_BYTES, out, BOUNDS_CAP, &len) ==
          PEL_OK);
    CHECK(pel_encode_record_canonicalize(big, PEL_ENCODE_RECORD_MAX_BYTES + 1u, out, BOUNDS_CAP,
                                         &len) == PEL_ERR_INVALID);
    /* A short output buffer reports the length it needs. */
    CHECK(pel_encode_record_canonicalize("{}", 2, out, 2, &len) == PEL_ERR_RANGE && len == 2u);
    /* Size query: no buffer, cap 0; a NULL buffer with a capacity is an error. */
    len = 0;
    CHECK(pel_encode_record_canonicalize("{}", 2, NULL, 0, &len) == PEL_ERR_RANGE && len == 2u);
    CHECK(pel_encode_record_canonicalize("{}", 2, NULL, 8, &len) == PEL_ERR_INVALID);
    CHECK(pel_encode_record_canonicalize("{\"a\":1}", 7, NULL, 0, &len) == PEL_ERR_INVALID);
    free(big);
    free(out);
}

int main(int argc, char **argv)
{
    test_sha256_fips();
    test_sha256_boundaries();
    test_sha256_null_data();
    test_scalar_int();
    test_scalar_f64();
    test_worked_example();
    test_build_matches_worked_example();
    test_build_mutations();
    test_build_dropped_and_tampered();
    test_build_rejects_members();
    test_build_rejects_params();
    test_canonical_cases();
    test_canonical_escapes();
    test_canonical_bounds();
    CHECK(argc == 2); /* the page path: docs/api/encode-record.md */
    if (argc == 2) {
        test_worked_example_from_doc(argv[1]);
    }

    if (g_fail != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", g_fail);
        return EXIT_FAILURE;
    }
    (void)printf("encode-record: all checks passed\n");
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
