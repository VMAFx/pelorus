/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * Unit tests for ffmpeg-patches/files/pelorus_analyze_maps.h, the host side of
 * vf_pelorus_analyze_vulkan's per-cell maps (issue #219, ADR-0177).
 *
 * Positive: a synthetic banded ramp, reduced to tile records the way
 * pelorus_analyze.comp.glsl reduces it, scores far higher banding than a flat
 * noisy frame, and the maps round-trip through pack, pel_blob_find_section()
 * and pel_blob_map(). Negative: an oversized grid, a partial map set, an
 * out-of-range variance and a short buffer are refused. Boundary: a frame
 * smaller than one cell (1x1 grid), a partial last cell and the largest grid.
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus_analyze_maps.h"

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop_test.c). */

/* All mutable test state lives here and is passed explicitly (no globals). */
typedef struct TestCtx {
    uint64_t rng;
    int failures;
} TestCtx;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);         \
            t->failures++;                                                                         \
        }                                                                                          \
    } while (0)

#define CELL 32
#define COLS 8
#define ROWS 4
#define IMG_W (COLS * CELL)
#define IMG_H (ROWS * CELL)
#define NCELLS (COLS * ROWS)

/* The filter's option defaults: flat=0.0015, grad_lo=0.002. */
static const PelAnThresholds k_thr = {0.0015f, 0.002f};

/* The tile records of one frame, struct of arrays like the shader's SSBO. */
typedef struct TileBuf {
    uint32_t var[NCELLS];
    uint32_t edge[NCELLS];
    uint32_t grad[NCELLS];
    uint32_t valid[NCELLS];
    uint32_t mean[NCELLS];
} TileBuf;

/* xorshift64*: deterministic, no libc rand(). */
static uint32_t rng_u32(TestCtx *t)
{
    t->rng ^= t->rng >> 12u;
    t->rng ^= t->rng << 25u;
    t->rng ^= t->rng >> 27u;
    return (uint32_t)((t->rng * 0x2545F4914F6CDD1Dull) >> 32u);
}

/* Per-pixel fixed-point terms, the shader's accumulate(): mean, square, edge and
 * the low-amplitude gradient, each truncated to uint as the shader does. */
static void pixel_terms(const float *img, int x, int y, uint32_t acc[4])
{
    const float ts = 65535.0f;
    const int xr = x + 1 < IMG_W ? x + 1 : x;
    const int yd = y + 1 < IMG_H ? y + 1 : y;
    const float l = img[y * IMG_W + x];
    const float g = fabsf(img[y * IMG_W + xr] - l) + fabsf(img[yd * IMG_W + x] - l);
    const float band_g = (g >= k_thr.grad_lo && g < k_thr.grad_lo * 8.0f) ? g : 0.0f;

    acc[0] += (uint32_t)(l * ts);
    acc[1] += (uint32_t)(l * l * ts);
    acc[2] += (uint32_t)(pel_an_clampf(g, 0.0f, 1.0f) * ts);
    acc[3] += (uint32_t)(band_g * ts);
}

/* CPU model of one workgroup's reduction and its tile[] writes. */
static void reduce_tile(const float *img, int tx, int ty, TileBuf *tb)
{
    const float ts = 65535.0f;
    const double gs = PEL_AN_GS;
    const int idx = ty * COLS + tx;
    uint32_t acc[4] = {0u, 0u, 0u, 0u};
    float n;
    float mean;
    float msq;
    int x;
    int y;

    for (y = ty * CELL; y < (ty + 1) * CELL; y++) {
        for (x = tx * CELL; x < (tx + 1) * CELL; x++)
            pixel_terms(img, x, y, acc);
    }
    n = (float)(CELL * CELL);
    mean = ((float)acc[0] / ts) / n;
    msq = ((float)acc[1] / ts) / n;
    tb->var[idx] = (uint32_t)((double)fmaxf(msq - mean * mean, 0.0f) * gs);
    tb->edge[idx] = (uint32_t)((double)(((float)acc[2] / ts) / n) * gs);
    tb->grad[idx] = (uint32_t)((double)(((float)acc[3] / ts) / n) * gs);
    tb->valid[idx] = 1u;
    tb->mean[idx] = (uint32_t)((double)mean * gs);
}

