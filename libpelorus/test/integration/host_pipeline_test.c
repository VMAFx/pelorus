/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "host_pipeline.h"

#include <stdio.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

/* Consume scenario 1, then run its negative and boundary cases on the produced buffers. */
static void check_scenario1(HpScenario1 *sc)
{
    hp_consume_scenario1(sc);

    hp_test_negative_flipped_json(sc->json, sc->json_len, sc->rec.digest);
    hp_test_negative_flipped_digest(sc->json, sc->json_len, &sc->rec);
    hp_test_negative_carrier_corrupt(sc->carrier, sc->carrier_len);
    hp_test_negative_short_section_size(&sc->rec);
    hp_test_negative_header_only_blob();

    hp_test_boundary_carrier_encode(sc->blob, sc->blob_len);
    hp_test_boundary_unwrap_scratch(sc->carrier, sc->carrier_len);
}

/* Run scenario 1: produce the blob and carrier, check them, free every buffer. */
static void run_scenario1(const char *grain_table_path)
{
    HpScenario1 sc;

    memset(&sc, 0, sizeof(sc));
    hp_build_expected_grain(&sc.expected_grain);
    hp_build_provenance_json(&sc.json, &sc.json_len);
    CHECK(sc.json != NULL && sc.json_len > 0u);
    if (sc.json == NULL || sc.json_len == 0u) {
        hp_scenario1_release(&sc);
        return;
    }

    hp_produce_scenario1(grain_table_path, &sc);
    CHECK(sc.blob != NULL && sc.carrier != NULL);
    if (sc.blob != NULL && sc.carrier != NULL && sc.blob_len > 0u && sc.carrier_len > 0u) {
        check_scenario1(&sc);
    }
    hp_scenario1_release(&sc);
}

/* Run both host-pipeline scenarios; exit 1 when any check failed. */
int main(int argc, char **argv)
{
    if (argc < 3 || argv[1] == NULL || argv[2] == NULL) {
        (void)fprintf(stderr, "usage: %s <grain.tbl> <x265.csv>\n", argc > 0 ? argv[0] : "test");
        return 1;
    }

    run_scenario1(argv[1]);
    hp_test_scenario2_telemetry(argv[2]);

    if (hp_fail_count() != 0) {
        (void)fprintf(stderr, "host-pipeline: %d checks failed\n", hp_fail_count());
        return 1;
    }
    return 0;
}

/* NOLINTEND(modernize-use-nullptr) */
