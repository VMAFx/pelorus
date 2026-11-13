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

/* Encode packed blob into COBS-encoded carrier stream. */
static void pack_scenario1_carrier(const uint8_t *blob, size_t blob_len, uint8_t **out_carrier,
                                   size_t *out_carrier_len)
{
    size_t c_need = 0;
    size_t c_len = 0;
    uint8_t *carrier;

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
    CHECK(c_len <= HP_SCRATCH_SIZE);
    for (size_t i = 0; i < c_len && i < HP_SCRATCH_SIZE; i++) {
        CHECK(carrier[i] != 0x00u);
    }
    *out_carrier = carrier;
    *out_carrier_len = c_len;
}

/* Produce scenario 1 packed blob and carrier stream from record and grain data. */
void hp_produce_scenario1(const char *grain_table_path, HpScenario1 *sc)
{
    PelorusSideData meta;
    PelorusPackSection secs[2];

    hp_create_record_section(sc->json, sc->json_len, &sc->rec);
    if (hp_read_scenario1_grain(grain_table_path, &sc->parsed_grain) != 0) {
        return;
    }
    hp_compare_grain_full(&sc->parsed_grain, &sc->expected_grain);

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 42u;
    meta.bit_depth = 10u;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.producer_id = PLRS_PRODUCER_ID;

    secs[0].id = PEL_SEC_ENCODE_RECORD;
    secs[0].data = &sc->rec;
    secs[0].size = (uint32_t)sizeof(sc->rec);
    secs[1].id = PEL_SEC_FILMGRAIN;
    secs[1].data = &sc->parsed_grain;
    secs[1].size = (uint32_t)sizeof(sc->parsed_grain);

    CHECK(pel_blob_pack(&meta, secs, 2, &sc->blob, &sc->blob_len) == PEL_OK);
    CHECK(sc->blob != NULL);
    if (sc->blob == NULL) {
        return;
    }

    pack_scenario1_carrier(sc->blob, sc->blob_len, &sc->carrier, &sc->carrier_len);
}

/* Unwrap carrier stream and verify header metadata matches expectations. */
static void verify_unwrapped_blob(const HpScenario1 *sc, uint8_t *scratch, size_t scratch_size,
                                  const uint8_t **out_unwrapped, size_t *out_unwrapped_len)
{
    CHECK(sc->carrier_len <= scratch_size);
    CHECK(pel_blob_unwrap(sc->carrier, sc->carrier_len, scratch, scratch_size, out_unwrapped,
                          out_unwrapped_len) == PEL_OK);
    CHECK(*out_unwrapped != NULL && *out_unwrapped_len == sc->blob_len);
    if (*out_unwrapped == NULL || *out_unwrapped_len != sc->blob_len ||
        sc->blob_len < PELORUS_SIDEDATA_UUID_LEN + sizeof(PelorusSideData)) {
        *out_unwrapped = NULL;
        return;
    }
    CHECK(memcmp(*out_unwrapped, sc->blob, sc->blob_len) == 0);
    CHECK(memcmp(*out_unwrapped, pelorus_sidedata_uuid, PELORUS_SIDEDATA_UUID_LEN) == 0);

    {
        const void *hdr_ptr = *out_unwrapped + PELORUS_SIDEDATA_UUID_LEN;
        const PelorusSideData *hdr = hdr_ptr;
        CHECK(hdr->frame_pts == 42u);
        CHECK(hdr->bit_depth == 10u);
        CHECK(hdr->producer_id == PLRS_PRODUCER_ID);
    }
}

/* Consume scenario 1 carrier stream and verify all payload sections. */
void hp_consume_scenario1(HpScenario1 *sc)
{
    _Alignas(8) uint8_t scratch[HP_SCRATCH_SIZE];
    const uint8_t *unwrapped = NULL;
    size_t unwrapped_len = 0;

    verify_unwrapped_blob(sc, scratch, sizeof(scratch), &unwrapped, &unwrapped_len);
    if (unwrapped != NULL && unwrapped_len > 0u) {
        hp_verify_consumed_record(unwrapped, unwrapped_len, sc->json, sc->json_len, &sc->rec);
        hp_verify_consumed_grain(unwrapped, unwrapped_len, &sc->expected_grain, &sc->parsed_grain);
    }
}

