/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/grain_table.h"

#include <errno.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct GrainWriter {
    char *buf;
    size_t cap;
    size_t written;
} GrainWriter;

static int is_ws(char c)
{
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f';
}

static pel_result next_token(const char **p, const char *end, char *buf, size_t buf_sz)
{
    const char *cur = *p;
    size_t step = 0;
    size_t max_skip = (size_t)(end - cur);

    while (cur < end && is_ws(*cur) && step < max_skip) {
        cur++;
        step++;
    }
    if (cur >= end) {
        *p = cur;
        return PEL_ERR_ABSENT;
    }
    const char *start = cur;
    step = 0;
    max_skip = (size_t)(end - cur);
    while (cur < end && !is_ws(*cur) && step < max_skip) {
        cur++;
        step++;
    }
    size_t len = (size_t)(cur - start);
    if (len >= buf_sz) {
        size_t d = (start[0] == '-' || start[0] == '+') ? 1u : 0u;
        while (d < len && start[d] >= '0' && start[d] <= '9') {
            d++;
        }
        return (d == len) ? PEL_ERR_RANGE : PEL_ERR_INVALID;
    }
    (void)memcpy(buf, start, len);
    buf[len] = '\0';
    *p = cur;
    return PEL_OK;
}

static pel_result parse_i64_token(const char *tok, int64_t *val)
{
    char *endptr = NULL;
    errno = 0;
    long long v = strtoll(tok, &endptr, 10);
    if (errno == ERANGE) {
        return PEL_ERR_RANGE;
    }
    if (endptr == tok || *endptr != '\0') {
        return PEL_ERR_INVALID;
    }
    *val = (int64_t)v;
    return PEL_OK;
}

static pel_result parse_bounded_i32(const char *tok, int64_t min_v, int64_t max_v, int32_t *val)
{
    int64_t v = 0;
    pel_result r = parse_i64_token(tok, &v);
    if (r != PEL_OK) {
        return r;
    }
    if (v < min_v || v > max_v) {
        return PEL_ERR_RANGE;
    }
    *val = (int32_t)v;
    return PEL_OK;
}

static pel_result parse_entry_header(const char **p, const char *end, PelorusGrainTableEntry *entry)
{
    char tok[128];
    pel_result r;
    int64_t seed = 0;
    int32_t apply = 0;
    int32_t update = 0;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK)
        return PEL_ERR_INVALID;
    r = parse_i64_token(tok, &entry->start_time);
    if (r != PEL_OK)
        return r;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK)
        return PEL_ERR_INVALID;
    r = parse_i64_token(tok, &entry->end_time);
    if (r != PEL_OK)
        return r;
    if (entry->end_time < entry->start_time)
        return PEL_ERR_RANGE;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK)
        return PEL_ERR_INVALID;
    r = parse_bounded_i32(tok, 0, 1, &apply);
    if (r != PEL_OK)
        return r;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK)
        return PEL_ERR_INVALID;
    r = parse_i64_token(tok, &seed);
    if (r != PEL_OK)
        return r;
    if (seed < 0 || seed > 65535)
        return PEL_ERR_RANGE;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK)
        return PEL_ERR_INVALID;
    r = parse_bounded_i32(tok, 0, 1, &update);
    if (r != PEL_OK)
        return r;

    entry->params.apply = (uint8_t)apply;
    entry->params.seed = (uint64_t)seed;
    entry->update_parameters = (uint8_t)update;
    return PEL_OK;
}

static pel_result parse_p_line(const char **p, const char *end, PelorusFilmGrainSection *params)
{
    char tok[128];
    pel_result r;
    int32_t vals[12];
    static const int32_t bounds[12][2] = {{0, 3},   {6, 9},   {0, 3},   {8, 11},
                                          {0, 1},   {0, 1},   {0, 255}, {0, 255},
                                          {0, 511}, {0, 255}, {0, 255}, {0, 511}};

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK || strcmp(tok, "p") != 0)
        return PEL_ERR_INVALID;

    for (size_t i = 0; i < 12; i++) {
        r = next_token(p, end, tok, sizeof(tok));
        if (r != PEL_OK)
            return PEL_ERR_INVALID;
        r = parse_bounded_i32(tok, bounds[i][0], bounds[i][1], &vals[i]);
        if (r != PEL_OK)
            return r;
    }

    params->ar_coeff_lag = vals[0];
    params->ar_coeff_shift = vals[1];
    params->grain_scale_shift = vals[2];
    params->scaling_shift = vals[3];
    params->chroma_scaling_from_luma = (uint8_t)vals[4];
    params->overlap_flag = (uint8_t)vals[5];
    params->uv_mult[0] = vals[6] - 128;
    params->uv_mult_luma[0] = vals[7] - 128;
    params->uv_offset[0] = vals[8] - 256;
    params->uv_mult[1] = vals[9] - 128;
    params->uv_mult_luma[1] = vals[10] - 128;
    params->uv_offset[1] = vals[11] - 256;
    return PEL_OK;
}