static PelAnTiles reduce_frame(const float *img, TileBuf *tb)
{
    PelAnTiles tv;
    int tx;
    int ty;

    for (ty = 0; ty < ROWS; ty++) {
        for (tx = 0; tx < COLS; tx++)
            reduce_tile(img, tx, ty, tb);
    }
    tv.var = tb->var;
    tv.edge = tb->edge;
    tv.grad = tb->grad;
    tv.valid = tb->valid;
    tv.mean = tb->mean;
    tv.cols = COLS;
    tv.rows = ROWS;
    return tv;
}

/* Mean of a uint8 map in [0, 1]: the VMAFx reader's banding salience. */
static double mean_u8(const uint8_t *m, int n)
{
    double sum = 0.0;
    int i;

    for (i = 0; i < n; i++)
        sum += m[i];
    return sum / ((double)n * 255.0);
}

typedef struct MapBuf {
    float score[NCELLS];
    uint8_t band[NCELLS];
    float var[NCELLS];
    uint8_t edge[NCELLS];
} MapBuf;

static PelAnMaps map_view(MapBuf *mb)
{
    PelAnMaps m;

    m.score = mb->score;
    m.band = mb->band;
    m.var = mb->var;
    m.edge = mb->edge;
    return m;
}

/* Banded 8-bit ramp: 16 + 64 * x / W, floored to code values (one code step
 * every 4 pixels), against flat mid-grey with +-20 codes of uniform noise. */
static void test_banded_vs_noisy(TestCtx *t)
{
    static float ramp[IMG_W * IMG_H];
    static float noisy[IMG_W * IMG_H];
    static TileBuf tb_ramp;
    static TileBuf tb_noisy;
    static MapBuf mb_ramp;
    static MapBuf mb_noisy;
    PelAnTiles tv;
    PelAnMaps m;
    double band_ramp;
    double band_noisy;
    double var_sum = 0.0;
    double tile_sum = 0.0;
    int i;

    for (i = 0; i < IMG_W * IMG_H; i++) {
        const int code = 16 + ((i % IMG_W) * 64) / IMG_W; /* floored: a code step every 4 px */
        const int noise = (int)(rng_u32(t) % 41u) - 20;
        ramp[i] = (float)code / 255.0f;
        noisy[i] = (float)(128 + noise) / 255.0f;
    }
    tv = reduce_frame(ramp, &tb_ramp);
    m = map_view(&mb_ramp);
    pel_an_cell_maps(&k_thr, &tv, &m);
    tv = reduce_frame(noisy, &tb_noisy);
    m = map_view(&mb_noisy);
    pel_an_cell_maps(&k_thr, &tv, &m);

    band_ramp = mean_u8(mb_ramp.band, NCELLS);
    band_noisy = mean_u8(mb_noisy.band, NCELLS);
    (void)printf("banded ramp: band mean %.3f; flat noisy: band mean %.3f\n", band_ramp,
                 band_noisy);
    CHECK(band_ramp > 0.8);
    CHECK(band_noisy < 0.1);
    CHECK(band_ramp > band_noisy + 0.5);
    for (i = 0; i < NCELLS; i++) {
        CHECK(mb_ramp.band[i] == pel_an_u8(mb_ramp.score[i]));
        CHECK(mb_noisy.var[i] > mb_ramp.var[i]); /* noise is the busier signal */
        CHECK(mb_noisy.var[i] >= 0.0f && mb_noisy.var[i] <= 0.25f);
        var_sum += mb_noisy.var[i];
        tile_sum += tb_noisy.var[i] / PEL_AN_GS;
    }
    /* The variance map's mean is the frame's global_variance (the filter's
     * frame_scalars() averages the same tile records). */
    CHECK(fabs(var_sum - tile_sum) < 1e-7 * (double)NCELLS);
    CHECK(mean_u8(mb_noisy.edge, NCELLS) > mean_u8(mb_ramp.edge, NCELLS));
}

