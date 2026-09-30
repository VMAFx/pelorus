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
 * path_utf8_test.c — the UTF-8 path contract of pel_x265_csv_parse (ADR-0149).
 *
 * Every platform: an ASCII and a non-ASCII (Latin-1, CJK, astral emoji)
 * directory + file name round trip through the library, a missing file, and the
 * degenerate empty / over-long / NULL paths.
 *
 * Windows: the non-ASCII fixture is created with _wopen from an independent
 * UTF-16 spelling, so the round trip proves the library's UTF-8 -> UTF-16 open
 * without reusing it; ill-formed UTF-8 must be PEL_ERR_INVALID even when its
 * ANSI-code-page reading names an existing file; a \\?\ path past MAX_PATH opens.
 * POSIX: the fixture is created from the UTF-8 bytes (the literal pass-through)
 * and a non-UTF-8 byte name still opens, exactly as before ADR-0149.
 *
 * Everything lives in a private per-process directory in the working directory,
 * created exclusively, and is removed again. Exit non-zero on any failure.
 */

#include "pelorus/interop.h"
#include "pelorus/pelorus.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <direct.h>
#include <fcntl.h>
#include <io.h>
#include <process.h>
#include <sys/stat.h>
#include <wchar.h>
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

static int g_fail;

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            (void)fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                  \
            g_fail++;                                                                              \
        }                                                                                          \
    } while (0)

#define PATH_CAP 1024u
#define FRAMES_CAP 8u
#define FIXTURE_ROWS 3u

/* Three coded frames of x265 --csv-log-level 2 (a column subset; the reader
 * locates columns by header name). */
static const char k_csv[] = "Encode Order, Type, POC, QP, Bits, Y PSNR, U PSNR, V PSNR\n"
                            "0, I-SLICE, 0, 26.00, 24000, 43.1, 40.5, 40.4\n"
                            "1, P-SLICE, 2, 30.00, 8000, 38.0, 37.2, 36.8\n"
                            "2, B-SLICE, 1, 34.00, 3000, 37.1, 36.6, 35.5\n";

/* The non-ASCII names: Latin-1 (U+00ED, U+00E9), CJK (U+76EE U+5F55, U+6587
 * U+4EF6) and an astral emoji (U+1F600, a UTF-16 surrogate pair). The library
 * always receives the UTF-8 bytes; the fixture is created from the platform
 * spelling, which on Windows is written independently as UTF-16. */
#define DIR_U8 "d\xc3\xadr_\xe7\x9b\xae\xe5\xbd\x95_\xf0\x9f\x98\x80"
#define FILE_U8 "qp_\xc3\xa9_\xe6\x96\x87\xe4\xbb\xb6_\xf0\x9f\x98\x80.csv"

#ifdef _WIN32
typedef wchar_t pchar;
#define SEP_P L'/'
#define PLAIN_P L"plain.csv"
#define DIR_P L"d\u00edr_\u76ee\u5f55_\U0001F600"
#define FILE_P L"qp_\u00e9_\u6587\u4ef6_\U0001F600.csv"
static size_t plen(const pchar *s)
{
    return wcslen(s);
}
static int make_dir(const pchar *p)
{
    return _wmkdir(p);
}
static int remove_dir(const pchar *p)
{
    return _wrmdir(p);
}
static int remove_file(const pchar *p)
{
    return _wremove(p);
}
/* Exclusive create, never reusing a file. Through _wopen + _O_EXCL rather than
 * the C11 "wx" fopen mode, which the legacy msvcrt.dll runtime (non-UCRT
 * mingw-w64 toolchains) rejects with EINVAL. */
