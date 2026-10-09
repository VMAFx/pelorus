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
 * Host-side cell grid, per-cell scores and map packing for
 * vf_pelorus_analyze_vulkan (issue #219, ADR-0177, docs/api/interop-abi.md
 * "Analysis maps"). Plain C on libpelorus's interop API with no libav*
 * dependency, so Pelorus's fast suite compiles and tests it directly
 * (ffmpeg-patches/test/analyze_maps_test.c).
 *
 * Grid: cells are `cell` x `cell` luma pixels, `cell` a power of two in
 * PEL_AN_CELL_MIN..PEL_AN_CELL_MAX; the grid is ceil(W / cell) x
 * ceil(H / cell), row-major from the top-left, with a partial last column and
 * row when the frame is not a multiple of the cell. A frame smaller than one
 * cell is a 1x1 grid. The blob header's grid_cols / grid_rows name it.
 *
 * Maps (grid_cols * grid_rows elements each, behind the ABI 1.0 offset/size
 * fields, so no wire layout changes):
 *   PelorusBandingSection.cell_data_*   uint8  round(255 * banding score)
 *   PelorusVarianceSection.var_cell_*   float  luma variance, [0, 1] domain
 *   PelorusVarianceSection.edge_cell_*  uint8  round(255 * edge density)
 * Each map starts at the next 8-aligned blob-relative offset after the packed
 * sections (the pel_blob_map() convention) and total_size ends at the last map
 * byte.
 */

#ifndef AVFILTER_PELORUS_ANALYZE_MAPS_H
#define AVFILTER_PELORUS_ANALYZE_MAPS_H

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <pelorus/interop.h>

/* Cell edge range in luma pixels; the edge is also a power of two. */
#define PEL_AN_CELL_MIN 8u
#define PEL_AN_CELL_MAX 64u
/* Workgroup edge cap: 32 x 32 = 1024 invocations, Vulkan's guaranteed
 * maxComputeWorkGroupInvocations. A 64-pixel cell runs a 2 x 2 span per
 * invocation (pelorus_analyze.comp.glsl, constant_id 1). */
#define PEL_AN_WG_MAX 32u
/* Grid bound (HISS-02): grid_cols * grid_rows <= this. 8192 x 8192 at cell 8 is
 * exactly the bound; the three maps then carry 6 MiB. */
#define PEL_AN_MAX_CELLS (1u << 20u)
/* Fixed-point scale shared by the shader writes and the host reads. */
#define PEL_AN_GS 1000000.0
/* Per-tile variance floor: a tile at or below it is constant, not a ramp. */
#define PEL_AN_VAR_LO 2e-6f

#define PEL_AN_ALIGN8(x) (((x) + 7u) & ~7u)

/* The shader's per-tile records, struct of arrays, each cols * rows long. */
typedef struct PelAnTiles {
    const uint32_t *var;   /* tile luma variance          * PEL_AN_GS        */
    const uint32_t *edge;  /* tile mean edge density      * PEL_AN_GS        */
    const uint32_t *grad;  /* tile low-amplitude gradient * PEL_AN_GS        */
    const uint32_t *valid; /* 1 if the tile had in-bounds pixels             */
    const uint32_t *mean;  /* tile mean luma              * PEL_AN_GS        */
    int cols;
    int rows;
} PelAnTiles;

/* The two detector thresholds of the filter's `flat` and `grad_lo` options. */
typedef struct PelAnThresholds {
    float flat_thr;
    float grad_lo;
} PelAnThresholds;

/* The per-cell maps of one frame plus their scratch, cols * rows each. */
typedef struct PelAnMaps {
    float *score;  /* banding score [0, 1]: the ROI input */
    uint8_t *band; /* round(255 * score)                  */
    float *var;    /* luma variance [0, 1]                */
    uint8_t *edge; /* round(255 * edge density)           */
} PelAnMaps;

/* The sections of one analyze blob. The packer writes the map offset/size
 * members of `var` and `band`; leave them zero. */
typedef struct PelAnSections {
    PelorusVarianceSection var;
    PelorusBandingSection band;
    PelorusComplexitySection cx;
} PelAnSections;

static inline float pel_an_clampf(float a, float lo, float hi)
{
    a = a > lo ? a : lo; /* NaN maps to lo, as FFMIN(FFMAX(a, lo), hi) does */
    return a < hi ? a : hi;
}

