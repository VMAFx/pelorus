<!-- markdownlint-disable MD013 MD060 -->
# Research 0174: evidence for interop ABI 1.4 (encoder telemetry, encode record, motion block size)

Evidence behind [ADR-0174](../adr/0174-encoder-telemetry-abi-1-4.md) and
[ADR-0175](../adr/0175-encode-provenance-record.md). Each item names its
source and the command that reproduces it. All items were read on
2026-10-08.

**Sources.**

- Pelorus `origin/master` `e2e4040` (v0.3.0).
- VMAFx `origin/master` `ab16559a3`, read through `git show` only.
- FFmpeg checkout `n9.0.1` (`bf1b838f2a`). The Pelorus patch base is
  `n9.0.2`. The FFmpeg items below are not re-read at `n9.0.2`, and a point
  release is not expected to change them.

## E1. The proposed layouts have no implicit padding

The three 1.4 structs were compiled in a scratch file outside the
repository: GCC and Clang with `-std=c11 -Wall -Wextra -Wpadded -Werror`,
and `gcc -m32`. The asserts held:

| Struct | `sizeof` | Alignment (x86_64) | Checked offsets |
|---|---:|---:|---|
| `PelorusEncTelemetrySection` | 104 | 8 | `map_cols` 88, `codec` 92, `adapter` 100 |
| `PelorusEncodeRecordSection` | 48 | 4 | `locator_offset` 32, `digest_alg` 40 |
| `PelorusMotionSection` (1.4) | 36 | 4 | `block_size_log2` 32 |

`-Wpadded` printed nothing, so no member gained implicit padding. On i386,
`uint64_t` inside a struct aligns to 4 and the sizes stay the same. R5
already limits v1 hosts to x86_64 and aarch64, so the plan asserts sizes, not
alignment. The worst-case map payload, `(1 << 20) * (2 + 4 + 1)`, is
7 340 032 bytes.

## E2. What FFmpeg's cross-encoder channel carries

- `libavcodec/packet.h:118-129` documents `AV_PKT_DATA_QUALITY_STATS`:
  `u32le` quality factor (1 good .. `FF_LAMBDA_MAX`), `u8` picture type,
  `u8` error count, `u16` reserved, then `u64le` SSE per error.
- `libx265.c:934`: `ff_encode_add_stats_side_data(pkt, x265pic_out->frameData.qp * FF_QP2LAMBDA, NULL, 0, pict_type)`.
- `libsvtav1.c:761`: `headerPtr->qp * FF_QP2LAMBDA`.
- `libaomenc.c:1143`: quality factor `0` and the SSE array when `have_sse`.
  libaom therefore reports no QP through this channel.
- `libavutil/avutil.h:226`: `FF_QP2LAMBDA 118`. `:277-284`:
  `AV_PICTURE_TYPE_NONE = 0`, `I`, `P`, `B`, `S`, `SI`, `SP`, `BI`.
  `pel_picture_type` reuses 1, 2 and 3.

Reproduce: `grep -n 'ff_encode_add_stats_side_data' libavcodec/lib{x265,svtav1,aomenc}.c`
in the FFmpeg checkout.

## E3. The AV1 and VP9 normalisation constants are not verified

`avg_qp_norm` for the qindex scales needs the codec's AC quantiser table and
the scale between its step size and H.264's. Neither was read for this
specification. Treat any remembered constant as unverified. The adapter pull
request reads the tables from libaom and libvpx source at a pinned revision,
cites them, and must pass #86 acceptance item 2 (within 1.0 across libx264,
libx265 and libsvtav1).

## E4. The scale of SVT-AV1's `headerPtr->qp` is not verified

E2 shows that FFmpeg multiplies the value by `FF_QP2LAMBDA`. Whether it is
the encoder's 0..63 scale or `base_q_idx` was not read. The SVT-AV1
adapter decides between `av1_encoder_qp` and `av1_qindex` from the SVT-AV1
source.

## E5. A Pelorus blob can reach the bitstream

`udu_sei` ("Use user data unregistered SEI if available") is an encoder
option at `libx264.c:1570`, `libx265.c:1051`, `nvenc_h264.c:167` and
`nvenc_hevc.c:153`. With it set, these encoders write frame side data of type
`AV_FRAME_DATA_SEI_UNREGISTERED` (the Pelorus carrier, ADR-0103) into the
stream. The copy loops are at `libx264.c:579`, `libx265.c:822` and
`nvenc.c:2759`. `grep -rln udu_sei libavcodec/` finds no AV1 encoder
wrapper with the option. A per-encoder
carriage test is a follow-up of ADR-0175.

