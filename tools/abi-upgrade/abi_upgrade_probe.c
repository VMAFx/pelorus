/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

#include "pelorus/interop.h"
#include "pelorus/pelorus.h"

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <sys/stat.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

typedef struct SectionDesc {
    size_t size;
    enum pel_section id;
    uint16_t min_minor;
} SectionDesc;

static const SectionDesc k_sections[] = {
    {sizeof(PelorusBandingSection), PEL_SEC_BANDING, 0u},
    {sizeof(PelorusVarianceSection), PEL_SEC_VARIANCE, 0u},
    {sizeof(PelorusDenoiseSection), PEL_SEC_DENOISE, 0u},
    {sizeof(PelorusFilmGrainSection), PEL_SEC_FILMGRAIN, 0u},
    {sizeof(PelorusMotionSection), PEL_SEC_MOTION, 0u},
    {sizeof(PelorusQpReportSection), PEL_SEC_QPREPORT, 1u},
    {sizeof(PelorusMotionConfSection), PEL_SEC_MOTION_CONF, 2u},
    {sizeof(PelorusComplexitySection), PEL_SEC_COMPLEXITY, 3u},
    {sizeof(PelorusEncTelemetrySection), PEL_SEC_ENC_TELEMETRY, 4u},
    {sizeof(PelorusEncodeRecordSection), PEL_SEC_ENCODE_RECORD, 4u},
#if PELORUS_ABI_MINOR >= 6
    {sizeof(PelorusQualitySection), PEL_SEC_QUALITY, 6u},
#endif
};

#define NUM_SECTIONS (sizeof(k_sections) / sizeof(k_sections[0]))

static uint8_t pattern_byte(uint32_t sec_id, size_t off)
{
    uint32_t v = (((sec_id * 31u) ^ (uint32_t)(off * 17u + 1u)) & 0xFFu);
    return (uint8_t)(v == 0u ? 0x5Au : v);
}

static FILE *open_write_exclusive(const char *path)
{
#ifdef _WIN32
    const int flags = _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY;
#ifdef _MSC_VER
    int fd = -1;

    if (_sopen_s(&fd, path, flags, _SH_DENYNO, _S_IREAD | _S_IWRITE) != 0) {
        fd = -1;
    }
#else
    const int fd = _open(path, flags, _S_IREAD | _S_IWRITE);
#endif
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    fp = _fdopen(fd, "wb");
    if (fp == NULL) {
        (void)_close(fd);
    }
    return fp;
#else
    /* O_EXCL fails on any existing name, links included (POSIX open());
     * O_NOFOLLOW states the same intent for the final component. */
    const int fd =
        open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, S_IRUSR | S_IWUSR);
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    fp = fdopen(fd, "wb");
    if (fp == NULL) {
        (void)close(fd);
    }
    return fp;
#endif
}

static int mode_pack(const char *out_path)
{
    PelorusSideData meta;
    PelorusPackSection pack_secs[NUM_SECTIONS];
    uint8_t sec_data[NUM_SECTIONS][256];
    uint8_t *blob = NULL;
    size_t blob_len = 0;
    pel_result rc;
    FILE *f = NULL;
    size_t written;
    size_t i;

    memset(&meta, 0, sizeof(meta));
    meta.frame_pts = 1000u;
    meta.plane_layout = PEL_LAYOUT_420;
    meta.bit_depth = 10u;
    meta.grid_cols = 16u;
    meta.grid_rows = 9u;
    /* PEL_FOURCC is the public interop.h macro; its char literals promote to int. */
    meta.producer_id = PEL_FOURCC('P', 'L', 'R', 'S'); // NOLINT(bugprone-signed-bitwise)

    for (i = 0; i < NUM_SECTIONS; i++) {
        if (k_sections[i].size > sizeof(sec_data[i])) {
            (void)fprintf(stderr, "section %u size exceeds buffer\n",
                          (unsigned int)k_sections[i].id);
            return 1;
        }
        for (size_t off = 0; off < k_sections[i].size; off++) {
            sec_data[i][off] = pattern_byte((uint32_t)k_sections[i].id, off);
        }
        pack_secs[i].id = k_sections[i].id;
        pack_secs[i].data = sec_data[i];
        pack_secs[i].size = (uint32_t)k_sections[i].size;
    }

    rc = pel_blob_pack(&meta, pack_secs, (int)NUM_SECTIONS, &blob, &blob_len);
    if (rc != PEL_OK || blob == NULL) {
        (void)fprintf(stderr, "pel_blob_pack failed: %d\n", (int)rc);
        return 1;
    }

    f = open_write_exclusive(out_path);
    if (f == NULL) {
        (void)fprintf(stderr, "open wb failed for %s\n", out_path);
        pel_blob_free(blob);
        return 1;
    }

    written = fwrite(blob, 1, blob_len, f);
    if (fclose(f) != 0 || written != blob_len) {
        (void)fprintf(stderr, "write or close failed for %s\n", out_path);
        pel_blob_free(blob);
        return 1;
    }

    pel_blob_free(blob);
    return 0;
}

