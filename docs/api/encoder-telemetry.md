<!-- markdownlint-disable MD013 MD060 -->
# Encoder telemetry (interop ABI 1.4)

> [!IMPORTANT]
> **Status: the layout, the input contract and the checks are implemented;
> the adapters are not.** `PELORUS_ABI_MINOR` is 4 in
> [`interop.h`](../../libpelorus/include/pelorus/interop.h)
> ([ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md), issue #86). The
> section, `pelorus/telemetry.h`, the registry and the parity check ship now.
> The adapters (x265 CSV, FFmpeg quality stats, SVT-AV1, libaom, VVenC,
> hardware feedback), their capability tables, the AV1 and VP9 QP
> normalisation constants and `pel_enc_telemetry_to_json()` come later (#86
> items 2 to 4). Until then only `adapter = external` records carry fields.

## In one paragraph

Each encoder reports what it did (QP, bits, picture type, block modes,
PSNR/SSIM) in its own shape and scale. ABI 1.4 adds one section,
`PEL_SEC_ENC_TELEMETRY` (bit 8), with one record per coded frame:

- the scalars in fixed units;
- the QP in its native scale plus an H.264-equivalent value, so encoders can
  be compared;
- an optional per-row or per-block map on the encoder's own grid;
- a 64-bit `present_mask` that separates "not reported" from zero.

Adapters (x265 CSV, FFmpeg `AV_PKT_DATA_QUALITY_STATS`, SVT-AV1 stat file,
libaom, VVenC, hardware feedback, any external caller) fill one plain-C input
struct. The same field names are the JSON keys that VMAFx's decode-side
stream metadata (VMAFx/vmafx#2271) uses for the fields both sides carry.

## Section layout

`PelorusEncTelemetrySection`, 104 bytes, 8-byte aligned (first member
`uint64_t`), no implicit padding. The layout was compiled with
`-Wpadded -Werror` under GCC and Clang
([research 0174](../research/0174-abi-1-4-telemetry-provenance.md), E1).
APPEND-ONLY after `_pad`.

| Offset | Type | Member | Unit and range | Presence |
|---:|---|---|---|---|
| 0 | `uint64_t` | `present_mask` | OR of `PEL_TLM_F_*` | always |
| 8 | `uint32_t` | `display_index` | frame; 0-based display order | bit 0 |
| 12 | `uint32_t` | `decode_index` | frame; 0-based coding order | bit 1 |
| 16 | `uint32_t` | `frame_bytes` | bytes of the coded frame, headers included, container excluded | bit 2 |
| 20 | `uint32_t` | `header_bits` | bits that are not residual (headers, modes, motion) | bit 3 |
| 24 | `uint32_t` | `residual_bits` | bits of transform coefficients | bit 4 |
| 28 | `float` | `avg_qp` | frame-mean QP in the `qp_scale` scale | bit 5 |
| 32 | `float` | `avg_qp_norm` | H.264-equivalent QP (see below) | bit 6 |
| 36 | `float` | `psnr_y` | dB, finite, >= 0 | bit 7 |
| 40 | `float` | `psnr_u` | dB | bit 8 |
| 44 | `float` | `psnr_v` | dB | bit 9 |
| 48 | `float` | `ssim_y` | linear 0..1 (not dB) | bit 10 |
| 52 | `float` | `intra_fraction` | share of luma area coded intra, 0..1 | bit 11 |
| 56 | `float` | `inter_fraction` | share coded inter (not skip), 0..1 | bit 12 |
| 60 | `float` | `skip_fraction` | share coded skip, 0..1 | bit 13 |
| 64 | `uint32_t` | `qp_map_offset` | blob-relative, 8-aligned | bit 19 |
| 68 | `uint32_t` | `qp_map_size` | `map_cols * map_rows * 2` | bit 19 |
| 72 | `uint32_t` | `bits_map_offset` | blob-relative, 8-aligned | bit 20 |
| 76 | `uint32_t` | `bits_map_size` | `map_cols * map_rows * 4` | bit 20 |
| 80 | `uint32_t` | `mode_map_offset` | blob-relative, 8-aligned | bit 21 |
| 84 | `uint32_t` | `mode_map_size` | `map_cols * map_rows` | bit 21 |
| 88 | `uint16_t` | `map_cols` | elements per row (1 for row granularity) | with any map |
| 90 | `uint16_t` | `map_rows` | element rows | with any map |
| 92 | `uint8_t` | `codec` | `enum pel_tlm_codec`, never 0 | always |
| 93 | `uint8_t` | `qp_scale` | `enum pel_qp_scale` | with bits 5, 6 or 19 |
| 94 | `uint8_t` | `picture_type` | `enum pel_picture_type` | bit 14 |
| 95 | `uint8_t` | `frame_flags` | `PEL_TLM_FRAME_*`; each flag has its own presence bit | bits 15-18 |
| 96 | `uint8_t` | `granularity` | `enum pel_tlm_granularity`, never 0 | always |
| 97 | `uint8_t` | `block_size_log2` | element edge `1 << n` luma pixels; 2..7, or 0 for frame granularity | with any map |
| 98 | `uint8_t` | `coded_bit_depth` | 8, 10 or 12: luma bit depth of the coded stream | bit 22 |
| 99 | `uint8_t` | `metric_source` | `enum pel_tlm_metric_source` | with bits 7-10 |
| 100 | `uint8_t` | `adapter` | `enum pel_tlm_adapter`, never 0 | always |
| 101 | `uint8_t[3]` | `_pad` | reserved, zero | - |

The blob header's `bit_depth`, `grid_cols` and `grid_rows` describe the
Pelorus analysis grid of a pre-encode blob. A telemetry record does not use
them. Its own `coded_bit_depth` and `map_cols`/`map_rows` apply.

## Presence bits

`#define PEL_TLM_F_<KEY> (UINT64_C(1) << nu)`, where `<KEY>` is the upper-cased
JSON key and `n` the bit (the shift count is unsigned so the defines are
clang-tidy clean). Bits are append-only and a retired bit is never reused
(R2). Bits 23..63 are free. `PEL_TLM_KNOWN_MASK` covers bits 0..22: a reader
ignores any other bit (R3), the validator rejects it.

| Bit | Constant | Bit | Constant |
|---:|---|---:|---|
| 0 | `PEL_TLM_F_DISPLAY_INDEX` | 12 | `PEL_TLM_F_INTER_FRACTION` |
| 1 | `PEL_TLM_F_DECODE_INDEX` | 13 | `PEL_TLM_F_SKIP_FRACTION` |
| 2 | `PEL_TLM_F_FRAME_BYTES` | 14 | `PEL_TLM_F_PICTURE_TYPE` |
| 3 | `PEL_TLM_F_HEADER_BITS` | 15 | `PEL_TLM_F_KEY_FRAME` |
| 4 | `PEL_TLM_F_RESIDUAL_BITS` | 16 | `PEL_TLM_F_REFERENCE` |
| 5 | `PEL_TLM_F_AVG_QP` | 17 | `PEL_TLM_F_SHOWN` |
| 6 | `PEL_TLM_F_AVG_QP_NORM` | 18 | `PEL_TLM_F_SCENE_CUT` |
| 7 | `PEL_TLM_F_PSNR_Y` | 19 | `PEL_TLM_F_QP_MAP` |
| 8 | `PEL_TLM_F_PSNR_U` | 20 | `PEL_TLM_F_BITS_MAP` |
| 9 | `PEL_TLM_F_PSNR_V` | 21 | `PEL_TLM_F_MODE_MAP` |
| 10 | `PEL_TLM_F_SSIM_Y` | 22 | `PEL_TLM_F_CODED_BIT_DEPTH` |
| 11 | `PEL_TLM_F_INTRA_FRACTION` | | |

`frame_flags` values: `PEL_TLM_FRAME_KEY` (bit 0, random-access point: IDR,
IRAP or an AV1 key frame), `PEL_TLM_FRAME_REFERENCE` (bit 1, used for
prediction), `PEL_TLM_FRAME_SHOWN` (bit 2, displayed; an AV1 hidden
alt-ref frame is 0), `PEL_TLM_FRAME_SCENE_CUT` (bit 3, the encoder detected a
cut). A flag value without its presence bit (15 to 18) is invalid.

## Enumerations

Value 0 is invalid in every enumeration below except `pel_block_mode`. JSON
writes the lower-case name.

| Enumeration | Values (JSON name) |
|---|---|
| `pel_tlm_codec` | 1 `h264`, 2 `hevc`, 3 `vvc`, 4 `av1`, 5 `vp9` (FFmpeg codec names) |
| `pel_qp_scale` | 1 `slice_qp` (H.26x `SliceQpY`: -QpBdOffsetY..51, VVC ..63), 2 `av1_qindex` (`base_q_idx` 0..255), 3 `vp9_qindex` (0..255), 4 `av1_encoder_qp` (libaom/SVT-AV1 `--qp` 0..63) |
| `pel_picture_type` | 1 `i`, 2 `p`, 3 `b`: the numeric values of FFmpeg `AVPictureType`; AV1 key and intra-only frames are `i`, other AV1 frames `p` (as `libaomenc` labels them) |
| `pel_tlm_granularity` | 1 `frame` (no maps), 2 `row`, 3 `block` |
| `pel_block_mode` (map element) | 0 not reported for this element, 1 `intra`, 2 `inter`, 3 `skip` |
| `pel_tlm_metric_source` | 1 `encoder` (the encoder compared its reconstruction with its input), 2 `adapter_sse` (the adapter derived PSNR from encoder-reported SSE, peak `2^coded_bit_depth - 1`) |
| `pel_tlm_adapter` | 1 `ffmpeg_quality_stats`, 2 `x265_csv`, 3 `svtav1_stat_file`, 4 `libaom_stats`, 5 `vvenc_log`, 6 `qsv`, 7 `nvenc`, 8 `amf`, 9 `vulkan_feedback`, 10 `external` (caller-filled) |

An adapter that reports QP with the `QpBdOffset` added (x264 high bit depth
reports `QP'`) subtracts the offset before it writes `slice_qp`.

## QP normalisation

`avg_qp_norm = 6 * log2(Qstep / 0.625)`, where `Qstep` is the frame's luma AC
step size in H.264 units at 8 bits. H.264 defines `Qstep(QP) = 0.625 *
2^(QP / 6)`, so the value is the H.264 QP with the same step size.

- `slice_qp`: identity, `avg_qp_norm == avg_qp`.
- `av1_qindex`, `vp9_qindex`, `av1_encoder_qp`: the step size comes from the
  codec's AC quantiser table at `coded_bit_depth`. The table is scaled to
  H.264 units and to 8 bits. The constants ship with the adapters and are
  **unverified** today (research 0174, E3).
- An adapter writes `avg_qp_norm` only when `coded_bit_depth` is known.

`pel_qp_normalize()` implements the identity for `slice_qp` and returns
`PEL_ERR_UNSUPPORTED` for the three `base_q_idx` scales until the table lands,
so no unverified constant reaches a record.

The check that keeps the table honest is #86 acceptance item 2. The same clip
encoded by libx264, libx265 and libsvtav1 at settings the table calls equal in
step size must give `avg_qp_norm` values within 1.0 of each other. An adapter
that skips the conversion fails it.

## Maps

A map is present only when its bit is set and `granularity` is `row` or
`block`. Elements are row-major, `map_cols` x `map_rows`. An element covers
`1 << block_size_log2` luma pixels on each side, and the last column and row
may be partial. For `row` granularity, `map_cols` is 1 and each element covers
a full row of that height.

| Map | Element | Meaning |
|---|---|---|
| `qp_map` | `int16_t` | native QP x 4 (Q2, `round(qp * 4)`); AV1 qindex 255 is 1020 |
| `bits_map` | `uint32_t` | coded bits of the element |
| `mode_map` | `uint8_t` | `enum pel_block_mode` |

Maps follow the existing map convention ([interop-abi.md](interop-abi.md)): the
producer appends them after the packed sections, raises `total_size`, and
patches the offsets. Telemetry adds the 8-byte alignment of every map offset.

**Bounds (HISS-02).** `map_cols * map_rows <= PEL_TLM_MAP_MAX_ELEMS`, where
`PEL_TLM_MAP_MAX_ELEMS` is `1u << 20` (1 048 576 elements; 8K at 8x8 is
518 400). With all three maps, the bound is 7 340 032 bytes per frame.

**Reader checks**, in this order, before any pointer is formed:

1. The map's presence bit is set and `granularity` is `row` or `block`, or
   the map is ignored.
2. `1 <= map_cols`, `1 <= map_rows`, and the product is at most the bound.
   `block_size_log2` is in 2..7.
3. `size == map_cols * map_rows * element_size`. Otherwise the result is
   `PEL_ERR_ABI`.
4. `offset % 8 == 0`. Otherwise the result is `PEL_ERR_ABI`.
5. `offset <= total_size` and `size <= total_size - offset`, with
   `total_size` no larger than the received length. Otherwise the result is
   `PEL_ERR_TRUNCATED`.

`pel_blob_map()` in `interop.c` runs checks 3 to 5 (and the blob framing
first); VMAFx gets it through the mirror. Between checks 4 and 5 it also
rejects a map that overlaps the header or the directory (`offset <
header_size + section_count * 16`, `PEL_ERR_ABI`). `pel_enc_telemetry_map()` in
`telemetry.c` runs all five for one map bit and returns `PEL_ERR_ABSENT` for
check 1 and `PEL_ERR_ABI` for check 2.

## Validation (writer and builder)

`pel_enc_telemetry_validate()` returns `PEL_ERR_INVALID` or
`PEL_ERR_RANGE`, and never builds a partial record, when:

- `codec`, `granularity` or `adapter` is 0 or out of range;
- `qp_scale` is 0 while bit 5, 6 or 19 is set, or `metric_source` is 0 while
  any of bits 7 to 10 is set;
- a value is set while its bit is clear (scalars, flags, map offsets and
  sizes);
- a float is NaN or infinite, a fraction is outside 0..1, or the three
  fractions sum above `1 + 1e-3`;
- `header_bits + residual_bits > frame_bytes * 8` when all three are present;
- `avg_qp` is outside its scale's range;
- the presence mask is not a subset of the adapter's capability mask (not
  checked for `external`);
- a map breaks the bounds above, or a map bit is set at `frame` granularity.

The implementation adds the checks the rules above imply, so a record never
says two things at once:

- `qp_scale` belongs to the codec: `slice_qp` to `h264`, `hevc` and `vvc`,
  `av1_qindex` and `av1_encoder_qp` to `av1`, `vp9_qindex` to `vp9`
  (`PEL_ERR_INVALID`);
- `row` or `block` granularity carries at least one map, and `row` has
  `map_cols == 1` (`PEL_ERR_INVALID`);
- every `mode_map` element is a `pel_block_mode` value (`PEL_ERR_INVALID`) and
  every `qp_map` element lies inside the scale's range times 4
  (`PEL_ERR_RANGE`);
- the `slice_qp` range is `-6 * (coded_bit_depth - 8)` to 51 (63 for `vvc`);
  without a reported bit depth the lower bound is -24, the 12-bit offset.

`pel_tlm_adapter_caps()` reports every defined bit for `external`. The other
adapters return `PEL_ERR_UNSUPPORTED` with an empty mask until their tables
land with them, so a record from them validates only when it reports nothing.

## JSON form

The JSON form is used by the field-name parity check, by VMAFx's result
document and by `pel_enc_telemetry_to_json()`, which is not implemented yet.
A stream is one object:

```json
{
  "schema": "pelorus/enc-telemetry/1",
  "codec": "hevc",
  "adapter": "x265_csv",
  "available": ["avg_qp", "decode_index", "display_index", "frame_bytes", "picture_type", "psnr_u", "psnr_v", "psnr_y"],
  "unavailable": ["avg_qp_norm", "bits_map", "coded_bit_depth", "header_bits", "inter_fraction", "intra_fraction", "key_frame", "mode_map", "qp_map", "reference", "residual_bits", "scene_cut", "shown", "skip_fraction", "ssim_y"],
  "frames": [
    {"decode_index": 0, "display_index": 0, "picture_type": "i", "frame_bytes": 41872, "avg_qp": 27.0, "qp_scale": "slice_qp", "granularity": "frame", "psnr_y": 41.2, "metric_source": "encoder"},
    {"decode_index": 1, "display_index": 4, "picture_type": "p", "frame_bytes": 12034, "avg_qp": 30.0, "qp_scale": "slice_qp", "granularity": "frame", "psnr_y": 39.8, "metric_source": "encoder"}
  ]
}
```

The values are illustrative, and the `available` list for `x265_csv` is
settled with that adapter. The rules:

- `available` is the adapter's capability mask as keys, and `unavailable` is
  the rest of the registry's bit-carrying keys. A key in `unavailable` is
  "not available" for the whole stream, which is the named marker
  VMAFx/vmafx#2271 asks for.
- A frame omits an available key it did not report. No value stands for "not
  reported", neither `null` nor `0`.
- Enums are written as their names and flags as booleans (`key_frame`,
  `reference`, `shown`, `scene_cut`). A map is a JSON array of numbers;
  `qp_map` is written as native QP, the Q2 value divided by 4.
- This text is not digested, so floats are JSON numbers.

## Field registry and cross-walk with VMAFx stream metadata

The normative names are the keys of
[`libpelorus/schema/telemetry-fields.json`](../../libpelorus/schema/telemetry-fields.json)
(schema `pelorus/telemetry-fields/1`, 31 keys). Each row has `key`, `bit` (or
`null`), `json` type, `c` type, `unit`, `range` and `decoder`. The units are
the tokens `none`, `frame`, `byte`, `bit`, `qp_scale`, `h264_qp`, `dB`,
`linear`, `area_share`, `element` and `log2_px`. A `shared` key is one
that the VMAFx decode-side stream metadata must spell, type and unit exactly
like this, if it carries the field at all. An `encoder_only` key is one only
an encoder can know.

| Key | JSON type | Unit | `decoder` | VMAFx/vmafx#2271 term |
|---|---|---|---|---|
| `picture_type` | string enum | - | shared | frame type |
| `key_frame` | boolean | - | shared | frame type, GOP position |
| `reference` | boolean | - | shared | - |
| `shown` | boolean | - | shared | - |
| `frame_bytes` | integer | byte | shared | frame size in bytes |
| `avg_qp` | number | `qp_scale` | shared | average QP where the codec exposes it |
| `qp_scale` | string enum | - | shared | (needed to read `avg_qp`) |
| `avg_qp_norm` | number | H.264 QP | shared | - (a decoder can compute it from `avg_qp`) |
| `display_index` | integer | frame | shared | display order |
| `decode_index` | integer | frame | shared | decode order |
| `codec` | string enum | - | shared | codec parameters |
| `coded_bit_depth` | integer | bit | shared | codec parameters |
| `qp_map` | array of number | `qp_scale` | shared | - (FFmpeg exposes per-block QP for some decoders) |
| `granularity`, `map_cols`, `map_rows`, `block_size_log2` | enum, integer | -, element, element, log2 px | shared | (geometry of `qp_map`) |
| `header_bits`, `residual_bits` | integer | bit | encoder_only | - |
| `psnr_y`, `psnr_u`, `psnr_v` | number | dB | encoder_only | - |
| `ssim_y` | number | linear | encoder_only | - |
| `metric_source` | string enum | - | encoder_only | - |
| `intra_fraction`, `inter_fraction`, `skip_fraction` | number | area share | encoder_only | - |
| `scene_cut` | boolean | - | encoder_only | - |
| `bits_map`, `mode_map` | array | bit, enum | encoder_only | - |
| `adapter` | string enum | - | encoder_only | - |

"GOP position" is not stored. It is derived as the display distance from the
last `key_frame`. If VMAFx stores it, its own name (for example
`frames_since_key`) is a VMAFx-only field.

## Input for any encoder (issue #221)

The new header `pelorus/telemetry.h` includes only `pelorus/interop.h`,
`<stddef.h>` and `<stdint.h>`. It holds no FFmpeg, SDK or OS types, and a
compile test builds it without FFmpeg include paths.

```c
typedef struct PelorusEncTelemetryInput {
    PelorusEncTelemetrySection frame; /* scalars, enums, present_mask; map offsets ignored */
    const int16_t *qp_map;            /* map_cols*map_rows, Q2; NULL unless PEL_TLM_F_QP_MAP */
    const uint32_t *bits_map;         /* NULL unless PEL_TLM_F_BITS_MAP                      */
    const uint8_t *mode_map;          /* NULL unless PEL_TLM_F_MODE_MAP                      */
} PelorusEncTelemetryInput;

pel_result pel_enc_telemetry_validate(const PelorusEncTelemetryInput *in);
pel_result pel_enc_telemetry_blob_size(const PelorusEncTelemetryInput *in, size_t *out_len);
pel_result pel_enc_telemetry_pack(const PelorusSideData *meta, const PelorusEncTelemetryInput *in,
                                  uint8_t *buf, size_t cap, size_t *out_len);
pel_result pel_tlm_adapter_caps(uint8_t adapter, uint8_t codec, uint64_t *out_mask);
pel_result pel_qp_normalize(uint8_t qp_scale, uint8_t coded_bit_depth, float qp, float *out_norm);
/* reader: checks 1 to 5 for one map bit */
pel_result pel_enc_telemetry_map(const uint8_t *blob, size_t len,
                                 const PelorusEncTelemetrySection *t, size_t got, uint64_t map_bit,
                                 const void **out_ptr, uint32_t *out_elems);
```

The packer builds on `pel_blob_pack_into()` (`interop.h`), the non-allocating
form of `pel_blob_pack()`; `pel_blob_pack_into(meta, secs, nb, NULL, 0, &len)`
returns `PEL_ERR_RANGE` with the length the image needs. Leave the
`*_map_offset` and `*_map_size` members of `frame` zero: the packer computes
them, and validation rejects a non-zero one under a clear bit.

The contract for callers outside FFmpeg (VMAFx's codec-adapter package,
VMAFx/vmafx#2147):

- Fill `frame` with `adapter = external`, and set the bits for exactly the
  fields you have.
- Pass map pointers that you own. They are read during the call, never kept.
- Size `buf` once with `pel_enc_telemetry_blob_size()` for your largest
  frame. `pel_enc_telemetry_pack()` writes the UUID, header, directory,
  section and maps into it, and allocates nothing. A short `buf` returns
  `PEL_ERR_RANGE` with the needed length in `*out_len` and is left untouched.
- An out-of-range enum, an oversized grid or a bit-value mismatch returns
  `PEL_ERR_INVALID` or `PEL_ERR_RANGE`, never a partial blob.

The FFmpeg adapter reads the documented `AV_PKT_DATA_QUALITY_STATS` bytes
(`u32le` quality, `u8` picture type, `u8` error count, `u16` reserved,
`u64le` SSE per error) as a byte buffer. libpelorus therefore never includes
FFmpeg headers. A quality factor of 0 means "no QP": `libaomenc` writes 0
there (research 0174, E2).

## Field-name parity (issue #220)

Both repositories load the same registry file,
`libpelorus/schema/telemetry-fields.json`. Pelorus owns it, and VMAFx mirrors
it byte for byte from VMAFx/vmafx#2271 on. Each repository checks the
direction it can break:

| File | Owner | Loaded by |
|---|---|---|
| `libpelorus/schema/telemetry-fields.json` | Pelorus | Pelorus check; VMAFx check (through the mirror) |
| VMAFx stream-metadata field list (same shape, `schema` `vmafx/stream-metadata-fields/1`, each field marked `pelorus: shared` or `vmafx_only`, plus a top-level `not_carried` list) | VMAFx (#2271) | VMAFx check; Pelorus check through a pinned snapshot `libpelorus/test/fixtures/vmafx-stream-metadata-fields.json` whose `source` member names `VMAFx/vmafx@<40-hex sha>:<path>` |

The comparator
[`scripts/check-telemetry-field-parity.py`](../../scripts/check-telemetry-field-parity.py)
applies these rules and prints both names on any difference:

1. Each file parses, carries its expected `schema`, has at least one field and
   no duplicate key. An empty or malformed file is an error (exit 2), never
   a pass.
2. Every Pelorus `shared` key is either a VMAFx `shared` field with the same
   `json` type and `unit`, or listed in VMAFx's `not_carried`. Otherwise the
   check fails with "missing in vmafx: KEY".
3. Every VMAFx `shared` field exists in the registry as `shared` with the same
   type and unit. Otherwise the check fails with "missing in pelorus: KEY" or
   "type/unit differs: KEY".
4. A VMAFx-only key that equals a registry key fails: the same name must mean
   the same field.
5. Every registry key with a bit has its `PEL_TLM_F_<KEY>` define in
   `interop.h` at that bit, and `interop.h` has no other `PEL_TLM_F_*`.

`--self-test` plants one failing case for each rule (a renamed key on each
side, a one-sided key on each side, an empty file, malformed JSON, a unit
difference, a bit difference) and one matching pair that must pass. Until
VMAFx/vmafx#2271 publishes its list, the fast-suite test runs rule 5 and the
registry checks, then exits 77. Meson reports that as SKIP, with the reason
"VMAFx field list not pinned (VMAFx/vmafx#2271)", not as a pass.
[`docs/metrics/encoder-telemetry.md`](../metrics/encoder-telemetry.md) names
the pinned VMAFx revision once one exists.

```bash
python3 -I scripts/check-telemetry-field-parity.py               # 77 = SKIP today
python3 -I scripts/check-telemetry-field-parity.py --self-test   # 0
python3 -I scripts/check-telemetry-field-parity.py --vmafx FILE  # any VMAFx list
```

## How VMAFx re-vendors ABI 1.4

1. The Pelorus ABI 1.4 pull request merges, and a Pelorus release contains it.
   VMAFx ADR-1276 pins reviewed released commits only.
2. VMAFx sets `PELORUS_VENDOR_SHA` to that commit and runs
   `scripts/sync-pelorus-interop.sh --update <pelorus checkout>`. Then it runs
   the script without `--update`, which must report no drift.
3. The re-pin touches only the existing ten mirrored files: `interop.h`
   (sections i and j, the motion field, the `PEL_TLM_F_*` bits),
   `interop.c` (`section_bit_valid`, `pel_blob_map`,
   `pel_encode_record_digest_text`), `pelorus.h` (`PEL_ERR_MISMATCH`) and the
   conformance fixture. No new manifest row is needed at RC4.
4. One prerequisite is already known on the VMAFx side. `render_vendor`
   expects the former 17-line BSD+Patent header, but Pelorus files now open
   with the 6-line EUPL-1.2 header (ADR-0171). The EUPL re-vendor that
   VMAFx schedules together with ABI 1.4 updates that check. Every file this
   specification adds uses the EUPL header and exactly one
   `#include "pelorus/..."`.
5. With VMAFx/vmafx#2271 (VMAFx 1.1), VMAFx adds a verbatim manifest row for
   `libpelorus/schema/telemetry-fields.json`. JSON has no comment syntax for
   the banner.

## Tests

The rows marked "adapters" arrive with the adapters (#86 items 2 to 4).

| Acceptance (issue) | Test |
|---|---|
| #86 item 1 (adapters): a truncated or field-shifted stats file is an error | each adapter's reader on a planted truncated and a column-shifted fixture returns `PEL_ERR_TRUNCATED`/`PEL_ERR_INVALID` and writes no record |
| #86 item 2 (adapters): matched QP agrees within one step | libx264, libx265, libsvtav1 fixture records at table-matched step size: `abs(avg_qp_norm` difference`) <= 1.0`; a planted adapter without conversion fails |
| #86 item 3: "not reported", not zero | `libpelorus/test/telemetry_test.c`: a value under a clear bit fails, a reported 0 dB passes, a set bit outside an adapter's (empty) mask fails; per-adapter JSON cases arrive with the adapters |
| #86 item 4 and #218: layout lock and fixture | `_Static_assert` sizes 104, 48, 36 plus every member offset in `interop.h`; `libpelorus/test/interop_test.c` packs and parses every new section and reads a 1.3-sized motion section as absent by size; `ffmpeg-patches/test/mc_stats_test.c` (`bsize` 8, 16, 32 write 3, 4, 5; other edges write 0); `ffmpeg-patches/test/pelorus_sidedata_test.c` (the named edge wins, also on a 1x1 frame and on grids that inference cannot resolve) |
| #221 | `libpelorus/test/telemetry_test.c` includes `pelorus/telemetry.h` first and builds without FFmpeg include paths; a hand-filled `external` input packs and parses; an out-of-range enum and an oversized grid return `PEL_ERR_INVALID`/`PEL_ERR_RANGE` |
| #220 | the Meson tests `telemetry-field-parity` (SKIP until VMAFx/vmafx#2271) and `telemetry-field-parity-self-test` |
