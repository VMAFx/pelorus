/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * encode_record.c — canonical JSON, digest and verification of the encode
 * provenance record (ADR-0175, docs/api/encode-record.md).
 *
 * Canonical form: RFC 8785 restricted so that Python's
 * json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) gives
 * the same bytes: every scalar is a string, keys are printable ASCII (byte order
 * == UTF-16 order), strings escape only '"', '\\' and control characters.
 *
 * No recursion (HISS-01) and no allocation: the canonicaliser walks the input
 * with an explicit stack of PEL_ENCODE_RECORD_MAX_DEPTH + 1 frames, each holding
 * the sorted member index of one open container (about 15 KiB of stack in
 * total). Output goes through a sink that writes a caller buffer, feeds the
 * private SHA-256, or both, so hashing never materialises the text. Every loop
 * is bounded by the input length or by a PEL_ENCODE_RECORD_MAX_* constant
 * (HISS-02).
 */

#include "pelorus/encode_record.h"

#include "sha256.h"

#include <math.h>
#include <string.h>

/* NOLINTBEGIN(modernize-use-nullptr): C translation unit kept buildable by MSVC's
 * C mode, which has no `nullptr` (same decision as interop.c, VMAFx ADR-1138). */

#define ER_BAD ((size_t)-1)
#define ER_MAX_FRAMES (PEL_ENCODE_RECORD_MAX_DEPTH + 1u)
#define ER_DIGEST_TEXT_LEN 71u /* "sha256:" + 64 hex digits */

/* ---- Output sink ------------------------------------------------------- */

typedef struct ErSink {
    char *buf;      /* NULL: no text output (hash pass)                    */
    size_t cap;     /* bytes of buf, NUL included                          */
    size_t len;     /* bytes emitted; counted on past cap, never past MAX  */
    PelSha256 *sha; /* NULL: no hashing                                    */
    int mute;       /* an excluded root member: validate it, emit nothing  */
    pel_result err; /* first validation error, sticky                      */
} ErSink;

static void er_sink_init(ErSink *s, char *buf, size_t cap, PelSha256 *sha)
{
    s->buf = buf;
    s->cap = cap;
    s->len = 0;
    s->sha = sha;
    s->mute = 0;
    s->err = PEL_OK;
}

/* Record the first validation error; later output is dropped. */
static void er_fail(ErSink *s)
{
    if (s->err == PEL_OK) {
        s->err = PEL_ERR_INVALID;
    }
}

static void er_put(ErSink *s, const char *p, size_t n)
{
    if (s->mute || s->err != PEL_OK) {
        return;
    }
    if (n > PEL_ENCODE_RECORD_MAX_BYTES - s->len) {
        s->err = PEL_ERR_INVALID; /* record bound: rejected, never truncated */
        return;
    }
    /* Write only while the text and its NUL still fit; len keeps counting, so the
     * caller learns the size it needs. */
    if (s->buf != NULL && s->cap > s->len && n < s->cap - s->len) {
        memcpy(s->buf + s->len, p, n);
    }
    if (s->sha != NULL && pel_sha256_update(s->sha, p, n) != PEL_OK) {
        s->err = PEL_ERR_INVALID; /* unreachable: p is never NULL for n > 0 */
        return;
    }
    s->len += n;
}

/* Close the output: sticky error, else the length, else RANGE for a short buffer. */
static pel_result er_finish(ErSink *s, size_t *out_len)
{
    if (s->err != PEL_OK) {
        return s->err;
    }
    *out_len = s->len;
    if (s->buf == NULL) {
        return PEL_OK;
    }
    if (s->len >= s->cap) {
        return PEL_ERR_RANGE;
    }
    s->buf[s->len] = '\0';
    return PEL_OK;
}

/* ---- UTF-8, escapes, scalars ------------------------------------------- */

static size_t er_strnlen(const char *s, size_t max)
{
    size_t n = 0;

    while (n < max && s[n] != '\0') {
        n++;
    }
    return n;
}

/* Lead byte of a multi-byte sequence: continuation count and the allowed range of the
 * second byte (RFC 3629: no overlong form, no surrogate, nothing above U+10FFFF). */
static int er_utf8_lead(uint8_t c, size_t *n, uint32_t *v, uint8_t *lo, uint8_t *hi)
{
    *lo = 0x80u;
    *hi = 0xBFu;
    if (c >= 0xC2u && c <= 0xDFu) {
        *n = 1;
        *v = c & 0x1Fu;
    } else if (c >= 0xE0u && c <= 0xEFu) {
        *n = 2;
        *v = c & 0x0Fu;
        *lo = c == 0xE0u ? 0xA0u : 0x80u;
        *hi = c == 0xEDu ? 0x9Fu : 0xBFu;
    } else if (c >= 0xF0u && c <= 0xF4u) {
        *n = 3;
        *v = c & 0x07u;
        *lo = c == 0xF0u ? 0x90u : 0x80u;
        *hi = c == 0xF4u ? 0x8Fu : 0xBFu;
    } else {
        return 0;
    }
    return 1;
}

