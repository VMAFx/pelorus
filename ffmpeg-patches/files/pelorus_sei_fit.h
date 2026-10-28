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
 * Fit user data unregistered SEI payloads into hevc_nvenc's per-picture
 * header limit (issue #267, ADR-0181). Plain C with no libav* and no
 * libpelorus dependency: libavcodec/nvenc.c includes it, and Pelorus's fast
 * suite tests it against libpelorus's packer
 * (ffmpeg-patches/test/sei_fit_test.c).
 *
 * NVENC's HEVC encoder fails nvEncLockBitstream() with
 * NV_ENC_ERR_OUT_OF_MEMORY when the non-VCL NAL units of one picture exceed
 * 1024 bytes: VPS, SPS, PPS, access unit delimiter and every SEI NAL unit,
 * counted in Annex B form with start codes and emulation prevention bytes.
 * FFmpeg maps that status to AVERROR(ENOMEM) and the encode stops. Measured
 * on an RTX 4090 with driver 615.71.09 (docs/research/0181-hevc-nvenc-sei-
 * header-budget.md); neither nvEncodeAPI.h 13.1 nor the NVENC programming
 * guide 13.1 states the limit. h264_nvenc carries 64 KiB payloads.
 *
 * The SEI a caller passes in seiPayloadArray get 768 of the 1024 bytes
 * (PEL_SEI_HEVC_USER_BUDGET); the reserve keeps room for the NAL units NVENC
 * writes on its own: at most 128 bytes measured (8K Main10, VUI, AUD), plus
 * the HDR10 and 3D reference display SEI it builds from picture parameters.
 * FFmpeg itself (96b7f8281a, "avcodec/nvenc: drop hevc_nvenc SEI above a
 * fixed size") drops any SEI that takes the payload bytes of a picture above
 * 512 (PEL_SEI_HEVC_PAYLOAD_LIMIT). pel_sei_hevc_budget() gives a Pelorus
 * blob what both rules leave, so the blob is never written whole only to be
 * dropped by FFmpeg. These rules are Pelorus's own and frozen in this file
 * (ADR-0236): FFmpeg's check counts payload bytes only, and the NAL size
 * rules of the shared FFmpeg fix series (ADR-0185, ADR-0218) left with the
 * merged form.
 *
 * A Pelorus blob (interop.h) that does not fit is carried without the
 * per-cell maps appended after its sections: the sections, which hold every
 * scalar, stay, and each map offset and size becomes zero, which interop.h
 * defines as an absent map. Patch 0022 strips the payload right after its
 * copy and drops a blob whose scalars still do not fit.
 *
 * NVENC (h264_nvenc and hevc_nvenc) also writes a SEI payload truncated when
 * its emulation prevention bytes exceed ceil(P / 3) + 3 for a P-byte payload
 * (issue #284, pel_sei_udu_truncated()), which the zero runs of flat per-cell
 * maps reach. Every Pelorus blob therefore goes into an NVENC stream in the
 * zero-free carrier form of interop ABI 1.5 (ADR-0183): pelorus_carrier_uuid,
 * then the COBS encoding of the blob image, with no 0x00 byte and so no
 * emulation prevention at all. pel_sei_carrier_write() produces the bytes
 * pel_blob_carrier_encode() does; sei_fit_test.c checks the two against each
 * other. The budget is charged for the carrier, after any stripping.
 */

#ifndef AVCODEC_PELORUS_SEI_FIT_H
#define AVCODEC_PELORUS_SEI_FIT_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* a + b, or SIZE_MAX when the sum does not fit a size_t (#389). */
static inline size_t pel_sei_size_add(size_t a, size_t b)
{
    return a > SIZE_MAX - b ? SIZE_MAX : a + b;
}

/* hevc_nvenc's per-picture non-VCL limit, the reserve for the NAL units NVENC
 * writes on its own, and the share left for SEI passed in seiPayloadArray. */
#define PEL_SEI_HEVC_NON_VCL_LIMIT 1024u
#define PEL_SEI_HEVC_RESERVE 256u
#define PEL_SEI_HEVC_USER_BUDGET ((size_t)(PEL_SEI_HEVC_NON_VCL_LIMIT - PEL_SEI_HEVC_RESERVE))

/* FFmpeg's NVENC_HEVC_SEI_MAX_PAYLOAD (96b7f8281a): hevc_nvenc drops the SEI
 * that takes the payload bytes of a picture above it, in array order. */
#define PEL_SEI_HEVC_PAYLOAD_LIMIT 512u

/* Mirrored interop.h ABI 1.x layout; R1 (append-only) keeps these offsets
 * fixed, and sei_fit_test.c checks each against offsetof(). */
#define PEL_SEI_UUID_LEN 16u
#define PEL_SEI_HDR_MIN 48u      /* sizeof(PelorusSideData)                  */
#define PEL_SEI_DIR_ENTRY 16u    /* sizeof(PelorusSectionDir)                */
#define PEL_SEI_MAX_SECTIONS 32u /* one directory entry per section bit    */

/* Sections this file can strip: those whose map fields pel_sei_map_fields
 * lists, plus those without maps (denoise, film grain, complexity). A blob
 * with any other section (QP report, encoder telemetry, encode record, or a
 * newer bit) is carried whole or dropped. */
#define PEL_SEI_STRIPPABLE 0xDFu

/* One (offset, size) map field pair: the uint32 offset at `at` bytes into the
 * section, the uint32 size right after it. */
typedef struct PelSeiMapField {
    uint32_t section;
    uint32_t at;
} PelSeiMapField;

static const PelSeiMapField pel_sei_map_fields[] = {
    {1u << 0u, 8u},  /* PelorusBandingSection.cell_data_offset     */
    {1u << 1u, 12u}, /* PelorusVarianceSection.var_cell_offset     */
    {1u << 1u, 20u}, /* PelorusVarianceSection.edge_cell_offset    */
    {1u << 4u, 20u}, /* PelorusMotionSection.mv_field_offset       */
    {1u << 6u, 0u},  /* PelorusMotionConfSection.conf_field_offset */
};
#define PEL_SEI_NB_MAP_FIELDS (sizeof(pel_sei_map_fields) / sizeof(pel_sei_map_fields[0]))

static inline uint16_t pel_sei_rd16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8u));
}

