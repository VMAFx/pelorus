/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#ifndef PELORUS_QUALITY_H
#define PELORUS_QUALITY_H

#include "pelorus/interop.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Upper bounds on align and shot sequence array sizes (HISS-02, HISS-06). */
#define PEL_QUALITY_ALIGN_MAX (1u << 20u)
#define PEL_SHOT_TARGET_MAX 4096u

/* Record of a coded frame from an encoder telemetry stream or log.
 * PTS is expressed in caller-asserted time base rec_tb_num / rec_tb_den. */
typedef struct PelQualityEncRecord {
    int64_t pts;    /* presentation timestamp in rec_tb */
    uint32_t index; /* stream or decode/display index     */
} PelQualityEncRecord;

/* Result of aligning one window to a sequence of encoder records. */
typedef struct PelQualityMatch {
    uint64_t window_id;    /* from PelorusQualitySection.window_id */
    uint32_t first_record; /* index of first matching record, or UINT32_MAX if none */
    uint32_t record_count; /* number of matching records in [pts_start, pts_end) */
    uint32_t matched;      /* 1 if >= 1 record matches, else 0 */
    uint32_t _pad;         /* reserved zero */
} PelQualityMatch;

/* Target CRF for one contiguous shot in a video stream. */
typedef struct PelShotTarget {
    uint32_t first_frame; /* first frame of shot, inclusive */
    uint32_t last_frame;  /* last frame of shot, inclusive  */
    float crf;            /* target CRF in [0, 63]          */
} PelShotTarget;

/*
 * Validate a PEL_SEC_QUALITY section struct against the interop specification (ADR-0214).
 * Rejects NULL, unknown metric_id, zero timebase, pts_end <= pts_start, non-finite score,
 * frame_count 0, or nonzero _pad with PEL_ERR_INVALID.
 */
pel_result pel_quality_validate(const PelorusQualitySection *q);

/*
 * Pack a PelorusSideData blob containing exactly one PEL_SEC_QUALITY section.
 * Validates `q` first. Allocates the blob via pel_blob_pack; free with pel_blob_free.
 */
pel_result pel_quality_pack(const PelorusSideData *meta, const PelorusQualitySection *q,
                            uint8_t **out, size_t *out_len);

/*
 * Find and parse the PEL_SEC_QUALITY section from a Pelorus side-data blob.
 * Copies min(readable size, sizeof) and zero-fills any remaining bytes (R4).
 * Validates the section before returning.
 */
pel_result pel_quality_find(const uint8_t *blob, size_t len, PelorusQualitySection *out);

/*
 * Align stream quality windows to encoder records by PTS using exact rational
 * arithmetic (cross-multiplication on int64 without floating point).
 *
 * Records must be sorted by pts ascending (else PEL_ERR_INVALID).
 * A record matches a window iff its PTS rescaled to the window time base falls in
 * [pts_start, pts_end). A window with no matching record receives matched=0,
 * record_count=0, first_record=UINT32_MAX. Nearest-neighbour matching is prohibited.
 *
 * Zero-fills out[0..n_windows) before matching when out != NULL and out_cap >= n_windows.
 * On error, out content is unspecified-but-zeroed up to n_windows.
 *
 * Returns PEL_OK, PEL_ERR_INVALID (NULL args, zero tb, unsorted records), or
 * PEL_ERR_RANGE (out_cap < n_windows, n_windows or n_records > PEL_QUALITY_ALIGN_MAX,
 * or integer overflow during rescaling).
 */
pel_result pel_quality_align(const PelorusQualitySection *windows, size_t n_windows,
                             const PelQualityEncRecord *records, size_t n_records,
                             uint32_t rec_tb_num, uint32_t rec_tb_den, PelQualityMatch *out,
                             size_t out_cap);

/*
 * Format shot sequence as an x265 `--zonefile` configuration.
 * Generates lines of `<first_frame> --crf <crf %u.%02u>\n`.
 * Shots must start at frame 0 (shots[0].first_frame == 0) and be contiguous
 * (first_frame == prev.last_frame + 1). Gaps, overlaps, or first_frame != 0
 * return PEL_ERR_INVALID. Decimal point formatting is locale-independent.
 *
 * Buffer conventions:
 * - need == NULL: returns PEL_ERR_INVALID.
 * - *need is set to 0 on entry.
 * - Validation failure returns PEL_ERR_INVALID with *need == 0.
 * - buf == NULL and cap == 0: returns PEL_OK and sets *need (including trailing NUL).
 * - buf == NULL and cap > 0: returns PEL_ERR_INVALID with *need == 0.
 * - cap < *need: returns PEL_ERR_RANGE and sets *need.
 * - cap >= *need: writes output into buf and returns PEL_OK.
 */
pel_result pel_zones_x265_zonefile(const PelShotTarget *shots, size_t n, char *buf, size_t cap,
                                   size_t *need);

/*
 * Format shot sequence as an x265 `--zones` / `x265-params zones=` string.
 * Generates `<start>,<end>,b=<mult>` joined by `/`, where mult = 2^((base_crf - crf)/6)
 * formatted as 4 decimals. Does not emit q= (ADR-0142).
 *
 * Ordinal conversion:
 * Emits `<first_frame+1>,<last_frame+1>,b=<mult>` converting 0-based frame indices
 * into 1-based x265 frame ordinals. In x265, `--zones` boundaries are 1-based
 * inclusive ordinals evaluated in encode order:
 * `m_framesDone + 1 >= z->startFrame && m_framesDone < z->endFrame`.
 *
 * Encode-order caveat:
 * Because `--zones` evaluates frames in encode order, when B-frames are enabled
 * display frames near a boundary can land on the wrong side by up to one mini-GOP.
 *
 * Shot-sequence rules and buffer conventions: as pel_zones_x265_zonefile().
 */
pel_result pel_zones_x265_param(const PelShotTarget *shots, size_t n, float base_crf, char *buf,
                                size_t cap, size_t *need);

/*
 * Format shot sequence as a segment plan CSV header and rows:
 *   first_frame,last_frame,crf\n
 *   <first_frame>,<last_frame>,<crf %u.%02u>\n
 * For chunked encoding with encoders lacking CLI zone controls (SVT-AV1, libaom).
 *
 * Shot-sequence rules and buffer conventions: as pel_zones_x265_zonefile().
 */
pel_result pel_zones_segment_plan(const PelShotTarget *shots, size_t n, char *buf, size_t cap,
                                  size_t *need);

#ifdef __cplusplus
}
#endif

#endif /* PELORUS_QUALITY_H */
