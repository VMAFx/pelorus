/*
 * Regression test for the FFmpeg-side consumer helpers in
 * ffmpeg-patches/files/pelorus_sidedata.h (BUG-003/004/005/015):
 *   - every producer appends its own SEI_UNREGISTERED blob; the consumer must
 *     scan all entries, newest first, not just the first one;
 *   - a shorter (older) producer section must be checked per field;
 *   - the motion-grid pitch is the producer block edge, not ceil(W / cols).
 * Compiled against a stub libavutil/frame.h; links libpelorus for the real
 * pack/parse code.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus_sidedata.h"

#define MAX_SD 4

typedef struct Fx {
    AVFrameSideData store[MAX_SD];
    AVFrameSideData *ptr[MAX_SD];
    AVFrame frame;
} Fx;

static int failures;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            failures++;                                                                            \
        }                                                                                          \
    } while (0)

static void fx_push(Fx *fx, enum AVFrameSideDataType type, uint8_t *data, size_t size)
{
    int n = fx->frame.nb_side_data;
    fx->store[n].type = type;
    fx->store[n].data = data;
    fx->store[n].size = size;
    fx->ptr[n] = &fx->store[n];
    fx->frame.side_data = fx->ptr;
    fx->frame.nb_side_data = n + 1;
}

/* Pack one section into a UUID-prefixed blob (producer id tags the writer). */
static uint8_t *pack1(enum pel_section id, const void *sec, uint32_t size, size_t *len)
{
    PelorusSideData meta;
    PelorusPackSection ps;
    uint8_t *blob = NULL;

    memset(&meta, 0, sizeof(meta));
    meta.producer_id = 0x54534554u /* 'TEST' */;
    ps.id = id;
    ps.data = sec;
    ps.size = size;
    if (pel_blob_pack(&meta, &ps, 1, &blob, len) != PEL_OK)
        return NULL;
    return blob;
}

static void test_scan_all_entries(void)
{
    Fx fx;
    PelorusVarianceSection var;
    PelorusMotionSection mo;
    uint8_t *b0;
    uint8_t *b1;
    size_t l0 = 0;
    size_t l1 = 0;
    size_t got = 0;
    const void *p = NULL;
    const AVFrameSideData *sd;

    memset(&fx, 0, sizeof(fx));
    memset(&var, 0, sizeof(var));
    memset(&mo, 0, sizeof(mo));
    mo.has_scene_cut = 1;
    b0 = pack1(PEL_SEC_VARIANCE, &var, sizeof(var), &l0); /* e.g. analyze, first */
    b1 = pack1(PEL_SEC_MOTION, &mo, sizeof(mo), &l1);     /* e.g. mc, appended later */
    CHECK(b0 && b1);
    if (!b0 || !b1)
        return;
    fx_push(&fx, AV_FRAME_DATA_SEI_UNREGISTERED, b0, l0);
    fx_push(&fx, AV_FRAME_DATA_SEI_UNREGISTERED, b1, l1);

    /* The first entry has no motion section: the old first-entry lookup misses it. */
    sd = pelorus_sd_find_section(&fx.frame, PEL_SEC_MOTION, sizeof(mo), &p, &got);
    CHECK(sd == &fx.store[1]);
    CHECK(p != NULL && got == sizeof(mo));
    CHECK(p && ((const PelorusMotionSection *)p)->has_scene_cut == 1);

    /* A section only the first blob carries is still found. */
    sd = pelorus_sd_find_section(&fx.frame, PEL_SEC_VARIANCE, sizeof(var), &p, &got);
    CHECK(sd == &fx.store[0]);

    /* Absent section. */
    sd = pelorus_sd_find_section(&fx.frame, PEL_SEC_FILMGRAIN, 8, &p, &got);
    CHECK(sd == NULL);

    pel_blob_free(b0);
    pel_blob_free(b1);
}

