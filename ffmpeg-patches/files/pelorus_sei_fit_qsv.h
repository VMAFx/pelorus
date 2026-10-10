/*
 * Copyright 2026 Lusoris
 *
 * This file is part of FFmpeg.
 *
 * FFmpeg is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at
 * your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
 * License for more details.
 */

/*
 * Fit user data unregistered SEI payloads into the SEI space the Intel QSV
 * runtime reserves per picture (issue #286). Plain C with no libav* and no
 * libpelorus dependency: libavcodec/qsvenc.c includes it, and Pelorus's fast
 * suite tests it (ffmpeg-patches/test/sei_fit_test.c). The Pelorus blob
 * stripper is pelorus_sei_fit.h's (patch 0022); this file only adds the QSV
 * budget and its cost model.
 *
 * Measured on an Intel Arc A380 (iHD 26.3.5, vpl-gpu-rt 26.3.5, libvpl 2.17.0),
 * 1080p, 640x360 and 2160p alike, docs/usage/ffmpeg.md:
 *
 * - The limit is per picture, not per payload: it is the sum of the
 *   mfxPayload.BufSize values (SEI message type byte, size bytes, payload)
 *   passed in one mfxEncodeCtrl. hevc_qsv damages the access unit once that
 *   sum passes 4 107 bytes (one payload of UUID + data = 4 089 bytes, or two
 *   of 2 043 and 2 044). Emulation prevention bytes do not count. h264_qsv
 *   fails the encode once the sum plus the emulation prevention bytes of the
 *   payloads passes about 42 420 bytes (42 255 bytes in one payload, or two of
 *   21 125); a zero-heavy payload hits it earlier.
 * - Both budgets stay below that with a margin: 4 040 bytes (67 bytes, 1.6 %)
 *   for HEVC and 40 960 bytes (about 1 460 bytes, 3.4 %) for H.264. The
 *   margin covers SEI the runtime writes on its own (pic_timing, buffering
 *   period) in case it shares the space.
 *
 * H.264 emulation prevention bytes are counted per payload in isolation: the
 * bytes of payloads already queued (the A/53 caption payload) and the type and
 * size header bytes of the message are not scanned, so a run of zeros across
 * such a boundary can add a byte or two the cost misses. The 3.4 % margin
 * covers that.
 *
 * A Pelorus blob that does not fit is written without the per-cell maps after
 * its sections (pel_sei_scalar_len() and pel_sei_strip_maps(), the same
 * stripping hevc_nvenc applies, ADR-0181); anything else that does not fit,
 * and a blob that still does not fit, is dropped. QSV writes a Pelorus blob in
 * its plain form: the zero-free carrier of ADR-0183 is for NVENC.
 */

#ifndef AVCODEC_PELORUS_SEI_FIT_QSV_H
#define AVCODEC_PELORUS_SEI_FIT_QSV_H

#include "pelorus_sei_fit.h"

/* Per picture, bytes of mfxPayload.BufSize (plus, for H.264, emulation
 * prevention bytes). */
#define PEL_SEI_QSV_HEVC_BUDGET 4040u
#define PEL_SEI_QSV_H264_BUDGET 40960u

/* Bytes of the SEI message the encoder is handed for an `n`-byte payload:
 * type byte, n / 255 + 1 size bytes, the payload (mfxPayload.BufSize). */
static inline size_t pel_sei_qsv_msg_len(size_t n)
{
    return n + n / 255u + 2u;
}

/* Budget bytes the `n`-byte payload `p` costs. `escaped` is 1 for H.264, whose
 * runtime counts the emulation prevention bytes. Only a payload that can fit
 * `limit` is scanned; a larger one returns more than `limit`. */
static inline size_t pel_sei_qsv_cost(const uint8_t *p, size_t n, int escaped, size_t limit)
{
    const size_t msg = pel_sei_qsv_msg_len(n);

    if (!escaped || n > limit || !p)
        return msg;
    return msg + pel_sei_epb(p, n);
}

/* Upper bound on the emulation prevention bytes the zeroed map fields of a
 * stripped blob add (five 8-byte fields: at most one byte per two zeros). */
#define PEL_SEI_QSV_STRIP_SLACK 24u

/*
 * How many bytes of the `n`-byte payload `p` to write when `budget` bytes are
 * left in the picture, decided without a copy so that a payload to drop is
 * never allocated: `n` when it fits whole, the length of the Pelorus blob
 * without its maps when that fits (an upper bound on its cost, so
 * pel_sei_qsv_fit() below never refuses it), 0 to drop.
 */
static inline size_t pel_sei_qsv_plan(const uint8_t *p, size_t n, int escaped, size_t budget)
{
    size_t keep;

    if (pel_sei_qsv_cost(p, n, escaped, budget) <= budget)
        return n;
    keep = pel_sei_scalar_len(p, n);
    if (!keep)
        return 0;
    return pel_sei_qsv_cost(p, keep, escaped, budget) + (escaped ? PEL_SEI_QSV_STRIP_SLACK : 0u) <=
                   budget
               ? keep
               : 0;
}

/*
 * Decide what to write of the `n`-byte payload `p` when `budget` bytes are
 * left in the picture. `out` holds `n` bytes and does not overlap `p`.
 * Returns the number of bytes copied to `out` to write: `n` when the whole
 * payload fits, the length of the Pelorus blob without its maps (UUID
 * included, maps zeroed in the copy) when only that fits, 0 to drop it.
 */
static inline size_t pel_sei_qsv_fit(const uint8_t *p, size_t n, int escaped, size_t budget,
                                     uint8_t *out)
{
    size_t keep;

    if (pel_sei_qsv_cost(p, n, escaped, budget) <= budget) {
        memcpy(out, p, n);
        return n;
    }
    keep = pel_sei_scalar_len(p, n);
    if (!keep)
        return 0;
    memcpy(out, p, keep);
    pel_sei_strip_maps(out, keep);
    return pel_sei_qsv_cost(out, keep, escaped, budget) <= budget ? keep : 0;
}

#endif /* AVCODEC_PELORUS_SEI_FIT_QSV_H */
