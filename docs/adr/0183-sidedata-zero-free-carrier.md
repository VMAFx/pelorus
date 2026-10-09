<!-- markdownlint-disable MD013 MD060 -->
# ADR-0183: NVENC writes Pelorus side data in a zero-free COBS carrier under its own UUID (interop ABI 1.5)

- **Status**: Proposed
- **Implementation**: pending (#284)
- **Date**: 2026-10-10
- **Deciders**: Lusoris
- **Tags**: interop, nvenc, ffmpeg, vmafx, abi

## Context

Pelorus filters attach their blob to each frame as
`AV_FRAME_DATA_SEI_UNREGISTERED`, and an encoder with `-udu_sei 1` writes it
into the stream as a user data unregistered SEI message
([ADR-0103](0103-interop-sidedata-abi.md), `docs/usage/ffmpeg.md`). VMAFx reads
it back from the decoded frames (`vf_libvmaf` `perceptual_weight`).

`h264_nvenc` and `hevc_nvenc` write a SEI payload truncated, without its
trailing bits, when the payload needs many emulation prevention bytes. The
decoder drops the message and the frame reaches VMAFx with no side data and no
error (issue #284). The rule is pinned exactly on an RTX 4090, driver
615.78.08, for both codecs and for IDR and P pictures: a payload of `P` bytes
(UUID included) is truncated when its emulation prevention bytes exceed
`ceil(P / 3) + 3`; with several entries in one picture the limit follows the
largest one (`.workingdir/evidence/nvenc-sei-zero-run-rule/results.md`,
0 mismatches over about 77 000 payloads). Emulation prevention comes from
pairs of zero bytes, and the per-cell maps of `pelorus_analyze_vulkan` on flat
content are mostly zeros: at 1920x1080 with 32-pixel cells the 12 424-byte blob
holds a run of 9 968 zero bytes. Measured with the real blobs of 48 analyze
cases (320x180 to 1920x1080; flat, gradient and detailed content; `maps=0` and
`maps=1` at cells 16, 32 and 64; 8 frames each), 11 of the 48 cases lose the
side data of every picture on `h264_nvenc`, and on `hevc_nvenc` the one case
that fits patch 0022's budget whole (320x180 flat, cell 32) does too
([research 0183](../research/0183-sidedata-zero-free-carrier.md)).

The fix belongs at the carrier, not in the blob layout: the blob is a fixed,
pointer-free image read in place by every in-graph consumer (R5), and the
defect is in one encoder's SEI writer. Any change to what goes on the wire is
an interop contract change that VMAFx must follow, so it is an ABI minor bump
and needs a rollout order with the VMAFx mirror
([mirror contract](../api/mirror-contract.md)).

## Decision

We add a second wire form of the blob, the zero-free carrier, in interop ABI
1.5 (`PELORUS_ABI_MINOR` 5), and NVENC writes it:

1. **Form.** The carrier of a blob is the 16-byte
   `pelorus_carrier_uuid` (`3f9b37b8-fd9a-4621-920e-9b78b55cf9b5`, a version-4
   UUID without a zero byte) followed by the COBS encoding (Cheshire and Baker,
   1999, no frame delimiter) of the blob image, that is everything after
   `pelorus_sidedata_uuid`. A block is a code byte `c` in 1..255 and `c - 1`
   non-zero bytes; every block except a full one (255) and the last stands
   for one zero byte; a full block that ends the image gets no empty block
   after it. The carrier holds no zero byte, so its NAL unit needs no emulation
   prevention: it can never meet NVENC's rule, whatever the content. It costs
   one byte plus one per 254 bytes without a zero (`PEL_CARRIER_MAX_LEN`).
2. **API.** `interop.c` gains `pel_blob_carrier_encode()` (blob to carrier,
   caller buffer, no allocation) and `pel_blob_unwrap()` (either form to the
   blob: a blob is returned as is, a carrier is decoded into a caller buffer
   of at least the payload's length). The decoder is strict: a zero byte, a
   block past the end, an empty final block after a full one, or an image
   shorter than the header fail, so `encode(decode(x)) == x` for every
   accepted payload. Readers then use `pel_blob_find_section()` and
   `pel_blob_map()` unchanged. No section, field or bit changes.
3. **Writers.** Filters keep attaching the blob; in-graph consumers are
   unchanged. Patch 0022 converts every Pelorus blob (blob UUID, magic, major
   1) into its carrier in `h264_nvenc` and `hevc_nvenc` with `udu_sei`, and
   writes any other payload unchanged. There is no option: the blob form is
   lossy on NVENC for the flat content Pelorus targets. QSV (patch 0019),
   Vulkan Video (patch 0020) and the stock software encoders keep writing the
   blob: they escape zero runs correctly (`h264_qsv` carried a 12 424-byte
   zero-heavy blob intact; the Vulkan encoders write through CBS).
4. **Budget (patch 0022, [ADR-0181](0181-hevc-nvenc-sei-header-budget.md)).**
   `hevc_nvenc` charges a Pelorus blob as its carrier. A blob that does not
   fit loses its maps as before (`pel_sei_strip_maps()` on the blob), then
   becomes a carrier, which is charged again; the carrier unwraps to the
   `maps=0` blob. Without emulation prevention the carrier's NAL unit is
   smaller than the blob's in all 48 measured cases, so more grids keep their
   maps (320x180 flat at cell 32: 747 to 560 bytes).
5. **Readers and rollout.** A reader calls `pel_blob_unwrap()` on every
   `AV_FRAME_DATA_SEI_UNREGISTERED` entry before parsing. A reader built on
   ABI 1.4 sees the carrier as a foreign SEI and ignores it (R3); it never
   misreads it. Pelorus ships writer and reader in one release; VMAFx re-pins
   to it (an ABI minor bump is a re-pin trigger) and adds the call. Until
   then VMAFx scores frames from NVENC streams unweighted.

## Alternatives considered

Measured with the 48 real analyze blobs, 8 frames each, through stock n9.0.2
NVENC on the RTX 4090 (`hevc_nvenc` only where the payload fits its 1024-byte
picture limit: 21 cases for the blob form, 38 for zlib). "Intact" counts
frames whose decoded payload equals the one written.

| Option | Intact frames `h264_nvenc` / `hevc_nvenc` | Overhead | Pros | Cons | Why not chosen |
| --- | --- | --- | --- | --- | --- |
| (a) COBS carrier under a new UUID (chosen) | 384/384 / 168/168; also 60 000 zero bytes and 60 000 random bytes as carriers | +1 to +70 bytes (at most 0.54 %), bound `n + 1 + n/254` | No zero byte, so no emulation prevention by construction, independent of NVENC's rule or a future driver's; standard, small strict decoder; smaller NAL than the blob | ABI 1.4 readers lose NVENC side data until they re-pin; one more wire form | Chosen |
| (b) XOR with a fixed xorshift32 sequence (runner-up) | 384/384 / 168/168 | 0 bytes | No size cost; trivial decoder | A zero byte wherever the blob equals the sequence: up to 211 zeros per blob here, none paired, but no guarantee; content that correlates with the sequence reaches the limit | Rejected: the margin depends on content, COBS's does not |
| (c) zlib, level 9 | 384/384 / 304/304 | -99.5 % (flat) to -20 % (detailed) | Flat maps shrink to under 240 bytes, so more fit hevc_nvenc's budget | Output still has zero runs (up to 39 bytes, 22 emulation prevention bytes on flat 1080p); a zlib dependency in `interop.c`, which VMAFx vendors dependency-free (AGENTS.md rule 9); no bound, as ADR-0181 found | Rejected: no guarantee and a new dependency |
| (d) Split the blob into four SEI payloads | 227/384 / 152/168 | +48 bytes (UUIDs) | Smaller messages | Zero runs stay (up to 12 283 bytes in one piece); NVENC's limit follows the largest entry, so splitting cannot lower it | Rejected by measurement |
| (e) Write the carrier from the filters | as (a) | as (a) | One form everywhere | Every in-graph consumer (denoise, scenecut, analyze, VMAFx in-graph) must decode a copy per frame, losing the in-place read (R5) and adding a per-frame buffer (HISS-03), to fix one encoder | Rejected: the defect is in the carrier, the fix stays there |
| An encoder option to keep the blob form for ABI 1.4 readers | 296/384 / 160/168 (the blob form) | 0 | VMAFx before its re-pin keeps reading blobs that survive | Keeps a known-lossy path selectable; one VMAFx re-pin closes the gap | Rejected |
| Write the carrier only when a blob is at risk | as (a) where used | as (a) | ABI 1.4 readers keep the blobs NVENC writes intact | The predicate is NVENC's buffer rule, measured on one driver; a carrier needs none | Rejected |

## Consequences

- **Positive**: Pelorus side data reaches the decoder intact through
  `h264_nvenc` and `hevc_nvenc` for every measured case, maps included on
  H.264 and on HEVC where the carrier fits the budget. The carrier needs no
  knowledge of NVENC's rule, so a driver that changes it does not reopen
  #284. Decode cost: at most 0.17 ms for a 49 KB blob on a debug build.
- **Negative**: VMAFx and any other reader must call `pel_blob_unwrap()`; a
  reader pinned at ABI 1.4 scores NVENC streams unweighted until it re-pins.
  The COBS encoder exists twice, in `interop.c` and in
  `ffmpeg-patches/files/pelorus_sei_fit.h` (libavcodec does not link
  libpelorus); `sei_fit_test.c` compares their output byte for byte. The
  tester's `sidedata_roundtrip` stage must learn the carrier UUID.
- **Neutral / follow-ups**: QSV fails on size, not on zero runs, and the
  carrier does not fix it: `hevc_qsv` (Arc A380, iHD 26.3.5, vpl-gpu-rt
  26.3.5) writes the tail of a payload over 4 089 bytes onto the start of the
  access unit, so from 4 091 bytes no picture decodes and from about 11 000
  bytes the encode fails; `h264_qsv` fails at 49 144 bytes. See the hevc_qsv
  size issue and the QSV table of the research digest. AV1 is not a
  carrier: metadata OBUs have no emulation prevention and no Pelorus AV1
  encoder writes `udu_sei`. The NVENC defect itself is reported upstream only
  with the maintainer's go (issue #284).

## References

- Issue [#284](https://github.com/VMAFx/pelorus/issues/284) and its acceptance list (req); maintainer decision for the release: hold rc.2 for a full fix, detection now and the ABI fix through an ADR (paraphrased, req).
- Research: [0183 zero-free carrier](../research/0183-sidedata-zero-free-carrier.md); NVENC rule: `.workingdir/evidence/nvenc-sei-zero-run-rule/results.md` (local).
- [ADR-0103](0103-interop-sidedata-abi.md) (R3, R5), [ADR-0181](0181-hevc-nvenc-sei-header-budget.md) (patch 0022 budget), [ADR-0177](0177-analyze-per-cell-maps.md) (maps).
- S. Cheshire and M. Baker, "Consistent Overhead Byte Stuffing", IEEE/ACM Transactions on Networking 7(2), 1999.
- FFmpeg `n9.0.2`: `ff_h2645_extract_rbsp()` (`libavcodec/h2645_parse.c:37`, emulation prevention removal) and `decode_unregistered_user_data()` (`libavcodec/h2645_sei.c:67`, the decoder's export as `AV_FRAME_DATA_SEI_UNREGISTERED`).