static FILE *create_file(const pchar *p)
{
    const int fd = _wopen(p, _O_WRONLY | _O_CREAT | _O_EXCL | _O_TEXT, _S_IREAD | _S_IWRITE);
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    fp = _fdopen(fd, "w");
    if (fp == NULL) {
        (void)_close(fd);
    }
    return fp;
}
static long process_id(void)
{
    return (long)_getpid();
}
/* The documented errno of the Windows length checks (docs/api/interop-abi.md). */
#define ERRNO_TOO_LONG ENAMETOOLONG
#else
typedef char pchar;
#define SEP_P '/'
#define PLAIN_P "plain.csv"
#define DIR_P DIR_U8
#define FILE_P FILE_U8
static size_t plen(const pchar *s)
{
    return strlen(s);
}
static int make_dir(const pchar *p)
{
    return mkdir(p, S_IRWXU);
}
static int remove_dir(const pchar *p)
{
    return rmdir(p);
}
static int remove_file(const pchar *p)
{
    return remove(p);
}
static FILE *create_file(const pchar *p)
{
    return fopen(p, "wx"); /* C11 exclusive create: never follows or reuses a file */
}
static long process_id(void)
{
    return (long)getpid();
}
/* POSIX: errno is whatever fopen set, which the contract does not pin. */
#define ERRNO_TOO_LONG 0
#endif

/* dst = a + sep + b in platform characters; -1 (dst untouched) if too long.
 * dst may be a; b must not overlap dst. */
static int join_p(pchar *dst, size_t cap, const pchar *a, pchar sep, const pchar *b)
{
    const size_t la = plen(a);
    const size_t lb = plen(b);
    size_t i;

    if (la + lb + 2u > cap) {
        return -1;
    }
    for (i = 0; i < la; i++) {
        dst[i] = a[i];
    }
    dst[la] = sep;
    for (i = 0; i <= lb; i++) {
        dst[la + 1u + i] = b[i]; /* includes b's terminator */
    }
    return 0;
}

/* dst = a + "/" + b in UTF-8 bytes; -1 (dst untouched) if too long. */
static int join_u8(char *dst, size_t cap, const char *a, const char *b)
{
    const int n = snprintf(dst, cap, "%s/%s", a, b);

    return (n < 0 || (size_t)n >= cap) ? -1 : 0;
}

/* Create p exclusively and write k_csv into it. */
static int write_fixture(const pchar *p)
{
    FILE *fp = create_file(p);
    int ok;

    if (fp == NULL) {
        return -1;
    }
    ok = fputs(k_csv, fp) >= 0;
    if (fclose(fp) != 0) {
        ok = 0;
    }
    return ok ? 0 : -1;
}

/* Parse path through the library into frames[] and verify the fixture rows. */
static int parse_fixture(const char *what, const char *path, PelorusX265Frame *frames)
{
    size_t count = 0;
    const pel_result rc = pel_x265_csv_parse(path, frames, FRAMES_CAP, &count);

    CHECK(rc == PEL_OK);
    CHECK(count == FIXTURE_ROWS);
    if (rc != PEL_OK || count != FIXTURE_ROWS) {
        (void)fprintf(stderr, "  %s: %s, %zu rows\n", what, pel_result_str(rc), count);
        return 0;
    }
    CHECK(frames[0].slice_type == 'I' && frames[0].qp == 26.0f && frames[0].bits == 24000u);
    CHECK(frames[1].slice_type == 'P' && frames[1].poc == 2);
    CHECK(frames[2].slice_type == 'B' && frames[2].psnr_v == 35.5f);
    (void)printf("ok: %s -> PEL_OK, %u rows\n", what, FIXTURE_ROWS);
    return 1;
}

/* The library must return want and set out_count to 0 for path. A non-zero
 * want_errno must also match errno: the Windows path checks document theirs
 * (docs/api/interop-abi.md); a failed open's errno is the C library's (pass 0). */
static void expect_rc(const char *what, const char *path, pel_result want, int want_errno)
{
    PelorusX265Frame frames[FRAMES_CAP];
    size_t count = 99;
    pel_result rc;
    int got_errno;

    errno = 0;
    rc = pel_x265_csv_parse(path, frames, FRAMES_CAP, &count);
    got_errno = errno;
    CHECK(rc == want);
    CHECK(count == 0u);
    CHECK(want_errno == 0 || got_errno == want_errno);
    if (rc != want || (want_errno != 0 && got_errno != want_errno)) {
        (void)fprintf(stderr, "  %s: got '%s' errno %d, want '%s' errno %d\n", what,
                      pel_result_str(rc), got_errno, pel_result_str(want), want_errno);
        return;
    }
    (void)printf("ok: %s -> %s\n", what, pel_result_str(rc));
}

