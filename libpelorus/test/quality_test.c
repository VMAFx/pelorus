/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/quality.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            g_fail++;                                                                              \
        }                                                                                          \
    } while (0)

static void init_test_quality(PelorusQualitySection *q)
{
    memset(q, 0, sizeof(*q));
    q->window_id = 101u;
    q->pts_start = 500;
    q->pts_end = 1500;
    q->tb_num = 1;
    q->tb_den = 1000;
    memset(q->model_hash, 0x5a, sizeof(q->model_hash));
    q->provenance_id = 777u;
    q->score = 92.125;
    q->metric_id = (uint32_t)PEL_QM_VMAF;
    q->latency_frames = 12;
    q->frame_count = 30;
    q->_pad = 0;
}

static void fill_meta_header(PelorusSideData *meta)
{
    memset(meta, 0, sizeof(*meta));
    meta->magic[0] = 'P';
    meta->magic[1] = 'E';
    meta->magic[2] = 'L';
    meta->magic[3] = '1';
    meta->abi_major = PELORUS_ABI_MAJOR;
    meta->abi_minor = PELORUS_ABI_MINOR;
}

static void test_quality_validate_scalars(void)
{
    PelorusQualitySection q;
    init_test_quality(&q);
    CHECK(pel_quality_validate(NULL) == PEL_ERR_INVALID);

    q.metric_id = 0;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.metric_id = (uint32_t)PEL_QM_CAMBI;
    CHECK(pel_quality_validate(&q) == PEL_OK);
    q.metric_id = 8u;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.metric_id = (uint32_t)PEL_QM_VMAF;

    q.tb_num = 0;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.tb_num = 1;
    q.tb_den = 0;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.tb_den = 1000;
}

static void test_quality_validate_ranges(void)
{
    PelorusQualitySection q;
    init_test_quality(&q);

    q.pts_end = q.pts_start;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.pts_end = q.pts_start - 10;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.pts_end = 1500;

    q.score = (double)NAN;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.score = (double)INFINITY;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.score = 92.125;

    q.frame_count = 0;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q.frame_count = 30;

    q._pad = 1;
    CHECK(pel_quality_validate(&q) == PEL_ERR_INVALID);
    q._pad = 0;

    CHECK(pel_quality_validate(&q) == PEL_OK);
}

static void test_quality_pack_refuses_invalid(void)
{
    PelorusSideData meta;
    PelorusQualitySection q;
    uint8_t *blob = NULL;
    size_t len = 0;

    fill_meta_header(&meta);
    init_test_quality(&q);

    CHECK(pel_quality_pack(NULL, &q, &blob, &len) == PEL_ERR_INVALID);
    CHECK(pel_quality_pack(&meta, NULL, &blob, &len) == PEL_ERR_INVALID);
    CHECK(pel_quality_pack(&meta, &q, NULL, &len) == PEL_ERR_INVALID);
    CHECK(pel_quality_pack(&meta, &q, &blob, NULL) == PEL_ERR_INVALID);

    q.score = (double)NAN;
    CHECK(pel_quality_pack(&meta, &q, &blob, &len) == PEL_ERR_INVALID);
    q.score = 92.125;

    q.tb_num = 0;
    CHECK(pel_quality_pack(&meta, &q, &blob, &len) == PEL_ERR_INVALID);
    q.tb_num = 1;

    q.frame_count = 0;
    CHECK(pel_quality_pack(&meta, &q, &blob, &len) == PEL_ERR_INVALID);
}

static void test_quality_pack_find_roundtrip(void)
{
    PelorusSideData meta;
    PelorusQualitySection q;
    PelorusQualitySection found;
    uint8_t *blob = NULL;
    size_t len = 0;

    fill_meta_header(&meta);
    init_test_quality(&q);
    CHECK(pel_quality_pack(&meta, &q, &blob, &len) == PEL_OK);
    CHECK(blob != NULL && len > 0);

    CHECK(pel_quality_find(blob, len, &found) == PEL_OK);
    CHECK(found.window_id == q.window_id);
    CHECK(found.pts_start == q.pts_start);
    CHECK(found.pts_end == q.pts_end);
    CHECK(found.tb_num == q.tb_num);
    CHECK(found.tb_den == q.tb_den);
    CHECK(memcmp(found.model_hash, q.model_hash, 32) == 0);
    CHECK(found.provenance_id == q.provenance_id);
    CHECK(found.score == q.score);
    CHECK(found.metric_id == q.metric_id);
    CHECK(found.latency_frames == q.latency_frames);
    CHECK(found.frame_count == q.frame_count);
    CHECK(found._pad == 0);

    pel_blob_free(blob);
}

