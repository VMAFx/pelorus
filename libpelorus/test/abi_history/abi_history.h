/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_TEST_ABI_HISTORY_H
#define PELORUS_TEST_ABI_HISTORY_H

/**
 * Shared declarations, memory buffer types, and cross-module function
 * prototypes for the historical release interop ABI test tool.
 */

#include <stddef.h>
#include <stdint.h>

#include "pelorus/interop.h"

typedef struct AlignedBlob16k {
    _Alignas(8) uint8_t bytes[16384];
} AlignedBlob16k;

typedef struct AlignedBlob32k {
    _Alignas(8) uint8_t bytes[32768];
} AlignedBlob32k;

/* Deterministic section population helpers (abi_history_fill.c). */
void abih_fill_banding(PelorusBandingSection *s);
void abih_fill_variance(PelorusVarianceSection *s);
void abih_fill_denoise(PelorusDenoiseSection *s);
void abih_fill_filmgrain(PelorusFilmGrainSection *s);
void abih_fill_motion(PelorusMotionSection *s);

#if PELORUS_ABI_MINOR >= 3
void abih_fill_qpreport(PelorusQpReportSection *s);
void abih_fill_motion_conf(PelorusMotionConfSection *s);
void abih_fill_complexity(PelorusComplexitySection *s);
#endif

#if PELORUS_ABI_MINOR >= 4
void abih_fill_enc_telemetry(PelorusEncTelemetrySection *s);
void abih_fill_encode_record(PelorusEncodeRecordSection *s);
#endif

#if PELORUS_ABI_MINOR >= 6
void abih_fill_quality(PelorusQualitySection *s);
#endif

/* History blob packing entry point (abi_history_pack.c). */
pel_result abih_pack_history_blob(uint8_t *blob_out, size_t cap, size_t *out_len);

/* History blob verification entry point (abi_history_check.c). */
int abih_read_and_verify_blob(const uint8_t *blob, size_t len);

#endif /* PELORUS_TEST_ABI_HISTORY_H */