/* Decode one well-formed UTF-8 sequence at p[*i] and advance *i; 0 when ill-formed. */
static int er_utf8(const uint8_t *p, size_t len, size_t *i, uint32_t *cp)
{
    const uint8_t c = p[*i];
    uint8_t lo = 0;
    uint8_t hi = 0;
    uint32_t v = 0;
    size_t n = 0;
    size_t k;

    if (c < 0x80u) {
        *cp = c;
        *i += 1u;
        return 1;
    }
    if (!er_utf8_lead(c, &n, &v, &lo, &hi) || n >= len - *i) {
        return 0;
    }
    for (k = 1; k <= n; k++) {
        const uint8_t b = p[*i + k];
        if (b < (k == 1u ? lo : 0x80u) || b > (k == 1u ? hi : 0xBFu)) {
            return 0;
        }
        v = (v << 6u) | (b & 0x3Fu);
    }
    *cp = v;
    *i += n + 1u;
    return 1;
}

static size_t er_utf8_encode(uint32_t cp, uint8_t out[4])
{
    if (cp < 0x80u) {
        out[0] = (uint8_t)cp;
        return 1;
    }
    if (cp < 0x800u) {
        out[0] = (uint8_t)(0xC0u | (cp >> 6u));
        out[1] = (uint8_t)(0x80u | (cp & 0x3Fu));
        return 2;
    }
    if (cp < 0x10000u) {
        out[0] = (uint8_t)(0xE0u | (cp >> 12u));
        out[1] = (uint8_t)(0x80u | ((cp >> 6u) & 0x3Fu));
        out[2] = (uint8_t)(0x80u | (cp & 0x3Fu));
        return 3;
    }
    out[0] = (uint8_t)(0xF0u | (cp >> 18u));
    out[1] = (uint8_t)(0x80u | ((cp >> 12u) & 0x3Fu));
    out[2] = (uint8_t)(0x80u | ((cp >> 6u) & 0x3Fu));
    out[3] = (uint8_t)(0x80u | (cp & 0x3Fu));
    return 4;
}

/* Emit one code point as canonical string content: short escapes for '"', '\\' and
 * \b \t \n \f \r, lower-case \u00xx for the other controls, UTF-8 for the rest. */
static void er_put_cp(ErSink *s, uint32_t cp)
{
    static const char hex_digits[] = "0123456789abcdef";
    static const char short_from[] = "\"\\\b\t\n\f\r";
    static const char short_to[] = "\"\\btnfr";
    const char *hit =
        cp < 0x80u && cp != 0u ? memchr(short_from, (int)cp, sizeof(short_from) - 1u) : NULL;
    char esc[6];
    uint8_t enc[4];
    size_t n;

    if (hit != NULL) {
        esc[0] = '\\';
        esc[1] = short_to[hit - short_from];
        er_put(s, esc, 2);
        return;
    }
    if (cp < 0x20u) {
        esc[0] = '\\';
        esc[1] = 'u';
        esc[2] = '0';
        esc[3] = '0';
        esc[4] = hex_digits[cp >> 4u];
        esc[5] = hex_digits[cp & 0x0Fu];
        er_put(s, esc, sizeof(esc));
        return;
    }
    n = er_utf8_encode(cp, enc);
    er_put(s, (const char *)enc, n);
}

static int er_hex4(const uint8_t *p, size_t len, size_t at, uint32_t *out)
{
    uint32_t v = 0;
    size_t k;

    if (at > len || len - at < 4u) {
        return 0;
    }
    for (k = 0; k < 4u; k++) {
        const uint8_t c = p[at + k];
        uint32_t d;
        if (c >= '0' && c <= '9') {
            d = (uint32_t)(c - '0');
        } else if (c >= 'a' && c <= 'f') {
            d = (uint32_t)(c - 'a') + 10u;
        } else if (c >= 'A' && c <= 'F') {
            d = (uint32_t)(c - 'A') + 10u;
        } else {
            return 0;
        }
        v = (v << 4u) | d;
    }
    *out = v;
    return 1;
}

/* Decode the escape at p[*i] == '\\' and advance *i; 0 when malformed. A \u surrogate
 * must form a pair: a lone one has no UTF-8 form and Python could not encode it. */
