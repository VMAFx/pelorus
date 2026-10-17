/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "encode_options_internal.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static int is_ws(char c)
{
    return c == ' ' || c == '\n' || c == '\t' || c == '\r';
}

typedef const PelEncodeOptDef *(*pel_opt_lookup_fn)(const char *name);

/* Parsing rules per dialect, each read from the encoder's own parser:
 * x264 common/base.c:856,913-919; x265 source/common/param.cpp:937-946,1551;
 * SVT-AV1 Source/Lib/Globals/enc_settings.c:1282-1350,1485-1497;
 * libaom common/args_helper.c:33-117 (long name only, base-10 integers). */
typedef struct PelOptDialect {
    pel_opt_lookup_fn lookup;
    int int_base;        /* strtoll base */
    int bool_nocase;     /* true/false/yes/no match case-insensitively */
    int fold_underscore; /* '_' in a key reads as '-' */
    int strip_dashes;    /* a leading "--" is dropped */
    int negation;        /* "no"/"no-" prefix inverts a BOOL option */
} PelOptDialect;

static const PelOptDialect k_dialects[] = {
    {pel_encode_options_x264_lookup, 0, 1, 1, 0, 1},
    {pel_encode_options_x265_lookup, 0, 0, 1, 1, 1},
    {pel_encode_options_svtav1_lookup, 0, 1, 0, 0, 0},
    {pel_encode_options_aom_lookup, 10, 1, 0, 0, 0},
};

static const PelOptDialect *dialect_rules(pel_encode_options_dialect d)
{
    if ((int)d < (int)PEL_ENCODE_OPTIONS_X264 || (int)d > (int)PEL_ENCODE_OPTIONS_AOM) {
        return NULL;
    }
    return &k_dialects[(int)d - (int)PEL_ENCODE_OPTIONS_X264];
}

static pel_result get_token(const char *text, size_t len, size_t *pos, char term, char *out,
                            size_t max_out, size_t *out_len)
{
    size_t p = *pos;
    size_t o = 0;
    size_t end = 0;

    while (p < len && is_ws(text[p])) {
        p++;
    }
    while (p < len && text[p] != '\0' && text[p] != term) {
        char c = text[p++];
        if (c == '\\' && p < len && text[p] != '\0') {
            if (o >= max_out) {
                return PEL_ERR_RANGE;
            }
            out[o++] = text[p++];
            end = o;
        } else if (c == '\'') {
            while (p < len && text[p] != '\'' && text[p] != '\0') {
                if (o >= max_out) {
                    return PEL_ERR_RANGE;
                }
                out[o++] = text[p++];
            }
            if (p < len && text[p] == '\'') {
                p++;
                end = o;
            }
        } else {
            if (o >= max_out) {
                return PEL_ERR_RANGE;
            }
            out[o++] = c;
        }
    }
    while (o > end && is_ws(out[o - 1])) {
        o--;
    }
    out[o] = '\0';
    *pos = p;
    *out_len = o;
    return PEL_OK;
}

static int parse_bool_val(const PelOptDialect *dl, const char *val, int *out_bool)
{
    if (dl->bool_nocase) {
        if (strcmp(val, "1") == 0 || strcasecmp(val, "true") == 0 || strcasecmp(val, "yes") == 0) {
            *out_bool = 1;
            return 1;
        }
        if (strcmp(val, "0") == 0 || strcasecmp(val, "false") == 0 || strcasecmp(val, "no") == 0) {
            *out_bool = 0;
            return 1;
        }
    } else {
        if (strcmp(val, "1") == 0 || strcmp(val, "true") == 0 || strcmp(val, "yes") == 0) {
            *out_bool = 1;
            return 1;
        }
        if (strcmp(val, "0") == 0 || strcmp(val, "false") == 0 || strcmp(val, "no") == 0) {
            *out_bool = 0;
            return 1;
        }
    }
    return 0;
}

