/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Unit tests for ffmpeg-patches/files/pelorus_udu_wrap.h, which classifies
 * direct and x265-wrapped user data unregistered (UDU) SEI payloads for
 * pelorus_fgs (ADR-0210).
 */

#ifndef _WIN32
#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE 700
#endif
#endif

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus/interop.h"
#include "pelorus_udu_wrap.h"
#include "test_path_safety.h"

_Static_assert(PEL_UDU_UUID_LEN == PELORUS_SIDEDATA_UUID_LEN, "uuid length mirror");

static int run_case(const char *name, int condition)
{
    if (condition) {
        (void)printf("PASS: %s\n", name);
        return 0;
    }
    (void)fprintf(stderr, "FAIL: %s\n", name);
    return 1;
}

#define VEC_MAX_LINES 256
#define VEC_MAX_BYTES 128
#define VEC_MIN_COUNT 17 /* vectors in fixtures/udu-wrap-vectors.txt today */

/* Value of one hex digit, or -1. */
static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

/* Decode lowercase or uppercase hex; "-" is zero bytes. Returns the byte
 * count, or -1 on odd length, a bad digit or more than cap bytes. */
static int hex_decode(const char *hex, uint8_t *out, size_t cap)
{
    size_t len = strlen(hex);

    if (strcmp(hex, "-") == 0)
        return 0;
    if (len % 2 != 0 || len / 2 > cap)
        return -1;
    for (size_t i = 0; i < len / 2; i++) {
        int hi = hex_nibble(hex[2 * i]);
        int lo = hex_nibble(hex[2 * i + 1]);
        if (hi < 0 || lo < 0)
            return -1;
        out[i] = (uint8_t)(((unsigned int)hi << 4U) | (unsigned int)lo);
    }
    return (int)(len / 2);
}

static int expected_class(const char *word)
{
    if (strcmp(word, "direct") == 0)
        return PEL_UDU_DIRECT;
    if (strcmp(word, "x265_wrapped") == 0)
        return PEL_UDU_X265_WRAPPED;
    if (strcmp(word, "none") == 0)
        return PEL_UDU_NONE;
    return -1;
}

/* Classify one vector line; 0 pass, 1 fail (a malformed line fails). */
static int replay_line(const char *line, int *seen)
{
    char name[96];
    char outer_hex[64];
    char data_hex[2 * VEC_MAX_BYTES + 2];
    char want_word[24];
    uint8_t outer[PEL_UDU_UUID_LEN];
    uint8_t data[VEC_MAX_BYTES];
    int data_len;
    int want;
    int got;

    if (sscanf(line, "%95s %63s %257s %23s", name, outer_hex, data_hex, want_word) != 4) {
        (void)fprintf(stderr, "FAIL: vector line malformed: %s", line);
        return 1;
    }
    data_len = hex_decode(data_hex, data, sizeof(data));
    want = expected_class(want_word);
    if (hex_decode(outer_hex, outer, sizeof(outer)) != PEL_UDU_UUID_LEN || data_len < 0 ||
        want < 0) {
        (void)fprintf(stderr, "FAIL: vector %s: bad field\n", name);
        return 1;
    }
    got = pel_udu_classify(outer, data_len ? data : NULL, (size_t)data_len);
    seen[want]++;
    if (got != want) {
        (void)fprintf(stderr, "FAIL: vector %s: expected %d, got %d\n", name, want, got);
        return 1;
    }
    (void)printf("PASS: vector %s\n", name);
    return 0;
}

/* Replay every vector; a missing file, fewer than VEC_MIN_COUNT vectors or a
 * class with no vector fails, so an emptied file cannot pass. */
static int replay_vectors(const char *path)
{
    char line[512];
    int failures = 0;
    int count = 0;
    int seen[3] = {0, 0, 0};
    FILE *f = path ? test_fopen_safe(path, "r") : NULL;

    if (!f) {
        (void)fprintf(stderr, "FAIL: vector file %s not readable\n", path ? path : "(none)");
        return 1;
    }
    for (int n = 0; n < VEC_MAX_LINES && fgets(line, sizeof(line), f); n++) {
        if (line[0] == '#' || line[0] == '\n')
            continue;
        failures += replay_line(line, seen);
        count++;
    }
    (void)fclose(f);
    if (count < VEC_MIN_COUNT || !seen[PEL_UDU_NONE] || !seen[PEL_UDU_DIRECT] ||
        !seen[PEL_UDU_X265_WRAPPED]) {
        (void)fprintf(stderr, "FAIL: %d vectors, need %d covering every class\n", count,
                      VEC_MIN_COUNT);
        failures++;
    }
    return failures;
}

/* Pointer cases a hex vector file cannot express. */
static int pointer_cases(void)
{
    int failures = 0;
    uint8_t payload64[64];

    memset(payload64, 0x55, sizeof(payload64));
    memcpy(payload64, pelorus_sidedata_uuid, PEL_UDU_UUID_LEN);
    failures += run_case("negative: x265 outer with NULL data len 32",
                         pel_udu_classify(pel_udu_x265_uuid, NULL, 32) == PEL_UDU_NONE);
    failures += run_case("negative: NULL outer UUID",
                         pel_udu_classify(NULL, payload64, 64) == PEL_UDU_NONE);
    failures +=
        run_case("negative: safe fopen refuses NULL path", test_fopen_safe(NULL, "r") == NULL);
    failures +=
        run_case("negative: safe fopen refuses directory", test_fopen_safe(".", "r") == NULL);
    return failures;
}