static void test_quality_find_conditions(void)
{
    PelorusSideData meta;
    PelorusBandingSection b;
    PelorusQualitySection q;
    PelorusPackSection sec;
    PelorusQualitySection found;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    fill_meta_header(&meta);
    init_test_quality(&q);

    /* 1. Absent section */
    memset(&b, 0, sizeof(b));
    sec.id = PEL_SEC_BANDING;
    sec.data = &b;
    sec.size = (uint32_t)sizeof(b);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(pel_quality_find(blob, len, &found) == PEL_ERR_ABSENT);
    pel_blob_free(blob);
    blob = NULL;

    /* 2. Invalid stored section (NaN score) packed directly via pel_blob_pack */
    q.score = (double)NAN;
    sec.id = PEL_SEC_QUALITY;
    sec.data = &q;
    sec.size = (uint32_t)sizeof(q);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(pel_quality_find(blob, len, &found) == PEL_ERR_INVALID);
    pel_blob_free(blob);
    blob = NULL;

    /* 3. Shorter older-producer section (24 bytes): blob API packs 24 bytes, copy path zero-fills tail */
    init_test_quality(&q);
    sec.id = PEL_SEC_QUALITY;
    sec.data = &q;
    sec.size = 24u;
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_QUALITY, sizeof(PelorusQualitySection), &p,
                                &got) == PEL_OK);
    CHECK(got == 24u && p != NULL);

    memset(&found, 0, sizeof(found));
    if (p != NULL) {
        memcpy(&found, p, got);
    }
    CHECK(found.window_id == q.window_id);
    CHECK(found.pts_start == q.pts_start);
    CHECK(found.pts_end == q.pts_end);
    CHECK(found.tb_num == 0);
    CHECK(found.score == 0.0);
    CHECK(found.metric_id == 0);

    /* pel_quality_find on a 24-byte section returns PEL_ERR_INVALID because tb/metric are 0 */
    CHECK(pel_quality_find(blob, len, &found) == PEL_ERR_INVALID);
    pel_blob_free(blob);
}

static void test_align_null_and_bounds(void)
{
    PelorusQualitySection win;
    PelQualityEncRecord rec;
    PelQualityMatch match;

    init_test_quality(&win);
    rec.pts = 1000;
    rec.index = 0;

    CHECK(pel_quality_align(NULL, 1, &rec, 1, 1, 1000, &match, 1) == PEL_ERR_INVALID);
    CHECK(pel_quality_align(&win, 1, NULL, 1, 1, 1000, &match, 1) == PEL_ERR_INVALID);
    CHECK(pel_quality_align(&win, 1, &rec, 1, 1, 1000, NULL, 1) == PEL_ERR_INVALID);
    CHECK(pel_quality_align(&win, 1, &rec, 1, 0, 1000, &match, 1) == PEL_ERR_INVALID);
    CHECK(pel_quality_align(&win, 1, &rec, 1, 1, 0, &match, 1) == PEL_ERR_INVALID);

    /* Bound check precedes access: count > PEL_QUALITY_ALIGN_MAX without huge allocation */
    CHECK(pel_quality_align(&win, (size_t)PEL_QUALITY_ALIGN_MAX + 1u, &rec, 1, 1, 1000, &match,
                            (size_t)PEL_QUALITY_ALIGN_MAX + 1u) == PEL_ERR_RANGE);
    CHECK(pel_quality_align(&win, 1, &rec, (size_t)PEL_QUALITY_ALIGN_MAX + 1u, 1, 1000, &match,
                            1) == PEL_ERR_RANGE);
    CHECK(pel_quality_align(&win, 1, &rec, 1, 1, 1000, &match, 0) == PEL_ERR_RANGE);
}

