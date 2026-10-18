/*
 * Copyright 2026 Lusoris
 *
 * This file is part of FFmpeg.
 *
 * FFmpeg is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by the
 * Free Software Foundation; either version 2.1 of the License, or (at
 * your option) any later version.
 *
 * FFmpeg is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public
 * License for more details.
 */

/*
 * Classify user data unregistered (UDU) SEI payloads for pelorus_fgs
 * (ffmpeg-patches/files/h265_pelorus_fgs_bsf.c, ADR-0210).
 *
 * libx265 (every release since 2.1, commit d4037be7a in x265 sei.h
 * SEIuserDataUnregistered::writeSEI) writes its own hard-coded UUID
 * 2ca2de09-b517-47db-bb55-a4fe7fc2fc4e as the outer uuid_iso_iec_11578 and
 * prefixes it in front of the caller's payload. When libx265 receives a Pelorus
 * UDU SEI via -udu_sei 1, the SEI message arrives wrapped:
 *   outer uuid_iso_iec_11578 = x265 UUID
 *   payload = Pelorus UUID (e1d7c4a2...) or carrier UUID (3f9b37b8...) + body.
 *
 * Allow-list rule (ADR-0210): pelorus_fgs must accept EXACTLY this one extra
 * wrapped form. Generic "skip 16 bytes and retry" or unverified outer UUIDs
 * are strictly prohibited to prevent arbitrary foreign SEI payloads from
 * injecting unwanted grain synthesis parameters.
 */

#ifndef AVCODEC_PELORUS_UDU_WRAP_H
#define AVCODEC_PELORUS_UDU_WRAP_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define PEL_UDU_UUID_LEN 16u

/* libx265 outer UUID: 2ca2de09-b517-47db-bb55-a4fe7fc2fc4e */
static const uint8_t pel_udu_x265_uuid[PEL_UDU_UUID_LEN] = {
    0x2c, 0xa2, 0xde, 0x09, 0xb5, 0x17, 0x47, 0xdb, 0xbb, 0x55, 0xa4, 0xfe, 0x7f, 0xc2, 0xfc, 0x4e};

/* interop.h pelorus_sidedata_uuid: e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64 */
static const uint8_t pel_udu_blob_uuid[PEL_UDU_UUID_LEN] = {
    0xe1, 0xd7, 0xc4, 0xa2, 0x6b, 0x93, 0x4f, 0x08, 0x9a, 0x55, 0x0f, 0x3c, 0x2d, 0xb1, 0x7e, 0x64};

/* interop.h pelorus_carrier_uuid (ABI 1.5): 3f9b37b8-fd9a-4621-920e-9b78b55cf9b5 */
static const uint8_t pel_udu_carrier_uuid[PEL_UDU_UUID_LEN] = {
    0x3f, 0x9b, 0x37, 0xb8, 0xfd, 0x9a, 0x46, 0x21, 0x92, 0x0e, 0x9b, 0x78, 0xb5, 0x5c, 0xf9, 0xb5};

enum PelUduClass {
    PEL_UDU_NONE = 0,
    PEL_UDU_DIRECT,
    PEL_UDU_X265_WRAPPED,
};

/**
 * Classify a user data unregistered SEI message.
 *
 * @param outer    Leading 16-byte uuid_iso_iec_11578 of the SEI message.
 * @param data     Payload following the outer UUID (may be NULL if data_len == 0).
 * @param data_len Length of data in bytes.
 * @return PEL_UDU_DIRECT if outer UUID is Pelorus blob or carrier UUID;
 *         PEL_UDU_X265_WRAPPED if outer UUID is x265 UUID, data is non-NULL,
 *         data_len > 16, and data[0..16) is Pelorus blob or carrier UUID;
 *         PEL_UDU_NONE otherwise.
 */
static inline int pel_udu_classify(const uint8_t outer[16], const uint8_t *data, size_t data_len)
{
    if (!outer)
        return PEL_UDU_NONE;

    if (!memcmp(outer, pel_udu_blob_uuid, PEL_UDU_UUID_LEN) ||
        !memcmp(outer, pel_udu_carrier_uuid, PEL_UDU_UUID_LEN))
        return PEL_UDU_DIRECT;

    if (!memcmp(outer, pel_udu_x265_uuid, PEL_UDU_UUID_LEN) && data &&
        data_len > PEL_UDU_UUID_LEN) {
        if (!memcmp(data, pel_udu_blob_uuid, PEL_UDU_UUID_LEN) ||
            !memcmp(data, pel_udu_carrier_uuid, PEL_UDU_UUID_LEN))
            return PEL_UDU_X265_WRAPPED;
    }

    return PEL_UDU_NONE;
}

#endif /* AVCODEC_PELORUS_UDU_WRAP_H */
