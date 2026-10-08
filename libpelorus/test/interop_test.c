/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
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

/* NOLINTBEGIN(modernize-use-nullptr): this C translation unit is built as C23
 * by vmafx, where clang-tidy also proposes the `nullptr` keyword, but MSVC's C
 * mode has no `nullptr` (C2065); the Windows builds compile it with cl.exe.
 * The NULL macro stays. Same decision as vmafx ADR-1138
 * (docs/adr/1138-c-translation-units-keep-null.md in VMAFx/vmafx). */

#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
/* windows.h first: sddl.h relies on its types. */
#include <sddl.h>
#include <aclapi.h>
#include <share.h>
#ifdef _MSC_VER
/* The fixture's Win32 security calls live in advapi32. */
#pragma comment(lib, "advapi32.lib")
#endif
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
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

/*
 * Fixture files (Pelorus issues #60 and #62, Pelorus ADR-0148). The fixture
 * writes files into the working directory, so it must never widen access or
 * write through a path it did not create:
 *   - creation is exclusive: an existing file, link, or dangling link at the
 *     path is refused, never followed, truncated, or replaced;
 *   - the file is owner-only from the first open: POSIX mode 0600 (the umask
 *     can only remove bits), Windows a protected DACL granting only the owner;
 *   - a file this code created is removed again on every failure path, and a
 *     path it refused is never touched.
 */
#define FIXTURE_MAX_BYTES 4096u

/* Upper bound on EINTR / zero-progress retries of one fixture write; the loop
 * is otherwise bounded by the byte count (each successful write moves >= 1). */
#define FIXTURE_WRITE_RETRIES 16u

typedef enum {
    FIXTURE_CREATED,      /* created, fully written, descriptor proven owner-only */
    FIXTURE_REFUSED,      /* nothing created: the path exists or creation failed */
    FIXTURE_TOO_LARGE,    /* nothing created: contents exceed FIXTURE_MAX_BYTES */
    FIXTURE_NOT_PRIVATE,  /* created, but the open descriptor is not
                           owner-only/regular */
    FIXTURE_WRITE_FAILED, /* created, then the write did not complete */
    FIXTURE_CLOSE_FAILED  /* created and written, then close failed */
} fixture_status;

/* 1 when this status left a file behind that the caller created (and must
 * remove). */
static int fixture_status_owns_file(fixture_status status)
{
    return status == FIXTURE_NOT_PRIVATE || status == FIXTURE_WRITE_FAILED ||
           status == FIXTURE_CLOSE_FAILED;
}

static const char *fixture_status_text(fixture_status status)
{
    switch (status) {
    case FIXTURE_CREATED:
        return "created";
    case FIXTURE_REFUSED:
        return "exclusive create refused (path exists, is a link, or directory not "
               "writable; "
               "a stale file from an aborted run is one possible cause)";
    case FIXTURE_TOO_LARGE:
        return "contents exceed the fixture size limit";
    case FIXTURE_NOT_PRIVATE:
        return "created file is not owner-only or not a regular file (checked on "
               "the open handle)";
    case FIXTURE_WRITE_FAILED:
        return "write did not complete";
    case FIXTURE_CLOSE_FAILED:
        return "close failed after writing";
    default:
        return "unknown fixture status";
    }
}

#ifdef _WIN32
/* Protected DACL, one ACE: full access for the file's owner (OWNER RIGHTS).
 * Nothing is inherited from the directory, the 0600 analogue. */
#define FIXTURE_OWNER_ONLY_SDDL "D:P(A;;FA;;;OW)"

/* Write all len bytes; WriteFile may report a partial count. 0 on success. */
static int fixture_write_all(HANDLE file, const char *contents, size_t len)
{
    size_t done = 0;
    unsigned attempts = 0;

    while (done < len) {
        DWORD written = 0;

        if (attempts++ > (unsigned)len + FIXTURE_WRITE_RETRIES ||
            !WriteFile(file, contents + done, (DWORD)(len - done), &written, NULL) ||
            written == 0) {
            return -1;
        }
        done += (size_t)written;
    }
    return 0;
}

/* The open handle is a regular file (not a link or directory) whose DACL is
 * protected and holds exactly one allow ACE, for OWNER RIGHTS: no inherited,
 * group, or world entry. Checked on the handle, so no path can be swapped in.
 */
static int fixture_handle_is_owner_only(HANDLE file)
{
    BY_HANDLE_FILE_INFORMATION info;
    SECURITY_DESCRIPTOR_CONTROL control = 0;
    PSECURITY_DESCRIPTOR sd = NULL;
    PACL dacl = NULL;
    DWORD revision = 0;
    void *ace = NULL;
    int owner_only = 0;

    if (!GetFileInformationByHandle(file, &info) ||
        (info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) != 0) {
        return 0;
    }
    if (GetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, NULL, NULL, &dacl, NULL,
                        &sd) != ERROR_SUCCESS) {
        return 0;
    }
    if (dacl != NULL && GetSecurityDescriptorControl(sd, &control, &revision) &&
        (control & SE_DACL_PROTECTED) != 0 && dacl->AceCount == 1 && GetAce(dacl, 0, &ace)) {
        owner_only =
            ((const ACE_HEADER *)ace)->AceType == ACCESS_ALLOWED_ACE_TYPE &&
            IsWellKnownSid(&((ACCESS_ALLOWED_ACE *)ace)->SidStart, WinCreatorOwnerRightsSid);
    }
    (void)LocalFree(sd);
    return owner_only;
}

static fixture_status fixture_write_new(const char *path, const char *contents, size_t len)
{
    SECURITY_ATTRIBUTES sa;
    PSECURITY_DESCRIPTOR sd = NULL;
    HANDLE file;
    fixture_status status = FIXTURE_CREATED;

    if (!ConvertStringSecurityDescriptorToSecurityDescriptorA(FIXTURE_OWNER_ONLY_SDDL,
                                                              SDDL_REVISION_1, &sd, NULL)) {
        return FIXTURE_REFUSED;
    }
    sa.nLength = (DWORD)sizeof(sa);
    sa.lpSecurityDescriptor = sd;
    sa.bInheritHandle = FALSE;
    /* CREATE_NEW alone follows a dangling link and creates its target;
   * FILE_FLAG_OPEN_REPARSE_POINT makes any existing link name fail.
   * READ_CONTROL lets the descriptor check below read the DACL. */
    file = CreateFileA(path, GENERIC_WRITE | READ_CONTROL, 0, &sa, CREATE_NEW,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    (void)LocalFree(sd);
    if (file == INVALID_HANDLE_VALUE) {
        return FIXTURE_REFUSED;
    }
    if (!fixture_handle_is_owner_only(file)) {
        status = FIXTURE_NOT_PRIVATE;
    } else if (fixture_write_all(file, contents, len) != 0) {
        status = FIXTURE_WRITE_FAILED;
    }
    if (!CloseHandle(file) && status == FIXTURE_CREATED) {
        status = FIXTURE_CLOSE_FAILED;
    }
    return status;
}

/* 0 created, 1 this account may not create links (skip), -1 error. Windows
 * needs Developer Mode or SeCreateSymbolicLinkPrivilege for file links. */
static int fixture_symlink(const char *target, const char *link_path)
{
    DWORD error;

    if (CreateSymbolicLinkA(link_path, target, SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE)) {
        return 0;
    }
    error = GetLastError();
    return (error == ERROR_PRIVILEGE_NOT_HELD || error == ERROR_INVALID_PARAMETER) ? 1 : -1;
}
#else
/* Write all len bytes, looping over short writes and EINTR. The loop ends
 * after at most len + FIXTURE_WRITE_RETRIES iterations. 0 on success. */
static int fixture_write_all(int fd, const char *contents, size_t len)
{
    size_t done = 0;
    size_t attempts = 0;

    while (done < len) {
        ssize_t n;

        if (attempts++ > len + FIXTURE_WRITE_RETRIES) {
            return -1;
        }
        n = write(fd, contents + done, len - done);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            return -1;
        }
        done += (size_t)n;
    }
    return 0;
}