static void test_align_zero_records_and_zero_fill(void)
{
    PelorusQualitySection win[2];
    PelQualityEncRecord recs[2];
    PelQualityMatch match[2];
    pel_result res;

    init_test_quality(&win[0]);
    init_test_quality(&win[1]);
    win[1].window_id = 102u;

    /* Zero records: all windows matched=0 */
    res = pel_quality_align(win, 2, NULL, 0, 1, 1000, match, 2);
    CHECK(res == PEL_OK);
    CHECK(match[0].matched == 0 && match[0].record_count == 0 &&
          match[0].first_record == UINT32_MAX);
    CHECK(match[1].matched == 0 && match[1].record_count == 0 &&
          match[1].first_record == UINT32_MAX);

    /* Zero-fill on error: unsorted records return PEL_ERR_INVALID, out is zero-filled */
    match[0].window_id = 999u;
    match[0].matched = 1;
    match[1].window_id = 888u;
    match[1].matched = 1;
    recs[0].pts = 1000;
    recs[0].index = 0;
    recs[1].pts = 500;
    recs[1].index = 1;
    res = pel_quality_align(win, 2, recs, 2, 1, 1000, match, 2);
    CHECK(res == PEL_ERR_INVALID);
    CHECK(match[0].window_id == 0 && match[0].matched == 0);
    CHECK(match[1].window_id == 0 && match[1].matched == 0);
}

static void test_align_edges_and_nearest_trap(void)
{
    PelorusQualitySection win;
    PelQualityEncRecord recs[6];
    PelQualityMatch match;
    pel_result res;

    init_test_quality(&win);
    win.pts_start = 100;
    win.pts_end = 200;
    win.tb_num = 1;
    win.tb_den = 1000;

    recs[0].pts = 50;
    recs[0].index = 0;
    recs[1].pts = 100;
    recs[1].index = 10;
    recs[2].pts = 150;
    recs[2].index = 20;
    recs[3].pts = 199;
    recs[3].index = 30;
    recs[4].pts = 200;
    recs[4].index = 40;
    recs[5].pts = 250;
    recs[5].index = 50;

    res = pel_quality_align(&win, 1, recs, 6, 1, 1000, &match, 1);
    CHECK(res == PEL_OK);
    CHECK(match.matched == 1);
    CHECK(match.first_record == 10);
    CHECK(match.record_count == 3);

    /* Planted nearest trap: no records in [300, 400). */
    win.pts_start = 300;
    win.pts_end = 400;
    recs[0].pts = 299;
    recs[0].index = 100;
    recs[1].pts = 400;
    recs[1].index = 101;
    recs[2].pts = 401;
    recs[2].index = 102;

    res = pel_quality_align(&win, 1, recs, 3, 1, 1000, &match, 1);
    CHECK(res == PEL_OK);
    CHECK(match.matched == 0);
    CHECK(match.first_record == UINT32_MAX);
    CHECK(match.record_count == 0);
}

static void test_align_three_windows_one_empty(void)
{
    PelorusQualitySection win[3];
    PelQualityEncRecord recs[4];
    PelQualityMatch match[3];
    pel_result res;

    init_test_quality(&win[0]);
    win[0].window_id = 1;
    win[0].pts_start = 0;
    win[0].pts_end = 100;

    init_test_quality(&win[1]);
    win[1].window_id = 2;
    win[1].pts_start = 100;
    win[1].pts_end = 200;

    init_test_quality(&win[2]);
    win[2].window_id = 3;
    win[2].pts_start = 200;
    win[2].pts_end = 300;

    recs[0].pts = 10;
    recs[0].index = 0;
    recs[1].pts = 20;
    recs[1].index = 1;
    recs[2].pts = 210;
    recs[2].index = 2;
    recs[3].pts = 220;
    recs[3].index = 3;

    res = pel_quality_align(win, 3, recs, 4, 1, 1000, match, 3);
    CHECK(res == PEL_OK);
    CHECK(match[0].matched == 1 && match[0].first_record == 0 && match[0].record_count == 2);
    CHECK(match[1].matched == 0 && match[1].first_record == UINT32_MAX &&
          match[1].record_count == 0);
    CHECK(match[2].matched == 1 && match[2].first_record == 2 && match[2].record_count == 2);
}

