<!-- markdownlint-disable MD013 -->
# libaom-av1 ROI steering (`-pelorus_roi`)

The `libaom-av1` encoder gains a `pelorus_roi` AVOption (boolean, default
`false`) that makes it honour `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data —
the same standard ROI channel the `vf_pelorus_analyze roi=1` filter emits
(ADR-0109) — by driving libaom's segment-based `AOME_SET_ROI_MAP`. This is the
software-AV1 leg of the Tier-1 encoder steering in ADR-0114, alongside the NVENC
`qpDeltaMap` and QSV `mfxExtMBQP` consumers.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i input.mkv \
  -vf "format=yuv420p,hwupload,pelorus_analyze_vulkan=roi=1,hwdownload,format=yuv420p" \
  -c:v libaom-av1 -pelorus_roi 1 out.mkv
```

`pelorus_roi` is orthogonal to every other libaom option; with it off (the
default) the encoder behaves exactly as upstream.

## How the mapping works

libaom's only region-of-interest hook is `AOME_SET_ROI_MAP`, which takes an
`aom_roi_map_t`. This is a **segment** model, not a dense per-block delta-QP map:

- One `uint8` **segment id** (0–7) per **4×4-luma "mode-info" cell**.
- A `delta_q[8]` table giving each segment a **qindex bias**. AV1 codes
  quantisation on the 0–255 qindex scale; a segment's delta is applied as
  `Final_q_idx = clip(0, 255, base_q_idx + delta)`.

The bridge, per frame:

1. Scales each `AVRegionOfInterest.qoffset` (an `AVRational` in [-1, +1];
   negative asks for more bits / lower q) by the 0–255 qindex span.
2. Clusters the distinct region deltas into **at most 8 segments** (segment 0 is
   the implicit background with delta 0).
3. Paints the 4×4-cell grid by nearest segment; the **first** region in the list
   wins on overlap (matching `vf_addroi` / the `libx264` ROI path).
4. Pushes the map via `aom_codec_control(AOME_SET_ROI_MAP)` before the encode.

### Granularity note

This differs from the NVENC/QSV dense maps in two ways:

- **QP resolution is coarse**: at most 8 distinct qindex levels per frame
  (`AOM_MAX_SEGMENTS = 8`). Many distinct region offsets are quantised into 8
  buckets; each cell snaps to its nearest bucket.
- **Spatial grid is fine**: 4×4 luma cells, finer than NVENC's per-CTB (32×32)
  or per-SB (64×64) grid.

A frame with no ROI side data — or whose regions all resolve to delta 0 — pushes
nothing, so the encoder sees the base qindex everywhere.

## Status: built and verified, gain deferred to upstream

The patch is verified on hardware to build, run, and degrade safely:

- Built into a real ffmpeg n8.1.1 (`--enable-libaom`), ran the analyze→libaom
  pipeline on a banding clip: **no crash**, valid 48-frame output, the bridge
  builds a sane 120×68 grid and quantises 32 regions into 6 segments.

However, **stock libaom 3.14.1 does not yet apply the map in FFmpeg's encoder
configuration**: `AOME_SET_ROI_MAP` returns `AOM_CODEC_INVALID_PARAM` and the
segment map is ignored (AOMedia's `ctrl_set_roi_map` is wired only for the RTC
delta-q path). Measured CAMBI is therefore identical with the option on or off
(0.93193 both on the test clip). The bridge logs one warning and continues with
no ROI bias — an **upstream limitation, not a patch defect**.

When a libaom release enables `AOME_SET_ROI_MAP` on the path FFmpeg uses, this
option becomes effective with no code change. See
[ADR-0120](../adr/0120-libaom-steering.md) for the full investigation.

## Diagnostics

At `-loglevel verbose` the encoder logs the grid setup, the segment count, and
any control rejection:

```text
Pelorus ROI: AOME_SET_ROI_MAP enabled, 120x68 4x4-cell grid, <= 8 segments/frame.
Pelorus ROI: 32 regions quantised into 6 AV1 segments (AOM_MAX_SEGMENTS=8); QP resolution is coarse.
Pelorus ROI: AOME_SET_ROI_MAP failed (res=8); continuing without ROI bias.
```

The last line is the current upstream-limitation signature on libaom 3.14.1.