static int parse_int_val(const char *val, int base, int is_unsigned,
                         char out[PEL_ENCODE_RECORD_SCALAR_SIZE], int *range_err)
{
    char *end = NULL;
    long long v;

    *range_err = 0;
    if (!val || val[0] == '\0') {
        return 0;
    }
    errno = 0;
    v = strtoll(val, &end, base);
    if (errno == 0 && end != val && *end == '\0' && is_unsigned && v < 0) {
        /* SVT-AV1 str_to_uint and libaom strtoul refuse a negative value */
        return 0;
    }
    if (errno == ERANGE || v > (is_unsigned ? 4294967295LL : 2147483647LL) || v < -2147483647LL) {
        *range_err = 1;
        return 0;
    }
    if (errno != 0 || end == val || *end != '\0') {
        return 0;
    }
    if (pel_encode_record_int((int64_t)v, out) != PEL_OK) {
        return 0;
    }
    return 1;
}

static int parse_float_val(const char *val, char out[PEL_ENCODE_RECORD_SCALAR_SIZE])
{
    char *end = NULL;
    double v;

    if (!val || val[0] == '\0') {
        return 0;
    }
    errno = 0;
    v = strtod(val, &end);
    if (errno != 0 || end == val || *end != '\0') {
        return 0;
    }
    if (pel_encode_record_f64(v, out) != PEL_OK) {
        return 0;
    }
    return 1;
}

/* x264 and x265 strip a "no" or "no-" prefix from any name and invert the
 * value (x264 common/base.c:913-919, x265 common/param.cpp:937-946). The
 * record keeps the inversion only for BOOL entries: a non-BOOL option would
 * receive "true"/"false" under its INT, FLOAT or STRING key, so a negated
 * non-BOOL name stays an unknown entry. */
static void lookup_negatable(pel_opt_lookup_fn lookup, const char *buf,
                             const PelEncodeOptDef **def_out, int *is_neg_out)
{
    /* char compares, not strncmp: buf[2] is then provably inside the string */
    if (buf[0] == 'n' && buf[1] == 'o') {
        const char *p = buf + 2;
        const PelEncodeOptDef *def;
        if (*p == '-') {
            p++;
        }
        def = lookup(p);
        if (def && def->type == PEL_OPT_TYPE_BOOL) {
            *def_out = def;
            *is_neg_out = 1;
            return;
        }
    }
    *def_out = lookup(buf);
}

/* Writes the normalised key (x265 "--" stripped, x264/x265 '_' -> '-') to
 * norm, which holds PEL_ENCODE_RECORD_MAX_KEY + 1 bytes; raw_key is at most
 * that long. */
static void normalize_and_lookup(const PelOptDialect *dl, const char *raw_key, char *norm,
                                 const PelEncodeOptDef **def_out, int *is_neg_out)
{
    const char *src = raw_key;
    size_t len;
    size_t i;

    *def_out = NULL;
    *is_neg_out = 0;
    if (dl->strip_dashes && src[0] == '-' && src[1] == '-') {
        src += 2;
    }
    len = strlen(src);
    for (i = 0; i < len; i++) {
        norm[i] = (dl->fold_underscore && src[i] == '_') ? '-' : src[i];
    }
    norm[len] = '\0';
    if (len == 0) {
        return;
    }
    if (dl->negation) {
        lookup_negatable(dl->lookup, norm, def_out, is_neg_out);
    } else {
        *def_out = dl->lookup(norm);
    }
}

static void remove_unknown_key(PelorusEncodeOptions *out, const char *k1, const char *k2)
{
    uint32_t i = 0;
    while (i < out->nb_unknown_params) {
        if ((k1 && strcmp(out->unknown_params[i].key, k1) == 0) ||
            (k2 && strcmp(out->unknown_params[i].key, k2) == 0)) {
            out->unknown_params[i] = out->unknown_params[out->nb_unknown_params - 1];
            out->unknown_val_ptrs[i] = out->unknown_val_ptrs[out->nb_unknown_params - 1];
            out->unknown_params[i].values = &out->unknown_val_ptrs[i];
            out->nb_unknown_params--;
        } else {
            i++;
        }
    }
}