static void test_newest_wins_and_junk_skipped(void)
{
    Fx fx;
    PelorusMotionSection old_mo;
    PelorusMotionSection new_mo;
    uint8_t *b0;
    uint8_t *b1;
    uint8_t junk[64];
    size_t l0 = 0;
    size_t l1 = 0;
    size_t got = 0;
    const void *p = NULL;
    const AVFrameSideData *sd;

    memset(&fx, 0, sizeof(fx));
    memset(&old_mo, 0, sizeof(old_mo));
    memset(&new_mo, 0, sizeof(new_mo));
    memset(junk, 0xA5, sizeof(junk));
    old_mo.has_scene_cut = 0;
    new_mo.has_scene_cut = 1;
    b0 = pack1(PEL_SEC_MOTION, &old_mo, sizeof(old_mo), &l0);
    b1 = pack1(PEL_SEC_MOTION, &new_mo, sizeof(new_mo), &l1);
    CHECK(b0 && b1);
    if (!b0 || !b1)
        return;
    fx_push(&fx, AV_FRAME_DATA_SEI_UNREGISTERED, b0, l0);
    fx_push(&fx, AV_FRAME_DATA_SEI_UNREGISTERED, b1, l1);

    sd = pelorus_sd_find_section(&fx.frame, PEL_SEC_MOTION, sizeof(new_mo), &p, &got);
    CHECK(sd == &fx.store[1]); /* newest valid blob wins */
    CHECK(p && ((const PelorusMotionSection *)p)->has_scene_cut == 1);

    /* A non-Pelorus SEI payload and a different side-data type, both newer, are
     * skipped; the newest VALID Pelorus blob is still returned. */
    fx_push(&fx, AV_FRAME_DATA_SEI_UNREGISTERED, junk, sizeof(junk));
    fx_push(&fx, AV_FRAME_DATA_OTHER_STUB, b0, l0);
    sd = pelorus_sd_find_section(&fx.frame, PEL_SEC_MOTION, sizeof(new_mo), &p, &got);
    CHECK(sd == &fx.store[1]);

    pel_blob_free(b0);
    pel_blob_free(b1);
}

static void test_short_producer_section(void)
{
    Fx fx;
    PelorusMotionSection mo;
    uint8_t *b;
    size_t l = 0;
    size_t got = 0;
    const void *p = NULL;
    const size_t short_sz = offsetof(PelorusMotionSection, has_scene_cut);

    /* An older producer whose section ends right before has_scene_cut. */
    memset(&fx, 0, sizeof(fx));
    memset(&mo, 0, sizeof(mo));
    mo.motion_magnitude_mean = 3.0f;
    b = pack1(PEL_SEC_MOTION, &mo, (uint32_t)short_sz, &l);
    CHECK(b != NULL);
    if (!b)
        return;
    fx_push(&fx, AV_FRAME_DATA_SEI_UNREGISTERED, b, l);

    CHECK(pelorus_sd_find_section(&fx.frame, PEL_SEC_MOTION, sizeof(mo), &p, &got) != NULL);
    CHECK(got == short_sz); /* R4: min(producer, consumer) */
    CHECK(PEL_SD_FIELD_OK(got, PelorusMotionSection, motion_magnitude_mean));
    CHECK(PEL_SD_FIELD_OK(got, PelorusMotionSection, mv_field_offset));
    CHECK(!PEL_SD_FIELD_OK(got, PelorusMotionSection, has_scene_cut));
    CHECK(!PEL_SD_FIELD_OK((size_t)0, PelorusMotionSection, global_motion_x));

    pel_blob_free(b);
}

static void test_cell_pitch_table(void)
{
    int pitch = 0;

    /* ceil(H/rows) is wrong: 100 px at b=32 -> 4 rows -> ceil(100/4)=25 != 32;
     * 720 px at b=31 -> 24 rows -> ceil(720/24)=30 != 31 (the old derivation). */
    CHECK(pelorus_mc_cell_pitch(64, 100, 2, 4, &pitch) == 1 && pitch == 32);
    CHECK(pelorus_mc_cell_pitch(1280, 720, 42, 24, &pitch) == 1 && pitch == 31);
    CHECK(pelorus_mc_cell_pitch(1920, 1080, 120, 68, &pitch) == 1 && pitch == 16);
    CHECK(pelorus_mc_cell_pitch(1920, 1080, 240, 135, &pitch) == 1 && pitch == 8);
    CHECK(pelorus_mc_cell_pitch(3840, 2160, 120, 68, &pitch) == 1 && pitch == 32);
    /* Grid that is no mc grid of this frame. */
    CHECK(pelorus_mc_cell_pitch(1920, 1080, 7, 7, &pitch) == 0);
    CHECK(pelorus_mc_cell_pitch(1920, 1080, 0, 68, &pitch) == 0);
    CHECK(pelorus_mc_cell_pitch(0, 1080, 120, 68, &pitch) == 0);
    /* Ambiguous with the mc default among the candidates: assume it (2).
     * 96x64 with a 6x4 grid fits b = 16..19 (the Vulkan format-matrix case). */
    CHECK(pelorus_mc_cell_pitch(96, 64, 6, 4, &pitch) == 2 && pitch == 16);
    CHECK(pelorus_mc_cell_pitch(8, 8, 1, 1, &pitch) == 2 && pitch == 16);
    /* Ambiguous without the default: 200x200 with 7x7 fits b = 29..32. */
    CHECK(pelorus_mc_cell_pitch(200, 200, 7, 7, &pitch) == 0);
}