static inline int pel_an_cell_ok(uint32_t cell)
{
    return cell >= PEL_AN_CELL_MIN && cell <= PEL_AN_CELL_MAX && (cell & (cell - 1u)) == 0u;
}

/* Workgroup edge for a cell: the cell itself, capped at PEL_AN_WG_MAX. */
static inline uint32_t pel_an_workgroup(uint32_t cell)
{
    return cell < PEL_AN_WG_MAX ? cell : PEL_AN_WG_MAX;
}

/*
 * The grid of a width x height frame at `cell`. PEL_OK; PEL_ERR_INVALID for
 * NULL outputs, a zero dimension or a bad cell edge; PEL_ERR_RANGE for a grid
 * above PEL_AN_MAX_CELLS or wider or taller than 65535 cells. On error both
 * outputs are 0.
 */
static inline pel_result pel_an_grid(uint32_t width, uint32_t height, uint32_t cell,
                                     uint16_t *out_cols, uint16_t *out_rows)
{
    uint32_t cols;
    uint32_t rows;

    if (!out_cols || !out_rows)
        return PEL_ERR_INVALID;
    *out_cols = 0;
    *out_rows = 0;
    if (width == 0u || height == 0u || !pel_an_cell_ok(cell))
        return PEL_ERR_INVALID;
    /* ceil without width + cell - 1, which could wrap near UINT32_MAX */
    cols = width / cell + (width % cell != 0u ? 1u : 0u);
    rows = height / cell + (height % cell != 0u ? 1u : 0u);
    if (cols > UINT16_MAX || rows > UINT16_MAX ||
        (uint64_t)cols * (uint64_t)rows > (uint64_t)PEL_AN_MAX_CELLS)
        return PEL_ERR_RANGE;
    *out_cols = (uint16_t)cols;
    *out_rows = (uint16_t)rows;
    return PEL_OK;
}

/*
 * Fine (per-tile) banding score in [0, 1]. A tile bands, and variance AQ
 * starves it, when it is not constant (var above PEL_AN_VAR_LO) yet not
 * textured (var below flat_thr). The score is ~1 on the flattest real ramps
 * and falls to 0 at flat_thr. A tile without even a minimal slope (grad below
 * grad_lo / 4) is a flat colour with sensor-floor noise and is scaled down.
 */
static inline float pel_an_fine_score(const PelAnThresholds *t, float var, float grad)
{
    const float flat_thr = t->flat_thr;
    float score;

    if (var <= PEL_AN_VAR_LO || var >= flat_thr)
        return 0.0f;
    score = (flat_thr - var) / (flat_thr - PEL_AN_VAR_LO);
    if (grad < t->grad_lo * 0.25f)
        score *= pel_an_clampf(grad / (t->grad_lo * 0.25f), 0.0f, 1.0f);
    return pel_an_clampf(score, 0.0f, 1.0f);
}

/*
 * Coarse (inter-tile) banding score, the CAMBI multi-scale alignment
 * (ADR-0133). A flat tile on a smooth ramp spanning many tiles bands visibly
 * but its own variance stays under the floor. Detect it from the tile-mean
 * field: a step of about 1 to 12 code values per tile is banding-prone, a
 * larger step is an edge. The score grows with the step.
 */
static inline float pel_an_coarse_score(const PelAnThresholds *t, const PelAnTiles *tv, int tx,
                                        int ty)
{
    const int gc = tv->cols;
    const int idx = ty * gc + tx;
    const float lo = 1.0f / 255.0f;  /* >= ~1 code per tile: a real step   */
    const float hi = 12.0f / 255.0f; /* > ~12 codes per tile: an edge      */
    float m;
    float ml;
    float mr;
    float mu;
    float md;
    float gx;
    float gy;
    float g;

    if (!tv->valid[idx] || (float)(tv->var[idx] / PEL_AN_GS) >= t->flat_thr)
        return 0.0f; /* textured tiles mask banding */
    m = (float)(tv->mean[idx] / PEL_AN_GS);
    ml = tx > 0 ? (float)(tv->mean[idx - 1] / PEL_AN_GS) : m;
    mr = tx < gc - 1 ? (float)(tv->mean[idx + 1] / PEL_AN_GS) : m;
    mu = ty > 0 ? (float)(tv->mean[idx - gc] / PEL_AN_GS) : m;
    md = ty < tv->rows - 1 ? (float)(tv->mean[idx + gc] / PEL_AN_GS) : m;
    gx = (mr - ml) * 0.5f;
    gy = (md - mu) * 0.5f;
    g = sqrtf(gx * gx + gy * gy);
    if (g < lo || g > hi)
        return 0.0f;
    return pel_an_clampf((g - lo) / (hi - lo), 0.0f, 1.0f);
}