static void remove_param_key(PelorusEncodeOptions *out, const char *key)
{
    uint32_t i = 0;
    if (!key) {
        return;
    }
    while (i < out->nb_params) {
        if (strcmp(out->params[i].key, key) == 0) {
            out->params[i] = out->params[out->nb_params - 1];
            out->param_val_ptrs[i] = out->param_val_ptrs[out->nb_params - 1];
            out->params[i].values = &out->param_val_ptrs[i];
            out->nb_params--;
        } else {
            i++;
        }
    }
}

static void compact_arena(PelorusEncodeOptions *out)
{
    const char **ptrs[PEL_ENCODE_RECORD_MAX_MEMBERS * 4];
    size_t total = 0;
    size_t i;
    size_t j;
    size_t dst = 0;

    for (i = 0; i < out->nb_params; i++) {
        ptrs[total++] = &out->params[i].key;
        ptrs[total++] = &out->param_val_ptrs[i];
    }
    for (i = 0; i < out->nb_unknown_params; i++) {
        ptrs[total++] = &out->unknown_params[i].key;
        ptrs[total++] = &out->unknown_val_ptrs[i];
    }

    for (i = 1; i < total; i++) {
        const char **cur = ptrs[i];
        for (j = i; j > 0 && *ptrs[j - 1] > *cur; j--) {
            ptrs[j] = ptrs[j - 1];
        }
        ptrs[j] = cur;
    }

    for (i = 0; i < total; i++) {
        const char *src = *ptrs[i];
        size_t slen = strlen(src) + 1;
        if (out->arena + dst != src) {
            (void)memmove(out->arena + dst, src, slen);
            *ptrs[i] = out->arena + dst;
        }
        dst += slen;
    }
    out->arena_used = dst;
    for (i = 0; i < out->nb_params; i++) {
        out->params[i].values = &out->param_val_ptrs[i];
    }
    for (i = 0; i < out->nb_unknown_params; i++) {
        out->unknown_params[i].values = &out->unknown_val_ptrs[i];
    }
}

static pel_result update_entry_val(PelorusEncodeOptions *out, PelorusEncodeParam *p,
                                   const char **val_ptr, const char *val, size_t vlen)
{
    size_t old_vlen = strlen(*val_ptr);
    char *vbuf;

    if (vlen <= old_vlen) {
        (void)memcpy((char *)*val_ptr, val, vlen);
        ((char *)*val_ptr)[vlen] = '\0';
        return PEL_OK;
    }
    if (*val_ptr + old_vlen + 1 == &out->arena[out->arena_used]) {
        if (out->arena_used + (vlen - old_vlen) > PEL_ENCODE_OPTIONS_ARENA_SIZE) {
            return PEL_ERR_RANGE;
        }
        (void)memcpy((char *)*val_ptr, val, vlen);
        ((char *)*val_ptr)[vlen] = '\0';
        out->arena_used += (vlen - old_vlen);
        return PEL_OK;
    }
    if (out->arena_used + vlen + 1 > PEL_ENCODE_OPTIONS_ARENA_SIZE) {
        compact_arena(out);
        if (out->arena_used + vlen + 1 > PEL_ENCODE_OPTIONS_ARENA_SIZE) {
            return PEL_ERR_RANGE;
        }
    }
    vbuf = &out->arena[out->arena_used];
    (void)memcpy(vbuf, val, vlen);
    vbuf[vlen] = '\0';
    out->arena_used += vlen + 1;
    *val_ptr = vbuf;
    p->values = val_ptr;
    return PEL_OK;
}

static pel_result store_entry(PelorusEncodeOptions *out, PelorusEncodeParam *params,
                              const char **val_ptrs, uint32_t *nb_params, const char *key,
                              const char *val, size_t klen, size_t vlen)
{
    uint32_t i;
    char *kbuf;
    char *vbuf;

    for (i = 0; i < *nb_params; i++) {
        if (strcmp(params[i].key, key) == 0) {
            return update_entry_val(out, &params[i], &val_ptrs[i], val, vlen);
        }
    }

    if (*nb_params >= PEL_ENCODE_RECORD_MAX_MEMBERS) {
        return PEL_ERR_RANGE;
    }
    if (out->arena_used + klen + 1 + vlen + 1 > PEL_ENCODE_OPTIONS_ARENA_SIZE) {
        compact_arena(out);
        if (out->arena_used + klen + 1 + vlen + 1 > PEL_ENCODE_OPTIONS_ARENA_SIZE) {
            return PEL_ERR_RANGE;
        }
    }
    kbuf = &out->arena[out->arena_used];
    (void)memcpy(kbuf, key, klen);
    kbuf[klen] = '\0';
    out->arena_used += klen + 1;

    vbuf = &out->arena[out->arena_used];
    (void)memcpy(vbuf, val, vlen);
    vbuf[vlen] = '\0';
    out->arena_used += vlen + 1;

    val_ptrs[*nb_params] = vbuf;
    params[*nb_params].key = kbuf;
    params[*nb_params].values = &val_ptrs[*nb_params];
    params[*nb_params].nb_values = 1;
    params[*nb_params].is_array = 0;
    (*nb_params)++;
    return PEL_OK;
}