static void test_align_negative_and_duplicates(void)
{
    PelorusQualitySection win;
    PelQualityEncRecord recs[3];
    PelQualityMatch match;
    pel_result res;

    /* Negative PTS matching */
    init_test_quality(&win);
    win.pts_start = -200;
    win.pts_end = -100;
    recs[0].pts = -250;
    recs[0].index = 1;
    recs[1].pts = -150;
    recs[1].index = 2;
    recs[2].pts = -50;
    recs[2].index = 3;

    res = pel_quality_align(&win, 1, recs, 3, 1, 1000, &match, 1);
    CHECK(res == PEL_OK);
    CHECK(match.matched == 1 && match.first_record == 2 && match.record_count == 1);

    /* Duplicate PTS records counted */
    win.pts_start = 100;
    win.pts_end = 200;
    recs[0].pts = 150;
    recs[0].index = 10;
    recs[1].pts = 150;
    recs[1].index = 11;
    recs[2].pts = 150;
    recs[2].index = 12;

    res = pel_quality_align(&win, 1, recs, 3, 1, 1000, &match, 1);
    CHECK(res == PEL_OK);
    CHECK(match.matched == 1 && match.first_record == 10 && match.record_count == 3);
}

static void test_align_overflow(void)
{
    PelorusQualitySection win;
    PelQualityEncRecord rec;
    PelQualityMatch match;

    /* 1. Huge PTS with tb 1/90000 vs 1/1 */
    init_test_quality(&win);
    win.tb_num = 1;
    win.tb_den = 90000;
    win.pts_start = 0;
    win.pts_end = 100;
    rec.pts = INT64_MAX / 2;
    rec.index = 0;
    CHECK(pel_quality_align(&win, 1, &rec, 1, 1, 1, &match, 1) == PEL_ERR_RANGE);

    /* 2. INT64_MAX pts_end case with multiplier > 1 */
    init_test_quality(&win);
    win.tb_num = 1;
    win.tb_den = 1;
    win.pts_start = 0;
    win.pts_end = INT64_MAX;
    rec.pts = 10;
    rec.index = 0;
    CHECK(pel_quality_align(&win, 1, &rec, 1, 1, 2, &match, 1) == PEL_ERR_RANGE);
}

static void init_two_shots(PelShotTarget shots[2])
{
    shots[0].first_frame = 0;
    shots[0].last_frame = 29;
    shots[0].crf = 28.00f;
    shots[1].first_frame = 30;
    shots[1].last_frame = 59;
    shots[1].crf = 20.00f;
}

static void test_emitters_two_shot_fixture(void)
{
    static const char exp_zf[] = "0 --crf 28.00\n30 --crf 20.00\n";
    static const char exp_zp[] = "1,30,b=1.0000/31,60,b=2.5198";
    static const char exp_sp[] = "first_frame,last_frame,crf\n0,29,28.00\n30,59,20.00\n";
    PelShotTarget shots[2];
    char buf[256];
    size_t need = 0;

    init_two_shots(shots);

    /* Zonefile */
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 0, &need) == PEL_OK);
    CHECK(need == sizeof(exp_zf));
    CHECK(pel_zones_x265_zonefile(shots, 2, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, exp_zf) == 0);

    /* Zones param (0-based 0..29, 30..59 -> 1-based 1,30, 31,60) */
    need = 0;
    CHECK(pel_zones_x265_param(shots, 2, 28.00f, NULL, 0, &need) == PEL_OK);
    CHECK(need == sizeof(exp_zp));
    CHECK(pel_zones_x265_param(shots, 2, 28.00f, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, exp_zp) == 0);

    /* Segment plan */
    need = 0;
    CHECK(pel_zones_segment_plan(shots, 2, NULL, 0, &need) == PEL_OK);
    CHECK(need == sizeof(exp_sp));
    CHECK(pel_zones_segment_plan(shots, 2, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, exp_sp) == 0);
}

static void test_emitters_crf_rounding_and_pins(void)
{
    PelShotTarget shot;
    char buf[128];
    size_t need = 0;

    shot.first_frame = 0;
    shot.last_frame = 10;

    shot.crf = 0.0f;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, "0 --crf 0.00\n") == 0);

    shot.crf = -0.0f;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, "0 --crf 0.00\n") == 0);

    shot.crf = 63.0f;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, "0 --crf 63.00\n") == 0);

    shot.crf = 62.999f;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, "0 --crf 63.00\n") == 0);

    /* Pinned output for 19.995 */
    shot.crf = 19.995f;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_OK);
    CHECK(strcmp(buf, "0 --crf 20.00\n") == 0);

    /* Refusals: NaN, out of range */
    shot.crf = (float)NAN;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_ERR_RANGE);
    shot.crf = 63.01f;
    CHECK(pel_zones_x265_zonefile(&shot, 1, buf, sizeof(buf), &need) == PEL_ERR_RANGE);

    shot.crf = 20.0f;
    CHECK(pel_zones_x265_param(&shot, 1, (float)NAN, buf, sizeof(buf), &need) == PEL_ERR_RANGE);
    CHECK(pel_zones_x265_param(&shot, 1, -0.01f, buf, sizeof(buf), &need) == PEL_ERR_RANGE);
    CHECK(pel_zones_x265_param(&shot, 1, 63.01f, buf, sizeof(buf), &need) == PEL_ERR_RANGE);
}