static inline uint32_t pel_sei_rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8u) | ((uint32_t)p[2] << 16u) |
           ((uint32_t)p[3] << 24u);
}

static inline void pel_sei_wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8u);
    p[2] = (uint8_t)(v >> 16u);
    p[3] = (uint8_t)(v >> 24u);
}

/* Emulation prevention bytes the `n` bytes of `p` need when `zeros` zero bytes
 * precede them: one before every byte <= 3 that follows two zeros. */
static inline size_t pel_sei_epb_count(const uint8_t *p, size_t n, size_t zeros)
{
    size_t epb = 0;

    for (size_t i = 0; i < n; i++) {
        if (zeros >= 2u && p[i] <= 3u) {
            epb++;
            zeros = 0;
        }
        zeros = p[i] ? 0u : zeros + 1u;
    }
    return epb;
}

/*
 * Annex B bytes of the SEI NAL unit NVENC writes for one payload of `type`
 * and `n` bytes: start code, NAL unit header, type and size bytes, the
 * payload with its emulation prevention bytes, the trailing bits, and one
 * byte for emulation prevention across the size and payload boundary. Only a
 * payload whose other bytes fit `limit` is scanned; a larger one or a NULL
 * `p` returns that size without the scan, and one beyond NVENC's 32-bit
 * payloadSize returns SIZE_MAX, which no budget below SIZE_MAX admits.
 */
static inline size_t pel_sei_nal_size(const uint8_t *p, uint32_t type, size_t n, size_t limit)
{
    size_t nal;

#if SIZE_MAX > UINT32_MAX
    if (n > UINT32_MAX)
        return SIZE_MAX;
#endif
    nal = pel_sei_size_add(n, 4u + 2u + type / 255u + 1u + n / 255u + 1u + 1u + 1u);
    if (!p || nal > limit)
        return nal;
    return pel_sei_size_add(nal, pel_sei_epb_count(p, n, 0));
}

/* pel_sei_nal_size() of a user data unregistered payload (type 5). */
static inline size_t pel_sei_nal_bytes(const uint8_t *p, size_t n, size_t limit)
{
    return pel_sei_nal_size(p, 5u, n, limit);
}