/* Unit-interval value to a uint8 map element, round half up. */
static inline uint8_t pel_an_u8(float v)
{
    return (uint8_t)(pel_an_clampf(v, 0.0f, 1.0f) * 255.0f + 0.5f);
}

/*
 * Fill the per-cell maps from the tile records. score is max(fine, coarse), the
 * value the ROI detector also uses; an invalid tile scores 0 and reports 0
 * variance and edge. Bounded by cols * rows <= PEL_AN_MAX_CELLS (HISS-02).
 */
static inline void pel_an_cell_maps(const PelAnThresholds *t, const PelAnTiles *tv,
                                    const PelAnMaps *out)
{
    const int n = tv->cols * tv->rows;
    int i;

    for (i = 0; i < n; i++) {
        const int ok = tv->valid[i] != 0u;
        const float var = ok ? (float)(tv->var[i] / PEL_AN_GS) : 0.0f;
        const float grad = ok ? (float)(tv->grad[i] / PEL_AN_GS) : 0.0f;
        const float fine = ok ? pel_an_fine_score(t, var, grad) : 0.0f;
        const float coarse = pel_an_coarse_score(t, tv, i % tv->cols, i / tv->cols);

        out->score[i] = fine > coarse ? fine : coarse;
        out->band[i] = pel_an_u8(out->score[i]);
        out->var[i] = pel_an_clampf(var, 0.0f, 1.0f);
        out->edge[i] = ok ? pel_an_u8((float)(tv->edge[i] / PEL_AN_GS)) : 0u;
    }
}

/* Blob-relative map offsets of one blob; band_off 0 means no maps. */
typedef struct PelAnLayout {
    uint32_t cells;
    uint32_t band_off;
    uint32_t var_off;
    uint32_t edge_off;
    uint32_t end; /* total_size of the finished blob */
} PelAnLayout;

/* Place the maps after packed sections that end at `end` (blob-relative); with_maps 0
 * places none. cells <= PEL_AN_MAX_CELLS, so no offset can wrap. */
static inline void pel_an_layout(uint32_t end, uint32_t cells, int with_maps, PelAnLayout *lay)
{
    memset(lay, 0, sizeof(*lay));
    lay->cells = cells;
    lay->end = end;
    if (!with_maps)
        return;
    lay->band_off = PEL_AN_ALIGN8(end);
    lay->var_off = PEL_AN_ALIGN8(lay->band_off + cells);
    lay->edge_off = PEL_AN_ALIGN8(lay->var_off + cells * (uint32_t)sizeof(float));
    lay->end = lay->edge_off + cells;
}

/* The section list in blob order: variance, banding, complexity. */
static inline void pel_an_section_list(const PelAnSections *sec, PelorusPackSection list[3])
{
    list[0].id = PEL_SEC_VARIANCE;
    list[0].data = &sec->var;
    list[0].size = (uint32_t)sizeof(sec->var);
    list[1].id = PEL_SEC_BANDING;
    list[1].data = &sec->band;
    list[1].size = (uint32_t)sizeof(sec->band);
    list[2].id = PEL_SEC_COMPLEXITY;
    list[2].data = &sec->cx;
    list[2].size = (uint32_t)sizeof(sec->cx);
}

/* Grid, map set and variance values of one pack request. */
static inline pel_result pel_an_check(const PelorusSideData *meta, const PelAnMaps *maps)
{
    const uint32_t cells = meta ? (uint32_t)meta->grid_cols * (uint32_t)meta->grid_rows : 0u;
    uint32_t i;

    if (!meta || meta->grid_cols == 0u || meta->grid_rows == 0u)
        return PEL_ERR_INVALID;
    if (cells > PEL_AN_MAX_CELLS)
        return PEL_ERR_RANGE;
    if (!maps)
        return PEL_OK;
    if (!maps->band || !maps->var || !maps->edge)
        return PEL_ERR_INVALID; /* the three maps travel together */
    for (i = 0; i < cells; i++) {
        if (!(maps->var[i] >= 0.0f && maps->var[i] <= 1.0f))
            return PEL_ERR_RANGE; /* also rejects NaN */
    }
    return PEL_OK;
}