static int er_unescape(const uint8_t *p, size_t len, size_t *i, uint32_t *cp)
{
    static const char from[] = "\"\\/bfnrt";
    static const char to[] = "\"\\/\b\f\n\r\t";
    const char *hit = *i + 1u < len ? memchr(from, p[*i + 1u], sizeof(from) - 1u) : NULL;
    uint32_t hi = 0;
    uint32_t lo = 0;

    if (hit != NULL) {
        *cp = (uint8_t)to[hit - from];
        *i += 2u;
        return 1;
    }
    if (*i + 1u >= len || p[*i + 1u] != 'u' || !er_hex4(p, len, *i + 2u, &hi)) {
        return 0;
    }
    *i += 6u;
    if (hi < 0xD800u || hi > 0xDFFFu) {
        *cp = hi;
        return 1;
    }
    if (hi > 0xDBFFu || *i + 1u >= len || p[*i] != '\\' || p[*i + 1u] != 'u' ||
        !er_hex4(p, len, *i + 2u, &lo) || lo < 0xDC00u || lo > 0xDFFFu) {
        return 0;
    }
    *i += 6u;
    *cp = 0x10000u + ((hi - 0xD800u) << 10u) + (lo - 0xDC00u);
    return 1;
}

/* Emit a NUL-terminated UTF-8 C string as a canonical JSON string. */
static void er_emit_cstr(ErSink *s, const char *str)
{
    const uint8_t *p = (const uint8_t *)str;
    size_t len;
    size_t i = 0;
    size_t step;
    uint32_t cp = 0;

    if (s->err != PEL_OK) {
        return;
    }
    len = str == NULL ? 0u : er_strnlen(str, PEL_ENCODE_RECORD_MAX_VALUE + 1u);
    if (str == NULL || len > PEL_ENCODE_RECORD_MAX_VALUE) {
        er_fail(s);
        return;
    }
    er_put(s, "\"", 1);
    for (step = 0; step < len && i < len; step++) {
        if (!er_utf8(p, len, &i, &cp)) {
            er_fail(s);
            return;
        }
        er_put_cp(s, cp);
    }
    er_put(s, "\"", 1);
}

static int er_key_char_ok(uint8_t c)
{
    return c >= 0x21u && c <= 0x7Eu && c != '"' && c != '\\';
}

/* A caller key: 1 to MAX_KEY bytes of printable ASCII without '"' and '\\'. */
static int er_cstr_key_ok(const char *key)
{
    size_t n;
    size_t i;

    if (key == NULL) {
        return 0;
    }
    n = er_strnlen(key, PEL_ENCODE_RECORD_MAX_KEY + 1u);
    if (n == 0u || n > PEL_ENCODE_RECORD_MAX_KEY) {
        return 0; /* an empty key names nothing */
    }
    for (i = 0; i < n; i++) {
        if (!er_key_char_ok((uint8_t)key[i])) {
            return 0;
        }
    }
    return 1;
}

/* Decimal text of an unsigned value; returns the digit count (<= 20). */
static size_t er_u64_text(uint64_t v, char *out)
{
    char tmp[20];
    size_t n = 0;
    size_t i;

    do {
        tmp[n++] = (char)('0' + (int)(v % 10u));
        v /= 10u;
    } while (v != 0u && n < sizeof(tmp));
    for (i = 0; i < n; i++) {
        out[i] = tmp[n - 1u - i];
    }
    out[n] = '\0';
    return n;
}

pel_result pel_encode_record_int(int64_t v, char out[PEL_ENCODE_RECORD_SCALAR_SIZE])
{
    if (out == NULL) {
        return PEL_ERR_INVALID;
    }
    if (v < 0) {
        out[0] = '-';
        /* -(v + 1) + 1 is the magnitude without overflowing INT64_MIN. */
        (void)er_u64_text((uint64_t)(-(v + 1)) + 1u, out + 1);
    } else {
        (void)er_u64_text((uint64_t)v, out);
    }
    return PEL_OK;
}

pel_result pel_encode_record_f64(double v, char out[PEL_ENCODE_RECORD_SCALAR_SIZE])
{
    static const char hex_digits[] = "0123456789abcdef";
    uint64_t bits = 0;
    unsigned i;

    if (out == NULL) {
        return PEL_ERR_INVALID;
    }
    if (!isfinite(v)) {
        return PEL_ERR_RANGE;
    }
    memcpy(&bits, &v, sizeof(bits));
    if (bits == (UINT64_C(1) << 63u)) {
        bits = 0; /* -0.0 is written as +0.0 */
    }
    memcpy(out, "f64:", 4);
    for (i = 0; i < 16u; i++) {
        out[4u + i] = hex_digits[(bits >> (60u - 4u * i)) & 0x0Fu];
    }
    out[20] = '\0';
    return PEL_OK;
}

/* ---- Canonicaliser: index, sort, walk ---------------------------------- */

/* One entry of an open container: its key (objects) and where its value starts. */
typedef struct ErItem {
    uint32_t key_off;
    uint32_t key_len;
    uint32_t val_off;
} ErItem;

typedef struct ErFrame {
    ErItem items[PEL_ENCODE_RECORD_MAX_MEMBERS];
    uint32_t count;   /* entries                          */
    uint32_t next;    /* next entry to emit               */
    uint32_t emitted; /* entries written (commas)         */
    uint8_t is_object;
} ErFrame;

