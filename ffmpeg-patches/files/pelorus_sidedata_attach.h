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
 * Shared producer-side attach of a Pelorus interop blob to an AVFrame, the
 * writer half of pelorus_sidedata.h. Every producer packs its sections with
 * pel_blob_pack() and APPENDS the blob as its own
 * AV_FRAME_DATA_SEI_UNREGISTERED entry; the AVBufferRef frees it through
 * pel_blob_free(), the allocator that made it.
 *
 * Kept apart from pelorus_sidedata.h so the consumer header stays free of
 * buffer and pixdesc dependencies (its fast-suite test compiles it against a
 * stub libavutil/frame.h), and kept private static-inline so the public
 * interop ABI does not change.
 */

#ifndef AVFILTER_PELORUS_SIDEDATA_ATTACH_H
#define AVFILTER_PELORUS_SIDEDATA_ATTACH_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "libavutil/buffer.h"
#include "libavutil/error.h"
#include "libavutil/frame.h"
#include "libavutil/pixdesc.h"

#include <pelorus/interop.h>

/* AVBufferRef free callback for a pel_blob_pack() allocation. */
static inline void pel_sd_free(void *opaque, uint8_t *data)
{
    (void)opaque;
    pel_blob_free(data);
}

/* Zero `meta`, then fill the frame PTS, the bit depth and plane layout of
 * `fmt` and the producer FourCC. Grid fields stay 0 for the caller. */
static inline void pel_sidedata_init_meta(PelorusSideData *meta, const AVFrame *frame,
                                          enum AVPixelFormat fmt, uint32_t producer_id)
{
    const AVPixFmtDescriptor *d = av_pix_fmt_desc_get(fmt);

    memset(meta, 0, sizeof(*meta));
    meta->frame_pts = (uint64_t)frame->pts;
    meta->bit_depth = d ? (uint8_t)d->comp[0].depth : 0;
    meta->plane_layout = (d && d->log2_chroma_w == 0 && d->log2_chroma_h == 0) ?
                             PEL_LAYOUT_444 :
                             ((d && d->log2_chroma_h == 0) ? PEL_LAYOUT_422 : PEL_LAYOUT_420);
    meta->producer_id = producer_id;
}

/* Append `blob` (from pel_blob_pack()) to `frame` as a new
 * AV_FRAME_DATA_SEI_UNREGISTERED entry. Takes ownership of `blob` whether it
 * succeeds or fails. Returns 0 or AVERROR(ENOMEM). */
static inline int pel_sidedata_attach_blob(AVFrame *frame, uint8_t *blob, size_t len)
{
    AVBufferRef *buf = av_buffer_create(blob, len, pel_sd_free, NULL, 0);

    if (!buf) {
        pel_blob_free(blob);
        return AVERROR(ENOMEM);
    }
    if (!av_frame_new_side_data_from_buf(frame, AV_FRAME_DATA_SEI_UNREGISTERED, buf)) {
        av_buffer_unref(&buf);
        return AVERROR(ENOMEM);
    }
    return 0;
}

/* Pack `n_secs` sections under `meta` and append the blob to `frame`.
 * Returns 0 or AVERROR(ENOMEM). */
static inline int pel_sidedata_pack_and_attach(AVFrame *frame, const PelorusSideData *meta,
                                               const PelorusPackSection *secs, int n_secs)
{
    uint8_t *blob = NULL;
    size_t len = 0;

    if (pel_blob_pack(meta, secs, n_secs, &blob, &len) != PEL_OK || !blob)
        return AVERROR(ENOMEM);
    return pel_sidedata_attach_blob(frame, blob, len);
}

#endif /* AVFILTER_PELORUS_SIDEDATA_ATTACH_H */