/* ABI 1.4 headers pack into the caller's buffer without allocating. Older
 * headers (the stack accepts libpelorus >= 0.2.0) pack with pel_blob_pack() and
 * copy. The fast suite builds both paths: PEL_AN_FORCE_LEGACY_PACK selects the
 * older one on 1.4 headers (meson test analyze-maps-legacy-pack). */
#if PELORUS_ABI_MINOR >= 4 && !defined(PEL_AN_FORCE_LEGACY_PACK)
#define PEL_AN_PACK_INTO 1
#else
#define PEL_AN_PACK_INTO 0
#endif

/* Byte length of the three packed sections (UUID included) without writing them. */
static inline pel_result pel_an_sections_len(const PelorusSideData *meta,
                                             const PelorusPackSection *list, size_t *len)
{
#if PEL_AN_PACK_INTO
    pel_result rc = pel_blob_pack_into(meta, list, 3, NULL, 0, len);

    return rc == PEL_ERR_RANGE ? PEL_OK : (rc == PEL_OK ? PEL_ERR_INVALID : rc);
#else
    uint8_t *blob = NULL;
    pel_result rc = pel_blob_pack(meta, list, 3, &blob, len);

    pel_blob_free(blob);
    return rc;
#endif
}

/* Write the three packed sections (`len` bytes, from pel_an_sections_len) to buf. */
static inline pel_result pel_an_sections_write(const PelorusSideData *meta,
                                               const PelorusPackSection *list, uint8_t *buf,
                                               size_t len)
{
#if PEL_AN_PACK_INTO
    size_t got = 0;
    pel_result rc = pel_blob_pack_into(meta, list, 3, buf, len, &got);

    return rc == PEL_OK && got != len ? PEL_ERR_ABI : rc;
#else
    uint8_t *blob = NULL;
    size_t got = 0;
    pel_result rc = pel_blob_pack(meta, list, 3, &blob, &got);

    if (rc == PEL_OK && got != len)
        rc = PEL_ERR_ABI;
    if (rc == PEL_OK)
        memcpy(buf, blob, len);
    pel_blob_free(blob);
    return rc;
#endif
}

/* Blob length for this grid, with or without maps. */
static inline pel_result pel_an_blob_size(const PelorusSideData *meta, int with_maps,
                                          size_t *out_len)
{
    PelAnSections sec;
    PelorusPackSection list[3];
    PelAnLayout lay;
    size_t len = 0;
    pel_result rc;

    if (!out_len)
        return PEL_ERR_INVALID;
    *out_len = 0;
    rc = pel_an_check(meta, NULL);
    if (rc != PEL_OK)
        return rc;
    memset(&sec, 0, sizeof(sec));
    pel_an_section_list(&sec, list);
    rc = pel_an_sections_len(meta, list, &len);
    if (rc != PEL_OK)
        return rc;
    pel_an_layout((uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN),
                  (uint32_t)meta->grid_cols * (uint32_t)meta->grid_rows, with_maps, &lay);
    *out_len = (size_t)PELORUS_SIDEDATA_UUID_LEN + lay.end;
    return PEL_OK;
}

/* Copy one map to `off`, zeroing the alignment gap after `*end`, and advance `*end`. */
static inline void pel_an_put(uint8_t *image, uint32_t *end, uint32_t off, const void *data,
                              uint32_t size)
{
    memset(image + *end, 0, (size_t)(off - *end));
    memcpy(image + off, data, size);
    *end = off + size;
}