static void test_cell_pitch_sweep(void)
{
    int pitch = 0;
    int bad = 0;
    int b;
    int w;
    int h;

    /* Whenever the pitch resolves it must equal the producer's block edge. */
    for (w = 90; w <= 140; w += 7) {
        for (h = 60; h <= 110; h += 5) {
            for (b = PEL_SD_MC_BSIZE_MIN; b <= PEL_SD_MC_BSIZE_MAX; b++) {
                int cols = (w + b - 1) / b;
                int rows = (h + b - 1) / b;
                int fit = pelorus_mc_cell_pitch(w, h, cols, rows, &pitch);
                /* A unique fit is the producer's edge; an assumed default must
                 * itself reproduce the grid (b and 16 are indistinguishable). */
                if (fit == 1 && pitch != b)
                    bad++;
                if (fit == 2 && (pitch != PEL_SD_MC_BSIZE_DEFAULT ||
                                 (w + pitch - 1) / pitch != cols || (h + pitch - 1) / pitch != rows))
                    bad++;
            }
        }
    }
    CHECK(bad == 0);
}

/* #218 (ABI 1.4): a section that names its block edge is used as named, also where
 * the grid alone is ambiguous or unresolvable; a 1.3-sized section, or the value 0,
 * falls back to inference; a named edge that contradicts the grid is refused. */
static void test_block_pitch_named(void)
{
    PelorusMotionSection mo;
    int pitch = 0;

    memset(&mo, 0, sizeof(mo));
    /* mc bsize=8 on a 64x64 frame (8x8 grid): the grid fits 8 and 9, so 1.3 inference
     * gives up; the named edge resolves it. */
    mo.block_size_log2 = 3;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 64, 64, 8, 8, &pitch) == 1 && pitch == 8);
    CHECK(pelorus_mc_cell_pitch(64, 64, 8, 8, &pitch) == 0);
    /* 96x64 with a 6x4 grid fits 16..19: named, nothing is assumed (BUG-035). */
    mo.block_size_log2 = 4;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 96, 64, 6, 4, &pitch) == 1 && pitch == 16);
    /* The smallest frame, one cell: each bsize the producer can name is taken as named. */
    mo.block_size_log2 = 3;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 1, 1, 1, 1, &pitch) == 1 && pitch == 8);
    mo.block_size_log2 = 5;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 1, 1, 1, 1, &pitch) == 1 && pitch == 32);
}

static void test_block_pitch_fallback(void)
{
    PelorusMotionSection mo;
    const size_t size_1_3 = offsetof(PelorusMotionSection, block_size_log2);
    int pitch = 0;

    memset(&mo, 0, sizeof(mo));
    /* An ABI 1.3 section: absent by size, whatever the bytes past `got` hold. */
    mo.block_size_log2 = 3;
    CHECK(pelorus_mc_block_pitch(&mo, size_1_3, 96, 64, 6, 4, &pitch) == 2 && pitch == 16);
    CHECK(pelorus_mc_block_pitch(&mo, size_1_3, 1920, 1080, 240, 135, &pitch) == 1 &&
          pitch == 8);
    /* 0 = not reported (bsize=12 has no log2): the grid decides. */
    mo.block_size_log2 = 0;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 1920, 1080, 160, 90, &pitch) == 1 &&
          pitch == 12);
    /* A named edge that contradicts the grid, or lies outside 8..32, is refused. */
    mo.block_size_log2 = 4;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 64, 64, 8, 8, &pitch) == 0);
    mo.block_size_log2 = 2;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 8, 8, 2, 2, &pitch) == 0);
    mo.block_size_log2 = 6;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 128, 128, 2, 2, &pitch) == 0);
    mo.block_size_log2 = 200;
    CHECK(pelorus_mc_block_pitch(&mo, sizeof(mo), 64, 64, 1, 1, &pitch) == 0);
    CHECK(pelorus_mc_block_pitch(NULL, 0, 1920, 1080, 240, 135, &pitch) == 1 && pitch == 8);
}

int main(void)
{
    test_scan_all_entries();
    test_newest_wins_and_junk_skipped();
    test_short_producer_section();
    test_cell_pitch_table();
    test_cell_pitch_sweep();
    test_block_pitch_named();
    test_block_pitch_fallback();
    if (failures) {
        (void)fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    (void)puts("pelorus_sidedata: all checks passed");
    return 0;
}