/* 1 when NVENC writes the user data unregistered payload `p` of `n` bytes
 * truncated: it escapes the payloads of a picture into a buffer of 4/3 of the
 * largest one, `pmax` bytes, plus 5, and a payload whose emulation prevention
 * bytes overflow it loses its end and trailing bits (issue #284; driver
 * 615.78.08, h264_nvenc and hevc_nvenc). The last size byte is zero when `n`
 * is a multiple of 255. A carrier never qualifies: it holds no zero byte. */
static inline int pel_sei_udu_truncated(const uint8_t *p, size_t n, size_t pmax)
{
    const size_t epb = pel_sei_epb_count(p, n, n % 255u ? 0u : 1u);

    return n + 3u + epb > 4u * (pmax + 5u) / 3u;
}

/* What the SEI already in one hevc_nvenc picture leave of the two limits:
 * NAL bytes of PEL_SEI_HEVC_USER_BUDGET, payload bytes of
 * PEL_SEI_HEVC_PAYLOAD_LIMIT. */
typedef struct PelSeiHevcRoom {
    size_t nal;
    size_t payload;
} PelSeiHevcRoom;

#define PEL_SEI_HEVC_ROOM_INIT {PEL_SEI_HEVC_USER_BUDGET, PEL_SEI_HEVC_PAYLOAD_LIMIT}

/* Charge one SEI of `type` and `n` bytes that precedes the Pelorus blob in
 * the picture's array. Its NAL bytes are charged in any case, stopping at
 * zero; its payload bytes only when they fit, since FFmpeg drops a SEI that
 * does not and keeps counting from the total of those it kept. */
static inline void pel_sei_hevc_charge(PelSeiHevcRoom *room, const uint8_t *p, uint32_t type,
                                       size_t n)
{
    const size_t nal = pel_sei_nal_size(p, type, n, room->nal);

    room->nal -= nal < room->nal ? nal : room->nal;
    if (n <= room->payload)
        room->payload -= n;
}

/* The NAL budget a carrier gets from `room`: a carrier of c bytes takes
 * c + c / 255 + 10 NAL bytes (pel_sei_carrier_nal_bytes()), which grows with
 * c, so the payload limit L becomes L + L / 255 + 10 NAL bytes. */
static inline size_t pel_sei_hevc_budget(const PelSeiHevcRoom *room)
{
    const size_t pay = room->payload + room->payload / 255u + 10u;

    return pay < room->nal ? pay : room->nal;
}

/* interop.h pelorus_sidedata_uuid and pelorus_carrier_uuid (ABI 1.5). */
static const uint8_t pel_sei_blob_uuid[PEL_SEI_UUID_LEN] = {
    0xe1, 0xd7, 0xc4, 0xa2, 0x6b, 0x93, 0x4f, 0x08, 0x9a, 0x55, 0x0f, 0x3c, 0x2d, 0xb1, 0x7e, 0x64};
static const uint8_t pel_sei_carrier_uuid[PEL_SEI_UUID_LEN] = {
    0x3f, 0x9b, 0x37, 0xb8, 0xfd, 0x9a, 0x46, 0x21, 0x92, 0x0e, 0x9b, 0x78, 0xb5, 0x5c, 0xf9, 0xb5};

/* 1 when `p` is an ABI 1.x Pelorus blob (UUID, magic, major 1), the test
 * pel_blob_is_present() applies: such a payload goes out as a carrier. */
static inline int pel_sei_is_blob(const uint8_t *p, size_t n)
{
    return p && n >= PEL_SEI_UUID_LEN + PEL_SEI_HDR_MIN &&
           !memcmp(p, pel_sei_blob_uuid, PEL_SEI_UUID_LEN) &&
           !memcmp(p + PEL_SEI_UUID_LEN, "PELOR1\0\0", 8) &&
           pel_sei_rd16(p + PEL_SEI_UUID_LEN + 8) == 1u;
}

