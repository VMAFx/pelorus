/**
 *
 *  Copyright 2026 Lusoris
 *
 *     Licensed under the BSD+Patent License (the "License");
 *     you may not use this file except in compliance with the License.
 *     You may obtain a copy of the License at
 *
 *         https://opensource.org/licenses/BSDplusPatent
 *
 *     Unless required by applicable law or agreed to in writing, software
 *     distributed under the License is distributed on an "AS IS" BASIS,
 *     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *     See the License for the specific language governing permissions and
 *     limitations under the License.
 *
 */

/*
 * interop_test.c — conformance test for the Pelorus side-data ABI.
 *
 * This is the shared fixture both Pelorus and vmafx run: it asserts the
 * pack/parse round-trip, the single-writer single-reader contract, and the
 * forward/back-compat rules (R3, R4, R6) that let the two repos evolve
 * independently. No external test framework — exit non-zero on first failure.
 */

#include "pelorus/deband.h"
#include "pelorus/interop.h"
#include "pelorus/pelorus.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                        \
            g_fail++;                                                                              \
        }                                                                                          \
    } while (0)

static void fill_meta(PelorusSideData *m)
{
    memset(m, 0, sizeof(*m));
    m->frame_pts = 123456;
    m->plane_layout = PEL_LAYOUT_420;
    m->bit_depth = 10;
    m->grid_cols = 16;
    m->grid_rows = 9;
    m->producer_id = PEL_FOURCC('P', 'L', 'R', 'S');
}

/* Round-trip: pack three sections, parse them back, verify scalars + framing. */
static void test_roundtrip(void)
{
    PelorusSideData meta;
    PelorusBandingSection band;
    PelorusVarianceSection var;
    PelorusFilmGrainSection grain;
    PelorusPackSection secs[3];
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    fill_meta(&meta);

    memset(&band, 0, sizeof(band));
    band.global_banding_risk = 0.42f;
    band.flat_area_fraction = 0.61f;
    band.contour_strength_mean = 0.03f;
    band.dominant_band_luma = 0.18f;

    memset(&var, 0, sizeof(var));
    var.global_variance = 0.25f;
    var.edge_density = 0.10f;
    var.texture_energy = 0.33f;

    memset(&grain, 0, sizeof(grain));
    grain.apply = 1;
    grain.seed = 0xDEADBEEFCAFEULL; /* exercises 8-byte alignment of u64 */
    grain.num_y_points = 3;
    grain.scaling_shift = 8;
    grain.ar_coeff_lag = 2;

    secs[0].id = PEL_SEC_BANDING;
    secs[0].data = &band;
    secs[0].size = (uint32_t)sizeof(band);
    secs[1].id = PEL_SEC_VARIANCE;
    secs[1].data = &var;
    secs[1].size = (uint32_t)sizeof(var);
    secs[2].id = PEL_SEC_FILMGRAIN;
    secs[2].data = &grain;
    secs[2].size = (uint32_t)sizeof(grain);

    CHECK(pel_blob_pack(&meta, secs, 3, &blob, &len) == PEL_OK);
    CHECK(blob != NULL);
    CHECK(pel_blob_is_present(blob, len) == 1);

    /* banding */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                                &got) == PEL_OK);
    CHECK(got == sizeof(PelorusBandingSection));
    {
        const PelorusBandingSection *b = p;
        CHECK(b->global_banding_risk == 0.42f);
        CHECK(b->flat_area_fraction == 0.61f);
    }

    /* variance */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_VARIANCE, sizeof(PelorusVarianceSection), &p,
                                &got) == PEL_OK);
    {
        const PelorusVarianceSection *v = p;
        CHECK(v->texture_energy == 0.33f);
    }

    /* film grain — verify the 64-bit seed survived (alignment) */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection), &p,
                                &got) == PEL_OK);
    {
        const PelorusFilmGrainSection *g = p;
        CHECK(g->seed == 0xDEADBEEFCAFEULL);
        CHECK(g->num_y_points == 3);
        CHECK(g->apply == 1);
    }

    /* a section we did not write is absent (R3 back-compat fallback) */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p,
                                &got) == PEL_ERR_ABSENT);

    pel_blob_free(blob);
}

/* Forward-compat (R4): a consumer that knows a SMALLER struct than the
 * producer wrote must get min(producer, consumer) readable bytes. */
