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
 * reader, a hardware-feedback reader, or a caller outside FFmpeg (issue #221).
 * VMAFx's codec-adapter package (VMAFx/vmafx#2147) may fill and validate it,
 * but only Pelorus packs a blob under the Pelorus UUID (single-writer
 * invariant, docs/api/interop-abi.md); VMAFx uses its own UUID. This header holds no
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
 * PEL_TLM_ADAPTER_EXTERNAL reports every bit this ABI minor defines. X265_CSV
 * with HEVC, SVTAV1_STAT_FILE / LIBAOM_STATS with AV1 and VVENC_LOG with VVC
 * report their adapter fields. FFMPEG_QUALITY_STATS reports its
 * codec-specific fields. Other adapter/codec pairs report PEL_ERR_UNSUPPORTED
 * with *out_mask = 0. Returns PEL_ERR_INVALID for a NULL out_mask or an
 * adapter or codec out of range.
 */
pel_result pel_tlm_adapter_caps(uint8_t adapter, uint8_t codec, uint64_t *out_mask);

/*
 * The H.264-equivalent QP of `qp` in `qp_scale`: 6 * log2(Qstep / 0.625), with
 * Qstep the frame's luma AC step in H.264 units at 8 bits.
 *
 * PEL_QP_SCALE_SLICE_QP is the identity. AV1_ENCODER_QP maps the public 0..63
 * scale through the AV1 luma AC quantiser table. Raw AV1 and VP9 qindex scales
 * remain unsupported. Returns PEL_ERR_INVALID for a NULL out_norm or an unknown
 * scale, and PEL_ERR_RANGE for a coded_bit_depth other than 8, 10 or 12 or a qp
 * outside the scale. *out_norm is written only on PEL_OK.
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

/* Caller-associated values for one libaom frame packet. No libaom dependency.
 * Indices are caller-owned 0-based orders, not raw packet PTS (timebase units).
 * Query quantizer_64 immediately for the corresponding coded frame; use -1
 * when unavailable. has_psnr is 0 or 1; psnr = overall, Y, U, V. */
typedef struct PelTlmLibaomFrame {
    uint32_t frame_flags;
    uint64_t frame_bytes;
    int32_t quantizer_64;
    uint32_t has_psnr;
    double psnr[4];
    uint32_t display_index;
    uint32_t decode_index;
} PelTlmLibaomFrame;

/* Convert caller-associated libaom values into one AV1 frame record, then
 * validate it. KEY/INTRAONLY map to I, other frames to the ABI's AV1 P type.
 * Reference/shown status is not reported. Zero bytes is a reported zero,
 * including without DROPPABLE; no inference about packet validity is made.
 * Encoder-private frame_flags bits 16-31 are ignored, never interpreted.
 * Returns PEL_ERR_INVALID for NULL arguments, unknown flag bits within
 * 0x0000FFFF or has_psnr outside 0..1; PEL_ERR_RANGE for bytes > UINT32_MAX,
 * quantizer outside -1..63, depth outside 8/10/12, or NaN/negative PSNR
 * (all four values when present).
 * PSNR above 100 dB, including +infinity, is capped; overall is validated but
 * has no output slot. With has_psnr=0 the array is ignored. With quantizer=-1
 * both QP bits and qp_scale stay clear. On error, out stays untouched. */
pel_result pel_tlm_from_libaom(const PelTlmLibaomFrame *in, uint8_t coded_bit_depth,
                               PelorusEncTelemetryInput *out);

/*
 * Parse an x265 `--csv --csv-log-level 1` text buffer into caller-owned frame
 * records. `coded_bit_depth` is the encoded stream depth; the CSV does not
 * carry it. The parser performs no file I/O or per-record allocation; it owns
 * one transient C numeric-locale handle for the call.
 *
 * Input text is bounded at 1,048,576 bytes, each line at 4,096 bytes, and an
 * x265 CSV row at 256 fields. These limits bound both parser passes and all
 * nested scans independently of caller-controlled input.
 *
 * Returns PEL_OK, PEL_ERR_INVALID for bad arguments or malformed/shifted fields,
 * PEL_ERR_ABSENT for an empty buffer, PEL_ERR_TRUNCATED for a short row, or
 * PEL_ERR_RANGE for invalid numeric ranges, an oversized input or line, too
 * many CSV fields, or insufficient record capacity; PEL_ERR_NOMEM means the
 * locale handle could not be created.
 * No output record is written on error. `*out_count` is zero on error except
 * insufficient capacity, when it is the required record count.
 */
pel_result pel_tlm_parse_x265_csv(const char *text, size_t len, uint8_t coded_bit_depth,
                                  PelorusEncTelemetryInput *out_frames, size_t cap,
                                  size_t *out_count);

/*
 * Parse an SVT-AV1 `--enable-stat-report 1 --stat-file` text buffer. Argument,
 * allocation, error and output semantics match pel_tlm_parse_x265_csv().
 */
pel_result pel_tlm_parse_svtav1_stat(const char *text, size_t len, uint8_t coded_bit_depth,
                                     PelorusEncTelemetryInput *out_frames, size_t cap,
                                     size_t *out_count);

/*
 * Parse an FFmpeg AV_PKT_DATA_QUALITY_STATS side-data packet buffer into one
 * PelorusEncTelemetryInput record (adapter PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS).
 *
 * Buffer layout (libavcodec/packet.h (FFmpeg n9.0)):
 *   u32le quality (lambda = QP * 118, or 0 if QP not reported)
 *   u8    picture_type (AV_PICTURE_TYPE_NONE=0, I=1, P=2, B=3, S=4, SI=5, SP=6, BI=7)
 *   u8    error_count (number of u64le SSE entries following)
 *   u16   reserved (ignored; FFmpeg never writes them)
 *   u64le sse[error_count]
 *
 * `codec` and `qp_scale` specify the encoded stream format; `coded_bit_depth`
 * must be 8, 10 or 12. AV1 accepts only PEL_QP_SCALE_AV1_ENCODER_QP (qindex scale
 * cannot normalise). For VP9, the FFmpeg value is a libvpx 0..63 quantizer with no
 * PEL scale: quality is ignored entirely (AVG_QP bits clear), while picture type and
 * SSE are parsed; caller passes PEL_QP_SCALE_VP9_QINDEX.
 *
 * If `plane_samples` is non-NULL, nonzero elements supply the sample count for each
 * plane (0=Y, 1=U, 2=V) to convert SSE into PSNR (dB). PSNR capped at 100 dB (SSE 0
 * or tiny MSE). Quality 0 leaves AVG_QP clear; a QP 0 (lossless) frame also writes
 * quality 0 and reports no QP.
 *
 * Total length must be at least 8 + 8*error_count; trailing bytes are ignored
 * because FFmpeg may reuse an existing side-data buffer that is larger.
 *
 * Returns PEL_OK; PEL_ERR_INVALID for NULL arguments or codec/scale mismatch (such
 * as AV1 with PEL_QP_SCALE_AV1_QINDEX); PEL_ERR_TRUNCATED if len < 8 or
 * len < 8 + 8*error_count; PEL_ERR_RANGE for pict_type > 7, QP outside scale range,
 * SSE > peak^2 * samples (negative PSNR), or unsupported bit depth (!= 8, 10, 12).
 * On any error, the whole call is refused and `out` is left untouched.
 */
pel_result pel_tlm_parse_ffmpeg_quality_stats(const uint8_t *buf, size_t len, uint8_t codec,
                                              uint8_t qp_scale, uint8_t coded_bit_depth,
                                              const uint64_t plane_samples[3],
                                              PelorusEncTelemetryInput *out);

/*
 * Parse a VVenC (Fraunhofer HHI VVC) verbose log buffer (`-v 4` / `notice`,
 * `-v 5` / `verbose`, `-v 6` / `details`) into caller-owned frame records.
 * Only per-frame `POC` lines are parsed; all startup configuration lines,
 * notices, and summary lines are ignored. Lines starting with `TId:` or
 * containing `-SLICE` without a `POC` prefix are treated as malformed/shifted
 * frame lines. The bit count must be an exact multiple of 8 as emitted by
 * EncGOP.cpp (numRBSPBytes * 8).
 *
 * Argument, allocation, and buffer limits match pel_tlm_parse_x265_csv():
 * input text bounded at 1,048,576 bytes, each line at 4,096 bytes.
 *
 * Return causes:
 * - PEL_OK: all frame records successfully parsed, or input contains zero frame
 *   records (*out_count is set to the record count, 0 if empty or no POC lines).
 * - PEL_ERR_INVALID: NULL text, out_frames, or out_count; cap == 0; malformed
 *   POC prefix, non-numeric POC, unknown slice character, inverted tokens, or
 *   bit count not divisible by 8.
 * - PEL_ERR_TRUNCATED: a POC frame line is truncated before bits, within the
 *   slice parentheses, or inside the PSNR brackets.
 * - PEL_ERR_RANGE: coded_bit_depth other than 8, 10, or 12; input text exceeds
 *   1,048,576 bytes; a line exceeds 4,096 bytes; numeric values out of valid
 *   domain (QP outside [-6 * (depth - 8), 63], temporal layer > 6); or cap is
 *   smaller than the parsed record count (in which case *out_count holds the
 *   required record capacity).
 * - PEL_ERR_NOMEM: the transient C numeric-locale handle could not be created.
 *
 * No output record is written on error. *out_count is zero on error except for
 * insufficient capacity, where it receives the required record count.
 */
pel_result pel_tlm_parse_vvenc_log(const char *text, size_t len, uint8_t coded_bit_depth,
                                   PelorusEncTelemetryInput *out_frames, size_t cap,
                                   size_t *out_count);

#ifdef __cplusplus
}
#endif
#endif /* PELORUS_TELEMETRY_H */
