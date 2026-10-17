/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_GRAIN_TABLE_H
#define PELORUS_GRAIN_TABLE_H

#include <stddef.h>
#include <stdint.h>

#include "pelorus/interop.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * One entry in a libaom film-grain table.
 *
 * Timestamps:
 *   start_time and end_time represent presentation intervals in libaom
 *   encoder ticks: 10 MHz (TICKS_PER_SEC in libaom av1/encoder/encoder.h).
 *
 * Parameters:
 *   update_parameters indicates whether params carries a new parameter set
 *   (1) or reuses previous parameters with updated timestamps/seed (0).
 *   When update_parameters is 0, only start_time, end_time, params.apply,
 *   and params.seed are serialized.
 */
typedef struct PelorusGrainTableEntry {
    int64_t start_time;
    int64_t end_time;
    uint8_t update_parameters;
    PelorusFilmGrainSection params;
} PelorusGrainTableEntry;

/**
 * Parse a libaom-format film-grain table from a memory buffer.
 *
 * Parameters:
 *   text   Pointer to buffer holding ASCII table text.
 *   len    Length of buffer in bytes.
 *   out    Array of entries to populate.
 *   cap    Capacity of out array.
 *   count  Pointer receiving number of entries parsed. Set to entries parsed
 *          on success, or entries parsed before cap exceeded on PEL_ERR_TRUNCATED.
 *
 * Returns:
 *   PEL_OK on success.
 *   PEL_ERR_INVALID on NULL arguments, invalid magic, syntax error or truncated line.
 *   PEL_ERR_RANGE on parameter values outside valid ranges or non-increasing coordinates.
 *   PEL_ERR_TRUNCATED if more entries are present than cap allows.
 */
pel_result pel_grain_table_parse(const char *text, size_t len, PelorusGrainTableEntry *out,
                                 size_t cap, size_t *count);

/**
 * Serialize film-grain table entries into the libaom text format.
 *
 * Output matches libaom's grain_table_entry_write byte for byte, including
 * spacing quirks.
 *
 * Parameters:
 *   in       Array of entries to serialize.
 *   count    Number of entries in array (0 writes magic only).
 *   buf      Output character buffer.
 *   cap      Capacity of buf in bytes.
 *   written  Pointer receiving required number of bytes (excluding NUL).
 *            Always populated if non-NULL.
 *
 * Returns:
 *   PEL_OK on success.
 *   PEL_ERR_INVALID on NULL arguments.
 *   PEL_ERR_RANGE on parameter values outside valid ranges.
 *   PEL_ERR_TRUNCATED if cap is smaller than the required size. No write past cap.
 */
pel_result pel_grain_table_write(const PelorusGrainTableEntry *in, size_t count, char *buf,
                                 size_t cap, size_t *written);

#ifdef __cplusplus
}
#endif

#endif /* PELORUS_GRAIN_TABLE_H */
