<!-- markdownlint-disable MD013 MD060 -->
# ADR-0120: libaom-av1 ROI steering via AOME_SET_ROI_MAP (SW-AV1 leg of Tier 1)

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: encoder, roi, qp-map, libaom, av1, software, adr-0114

## Context

ADR-0114 adopted a tiered encoder-steering strategy: one shared producer
(`vf_pelorus_analyze roi=1`, ADR-0109) emits `AV_FRAME_DATA_REGIONS_OF_INTEREST`,
and progressively less-portable encoder hooks consume it. The merged work covers
NVENC `qpDeltaMap` and QSV `mfxExtMBQP` (Tier 1 dense maps) and the cross-vendor
Vulkan-Video quantization map (Tier 2). **libaom-av1**, a software AV1 encoder
with zero Pelorus steering, was an open Tier-1 leg.

A survey of the FFmpeg `libavcodec/libaomenc.c` against the installed aom 3.14.1
headers found:

- Stock `libaomenc.c` has **no** region-of-interest handling whatsoever.
- libaom exposes exactly one ROI hook: `AOME_SET_ROI_MAP` taking an
  `aom_roi_map_t`. Unlike the NVENC/QSV dense per-block int8 maps, this is a
  **segment** model: one `uint8` segment id (0..7) per 4x4-luma mode-info cell,
  plus a `delta_q[8]` table giving each segment a qindex bias. AV1 codes
  quantisation on the 0..255 qindex scale (`Final_q_idx = clip(0, 255,
  base_q_idx + delta)`), so a frame supports at most `AOM_MAX_SEGMENTS` (8)
  *distinct* QP levels — coarse in value, but the 4x4 grid is far finer in space
  than NVENC's per-CTB/per-SB grid.
- A `film_grain_table` hook exists (`AV1E_SET_FILM_GRAIN_TABLE`, a file path),
  but it consumes a serialised grain-table file, not the in-memory side-data
  channel the AV1 software path already round-trips via
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS`. Grain is therefore out of scope here.

The vmafx fork's qpfile→ROI bridge in its own `libaomenc.c` (its ADR-0312) was
prior art for the segment-quantisation-and-paint mechanics.

## Decision

Add a `pelorus_roi` AVOption (`AV_OPT_TYPE_BOOL`, default off) to `libaom-av1`,
mirroring the NVENC/QSV ROI patches, that consumes each frame's
`AV_FRAME_DATA_REGIONS_OF_INTEREST` and drives `AOME_SET_ROI_MAP`:

1. Allocate the segment map once at the encoder mode-info grid (frame size
   aligned to 8 luma px, divided by the 4-px cell).
2. Per frame: scale each `AVRegionOfInterest.qoffset` (AVRational in [-1,+1]) by
   the 0..255 qindex span; cluster the distinct deltas into ≤ 8 segments
   (segment 0 = background, delta 0); paint the cell grid by nearest segment
   (first region wins on overlap, matching `libx264.c` `setup_roi`); push the map
   via `aom_codec_control(AOME_SET_ROI_MAP)` before `aom_codec_encode`.
3. Degrade gracefully: a frame with no ROI side data, or whose regions all
   resolve to delta 0, pushes nothing; a control failure warns once and encodes
   with no bias. Zero behaviour change when the option is off.

Shipped as patch `0012-libaom-pelorus-roi.patch` (hand-maintained unified diff in
`ffmpeg-patches/files/`, applied as its own commit by `generate.sh`, landing last
so no shipped artifact renumbers).

## Verified-vs-deferred (the honest result)

Built the full Pelorus stack + this patch into a real ffmpeg n8.1.1
(`--enable-gpl --enable-libaom --enable-vulkan --enable-libshaderc`), and ran
`vf_pelorus_analyze roi=1 → libaom-av1 -pelorus_roi 1` on a banding clip.

- **No crash; valid output produced.** The bridge runs per frame, builds a sane
  120×68 4x4-cell grid, quantises 32 regions into 6 AV1 segments, and the
  encoder completes all 48 frames.
- **No quality effect — honest negative.** CAMBI is bit-for-bit identical
  baseline vs +ROI (0.93193 both). Root cause, caught on hardware: in FFmpeg's
  encoder configuration `AOME_SET_ROI_MAP` returns `AOM_CODEC_INVALID_PARAM`
  (`res=8`) and the segment map is ignored. AOMedia's `av1_cx_iface`
  `ctrl_set_roi_map` is wired only for the RTC delta-q path (and is a
  "re-implement and test for AV1" stub elsewhere); a standalone probe confirms
  the control only bites under a narrow `g_usage=REALTIME` + `rc_end_usage=AOM_Q`
  state that FFmpeg's wrapper does not reproduce. This is an **upstream
  limitation, not a patch defect**: the graceful-degrade path is exactly what
  fires, and the bridge becomes effective for free once a libaom release enables
  the control on the path FFmpeg uses.

The patch is therefore landed as the correctly-wired, lockstep SW-AV1 ROI leg of
Tier 1 — verified to build, run, and degrade safely — with the quality gain
deferred to upstream libaom.

## Alternatives considered

- **`aom_active_map` (AOME_SET_ACTIVEMAP)** — a 16×16 on/off skip map, not a
  delta-QP map; it can drop blocks but cannot bias quality up on banding tiles,
  so it does not serve the analyze producer's intent. Rejected.
- **Coalescing ROI into a serialised `film_grain_table`/qpfile and using a file
  path control** — re-introduces a temp-file side channel and an offline step;
  contradicts the zero-copy in-memory side-data contract. Rejected.
- **Forcing `g_usage=REALTIME` + `AOM_Q` from the patch to unlock the control** —
  would silently change the user's encode mode (latency, quality, RC) as a
  side effect of an ROI flag; unacceptable. The option must be orthogonal.
  Rejected; documented as the upstream precondition instead.

## Consequences

- A second software-AV1 ROI consumer exists in lockstep with NVENC/QSV, so the
  shared analyze producer needs no change when upstream lands the feature.
- The granularity tradeoff (≤ 8 qindex levels/frame, 4x4 grid) is documented for
  users in `docs/backends/libaom-roi.md`.
- A libaom version bump that enables `AOME_SET_ROI_MAP` on the non-RTC path turns
  this from a no-op into a measurable gain with no code change; re-run the A/B to
  capture the number then.

## References

- req: "Add Pelorus encoder steering to libaom-av1 … consume
  AV_FRAME_DATA_REGIONS_OF_INTEREST → libaom's ROI/delta-q. Survey what libaom
  3.14.1 actually exposes. Mirror the NVENC/QSV patch model. an honest negative
  is acceptable, a fabricated/unrun result is NOT."
- ADR-0114 (encoder-steering strategy, Tier 1), ADR-0109 (vf_pelorus_analyze).
- libaom 3.14.1 `aom/aomcx.h` (`AOME_SET_ROI_MAP`, `aom_roi_map_t`,
  `AOM_MAX_SEGMENTS`); AOMedia `av1/av1_cx_iface.c` `ctrl_set_roi_map`.