static void sort_params(PelorusEncodeParam *p, const char **vptrs, uint32_t count)
{
    uint32_t i;
    uint32_t j;

    for (i = 1; i < count; i++) {
        PelorusEncodeParam cur = p[i];
        const char *cur_v = vptrs[i];
        for (j = i; j > 0 && strcmp(p[j - 1].key, cur.key) > 0; j--) {
            p[j] = p[j - 1];
            vptrs[j] = vptrs[j - 1];
            p[j].values = &vptrs[j];
        }
        p[j] = cur;
        vptrs[j] = cur_v;
        p[j].values = &vptrs[j];
    }
    for (i = 0; i < count; i++) {
        p[i].values = &vptrs[i];
        p[i].nb_values = 1;
        p[i].is_array = 0;
    }
}

/* Drops every unknown entry a known key can sit under: its canonical name and,
 * where the dialect negates, the spellings "no-<key>" and "no<key>", so the
 * last value wins. */
static void drop_unknown_spellings(const PelOptDialect *dl, PelorusEncodeOptions *out,
                                   const char *canonical)
{
    char neg[PEL_ENCODE_RECORD_MAX_KEY + 4];
    size_t clen = strlen(canonical);

    remove_unknown_key(out, canonical, NULL);
    if (!dl->negation || clen + 3 >= sizeof(neg)) {
        return;
    }
    neg[0] = 'n';
    neg[1] = 'o';
    neg[2] = '-';
    memcpy(neg + 3, canonical, clen + 1);
    remove_unknown_key(out, neg, NULL);
    memmove(neg + 2, neg + 3, clen + 1);
    remove_unknown_key(out, neg, NULL);
}

/* Checks raw_val against def's type; on success *final_val points at the
 * canonical value text (raw_val, a literal, or scalar_buf). */
static pel_result validate_known_value(const PelOptDialect *dl, const PelEncodeOptDef *def,
                                       int is_neg, const char *raw_val,
                                       char scalar_buf[PEL_ENCODE_RECORD_SCALAR_SIZE],
                                       const char **final_val, int *valid)
{
    int bval = 0;
    int range_err = 0;

    *valid = 0;
    *final_val = raw_val;
    if (def->type == PEL_OPT_TYPE_BOOL) {
        if (parse_bool_val(dl, raw_val, &bval)) {
            *final_val = (bval != is_neg) ? "true" : "false";
            *valid = 1;
        }
    } else if (def->type == PEL_OPT_TYPE_INT || def->type == PEL_OPT_TYPE_UINT) {
        if (parse_int_val(raw_val, dl->int_base, def->type == PEL_OPT_TYPE_UINT, scalar_buf,
                          &range_err)) {
            *final_val = scalar_buf;
            *valid = 1;
        } else if (range_err) {
            return PEL_ERR_RANGE;
        }
    } else if (def->type == PEL_OPT_TYPE_FLOAT) {
        if (parse_float_val(raw_val, scalar_buf)) {
            *final_val = scalar_buf;
            *valid = 1;
        }
    } else {
        *valid = 1;
    }
    return PEL_OK;
}