/* Field-by-field row equality (the struct has padding, so no memcmp). */
static int same_rows(const PelorusX265Frame *a, const PelorusX265Frame *b, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
        if (a[i].poc != b[i].poc || a[i].qp != b[i].qp || a[i].bits != b[i].bits ||
            a[i].psnr_y != b[i].psnr_y || a[i].psnr_u != b[i].psnr_u ||
            a[i].psnr_v != b[i].psnr_v || a[i].slice_type != b[i].slice_type) {
            return 0;
        }
    }
    return 1;
}

/* Every path the round-trip test touches, in both spellings. */
typedef struct fixture_paths {
    pchar plain_p[PATH_CAP]; /* <base>/plain.csv          (platform spelling) */
    pchar dir_p[PATH_CAP];   /* <base>/<non-ASCII dir>                        */
    pchar file_p[PATH_CAP];  /* <base>/<non-ASCII dir>/<non-ASCII file>       */
    char plain_u8[PATH_CAP]; /* the same three, as the library receives them  */
    char dir_u8[PATH_CAP];
    char file_u8[PATH_CAP];
    char miss_u8[PATH_CAP]; /* a missing non-ASCII file in the non-ASCII dir  */
} fixture_paths;

/* Short-circuits: a later join never reads a buffer an earlier one left unset. */
static int build_paths(fixture_paths *fx, const char *base_u8, const pchar *base_p)
{
    if (join_p(fx->plain_p, PATH_CAP, base_p, SEP_P, PLAIN_P) != 0 ||
        join_p(fx->dir_p, PATH_CAP, base_p, SEP_P, DIR_P) != 0 ||
        join_p(fx->file_p, PATH_CAP, fx->dir_p, SEP_P, FILE_P) != 0 ||
        join_u8(fx->plain_u8, PATH_CAP, base_u8, "plain.csv") != 0 ||
        join_u8(fx->dir_u8, PATH_CAP, base_u8, DIR_U8) != 0 ||
        join_u8(fx->file_u8, PATH_CAP, fx->dir_u8, FILE_U8) != 0 ||
        join_u8(fx->miss_u8, PATH_CAP, fx->dir_u8, "missing_\xc3\xa9.csv") != 0) {
        return -1;
    }
    return 0;
}

/* Evidence only (it depends on the machine's ANSI code page, so it asserts
 * nothing): what the pre-ADR-0149 narrow open does with the same UTF-8 bytes. */
static void report_narrow_open(const char *path_u8)
{
#ifdef _WIN32
    FILE *narrow = fopen(path_u8, "r");

    (void)printf("info: narrow fopen of the UTF-8 name under ANSI code page %u: %s\n", GetACP(),
                 narrow != NULL ? "opened" : "failed (the defect ADR-0149 fixes)");
    if (narrow != NULL) {
        (void)fclose(narrow);
    }
#else
    (void)path_u8;
#endif
}

/* ASCII and non-ASCII directory + file names parse to identical rows. */
static void test_round_trips(const char *base_u8, const pchar *base_p)
{
    fixture_paths fx;
    PelorusX265Frame ascii_rows[FRAMES_CAP];
    PelorusX265Frame utf8_rows[FRAMES_CAP];
    const int paths_fit = build_paths(&fx, base_u8, base_p) == 0;
    int have_ascii;
    int have_utf8;

    CHECK(paths_fit);
    if (!paths_fit) {
        return;
    }
    CHECK(write_fixture(fx.plain_p) == 0);
    CHECK(make_dir(fx.dir_p) == 0);
    CHECK(write_fixture(fx.file_p) == 0);

    have_ascii = parse_fixture("ASCII path", fx.plain_u8, ascii_rows);
    have_utf8 =
        parse_fixture("non-ASCII UTF-8 dir + file (Latin-1, CJK, U+1F600)", fx.file_u8, utf8_rows);
    CHECK(have_ascii && have_utf8);
    if (have_ascii && have_utf8) {
        CHECK(same_rows(ascii_rows, utf8_rows, FIXTURE_ROWS));
    }
    expect_rc("missing non-ASCII file", fx.miss_u8, PEL_ERR_ABSENT, 0);
    expect_rc("missing ASCII file", "pelorus_path_utf8_no_such_file.csv", PEL_ERR_ABSENT, 0);
    report_narrow_open(fx.file_u8);

    CHECK(remove_file(fx.file_p) == 0);
    CHECK(remove_dir(fx.dir_p) == 0);
    CHECK(remove_file(fx.plain_p) == 0);
}