typedef struct ErWalk {
    const char *p;
    size_t len;
    ErSink *sink;
    int exclude;         /* drop the root's digest and elapsed_ns */
    unsigned depth;      /* frames in use                          */
    unsigned mute_depth; /* depth at which muting ends; 0 = none   */
    ErFrame frames[ER_MAX_FRAMES];
} ErWalk;

static size_t er_ws(const char *p, size_t len, size_t pos)
{
    while (pos < len && (p[pos] == ' ' || p[pos] == '\t' || p[pos] == '\n' || p[pos] == '\r')) {
        pos++;
    }
    return pos;
}

/* Position after the string that opens at p[pos] == '"', or ER_BAD. */
static size_t er_skip_string(const char *p, size_t len, size_t pos)
{
    int esc = 0;
    size_t i;

    for (i = pos + 1u; i < len; i++) {
        if (esc) {
            esc = 0;
        } else if (p[i] == '\\') {
            esc = 1;
        } else if (p[i] == '"') {
            return i + 1u;
        }
    }
    return ER_BAD;
}

/* Position after the value at p[pos]: a string or a bracket-balanced container. A
 * non-string scalar is ER_BAD. The container's inside is checked strictly when it is
 * indexed for emission; this only finds its end. Every step advances i, so at most len
 * steps run. */
static size_t er_skip_value(const char *p, size_t len, size_t pos)
{
    size_t depth = 0;
    size_t i = pos;
    size_t step;

    if (pos >= len || (p[pos] != '"' && p[pos] != '{' && p[pos] != '[')) {
        return ER_BAD;
    }
    for (step = 0; step < len && i < len; step++) {
        if (p[i] == '"') {
            i = er_skip_string(p, len, i);
            if (i == ER_BAD) {
                return ER_BAD;
            }
        } else {
            if (p[i] == '{' || p[i] == '[') {
                depth++;
            } else if ((p[i] == '}' || p[i] == ']') && depth > 0u) {
                depth--;
            }
            i++;
        }
        if (depth == 0u) {
            return i; /* the string, or the bracket that closed the container */
        }
    }
    return ER_BAD;
}

/* Parse `"key"` ws ':' ws at *i into `it`; 0 when the key breaks the key rule (empty,
 * longer than MAX_KEY, or outside printable ASCII without '"' and '\\'). */
static int er_parse_key(const char *p, size_t len, size_t *i, ErItem *it)
{
    const size_t k = *i;
    size_t n;
    size_t j;

    if (k >= len || p[k] != '"') {
        return 0;
    }
    for (n = 0; n <= PEL_ENCODE_RECORD_MAX_KEY && k + 1u + n < len; n++) {
        const uint8_t c = (uint8_t)p[k + 1u + n];
        if (c == '"') {
            break;
        }
        if (!er_key_char_ok(c)) {
            return 0;
        }
    }
    if (n == 0u || n > PEL_ENCODE_RECORD_MAX_KEY || k + 1u + n >= len) {
        return 0;
    }
    it->key_off = (uint32_t)(k + 1u);
    it->key_len = (uint32_t)n;
    j = er_ws(p, len, k + 2u + n);
    if (j >= len || p[j] != ':') {
        return 0;
    }
    *i = er_ws(p, len, j + 1u);
    return 1;
}

static int er_key_cmp(const char *p, const ErItem *a, const ErItem *b)
{
    const uint32_t n = a->key_len < b->key_len ? a->key_len : b->key_len;
    const int c = memcmp(p + a->key_off, p + b->key_off, n);

    if (c != 0) {
        return c;
    }
    return (a->key_len > b->key_len) - (a->key_len < b->key_len);
}

/* Sort the members by key bytes (insertion sort, at most MAX_MEMBERS entries); equal
 * neighbours are duplicate keys. */
static pel_result er_sort(const char *p, ErFrame *f)
{
    uint32_t i;
    uint32_t j;

    for (i = 1; i < f->count; i++) {
        const ErItem cur = f->items[i];
        for (j = i; j > 0u && er_key_cmp(p, &f->items[j - 1u], &cur) > 0; j--) {
            f->items[j] = f->items[j - 1u];
        }
        f->items[j] = cur;
    }
    for (i = 1; i < f->count; i++) {
        if (er_key_cmp(p, &f->items[i - 1u], &f->items[i]) == 0) {
            return PEL_ERR_INVALID;
        }
    }
    return PEL_OK;
}

/* Index the container that opens at p[pos]: every entry's key and value start, in input
 * order for arrays and key order for objects. */
