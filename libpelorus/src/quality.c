/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/quality.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

pel_result pel_quality_validate(const PelorusQualitySection *q)
{
    if (q == NULL) {
        return PEL_ERR_INVALID;
    }
    if (q->metric_id == (uint32_t)PEL_QM_NONE || q->metric_id > (uint32_t)PEL_QM_CAMBI) {
        return PEL_ERR_INVALID;
    }
    if (q->tb_num == 0 || q->tb_den == 0) {
        return PEL_ERR_INVALID;
    }
    if (q->pts_end <= q->pts_start) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(q->score)) {
        return PEL_ERR_INVALID;
    }
    if (q->frame_count == 0) {
        return PEL_ERR_INVALID;
    }
    if (q->_pad != 0) {
        return PEL_ERR_INVALID;
    }
    return PEL_OK;
}

pel_result pel_quality_pack(const PelorusSideData *meta, const PelorusQualitySection *q,
                            uint8_t **out, size_t *out_len)
{
    PelorusPackSection sec;
    pel_result res;

    if (meta == NULL || q == NULL || out == NULL || out_len == NULL) {
        return PEL_ERR_INVALID;
    }
    res = pel_quality_validate(q);
    if (res != PEL_OK) {
        return res;
    }

    sec.id = PEL_SEC_QUALITY;
    sec.data = q;
    sec.size = (uint32_t)sizeof(*q);

    return pel_blob_pack(meta, &sec, 1, out, out_len);
}

pel_result pel_quality_find(const uint8_t *blob, size_t len, PelorusQualitySection *out)
{
    const void *ptr = NULL;
    size_t readable_size = 0;
    size_t copy_size;
    pel_result res;

    if (blob == NULL || out == NULL) {
        return PEL_ERR_INVALID;
    }
    res = pel_blob_find_section(blob, len, PEL_SEC_QUALITY, sizeof(PelorusQualitySection), &ptr,
                                &readable_size);
    if (res != PEL_OK) {
        return res;
    }
    if (ptr == NULL || readable_size == 0) {
        return PEL_ERR_ABSENT;
    }

    memset(out, 0, sizeof(*out));
    copy_size = readable_size < sizeof(*out) ? readable_size : sizeof(*out);
    memcpy(out, ptr, copy_size);

    return pel_quality_validate(out);
}

static uint64_t gcd_u64(uint64_t a, uint64_t b)
{
    uint32_t iter = 0;
    while (b != 0 && iter < 128u) {
        uint64_t t = b;
        b = a % b;
        a = t;
        iter++;
    }
    return a;
}

static int mul_u64_checked(uint64_t a, uint64_t b, uint64_t *out)
{
    if (a != 0 && b > UINT64_MAX / a) {
        return -1;
    }
    *out = a * b;
    return 0;
}

static int mul_i64_checked(int64_t a, int64_t b, int64_t *out)
{
    if (a == 0 || b == 0) {
        *out = 0;
        return 0;
    }
    if (a > 0) {
        if (b > 0) {
            if (a > INT64_MAX / b) {
                return -1;
            }
        } else {
            if (b < INT64_MIN / a) {
                return -1;
            }
        }
    } else {
        if (b > 0) {
            if (a < INT64_MIN / b) {
                return -1;
            }
        } else {
            if (a < INT64_MAX / b) {
                return -1;
            }
        }
    }
    *out = a * b;
    return 0;
}

static size_t lower_bound_pts(const PelQualityEncRecord *records, size_t n, int64_t target,
                              int64_t kr)
{
    size_t low = 0;
    size_t high = n;
    uint32_t iter = 0;
    while (low < high && iter < 64u) {
        size_t mid = low + (high - low) / 2;
        int64_t r_scaled = records[mid].pts * kr;
        if (r_scaled < target) {
            low = mid + 1;
        } else {
            high = mid;
        }
        iter++;
    }
    return low;
}

static pel_result compute_window_scales(const PelorusQualitySection *w, uint32_t rec_tb_num,
                                        uint32_t rec_tb_den, int64_t *out_kw, int64_t *out_kr,
                                        int64_t *out_start, int64_t *out_end)
{
    uint64_t kw_u = 0;
    uint64_t kr_u = 0;
    uint64_t g = 0;

    if (mul_u64_checked(w->tb_num, rec_tb_den, &kw_u) != 0 ||
        mul_u64_checked(rec_tb_num, w->tb_den, &kr_u) != 0) {
        return PEL_ERR_RANGE;
    }
    g = gcd_u64(kw_u, kr_u);
    if (g == 0) {
        return PEL_ERR_RANGE;
    }
    kw_u /= g;
    kr_u /= g;
    if (kw_u > (uint64_t)INT64_MAX || kr_u > (uint64_t)INT64_MAX) {
        return PEL_ERR_RANGE;
    }
    *out_kw = (int64_t)kw_u;
    *out_kr = (int64_t)kr_u;
    if (mul_i64_checked(w->pts_start, *out_kw, out_start) != 0 ||
        mul_i64_checked(w->pts_end, *out_kw, out_end) != 0) {
        return PEL_ERR_RANGE;
    }
    return PEL_OK;
}

