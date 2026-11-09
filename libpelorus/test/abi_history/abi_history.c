/**
 *  Copyright 2026 Lusoris
 *
 * SPDX-FileCopyrightText: 2026 Lusoris
 * SPDX-License-Identifier: EUPL-1.2
 */

/* NOLINTNEXTLINE(bugprone-reserved-identifier,cert-dcl37-c,cert-dcl51-cpp): test_path_safety.h POSIX visibility */
#define _XOPEN_SOURCE 700

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pelorus/interop.h"
#include "../test_path_safety.h"
#include "abi_history.h"

static const uint8_t k_carrier_uuid[16] = {0x3f, 0x9b, 0x37, 0xb8, 0xfd, 0x9a, 0x46, 0x21,
                                           0x92, 0x0e, 0x9b, 0x78, 0xb5, 0x5c, 0xf9, 0xb5};

/**
 * Print binary byte buffer to stdout as a single lowercase hexadecimal line.
 */
static void emit_hex(const uint8_t *data, size_t len)
{
    static const char hex_chars[] = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        (void)putchar(hex_chars[((unsigned int)data[i] >> 4u) & 0x0fu]);
        (void)putchar(hex_chars[(unsigned int)data[i] & 0x0fu]);
    }
    (void)putchar('\n');
}

/**
 * Parse an unsigned decimal integer string of at most 10 digits into uint32_t.
 * Returns 1 on success, 0 on invalid characters, excess length, or overflow.
 */
static int parse_uint32(const char *s, uint32_t *out)
{
    uint64_t val = 0;
    size_t i;
    if (s == NULL || *s == '\0') {
        return 0;
    }
    for (i = 0; i < 10u && s[i] != '\0'; i++) {
        if (s[i] < '0' || s[i] > '9') {
            return 0;
        }
        val = val * 10u + (uint64_t)(s[i] - '0');
    }
    if (s[i] != '\0' || val > UINT32_MAX) {
        return 0;
    }
    *out = (uint32_t)val;
    return 1;
}

/**
 * Read the entire contents of a binary file into a preallocated buffer.
 * Returns 0 on success, 2 on file opening or read error.
 */
static int read_file_to_buf(const char *path, uint8_t *buf, size_t cap, size_t *out_len)
{
    FILE *fp = test_fopen_safe(path, "rb");
    size_t n;
    if (fp == NULL) {
        return 2;
    }
    n = fread(buf, 1, cap, fp);
    if (ferror(fp) || !feof(fp)) {
        (void)fclose(fp);
        return 2;
    }
    (void)fclose(fp);
    *out_len = n;
    return 0;
}

/**
 * Compare file contents against expected memory buffer.
 * Returns 0 on exact match, 1 on file error, size difference, or mismatch.
 */
static int check_file_match(const char *path, const uint8_t *expected, size_t expected_len)
{
    AlignedBlob16k buf = {0};
    size_t file_len = 0;
    int rc = read_file_to_buf(path, buf.bytes, sizeof(buf.bytes), &file_len);
    if (rc != 0) {
        (void)fprintf(stderr, "FAIL: missing fixture %s\n", path);
        return 1;
    }
    if (file_len != expected_len || memcmp(buf.bytes, expected, expected_len) != 0) {
        (void)fprintf(stderr, "FAIL: %s does not match writer output\n", path);
        return 1;
    }
    return 0;
}

/**
 * Pack the history blob and verify that it matches the plain fixture file.
 * Returns 0 on success, 1 on mismatch or packing failure.
 */
static int check_plain_writer(const char *plain_path, AlignedBlob16k *plain, size_t *plain_len)
{
    if (abih_pack_history_blob(plain->bytes, sizeof(plain->bytes), plain_len) != PEL_OK) {
        (void)fprintf(stderr, "FAIL: pack_history_blob failed\n");
        return 1;
    }
    if (check_file_match(plain_path, plain->bytes, *plain_len) != 0) {
        return 1;
    }
    return 0;
}

#if PELORUS_ABI_MINOR >= 5
/**
 * Check plain and carrier writer output against fixture files.
 * Returns 0 on success, 1 on mismatch or encoding failure.
 */