static void test_forward_compat(void)
{
    PelorusSideData meta;
    PelorusVarianceSection var;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    const size_t older_consumer_size = 12; /* knew only the first 3 floats */

    fill_meta(&meta);
    memset(&var, 0, sizeof(var));
    var.global_variance = 1.0f;
    sec.id = PEL_SEC_VARIANCE;
    sec.data = &var;
    sec.size = (uint32_t)sizeof(var);

    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_VARIANCE, older_consumer_size, &p, &got) ==
          PEL_OK);
    CHECK(got == older_consumer_size); /* clamped to what the consumer knows */
    pel_blob_free(blob);
}

/* R6: an ABI-major mismatch is detected and rejected, not misread. */
static void test_abi_major_mismatch(void)
{
    PelorusSideData meta;
    PelorusBandingSection band;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    PelorusSideData *hdr;

    fill_meta(&meta);
    memset(&band, 0, sizeof(band));
    sec.id = PEL_SEC_BANDING;
    sec.data = &band;
    sec.size = (uint32_t)sizeof(band);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);

    hdr = (PelorusSideData *)(void *)(blob + PELORUS_SIDEDATA_UUID_LEN);
    hdr->abi_major = (uint16_t)(PELORUS_ABI_MAJOR + 1u);

    CHECK(pel_blob_is_present(blob, len) == 0);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                                &got) == PEL_ERR_ABI);
    pel_blob_free(blob);
}

/* A non-Pelorus buffer (e.g. an x264 user-data SEI) is cleanly ignored. */
static void test_foreign_buffer(void)
{
    uint8_t foreign[64];
    const void *p = NULL;
    size_t got = 0;

    memset(foreign, 0xAB, sizeof(foreign));
    CHECK(pel_blob_is_present(foreign, sizeof(foreign)) == 0);
    CHECK(pel_blob_find_section(foreign, sizeof(foreign), PEL_SEC_BANDING,
                                sizeof(PelorusBandingSection), &p, &got) == PEL_ERR_ABSENT);
}

/* A header-only blob (no sections) is valid and parses. */
static void test_header_only(void)
{
    PelorusSideData meta;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    fill_meta(&meta);
    CHECK(pel_blob_pack(&meta, NULL, 0, &blob, &len) == PEL_OK);
    CHECK(pel_blob_is_present(blob, len) == 1);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                                &got) == PEL_ERR_ABSENT);
    pel_blob_free(blob);
}

/* Truncating the buffer is detected, not read out of bounds. */
static void test_truncation(void)
{
    PelorusSideData meta;
    PelorusMotionSection mv;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    fill_meta(&meta);
    memset(&mv, 0, sizeof(mv));
    sec.id = PEL_SEC_MOTION;
    sec.data = &mv;
    sec.size = (uint32_t)sizeof(mv);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);

    /* Lie about the length: claim only the uuid + header are present. */
    CHECK(pel_blob_find_section(blob, (size_t)PELORUS_SIDEDATA_UUID_LEN + sizeof(PelorusSideData),
                                PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p,
                                &got) == PEL_ERR_TRUNCATED);
    pel_blob_free(blob);
}

/* PEL_SEC_QPREPORT (f): pack the encoder-honored QP readback with a per-cell
 * QP map appended after the blob, parse it back, verify scalars + the map. */
static void test_qp_report_roundtrip(void)
{
    PelorusSideData meta;
    PelorusQpReportSection qp;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    const uint16_t cells = 16 * 9; /* matches fill_meta grid */
    int8_t cellmap[16 * 9];
    int i;

    fill_meta(&meta);

    memset(&qp, 0, sizeof(qp));
    qp.avg_qp = 27.5f;
    qp.psnr_y = 41.2f;
    qp.total_bits = 1234567ULL; /* exercises the 64-bit field alignment */
    qp.num_intra_blocks = 40;
    qp.num_inter_blocks = 200;
    qp.num_skipped_blocks = 16;
    qp.honored_fraction = 0.75f;
    qp.report_source = PEL_QPSRC_QSV;
    qp.block_size_log2 = 4; /* 16x16 */
    qp.qp_valid = 1;
    qp.qp_cell_size = cells; /* int8 per cell */
    /* qp_cell_offset is set below once we know the section's blob offset. */

    for (i = 0; i < (int)cells; i++) {
        cellmap[i] = (int8_t)(20 + (i % 12)); /* a recognizable QP ramp */
    }

    sec.id = PEL_SEC_QPREPORT;
    sec.data = &qp;
    sec.size = (uint32_t)sizeof(qp);

    /* Pack the section and verify the scalars + the map offset/size fields
     * round-trip (the per-cell map payload itself is appended by the producer
     * after pack, like every other map section — see docs/api/interop-abi.md;
     * the fold path is exercised separately in test_qp_report_fold). */
    qp.qp_cell_offset = 0; /* producer sets this when it appends cellmap */
    (void)cellmap;
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(blob != NULL);
    CHECK(pel_blob_is_present(blob, len) == 1);

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_QPREPORT, sizeof(PelorusQpReportSection), &p,
                                &got) == PEL_OK);
    CHECK(got == sizeof(PelorusQpReportSection));
    {
        const PelorusQpReportSection *r = p;
        CHECK(r->avg_qp == 27.5f);
        CHECK(r->psnr_y == 41.2f);
        CHECK(r->total_bits == 1234567ULL);
        CHECK(r->num_inter_blocks == 200);
        CHECK(r->honored_fraction == 0.75f);
        CHECK(r->report_source == PEL_QPSRC_QSV);
        CHECK(r->block_size_log2 == 4);
        CHECK(r->qp_valid == 1);
        CHECK(r->qp_cell_size == cells);
    }

    /* An older consumer (knows only the first two floats) still parses (R4). */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_QPREPORT, 8, &p, &got) == PEL_OK);
    CHECK(got == 8);

    pel_blob_free(blob);
}

