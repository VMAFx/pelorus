<!-- markdownlint-disable MD013 MD060 -->
# ADR-0114: Encoder-steering strategy — feed Pelorus GPU maps to every encoder we can

- **Status**: Proposed
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: encoder, roi, qp-map, nvenc, qsv, amf, vulkan-video, roadmap, strategy

## Context

Pelorus measures things on the GPU that an encoder's cheap intra-frame variance
AQ cannot see — contour/banding salience (`vf_pelorus_analyze`, ADR-0109),
denoise residual (ADR-0112), and (roadmap) motion (ADR-0113) and film grain. The
question: which encoder hooks can consume those maps to improve BD-rate, across
NVIDIA / AMD / Intel / Vulkan / software? A full survey of the installed APIs
(NVENC SDK 13, oneVPL, Vulkan-Video, libx265/SVT-AV1/libaom/libx264; AMF headers
absent, `CONFIG_AMF=0` in this build) was run. This box has all three vendors'
HW encoders **and** ffmpeg's native `h264_vulkan`/`hevc_vulkan`/`av1_vulkan`.

## Decision

Adopt a **tiered** steering strategy, one shared producer (`vf_pelorus_analyze`
delta-QP / banding map) feeding progressively more powerful, less portable hooks.

- **Tier 0 — works today, no patch, vendor-neutral**: emit
  `AV_FRAME_DATA_REGIONS_OF_INTEREST` (a `qoffset` map coalesced from the banding
  map). Verified consumers in *vanilla* ffmpeg: `libx264`, `libx265`, `libvpx`,
  `qsvenc`, `vaapi_encode`, `d3d12va`. Precondition: keep `-aq-mode != none`
  (x264 silently drops ROI otherwise). This is the cheapest win and the baseline
  A/B harness target. **MEASURED (concept, manual ROI): −36% banding (CAMBI
  0.436→0.280) at iso-bitrate vs `x265 aq-mode 2`, VMAF unchanged** — it beats the
  encoder's *own* AQ because variance-AQ starves the flat gradients that band
  (see bench-results.md v0.4). Production needs `vf_pelorus_analyze` to
  auto-detect banding-prone tiles and emit the ROI map.
- **Tier 1 — our ffmpeg patches, dense per-block delta-QP** (the BD-rate
  step-up): one analyze→`int8` delta-QP raster drives **NVENC** `qpDeltaMap` +
  `qpMapMode=DELTA` (revive the *dead* `NVENC_HAVE_QP_MAP_MODE` guard;
  `nvenc.c` has zero refs today) and **QSV** `mfxExtMBQP` (clean injection via the
  existing `set_encode_ctrl_cb`, no frame-loop fork). Round out software with the
  **x265 qpfile→ROI** bridge (x265 lacks the qpfile path the fork's other SW
  encoders have). NVENC and QSV are tied for highest value. **NVENC DONE +
  MEASURED** (`ffmpeg-patches/files/nvenc-pelorus-roi.patch`, `-pelorus_roi 1`):
  it consumes the standard ROI side data (so the proven `analyze` producer drives
  it — no separate producer needed) → **−41% banding, +0.10 VMAF, +3% bitrate** at
  constant-QP (bench-results.md v0.5). Caveat: NVENC's own AQ overrides the map,
  so use constant-QP / AQ-off; VBR redistribution costs VMAF.
- **Tier 2 — cross-vendor strategic, "via Vulkan"**: patch `vulkan_encode.c` for
  `VK_KHR_video_encode_quantization_map` (delta map for CQP, emphasis map
  `R8_UNORM` for cbr/vbr — the latter maps almost directly onto analyze's 0..1
  `global_banding_risk`). One Pelorus compute pass turns the cell grid into the
  map image, bound zero-copy at `vkCmdEncodeVideoKHR`. One producer → all three
  GPU vendors, no host roundtrip. Gated on driver maturity (see caveats).
- **Tier 3 — specialist, producer-gated**: NVENC external ME hints
  (`NVENC_EXTERNAL_ME_HINT`, speed-first — see ADR-0113) once `vf_pelorus_mc`
  exists; AV1 film-grain (NVENC `NV_ENC_FILM_GRAIN_PARAMS_AV1` patch / SVT-AV1
  `fgs_table` / x265 H.274 FGC) once `vf_pelorus_grain` exists.

## Alternatives considered (per access tier)

