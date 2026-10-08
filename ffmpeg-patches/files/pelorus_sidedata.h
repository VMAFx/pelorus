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
 * Shared consumer-side lookup of Pelorus interop sections on an AVFrame.
 *
 * Every Pelorus producer APPENDS its own AV_FRAME_DATA_SEI_UNREGISTERED entry
 * (av_frame_new_side_data_from_buf, n9.0.2 libavutil/side_data.c, never merges
 * or replaces), so a frame that passed through a chain of producers carries one
 * blob per producer. A consumer must scan all entries instead of taking the
 * first one, which av_frame_get_side_data() returns.
 *
 * Precedence: entries are scanned newest first (highest index = attached
 * last); the newest valid Pelorus blob that contains the requested section
 * wins. A section's grid description (PelorusSideData.grid_cols/rows) belongs
 * to the blob that carries it, so callers read it from the returned entry.
 *
 * Kept as a private static-inline helper rather than a libpelorus API so the
 * public interop ABI (vendored byte-for-byte by vmafx) does not change.
 */

#ifndef AVFILTER_PELORUS_SIDEDATA_H
#define AVFILTER_PELORUS_SIDEDATA_H

#include <stddef.h>
#include <stdint.h>

#include "libavutil/frame.h"

#include <pelorus/interop.h>

/* True when the readable size `got` (R4: min(producer, consumer-known)) covers
 * field `f` of section struct `T`. An older, shorter producer section must be
 * checked per field before any read. */
#define PEL_SD_FIELD_OK(got, T, f) ((got) >= offsetof(T, f) + sizeof(((const T *)0)->f))

/* Newest Pelorus SEI_UNREGISTERED entry carrying `sec`; NULL if none. On
 * success *out_ptr / *out_size receive the section pointer and the number of
 * bytes the consumer may read (see PEL_SD_FIELD_OK). */
static inline const AVFrameSideData *pelorus_sd_find_section(const AVFrame *frame,
                                                             enum pel_section sec,
                                                             size_t consumer_known_size,
                                                             const void **out_ptr, size_t *out_size)
{
    int i;

    if (!frame)
        return NULL;
    for (i = frame->nb_side_data - 1; i >= 0; i--) {
        const AVFrameSideData *sd = frame->side_data[i];
        const void *p = NULL;
        size_t got = 0;

        if (!sd || sd->type != AV_FRAME_DATA_SEI_UNREGISTERED || !sd->data)
            continue;
        if (pel_blob_find_section(sd->data, sd->size, sec, consumer_known_size, &p, &got) ==
                PEL_OK &&
            p != NULL) {
            *out_ptr = p;
            *out_size = got;
            return sd;
        }
    }
    return NULL;
}

/* Block-edge range of vf_pelorus_mc's `bsize` option (8..PEL_MC_BLOCK_DIM). */
#define PEL_SD_MC_BSIZE_MIN 8
#define PEL_SD_MC_BSIZE_MAX 32
/* vf_pelorus_mc's `bsize` default; assumed only when the grid is ambiguous. */
#define PEL_SD_MC_BSIZE_DEFAULT 16

/*
 * Recover the motion-grid cell pitch (the producer's block edge, luma pixels)
 * from the frame size and the grid the blob describes. The producer lays the
 * grid out as cols = ceil(W / b), rows = ceil(H / b) (vf_pelorus_mc_vulkan.c),
 * and an ABI 1.3 PelorusMotionSection carries no block-size field, so the pitch
 * is the integer b in [MIN, MAX] that reproduces BOTH dimensions exactly. ABI
 * 1.4 producers name the edge; use pelorus_mc_block_pitch(), which prefers it. ceil(W /
 * cols) is NOT equivalent: e.g. W=100, b=32 gives 4 cols but ceil(100/4)=25.
 *
 * Returns 1 and sets *pitch when exactly one b fits. When several fit (small
 * frames: 96x64 with a 6x4 grid fits b = 16..19) and vf_pelorus_mc's default
 * block edge is one of them, returns 2 and sets *pitch to that default: the
 * common configuration keeps motion compensation, and the caller must say it
 * assumed the default. Returns 0 when none fits (grid is not an mc grid of
 * this frame) or the ambiguity excludes the default; a wrong pick would
 * misaddress cells, so the caller must not run motion compensation then.
 */
static inline int pelorus_mc_cell_pitch(int width, int height, int cols, int rows, int *pitch)
{
    int b, found = 0, pick = 0, has_default = 0;

    if (width <= 0 || height <= 0 || cols <= 0 || rows <= 0)
        return 0;
    for (b = PEL_SD_MC_BSIZE_MIN; b <= PEL_SD_MC_BSIZE_MAX; b++) {
        if ((width + b - 1) / b == cols && (height + b - 1) / b == rows) {
            found++;
            pick = b;
            has_default |= b == PEL_SD_MC_BSIZE_DEFAULT;
        }
    }
    if (found == 1) {
        *pitch = pick;
        return 1;
    }
    if (found > 1 && has_default) {
        *pitch = PEL_SD_MC_BSIZE_DEFAULT;
        return 2;
    }
    return 0;
}

/*
 * The motion-grid cell pitch of a PEL_SEC_MOTION section `mo` whose readable
 * size is `got`. An ABI 1.4 producer names its block edge in block_size_log2
 * (#218). When `got` covers that field and it is non-zero, the pitch is
 * 1 << block_size_log2: it must lie in [MIN, MAX] and reproduce the grid, or the
 * section contradicts itself and 0 is returned. An ABI 1.3 section (`got` ends
 * before the field, detected by size, not by value) or a 0 ("not reported",
 * e.g. bsize=12) falls back to pelorus_mc_cell_pitch() and returns its result.
 * Returns 1 for an exact pitch, 2 for the assumed default, 0 for none.
 */
static inline int pelorus_mc_block_pitch(const PelorusMotionSection *mo, size_t got, int width,
                                         int height, int cols, int rows, int *pitch)
{
    int b;

    if (!mo || !PEL_SD_FIELD_OK(got, PelorusMotionSection, block_size_log2) ||
        mo->block_size_log2 == 0)
        return pelorus_mc_cell_pitch(width, height, cols, rows, pitch);
    if (mo->block_size_log2 > 5)
        return 0;
    b = 1 << mo->block_size_log2;
    if (b < PEL_SD_MC_BSIZE_MIN || b > PEL_SD_MC_BSIZE_MAX || width <= 0 || height <= 0 ||
        (width + b - 1) / b != cols || (height + b - 1) / b != rows)
        return 0;
    *pitch = b;
    return 1;
}

#endif /* AVFILTER_PELORUS_SIDEDATA_H */