/* The open descriptor is a regular file with exactly mode 0600. Checked on the
 * descriptor, so no path can be swapped in between create and check. */
static int fixture_fd_is_owner_only(int fd)
{
    struct stat st;

    if (fstat(fd, &st) != 0) {
        return 0;
    }
    return S_ISREG(st.st_mode) &&
           (st.st_mode & (S_IRWXU | S_IRWXG | S_IRWXO)) == (S_IRUSR | S_IWUSR);
}

static fixture_status fixture_write_new(const char *path, const char *contents, size_t len)
{
    /* O_EXCL fails on any existing name, links included (POSIX open());
   * O_NOFOLLOW states the same intent for the final component. */
    const int fd =
        open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR);
    fixture_status status = FIXTURE_CREATED;

    if (fd < 0) {
        return FIXTURE_REFUSED;
    }
    if (!fixture_fd_is_owner_only(fd)) {
        status = FIXTURE_NOT_PRIVATE;
    } else if (fixture_write_all(fd, contents, len) != 0) {
        status = FIXTURE_WRITE_FAILED;
    }
    if (close(fd) != 0 && status == FIXTURE_CREATED) {
        status = FIXTURE_CLOSE_FAILED;
    }
    return status;
}

/* 0 created, -1 error: every supported POSIX host can create links. */
static int fixture_symlink(const char *target, const char *link_path)
{
    return symlink(target, link_path) == 0 ? 0 : -1;
}
#endif

/* Create path exclusively, prove the open handle owner-only, write contents.
 * A file this call created is removed again on every failure after creation. */
static fixture_status write_private_fixture_status(const char *path, const char *contents)
{
    const size_t len = strlen(contents);
    fixture_status status;

    if (len > FIXTURE_MAX_BYTES) {
        return FIXTURE_TOO_LARGE;
    }
    status = fixture_write_new(path, contents, len);
    if (fixture_status_owns_file(status)) {
        CHECK(remove(path) == 0); /* ours: never leave a partial fixture behind */
    }
    return status;
}

/* Create path exclusively and write contents. 0 on success. */
static int write_private_fixture(const char *path, const char *contents)
{
    return write_private_fixture_status(path, contents) == FIXTURE_CREATED ? 0 : -1;
}

/* Create a fixture under the most permissive umask, so only the requested
 * mode can keep it private; fixture_write_new proves it owner-only on the open
 * descriptor. 0 on success; on failure nothing this call created remains. */
static int create_checked_fixture(const char *path, const char *contents)
{
    fixture_status status;
#ifndef _WIN32
    const mode_t old_umask = umask(0);
#endif

    status = write_private_fixture_status(path, contents);
#ifndef _WIN32
    (void)umask(old_umask);
#endif
    if (status != FIXTURE_CREATED) {
        (void)fprintf(stderr, "fixture %s: %s\n", path, fixture_status_text(status));
        return -1;
    }
    return 0;
}

/* Opens a fixture for reading. Windows: _fsopen() with _SH_DENYNO, the sharing fopen() gives;
 * the CRT declares fopen() deprecated (C4996 under cl.exe, -Wdeprecated-declarations under
 * clang-cl and icx-cl), and fopen_s() would open the file exclusively. */
static FILE *fixture_open_read(const char *path)
{
#ifdef _WIN32
    return _fsopen(path, "rb", _SH_DENYNO);
#else
    return fopen(path, "rb");
#endif
}

/* The whole file equals expected: nothing truncated, appended, or replaced. */
static int fixture_equals(const char *path, const char *expected)
{
    char buf[64];
    size_t n;
    int closed;
    FILE *fp = fixture_open_read(path);

    if (fp == NULL) {
        return 0;
    }
    n = fread(buf, 1, sizeof(buf), fp);
    closed = fclose(fp);
    return closed == 0 && n == strlen(expected) && memcmp(buf, expected, n) == 0;
}

/* 1 when the path exists (checked by opening it, so a link is followed). */
static int fixture_path_exists(const char *path)
{
    FILE *fp = fixture_open_read(path);

    if (fp == NULL) {
        return 0;
    }
    CHECK(fclose(fp) == 0);
    return 1;
}

/* A link to an existing file is refused; the target keeps its bytes. Returns
 * 1 when this account cannot create links (Windows without the right). */
static int check_fixture_refuses_live_link(const char *target, const char *original)
{
    const char *live = "pelorus_fixture_link.tmp";
    const int link_rc = fixture_symlink(target, live);

    if (link_rc == 1) {
        (void)fprintf(stderr, "note: no symlink privilege; fixture link cases skipped\n");
        return 1;
    }
    CHECK(link_rc == 0);
    if (link_rc != 0) {
        return 0;
    }
    CHECK(write_private_fixture(live, "clobbered\n") != 0);
    CHECK(fixture_equals(target, original));
    CHECK(remove(live) == 0);
    return 0;
}

/* A dangling link is refused, and nothing is created at its target. */
static void check_fixture_refuses_dangling_link(void)
{
    const char *dangling = "pelorus_fixture_dangling.tmp";
    const char *absent = "pelorus_fixture_absent.tmp";
    const int stale = fixture_path_exists(absent);
    int link_rc;
    int created;

    CHECK(!stale); /* else the link would not dangle; never touch that file */
    if (stale) {
        return;
    }
    link_rc = fixture_symlink(absent, dangling);
    CHECK(link_rc == 0);
    if (link_rc != 0) {
        return;
    }
    CHECK(write_private_fixture(dangling, "clobbered\n") != 0);
    created = fixture_path_exists(absent);
    CHECK(!created); /* nothing may be created through the link */
    if (created) {
        CHECK(remove(absent) == 0);
    }
    CHECK(remove(dangling) == 0);
}

#ifndef _WIN32
/* Issue #65: the privacy check reads the open descriptor, and a failing write
 * ends the bounded loop instead of spinning. (The Windows handle check is
 * exercised by every fixture creation on that host.) */
static void test_fixture_descriptor_checks(void)
{
    const char *probe = "pelorus_fixture_probe.tmp";
    const mode_t old_umask = umask(0);
    const int fd =
        open(probe, O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR);

    (void)umask(old_umask);
    CHECK(fd >= 0);
    if (fd < 0) {
        return;
    }
    CHECK(fixture_fd_is_owner_only(fd) == 1);
    CHECK(fchmod(fd, S_IRUSR | S_IWUSR | S_IRGRP) == 0);
    CHECK(fixture_fd_is_owner_only(fd) == 0); /* group-readable is refused */
    CHECK(fchmod(fd, S_IRUSR | S_IWUSR) == 0);
    CHECK(fixture_write_all(fd, "abc", 3) == 0);
    CHECK(close(fd) == 0);
    CHECK(fixture_write_all(fd, "abc", 3) != 0); /* EBADF: fails, never loops */
    CHECK(remove(probe) == 0);
}
#endif

