<!-- markdownlint-disable MD013 MD060 -->
# ADR-0181: hevc_nvenc writes Pelorus side data within NVENC's 1024-byte header limit, without the per-cell maps when they do not fit

- **Status**: Proposed
- **Implementation**: pending (#267)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: nvenc, interop, analyze, ffmpeg
- **Amended by**: [ADR-0185](0185-shared-ffmpeg-fix-series.md): the budget, the drop of a payload that does not fit and their log lines are patch 0003 of the shared FFmpeg fix series; patch 0022 keeps the map stripping. The decision is unchanged.

## Context

Since the analyze maps landed ([ADR-0177](0177-analyze-per-cell-maps.md)),
`hevc_nvenc -udu_sei 1` stops with `Cannot allocate memory` whenever
`pelorus_analyze_vulkan` runs with its default `maps=1` (issue #267).
`h264_nvenc`, `hevc_vulkan` and `h264_vulkan` carry the same blob.

The error is NVENC's, not FFmpeg's or Pelorus's. `nvEncLockBitstream()`
returns `NV_ENC_ERR_OUT_OF_MEMORY` (10), which FFmpeg's `nvenc.c` maps to
`AVERROR(ENOMEM)` in its error table and reports as
`Failed locking bitstream buffer: out of memory (10)`. No Pelorus patch
touches that path. Measured on an RTX 4090 with driver 615.71.09
([research 0181](../research/0181-hevc-nvenc-sei-header-budget.md)), the
HEVC encoder refuses a picture whose non-VCL NAL units exceed 1024 bytes:
VPS, SPS, PPS, access unit delimiter and every SEI NAL unit, in Annex B
form with start codes and emulation prevention bytes. The cap is the same
on IDR and P pictures, at every resolution and preset tried, and for one
large SEI or several small ones. `h264_nvenc` carries a 64 KiB payload.
Neither `nvEncodeAPI.h` (nv-codec-headers `n12.1.14.0` and
`13.1.15.0.1`) nor the NVENC programming guide 13.1 states a size limit
for `seiPayloadArray`.

The analyze blob with maps is 12 424 bytes at 1080p and 48 KiB at 2160p
with `cell=32`; without maps it is 184 bytes. At the end of a stream the
same over-limit picture is lost with only an error line, so a one-frame
test passes while the picture is missing.

## Decision

Patch 0022 budgets the SEI that `hevc_nvenc` passes to NVENC: 768 of the
1024 bytes per picture, minus the A/53 and timecode SEI already queued. The
other 256 bytes stay for the NAL units NVENC writes itself (128 bytes
measured at 8K Main10 with VUI and AUD, plus the HDR10 and 3D reference
display SEI it builds from picture parameters). Each
`AV_FRAME_DATA_SEI_UNREGISTERED` entry, in frame order, is written whole
when it fits. A Pelorus blob that does not fit is written without the
per-cell maps appended after its sections: every scalar stays, and each
map offset and size is set to zero, which the ABI defines as an absent
map. The result is byte-identical to the analyze filter's `maps=0` blob.
Any other payload that does not fit is not written. The first stripped
blob and the first dropped payload are warnings that name the frame, the
sizes and the limit; later ones are verbose; the encoder logs both totals
when it closes. Consumers that need the maps read them from the frame side
data in the filter graph, before the encoder. No interop ABI change.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Strip the maps over the limit, keep the scalar sections (chosen) | The encode always completes; every scalar section reaches the stream; the stripped blob is a valid `maps=0` blob every reader already handles; no ABI change | The maps reach an `hevc_nvenc` stream only up to about 90 cells (1080p at `cell=32` has 2 040); the size logic copies part of the interop layout into `nvenc.c` | Chosen |
| Split the blob across several SEI messages | Keeps the maps | The limit is per picture, not per message: two payloads fit 495 bytes each, one fits 999 bytes. Spreading maps over later pictures breaks the per-frame binding | Rejected by measurement |
| Insert the SEI into the packet after `nvEncLockBitstream()` (runner-up) | Keeps the maps in the stream at any size | NVENC's rate control and HRD buffering SEI never see those bytes (2.4 Mbit/s at 1080p25 with maps), so the stream can break its own VBV model; Annex B parsing and escaping in a stock hot path | Rejected: a conformant stream matters more than maps in the bitstream |
| Compress the maps | Keeps more data | No bound: 1080p needs 12x on random content, so the encode still fails | Rejected |
| `maps=0` when the next stage is an encoder with `udu_sei` | Smaller change | A filter cannot see the encoder; the encode still aborts when an operator sets `maps=1`, and other producers can exceed the limit without maps | Rejected |
| Default `maps=0` | Fixes the default graph | VMAFx loses the maps in-graph, the reason ADR-0177 rejected it; an explicit `maps=1` still aborts | Rejected |
| A header flag bit that marks a stripped blob | A stream reader could tell "stripped" from "`maps=0`" | ABI 1.5 and a VMAFx mirror change for a fact the encoder log already names | Deferred: no stream reader needs the distinction yet |

## Consequences

- **Positive**: analyze graphs into `hevc_nvenc -udu_sei 1` encode at every
  grid, including 1x1 and 2^20 cells. A foreign payload over the limit no
  longer stops the encode or loses the last picture.
- **Negative**: the 768-byte budget is conservative: it drops payloads that
  NVENC would have taken in pictures without parameter sets. The limit is
  measured on one driver; a driver that changes it needs a new measurement
  (`ffmpeg-patches/test/nvenc-udu-sei-smoke.sh`, research 0181).
- **Neutral / follow-ups**: `h264_nvenc` keeps the stock path (no limit
  found up to 64 KiB). `av1_nvenc` has no `udu_sei` option in n9.0.2 or on
  FFmpeg master, and no Pelorus patch adds one, so the type-5 metadata OBU
  write in `prepare_sei_data_array()` (type 5 is timecode in AV1) is not
  reachable; it would matter only if an option were added later. The
  tester's `sidedata_roundtrip` stage still skips the case instead of
  failing (issue #268).

## References

- Issue [#267](https://github.com/VMAFx/pelorus/issues/267) and its acceptance list (req).
- Research: [0181 HEVC NVENC header limit](../research/0181-hevc-nvenc-sei-header-budget.md).
- [ADR-0103](0103-interop-sidedata-abi.md) (R3: absent maps), [ADR-0177](0177-analyze-per-cell-maps.md).
- FFmpeg `libavcodec/nvenc.c` at `n9.0.2`: `nvenc_errors[]` (`NV_ENC_ERR_OUT_OF_MEMORY` to `AVERROR(ENOMEM)`), `process_output_surface()`, `prepare_sei_data_array()`.
- nv-codec-headers `n12.1.14.0` `nvEncodeAPI.h` lines 2188-2196 and `13.1.15.0.1` lines 2396-2404 (`NV_ENC_SEI_PAYLOAD`); NVENC Video Encoder API Programming Guide 13.1, section 8.25 (read 2026-10-09).