static pel_result er_index(const char *p, size_t len, size_t pos, ErFrame *f)
{
    const int obj = p[pos] == '{';
    const char close = obj ? '}' : ']';
    const uint32_t max = obj ? PEL_ENCODE_RECORD_MAX_MEMBERS : PEL_ENCODE_RECORD_MAX_ARRAY;
    size_t i = er_ws(p, len, pos + 1u);
    uint32_t n;

    f->count = 0;
    f->next = 0;
    f->emitted = 0;
    f->is_object = (uint8_t)obj;
    if (i < len && p[i] == close) {
        return PEL_OK;
    }
    for (n = 0; n < max; n++) {
        ErItem *it = &f->items[n];
        it->key_off = 0;
        it->key_len = 0;
        if (obj && !er_parse_key(p, len, &i, it)) {
            return PEL_ERR_INVALID;
        }
        it->val_off = (uint32_t)i;
        i = er_skip_value(p, len, i);
        if (i == ER_BAD) {
            return PEL_ERR_INVALID;
        }
        i = er_ws(p, len, i);
        f->count = n + 1u;
        if (i < len && p[i] == close) {
            return obj ? er_sort(p, f) : PEL_OK;
        }
        if (i >= len || p[i] != ',') {
            return PEL_ERR_INVALID;
        }
        i = er_ws(p, len, i + 1u);
    }
    return PEL_ERR_INVALID; /* more entries than the bound */
}

/* Emit the JSON string at p[pos] in canonical form; its decoded UTF-8 is at most
 * MAX_VALUE bytes. */
static pel_result er_emit_json_string(ErWalk *w, size_t pos)
{
    const uint8_t *p = (const uint8_t *)w->p;
    size_t i = pos + 1u;
    size_t bytes = 0;
    size_t step;
    uint32_t cp = 0;
    uint8_t enc[4];

    er_put(w->sink, "\"", 1);
    for (step = 0; step < w->len && i < w->len; step++) {
        int ok;
        if (p[i] == '"') {
            er_put(w->sink, "\"", 1);
            return PEL_OK;
        }
        if (p[i] == '\\') {
            ok = er_unescape(p, w->len, &i, &cp);
        } else {
            ok = p[i] >= 0x20u && er_utf8(p, w->len, &i, &cp);
        }
        bytes += ok ? er_utf8_encode(cp, enc) : 0u;
        if (!ok || bytes > PEL_ENCODE_RECORD_MAX_VALUE) {
            return PEL_ERR_INVALID;
        }
        er_put_cp(w->sink, cp);
    }
    return PEL_ERR_INVALID;
}

/* Emit a value: a string now, or open a container (push its sorted index). */
static pel_result er_value(ErWalk *w, size_t pos)
{
    const char c = w->p[pos];
    pel_result rc;

    if (c == '"') {
        return er_emit_json_string(w, pos);
    }
    if ((c != '{' && c != '[') || w->depth >= ER_MAX_FRAMES) {
        return PEL_ERR_INVALID; /* a non-string scalar, or nested too deep */
    }
    rc = er_index(w->p, w->len, pos, &w->frames[w->depth]);
    if (rc != PEL_OK) {
        return rc;
    }
    er_put(w->sink, c == '{' ? "{" : "[", 1);
    w->depth++;
    return PEL_OK;
}

static int er_key_is(const char *p, const ErItem *it, const char *name)
{
    const size_t n = strlen(name);

    return it->key_len == n && memcmp(p + it->key_off, name, n) == 0;
}

/* One walk step: close the top container, or emit its next entry. */
static pel_result er_step(ErWalk *w)
{
    ErFrame *f = &w->frames[w->depth - 1u];
    const ErItem *it;

    if (w->mute_depth != 0u && w->mute_depth == w->depth) {
        w->mute_depth = 0; /* back at the root: the excluded member is behind us */
        w->sink->mute = 0;
    }
    if (f->next == f->count) {
        er_put(w->sink, f->is_object ? "}" : "]", 1);
        w->depth--;
        return PEL_OK;
    }
    it = &f->items[f->next++];
    if (w->exclude && w->depth == 1u &&
        (er_key_is(w->p, it, "digest") || er_key_is(w->p, it, "elapsed_ns"))) {
        w->sink->mute = 1; /* still parsed and validated, never written or hashed */
        w->mute_depth = 1;
    } else {
        if (f->emitted++ > 0u) {
            er_put(w->sink, ",", 1);
        }
        if (f->is_object) {
            er_put(w->sink, "\"", 1);
            er_put(w->sink, w->p + it->key_off, it->key_len);
            er_put(w->sink, "\":", 2);
        }
    }
    return er_value(w, it->val_off);
}