/* Pelorus issues #60 and #62: fixture creation never grants group/world
 * access and never follows, truncates, or replaces an existing path. */
static void test_fixture_file_safety(void)
{
    const char *plant = "pelorus_fixture_plant.tmp";
    const char *original = "planted\n";
    int rc;

    rc = create_checked_fixture(plant, original);
    CHECK(rc == 0);
    if (rc != 0) {
        return;
    }
    CHECK(write_private_fixture(plant, "clobbered\n") != 0);
    CHECK(fixture_equals(plant, original));
    if (check_fixture_refuses_live_link(plant, original) == 0) {
        check_fixture_refuses_dangling_link();
    }
    CHECK(remove(plant) == 0);
#ifndef _WIN32
    test_fixture_descriptor_checks();
#endif
}

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

static void check_roundtrip_banding(const uint8_t *blob, size_t len)
{
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                                &got) == PEL_OK);
    CHECK(got == sizeof(PelorusBandingSection));
    {
        const PelorusBandingSection *b = p;
        CHECK(b->global_banding_risk == 0.42f);
        CHECK(b->flat_area_fraction == 0.61f);
    }
}

static void check_roundtrip_variance(const uint8_t *blob, size_t len)
{
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_VARIANCE, sizeof(PelorusVarianceSection), &p,
                                &got) == PEL_OK);
    {
        const PelorusVarianceSection *v = p;
        CHECK(v->texture_energy == 0.33f);
    }
}

static void check_roundtrip_filmgrain(const uint8_t *blob, size_t len)
{
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection), &p,
                                &got) == PEL_OK);
    {
        const PelorusFilmGrainSection *g = p;
        CHECK(g->seed == 0xDEADBEEFCAFEULL);
        CHECK(g->num_y_points == 3);
        CHECK(g->apply == 1);
    }
}

static void check_roundtrip_sections(const uint8_t *blob, size_t len)
{
    const void *p = NULL;
    size_t got = 0;

    check_roundtrip_banding(blob, len);
    check_roundtrip_variance(blob, len);
    check_roundtrip_filmgrain(blob, len);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p,
                                &got) == PEL_ERR_ABSENT);
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
    check_roundtrip_sections(blob, len);

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

/* Header and directory patches go through memcpy on the byte buffer: the blob
 * is a uint8_t array, so no struct pointer is formed over it. */
static PelorusSideData blob_header_load(const uint8_t *blob)
{
    PelorusSideData hdr;

    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    return hdr;
}

static void blob_header_store(uint8_t *blob, const PelorusSideData *hdr)
{
    memcpy(blob + PELORUS_SIDEDATA_UUID_LEN, hdr, sizeof(*hdr));
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
    PelorusSideData hdr;

    fill_meta(&meta);
    memset(&band, 0, sizeof(band));
    sec.id = PEL_SEC_BANDING;
    sec.data = &band;
    sec.size = (uint32_t)sizeof(band);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);

    hdr = blob_header_load(blob);
    hdr.abi_major = (uint16_t)(PELORUS_ABI_MAJOR + 1u);
    blob_header_store(blob, &hdr);

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

/* Defensive parser (R5): a crafted/corrupt blob whose section payload offset is
 * NOT 8-byte aligned must be rejected, not cast to a struct at an unaligned
 * address (the packer always 8-aligns, so this only arises from foreign/corrupt
 * framing). The current suite never patches dir[i].offset, so exercise it here. */
static void test_misaligned_offset(void)
{
    PelorusSideData meta;
    PelorusFilmGrainSection grain; /* has a u64 at offset 0 -> alignment matters */
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    PelorusSideData hdr;
    PelorusSectionDir dir;
    size_t dir_off;

    fill_meta(&meta);
    memset(&grain, 0, sizeof(grain));
    grain.seed = 0xDEADBEEFCAFEULL;
    sec.id = PEL_SEC_FILMGRAIN;
    sec.data = &grain;
    sec.size = (uint32_t)sizeof(grain);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);

    /* Well-formed first: the 8-aligned offset parses. */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection), &p,
                                &got) == PEL_OK);

    /* Now hand-patch dir[0].offset to a misaligned value (+4). The section then
     * still fits the buffer but its start is no longer 8-aligned. */
    hdr = blob_header_load(blob);
    dir_off = (size_t)PELORUS_SIDEDATA_UUID_LEN + hdr.header_size;
    memcpy(&dir, blob + dir_off, sizeof(dir));
    dir.offset += 4u;
    memcpy(blob + dir_off, &dir, sizeof(dir));

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection), &p,
                                &got) == PEL_ERR_ABI);
    pel_blob_free(blob);
}

/* Defensive packer (R5 overflow guard): a section whose declared size, once
 * 8-aligned and summed, would overflow the uint32 total_size wire field must be
 * rejected before allocating (otherwise the alloc undersizes and the copy
 * overflows the heap). The size check runs before any deref of sec.data, so a
 * tiny dummy data pointer with a huge declared size is safe to pass here. */
static void test_pack_size_overflow(void)
{
    PelorusSideData meta;
    PelorusPackSection sec;
    uint8_t dummy = 0;
    uint8_t *blob = NULL;
    size_t len = 0;

    fill_meta(&meta);
    /* size near UINT32_MAX: 8-aligning it wraps a 32-bit accumulator to ~0 but the
     * 64-bit guard catches need > UINT32_MAX and rejects. data is never read. */
    sec.id = PEL_SEC_BANDING;
    sec.data = &dummy;
    sec.size = 0xFFFFFFF9u;
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_ERR_RANGE);
    CHECK(blob == NULL);
}

static void check_qp_report_scalars(const PelorusQpReportSection *r)
{
    CHECK(r->avg_qp == 27.5f);
    CHECK(r->psnr_y == 41.2f);
    CHECK(r->total_bits == 1234567ULL);
    CHECK(r->num_inter_blocks == 200);
    CHECK(r->honored_fraction == 0.75f);
    CHECK(r->report_source == PEL_QPSRC_QSV);
    CHECK(r->block_size_log2 == 4);
}

static void check_qp_report_fields(const PelorusQpReportSection *r, uint16_t cells)
{
    check_qp_report_scalars(r);
    CHECK(r->qp_valid == 1);
    CHECK(r->qp_cell_size == cells);
    CHECK(r->num_intra_blocks == 40);
    CHECK(r->num_skipped_blocks == 16);
    CHECK(r->psnr_u == 0.0f);
    CHECK(r->psnr_v == 0.0f);
}

static void check_qp_report_section(const uint8_t *blob, size_t len, uint16_t cells)
{
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_QPREPORT, sizeof(PelorusQpReportSection), &p,
                                &got) == PEL_OK);
    CHECK(got == sizeof(PelorusQpReportSection));
    check_qp_report_fields(p, cells);

    /* An older consumer (knows only the first two floats) still parses (R4). */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_QPREPORT, 8, &p, &got) == PEL_OK);
    CHECK(got == 8);
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
    check_qp_report_section(blob, len, cells);

    pel_blob_free(blob);
}