/* Point the packed variance and banding sections at the maps and append them. */
static inline pel_result pel_an_write_maps(uint8_t *buf, size_t packed_len, const PelAnLayout *lay,
                                           const PelAnMaps *maps)
{
    uint8_t *image = buf + PELORUS_SIDEDATA_UUID_LEN;
    const void *pv = NULL;
    const void *pb = NULL;
    size_t got = 0;
    uint32_t end;
    PelorusVarianceSection var;
    PelorusBandingSection band;
    PelorusSideData hdr;

    if (pel_blob_find_section(buf, packed_len, PEL_SEC_VARIANCE, sizeof(var), &pv, &got) !=
            PEL_OK ||
        got != sizeof(var) ||
        pel_blob_find_section(buf, packed_len, PEL_SEC_BANDING, sizeof(band), &pb, &got) !=
            PEL_OK ||
        got != sizeof(band))
        return PEL_ERR_ABI;
    memcpy(&var, pv, sizeof(var));
    memcpy(&band, pb, sizeof(band));
    var.var_cell_offset = lay->var_off;
    var.var_cell_size = lay->cells * (uint32_t)sizeof(float);
    var.edge_cell_offset = lay->edge_off;
    var.edge_cell_size = lay->cells;
    band.cell_data_offset = lay->band_off;
    band.cell_data_size = lay->cells;
    memcpy(buf + ((const uint8_t *)pv - buf), &var, sizeof(var));
    memcpy(buf + ((const uint8_t *)pb - buf), &band, sizeof(band));

    end = (uint32_t)(packed_len - PELORUS_SIDEDATA_UUID_LEN);
    pel_an_put(image, &end, lay->band_off, maps->band, lay->cells);
    pel_an_put(image, &end, lay->var_off, maps->var, lay->cells * (uint32_t)sizeof(float));
    pel_an_put(image, &end, lay->edge_off, maps->edge, lay->cells);
    memcpy(&hdr, image, sizeof(hdr));
    hdr.total_size = lay->end;
    memcpy(image, &hdr, sizeof(hdr));
    return PEL_OK;
}

/*
 * Pack one analyze blob into buf: header from `meta` (its grid names the map
 * grid), the variance, banding and complexity sections, then the three maps
 * when `maps` is non-NULL. With maps NULL the blob is exactly pel_blob_pack()
 * of the three sections. With ABI 1.4 headers nothing is allocated (the
 * sections go through pel_blob_pack_into()); older headers pack with
 * pel_blob_pack() and copy (PEL_AN_PACK_INTO). buf is caller-owned and reused
 * (a pooled buffer).
 *
 * PEL_OK with *out_len set; PEL_ERR_INVALID for NULL buf / out_len / sec, a
 * zero grid or a partial map set; PEL_ERR_RANGE for a grid above the bound, a
 * variance element outside [0, 1] or NaN, or cap shorter than the blob (then
 * *out_len holds the length needed and buf is unchanged); PEL_ERR_NOMEM from
 * pel_blob_pack() on pre-1.4 headers.
 */
static inline pel_result pel_an_pack(const PelorusSideData *meta, const PelAnSections *sec,
                                     const PelAnMaps *maps, uint8_t *buf, size_t cap,
                                     size_t *out_len)
{
    PelorusPackSection list[3];
    PelAnLayout lay;
    size_t len = 0;
    pel_result rc;

    if (!out_len)
        return PEL_ERR_INVALID;
    *out_len = 0;
    if (!buf || !sec)
        return PEL_ERR_INVALID;
    rc = pel_an_check(meta, maps);
    if (rc != PEL_OK)
        return rc;
    pel_an_section_list(sec, list);
    rc = pel_an_sections_len(meta, list, &len);
    if (rc != PEL_OK)
        return rc;
    pel_an_layout((uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN),
                  (uint32_t)meta->grid_cols * (uint32_t)meta->grid_rows, maps != NULL, &lay);
    if (cap < (size_t)PELORUS_SIDEDATA_UUID_LEN + lay.end) {
        *out_len = (size_t)PELORUS_SIDEDATA_UUID_LEN + lay.end;
        return PEL_ERR_RANGE; /* never a partial blob: buf is untouched */
    }
    rc = pel_an_sections_write(meta, list, buf, len);
    if (rc == PEL_OK && maps)
        rc = pel_an_write_maps(buf, len, &lay, maps);
    if (rc != PEL_OK)
        return rc;
    *out_len = (size_t)PELORUS_SIDEDATA_UUID_LEN + lay.end;
    return PEL_OK;
}

#endif /* AVFILTER_PELORUS_ANALYZE_MAPS_H */