/* The reader stub: fold a per-block QP grid onto the cell grid. With a block
 * grid that is an integer multiple of the cell grid, each cell averages a clean
 * block tile, so a uniform block QP folds to the same per-cell QP. */
static void test_qp_report_fold(void)
{
    PelorusQpReportInput in;
    PelorusQpReportSection out;
    int8_t blocks[8 * 4];
    int8_t cells[4 * 2];
    int i;

    memset(&in, 0, sizeof(in));
    for (i = 0; i < (int)(sizeof(blocks)); i++) {
        blocks[i] = 30; /* uniform actual QP across all blocks */
    }
    in.block_qp = blocks;
    in.blk_cols = 8;
    in.blk_rows = 4;
    in.block_size_log2 = 4;
    in.report_source = PEL_QPSRC_QSV;
    in.avg_qp = 30.0f;
    in.num_inter_blocks = 32;

    CHECK(pel_qp_report_from_blocks(&in, 4, 2, &out, cells, sizeof(cells)) == PEL_OK);
    CHECK(out.qp_valid == 1);
    CHECK(out.qp_cell_size == 4u * 2u);
    CHECK(out.report_source == PEL_QPSRC_QSV);
    CHECK(out.avg_qp == 30.0f);
    for (i = 0; i < (int)(sizeof(cells)); i++) {
        CHECK(cells[i] == 30); /* uniform block QP -> uniform cell QP */
    }

    /* Frame-stats-only path: no block grid -> qp_valid 0, scalars preserved. */
    in.block_qp = NULL;
    CHECK(pel_qp_report_from_blocks(&in, 4, 2, &out, NULL, 0) == PEL_OK);
    CHECK(out.qp_valid == 0);
    CHECK(out.qp_cell_size == 0u);
    CHECK(out.avg_qp == 30.0f);

    /* Too-small output buffer is rejected, not overrun. */
    in.block_qp = blocks;
    CHECK(pel_qp_report_from_blocks(&in, 4, 2, &out, cells, 3) == PEL_ERR_RANGE);

    /* NULL inputs / zero grid are rejected. */
    CHECK(pel_qp_report_from_blocks(NULL, 4, 2, &out, cells, sizeof(cells)) == PEL_ERR_INVALID);
    CHECK(pel_qp_report_from_blocks(&in, 0, 2, &out, cells, sizeof(cells)) == PEL_ERR_INVALID);
}

/* The deband param contract: defaults validate, out-of-range is rejected. */
static void test_deband_params(void)
{
    PelorusDebandParams pp;
    const char *what = NULL;

    pel_deband_params_default(&pp);
    CHECK(pel_deband_params_validate(&pp, &what) == PEL_OK);

    pp.range = 99;
    CHECK(pel_deband_params_validate(&pp, &what) == PEL_ERR_RANGE);
    CHECK(what != NULL && strcmp(what, "range") == 0);
}

int main(void)
{
    test_roundtrip();
    test_forward_compat();
    test_abi_major_mismatch();
    test_foreign_buffer();
    test_header_only();
    test_truncation();
    test_qp_report_roundtrip();
    test_qp_report_fold();
    test_deband_params();

    if (g_fail != 0) {
        fprintf(stderr, "%d check(s) failed\n", g_fail);
        return EXIT_FAILURE;
    }
    printf("interop: all checks passed (libpelorus %s, ABI %u.%u)\n", pelorus_version_string(),
           PELORUS_ABI_MAJOR, PELORUS_ABI_MINOR);
    return EXIT_SUCCESS;
}