/*
 * COBS-encode in[0..n) into out (Cheshire and Baker, 1999; no delimiter), the
 * encoding interop.c's pel_blob_carrier_encode() writes: blocks of a code byte
 * c (1..255) and c - 1 non-zero bytes; every block but a full one (255) and
 * the last stands for a zero; a full block that ends the input gets no empty
 * block after it. out NULL: length only. Returns the encoded length.
 */
static inline size_t pel_sei_cobs(const uint8_t *in, size_t n, uint8_t *out)
{
    size_t code_at = 0; /* position of the open block's code byte */
    size_t w = 1;       /* next output position                    */
    size_t code = 1;    /* 1 + data bytes of the open block        */

    for (size_t i = 0; i < n; i++) {
        const int full = in[i] && code + 1u == 0xFFu && i + 1u < n;

        if (in[i]) {
            if (out)
                out[w] = in[i];
            w++;
            code++;
        }
        if (!in[i] || full) {
            if (out)
                out[code_at] = (uint8_t)code;
            code_at = w++;
            code = 1;
        }
    }
    if (out)
        out[code_at] = (uint8_t)code;
    return w;
}

/* Bytes of the carrier form of the `n`-byte blob `p` (pel_sei_is_blob()). */
static inline size_t pel_sei_carrier_len(const uint8_t *p, size_t n)
{
    return PEL_SEI_UUID_LEN + pel_sei_cobs(p + PEL_SEI_UUID_LEN, n - PEL_SEI_UUID_LEN, NULL);
}

/* Write the carrier form of the blob `p` into `out`, which holds
 * pel_sei_carrier_len(p, n) bytes and does not overlap `p`. */
static inline void pel_sei_carrier_write(const uint8_t *p, size_t n, uint8_t *out)
{
    memcpy(out, pel_sei_carrier_uuid, PEL_SEI_UUID_LEN);
    pel_sei_cobs(p + PEL_SEI_UUID_LEN, n - PEL_SEI_UUID_LEN, out + PEL_SEI_UUID_LEN);
}

/* SEI NAL bytes of the carrier of the blob `p` (no emulation prevention: it
 * holds no zero byte). Only a blob whose carrier can fit `limit` is scanned:
 * a carrier is at least one byte longer than its blob (so n >= limit cannot
 * fit, #389). */
static inline size_t pel_sei_carrier_nal_bytes(const uint8_t *p, size_t n, size_t limit)
{
    const size_t c = n >= limit ? pel_sei_size_add(n, 1u) : pel_sei_carrier_len(p, n);

    return pel_sei_size_add(c, c / 255u + 10u);
}

/* The header fields of a Pelorus blob the stripper needs. */
typedef struct PelSeiBlob {
    uint32_t total; /* total_size, blob-relative (UUID excluded) */
    uint16_t count; /* section_count                             */
    uint16_t hsize; /* header_size: dir[] starts here            */
} PelSeiBlob;

/* 0 and the header fields when `p` (UUID first) is an ABI 1.x Pelorus blob
 * that holds only strippable sections and whose directory fits; -1 otherwise. */
static inline int pel_sei_blob(const uint8_t *p, size_t n, PelSeiBlob *b)
{
    static const uint8_t uuid[PEL_SEI_UUID_LEN] = {0xe1, 0xd7, 0xc4, 0xa2, 0x6b, 0x93, 0x4f, 0x08,
                                                   0x9a, 0x55, 0x0f, 0x3c, 0x2d, 0xb1, 0x7e, 0x64};
    const uint8_t *img;

    if (!p || n < PEL_SEI_UUID_LEN + PEL_SEI_HDR_MIN || memcmp(p, uuid, PEL_SEI_UUID_LEN) != 0)
        return -1;
    img = p + PEL_SEI_UUID_LEN;
    if (memcmp(img, "PELOR1\0\0", 8) != 0 || pel_sei_rd16(img + 8) != 1u)
        return -1;
    b->total = pel_sei_rd32(img + 12);
    b->count = pel_sei_rd16(img + 20);
    b->hsize = pel_sei_rd16(img + 22);
    if ((pel_sei_rd32(img + 16) & ~(uint32_t)PEL_SEI_STRIPPABLE) != 0u ||
        b->total > n - PEL_SEI_UUID_LEN || b->hsize < PEL_SEI_HDR_MIN ||
        b->count > PEL_SEI_MAX_SECTIONS ||
        (uint32_t)b->hsize + (uint32_t)b->count * PEL_SEI_DIR_ENTRY > b->total)
        return -1;
    return 0;
}