static pel_result check_records_sorted(const PelQualityEncRecord *records, size_t n_records)
{
    size_t i;
    for (i = 1; i < n_records; i++) {
        if (records[i].pts < records[i - 1].pts) {
            return PEL_ERR_INVALID;
        }
    }
    return PEL_OK;
}

static pel_result check_records_bounds(const PelQualityEncRecord *records, size_t n_records,
                                       int64_t kr)
{
    int64_t dummy = 0;
    if (n_records == 0) {
        return PEL_OK;
    }
    if (mul_i64_checked(records[0].pts, kr, &dummy) != 0 ||
        mul_i64_checked(records[n_records - 1].pts, kr, &dummy) != 0) {
        return PEL_ERR_RANGE;
    }
    return PEL_OK;
}

static pel_result align_single_window(const PelorusQualitySection *window,
                                      const PelQualityEncRecord *records, size_t n_records,
                                      uint32_t rec_tb_num, uint32_t rec_tb_den,
                                      PelQualityMatch *out)
{
    int64_t kw = 0;
    int64_t kr = 0;
    int64_t start_sc = 0;
    int64_t end_sc = 0;
    size_t i_start = 0;
    size_t i_end = 0;
    pel_result res;

    res = pel_quality_validate(window);
    if (res != PEL_OK) {
        return res;
    }
    res = compute_window_scales(window, rec_tb_num, rec_tb_den, &kw, &kr, &start_sc, &end_sc);
    if (res != PEL_OK) {
        return res;
    }
    res = check_records_bounds(records, n_records, kr);
    if (res != PEL_OK) {
        return res;
    }
    i_start = lower_bound_pts(records, n_records, start_sc, kr);
    i_end = lower_bound_pts(records, n_records, end_sc, kr);

    out->window_id = window->window_id;
    out->_pad = 0;
    if (i_start < i_end) {
        out->matched = 1;
        out->first_record = records[i_start].index;
        out->record_count = (uint32_t)(i_end - i_start);
    } else {
        out->matched = 0;
        out->first_record = UINT32_MAX;
        out->record_count = 0;
    }
    return PEL_OK;
}

pel_result pel_quality_align(const PelorusQualitySection *windows, size_t n_windows,
                             const PelQualityEncRecord *records, size_t n_records,
                             uint32_t rec_tb_num, uint32_t rec_tb_den, PelQualityMatch *out,
                             size_t out_cap)
{
    size_t w;
    pel_result res;

    if (n_windows > PEL_QUALITY_ALIGN_MAX || n_records > PEL_QUALITY_ALIGN_MAX) {
        return PEL_ERR_RANGE;
    }
    if (out_cap < n_windows) {
        return PEL_ERR_RANGE;
    }
    if (out != NULL && n_windows > 0) {
        memset(out, 0, n_windows * sizeof(*out));
    }
    if (rec_tb_num == 0 || rec_tb_den == 0) {
        return PEL_ERR_INVALID;
    }
    if (n_windows > 0 && (windows == NULL || out == NULL)) {
        return PEL_ERR_INVALID;
    }
    if (n_records > 0 && records == NULL) {
        return PEL_ERR_INVALID;
    }

    res = check_records_sorted(records, n_records);
    if (res != PEL_OK) {
        return res;
    }

    for (w = 0; w < n_windows; w++) {
        res = align_single_window(&windows[w], records, n_records, rec_tb_num, rec_tb_den, &out[w]);
        if (res != PEL_OK) {
            return res;
        }
    }
    return PEL_OK;
}

static pel_result validate_shots(const PelShotTarget *shots, size_t n)
{
    size_t i;
    if (shots == NULL || n == 0) {
        return PEL_ERR_INVALID;
    }
    if (n > PEL_SHOT_TARGET_MAX) {
        return PEL_ERR_RANGE;
    }
    if (shots[0].first_frame != 0u) {
        return PEL_ERR_INVALID;
    }
    for (i = 0; i < n; i++) {
        if (!isfinite(shots[i].crf) || shots[i].crf < 0.0f || shots[i].crf > 63.0f) {
            return PEL_ERR_RANGE;
        }
        if (shots[i].last_frame < shots[i].first_frame) {
            return PEL_ERR_INVALID;
        }
        if (i > 0) {
            if (shots[i].first_frame != shots[i - 1].last_frame + 1u) {
                return PEL_ERR_INVALID;
            }
        }
    }
    return PEL_OK;
}

static uint32_t crf_to_hundredths(float crf)
{
    double c = (double)crf;
    if (c <= 0.0) {
        return 0u;
    }
    return (uint32_t)floor(c * 100.0 + 0.5);
}

static uint64_t mult_to_ten_thousandths(float base_crf, float shot_crf)
{
    double delta = ((double)base_crf - (double)shot_crf) / 6.0;
    double mult = pow(2.0, delta);
    if (mult <= 0.0) {
        return 0u;
    }
    return (uint64_t)floor(mult * 10000.0 + 0.5);
}