static void check_motion_conf_pair(const PelorusSideData *meta,
                                   const PelorusMotionConfSection *conf)
{
    PelorusMotionSection motion;
    PelorusPackSection sections[2];
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    memset(&motion, 0, sizeof(motion));
    sections[0].id = PEL_SEC_MOTION;
    sections[0].data = &motion;
    sections[0].size = (uint32_t)sizeof(motion);
    sections[1].id = PEL_SEC_MOTION_CONF;
    sections[1].data = conf;
    sections[1].size = (uint32_t)sizeof(*conf);
    CHECK(pel_blob_pack(meta, sections, 2, &blob, &len) == PEL_OK);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p,
                                &got) == PEL_OK);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION_CONF, sizeof(PelorusMotionConfSection),
                                &p, &got) == PEL_OK);
    CHECK(got == sizeof(PelorusMotionConfSection));
    pel_blob_free(blob);
}

static void check_motion_conf_fields(const uint8_t *blob, size_t len)
{
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION_CONF, sizeof(PelorusMotionConfSection),
                                &p, &got) == PEL_OK);
    CHECK(got == sizeof(PelorusMotionConfSection));
    {
        const PelorusMotionConfSection *r = p;
        CHECK(r->conf_field_size == 16 * 9);
        CHECK(r->conf_metric == PEL_MOTION_CONF_SAD);
    }

    /* R4: a consumer that knows only conf_field_offset+conf_field_size (8 bytes,
     * the meaning that predates conf_metric) still parses. */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION_CONF, 8, &p, &got) == PEL_OK);
    CHECK(got == 8);

    /* R3: the plain motion section we did NOT write is absent. */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p,
                                &got) == PEL_ERR_ABSENT);
}

/* PEL_SEC_MOTION_CONF (g): pack the per-block MV confidence section, parse it
 * back, verify the offset/size/metric fields round-trip; a consumer that knows
 * only the offset/size (not conf_metric) still parses (R4); and a section we did
 * not write is absent (R3). */
static void test_motion_conf_roundtrip(void)
{
    PelorusSideData meta;
    PelorusMotionConfSection conf;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;

    fill_meta(&meta);
    memset(&conf, 0, sizeof(conf));
    conf.conf_field_offset = 0;    /* producer patches once it appends the map */
    conf.conf_field_size = 16 * 9; /* matches fill_meta grid (uint8 per cell)  */
    conf.conf_metric = PEL_MOTION_CONF_SAD;

    sec.id = PEL_SEC_MOTION_CONF;
    sec.data = &conf;
    sec.size = (uint32_t)sizeof(conf);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(blob != NULL);
    CHECK(pel_blob_is_present(blob, len) == 1);

    check_motion_conf_fields(blob, len);

    pel_blob_free(blob);
    /* Production writes MOTION and MOTION_CONF together; both must survive. */
    check_motion_conf_pair(&meta, &conf);
}

static void check_complexity_values(const PelorusComplexitySection *r)
{
    CHECK(r->complexity == 0.625f);
    CHECK(r->texture_energy == 0.5f);
    CHECK(r->motion_component == 0.25f);
    CHECK(r->has_scene_cut == 1);
}

static void check_complexity_fields(const uint8_t *blob, size_t len)
{
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_find_section(blob, len, PEL_SEC_COMPLEXITY, sizeof(PelorusComplexitySection), &p,
                                &got) == PEL_OK);
    CHECK(got == sizeof(PelorusComplexitySection));
    check_complexity_values(p);
    /* R4: a consumer that knows only `complexity` (first 4 bytes) still parses. */
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_COMPLEXITY, 4, &p, &got) == PEL_OK);
    CHECK(got == 4);
}

/* PEL_SEC_COMPLEXITY (h): pack the per-frame complexity scalar, round-trip the
 * fields, and confirm R4 (an older consumer that knows only the first float
 * still parses). */
static void test_complexity_roundtrip(void)
{
    PelorusSideData meta;
    PelorusComplexitySection cx;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;

    fill_meta(&meta);
    memset(&cx, 0, sizeof(cx));
    cx.complexity = 0.625f;
    cx.texture_energy = 0.5f;
    cx.motion_component = 0.25f;
    cx.has_scene_cut = 1;

    sec.id = PEL_SEC_COMPLEXITY;
    sec.data = &cx;
    sec.size = (uint32_t)sizeof(cx);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(blob != NULL);

    check_complexity_fields(blob, len);
    pel_blob_free(blob);
}

static void check_fold_uniform(const PelorusQpReportSection *out, const int8_t *cells, size_t n)
{
    size_t i;

    CHECK(out->qp_valid == 1);
    CHECK(out->qp_cell_size == 4u * 2u);
    CHECK(out->report_source == PEL_QPSRC_QSV);
    CHECK(out->avg_qp == 30.0f);
    for (i = 0; i < n; i++) {
        CHECK(cells[i] == 30); /* uniform block QP -> uniform cell QP */
    }
}

static void check_fold_stats_only(const PelorusQpReportInput *in)
{
    PelorusQpReportSection out;

    CHECK(pel_qp_report_from_blocks(in, 4, 2, &out, NULL, 0) == PEL_OK);
    CHECK(out.qp_valid == 0);
    CHECK(out.qp_cell_size == 0u);
    CHECK(out.avg_qp == 30.0f);
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
    check_fold_uniform(&out, cells, sizeof(cells));

    /* Frame-stats-only path: no block grid -> qp_valid 0, scalars preserved. */
    in.block_qp = NULL;
    check_fold_stats_only(&in);

    /* Too-small output buffer is rejected, not overrun. */
    in.block_qp = blocks;
    CHECK(pel_qp_report_from_blocks(&in, 4, 2, &out, cells, 3) == PEL_ERR_RANGE);

    /* NULL inputs / zero grid are rejected. */
    CHECK(pel_qp_report_from_blocks(NULL, 4, 2, &out, cells, sizeof(cells)) == PEL_ERR_INVALID);
    CHECK(pel_qp_report_from_blocks(&in, 0, 2, &out, cells, sizeof(cells)) == PEL_ERR_INVALID);
}

static void check_x265_fold_scalars(const PelorusQpReportSection *qp)
{
    CHECK(qp->qp_valid == 0);
    CHECK(qp->report_source == PEL_QPSRC_NONE);
    CHECK(qp->total_bits == 35000ULL);
    /* bit-weighted: (26*24000 + 30*8000 + 34*3000)/35000 = 27.6 exactly. */
    CHECK(qp->avg_qp > 27.55f && qp->avg_qp < 27.65f);
    CHECK(qp->psnr_y > 41.0f && qp->psnr_y < 43.2f); /* I-frame-dominated */
    CHECK(qp->honored_fraction == 0.0f);             /* no request to compare */
}