static pel_result parse_points(const char **p, const char *end, const char *expected_tag,
                               int32_t max_pts, int32_t *num_pts_out, uint8_t pts[][2])
{
    char tok[128];
    pel_result r;
    int32_t count = 0;
    int32_t prev_x = -1;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK || strcmp(tok, expected_tag) != 0)
        return PEL_ERR_INVALID;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK)
        return PEL_ERR_INVALID;
    r = parse_bounded_i32(tok, 0, max_pts, &count);
    if (r != PEL_OK)
        return r;

    *num_pts_out = count;
    for (int32_t i = 0; i < count && i < max_pts; i++) {
        int32_t x = 0;
        int32_t y = 0;
        r = next_token(p, end, tok, sizeof(tok));
        if (r != PEL_OK)
            return PEL_ERR_INVALID;
        r = parse_bounded_i32(tok, 0, 255, &x);
        if (r != PEL_OK)
            return r;
        if (x <= prev_x)
            return PEL_ERR_RANGE;
        prev_x = x;

        r = next_token(p, end, tok, sizeof(tok));
        if (r != PEL_OK)
            return PEL_ERR_INVALID;
        r = parse_bounded_i32(tok, 0, 255, &y);
        if (r != PEL_OK)
            return r;

        pts[i][0] = (uint8_t)x;
        pts[i][1] = (uint8_t)y;
    }
    return PEL_OK;
}

static pel_result parse_coeffs(const char **p, const char *end, const char *expected_tag,
                               int32_t count, int8_t *coeffs, int32_t max_cap)
{
    char tok[128];
    pel_result r;

    r = next_token(p, end, tok, sizeof(tok));
    if (r != PEL_OK || strcmp(tok, expected_tag) != 0)
        return PEL_ERR_INVALID;

    for (int32_t i = 0; i < count && i < max_cap; i++) {
        int32_t v = 0;
        r = next_token(p, end, tok, sizeof(tok));
        if (r != PEL_OK)
            return PEL_ERR_INVALID;
        r = parse_bounded_i32(tok, -128, 127, &v);
        if (r != PEL_OK)
            return r;
        coeffs[i] = (int8_t)v;
    }
    return PEL_OK;
}

static pel_result parse_entry(const char **p, const char *end, PelorusGrainTableEntry *entry)
{
    pel_result r;
    (void)memset(&entry->params, 0, sizeof(entry->params));
    entry->params.grain_model = PEL_GRAIN_AOM;

    r = parse_entry_header(p, end, entry);
    if (r != PEL_OK)
        return r;

    if (!entry->update_parameters) {
        return PEL_OK;
    }

    r = parse_p_line(p, end, &entry->params);
    if (r != PEL_OK)
        return r;

    r = parse_points(p, end, "sY", 14, &entry->params.num_y_points, entry->params.y_points);
    if (r != PEL_OK)
        return r;

    r = parse_points(p, end, "sCb", 10, &entry->params.num_uv_points[0],
                     entry->params.uv_points[0]);
    if (r != PEL_OK)
        return r;

    r = parse_points(p, end, "sCr", 10, &entry->params.num_uv_points[1],
                     entry->params.uv_points[1]);
    if (r != PEL_OK)
        return r;

    int32_t lag = entry->params.ar_coeff_lag;
    int32_t n = 2 * lag * (lag + 1);

    r = parse_coeffs(p, end, "cY", n, entry->params.ar_coeffs_y, 24);
    if (r != PEL_OK)
        return r;

    r = parse_coeffs(p, end, "cCb", n + 1, entry->params.ar_coeffs_uv[0], 25);
    if (r != PEL_OK)
        return r;

    r = parse_coeffs(p, end, "cCr", n + 1, entry->params.ar_coeffs_uv[1], 25);
    if (r != PEL_OK)
        return r;

    return PEL_OK;
}