/* Empty, over-long and NULL paths fail the same way on every platform. */
static void test_degenerate_paths(void)
{
    const size_t huge = 100000u;      /* past Windows' 98301-byte UTF-8 path bound */
    const size_t long_units = 40000u; /* > 32767 UTF-16 units, < the byte bound */
    char *long_path = malloc(huge + 1u);
    PelorusX265Frame frames[FRAMES_CAP];
    size_t count = 99;

    expect_rc("empty path", "", PEL_ERR_ABSENT, 0);
    /* Argument validation fails before out_count is written (documented). */
    CHECK(pel_x265_csv_parse(NULL, frames, FRAMES_CAP, &count) == PEL_ERR_INVALID);
    CHECK(count == 99u);
    CHECK(long_path != NULL);
    if (long_path == NULL) {
        return;
    }
    (void)memset(long_path, 'a', long_units);
    long_path[long_units] = '\0';
    expect_rc("40000-byte path", long_path, PEL_ERR_ABSENT, ERRNO_TOO_LONG);
    (void)memset(long_path, 0xff, huge); /* ill-formed AND over-long: length wins */
    long_path[huge] = '\0';
    expect_rc("100000-byte ill-formed path", long_path, PEL_ERR_ABSENT, ERRNO_TOO_LONG);
    free(long_path);
}

#ifdef _WIN32
/* Ill-formed UTF-8 is PEL_ERR_INVALID, never re-read through the ANSI code page:
 * "alias_\xff.csv" would name the existing "alias_\u00ff.csv" under cp1252. */
static void test_ill_formed_utf8(const char *base_u8, const pchar *base_p)
{
    static const struct {
        const char *name;
        const char *why;
    } bad[] = {
        {"bad_\xff.csv", "ill-formed UTF-8: 0xFF byte"},
        {"bad_\x80.csv", "ill-formed UTF-8: stray continuation byte"},
        {"bad_\xc0\xaf.csv", "ill-formed UTF-8: overlong '/'"},
        {"bad_\xed\xa0\x80.csv", "ill-formed UTF-8: encoded surrogate U+D800"},
        {"bad_\xf4\x90\x80\x80.csv", "ill-formed UTF-8: above U+10FFFF"},
        {"bad_\xe6\x96", "ill-formed UTF-8: truncated 3-byte sequence"},
    };
    pchar alias_p[PATH_CAP];
    char path_u8[PATH_CAP];
    size_t i;

    for (i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        CHECK(join_u8(path_u8, PATH_CAP, base_u8, bad[i].name) == 0);
        expect_rc(bad[i].why, path_u8, PEL_ERR_INVALID, EILSEQ);
    }
    CHECK(join_p(alias_p, PATH_CAP, base_p, L'/', L"alias_\u00ff.csv") == 0);
    CHECK(join_u8(path_u8, PATH_CAP, base_u8, "alias_\xff.csv") == 0);
    CHECK(write_fixture(alias_p) == 0);
    expect_rc("ill-formed name whose ANSI reading exists", path_u8, PEL_ERR_INVALID, EILSEQ);
    CHECK(remove_file(alias_p) == 0);
}

/* \\?\<cwd>\<base>\<seg>\<seg>\long.csv into d1/d2/f; -1 if the working
 * directory is not a drive path (the prefix form would differ) or too long. */
static int build_long_paths(const pchar *base_p, pchar *d1, pchar *d2, pchar *f)
{
    static const pchar seg[] = L"long_segment_\u00e9_"
                               L"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                               L"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";
    pchar cwd[PATH_CAP];
    pchar prefixed[PATH_CAP];
    pchar root[PATH_CAP];

    if (_wgetcwd(cwd, (int)PATH_CAP) == NULL || cwd[1] != L':' || cwd[2] != L'\\') {
        return -1;
    }
    if (join_p(prefixed, PATH_CAP, L"\\\\?", L'\\', cwd) != 0 ||
        join_p(root, PATH_CAP, prefixed, L'\\', base_p) != 0 ||
        join_p(d1, PATH_CAP, root, L'\\', seg) != 0 || join_p(d2, PATH_CAP, d1, L'\\', seg) != 0 ||
        join_p(f, PATH_CAP, d2, L'\\', L"long.csv") != 0) {
        return -1;
    }
    return 0;
}