static pel_result process_pair(const PelOptDialect *dl, const char *raw_key, const char *raw_val,
                               size_t vlen, PelorusEncodeOptions *out)
{
    const PelEncodeOptDef *def = NULL;
    int is_neg = 0;
    int valid = 0;
    char scalar_buf[PEL_ENCODE_RECORD_SCALAR_SIZE];
    const char *final_val = raw_val;
    char norm[PEL_ENCODE_RECORD_MAX_KEY + 1];
    const char *unknown_key;
    pel_result r;

    normalize_and_lookup(dl, raw_key, norm, &def, &is_neg);
    if (def != NULL) {
        r = validate_known_value(dl, def, is_neg, raw_val, scalar_buf, &final_val, &valid);
        if (r != PEL_OK) {
            return r;
        }
        drop_unknown_spellings(dl, out, def->canonical);
    }
    if (def != NULL && valid) {
        return store_entry(out, out->params, out->param_val_ptrs, &out->nb_params, def->canonical,
                           final_val, strlen(def->canonical),
                           final_val == raw_val ? vlen : strlen(final_val));
    }
    /* Unknown entries use the key the encoder would see, so spellings that
     * normalise alike collapse to one entry and the last one wins. A known
     * key with a value of the wrong type keeps its canonical name. */
    if (def != NULL && !is_neg) {
        unknown_key = def->canonical;
    } else if (norm[0] != '\0') {
        unknown_key = norm;
    } else {
        unknown_key = raw_key;
    }
    if (def != NULL) {
        remove_param_key(out, def->canonical);
    }
    return store_entry(out, out->unknown_params, out->unknown_val_ptrs, &out->nb_unknown_params,
                       unknown_key, raw_val, strlen(unknown_key), vlen);
}

static pel_result parse_one_pair(const PelOptDialect *dl, const char *text, size_t len, size_t *pos,
                                 PelorusEncodeOptions *out)
{
    char raw_key[PEL_ENCODE_RECORD_MAX_KEY + 2];
    char raw_val[PEL_ENCODE_RECORD_MAX_VALUE + 2];
    size_t klen = 0;
    size_t vlen = 0;
    pel_result r;

    r = get_token(text, len, pos, '=', raw_key, sizeof(raw_key) - 1, &klen);
    if (r != PEL_OK) {
        return r;
    }
    if (klen == 0 || klen > PEL_ENCODE_RECORD_MAX_KEY) {
        return klen > PEL_ENCODE_RECORD_MAX_KEY ? PEL_ERR_RANGE : PEL_ERR_INVALID;
    }
    if (*pos >= len || text[*pos] != '=') {
        return PEL_ERR_INVALID;
    }
    (*pos)++;

    r = get_token(text, len, pos, ':', raw_val, sizeof(raw_val) - 1, &vlen);
    if (r != PEL_OK) {
        return r;
    }
    if (vlen == 0 || vlen > PEL_ENCODE_RECORD_MAX_VALUE) {
        return vlen > PEL_ENCODE_RECORD_MAX_VALUE ? PEL_ERR_RANGE : PEL_ERR_INVALID;
    }

    r = process_pair(dl, raw_key, raw_val, vlen, out);
    if (r != PEL_OK) {
        return r;
    }
    if (*pos < len && text[*pos] == ':') {
        (*pos)++;
    }
    return PEL_OK;
}

pel_result pel_encode_options_parse(pel_encode_options_dialect d, const char *text, size_t len,
                                    PelorusEncodeOptions *out)
{
    size_t pos = 0;
    const PelOptDialect *dl;

    if (!out || !text) {
        return PEL_ERR_INVALID;
    }
    out->nb_params = 0;
    out->nb_unknown_params = 0;
    out->arena_used = 0;

    dl = dialect_rules(d);
    if (!dl) {
        return PEL_ERR_INVALID;
    }

    if (len == 0 || text[0] == '\0') {
        return PEL_OK;
    }

    while (pos < len && text[pos] != '\0') {
        pel_result r = parse_one_pair(dl, text, len, &pos, out);
        if (r != PEL_OK) {
            out->nb_params = 0;
            out->nb_unknown_params = 0;
            out->arena_used = 0;
            return r;
        }
    }

    sort_params(out->params, out->param_val_ptrs, out->nb_params);
    sort_params(out->unknown_params, out->unknown_val_ptrs, out->nb_unknown_params);
    return PEL_OK;
}