/* An invalid tile (no in-bounds pixels) reports zeros, never stale scratch. */
static void test_invalid_tile(TestCtx *t)
{
    uint32_t var[1] = {500u};
    uint32_t edge[1] = {800000u};
    uint32_t grad[1] = {4000u};
    uint32_t valid[1] = {0u};
    uint32_t mean[1] = {500000u};
    float score[1] = {0.7f};
    uint8_t band[1] = {9u};
    uint8_t edge_map[1] = {9u};
    float var_map[1] = {0.3f};
    PelAnTiles tv = {var, edge, grad, valid, mean, 1, 1};
    PelAnMaps m = {score, band, var_map, edge_map};

    pel_an_cell_maps(&k_thr, &tv, &m);
    CHECK(score[0] == 0.0f && band[0] == 0u && var_map[0] == 0.0f && edge_map[0] == 0u);
    valid[0] = 1u;
    pel_an_cell_maps(&k_thr, &tv, &m);
    CHECK(var_map[0] == 0.0005f && edge_map[0] == 204u); /* 0.8 * 255, rounded */
}

static void check_grid(TestCtx *t, uint32_t w, uint32_t h, uint32_t cell, pel_result want,
                       uint16_t want_cols, uint16_t want_rows)
{
    uint16_t cols = 77;
    uint16_t rows = 77;

    CHECK(pel_an_grid(w, h, cell, &cols, &rows) == want);
    CHECK(cols == want_cols && rows == want_rows);
}

static void test_grid(TestCtx *t)
{
    uint16_t c = 0;

    check_grid(t, 1920, 1080, 32, PEL_OK, 60, 34);
    check_grid(t, 16, 16, 32, PEL_OK, 1, 1);  /* smaller than one cell */
    check_grid(t, 100, 70, 32, PEL_OK, 4, 3); /* partial last column and row */
    check_grid(t, 1, 1, 8, PEL_OK, 1, 1);
    check_grid(t, 8192, 8192, 8, PEL_OK, 1024, 1024);      /* exactly PEL_AN_MAX_CELLS */
    check_grid(t, 8200, 8192, 8, PEL_ERR_RANGE, 0, 0);     /* one column over */
    check_grid(t, 524288, 8, 8, PEL_ERR_RANGE, 0, 0);      /* 65536 columns */
    check_grid(t, UINT32_MAX, 1, 64, PEL_ERR_RANGE, 0, 0); /* no wrap in the ceil */
    check_grid(t, 0, 8, 8, PEL_ERR_INVALID, 0, 0);
    check_grid(t, 64, 64, 4, PEL_ERR_INVALID, 0, 0);
    check_grid(t, 64, 64, 12, PEL_ERR_INVALID, 0, 0);
    check_grid(t, 64, 64, 48, PEL_ERR_INVALID, 0, 0);
    check_grid(t, 64, 64, 128, PEL_ERR_INVALID, 0, 0);
    CHECK(pel_an_grid(64, 64, 8, NULL, &c) == PEL_ERR_INVALID);
    CHECK(pel_an_workgroup(8) == 8u && pel_an_workgroup(16) == 16u);
    CHECK(pel_an_workgroup(32) == 32u && pel_an_workgroup(64) == 32u);
}

static void fill_meta(PelorusSideData *meta, uint16_t cols, uint16_t rows)
{
    memset(meta, 0, sizeof(*meta));
    meta->frame_pts = 42;
    meta->plane_layout = PEL_LAYOUT_420;
    meta->bit_depth = 8;
    meta->grid_cols = cols;
    meta->grid_rows = rows;
    meta->producer_id = 0x41524C50u; /* PEL_FOURCC('P', 'L', 'R', 'A') */
}

static void fill_sections(PelAnSections *sec)
{
    memset(sec, 0, sizeof(*sec));
    sec->var.global_variance = 0.125f;
    sec->var.edge_density = 0.5f;
    sec->band.global_banding_risk = 0.75f;
    sec->band.flat_area_fraction = 0.75f;
    sec->cx.complexity = 0.25f;
}

/* An 8-aligned blob buffer for small grids. */
typedef union SmallBlob {
    uint64_t align;
    uint8_t bytes[512];
} SmallBlob;