## E6. What VMAFx accepts as an encode-record digest

- VMAFx `core/src/vmafx/provenance.c:603` (`valid_encode_record`) accepts
  only `sha256:` followed by exactly 64 characters in `0-9a-f`. `:627`
  rejects anything else with `VMAFX_E_INVALID`. NULL or an empty string
  clears the field.
- `core/include/vmafx/provenance.h:327` declares
  `vmafx_context_set_encode_record()`.
- VMAFx ADR-2073 item 2 defines the canonical form (RFC 8785, keys in byte
  order, no whitespace, plain decimal integers, strings escaping only `"`,
  `\` and control characters, invalid UTF-8 replaced with U+FFFD) and the
  digest (`sha256:` over the text without `digest` and `elapsed_ns`). Its
  tests use Python `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
  as the independent implementation.

## E7. Python's escaping matches RFC 8785 for all-string records

`json.dumps("a\x01\x1f\b\t\n\f\r\"\\/\x7f é", ensure_ascii=False)`
prints `"a\u0001\u001f\b\t\n\f\r\"\\/` followed by DEL, U+2028 and `é`
unescaped. That matches the RFC 8785 string rules: short escapes for
`\b \t \n \f \r`, lower-case `\u00xx` for other control characters, nothing
else escaped. Because ADR-0175 records hold no numbers, RFC 8785's
ECMAScript number formatting never applies. Python prints `1e-07` where
ECMAScript prints `1e-7`, which is why reals are written as `f64:` bit
strings. The worked example in [encode-record.md](../api/encode-record.md)
was recomputed from the page's own JSON block with the page's own Python
snippet. It gives
`sha256:b0201cec9782a58a59541103a7757fbd8221f67bd45a32f3b6a8d7f054d244bc`
over a 596-byte canonical text.

## E8. The VMAFx mirror's rendering rules

VMAFx `scripts/sync-pelorus-interop.sh` imposes these rules:

- `:155` requires a 17-line licence header followed by a blank line in every
  mirrored file. That is the BSD+Patent header at the current pin `11e183e`
  (`v0.2.2-12-g11e183e`). Pelorus files now open with the 6-line EUPL-1.2
  header (ADR-0171), so the next VMAFx re-vendor must change this check
  whatever ABI 1.4 contains.
- `:163` requires exactly one `#include "pelorus/<x>.h"` per file to
  rewrite. `interop.h` can therefore not include a second Pelorus header,
  and `telemetry.h` includes only `pelorus/interop.h`.
- The manifest `scripts/ci/pelorus-mirror-paths.txt` lists ten exact paths:
  four headers, five sources and the fixture. A new file needs a VMAFx
  manifest row.

## E9. VMAFx already has a reviewed SHA-256

VMAFx `core/src/vmafx/sha256.c` (198 lines) and `sha256.h` implement
FIPS 180-4 streaming SHA-256 with lower-case hex output. The copyright holder
is Lusoris and the licence EUPL-1.2. `core/test/test_vmafx_sha256.c` checks
them against the standard's example messages. ADR-0175 ports this file
instead of adding a dependency.

## E10. VMAFx has no stream-metadata schema yet

`git grep -i -E 'stream.metadata|bitstream.facts'` over VMAFx `docs/adr`,
`docs/api`, `docs/usage` and `proto` finds nothing. The only JSON schemas in
the tree are Helm values, hardware reports and the tiny-model registry.
VMAFx/vmafx#2271 (VMAFx 1.1) owns that schema. Until it exists, the parity
test of #220 can check only the Pelorus side, and it reports SKIP for the
VMAFx side.

## E11. Motion producers and the block size

- `vf_pelorus_mc_vulkan.c:116` sets `bsize` to the block edge in luma pixels
  (8, 16 or 32). `:684-685` derives the grid from it.
- `:319` zeroes the motion section with `memset`. 1.3 blobs from this
  producer therefore have zero pad bytes. ADR-0174 still detects 1.3 blobs by
  size, not by value.
- `vf_pelorus_analyze_vulkan.c:535-591` packs `PEL_SEC_VARIANCE`,
  `PEL_SEC_BANDING` and `PEL_SEC_COMPLEXITY` only. `vf_pelorus_mc` is the one
  motion writer.
- Readers guard tail fields with
  `PEL_SD_FIELD_OK` (`ffmpeg-patches/files/pelorus_sidedata.h:48`).