static int format_shot_zonefile(char *buf, size_t cap, const PelShotTarget *shot)
{
    uint32_t crf_100 = crf_to_hundredths(shot->crf);
    return snprintf(buf, cap, "%u --crf %u.%02u\n", shot->first_frame, crf_100 / 100u,
                    crf_100 % 100u);
}

static int format_shot_param(char *buf, size_t cap, const PelShotTarget *shot, float base_crf,
                             int is_last)
{
    uint64_t m = mult_to_ten_thousandths(base_crf, shot->crf);
    const char *sep = is_last ? "" : "/";
    return snprintf(buf, cap, "%u,%u,b=%llu.%04llu%s", shot->first_frame + 1u,
                    shot->last_frame + 1u, (unsigned long long)(m / 10000u),
                    (unsigned long long)(m % 10000u), sep);
}

static int format_shot_segment_plan(char *buf, size_t cap, const PelShotTarget *shot)
{
    uint32_t crf_100 = crf_to_hundredths(shot->crf);
    return snprintf(buf, cap, "%u,%u,%u.%02u\n", shot->first_frame, shot->last_frame,
                    crf_100 / 100u, crf_100 % 100u);
}

pel_result pel_zones_x265_zonefile(const PelShotTarget *shots, size_t n, char *buf, size_t cap,
                                   size_t *need)
{
    pel_result res;
    size_t i;
    size_t total_len = 0;
    size_t offset = 0;

    if (need == NULL) {
        return PEL_ERR_INVALID;
    }
    *need = 0;
    if (buf == NULL && cap > 0) {
        return PEL_ERR_INVALID;
    }
    res = validate_shots(shots, n);
    if (res != PEL_OK) {
        return res;
    }

    for (i = 0; i < n; i++) {
        int len = format_shot_zonefile(NULL, 0, &shots[i]);
        if (len < 0) {
            return PEL_ERR_RANGE;
        }
        total_len += (size_t)len;
    }

    *need = total_len + 1u;
    if (buf == NULL && cap == 0) {
        return PEL_OK;
    }
    if (cap < *need) {
        return PEL_ERR_RANGE;
    }

    for (i = 0; i < n; i++) {
        int w = format_shot_zonefile(buf + offset, cap - offset, &shots[i]);
        if (w < 0 || (size_t)w >= cap - offset) {
            return PEL_ERR_RANGE;
        }
        offset += (size_t)w;
    }
    return PEL_OK;
}

pel_result pel_zones_x265_param(const PelShotTarget *shots, size_t n, float base_crf, char *buf,
                                size_t cap, size_t *need)
{
    pel_result res;
    size_t i;
    size_t total_len = 0;
    size_t offset = 0;

    if (need == NULL) {
        return PEL_ERR_INVALID;
    }
    *need = 0;
    if (buf == NULL && cap > 0) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(base_crf) || base_crf < 0.0f || base_crf > 63.0f) {
        return PEL_ERR_RANGE;
    }
    res = validate_shots(shots, n);
    if (res != PEL_OK) {
        return res;
    }

    for (i = 0; i < n; i++) {
        int len = format_shot_param(NULL, 0, &shots[i], base_crf, i + 1u == n);
        if (len < 0) {
            return PEL_ERR_RANGE;
        }
        total_len += (size_t)len;
    }

    *need = total_len + 1u;
    if (buf == NULL && cap == 0) {
        return PEL_OK;
    }
    if (cap < *need) {
        return PEL_ERR_RANGE;
    }

    for (i = 0; i < n; i++) {
        int w = format_shot_param(buf + offset, cap - offset, &shots[i], base_crf, i + 1u == n);
        if (w < 0 || (size_t)w >= cap - offset) {
            return PEL_ERR_RANGE;
        }
        offset += (size_t)w;
    }
    return PEL_OK;
}

pel_result pel_zones_segment_plan(const PelShotTarget *shots, size_t n, char *buf, size_t cap,
                                  size_t *need)
{
    static const char header[] = "first_frame,last_frame,crf\n";
    size_t header_len = sizeof(header) - 1u;
    pel_result res;
    size_t i;
    size_t total_len = header_len;
    size_t offset = header_len;

    if (need == NULL) {
        return PEL_ERR_INVALID;
    }
    *need = 0;
    if (buf == NULL && cap > 0) {
        return PEL_ERR_INVALID;
    }
    res = validate_shots(shots, n);
    if (res != PEL_OK) {
        return res;
    }

    for (i = 0; i < n; i++) {
        int len = format_shot_segment_plan(NULL, 0, &shots[i]);
        if (len < 0) {
            return PEL_ERR_RANGE;
        }
        total_len += (size_t)len;
    }

    *need = total_len + 1u;
    if (buf == NULL && cap == 0) {
        return PEL_OK;
    }
    if (cap < *need) {
        return PEL_ERR_RANGE;
    }

    memcpy(buf, header, header_len);
    for (i = 0; i < n; i++) {
        int w = format_shot_segment_plan(buf + offset, cap - offset, &shots[i]);
        if (w < 0 || (size_t)w >= cap - offset) {
            return PEL_ERR_RANGE;
        }
        offset += (size_t)w;
    }
    return PEL_OK;
}