static void test_emitters_buffer_semantics(void)
{
    PelShotTarget shots[2];
    char buf[128];
    size_t need = 0;
    size_t need2 = 0;

    init_two_shots(shots);

    /* need == NULL */
    CHECK(pel_zones_x265_zonefile(shots, 2, buf, sizeof(buf), NULL) == PEL_ERR_INVALID);
    CHECK(pel_zones_x265_param(shots, 2, 28.0f, buf, sizeof(buf), NULL) == PEL_ERR_INVALID);
    CHECK(pel_zones_segment_plan(shots, 2, buf, sizeof(buf), NULL) == PEL_ERR_INVALID);

    /* buf == NULL, cap > 0 -> PEL_ERR_INVALID, need == 0 */
    need = 999;
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 10, &need) == PEL_ERR_INVALID && need == 0);
    need = 999;
    CHECK(pel_zones_x265_param(shots, 2, 28.0f, NULL, 10, &need) == PEL_ERR_INVALID && need == 0);
    need = 999;
    CHECK(pel_zones_segment_plan(shots, 2, NULL, 10, &need) == PEL_ERR_INVALID && need == 0);

    /* cap == need vs cap == need - 1 */
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 0, &need) == PEL_OK);
    CHECK(pel_zones_x265_zonefile(shots, 2, buf, need, &need2) == PEL_OK);
    CHECK(pel_zones_x265_zonefile(shots, 2, buf, need - 1u, &need2) == PEL_ERR_RANGE &&
          need2 == need);

    CHECK(pel_zones_x265_param(shots, 2, 28.0f, NULL, 0, &need) == PEL_OK);
    CHECK(pel_zones_x265_param(shots, 2, 28.0f, buf, need, &need2) == PEL_OK);
    CHECK(pel_zones_x265_param(shots, 2, 28.0f, buf, need - 1u, &need2) == PEL_ERR_RANGE &&
          need2 == need);
}

static void test_emitters_shot_validation(void)
{
    PelShotTarget shots[2];
    size_t need = 999;

    init_two_shots(shots);

    /* n == 0 refused */
    CHECK(pel_zones_x265_zonefile(shots, 0, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);
    /* n == 4097 refused */
    CHECK(pel_zones_x265_zonefile(shots, 4097, NULL, 0, &need) == PEL_ERR_RANGE && need == 0);

    /* shots[0].first_frame != 0 refused */
    shots[0].first_frame = 1;
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);
    CHECK(pel_zones_x265_param(shots, 2, 28.0f, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);
    CHECK(pel_zones_segment_plan(shots, 2, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);

    /* last < first refused */
    shots[0].first_frame = 0;
    shots[0].last_frame = 10;
    shots[1].first_frame = 11;
    shots[1].last_frame = 5;
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);

    /* gaps and overlaps refused */
    shots[1].first_frame = 12;
    shots[1].last_frame = 20;
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);
    shots[1].first_frame = 10;
    CHECK(pel_zones_x265_zonefile(shots, 2, NULL, 0, &need) == PEL_ERR_INVALID && need == 0);
}

int main(void)
{
    test_quality_validate_scalars();
    test_quality_validate_ranges();
    test_quality_pack_refuses_invalid();
    test_quality_pack_find_roundtrip();
    test_quality_find_conditions();
    test_align_null_and_bounds();
    test_align_zero_records_and_zero_fill();
    test_align_edges_and_nearest_trap();
    test_align_three_windows_one_empty();
    test_align_negative_and_duplicates();
    test_align_overflow();
    test_emitters_two_shot_fixture();
    test_emitters_crf_rounding_and_pins();
    test_emitters_buffer_semantics();
    test_emitters_shot_validation();

    if (g_fail != 0) {
        (void)fprintf(stderr, "%d check(s) failed in quality_test\n", g_fail);
        return EXIT_FAILURE;
    }
    (void)printf("quality: all checks passed\n");
    return EXIT_SUCCESS;
}