static void check_x265_fold(const PelorusX265Frame *frames, size_t count)
{
    PelorusQpReportSection qp;
    const float requested_flat[3] = {28.0f, 28.0f, 28.0f};
    const float requested_shaped[3] = {24.0f, 30.0f, 36.0f};

    CHECK(pel_qp_report_from_x265_frames(frames, count, NULL, &qp) == PEL_OK);
    check_x265_fold_scalars(&qp);

    /* Flat requested QP agrees only with the achieved mean frame: 1/3. */
    CHECK(pel_qp_report_from_x265_frames(frames, count, requested_flat, &qp) == PEL_OK);
    CHECK(qp.honored_fraction > 0.33f && qp.honored_fraction < 0.34f);

    /* A requested shape matching achieved low/flat/high QP agrees everywhere. */
    CHECK(pel_qp_report_from_x265_frames(frames, count, requested_shaped, &qp) == PEL_OK);
    CHECK(qp.honored_fraction == 1.0f);
}

static void check_x265_frames(const PelorusX265Frame *frames)
{
    CHECK(frames[0].slice_type == 'I');
    CHECK(frames[0].qp == 26.0f);
    CHECK(frames[0].bits == 24000ULL);
    CHECK(frames[1].slice_type == 'P');
    CHECK(frames[2].qp == 34.0f);
    CHECK(frames[2].psnr_v == 35.5f);
}

/* The x265 CSV reader (ADR-0122): write a minimal x265-shaped CSV to a temp
 * file, parse it, fold it into a PEL_SEC_QPREPORT, and verify the aggregated
 * scalars + the requested-vs-honored honored_fraction. This is the runnable
 * closed-loop surface; the fixture exercises it without invoking x265. */
static void test_x265_csv_reader(void)
{
    /* Three coded frames + a trailing aggregate row x265 appends. Columns match
     * x265 --csv-log-level 2 (subset; the reader locates by header name). The
     * honored QP differs from a flat requested QP per slice type, exactly the
     * signal honored_fraction measures. */
    static const char *csv = "Encode Order, Type, POC, QP, Bits, Y PSNR, U PSNR, V PSNR\n"
                             "0, I-SLICE, 0, 26.00, 24000, 43.1, 40.5, 40.4\n"
                             "1, P-SLICE, 2, 30.00, 8000, 38.0, 37.2, 36.8\n"
                             "2, B-SLICE, 1, 34.00, 3000, 37.1, 36.6, 35.5\n"
                             "Total frames, 3, , 30.00, , , , \n";
    PelorusX265Frame frames[8];
    size_t count = 0;
    const char *path = "pelorus_x265_csv_test.csv";
    int fixture_rc;

    fixture_rc = create_checked_fixture(path, csv);
    CHECK(fixture_rc == 0);
    if (fixture_rc != 0) {
        return;
    }
    /* Once created, every later step falls through to the remove() below. */

    /* Parse: 3 coded frames, the "Total frames" aggregate row dropped. */
    CHECK(pel_x265_csv_parse(path, frames, 8, &count) == PEL_OK);
    CHECK(count == 3);
    check_x265_frames(frames);

    check_x265_fold(frames, count);

    /* A capacity smaller than the row count truncates and reports RANGE. */
    CHECK(pel_x265_csv_parse(path, frames, 2, &count) == PEL_ERR_RANGE);
    CHECK(count == 2);

    CHECK(remove(path) == 0);
}

/* The x265 CSV reader's error paths need no fixture file. */
static void test_x265_csv_reader_guards(void)
{
    PelorusX265Frame frames[8];
    size_t count = 0;
    PelorusQpReportSection qp;

    memset(frames, 0, sizeof(frames));
    /* A missing file is ABSENT, not a crash. */
    CHECK(pel_x265_csv_parse("pelorus_no_such_file.csv", frames, 8, &count) == PEL_ERR_ABSENT);

    /* NULL guards. */
    CHECK(pel_x265_csv_parse(NULL, frames, 8, &count) == PEL_ERR_INVALID);
    CHECK(pel_qp_report_from_x265_frames(NULL, 1, NULL, &qp) == PEL_ERR_INVALID);
    CHECK(pel_qp_report_from_x265_frames(frames, 0, NULL, &qp) == PEL_ERR_INVALID);
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

/* Parse one re-homed copy of the film-grain blob and read the payload through
 * memcpy, as a careful consumer does. */
static void check_skewed_blob(const uint8_t *skewed, size_t len)
{
    PelorusFilmGrainSection got_grain;
    const void *p = NULL;
    size_t got = 0;

    CHECK(pel_blob_is_present(skewed, len) == 1);
    CHECK(pel_blob_find_section(skewed, len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection), &p,
                                &got) == PEL_OK);
    CHECK(p != NULL && got == sizeof(PelorusFilmGrainSection));
    if (p != NULL && got == sizeof(got_grain)) {
        memcpy(&got_grain, p, sizeof(got_grain));
        CHECK(got_grain.seed == 0xDEADBEEFCAFEULL);
    }
}

/* Issue #44: the parser must not assume the caller's blob base is 8-byte
 * aligned. Before the memcpy-based parse this produced 26 -fsanitize=alignment
 * diagnostics and is genuine UB on strict-alignment targets. The section
 * pointer handed back is still only castable when the BASE was aligned, so the
 * check here reads the payload through memcpy, exactly as a careful consumer
 * (and vmafx's perceptual_weight.c) does. */
static void test_misaligned_blob_base(void)
{
    PelorusSideData meta;
    PelorusFilmGrainSection grain; /* u64 at offset 0 -> alignment matters */
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    uint8_t *raw = NULL;
    size_t len = 0;
    size_t skew;

    fill_meta(&meta);
    memset(&grain, 0, sizeof(grain));
    grain.seed = 0xDEADBEEFCAFEULL;
    sec.id = PEL_SEC_FILMGRAIN;
    sec.data = &grain;
    sec.size = (uint32_t)sizeof(grain);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);

    /* Re-home the blob at every misalignment in one 8-byte period. */
    raw = (uint8_t *)malloc(len + 8u);
    CHECK(raw != NULL);
    if (raw == NULL) {
        pel_blob_free(blob);
        return;
    }
    for (skew = 1u; skew < 8u; skew++) {
        memcpy(raw + skew, blob, len);
        check_skewed_blob(raw + skew, len);
    }

    free(raw);
    pel_blob_free(blob);
}

/* A header_size that is not a multiple of 8 would put dir[] on a misaligned
 * start. That is corrupt framing from an untrusted producer, not a short
 * buffer, so it must be rejected rather than walked. */
static void test_unaligned_header_size(void)
{
    PelorusSideData meta;
    PelorusFilmGrainSection grain;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    PelorusSideData hdr;

    fill_meta(&meta);
    memset(&grain, 0, sizeof(grain));
    sec.id = PEL_SEC_FILMGRAIN;
    sec.data = &grain;
    sec.size = (uint32_t)sizeof(grain);
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);

    hdr = blob_header_load(blob);
    hdr.header_size = (uint16_t)(hdr.header_size + 4u);
    blob_header_store(blob, &hdr);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_FILMGRAIN, sizeof(PelorusFilmGrainSection), &p,
                                &got) == PEL_ERR_ABI);

    pel_blob_free(blob);
}

/* ===================== ABI 1.4 (ADR-0174, ADR-0175) ===================== */

/* True when the readable size `got` covers field `f` of section struct `T` (R4). */
#define FIXTURE_FIELD_OK(got, T, f) ((got) >= offsetof(T, f) + sizeof(((const T *)0)->f))

/* An 8-aligned scratch blob for the non-allocating packer (R5 alignment holds for it). */
typedef union FixtureBlob {
    uint64_t align;
    uint8_t bytes[1024];
} FixtureBlob;