/* A \\?\ extended-length path past MAX_PATH opens: the converter does not stop
 * at 260 units and passes the prefix through untouched. */
static void test_long_path(const pchar *base_p)
{
    pchar d1[PATH_CAP];
    pchar d2[PATH_CAP];
    pchar f[PATH_CAP];
    char f_u8[4u * PATH_CAP];
    PelorusX265Frame frames[FRAMES_CAP];

    if (build_long_paths(base_p, d1, d2, f) != 0 ||
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, f, -1, f_u8, (int)sizeof(f_u8), NULL,
                            NULL) <= 0) {
        (void)printf("skip: \\\\?\\ long path (working directory is not a short drive path)\n");
        return;
    }
    CHECK(wcslen(f) > (size_t)MAX_PATH);
    CHECK(make_dir(d1) == 0);
    CHECK(make_dir(d2) == 0);
    CHECK(write_fixture(f) == 0);
    (void)printf("info: \\\\?\\ path is %zu UTF-16 units (MAX_PATH %d)\n", wcslen(f), MAX_PATH);
    (void)parse_fixture("\\\\?\\ extended-length path past MAX_PATH", f_u8, frames);
    CHECK(remove_file(f) == 0);
    CHECK(remove_dir(d2) == 0);
    CHECK(remove_dir(d1) == 0);
}
#else
/* POSIX passes bytes through: a name that is not UTF-8 still opens. */
static void test_raw_byte_name(const char *base_u8)
{
    char path[PATH_CAP];
    PelorusX265Frame frames[FRAMES_CAP];

    CHECK(join_u8(path, PATH_CAP, base_u8, "raw_\xff\xfe.csv") == 0);
    expect_rc("missing non-UTF-8 byte name", path, PEL_ERR_ABSENT, 0);
    if (write_fixture(path) != 0) {
        (void)printf("skip: this filesystem rejects non-UTF-8 byte names\n");
        return;
    }
    (void)parse_fixture("non-UTF-8 byte name (literal pass-through)", path, frames);
    CHECK(remove_file(path) == 0);
}
#endif

/* Private, exclusively created per-process directory in the working directory.
 * cap_u8 counts bytes, cap_p platform characters; the name must fit both. */
static int make_base(char *base_u8, size_t cap_u8, pchar *base_p, size_t cap_p)
{
    const int n = snprintf(base_u8, cap_u8, "pelorus_path_utf8_%ld", process_id());
    size_t i;

    if (n < 0 || (size_t)n >= cap_u8 || (size_t)n >= cap_p) {
        return -1;
    }
    for (i = 0; i <= (size_t)n; i++) {
        base_p[i] = (pchar)base_u8[i]; /* ASCII: widening is exact */
    }
    return make_dir(base_p);
}

int main(void)
{
    char base_u8[64] = {0};
    pchar base_p[64] = {0};

    if (make_base(base_u8, sizeof(base_u8), base_p, sizeof(base_p) / sizeof(base_p[0])) != 0) {
        (void)fprintf(stderr, "FAIL: cannot create the private fixture directory\n");
        return EXIT_FAILURE;
    }
    test_round_trips(base_u8, base_p);
    test_degenerate_paths();
#ifdef _WIN32
    test_ill_formed_utf8(base_u8, base_p);
    test_long_path(base_p);
#else
    test_raw_byte_name(base_u8);
#endif
    CHECK(remove_dir(base_p) == 0);

    if (g_fail != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", g_fail);
        return EXIT_FAILURE;
    }
#ifdef _WIN32
    (void)printf("path-utf8: all checks passed (Windows: _wfopen via UTF-8 -> UTF-16)\n");
#else
    (void)printf("path-utf8: all checks passed (POSIX: literal fopen pass-through)\n");
#endif
    return EXIT_SUCCESS;
}
