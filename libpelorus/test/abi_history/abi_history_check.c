/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

/**
 * Historical interop side-data blob inspection, section verification, and
 * auxiliary map validation routines.
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "abi_history.h"
#include "abi_history_values.h"

/**
 * Return the smaller of two size_t values.
 */
static inline size_t min_size(size_t a, size_t b)
{
    return (a < b) ? a : b;
}

#if PELORUS_ABI_MINOR >= 4
/**
 * Verify auxiliary map bounds and match against expected reference bytes (v1.4+).
 */
static int check_map_bytes_v4(const uint8_t *blob, size_t len, uint32_t offset, uint32_t size,
                              uint32_t elem_count, uint32_t elem_size, const void *expected,
                              const char *name)
{
    const void *mapped = NULL;
    pel_result rc = pel_blob_map(blob, len, offset, size, elem_count, elem_size, &mapped);
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: map %s pel_blob_map error %d\n", name, (int)rc);
        return 1;
    }
    if (memcmp(mapped, expected, size) != 0) {
        (void)fprintf(stderr, "FAIL: map %s data mismatch\n", name);
        return 1;
    }
    return 0;
}
#else
/**
 * Validate arguments and alignment for legacy map verification.
 */
static int validate_map_args(const uint8_t *blob, const void *expected, uint32_t elem_count,
                             uint32_t elem_size, uint32_t size, uint32_t offset, const char *name)
{
    if (blob == NULL || expected == NULL || elem_count == 0u || elem_size == 0u) {
        return 1;
    }
    if ((uint64_t)elem_count * (uint64_t)elem_size != (uint64_t)size) {
        (void)fprintf(stderr, "FAIL: map %s bad size\n", name);
        return 1;
    }
    if ((offset & 7u) != 0u) {
        (void)fprintf(stderr, "FAIL: map %s misaligned offset\n", name);
        return 1;
    }
    return 0;
}

/**
 * Validate header bounds and payload containment for legacy map verification.
 */
static int validate_map_bounds(const uint8_t *blob, size_t len, uint32_t offset, uint32_t size,
                               const char *name)
{
    PelorusSideData hdr;
    if (len < (size_t)PELORUS_SIDEDATA_UUID_LEN + sizeof(hdr)) {
        (void)fprintf(stderr, "FAIL: map %s blob too short for header\n", name);
        return 1;
    }
    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    if ((uint64_t)offset <
        (uint64_t)hdr.header_size + (uint64_t)hdr.section_count * sizeof(PelorusSectionDir)) {
        (void)fprintf(stderr, "FAIL: map %s inside header/dir\n", name);
        return 1;
    }
    if (offset > hdr.total_size || size > hdr.total_size - offset) {
        (void)fprintf(stderr, "FAIL: map %s outside total_size\n", name);
        return 1;
    }
    if ((uint64_t)PELORUS_SIDEDATA_UUID_LEN + offset + size > (uint64_t)len) {
        (void)fprintf(stderr, "FAIL: map %s outside blob\n", name);
        return 1;
    }
    return 0;
}

/**
 * Verify auxiliary map bounds and match against expected reference bytes (pre-1.4).
 */
static int check_map_bytes_legacy(const uint8_t *blob, size_t len, uint32_t offset, uint32_t size,
                                  uint32_t elem_count, uint32_t elem_size, const void *expected,
                                  const char *name)
{
    if (validate_map_args(blob, expected, elem_count, elem_size, size, offset, name) != 0 ||
        validate_map_bounds(blob, len, offset, size, name) != 0) {
        return 1;
    }
    if (memcmp(blob + PELORUS_SIDEDATA_UUID_LEN + offset, expected, size) != 0) {
        (void)fprintf(stderr, "FAIL: map %s data mismatch\n", name);
        return 1;
    }
    return 0;
}
#endif

/**
 * Verify auxiliary map bounds and match against expected reference bytes.
 * Returns 0 on match, 1 on bounds violation or byte mismatch.
 */