/* Pack one motion section of `size` bytes; return what a consumer knowing `known` bytes reads. */
static size_t motion_readable_size_for(const PelorusMotionSection *mo, uint32_t size, size_t known)
{
    PelorusSideData meta;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    fill_meta(&meta);
    sec.id = PEL_SEC_MOTION;
    sec.data = mo;
    sec.size = size;
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION, known, &p, &got) == PEL_OK);
    pel_blob_free(blob);
    return got;
}

/* Pack one motion section of `size` bytes; return the readable size a 1.4 consumer sees. */
static size_t motion_readable_size(const PelorusMotionSection *mo, uint32_t size,
                                   PelorusMotionSection *out)
{
    PelorusSideData meta;
    PelorusPackSection sec;
    uint8_t *blob = NULL;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;

    fill_meta(&meta);
    meta.grid_cols = 8; /* vf_pelorus_mc, bsize 8 on a 64x64 frame */
    meta.grid_rows = 8;
    sec.id = PEL_SEC_MOTION;
    sec.data = mo;
    sec.size = size;
    memset(out, 0, sizeof(*out));
    CHECK(pel_blob_pack(&meta, &sec, 1, &blob, &len) == PEL_OK);
    CHECK(pel_blob_find_section(blob, len, PEL_SEC_MOTION, sizeof(PelorusMotionSection), &p,
                                &got) == PEL_OK);
    if (p != NULL && got <= sizeof(*out)) {
        memcpy(out, p, got);
    }
    pel_blob_free(blob);
    return got;
}

/* #218: the 1.4 motion section names its block edge; a 1.3 section (32 bytes) is
 * detected by its readable size, not by the value; a 1.3 consumer still parses (R4). */
static void test_motion_block_size(void)
{
    PelorusMotionSection mo;
    PelorusMotionSection got_mo;
    const uint32_t size_1_3 = (uint32_t)offsetof(PelorusMotionSection, block_size_log2);
    size_t got;

    memset(&mo, 0, sizeof(mo));
    mo.has_scene_cut = 1;
    mo.block_size_log2 = 3; /* bsize 8 */

    got = motion_readable_size(&mo, (uint32_t)sizeof(mo), &got_mo);
    CHECK(got == 36u);
    CHECK(FIXTURE_FIELD_OK(got, PelorusMotionSection, block_size_log2));
    CHECK(got_mo.block_size_log2 == 3);

    /* A 1.3 producer wrote 32 bytes: the field is absent, not a zero-valued edge. */
    got = motion_readable_size(&mo, size_1_3, &got_mo);
    CHECK(size_1_3 == 32u);
    CHECK(got == 32u);
    CHECK(!FIXTURE_FIELD_OK(got, PelorusMotionSection, block_size_log2));
    CHECK(FIXTURE_FIELD_OK(got, PelorusMotionSection, has_scene_cut) && got_mo.has_scene_cut == 1);

    /* R4: a 1.3 consumer (knows 32 bytes) reads a 1.4 blob and never sees the field. */
    CHECK(motion_readable_size_for(&mo, (uint32_t)sizeof(mo), size_1_3) == 32u);

    /* Boundary: the largest edge vf_pelorus_mc writes (bsize 32 -> 5). */
    mo.block_size_log2 = 5;
    got = motion_readable_size(&mo, (uint32_t)sizeof(mo), &got_mo);
    CHECK(got == 36u && got_mo.block_size_log2 == 5);
}

/* The non-allocating packer writes the same image as pel_blob_pack. */
static void check_pack_into_matches(const PelorusSideData *meta, const PelorusPackSection *sec)
{
    FixtureBlob fx;
    uint8_t *blob = NULL;
    size_t len = 0;
    size_t need = 0;

    CHECK(pel_blob_pack(meta, sec, 1, &blob, &len) == PEL_OK);
    /* Size query: cap 0 reports the length the image needs. */
    CHECK(pel_blob_pack_into(meta, sec, 1, NULL, 0, &need) == PEL_ERR_RANGE);
    CHECK(need == len);
    CHECK(pel_blob_pack_into(meta, sec, 1, fx.bytes, need - 1u, &need) == PEL_ERR_RANGE);
    memset(fx.bytes, 0xA5, sizeof(fx.bytes));
    CHECK(pel_blob_pack_into(meta, sec, 1, fx.bytes, sizeof(fx.bytes), &need) == PEL_OK);
    CHECK(need == len);
    CHECK(blob != NULL && memcmp(fx.bytes, blob, len) == 0);
    CHECK(fx.bytes[len] == 0xA5); /* nothing past the image is touched */
    pel_blob_free(blob);
}

static void test_pack_into(void)
{
    PelorusSideData meta;
    PelorusEncodeRecordSection rec;
    PelorusPackSection sec;
    uint8_t buf[256];
    size_t out_len = 0;

    fill_meta(&meta);
    memset(&rec, 0, sizeof(rec));
    rec.digest_alg = PEL_DIGEST_ALG_SHA256;
    sec.id = PEL_SEC_ENCODE_RECORD;
    sec.data = &rec;
    sec.size = (uint32_t)sizeof(rec);
    check_pack_into_matches(&meta, &sec);

    CHECK(pel_blob_pack_into(&meta, &sec, 1, NULL, 8, &out_len) == PEL_ERR_INVALID);
    CHECK(pel_blob_pack_into(&meta, &sec, 1, buf, sizeof(buf), NULL) == PEL_ERR_INVALID);
    CHECK(pel_blob_pack_into(NULL, &sec, 1, buf, sizeof(buf), &out_len) == PEL_ERR_INVALID);
    /* Bit 10 is not minted: still rejected after the two 1.4 bits joined. The value is
     * copied in because it is no enumerator of pel_section. */
    {
        const uint32_t unminted = 0x400u; /* 1 << 10 */
        memcpy(&sec.id, &unminted,
               sizeof(sec.id) < sizeof(unminted) ? sizeof(sec.id) : sizeof(unminted));
    }
    CHECK(pel_blob_pack_into(&meta, &sec, 1, buf, sizeof(buf), &out_len) == PEL_ERR_RANGE);
}

/* Fill a telemetry record with every scalar present and two maps on a 4x2 block grid. */
static void fill_enc_telemetry(PelorusEncTelemetrySection *t)
{
    memset(t, 0, sizeof(*t));
    t->present_mask = PEL_TLM_F_DISPLAY_INDEX | PEL_TLM_F_DECODE_INDEX | PEL_TLM_F_FRAME_BYTES |
                      PEL_TLM_F_AVG_QP | PEL_TLM_F_PICTURE_TYPE | PEL_TLM_F_KEY_FRAME |
                      PEL_TLM_F_QP_MAP | PEL_TLM_F_MODE_MAP;
    t->display_index = 4;
    t->decode_index = 1;
    t->frame_bytes = 12034;
    t->avg_qp = 30.0f;
    t->picture_type = PEL_PICTURE_P;
    t->frame_flags = 0; /* key_frame reported, and it is not one */
    t->map_cols = 4;
    t->map_rows = 2;
    t->codec = PEL_TLM_CODEC_HEVC;
    t->qp_scale = PEL_QP_SCALE_SLICE_QP;
    t->granularity = PEL_TLM_GRAN_BLOCK;
    t->block_size_log2 = 4;
    t->adapter = PEL_TLM_ADAPTER_EXTERNAL;
}