static pel_result er_walk(const char *json, size_t len, ErSink *sink, int exclude)
{
    ErWalk w;
    size_t root;
    size_t end;
    size_t step;
    pel_result rc;

    if (len > PEL_ENCODE_RECORD_MAX_BYTES) {
        return PEL_ERR_INVALID;
    }
    root = er_ws(json, len, 0);
    end = er_skip_value(json, len, root);
    if (root >= len || json[root] != '{' || end == ER_BAD || er_ws(json, len, end) != len) {
        return PEL_ERR_INVALID; /* the root is one object with nothing after it */
    }
    w.p = json;
    w.len = len;
    w.sink = sink;
    w.exclude = exclude;
    w.depth = 0;
    w.mute_depth = 0;
    rc = er_value(&w, root);
    /* Each step writes one entry or closes one container, and each spans at least one
     * input byte, so 2 * len + 2 steps bound the walk. */
    for (step = 0; rc == PEL_OK && w.depth > 0u && step <= 2u * len + 2u; step++) {
        rc = er_step(&w);
    }
    if (rc == PEL_OK && w.depth != 0u) {
        rc = PEL_ERR_INVALID;
    }
    return rc == PEL_OK ? sink->err : rc;
}

pel_result pel_encode_record_canonicalize(const char *json, size_t len, char *buf, size_t cap,
                                          size_t *out_len)
{
    ErSink s;
    pel_result rc;

    if (out_len == NULL) {
        return PEL_ERR_INVALID;
    }
    *out_len = 0;
    if (json == NULL || (buf == NULL && cap > 0u)) {
        return PEL_ERR_INVALID;
    }
    er_sink_init(&s, buf, cap, NULL);
    rc = er_walk(json, len, &s, 0);
    if (rc == PEL_OK) {
        rc = er_finish(&s, out_len);
    }
    /* buf NULL with cap 0 is a size query: the length is in *out_len. */
    return (rc == PEL_OK && buf == NULL) ? PEL_ERR_RANGE : rc;
}

pel_result pel_encode_record_hash(const char *json, size_t len,
                                  uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE])
{
    PelSha256 sha;
    ErSink s;
    size_t n = 0;
    pel_result rc;

    if (json == NULL || digest == NULL) {
        return PEL_ERR_INVALID;
    }
    pel_sha256_init(&sha);
    er_sink_init(&s, NULL, 0, &sha);
    rc = er_walk(json, len, &s, 1);
    if (rc == PEL_OK) {
        rc = er_finish(&s, &n);
    }
    if (rc == PEL_OK) {
        pel_sha256_final(&sha, digest);
    }
    return rc;
}

/* The 71-character text of a raw digest, through the mirrored interop.c formatter. */
static pel_result er_digest_text(const uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE],
                                 char text[PEL_DIGEST_TEXT_SIZE])
{
    PelorusEncodeRecordSection sec;

    memset(&sec, 0, sizeof(sec));
    memcpy(sec.digest, digest, sizeof(sec.digest));
    sec.digest_alg = (uint8_t)PEL_DIGEST_ALG_SHA256;
    return pel_encode_record_digest_text(&sec, sizeof(sec), text, PEL_DIGEST_TEXT_SIZE);
}

/* The raw text of the root's `digest` string (the record is already validated). */
static pel_result er_root_digest(const char *p, size_t len, const char **val, size_t *val_len)
{
    ErFrame f;
    uint32_t i;
    pel_result rc = er_index(p, len, er_ws(p, len, 0), &f);

    for (i = 0; rc == PEL_OK && i < f.count; i++) {
        const ErItem *it = &f.items[i];
        if (er_key_is(p, it, "digest")) {
            const size_t end = er_skip_string(p, len, it->val_off);
            if (p[it->val_off] != '"' || end == ER_BAD) {
                return PEL_ERR_MISMATCH; /* not a digest string at all */
            }
            *val = p + it->val_off + 1u;
            *val_len = end - it->val_off - 2u;
            return PEL_OK;
        }
    }
    return rc == PEL_OK ? PEL_ERR_ABSENT : rc;
}

pel_result pel_encode_record_verify(const char *json, size_t len,
                                    const uint8_t expected[PEL_ENCODE_RECORD_DIGEST_SIZE])
{
    uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE];
    char text[PEL_DIGEST_TEXT_SIZE];
    const char *val = NULL;
    size_t val_len = 0;
    pel_result rc = pel_encode_record_hash(json, len, digest);

    if (rc != PEL_OK) {
        return rc;
    }
    if (expected != NULL && memcmp(expected, digest, sizeof(digest)) != 0) {
        return PEL_ERR_MISMATCH;
    }
    rc = er_root_digest(json, len, &val, &val_len);
    if (rc == PEL_ERR_ABSENT) {
        return expected != NULL ? PEL_OK : PEL_ERR_ABSENT;
    }
    if (rc == PEL_OK) {
        rc = er_digest_text(digest, text);
    }
    if (rc != PEL_OK) {
        return rc;
    }
    return (val_len == ER_DIGEST_TEXT_LEN && memcmp(val, text, ER_DIGEST_TEXT_LEN) == 0) ?
               PEL_OK :
               PEL_ERR_MISMATCH;
}

