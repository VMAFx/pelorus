/**
 *
 *  Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

/*
 * sha256.h — private SHA-256 (FIPS 180-4) for the encode-record digest
 * (ADR-0175). Not a public libpelorus header: the public surface is
 * pel_encode_record_hash() in pelorus/encode_record.h. A content digest, not a
 * security primitive (no key, no constant-time requirement).
 */
#ifndef PELORUS_SRC_SHA256_H
#define PELORUS_SRC_SHA256_H

#include <stddef.h>
#include <stdint.h>

#include "pelorus/pelorus.h"

#define PEL_SHA256_DIGEST_SIZE 32u

/* Streaming state: init, update any number of times, final once. */
typedef struct PelSha256 {
    uint32_t state[8];
    uint8_t block[64];
    size_t block_len; /* bytes waiting in `block` */
    uint64_t total;   /* bytes hashed so far */
} PelSha256;

/* `sha` and `digest` are non-NULL (internal API, every caller holds them on its
 * stack). `data` may be NULL only when `len` is 0: NULL with len > 0 returns
 * PEL_ERR_INVALID and hashes nothing, so it can never pass for the empty
 * message (HISS-07: callers check the result). */
void pel_sha256_init(PelSha256 *sha);
pel_result pel_sha256_update(PelSha256 *sha, const void *data, size_t len);
/* The digest of everything passed to update; `sha` must be initialised again
 * before it is reused. */
void pel_sha256_final(PelSha256 *sha, uint8_t digest[PEL_SHA256_DIGEST_SIZE]);

/* One-shot digest of `data[0..len)`; PEL_ERR_INVALID (digest untouched) for NULL
 * data with len > 0. */
pel_result pel_sha256(const void *data, size_t len, uint8_t digest[PEL_SHA256_DIGEST_SIZE]);

#endif /* PELORUS_SRC_SHA256_H */
