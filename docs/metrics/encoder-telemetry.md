<!-- markdownlint-disable MD013 MD060 -->
# Encoder telemetry: field names, the VMAFx check and the adapter contract

Interop ABI 1.4 carries what an encoder did per coded frame in one
normalised section, `PEL_SEC_ENC_TELEMETRY`. The layout, every bit and every
validation rule are on the API page,
[encoder-telemetry.md](../api/encoder-telemetry.md); the decision is
[ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md). This page answers three
operational questions: where the field names live, how the VMAFx name check
runs, and what a caller outside FFmpeg fills in.

Three software-encoder adapters ship: x265 CSV, SVT-AV1 stat files, and FFmpeg
`AV_PKT_DATA_QUALITY_STATS` packet side data. All three read caller-provided
buffers and write caller-provided frame records; the library performs no file
I/O or allocation in its record loops. One transient C numeric-locale handle
per call makes text adapters process-locale independent. Callers supply the
coded bit depth because none of the formats record it.

## Adapter coverage

`yes` means the parser fills the field and sets its presence bit. `caller`
means the parser fills the field from the `coded_bit_depth` argument. `-`
means the bit stays clear and its value stays zero: not reported, not a
reported zero. These rows are the exact masks returned by
`pel_tlm_adapter_caps()`.

| Field | x265 CSV level 1 | SVT-AV1 stat file | FFmpeg quality stats |
|---|---:|---:|---:|
| `display_index` | yes | yes | - |
| `decode_index` | yes | - | - |
| `frame_bytes` | yes | yes | - |
| `header_bits` | - | - | - |
| `residual_bits` | - | - | - |
| `avg_qp` | yes | yes | yes |
| `avg_qp_norm` | yes | yes | yes |
| `psnr_y` | - | yes | yes |
| `psnr_u` | - | yes | yes |
| `psnr_v` | - | yes | yes |
| `ssim_y` | - | yes | - |
| `intra_fraction` | - | - | - |
| `inter_fraction` | - | - | - |
| `skip_fraction` | - | - | - |
| `picture_type` | yes | - | yes |
| `key_frame` | yes | - | - |
| `reference` | yes | - | - |
| `shown` | yes | - | - |
| `scene_cut` | yes | - | - |
| `qp_map` | - | - | - |
| `bits_map` | - | - | - |
| `mode_map` | - | - | - |
| `coded_bit_depth` | caller | caller | caller |

x265's `POC`, `Encode Order`, `Bits`, `QP`, `Type` and `Scenecut` columns
supply its fields. SVT-AV1's `Picture Number`, `Average QP`, per-plane PSNR,
luma SSIM and byte count supply its fields. FFmpeg's `quality`, `picture_type`
and `error_count` SSE planes supply its fields. A malformed, truncated or
field-shifted row returns an error before any output record is written.