/* Append `size` bytes at the next 8-aligned blob-relative offset after `*end`; return it. */
static uint32_t fixture_append(FixtureBlob *fx, uint32_t *end, const void *data, uint32_t size)
{
    uint32_t off = (*end + 7u) & ~7u;

    memcpy(fx->bytes + PELORUS_SIDEDATA_UUID_LEN + off, data, size);
    *end = off + size;
    return off;
}

/* Producer side: pack the record without allocating, append its maps, patch the offsets
 * and total_size (the mv_field convention). Returns the blob length. */
static size_t pack_enc_telemetry(FixtureBlob *fx, PelorusEncTelemetrySection *t)
{
    static const int16_t qp_map[8] = {120, 124, 128, 132, 116, 120, 124, 1020};
    static const uint8_t mode_map[8] = {1, 2, 2, 3, 3, 2, 1, 0};
    PelorusSideData meta;
    PelorusSideData hdr;
    PelorusPackSection sec;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    uint32_t end;

    fill_meta(&meta);
    sec.id = PEL_SEC_ENC_TELEMETRY;
    sec.data = t;
    sec.size = (uint32_t)sizeof(*t);
    memset(fx->bytes, 0, sizeof(fx->bytes));
    if (pel_blob_pack_into(&meta, &sec, 1, fx->bytes, sizeof(fx->bytes), &len) != PEL_OK) {
        CHECK(!"pel_blob_pack_into refused the telemetry section");
        return 0; /* every later parse of a 0-byte blob fails its own CHECK */
    }
    end = (uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN);
    t->qp_map_size = (uint32_t)sizeof(qp_map);
    t->qp_map_offset = fixture_append(fx, &end, qp_map, t->qp_map_size);
    t->mode_map_size = (uint32_t)sizeof(mode_map);
    t->mode_map_offset = fixture_append(fx, &end, mode_map, t->mode_map_size);

    CHECK(pel_blob_find_section(fx->bytes, len, PEL_SEC_ENC_TELEMETRY, sizeof(*t), &p, &got) ==
          PEL_OK);
    if (p != NULL) { /* patch the packed record in place: it lives inside fx->bytes */
        memcpy(fx->bytes + ((const uint8_t *)p - fx->bytes), t, sizeof(*t));
    }
    hdr = blob_header_load(fx->bytes);
    hdr.total_size = end;
    blob_header_store(fx->bytes, &hdr);
    return (size_t)PELORUS_SIDEDATA_UUID_LEN + end;
}

static void check_enc_telemetry_maps(const uint8_t *blob, size_t len,
                                     const PelorusEncTelemetrySection *t)
{
    const void *qp = NULL;
    const void *mode = NULL;
    const void *bits = NULL;
    int16_t q = 0;

    CHECK(pel_blob_map(blob, len, t->qp_map_offset, t->qp_map_size, 8u, 2u, &qp) == PEL_OK);
    CHECK(pel_blob_map(blob, len, t->mode_map_offset, t->mode_map_size, 8u, 1u, &mode) == PEL_OK);
    if (qp != NULL && mode != NULL) {
        memcpy(&q, (const uint8_t *)qp + 7u * sizeof(q), sizeof(q));
        CHECK(q == 1020); /* AV1 qindex 255 in Q2 still fits int16 */
        CHECK(((const uint8_t *)mode)[3] == PEL_BLOCK_MODE_SKIP);
    }
    /* "not reported" is the clear bit: the bits_map offset/size stay zero. */
    CHECK((t->present_mask & PEL_TLM_F_BITS_MAP) == 0u);
    CHECK(t->bits_map_offset == 0u && t->bits_map_size == 0u);
    CHECK(pel_blob_map(blob, len, t->bits_map_offset, t->bits_map_size, 8u, 4u, &bits) ==
          PEL_ERR_ABI);
}

/* PEL_SEC_ENC_TELEMETRY (i): pack, parse, read both maps; an older consumer that knows
 * only present_mask still parses (R4). */
static void test_enc_telemetry_roundtrip(void)
{
    FixtureBlob fx;
    PelorusEncTelemetrySection t;
    const void *p = NULL;
    size_t got = 0;
    size_t len;

    fill_enc_telemetry(&t);
    len = pack_enc_telemetry(&fx, &t);
    CHECK(pel_blob_find_section(fx.bytes, len, PEL_SEC_ENC_TELEMETRY, sizeof(t), &p, &got) ==
          PEL_OK);
    CHECK(got == 104u);
    if (p != NULL && got == sizeof(t)) {
        PelorusEncTelemetrySection r;
        memcpy(&r, p, sizeof(r));
        CHECK(r.present_mask == t.present_mask && r.frame_bytes == 12034u);
        CHECK(r.avg_qp == 30.0f && r.codec == PEL_TLM_CODEC_HEVC && r.adapter == 10u);
        /* present_mask: psnr_y not reported (bit clear) is not a reported 0 dB. */
        CHECK((r.present_mask & PEL_TLM_F_PSNR_Y) == 0u && r.psnr_y == 0.0f);
        CHECK((r.present_mask & PEL_TLM_F_KEY_FRAME) != 0u && r.frame_flags == 0u);
        check_enc_telemetry_maps(fx.bytes, len, &r);
    }
    CHECK(pel_blob_find_section(fx.bytes, len, PEL_SEC_ENC_TELEMETRY, 8, &p, &got) == PEL_OK);
    CHECK(got == 8u);
}

/* pel_blob_map reader checks 3 to 5, in order, plus framing and argument guards. */
static void check_blob_map_rejects(const uint8_t *blob, size_t len, uint32_t off, uint32_t total)
{
    const void *p = NULL;

    CHECK(pel_blob_map(blob, len, off, 16u, 7u, 2u, &p) == PEL_ERR_ABI);      /* check 3 */
    CHECK(pel_blob_map(blob, len, off + 4u, 16u, 8u, 2u, &p) == PEL_ERR_ABI); /* check 4 */
    CHECK(p == NULL);
    CHECK(pel_blob_map(blob, len, total + 8u, 8u, 8u, 1u, &p) == PEL_ERR_TRUNCATED); /* 5 */
    CHECK(pel_blob_map(blob, len, 0xFFFFFFF8u, 8u, 8u, 1u, &p) == PEL_ERR_TRUNCATED);
    /* elem_count * elem_size wraps 32 bits to 0: computed in 64 bits, so ABI, not OK. */
    CHECK(pel_blob_map(blob, len, off, 0u, 0x10000u, 0x10000u, &p) == PEL_ERR_ABI);
    CHECK(pel_blob_map(blob, len, off, 16u, 0u, 2u, &p) == PEL_ERR_INVALID);
    CHECK(pel_blob_map(NULL, len, off, 16u, 8u, 2u, &p) == PEL_ERR_INVALID);
    CHECK(pel_blob_map(blob, len, off, 16u, 8u, 2u, NULL) == PEL_ERR_INVALID);
    /* A received length shorter than total_size fails the framing. */
    CHECK(pel_blob_map(blob, len - 1u, off, 16u, 8u, 2u, &p) == PEL_ERR_TRUNCATED);
}