/* Verify carrier unwrap rejects zero byte in stream and foreign UUID. */
static void test_carrier_corrupted_bytes(uint8_t *carrier, size_t carrier_len)
{
    _Alignas(8) uint8_t scratch[HP_SCRATCH_SIZE];
    const uint8_t *out = NULL;
    size_t out_len = 0;
    uint8_t saved_byte;

    /* A 0x00 byte cannot occur in a COBS stream. */
    saved_byte = carrier[17];
    carrier[17] = 0x00u;
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch, sizeof(scratch), &out, &out_len) ==
          PEL_ERR_ABI);
    CHECK(out == NULL);
    carrier[17] = saved_byte;

    /* A foreign UUID is no Pelorus carrier at all. */
    carrier[0] = (uint8_t)(carrier[0] ^ 0x01u);
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch, sizeof(scratch), &out, &out_len) ==
          PEL_ERR_ABSENT);
    CHECK(out == NULL);
    carrier[0] = (uint8_t)(carrier[0] ^ 0x01u);

    /* Verify clean unwrap after restoring foreign UUID. */
    out = NULL;
    out_len = 0;
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch, sizeof(scratch), &out, &out_len) ==
          PEL_OK);
    CHECK(out != NULL);
}

/* Verify carrier unwrap rejects truncated carrier streams. */
static void test_carrier_truncated(const uint8_t *carrier)
{
    _Alignas(8) uint8_t scratch[HP_SCRATCH_SIZE];
    const uint8_t *out = NULL;
    size_t out_len = 0;

    /* Cut inside the first COBS block: its code byte runs past the end. */
    CHECK(pel_blob_unwrap(carrier, PELORUS_SIDEDATA_UUID_LEN + 4u, scratch, sizeof(scratch), &out,
                          &out_len) == PEL_ERR_ABI);
    CHECK(out == NULL);

    /* Shorter than the UUID. */
    out = NULL;
    out_len = 0;
    CHECK(pel_blob_unwrap(carrier, PELORUS_SIDEDATA_UUID_LEN - 1u, scratch, sizeof(scratch), &out,
                          &out_len) == PEL_ERR_ABSENT);
    CHECK(out == NULL);
}

/* Verify carrier unwrap rejects corrupted COBS, wrong UUID, and truncated streams. */
void hp_test_negative_carrier_corrupt(uint8_t *carrier, size_t carrier_len)
{
    CHECK(carrier != NULL && carrier_len > 17u);
    if (carrier == NULL || carrier_len <= 17u) {
        return;
    }
    test_carrier_corrupted_bytes(carrier, carrier_len);
    test_carrier_truncated(carrier);
}

/* Verify that a header-only blob without sections reports absent on section search. */
void hp_test_negative_header_only_blob(void)
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

typedef struct HpCarrierEncodeCtx {
    const uint8_t *blob;
    size_t blob_len;
} HpCarrierEncodeCtx;

static pel_result encode_boundary_carrier(const void *ctx, uint8_t *out, size_t cap,
                                          size_t *out_len)
{
    const HpCarrierEncodeCtx *carrier = ctx;
    return pel_blob_carrier_encode(carrier->blob, carrier->blob_len, out, cap, out_len);
}

/* Verify boundary behavior and size query for carrier encoding. */
void hp_test_boundary_carrier_encode(const uint8_t *blob, size_t blob_len)
{
    size_t need = 0;

    CHECK(pel_blob_carrier_encode(blob, blob_len, NULL, 0u, &need) == PEL_ERR_RANGE);
    const HpCarrierEncodeCtx ctx = {blob, blob_len};
    hp_check_size_boundary("carrier encode", encode_boundary_carrier, &ctx, need);
}

/* Verify boundary behavior for unwrap destination scratch sizing. */
void hp_test_boundary_unwrap_scratch(const uint8_t *carrier, size_t carrier_len)
{
    _Alignas(8) uint8_t scratch[HP_SCRATCH_SIZE];
    const uint8_t *out = NULL;
    size_t out_len = 0;

    CHECK(carrier_len > 0u && carrier_len <= sizeof(scratch));
    if (carrier_len == 0u || carrier_len > sizeof(scratch)) {
        return;
    }
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch, carrier_len - 1u, &out, &out_len) ==
          PEL_ERR_RANGE);

    out = NULL;
    out_len = 0;
    CHECK(pel_blob_unwrap(carrier, carrier_len, scratch, carrier_len, &out, &out_len) == PEL_OK);
    CHECK(out != NULL);
}

/* NOLINTEND(modernize-use-nullptr) */