pel_result pel_grain_table_parse(const char *text, size_t len, PelorusGrainTableEntry *out,
                                 size_t cap, size_t *count)
{
    if (text == NULL || count == NULL || (out == NULL && cap > 0)) {
        return PEL_ERR_INVALID;
    }
    *count = 0;
    if (len < 9 || memcmp(text, "filmgrn1", 8) != 0 || !is_ws(text[8])) {
        return PEL_ERR_INVALID;
    }
    const char *p = text + 8;
    const char *end = text + len;
    size_t entries_parsed = 0;
    size_t step = 0;

    while (p < end && step < len) {
        char tok[128];
        pel_result r = next_token(&p, end, tok, sizeof(tok));
        if (r == PEL_ERR_ABSENT) {
            break;
        }
        if (r != PEL_OK || strcmp(tok, "E") != 0) {
            return PEL_ERR_INVALID;
        }
        if (entries_parsed >= cap) {
            *count = entries_parsed;
            return PEL_ERR_TRUNCATED;
        }
        r = parse_entry(&p, end, &out[entries_parsed]);
        if (r != PEL_OK) {
            return r;
        }
        entries_parsed++;
        step++;
    }
    *count = entries_parsed;
    return PEL_OK;
}

static void gw_putc(GrainWriter *w, char c)
{
    if (w->buf != NULL && w->written < w->cap) {
        w->buf[w->written] = c;
    }
    w->written++;
}

static void gw_puts(GrainWriter *w, const char *s)
{
    while (*s != '\0') {
        gw_putc(w, *s);
        s++;
    }
}

static void gw_put_i64(GrainWriter *w, int64_t v)
{
    char tmp[32];
    int n = 0;
    uint64_t uv;

    if (v < 0) {
        gw_putc(w, '-');
        uv = (uint64_t)-(v + 1) + 1U;
    } else {
        uv = (uint64_t)v;
    }
    do {
        tmp[n] = (char)('0' + (uv % 10U));
        n++;
        uv /= 10U;
    } while (uv > 0U && n < 31);
    while (n > 0) {
        n--;
        gw_putc(w, tmp[n]);
    }
}

static pel_result validate_entry(const PelorusGrainTableEntry *entry)
{
    if (entry->end_time < entry->start_time)
        return PEL_ERR_RANGE;
    if (entry->params.apply > 1 || entry->update_parameters > 1)
        return PEL_ERR_RANGE;
    if (entry->params.seed > 65535)
        return PEL_ERR_RANGE;
    if (!entry->update_parameters)
        return PEL_OK;

    const PelorusFilmGrainSection *p = &entry->params;
    if (p->ar_coeff_lag < 0 || p->ar_coeff_lag > 3)
        return PEL_ERR_RANGE;
    if (p->ar_coeff_shift < 6 || p->ar_coeff_shift > 9)
        return PEL_ERR_RANGE;
    if (p->grain_scale_shift < 0 || p->grain_scale_shift > 3)
        return PEL_ERR_RANGE;
    if (p->scaling_shift < 8 || p->scaling_shift > 11)
        return PEL_ERR_RANGE;
    if (p->chroma_scaling_from_luma > 1 || p->overlap_flag > 1)
        return PEL_ERR_RANGE;

    for (int c = 0; c < 2; c++) {
        if (p->uv_mult[c] < -128 || p->uv_mult[c] > 127 || p->uv_mult_luma[c] < -128 ||
            p->uv_mult_luma[c] > 127 || p->uv_offset[c] < -256 || p->uv_offset[c] > 255) {
            return PEL_ERR_RANGE;
        }
    }
    if (p->num_y_points < 0 || p->num_y_points > 14)
        return PEL_ERR_RANGE;
    for (int32_t i = 1; i < p->num_y_points && i < 14; i++) {
        if (p->y_points[i][0] <= p->y_points[i - 1][0])
            return PEL_ERR_RANGE;
    }
    for (int c = 0; c < 2; c++) {
        if (p->num_uv_points[c] < 0 || p->num_uv_points[c] > 10)
            return PEL_ERR_RANGE;
        for (int32_t i = 1; i < p->num_uv_points[c] && i < 10; i++) {
            if (p->uv_points[c][i][0] <= p->uv_points[c][i - 1][0])
                return PEL_ERR_RANGE;
        }
    }
    return PEL_OK;
}

static void write_entry_header(GrainWriter *w, const PelorusGrainTableEntry *entry)
{
    gw_puts(w, "E ");
    gw_put_i64(w, entry->start_time);
    gw_putc(w, ' ');
    gw_put_i64(w, entry->end_time);
    gw_putc(w, ' ');
    gw_put_i64(w, entry->params.apply);
    gw_putc(w, ' ');
    gw_put_i64(w, (int64_t)entry->params.seed);
    gw_putc(w, ' ');
    gw_put_i64(w, entry->update_parameters);
    gw_putc(w, '\n');
}