| Hook | NVENC | AMF | QSV | Vulkan-Video | x264/x265 | SVT/aom |
|---|---|---|---|---|---|---|
| ROI delta-QP (side data) | patch | patch | **today** | patch | **today** | fork-qpfile |
| Dense per-block delta-QP | patch | none(\*) | patch | patch | via-ROI | fork(8-seg) |
| External ME hints | patch(speed) | none | none | none | analysis-reuse(fragile) | none |
| AV1 film-grain | patch | none | decode-only | separate(OBU) | x265 FGC today | SVT today |
| Built-in AQ / lookahead | today | today | today | rc-only | today | today |

(\*) AMF has only a coarse 0–10 importance map (no true delta-QP input); disabled
in this build. The encoders' **own** AQ/lookahead is "today" everywhere — Pelorus
must *beat* it, not merely turn it on.

## Consequences

- **Positive**: a single GPU-measured map producer can steer bit allocation on
  every backend; Tier 0 ships immediately on the vanilla six; Tier 2 is the
  zero-copy cross-vendor endgame. Pelorus becomes an encoder *front-end*, not
  just a filter.
- **CRITICAL honest caveats** (these gate every claim):
  1. **Unverified magnitudes.** No in-tree BD-rate run exists for any map yet.
     Every number in the survey is a directional prior. Measure per ADR-0111
     before claiming anything.
  2. **Bit-redistribution, not free quality.** ROI/QP steering moves bits
     between regions; PSNR/SSIM BD-rate can go **negative** if bits go to flat
     regions without a compensating positive offset on busy ones. The honest win
     is **perceptual** (CAMBI / VMAF-NEG on banding-heavy content), ~0 on
     clean/busy footage.
  3. **Beat-the-baseline.** The real delta is *Pelorus's measured banding map vs
     the encoder's built-in variance AQ/lookahead*. A/B against the encoder's own
     AQ, never against AQ-off.
  4. **Vendor-lock (verified).** NVENC and AMF do **not** honor ROI side-data in
     vanilla (patch required even for the "standard" channel). QSV/AMF have **no**
     external ME-hint API (redirect motion into the QP map there). AV1 film-grain
     side-data is **decode-output-only** — no encoder reads it as input.
  5. **Vulkan-Video immaturity.** The encoders exist in n8.1 but `vulkan_encode.c`
     exposes rate-control only (zero quant-map refs); the extension is new
     (Nov 2024) with uneven beta Linux driver coverage. Strategic, not near-term.

## Build order

0. Finish `vf_pelorus_analyze` per-cell banding/variance map (the one producer).
1. Tier 0 ROI emitter (`AV_FRAME_DATA_REGIONS_OF_INTEREST`) → measure on libx265.
2. Validate the same emitter on QSV/VAAPI (capability probe + graceful degrade).
3. Tier 1 dense delta-QP: QSV `mfxExtMBQP` + NVENC `qpDeltaMap` (one producer) + x265 qpfile bridge; A/B each vs its own AQ.
4. Tier 2 Vulkan-Video quant-map (patch `vulkan_encode.c` + a `pelorus_qpmap.comp`).
5. Tier 3 ME hints (after `vf_pelorus_mc`) and FGS (after `vf_pelorus_grain`).
6. Closed loop: read QSV `mfxExtEncodeStats` / AMF block-QP feedback back into the interop ABI to verify the map was honored and improve it.

## References

- Survey: NVENC `nvEncodeAPI.h` (`qpDeltaMap`/`qpMapMode`, dead `NVENC_HAVE_QP_MAP_MODE` guard, `meExternalHints`, `NV_ENC_FILM_GRAIN_PARAMS_AV1`); `vpl/mfx.h` (`mfxExtMBQP`, `mfxExtEncoderROI`); `vk_video/*_encode.h` + `VkVideoEncodeQuantizationMapInfoKHR`; `libavcodec/{nvenc,qsvenc,libx265,libx264,vulkan_encode}.c`; `libavutil/frame.h` (`AV_FRAME_DATA_REGIONS_OF_INTEREST`).
- [ADR-0109](0109-analyze-filter.md) (the map producer), [ADR-0111](0111-benchmark-methodology.md) (how to measure a claim), [ADR-0112](0112-temporal-denoise.md), [ADR-0113](0113-optical-flow-mc.md).
- Source: `req` — "check all vendor APIs for everything we can abuse to make this a good encoder" and "we should have all software installed" and "via vulkan" (survey every vendor encoder API for hooks Pelorus's GPU maps can feed; the cross-vendor Vulkan quant-map is the strategic target).