/* Poison the buffer: a byte the packer fails to write shows up as 0xA5. */
static void blob_clear(SmallBlob *fx)
{
    memset(fx, 0xA5, sizeof(*fx));
}

/* Element-wise float comparison: memcmp would compare object representations. */
static int floats_equal(const void *map, const float *want, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
        float v;
        memcpy(&v, (const uint8_t *)map + i * sizeof(v), sizeof(v));
        if (v != want[i])
            return 0;
    }
    return 1;
}

static PelorusSideData load_header(const uint8_t *blob)
{
    PelorusSideData hdr;

    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    return hdr;
}

/* Read the variance and banding sections back (zeroed when absent). */
static void read_sections(TestCtx *t, const uint8_t *blob, size_t len, PelorusVarianceSection *var,
                          PelorusBandingSection *band)
{
    const void *p = NULL;
    size_t got = 0;

    memset(var, 0, sizeof(*var));
    memset(band, 0, sizeof(*band));
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_VARIANCE, sizeof(*var), &p, &got) == PEL_OK);
    CHECK(got == sizeof(*var));
    if (p && got == sizeof(*var))
        memcpy(var, p, sizeof(*var));
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(*band), &p, &got) == PEL_OK);
    if (p && got == sizeof(*band))
        memcpy(band, p, sizeof(*band));
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_COMPLEXITY, sizeof(PelorusComplexitySection), &p,
                                &got) == PEL_OK);
}

/* Read the sections back and check the map fields against the reader rules. */
static void check_map_fields(TestCtx *t, const uint8_t *blob, size_t len, uint32_t cells,
                             PelorusVarianceSection *var, PelorusBandingSection *band)
{
    const PelorusSideData hdr = load_header(blob);

    read_sections(t, blob, len, var, band);
    CHECK(hdr.total_size == (uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN));
    CHECK((uint32_t)hdr.grid_cols * hdr.grid_rows == cells);
    CHECK(band->cell_data_size == cells);
    CHECK(var->var_cell_size == cells * 4u && var->edge_cell_size == cells);
    CHECK(band->cell_data_offset % 8u == 0u && var->var_cell_offset % 8u == 0u &&
          var->edge_cell_offset % 8u == 0u);
    CHECK(band->cell_data_offset < var->var_cell_offset);
    CHECK(var->var_cell_offset < var->edge_cell_offset);
    CHECK(var->edge_cell_offset + cells == hdr.total_size); /* the blob ends at the last map */
    CHECK(var->global_variance == 0.125f && band->global_banding_risk == 0.75f);
}

/* Every map of a 3x2 blob reads back through pel_blob_map(). */
static void check_maps_read_back(TestCtx *t, const uint8_t *blob, size_t len,
                                 const PelorusVarianceSection *v, const PelorusBandingSection *b,
                                 const PelAnMaps *maps)
{
    const void *mp = NULL;

    CHECK(pel_blob_map(blob, len, b->cell_data_offset, b->cell_data_size, 6u, 1u, &mp) == PEL_OK);
    CHECK(mp && memcmp(mp, maps->band, 6u) == 0);
    CHECK(pel_blob_map(blob, len, v->var_cell_offset, v->var_cell_size, 6u, 4u, &mp) == PEL_OK);
    CHECK(mp && floats_equal(mp, maps->var, 6u));
    CHECK(pel_blob_map(blob, len, v->edge_cell_offset, v->edge_cell_size, 6u, 1u, &mp) == PEL_OK);
    CHECK(mp && memcmp(mp, maps->edge, 6u) == 0);
}

/* Pack, then read every map back through pel_blob_map() (VMAFx reads the same
 * offsets). */