/* ---- Builder: canonical text straight from the input struct ------------- */

/* Write `,"key":` (no comma before the first member). `key` is a literal or a key that
 * er_cstr_key_ok accepted, so strlen is bounded. */
static void er_member(ErSink *s, const char *key, int *first)
{
    if (!*first) {
        er_put(s, ",", 1);
    }
    *first = 0;
    er_put(s, "\"", 1);
    er_put(s, key, strlen(key));
    er_put(s, "\":", 2);
}

static void er_emit_required(ErSink *s, const char *str)
{
    if (str == NULL || str[0] == '\0') {
        er_fail(s);
        return;
    }
    er_emit_cstr(s, str);
}

static int er_param_ok(const PelorusEncodeParam *pm)
{
    uint32_t i;

    if (!er_cstr_key_ok(pm->key) || pm->values == NULL || pm->nb_values == 0u ||
        pm->nb_values > (pm->is_array ? PEL_ENCODE_RECORD_MAX_ARRAY : 1u)) {
        return 0;
    }
    for (i = 0; i < pm->nb_values; i++) {
        if (pm->values[i] == NULL) {
            return 0;
        }
    }
    return 1;
}

static void er_build_param_value(ErSink *s, const PelorusEncodeParam *pm)
{
    uint32_t i;

    if (!pm->is_array) {
        er_emit_cstr(s, pm->values[0]);
        return;
    }
    er_put(s, "[", 1);
    for (i = 0; i < pm->nb_values; i++) {
        if (i > 0u) {
            er_put(s, ",", 1);
        }
        er_emit_cstr(s, pm->values[i]);
    }
    er_put(s, "]", 1);
}

/* A params object: validated, sorted by key bytes, duplicate keys rejected. */
static void er_build_params(ErSink *s, const PelorusEncodeParam *params, uint32_t nb)
{
    uint16_t order[PEL_ENCODE_RECORD_MAX_MEMBERS];
    uint32_t i;
    uint32_t j;
    int first = 1;

    if (nb > PEL_ENCODE_RECORD_MAX_MEMBERS || (nb > 0u && params == NULL)) {
        er_fail(s);
        return;
    }
    for (i = 0; i < nb; i++) {
        if (!er_param_ok(&params[i])) {
            er_fail(s);
            return;
        }
        for (j = i; j > 0u && strcmp(params[order[j - 1u]].key, params[i].key) > 0; j--) {
            order[j] = order[j - 1u];
        }
        order[j] = (uint16_t)i;
    }
    for (i = 1; i < nb; i++) {
        if (strcmp(params[order[i - 1u]].key, params[order[i]].key) == 0) {
            er_fail(s);
            return;
        }
    }
    er_put(s, "{", 1);
    for (i = 0; i < nb && s->err == PEL_OK; i++) {
        er_member(s, params[order[i]].key, &first);
        er_build_param_value(s, &params[order[i]]);
    }
    er_put(s, "}", 1);
}

static void er_build_encoder(ErSink *s, const PelorusEncodeRecordInput *in)
{
    int first = 1;

    er_put(s, "{", 1);
    er_member(s, "codec", &first);
    er_emit_required(s, in->encoder_codec);
    er_member(s, "name", &first);
    er_emit_required(s, in->encoder_name);
    er_member(s, "params", &first);
    er_build_params(s, in->encoder_params, in->nb_encoder_params);
    er_member(s, "unknown_params", &first);
    er_build_params(s, in->encoder_unknown_params, in->nb_encoder_unknown_params);
    er_member(s, "version", &first);
    er_emit_required(s, in->encoder_version);
    er_put(s, "}", 1);
}

static void er_build_filters(ErSink *s, const PelorusEncodeFilter *filters, uint32_t nb)
{
    uint32_t i;

    if (nb > PEL_ENCODE_RECORD_MAX_ARRAY || (nb > 0u && filters == NULL)) {
        er_fail(s);
        return;
    }
    er_put(s, "[", 1);
    for (i = 0; i < nb && s->err == PEL_OK; i++) {
        const PelorusEncodeFilter *f = &filters[i];
        int first = 1;
        er_put(s, i > 0u ? ",{" : "{", i > 0u ? 2u : 1u);
        er_member(s, "library", &first);
        er_emit_required(s, f->library);
        er_member(s, "name", &first);
        er_emit_required(s, f->name);
        er_member(s, "params", &first);
        er_build_params(s, f->params, f->nb_params);
        er_member(s, "unknown_params", &first);
        er_build_params(s, f->unknown_params, f->nb_unknown_params);
        er_member(s, "version", &first);
        er_emit_required(s, f->version);
        er_put(s, "}", 1);
    }
    er_put(s, "]", 1);
}

static int er_hw_path_ok(const char *path)
{
    static const char *const names[] = {"cpu", "vulkan", "nvenc",       "qsv",
                                        "amf", "vaapi",  "videotoolbox"};
    size_t i;

    for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        if (strcmp(path, names[i]) == 0) {
            return 1;
        }
    }
    return 0;
}