The x265 `Bits` value must be divisible by eight. In x265 4.3 commit
`e9b88125dc21393b3fd8d68e98083bdfb89778a8`, the frame encoder derives the
access-unit count as [`bytes << 3`](https://github.com/Multicorewareinc/x265/blob/e9b88125dc21393b3fd8d68e98083bdfb89778a8/source/encoder/frameencoder.cpp#L1242),
the encoder copies that count to
[`frameStats->bits`](https://github.com/Multicorewareinc/x265/blob/e9b88125dc21393b3fd8d68e98083bdfb89778a8/source/encoder/encoder.cpp#L3150-L3231),
and the CSV writer emits that field as
[`Bits`](https://github.com/Multicorewareinc/x265/blob/e9b88125dc21393b3fd8d68e98083bdfb89778a8/source/encoder/api.cpp#L1441-L1448).
A non-multiple therefore cannot be an x265 4.3 frame byte count and is rejected
instead of rounded.

## Where the field names live

The registry
[`libpelorus/schema/telemetry-fields.json`](../../libpelorus/schema/telemetry-fields.json)
lists all 31 keys with their bit, JSON type, C type, unit, range and whether
the decode side shares them (`decoder`: `shared` or `encoder_only`). The
`PEL_TLM_F_<KEY>` defines in
[`interop.h`](../../libpelorus/include/pelorus/interop.h) must match it bit for
bit. A rename is a change in three places: the registry, `interop.h` and the
API page.

## The VMAFx name check (issue #220)

VMAFx's decode-side stream metadata (VMAFx/vmafx#2271) uses the `shared` names
with the same type and unit. The check is
[`scripts/check-telemetry-field-parity.py`](../../scripts/check-telemetry-field-parity.py);
the fast suite runs it twice:

| Meson test | Command | Result today |
|---|---|---|
| `telemetry-field-parity` | `python3 -I scripts/check-telemetry-field-parity.py` | pass: "pelorus and vmafx agree (17 shared fields)", after the registry and `interop.h` checks pass |
| `telemetry-field-parity-self-test` | `python3 -I scripts/check-telemetry-field-parity.py --self-test` | pass: a planted failure for every rule is rejected, a matching pair passes |

**Pinned VMAFx revision: `243904ab13ea58ebce86d62e727b3289042fbf54`** (VMAFx pull request 2773,
`api/stream-metadata-fields.json`). Its file is copied unchanged, apart from a
`source` member `VMAFx/vmafx@243904ab13ea58ebce86d62e727b3289042fbf54:api/stream-metadata-fields.json`,
to `libpelorus/test/fixtures/vmafx-stream-metadata-fields.json`. A difference
fails with both names printed, for example `missing in vmafx: frame_bytes` or
`type/unit differs: avg_qp`. An empty or malformed file, or a `source` that
names no 40-hex commit, exits 2 and is never a pass. A missing file exits 77;
the test is not on the declared skip list (`.config/test-skips.json`), so
`scripts/check-test-skips.py` fails that SKIP. To move the pin, copy the newer
VMAFx file, set `source` to its commit and update this paragraph.

## Filling a record outside FFmpeg (issue #221)

`pelorus/telemetry.h` includes only `pelorus/interop.h` and the C standard
headers; the telemetry test compiles it with no FFmpeg include path. A
Pelorus-side producer that drives an encoder without FFmpeg:

1. Fills `PelorusEncTelemetryInput.frame` with `adapter =
   PEL_TLM_ADAPTER_EXTERNAL`, the codec, `granularity`, and a
   `present_mask` bit for exactly the fields it has. A field it does not have
   keeps its bit clear and its value zero: that is "not reported", which is
   different from a reported zero.
2. Points `qp_map`, `bits_map` and `mode_map` at its own arrays for the maps
   it reports (`map_cols * map_rows` elements, at most 1 048 576). They are
   read during the call and never kept.
3. Sizes one buffer with `pel_enc_telemetry_blob_size()` for its largest frame
   and calls `pel_enc_telemetry_pack()` per frame. Nothing is allocated, and an
   invalid record returns `PEL_ERR_INVALID` or `PEL_ERR_RANGE` without
   writing.

VMAFx's codec-adapter package (VMAFx/vmafx#2147) uses steps 1 and 2 and
`pel_enc_telemetry_validate()` for its own records. It does not pack: a blob
under the Pelorus UUID has one writer, Pelorus, and a VMAFx-origin annotation
uses VMAFx's own UUID ([interop-abi.md](../api/interop-abi.md#sections),
VMAFx ADR-1113).

`pel_qp_normalize()` is the identity for H.26x `slice_qp`. For SVT-AV1's
0..63 encoder QP it maps through the AV1 quantizer-to-qindex table and the
luma AC QTX table. For coded bit depth `bd`, the conversion is
`qstep_8bit = ac_q / (8 << (bd-8))`, followed by
`avg_qp_norm = 6 * log2(qstep_8bit / 0.625)`. The luma AC quantizer is chosen
because `avg_qp_norm` is defined as the frame's luma AC step; chroma deltas and
DC quantizers do not define that common scale.

SVT-AV1 v4.2.0 writes `Average QP` as its rounded mean qindex divided by four,
so the stat-file adapter first multiplies that field by four (clamped to
qindex 255) and inversely interpolates SVT-AV1's 64-entry encoder-QP mapping.
The record's `avg_qp` and `qp_scale` therefore agree in the
`av1_encoder_qp` domain. The source field's integer rounding limits this
reconstruction to 0.5 encoder-QP; in particular, the valid fixed-QP-63 value
`Average QP: 64` reconstructs encoder QP 63 instead of failing the 0..63
range check. [Research 0174 E4](../research/0174-abi-1-4-telemetry-provenance.md#e4-svt-av1-stat-file-and-ffmpeg-values-are-separate)
pins the SVT-AV1 calculation and mapping.

Fractional encoder QP is interpolated in the log-step domain: Pelorus computes
the normalized value at the two adjacent integer-QP table entries and linearly
interpolates those normalized values. This is equivalent to interpolating
`log2(qstep_8bit)`, not `ac_q`, and keeps fractional normalized QP linear on the
same logarithmic scale as the integer anchors. Raw AV1 and VP9 qindex scales
remain unsupported in this round. [Research 0174 E3](../research/0174-e3-av1-qp-scale.md)
records the pinned libaom sources and the 192-value comparison.