static void write_entry_params(GrainWriter *w, const PelorusFilmGrainSection *p)
{
    gw_puts(w, "\tp ");
    gw_put_i64(w, p->ar_coeff_lag);
    gw_putc(w, ' ');
    gw_put_i64(w, p->ar_coeff_shift);
    gw_putc(w, ' ');
    gw_put_i64(w, p->grain_scale_shift);
    gw_putc(w, ' ');
    gw_put_i64(w, p->scaling_shift);
    gw_putc(w, ' ');
    gw_put_i64(w, p->chroma_scaling_from_luma);
    gw_putc(w, ' ');
    gw_put_i64(w, p->overlap_flag);
    gw_putc(w, ' ');
    gw_put_i64(w, p->uv_mult[0] + 128);
    gw_putc(w, ' ');
    gw_put_i64(w, p->uv_mult_luma[0] + 128);
    gw_putc(w, ' ');
    gw_put_i64(w, p->uv_offset[0] + 256);
    gw_putc(w, ' ');
    gw_put_i64(w, p->uv_mult[1] + 128);
    gw_putc(w, ' ');
    gw_put_i64(w, p->uv_mult_luma[1] + 128);
    gw_putc(w, ' ');
    gw_put_i64(w, p->uv_offset[1] + 256);
    gw_putc(w, '\n');
}

static void write_entry_points(GrainWriter *w, const PelorusFilmGrainSection *p)
{
    gw_puts(w, "\tsY ");
    gw_put_i64(w, p->num_y_points);
    gw_putc(w, ' ');
    for (int32_t i = 0; i < p->num_y_points && i < 14; i++) {
        gw_putc(w, ' ');
        gw_put_i64(w, p->y_points[i][0]);
        gw_putc(w, ' ');
        gw_put_i64(w, p->y_points[i][1]);
    }
    gw_puts(w, "\n\tsCb ");
    gw_put_i64(w, p->num_uv_points[0]);
    for (int32_t i = 0; i < p->num_uv_points[0] && i < 10; i++) {
        gw_putc(w, ' ');
        gw_put_i64(w, p->uv_points[0][i][0]);
        gw_putc(w, ' ');
        gw_put_i64(w, p->uv_points[0][i][1]);
    }
    gw_puts(w, "\n\tsCr ");
    gw_put_i64(w, p->num_uv_points[1]);
    for (int32_t i = 0; i < p->num_uv_points[1] && i < 10; i++) {
        gw_putc(w, ' ');
        gw_put_i64(w, p->uv_points[1][i][0]);
        gw_putc(w, ' ');
        gw_put_i64(w, p->uv_points[1][i][1]);
    }
}

static void write_entry_coeffs(GrainWriter *w, const PelorusFilmGrainSection *p)
{
    int32_t n = 2 * p->ar_coeff_lag * (p->ar_coeff_lag + 1);
    gw_puts(w, "\n\tcY");
    for (int32_t i = 0; i < n && i < 24; i++) {
        gw_putc(w, ' ');
        gw_put_i64(w, p->ar_coeffs_y[i]);
    }
    gw_puts(w, "\n\tcCb");
    for (int32_t i = 0; i <= n && i < 25; i++) {
        gw_putc(w, ' ');
        gw_put_i64(w, p->ar_coeffs_uv[0][i]);
    }
    gw_puts(w, "\n\tcCr");
    for (int32_t i = 0; i <= n && i < 25; i++) {
        gw_putc(w, ' ');
        gw_put_i64(w, p->ar_coeffs_uv[1][i]);
    }
    gw_putc(w, '\n');
}

static void write_entry(GrainWriter *w, const PelorusGrainTableEntry *entry)
{
    write_entry_header(w, entry);
    if (!entry->update_parameters) {
        return;
    }
    write_entry_params(w, &entry->params);
    write_entry_points(w, &entry->params);
    write_entry_coeffs(w, &entry->params);
}

pel_result pel_grain_table_write(const PelorusGrainTableEntry *in, size_t count, char *buf,
                                 size_t cap, size_t *written)
{
    if (written == NULL) {
        return PEL_ERR_INVALID;
    }
    *written = 0;
    if ((in == NULL && count > 0) || (buf == NULL && cap > 0)) {
        return PEL_ERR_INVALID;
    }
    for (size_t i = 0; i < count; i++) {
        pel_result r = validate_entry(&in[i]);
        if (r != PEL_OK) {
            return r;
        }
    }
    GrainWriter w;
    w.buf = buf;
    w.cap = cap;
    w.written = 0;

    gw_puts(&w, "filmgrn1\n");
    for (size_t i = 0; i < count; i++) {
        write_entry(&w, &in[i]);
    }
    *written = w.written;
    if (cap < w.written) {
        return PEL_ERR_TRUNCATED;
    }
    return PEL_OK;
}