static int check_map_bytes(const uint8_t *blob, size_t len, uint32_t offset, uint32_t size,
                           uint32_t elem_count, uint32_t elem_size, const void *expected,
                           const char *name)
{
#if PELORUS_ABI_MINOR >= 4
    return check_map_bytes_v4(blob, len, offset, size, elem_count, elem_size, expected, name);
#else
    return check_map_bytes_legacy(blob, len, offset, size, elem_count, elem_size, expected, name);
#endif
}

#if PELORUS_ABI_MINOR >= 3
/**
 * Verify presence, absence, and payload size of a minor-gated section.
 * Returns 0 if expected absent and verified, 1 on error, or 2 if present and size valid.
 */
static int check_sec_presence(pel_result rc, size_t got, size_t exp_size, uint16_t w_minor,
                              uint16_t min_minor, const char *name)
{
    if (w_minor < min_minor) {
        if (rc != PEL_ERR_ABSENT) {
            (void)fprintf(stderr, "FAIL: %s should be absent for minor %u\n", name,
                          (unsigned)w_minor);
            return 1;
        }
        return 0;
    }
    if (rc != PEL_OK || got != exp_size) {
        (void)fprintf(stderr, "FAIL: %s missing or wrong size (rc=%d got=%zu)\n", name, (int)rc,
                      got);
        return 1;
    }
    return 2;
}
#endif

/**
 * Compare flat section fields against deterministic expected bytes.
 */
static int check_simple_fields(const void *expected, const void *actual, size_t size,
                               const char *name)
{
    /* NOLINTNEXTLINE(bugprone-suspicious-memory-comparison,cert-exp42-c,cert-flp37-c): deterministic padding zeroed by calloc */
    if (memcmp(expected, actual, size) != 0) {
        (void)fprintf(stderr, "FAIL: %s fields mismatch\n", name);
        return 1;
    }
    return 0;
}

/**
 * Verify banding section header fields and referenced auxiliary map.
 * Returns 0 on match, 1 on absent section or data mismatch.
 */
static int check_banding(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusBandingSection expected;
    const PelorusBandingSection *actual;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: banding section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusBandingSection)) {
        (void)fprintf(stderr, "FAIL: banding size %zu != %zu\n", got,
                      sizeof(PelorusBandingSection));
        return 1;
    }
    abih_fill_banding(&expected);
    actual = (const PelorusBandingSection *)p;
    expected.cell_data_offset = actual->cell_data_offset;
    expected.cell_data_size = actual->cell_data_size;
    if (check_simple_fields(&expected, actual, sizeof(expected), "banding") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->cell_data_offset, actual->cell_data_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_banding_cell_data,
                           "banding");
}

/**
 * Verify variance section header fields and referenced auxiliary maps.
 * Returns 0 on match, 1 on absent section or data mismatch.
 */
static int check_variance(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusVarianceSection expected;
    const PelorusVarianceSection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_VARIANCE,
                                          sizeof(PelorusVarianceSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: variance section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusVarianceSection)) {
        (void)fprintf(stderr, "FAIL: variance size %zu != %zu\n", got,
                      sizeof(PelorusVarianceSection));
        return 1;
    }
    abih_fill_variance(&expected);
    actual = (const PelorusVarianceSection *)p;
    expected.var_cell_offset = actual->var_cell_offset;
    expected.var_cell_size = actual->var_cell_size;
    expected.edge_cell_offset = actual->edge_cell_offset;
    expected.edge_cell_size = actual->edge_cell_size;
    if (check_simple_fields(&expected, actual, sizeof(expected), "variance") != 0) {
        return 1;
    }
    if (check_map_bytes(blob, len, actual->var_cell_offset, actual->var_cell_size, VAL_CELL_COUNT,
                        (uint32_t)sizeof(float), val_var_cell_data, "variance_var") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->edge_cell_offset, actual->edge_cell_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_edge_cell_data,
                           "variance_edge");
}

/**
 * Verify denoise section header fields match deterministic test values.
 * Returns 0 on match, 1 on absent section or data mismatch.
 */
