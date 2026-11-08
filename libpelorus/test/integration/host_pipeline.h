/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_TEST_INTEGRATION_HOST_PIPELINE_H
#define PELORUS_TEST_INTEGRATION_HOST_PIPELINE_H

#include "pelorus/deband.h"
#include "pelorus/encode_options.h"
#include "pelorus/encode_record.h"
#include "pelorus/grain_convert.h"
#include "pelorus/grain_table.h"
#include "pelorus/interop.h"
#include "pelorus/pelorus.h"
#include "pelorus/telemetry.h"

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define HP_FIXTURE_BUF_CAP ((size_t)1024u * (size_t)1024u)
#define HP_READ_CHUNK_SIZE 4096u
#define HP_MAX_READ_ITERS (HP_FIXTURE_BUF_CAP / HP_READ_CHUNK_SIZE)
#define HP_SCRATCH_SIZE 8192u
#define HP_TELEMETRY_FRAME_CAP 32u
#define HP_EXPECTED_X265_FRAME_COUNT 10u
#define HP_GRAIN_ENTRY_CAP 4u

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

void hp_check_failed(const char *file, int line, const char *cond);
int hp_fail_count(void);

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            hp_check_failed(__FILE__, __LINE__, #cond);                                            \
        }                                                                                          \
    } while (0)

typedef struct HpScenario1 {
    char *json;
    size_t json_len;
    uint8_t *blob;
    size_t blob_len;
    uint8_t *carrier;
    size_t carrier_len;
    PelorusFilmGrainSection parsed_grain;
    PelorusFilmGrainSection expected_grain;
    PelorusEncodeRecordSection rec;
} HpScenario1;

/* Free the scenario 1 buffers, then reset every pointer, length and section to zero. */
static inline void hp_scenario1_release(HpScenario1 *sc)
{
    if (sc == NULL) {
        return;
    }
    free(sc->carrier);
    sc->carrier = NULL;
    sc->carrier_len = 0u;

    pel_blob_free(sc->blob);
    sc->blob = NULL;
    sc->blob_len = 0u;

    free(sc->json);
    sc->json = NULL;
    sc->json_len = 0u;

    memset(&sc->parsed_grain, 0, sizeof(sc->parsed_grain));
    memset(&sc->expected_grain, 0, sizeof(sc->expected_grain));
    memset(&sc->rec, 0, sizeof(sc->rec));
}

/* Common fixture reading */
int hp_read_fixture(const char *path, char *buf, size_t cap, size_t *out_len);

/* Encode record and provenance tests */
void hp_build_provenance_json(char **out_json, size_t *out_len);
void hp_create_record_section(const char *json, size_t json_len,
                              PelorusEncodeRecordSection *rec_sec);
void hp_verify_consumed_record(const uint8_t *unwrapped, size_t unwrapped_len, const char *json,
                               size_t json_len, PelorusEncodeRecordSection *out_sec_rec);
void hp_test_negative_flipped_json(char *json, size_t len,
                                   const uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE]);
void hp_test_negative_flipped_digest(const char *json, size_t len,
                                     const PelorusEncodeRecordSection *sec);
void hp_test_negative_short_section_size(const PelorusEncodeRecordSection *sec);

/* Film grain tests */
void hp_build_expected_grain(PelorusFilmGrainSection *expected);
int hp_read_scenario1_grain(const char *grain_table_path, PelorusFilmGrainSection *out_grain0);
void hp_compare_grain_full(const PelorusFilmGrainSection *got,
                           const PelorusFilmGrainSection *expected);
void hp_verify_consumed_grain(const uint8_t *unwrapped, size_t unwrapped_len,
                              const PelorusFilmGrainSection *expected_grain,
                              const PelorusFilmGrainSection *parsed_grain);

/* Carrier and blob tests */
void hp_produce_scenario1(const char *grain_table_path, HpScenario1 *sc);
void hp_consume_scenario1(HpScenario1 *sc);
void hp_test_negative_carrier_corrupt(uint8_t *carrier, size_t carrier_len);
void hp_test_negative_header_only_blob(void);
void hp_test_boundary_carrier_encode(const uint8_t *blob, size_t blob_len);
void hp_test_boundary_unwrap_scratch(const uint8_t *carrier, size_t carrier_len);

/* Telemetry tests */
void hp_test_scenario2_telemetry(const char *csv_path);

#endif /* PELORUS_TEST_INTEGRATION_HOST_PIPELINE_H */