/*
 * Bytes of the Pelorus blob `p` up to the end of its last section, UUID
 * included: the blob without the maps appended after the sections. 0 when `p`
 * is no blob pel_sei_blob() accepts, a directory entry is not one strippable
 * section inside total_size, or nothing follows the sections.
 */
static inline size_t pel_sei_scalar_len(const uint8_t *p, size_t n)
{
    PelSeiBlob b;
    uint32_t dir_end;
    uint32_t end;

    if (pel_sei_blob(p, n, &b) != 0)
        return 0;
    dir_end = (uint32_t)b.hsize + (uint32_t)b.count * PEL_SEI_DIR_ENTRY;
    end = dir_end;
    for (uint32_t i = 0; i < b.count; i++) {
        const uint8_t *d = p + PEL_SEI_UUID_LEN + b.hsize + i * PEL_SEI_DIR_ENTRY;
        const uint32_t id = pel_sei_rd32(d);
        const uint32_t off = pel_sei_rd32(d + 4);
        const uint32_t size = pel_sei_rd32(d + 8);

        if ((id & PEL_SEI_STRIPPABLE) == 0u || (id & (id - 1u)) != 0u || off < dir_end ||
            size > b.total || off > b.total - size)
            return 0;
        end = off + size > end ? off + size : end;
    }
    return end < b.total ? (size_t)PEL_SEI_UUID_LEN + end : 0;
}

/*
 * Turn `p`, a copy of the first `len` = pel_sei_scalar_len() bytes of a
 * Pelorus blob, into a blob without maps: zero the map offset and size fields
 * of its sections and set total_size to the new end.
 */
static inline void pel_sei_strip_maps(uint8_t *p, size_t len)
{
    uint8_t *img = p + PEL_SEI_UUID_LEN;
    uint32_t count;
    uint32_t hsize;

    if (len < PEL_SEI_UUID_LEN + PEL_SEI_HDR_MIN)
        return; /* not a pel_sei_scalar_len() result: nothing to strip */
    count = pel_sei_rd16(img + 20);
    hsize = pel_sei_rd16(img + 22);
    for (uint32_t i = 0; i < count && i < PEL_SEI_MAX_SECTIONS; i++) {
        const uint8_t *d = img + hsize + i * PEL_SEI_DIR_ENTRY;
        const uint32_t id = pel_sei_rd32(d);
        const uint32_t off = pel_sei_rd32(d + 4);
        const uint32_t size = pel_sei_rd32(d + 8);

        for (size_t f = 0; f < PEL_SEI_NB_MAP_FIELDS; f++) {
            if (pel_sei_map_fields[f].section != id || pel_sei_map_fields[f].at + 8u > size)
                continue;
            pel_sei_wr32(img + off + pel_sei_map_fields[f].at, 0);
            pel_sei_wr32(img + off + pel_sei_map_fields[f].at + 4u, 0);
        }
    }
    pel_sei_wr32(img + 12, (uint32_t)(len - PEL_SEI_UUID_LEN));
}

/*
 * How many leading bytes of one SEI payload `p` of `n` bytes to carry when
 * `budget` bytes of SEI NAL units are left in the picture: `n` when the whole
 * payload fits, pel_sei_scalar_len() when it does not and `p` is a strippable
 * Pelorus blob (strip the copy with pel_sei_strip_maps(), turn it into its
 * carrier and check that with pel_sei_nal_bytes(), which also covers bytes the
 * zeroed fields change), 0 to drop the payload. A Pelorus blob is measured as
 * its carrier, the form the encoder writes.
 */
static inline size_t pel_sei_fit_len(const uint8_t *p, size_t n, size_t budget)
{
    const size_t need = pel_sei_is_blob(p, n) ? pel_sei_carrier_nal_bytes(p, n, budget) :
                                                pel_sei_nal_bytes(p, n, budget);

    if (need <= budget)
        return n;
    return pel_sei_scalar_len(p, n);
}

#endif /* AVCODEC_PELORUS_SEI_FIT_H */