static int check_denoise(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusDenoiseSection expected;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_DENOISE, sizeof(PelorusDenoiseSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: denoise section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusDenoiseSection)) {
        (void)fprintf(stderr, "FAIL: denoise size %zu != %zu\n", got,
                      sizeof(PelorusDenoiseSection));
        return 1;
    }
    abih_fill_denoise(&expected);
    return check_simple_fields(&expected, p, sizeof(expected), "denoise");
}

/**
 * Verify filmgrain section header fields match deterministic test values.
 * Returns 0 on match, 1 on absent section or data mismatch.
 */
static int check_filmgrain(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusFilmGrainSection expected;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN,
                                          sizeof(PelorusFilmGrainSection), &p, &got);
    (void)w_minor;
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: filmgrain section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != sizeof(PelorusFilmGrainSection)) {
        (void)fprintf(stderr, "FAIL: filmgrain size %zu != %zu\n", got,
                      sizeof(PelorusFilmGrainSection));
        return 1;
    }
    abih_fill_filmgrain(&expected);
    return check_simple_fields(&expected, p, sizeof(expected), "filmgrain");
}

/**
 * Verify motion section header fields and referenced motion vector map.
 * Returns 0 on match, 1 on absent section or data mismatch.
 */
static int check_motion(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    size_t w_size = (w_minor < 4u) ? 32u : 36u;
    size_t exp_got = min_size(sizeof(PelorusMotionSection), w_size);
    PelorusMotionSection expected;
    const PelorusMotionSection *actual;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p, &got);
    if (rc != PEL_OK) {
        (void)fprintf(stderr, "FAIL: motion section missing (rc=%d)\n", (int)rc);
        return 1;
    }
    if (got != exp_got) {
        (void)fprintf(stderr, "FAIL: motion got %zu != exp %zu\n", got, exp_got);
        return 1;
    }
    abih_fill_motion(&expected);
    actual = (const PelorusMotionSection *)p;
    expected.mv_field_offset = actual->mv_field_offset;
    expected.mv_field_size = actual->mv_field_size;
    if (memcmp(&expected, actual, got) != 0) {
        (void)fprintf(stderr, "FAIL: motion fields mismatch\n");
        return 1;
    }
    return check_map_bytes(blob, len, actual->mv_field_offset, actual->mv_field_size,
                           VAL_CELL_COUNT, (uint32_t)(2u * sizeof(int16_t)), val_mv_field_data,
                           "motion_mv");
}

#if PELORUS_ABI_MINOR >= 3
/**
 * Verify qpreport section header fields and referenced qp cell map.
 * Returns 0 on match or expected absence, 1 on error or mismatch.
 */
static int check_qpreport(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusQpReportSection expected;
    const PelorusQpReportSection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_QPREPORT,
                                          sizeof(PelorusQpReportSection), &p, &got);
    int pr = check_sec_presence(rc, got, sizeof(PelorusQpReportSection), w_minor, 3u, "qpreport");
    if (pr != 2) {
        return pr;
    }
    abih_fill_qpreport(&expected);
    actual = (const PelorusQpReportSection *)p;
    expected.qp_cell_offset = actual->qp_cell_offset;
    expected.qp_cell_size = actual->qp_cell_size;
    if (check_simple_fields(&expected, actual, sizeof(expected), "qp_report") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->qp_cell_offset, actual->qp_cell_size, VAL_CELL_COUNT,
                           (uint32_t)sizeof(int8_t), val_qp_cell_data, "qp_report");
}

/**
 * Verify motion confidence section header fields and confidence map.
 * Returns 0 on match or expected absence, 1 on error or mismatch.
 */