static void er_build_hardware(ErSink *s, const PelorusEncodeRecordInput *in)
{
    int first = 1;

    if (in->hardware_path != NULL && !er_hw_path_ok(in->hardware_path)) {
        er_fail(s);
        return;
    }
    er_put(s, "{", 1);
    if (in->hardware_device != NULL) {
        er_member(s, "device", &first);
        er_emit_cstr(s, in->hardware_device);
    }
    if (in->hardware_driver != NULL) {
        er_member(s, "driver", &first);
        er_emit_cstr(s, in->hardware_driver);
    }
    if (in->hardware_path != NULL) {
        er_member(s, "path", &first);
        er_emit_cstr(s, in->hardware_path);
    }
    er_put(s, "}", 1);
}

/* "sha256:" followed by exactly 64 lower-case hex digits. */
static int er_digest_format_ok(const char *d)
{
    size_t i;

    if (strncmp(d, "sha256:", 7) != 0) {
        return 0;
    }
    for (i = 7; i < ER_DIGEST_TEXT_LEN; i++) {
        if (!((d[i] >= '0' && d[i] <= '9') || (d[i] >= 'a' && d[i] <= 'f'))) {
            return 0; /* also stops at an early NUL */
        }
    }
    return d[ER_DIGEST_TEXT_LEN] == '\0';
}

static void er_build_input(ErSink *s, const PelorusEncodeRecordInput *in)
{
    int first = 1;

    if ((in->input_digest == NULL && in->input_id == NULL) ||
        (in->input_digest != NULL && !er_digest_format_ok(in->input_digest))) {
        er_fail(s);
        return;
    }
    er_put(s, "{", 1);
    if (in->input_digest != NULL) {
        er_member(s, "digest", &first);
        er_emit_cstr(s, in->input_digest);
    }
    if (in->input_id != NULL) {
        er_member(s, "id", &first);
        er_emit_cstr(s, in->input_id);
    }
    er_put(s, "}", 1);
}

/* The record in key order. With digest_text NULL this is the digested text: no
 * `digest`, no `elapsed_ns`. */
static void er_build_root(ErSink *s, const PelorusEncodeRecordInput *in, const char *digest_text)
{
    char num[PEL_ENCODE_RECORD_SCALAR_SIZE];
    int first = 1;

    er_put(s, "{", 1);
    if (digest_text != NULL) {
        er_member(s, "digest", &first);
        er_emit_cstr(s, digest_text);
        if (in->has_elapsed_ns) {
            (void)er_u64_text(in->elapsed_ns, num);
            er_member(s, "elapsed_ns", &first);
            er_emit_cstr(s, num);
        }
    }
    er_member(s, "encoder", &first);
    er_build_encoder(s, in);
    er_member(s, "filters", &first);
    er_build_filters(s, in->filters, in->nb_filters);
    if (in->hardware_path != NULL || in->hardware_device != NULL || in->hardware_driver != NULL) {
        er_member(s, "hardware", &first);
        er_build_hardware(s, in);
    }
    er_member(s, "input", &first);
    er_build_input(s, in);
    er_member(s, "schema", &first);
    er_emit_cstr(s, PEL_ENCODE_RECORD_SCHEMA);
    er_put(s, "}", 1);
}

pel_result pel_encode_record_build(const PelorusEncodeRecordInput *in, char *buf, size_t cap,
                                   size_t *out_len)
{
    PelSha256 sha;
    uint8_t digest[PEL_ENCODE_RECORD_DIGEST_SIZE];
    char text[PEL_DIGEST_TEXT_SIZE];
    ErSink s;
    size_t n = 0;
    pel_result rc;

    if (out_len == NULL) {
        return PEL_ERR_INVALID;
    }
    *out_len = 0;
    if (in == NULL || (buf == NULL && cap > 0u)) {
        return PEL_ERR_INVALID;
    }
    /* Pass 1 hashes the text without digest and elapsed_ns; pass 2 writes the record. */
    pel_sha256_init(&sha);
    er_sink_init(&s, NULL, 0, &sha);
    er_build_root(&s, in, NULL);
    rc = er_finish(&s, &n);
    if (rc != PEL_OK) {
        return rc;
    }
    pel_sha256_final(&sha, digest);
    rc = er_digest_text(digest, text);
    if (rc != PEL_OK) {
        return rc;
    }
    er_sink_init(&s, buf, cap, NULL);
    er_build_root(&s, in, text);
    rc = er_finish(&s, out_len);
    /* buf NULL with cap 0 is a size query: the length is in *out_len. */
    return (rc == PEL_OK && buf == NULL) ? PEL_ERR_RANGE : rc;
}

/* NOLINTEND(modernize-use-nullptr) */