static int do_check_writer(const char *plain_path, const char *carrier_path)
{
    AlignedBlob16k plain = {0};
    AlignedBlob32k carrier = {0};
    size_t plain_len = 0;
    size_t carrier_len = 0;
    pel_result cr;

    if (check_plain_writer(plain_path, &plain, &plain_len) != 0) {
        return 1;
    }
    cr = pel_blob_carrier_encode(plain.bytes, plain_len, carrier.bytes, sizeof(carrier.bytes),
                                 &carrier_len);
    if (cr != PEL_OK) {
        (void)fprintf(stderr, "FAIL: carrier encode failed (rc=%d)\n", (int)cr);
        return 1;
    }
    if (check_file_match(carrier_path, carrier.bytes, carrier_len) != 0) {
        return 1;
    }
    return 0;
}
#else
/**
 * Check plain writer output against fixture file.
 * Returns 0 on success, 1 on mismatch.
 */
static int do_check_writer(const char *plain_path)
{
    AlignedBlob16k plain = {0};
    size_t plain_len = 0;

    return check_plain_writer(plain_path, &plain, &plain_len);
}
#endif

/**
 * Verify carrier container wrapping and extract inner payload.
 */
static int handle_carrier_blob(const uint8_t *blob, size_t len)
{
#if PELORUS_ABI_MINOR < 5
    const void *p = NULL;
    size_t got = 0;
    if (pel_blob_is_present(blob, len) == 0 &&
        pel_blob_find_section(blob, len, PEL_SEC_BANDING, sizeof(PelorusBandingSection), &p,
                              &got) != PEL_OK) {
        return 0;
    }
    (void)fprintf(stderr, "FAIL: carrier not ignored by pre-1.5 reader\n");
    return 1;
#else
    const uint8_t *unwrapped = NULL;
    size_t unwrapped_len = 0;
    AlignedBlob32k scratch = {0};
    pel_result cr = pel_blob_unwrap(blob, len, scratch.bytes, sizeof(scratch.bytes), &unwrapped,
                                    &unwrapped_len);
    if (cr != PEL_OK) {
        (void)fprintf(stderr, "FAIL: pel_blob_unwrap error %d\n", (int)cr);
        return 1;
    }
    return abih_read_and_verify_blob(unwrapped, unwrapped_len);
#endif
}

/**
 * Read a blob file, optionally apply single-bit flip, unwrap carrier, and verify.
 * Returns 0 on verification pass, 1 on content failure, 2 on I/O or flip error.
 */
static int do_read_blob(const char *path, int has_flip, uint32_t flip_off)
{
    AlignedBlob32k buf = {0};
    size_t len = 0;
    int rc = read_file_to_buf(path, buf.bytes, sizeof(buf.bytes), &len);
    if (rc != 0) {
        (void)fprintf(stderr, "IO error: %s\n", path);
        return 2;
    }
    if (has_flip) {
        if ((size_t)flip_off >= len) {
            (void)fprintf(stderr, "Flip offset %u >= len %zu\n", flip_off, len);
            return 2;
        }
        buf.bytes[flip_off] ^= (uint8_t)0x01u;
    }
    if (len >= 16 && memcmp(buf.bytes, k_carrier_uuid, 16) == 0) {
        return handle_carrier_blob(buf.bytes, len);
    }
    return abih_read_and_verify_blob(buf.bytes, len);
}

/**
 * Handle "emit" CLI command, packing plain or carrier blob and printing hex.
 * Returns 0 on success, 1 on packing/carrier error, 2 on usage error.
 */
static int do_emit(int argc, char **argv)
{
    AlignedBlob16k plain = {0};
    size_t plain_len = 0;
    if (argc != 3) {
        (void)fprintf(stderr, "Usage: abi_history emit plain|carrier\n");
        return 2;
    }
    if (abih_pack_history_blob(plain.bytes, sizeof(plain.bytes), &plain_len) != PEL_OK) {
        return 1;
    }
    if (strcmp(argv[2], "plain") == 0) {
        emit_hex(plain.bytes, plain_len);
        return 0;
    }
    if (strcmp(argv[2], "carrier") == 0) {
#if PELORUS_ABI_MINOR >= 5
        AlignedBlob32k carrier = {0};
        size_t carrier_len = 0;
        if (pel_blob_carrier_encode(plain.bytes, plain_len, carrier.bytes, sizeof(carrier.bytes),
                                    &carrier_len) != PEL_OK) {
            return 1;
        }
        emit_hex(carrier.bytes, carrier_len);
        return 0;
#else
        (void)fprintf(stderr, "carrier mode requires PELORUS_ABI_MINOR >= 5 (current %u)\n",
                      (unsigned)PELORUS_ABI_MINOR);
        return 2;
#endif
    }
    (void)fprintf(stderr, "Usage: abi_history emit plain|carrier\n");
    return 2;
}