static int check_motion_conf(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusMotionConfSection expected;
    const PelorusMotionConfSection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_MOTION_CONF,
                                          sizeof(PelorusMotionConfSection), &p, &got);
    int pr =
        check_sec_presence(rc, got, sizeof(PelorusMotionConfSection), w_minor, 3u, "motion_conf");
    if (pr != 2) {
        return pr;
    }
    abih_fill_motion_conf(&expected);
    actual = (const PelorusMotionConfSection *)p;
    expected.conf_field_offset = actual->conf_field_offset;
    expected.conf_field_size = actual->conf_field_size;
    if (check_simple_fields(&expected, actual, sizeof(expected), "motion_conf") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->conf_field_offset, actual->conf_field_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_conf_field_data,
                           "motion_conf");
}

/**
 * Verify complexity section header fields match deterministic test values.
 * Returns 0 on match or expected absence, 1 on error or mismatch.
 */
static int check_complexity(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusComplexitySection expected;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_COMPLEXITY,
                                          sizeof(PelorusComplexitySection), &p, &got);
    int pr =
        check_sec_presence(rc, got, sizeof(PelorusComplexitySection), w_minor, 3u, "complexity");
    if (pr != 2) {
        return pr;
    }
    abih_fill_complexity(&expected);
    return check_simple_fields(&expected, p, sizeof(expected), "complexity");
}
#endif

#if PELORUS_ABI_MINOR >= 4
/**
 * Verify encoder telemetry section header and referenced telemetry maps.
 * Returns 0 on match or expected absence, 1 on error or mismatch.
 */
static int check_enc_telemetry(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusEncTelemetrySection expected;
    const PelorusEncTelemetrySection *actual;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_ENC_TELEMETRY,
                                          sizeof(PelorusEncTelemetrySection), &p, &got);
    int pr =
        check_sec_presence(rc, got, sizeof(PelorusEncTelemetrySection), w_minor, 4u, "telemetry");
    if (pr != 2) {
        return pr;
    }
    abih_fill_enc_telemetry(&expected);
    actual = (const PelorusEncTelemetrySection *)p;
    expected.qp_map_offset = actual->qp_map_offset;
    expected.qp_map_size = actual->qp_map_size;
    expected.mode_map_offset = actual->mode_map_offset;
    expected.mode_map_size = actual->mode_map_size;
    if (check_simple_fields(&expected, actual, sizeof(expected), "telemetry") != 0) {
        return 1;
    }
    if (check_map_bytes(blob, len, actual->qp_map_offset, actual->qp_map_size, VAL_CELL_COUNT,
                        (uint32_t)sizeof(int16_t), val_tlm_qp_map_data, "tlm_qp") != 0) {
        return 1;
    }
    return check_map_bytes(blob, len, actual->mode_map_offset, actual->mode_map_size,
                           VAL_CELL_COUNT, (uint32_t)sizeof(uint8_t), val_tlm_mode_map_data,
                           "tlm_mode");
}

/**
 * Verify encode record section header fields match deterministic test values.
 * Returns 0 on match or expected absence, 1 on error or mismatch.
 */
static int check_encode_record(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusEncodeRecordSection expected;
    pel_result rc = pel_blob_find_section(blob, len, PEL_SEC_ENCODE_RECORD,
                                          sizeof(PelorusEncodeRecordSection), &p, &got);
    int pr = check_sec_presence(rc, got, sizeof(PelorusEncodeRecordSection), w_minor, 4u, "record");
    if (pr != 2) {
        return pr;
    }
    abih_fill_encode_record(&expected);
    return check_simple_fields(&expected, p, sizeof(expected), "record");
}
#endif

#if PELORUS_ABI_MINOR >= 6
/**
 * Verify quality section header fields match deterministic test values.
 * Returns 0 on match or expected absence, 1 on error or mismatch.
 */
static int check_quality(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    const void *p = NULL;
    size_t got = 0;
    PelorusQualitySection expected;
    pel_result rc =
        pel_blob_find_section(blob, len, PEL_SEC_QUALITY, sizeof(PelorusQualitySection), &p, &got);
    int pr = check_sec_presence(rc, got, sizeof(PelorusQualitySection), w_minor, 6u, "quality");
    if (pr != 2) {
        return pr;
    }
    abih_fill_quality(&expected);
    return check_simple_fields(&expected, p, sizeof(expected), "quality");
}
#endif