/* Pack one film grain section; the caller frees *blob with pel_blob_free. */
static pel_result pack_grain_blob(PelorusFilmGrainSection *grain, uint8_t **blob, size_t *len)
{
    PelorusSideData meta;
    PelorusPackSection sec;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 100;
    memset(grain, 0, sizeof(*grain));
    grain->grain_model = PEL_GRAIN_AOM;
    grain->apply = 1;
    sec.id = PEL_SEC_FILMGRAIN;
    sec.data = grain;
    sec.size = (uint32_t)sizeof(*grain);
    return pel_blob_pack(&meta, &sec, 1, blob, len);
}

/* Unwrap the payload of an x265-wrapped message as pelorus_fgs does and look
 * up the film grain section; returns the find_section result. */
static pel_result unwrap_and_find(const uint8_t *data, size_t len, uint8_t *scratch,
                                  size_t scratch_cap, const void **sec, size_t *sec_size)
{
    const uint8_t *blob = NULL;
    size_t blob_len = 0;
    pel_result pr = pel_blob_unwrap(data, len, scratch, scratch_cap, &blob, &blob_len);

    if (pr != PEL_OK)
        return pr;
    return pel_blob_find_section(blob, blob_len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection),
                                 sec, sec_size);
}

/* One wrapped payload (blob or carrier): classified as x265-wrapped, the
 * section round-trips, and the payload cut by one byte is refused. */
static int wrapped_round_trip(const char *what, const uint8_t *data, size_t len,
                              const PelorusFilmGrainSection *grain, uint8_t *scratch,
                              size_t scratch_cap)
{
    char name[96];
    int failures = 0;
    const void *sec = NULL;
    size_t sec_size = 0;
    pel_result pr;

    (void)snprintf(name, sizeof(name), "%s: classified x265-wrapped", what);
    failures +=
        run_case(name, pel_udu_classify(pel_udu_x265_uuid, data, len) == PEL_UDU_X265_WRAPPED);
    pr = unwrap_and_find(data, len, scratch, scratch_cap, &sec, &sec_size);
    (void)snprintf(name, sizeof(name), "%s: film grain section round-trips", what);
    failures += run_case(name, pr == PEL_OK && sec != NULL && sec_size >= sizeof(*grain) &&
                                   memcmp(sec, grain, sizeof(*grain)) == 0);
    /* pel_blob_unwrap does not check total_size; find_section refuses the cut. */
    pr = unwrap_and_find(data, len - 1, scratch, scratch_cap, &sec, &sec_size);
    (void)snprintf(name, sizeof(name), "%s: cut by one byte is refused", what);
    failures += run_case(name, pr != PEL_OK);
    return failures;
}

static int round_trip_cases(void)
{
    PelorusFilmGrainSection grain;
    uint8_t *blob = NULL;
    uint8_t *carrier = NULL;
    uint8_t *scratch = NULL;
    size_t blob_len = 0;
    size_t carrier_len = 0;
    size_t cap;
    int failures = 0;

    if (pack_grain_blob(&grain, &blob, &blob_len) != PEL_OK || blob_len <= PEL_UDU_UUID_LEN)
        return run_case("round trip: pel_blob_pack succeeds", 0);
    cap = PEL_CARRIER_MAX_LEN(blob_len);
    carrier = (uint8_t *)malloc(cap);
    scratch = (uint8_t *)malloc(cap);
    if (carrier && scratch &&
        pel_blob_carrier_encode(blob, blob_len, carrier, cap, &carrier_len) == PEL_OK) {
        failures += wrapped_round_trip("blob round trip", blob, blob_len, &grain, scratch, cap);
        failures +=
            wrapped_round_trip("carrier round trip", carrier, carrier_len, &grain, scratch, cap);
    } else {
        failures += run_case("round trip: carrier buffers and encode", 0);
    }
    free(scratch);
    free(carrier);
    pel_blob_free(blob);
    return failures;
}

int main(int argc, char **argv)
{
    int failures = 0;

    failures += run_case("mirror: pel_udu_blob_uuid matches pelorus_sidedata_uuid",
                         memcmp(pel_udu_blob_uuid, pelorus_sidedata_uuid, PEL_UDU_UUID_LEN) == 0);
    failures += run_case("mirror: pel_udu_carrier_uuid matches pelorus_carrier_uuid",
                         memcmp(pel_udu_carrier_uuid, pelorus_carrier_uuid, PEL_UDU_UUID_LEN) == 0);
    /* Published vectors (fixtures/udu-wrap-vectors.txt), replayed by vmafx too */
    failures += replay_vectors(argc > 1 ? argv[1] : NULL);
    failures += pointer_cases();
    failures += round_trip_cases();
    if (failures != 0) {
        (void)fprintf(stderr, "udu-wrap: %d failures\n", failures);
        return 1;
    }
    (void)printf("udu-wrap: all cases passed\n");
    return 0;
}
