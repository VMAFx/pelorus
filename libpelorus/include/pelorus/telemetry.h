/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * telemetry.h — encoder-agnostic input for the PEL_SEC_ENC_TELEMETRY section
 * (interop ABI 1.4, ADR-0174, docs/api/encoder-telemetry.md).
 *
 * Any encoder adapter fills one plain-C struct: an FFmpeg wrapper, a stats-file
 * reader, a hardware-feedback reader, or a caller outside FFmpeg such as VMAFx's
 * codec-adapter package (issue #221, VMAFx/vmafx#2147). This header holds no
 * FFmpeg, SDK or OS type: it includes only pelorus/interop.h and the C standard
 * headers below, and the telemetry test compiles it without FFmpeg include paths.
 *
 * Contract for callers:
 *   - fill `frame` and set present_mask bits for exactly the fields you have
 *     (adapter = PEL_TLM_ADAPTER_EXTERNAL for a caller-filled record);
 *   - map pointers are yours: they are read during the call, never kept;
 *   - size one buffer with pel_enc_telemetry_blob_size() for your largest frame,
 *     then pel_enc_telemetry_pack() writes into it and allocates nothing;
 *   - an invalid record returns PEL_ERR_INVALID or PEL_ERR_RANGE, never a
 *     partial blob.
 */
#ifndef PELORUS_TELEMETRY_H
#define PELORUS_TELEMETRY_H

#include <stddef.h>
#include <stdint.h>

#include "pelorus/interop.h"

#ifdef __cplusplus
extern "C" {
#endif

/* One coded frame's telemetry plus its optional maps (map_cols * map_rows
 * elements each, row-major). The *_map_offset / *_map_size members of `frame`
 * are computed by the packer: leave them zero. */
typedef struct PelorusEncTelemetryInput {
    PelorusEncTelemetrySection frame; /* scalars, enums, present_mask             */
    const int16_t *qp_map;            /* native QP x 4 (Q2); NULL unless QP_MAP   */
    const uint32_t *bits_map;         /* coded bits; NULL unless BITS_MAP         */
    const uint8_t *mode_map;          /* enum pel_block_mode; NULL unless MODE_MAP */
} PelorusEncTelemetryInput;

/*
 * Check a record against docs/api/encoder-telemetry.md, "Validation".
 *
 * PEL_ERR_INVALID: NULL `in`; codec, granularity or adapter 0 or out of range;
 * an unknown present_mask bit; qp_scale or metric_source missing for its bits,
 * set without them, out of range, or a qp_scale that does not belong to the
 * codec; a value, flag, map pointer, map offset or map size set while its bit is
 * clear, or a map bit without its pointer; a picture_type or mode_map element out
 * of range; a map bit at frame granularity, no map at row or block granularity,
 * or map_cols != 1 at row granularity; a bit outside the adapter's capability
 * mask (pel_tlm_adapter_caps; not checked for PEL_TLM_ADAPTER_EXTERNAL).
 *
 * PEL_ERR_RANGE: a NaN or infinite float; a negative PSNR; ssim_y or a fraction
 * outside 0..1; the three fractions summing above 1 + 1e-3; header_bits +
 * residual_bits > frame_bytes * 8 when all three are present; avg_qp or a
 * qp_map element outside its scale's range; coded_bit_depth not 8, 10 or 12;
 * map_cols or map_rows 0, their product above PEL_TLM_MAP_MAX_ELEMS, or
 * block_size_log2 outside 2..7.
 */
pel_result pel_enc_telemetry_validate(const PelorusEncTelemetryInput *in);

/*
 * The blob length pel_enc_telemetry_pack() needs for this record: UUID,
 * header, one directory entry, the section and its maps. Validates `in` first.
 * Returns PEL_OK, PEL_ERR_INVALID (NULL arguments) or a validation result.
 */
pel_result pel_enc_telemetry_blob_size(const PelorusEncTelemetryInput *in, size_t *out_len);

/*
 * Validate `in` and write the UUID-prefixed blob into `buf`: header (`meta`
 * supplies frame_pts, producer_id and the other caller fields), one directory
 * entry, the section with its map offsets and sizes filled in, then each present
 * map at an 8-aligned blob-relative offset. Allocates nothing.
 *
 * Returns PEL_OK with *out_len set to the blob length, PEL_ERR_INVALID (NULL
 * arguments), a validation result, or PEL_ERR_RANGE when cap is shorter than the
 * blob (then *out_len holds the length needed and buf is unchanged).
 */
pel_result pel_enc_telemetry_pack(const PelorusSideData *meta, const PelorusEncTelemetryInput *in,
                                  uint8_t *buf, size_t cap, size_t *out_len);

/*
 * The static capability mask of an adapter for a codec: the PEL_TLM_F_* bits it
 * can fill. A record's present_mask must be a subset of it.
 *
 * PEL_TLM_ADAPTER_EXTERNAL reports every bit this ABI minor defines. The other
 * adapters' tables land with the adapters themselves (#86 items 2-4); until then
 * they report PEL_ERR_UNSUPPORTED with *out_mask = 0, so only an empty record
 * validates for them. Returns PEL_ERR_INVALID for a NULL out_mask or an adapter
 * or codec out of range.
 */
pel_result pel_tlm_adapter_caps(uint8_t adapter, uint8_t codec, uint64_t *out_mask);

/*
 * The H.264-equivalent QP of `qp` in `qp_scale`: 6 * log2(Qstep / 0.625), with
 * Qstep the frame's luma AC step in H.264 units at 8 bits.
 *
 * PEL_QP_SCALE_SLICE_QP is the identity. The base_q_idx scales (AV1, VP9 and the
 * AV1 encoder --qp) need the codec's quantiser table; those constants are not
 * verified yet (research 0174, E3) and return PEL_ERR_UNSUPPORTED, so an adapter
 * leaves PEL_TLM_F_AVG_QP_NORM clear. Returns PEL_ERR_INVALID for a NULL
 * out_norm or an unknown scale, and PEL_ERR_RANGE for a coded_bit_depth other
 * than 8, 10 or 12 or a qp outside the scale. *out_norm is written only on
 * PEL_OK.
 */
pel_result pel_qp_normalize(uint8_t qp_scale, uint8_t coded_bit_depth, float qp, float *out_norm);

/*
 * Reader: locate one map of a telemetry record after reader checks 1 to 5
 * (docs/api/encoder-telemetry.md, "Maps").
 *
 *   blob, len  the UUID-prefixed blob the section came from.
 *   t, got     the section (copied to an aligned local) and its readable size.
 *   map_bit    PEL_TLM_F_QP_MAP, PEL_TLM_F_BITS_MAP or PEL_TLM_F_MODE_MAP.
 *   out_ptr    receives the map (no copy); out_elems map_cols * map_rows.
 *
 * Returns PEL_OK, PEL_ERR_ABSENT (bit clear or frame granularity: the map is
 * ignored), PEL_ERR_INVALID (NULL arguments, another map_bit, got shorter than
 * the section), PEL_ERR_ABI (bad geometry, size or alignment) or
 * PEL_ERR_TRUNCATED (the map leaves the blob).
 */
pel_result pel_enc_telemetry_map(const uint8_t *blob, size_t len,
                                 const PelorusEncTelemetrySection *t, size_t got, uint64_t map_bit,
                                 const void **out_ptr, uint32_t *out_elems);

#ifdef __cplusplus
}
#endif
#endif /* PELORUS_TELEMETRY_H */