/**
 * Verify base sections present in blob against expected minor version values.
 */
static int check_base_sections(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    return check_banding(blob, len, w_minor) != 0 || check_variance(blob, len, w_minor) != 0 ||
           check_denoise(blob, len, w_minor) != 0 || check_filmgrain(blob, len, w_minor) != 0 ||
           check_motion(blob, len, w_minor) != 0;
}

#if PELORUS_ABI_MINOR >= 3
/**
 * Verify minor 3 sections present in blob against expected minor version values.
 */
static int check_minor3_sections(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    return check_qpreport(blob, len, w_minor) != 0 || check_motion_conf(blob, len, w_minor) != 0 ||
           check_complexity(blob, len, w_minor) != 0;
}
#endif

#if PELORUS_ABI_MINOR >= 4
/**
 * Verify minor 4 sections present in blob against expected minor version values.
 */
static int check_minor4_sections(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    return check_enc_telemetry(blob, len, w_minor) != 0 ||
           check_encode_record(blob, len, w_minor) != 0;
}
#endif

/**
 * Verify all sections present in blob against expected minor version values.
 * Returns 0 on success, 1 on any section mismatch.
 */
static int check_all_sections(const uint8_t *blob, size_t len, uint16_t w_minor)
{
    if (check_base_sections(blob, len, w_minor) != 0) {
        return 1;
    }
#if PELORUS_ABI_MINOR >= 3
    if (check_minor3_sections(blob, len, w_minor) != 0) {
        return 1;
    }
#endif
#if PELORUS_ABI_MINOR >= 4
    if (check_minor4_sections(blob, len, w_minor) != 0) {
        return 1;
    }
#endif
#if PELORUS_ABI_MINOR >= 6
    if (check_quality(blob, len, w_minor) != 0) {
        return 1;
    }
#endif
    return 0;
}

/**
 * Validate header magic, ABI major, PTS, and producer identifier.
 */
static int verify_header_identity(const PelorusSideData *hdr)
{
    return memcmp(hdr->magic, PELORUS_MAGIC_STR, PELORUS_MAGIC_LEN) != 0 ||
           hdr->abi_major != (uint16_t)PELORUS_ABI_MAJOR || hdr->frame_pts != VAL_FRAME_PTS ||
           hdr->producer_id != VAL_PRODUCER_ID;
}

/**
 * Validate header geometry, grid dimensions, and container sizes.
 */
static int verify_header_layout(const PelorusSideData *hdr, size_t len)
{
    return hdr->plane_layout != (uint8_t)VAL_PLANE_LAYOUT ||
           hdr->bit_depth != (uint8_t)VAL_BIT_DEPTH || hdr->grid_cols != (uint16_t)VAL_GRID_COLS ||
           hdr->grid_rows != (uint16_t)VAL_GRID_ROWS ||
           hdr->total_size > len - PELORUS_SIDEDATA_UUID_LEN ||
           hdr->header_size < sizeof(PelorusSideData);
}

/**
 * Verify presence, header, UUID, and section contents of an unwrapped blob.
 * Returns 0 on valid blob matching expectations, 1 on mismatch or corruption.
 */
int abih_read_and_verify_blob(const uint8_t *blob, size_t len)
{
    PelorusSideData hdr;
    if (pel_blob_is_present(blob, len) != 1) {
        (void)fprintf(stderr, "FAIL: pel_blob_is_present != 1\n");
        return 1;
    }
    if (len < (size_t)PELORUS_SIDEDATA_UUID_LEN + sizeof(PelorusSideData)) {
        (void)fprintf(stderr, "FAIL: blob too short for header\n");
        return 1;
    }
    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    if (verify_header_identity(&hdr) != 0 || verify_header_layout(&hdr, len) != 0) {
        (void)fprintf(stderr, "FAIL: header field mismatch or truncation\n");
        return 1;
    }
    return check_all_sections(blob, len, hdr.abi_minor);
}