static void test_pack_roundtrip(TestCtx *t)
{
    uint8_t band[6] = {0, 51, 102, 153, 204, 255};
    float var[6] = {0.0f, 0.001f, 0.01f, 0.1f, 0.25f, 1.0f}; /* 1.0 is the inclusive bound */
    uint8_t edge[6] = {255, 0, 7, 8, 9, 10};
    PelAnMaps maps = {NULL, band, var, edge};
    PelorusSideData meta;
    PelAnSections sec;
    PelorusVarianceSection v;
    PelorusBandingSection b;
    SmallBlob fx;
    const void *mp = NULL;
    size_t len = 0;
    size_t want = 0;

    blob_clear(&fx);
    fill_meta(&meta, 3, 2);
    fill_sections(&sec);
    CHECK(pel_an_blob_size(&meta, 1, &want) == PEL_OK);
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_OK);
    CHECK(len == want);
    check_map_fields(t, fx.bytes, len, 6u, &v, &b);
    check_maps_read_back(t, fx.bytes, len, &v, &b, &maps);
    /* The 2-byte alignment gap after the 6-byte banding map is zeroed, not left over. */
    CHECK(v.var_cell_offset == b.cell_data_offset + 8u);
    CHECK(fx.bytes[PELORUS_SIDEDATA_UUID_LEN + b.cell_data_offset + 6u] == 0u &&
          fx.bytes[PELORUS_SIDEDATA_UUID_LEN + b.cell_data_offset + 7u] == 0u);
    /* An older consumer that knows only the first two banding floats still parses (R4). */
    CHECK(pel_blob_find_section(fx.bytes, len, PEL_SEC_BANDING, 8u, &mp, &want) == PEL_OK);
    CHECK(want == 8u);
}

/* maps NULL: the scalar-only blob is byte-identical to pel_blob_pack(). */
static void test_pack_without_maps(TestCtx *t)
{
    PelorusSideData meta;
    PelAnSections sec;
    PelorusPackSection list[3];
    SmallBlob fx;
    uint8_t *ref = NULL;
    size_t len = 0;
    size_t ref_len = 0;
    size_t want = 0;
    const void *p = NULL;
    size_t got = 0;

    blob_clear(&fx);
    fill_meta(&meta, 3, 2);
    fill_sections(&sec);
    CHECK(pel_an_blob_size(&meta, 0, &want) == PEL_OK);
    CHECK(pel_an_pack(&meta, &sec, NULL, fx.bytes, sizeof(fx.bytes), &len) == PEL_OK);
    CHECK(len == want);
    pel_an_section_list(&sec, list);
    CHECK(pel_blob_pack(&meta, list, 3, &ref, &ref_len) == PEL_OK);
    CHECK(ref && ref_len == len && memcmp(ref, fx.bytes, len) == 0);
    pel_blob_free(ref);
    CHECK(pel_blob_find_section(fx.bytes, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                                &got) == PEL_OK);
    if (p) {
        PelorusBandingSection b;
        memcpy(&b, p, sizeof(b));
        CHECK(b.cell_data_offset == 0u && b.cell_data_size == 0u); /* VMAFx: frame scalars */
    }
}

static void test_pack_rejects(TestCtx *t)
{
    uint8_t band[6] = {0};
    float var[6] = {0.0f};
    uint8_t edge[6] = {0};
    PelAnMaps maps = {NULL, band, var, edge};
    PelAnMaps partial = {NULL, band, var, NULL};
    PelorusSideData meta;
    PelAnSections sec;
    SmallBlob fx;
    size_t len = 99;
    size_t need = 0;

    blob_clear(&fx);
    fill_meta(&meta, 3, 2);
    fill_sections(&sec);
    CHECK(pel_an_pack(&meta, &sec, &partial, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_INVALID);
    CHECK(len == 0u);
    var[4] = NAN;
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_RANGE);
    var[4] = -0.001f;
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_RANGE);
    var[4] = 1.001f;
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_RANGE);
    var[4] = INFINITY;
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_RANGE);
    var[4] = 0.0f;

    /* A short buffer: the length needed comes back and the buffer is untouched. */
    CHECK(pel_an_blob_size(&meta, 1, &need) == PEL_OK);
    memset(fx.bytes, 0xA5, sizeof(fx.bytes));
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, need - 1u, &len) == PEL_ERR_RANGE);
    CHECK(len == need && fx.bytes[0] == 0xA5 && fx.bytes[need - 2u] == 0xA5);
}