static FILE *open_read_regular(const char *path, size_t *out_size)
{
#ifdef _WIN32
    const int flags = _O_RDONLY | _O_BINARY;
#ifdef _MSC_VER
    int fd = -1;

    if (_sopen_s(&fd, path, flags, _SH_DENYNO, 0) != 0) {
        fd = -1;
    }
#else
    const int fd = _open(path, flags);
#endif
    struct _stat st;
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    if (_fstat(fd, &st) != 0 || (st.st_mode & _S_IFMT) != _S_IFREG || st.st_size <= 0) {
        (void)_close(fd);
        return NULL;
    }
    fp = _fdopen(fd, "rb");
    if (fp == NULL) {
        (void)_close(fd);
        return NULL;
    }
    *out_size = (size_t)st.st_size;
    return fp;
#else
    const int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    struct stat st;
    FILE *fp;

    if (fd < 0) {
        return NULL;
    }
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 0) {
        (void)close(fd);
        return NULL;
    }
    fp = fdopen(fd, "rb");
    if (fp == NULL) {
        (void)close(fd);
        return NULL;
    }
    *out_size = (size_t)st.st_size;
    return fp;
#endif
}

static int read_file_bytes(const char *in_path, uint8_t **out_buf, size_t *out_size)
{
    size_t sz = 0;
    FILE *f = open_read_regular(in_path, &sz);
    size_t nread;
    uint8_t *buf = NULL;

    if (f == NULL) {
        (void)fprintf(stderr, "open rb failed for %s\n", in_path);
        return 1;
    }
    buf = (uint8_t *)malloc(sz);
    if (buf == NULL) {
        (void)fclose(f);
        return 1;
    }
    nread = fread(buf, 1, sz, f);
    (void)fclose(f);
    if (nread != sz) {
        free(buf);
        return 1;
    }
    *out_buf = buf;
    *out_size = sz;
    return 0;
}

static int check_section_bytes(const uint8_t *bytes, uint32_t sec_id, size_t len)
{
    for (size_t off = 0; off < len; off++) {
        if (bytes[off] != pattern_byte(sec_id, off)) {
            return 1;
        }
    }
    return 0;
}

static int mode_read(const char *in_path)
{
    uint8_t *blob = NULL;
    size_t blob_len = 0;
    PelorusSideData hdr;
    uint16_t producer_minor = 0;
    int failed = 0;
    size_t i;

    if (read_file_bytes(in_path, &blob, &blob_len) != 0) {
        return 1;
    }

    if (blob_len < PELORUS_SIDEDATA_UUID_LEN + sizeof(hdr) ||
        !pel_blob_is_present(blob, blob_len)) {
        (void)fprintf(stderr, "blob not present or truncated in %s\n", in_path);
        free(blob);
        return 1;
    }

    memcpy(&hdr, blob + PELORUS_SIDEDATA_UUID_LEN, sizeof(hdr));
    producer_minor = hdr.abi_minor;

    for (i = 0; i < NUM_SECTIONS; i++) {
        const void *p = NULL;
        size_t got = 0;
        pel_result rc =
            pel_blob_find_section(blob, blob_len, k_sections[i].id, k_sections[i].size, &p, &got);
        if (rc == PEL_OK) {
            if (p == NULL || got != k_sections[i].size ||
                check_section_bytes((const uint8_t *)p, (uint32_t)k_sections[i].id, got) != 0) {
                (void)printf("section=%u status=mismatch size=%zu\n",
                             (unsigned int)k_sections[i].id, got);
                failed = 1;
            } else {
                (void)printf("section=%u status=ok size=%zu\n", (unsigned int)k_sections[i].id,
                             got);
            }
        } else if (rc == PEL_ERR_ABSENT) {
            if (k_sections[i].min_minor > producer_minor) {
                (void)printf("section=%u status=absent size=0\n", (unsigned int)k_sections[i].id);
            } else {
                (void)printf("section=%u status=mismatch size=0\n", (unsigned int)k_sections[i].id);
                (void)fprintf(stderr, "section %u unexpectedly absent\n",
                              (unsigned int)k_sections[i].id);
                failed = 1;
            }
        } else {
            (void)fprintf(stderr, "section %u find_section error: %d\n",
                          (unsigned int)k_sections[i].id, (int)rc);
            failed = 1;
        }
    }

    free(blob);
    return failed != 0 ? 1 : 0;
}

/* Blob names the probe opens, relative to its working directory. The command
 * line selects one by exact match and the probe opens the table's own string,
 * so no command-line text reaches open(): a path, "..", or an unknown name is
 * a usage error. scripts/test-abi-upgrade.sh runs the probe from its build
 * directory and plants a relative escape and an absolute path. */
static const char *const k_blob_names[] = {
    "self_test_valid.bin", "self_test_corrupt.bin", "self_test_trunc.bin",
    "blob_new.bin",        "blob_old.bin",
};

static const char *known_blob_name(const char *arg)
{
    for (size_t i = 0; i < sizeof(k_blob_names) / sizeof(k_blob_names[0]); i++) {
        if (strcmp(arg, k_blob_names[i]) == 0) {
            return k_blob_names[i];
        }
    }
    return NULL;
}

int main(int argc, char **argv)
{
    const char *name;

    if (argc != 3) {
        (void)fprintf(stderr, "usage: %s <pack|read> <blob name>\n", argv[0]);
        return 2;
    }
    name = known_blob_name(argv[2]);
    if (name == NULL) {
        (void)fprintf(stderr, "unknown blob name (expected a name from k_blob_names)\n");
        return 2;
    }
    if (strcmp(argv[1], "pack") == 0) {
        return mode_pack(name);
    }
    if (strcmp(argv[1], "read") == 0) {
        return mode_read(name);
    }
    (void)fprintf(stderr, "unknown mode '%s' (expected 'pack' or 'read')\n", argv[1]);
    return 2;
}