static void test_blob_map_bounds(void)
{
    FixtureBlob fx;
    PelorusEncTelemetrySection t;
    uint8_t foreign[96];
    const void *p = NULL;
    size_t len;
    uint32_t total;

    fill_enc_telemetry(&t);
    len = pack_enc_telemetry(&fx, &t);
    if (len == 0u) {
        return; /* the packer refused; pack_enc_telemetry already counted the failure */
    }
    total = (uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN);
    check_blob_map_rejects(fx.bytes, len, t.qp_map_offset, total);

    /* Boundary: a map that ends exactly at total_size is inside; one byte more is not. */
    CHECK(pel_blob_map(fx.bytes, len, t.mode_map_offset, 8u, 8u, 1u, &p) == PEL_OK);
    CHECK(t.mode_map_offset + 8u == total);
    CHECK(pel_blob_map(fx.bytes, len, t.mode_map_offset, 9u, 9u, 1u, &p) == PEL_ERR_TRUNCATED);

    memset(foreign, 0xAB, sizeof(foreign));
    CHECK(pel_blob_map(foreign, sizeof(foreign), 64u, 8u, 8u, 1u, &p) == PEL_ERR_ABSENT);
}

/* SHA-256("abc") from FIPS 180-4, as the raw digest a producer stores. */
static const uint8_t fixture_digest_abc[32] = {
    0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
    0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad};

static const char fixture_digest_abc_text[] =
    "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

/* Pack a record section with its locator appended; return the blob length. */
static size_t pack_encode_record(FixtureBlob *fx, PelorusEncodeRecordSection *rec,
                                 const char *locator)
{
    PelorusSideData meta;
    PelorusSideData hdr;
    PelorusPackSection sec;
    size_t len = 0;
    const void *p = NULL;
    size_t got = 0;
    uint32_t end;

    fill_meta(&meta);
    memset(rec, 0, sizeof(*rec));
    memcpy(rec->digest, fixture_digest_abc, sizeof(rec->digest));
    rec->digest_alg = PEL_DIGEST_ALG_SHA256;
    rec->locator_kind = PEL_LOCATOR_MEDIA_PATH;
    rec->record_major = (uint8_t)PEL_ENCODE_RECORD_MAJOR;
    sec.id = PEL_SEC_ENCODE_RECORD;
    sec.data = rec;
    sec.size = (uint32_t)sizeof(*rec);
    memset(fx->bytes, 0, sizeof(fx->bytes));
    if (pel_blob_pack_into(&meta, &sec, 1, fx->bytes, sizeof(fx->bytes), &len) != PEL_OK) {
        CHECK(!"pel_blob_pack_into refused the encode-record section");
        return 0;
    }
    end = (uint32_t)(len - PELORUS_SIDEDATA_UUID_LEN);
    rec->locator_size = (uint32_t)strlen(locator); /* UTF-8, no NUL */
    rec->locator_offset = fixture_append(fx, &end, locator, rec->locator_size);
    CHECK(pel_blob_find_section(fx->bytes, len, PEL_SEC_ENCODE_RECORD, sizeof(*rec), &p, &got) ==
          PEL_OK);
    if (p != NULL) {
        memcpy(fx->bytes + ((const uint8_t *)p - fx->bytes), rec, sizeof(*rec));
    }
    hdr = blob_header_load(fx->bytes);
    hdr.total_size = end;
    blob_header_store(fx->bytes, &hdr);
    return (size_t)PELORUS_SIDEDATA_UUID_LEN + end;
}

static void check_digest_text_guards(const PelorusEncodeRecordSection *rec)
{
    PelorusEncodeRecordSection bad;
    char text[PEL_DIGEST_TEXT_SIZE];

    memset(text, 'x', sizeof(text));
    CHECK(pel_encode_record_digest_text(rec, sizeof(*rec), text, sizeof(text) - 1u) ==
          PEL_ERR_RANGE);
    CHECK(text[0] == 'x'); /* untouched on error */
    CHECK(pel_encode_record_digest_text(rec, sizeof(*rec) - 1u, text, sizeof(text)) ==
          PEL_ERR_INVALID);
    CHECK(pel_encode_record_digest_text(NULL, sizeof(*rec), text, sizeof(text)) == PEL_ERR_INVALID);
    CHECK(pel_encode_record_digest_text(rec, sizeof(*rec), NULL, sizeof(text)) == PEL_ERR_INVALID);
    memcpy(&bad, rec, sizeof(bad));
    bad.digest_alg = 0; /* 0 is invalid */
    CHECK(pel_encode_record_digest_text(&bad, sizeof(bad), text, sizeof(text)) == PEL_ERR_INVALID);
}

/* PEL_SEC_ENCODE_RECORD (j): the digest text is what VMAFx's
 * vmafx_context_set_encode_record() accepts; the locator reads through pel_blob_map. */
static void test_encode_record_section(void)
{
    static const char locator[] = "clip.encode-record.json";
    FixtureBlob fx;
    PelorusEncodeRecordSection rec;
    char text[PEL_DIGEST_TEXT_SIZE];
    const void *p = NULL;
    const void *loc = NULL;
    size_t got = 0;
    size_t len;

    len = pack_encode_record(&fx, &rec, locator);
    CHECK(pel_blob_find_section(fx.bytes, len, PEL_SEC_ENCODE_RECORD, sizeof(rec), &p, &got) ==
          PEL_OK);
    CHECK(got == 48u);
    if (p != NULL && got == sizeof(rec)) {
        PelorusEncodeRecordSection r;
        memcpy(&r, p, sizeof(r));
        CHECK(pel_encode_record_digest_text(&r, got, text, sizeof(text)) == PEL_OK);
        CHECK(strlen(text) == 71u && strcmp(text, fixture_digest_abc_text) == 0);
        CHECK(r.locator_size <= PEL_ENCODE_RECORD_LOCATOR_MAX && r.record_major == 1u);
        CHECK(pel_blob_map(fx.bytes, len, r.locator_offset, r.locator_size, r.locator_size, 1u,
                           &loc) == PEL_OK);
        CHECK(loc != NULL && memcmp(loc, locator, sizeof(locator) - 1u) == 0);
        check_digest_text_guards(&r);
    }
}

int main(void)
{
    test_roundtrip();
    test_forward_compat();
    test_abi_major_mismatch();
    test_foreign_buffer();
    test_header_only();
    test_truncation();
    test_misaligned_offset();
    test_misaligned_blob_base();
    test_unaligned_header_size();
    test_pack_size_overflow();
    test_qp_report_roundtrip();
    test_motion_conf_roundtrip();
    test_complexity_roundtrip();
    test_qp_report_fold();
    test_fixture_file_safety();
    test_x265_csv_reader();
    test_x265_csv_reader_guards();
    test_deband_params();
    test_motion_block_size();
    test_pack_into();
    test_enc_telemetry_roundtrip();
    test_blob_map_bounds();
    test_encode_record_section();

    if (g_fail != 0) {
        (void)fprintf(stderr, "%d check(s) failed\n", g_fail);
        return EXIT_FAILURE;
    }
    (void)printf("interop: all checks passed (libpelorus %s, ABI %u.%u)\n",
                 pelorus_version_string(), PELORUS_ABI_MAJOR, PELORUS_ABI_MINOR);
    return EXIT_SUCCESS;
}

/* NOLINTEND(modernize-use-nullptr) */