/* NULL arguments, a zero grid and an oversized grid are refused. */
static void test_pack_rejects_grid(TestCtx *t)
{
    uint8_t band[6] = {0};
    float var[6] = {0.0f};
    uint8_t edge[6] = {0};
    PelAnMaps maps = {NULL, band, var, edge};
    PelorusSideData meta;
    PelAnSections sec;
    SmallBlob fx;
    size_t len = 0;
    size_t need = 0;

    blob_clear(&fx);
    fill_meta(&meta, 3, 2);
    fill_sections(&sec);
    CHECK(pel_an_pack(&meta, &sec, &maps, NULL, sizeof(fx.bytes), &len) == PEL_ERR_INVALID);
    CHECK(pel_an_pack(&meta, NULL, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_INVALID);
    CHECK(pel_an_pack(&meta, &sec, &maps, fx.bytes, sizeof(fx.bytes), NULL) == PEL_ERR_INVALID);
    CHECK(pel_an_pack(NULL, &sec, &maps, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_INVALID);

    fill_meta(&meta, 0, 2); /* a zero grid has no cells to map */
    CHECK(pel_an_pack(&meta, &sec, NULL, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_INVALID);
    CHECK(pel_an_blob_size(&meta, 0, &need) == PEL_ERR_INVALID);
    fill_meta(&meta, 2048, 513); /* oversized: one row past PEL_AN_MAX_CELLS */
    CHECK(pel_an_pack(&meta, &sec, NULL, fx.bytes, sizeof(fx.bytes), &len) == PEL_ERR_RANGE);
    CHECK(pel_an_blob_size(&meta, 1, &need) == PEL_ERR_RANGE && need == 0u);
    CHECK(pel_an_blob_size(&meta, 1, NULL) == PEL_ERR_INVALID);
}

/* Boundaries: the 1x1 grid of a frame smaller than one cell, and the largest grid. */
static void test_pack_boundaries(TestCtx *t)
{
    const uint32_t max = PEL_AN_MAX_CELLS;
    uint8_t one_band = 200;
    uint8_t one_edge = 3;
    float one_var = 0.02f;
    PelAnMaps one = {NULL, &one_band, &one_var, &one_edge};
    PelorusSideData meta;
    PelAnSections sec;
    PelorusVarianceSection v;
    PelorusBandingSection b;
    SmallBlob fx;
    PelAnMaps big;
    uint8_t *blob;
    size_t len = 0;
    size_t need = 0;

    blob_clear(&fx);
    fill_meta(&meta, 1, 1);
    fill_sections(&sec);
    CHECK(pel_an_pack(&meta, &sec, &one, fx.bytes, sizeof(fx.bytes), &len) == PEL_OK);
    check_map_fields(t, fx.bytes, len, 1u, &v, &b);

    fill_meta(&meta, 1024, 1024);
    CHECK(pel_an_blob_size(&meta, 1, &need) == PEL_OK);
    big.score = NULL;
    big.band = calloc(max, 1);
    big.var = calloc(max, sizeof(float));
    big.edge = calloc(max, 1);
    blob = need > 0u ? calloc(need, 1) : NULL;
    if (!big.band || !big.var || !big.edge || !blob) {
        CHECK(!"allocation failed");
    } else {
        big.band[max - 1u] = 77;
        big.var[max - 1u] = 0.25f;
        big.edge[max - 1u] = 66;
        CHECK(pel_an_pack(&meta, &sec, &big, blob, need, &len) == PEL_OK && len == need);
        check_map_fields(t, blob, len, max, &v, &b);
        CHECK(blob[PELORUS_SIDEDATA_UUID_LEN + b.cell_data_offset + max - 1u] == 77u);
        CHECK(blob[len - 1u] == 66u);
    }
    free(blob);
    free(big.band);
    free(big.var);
    free(big.edge);
}

int main(void)
{
    TestCtx ctx = {0x9E3779B97F4A7C15ull, 0};
    TestCtx *t = &ctx;

    test_grid(t);
    test_invalid_tile(t);
    test_banded_vs_noisy(t);
    test_pack_roundtrip(t);
    test_pack_without_maps(t);
    test_pack_rejects(t);
    test_pack_rejects_grid(t);
    test_pack_boundaries(t);
    if (t->failures != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", t->failures);
        return EXIT_FAILURE;
    }
    (void)printf("analyze maps: all checks passed\n");
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