/**
 * Dispatch check-writer CLI invocation to appropriate minor-gated checker.
 */
static int handle_check_writer(int argc, char **argv)
{
#if PELORUS_ABI_MINOR >= 5
    if (argc != 4) {
        (void)fprintf(stderr, "Usage: abi_history check-writer <plain.bin> <carrier.bin>\n");
        return 2;
    }
    return do_check_writer(argv[2], argv[3]);
#else
    if (argc != 3) {
        (void)fprintf(stderr, "Usage: abi_history check-writer <plain.bin>\n");
        return 2;
    }
    return do_check_writer(argv[2]);
#endif
}

/**
 * Parse arguments for read and expect-reject commands.
 * Returns 0 on success, 2 on usage error.
 */
static int parse_read_args(int argc, char **argv, const char *subcmd, int *has_flip,
                           uint32_t *flip_off)
{
    int ok = argc == 3 || argc == 5;
    if (ok && argc == 5) {
        ok = strcmp(argv[3], "--flip") == 0 && parse_uint32(argv[4], flip_off);
        *has_flip = ok;
    }
    if (!ok) {
        (void)fprintf(
            stderr,
            "Usage: abi_history %s <blob.bin> [--flip <byte offset, 0..4294967295, at most 10 digits>]\n",
            subcmd);
        return 2;
    }
    return 0;
}

/**
 * Parse arguments and dispatch blob read/verification command.
 */
static int handle_read(int argc, char **argv)
{
    int has_flip = 0;
    uint32_t flip_off = 0;
    int rc = parse_read_args(argc, argv, "read", &has_flip, &flip_off);
    if (rc != 0) {
        return rc;
    }
    return do_read_blob(argv[2], has_flip, flip_off);
}

/**
 * Parse arguments and dispatch expect-reject verification command.
 * Inverts reader rejection into test success (rc 0).
 */
static int handle_expect_reject(int argc, char **argv)
{
    int has_flip = 0;
    uint32_t flip_off = 0;
    int rc = parse_read_args(argc, argv, "expect-reject", &has_flip, &flip_off);
    if (rc != 0) {
        return rc;
    }
    /* The blob path goes to printf through a named local: Codacy's Opengrep
     * rule insecure-use-printf-fn reports argv[N] passed straight to printf
     * even with a literal format (.semgrep.yml pelorus-c-printf-argv). */
    const char *blob_path = argv[2];
    rc = do_read_blob(blob_path, has_flip, flip_off);
    if (rc == 1) {
        (void)printf("ok: reader rejected %s\n", blob_path);
        return 0;
    }
    if (rc == 0) {
        (void)fprintf(stderr, "FAIL: reader accepted %s; expect-reject wants a rejection\n",
                      blob_path);
        return 1;
    }
    return rc;
}

/**
 * Main entry point for abi_history test tool (emit|check-writer|read|expect-reject).
 * Returns 0 on success, 1 on verification failure, 2 on usage or I/O error.
 */
int main(int argc, char **argv)
{
    if (argc < 2) {
        (void)fprintf(stderr, "Usage: abi_history emit|check-writer|read|expect-reject ...\n");
        return 2;
    }
    if (strcmp(argv[1], "emit") == 0) {
        return do_emit(argc, argv);
    }
    if (strcmp(argv[1], "check-writer") == 0) {
        return handle_check_writer(argc, argv);
    }
    if (strcmp(argv[1], "read") == 0) {
        return handle_read(argc, argv);
    }
    if (strcmp(argv[1], "expect-reject") == 0) {
        return handle_expect_reject(argc, argv);
    }
    (void)fprintf(stderr, "Unknown mode: %s\n", argv[1]);
    return 2;
}
