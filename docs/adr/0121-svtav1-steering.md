<!-- markdownlint-disable MD013 MD060 -->
# ADR-0121: SVT-AV1 ROI steering — honor AV_FRAME_DATA_REGIONS_OF_INTEREST via the SVT-AV1 segment map

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: svtav1, av1, roi, qp-map, ffmpeg, encoder-steering, software

## Context

[ADR-0114](0114-encoder-steering.md) defined a tiered encoder-steering strategy:
one producer (`vf_pelorus_analyze roi=1`, ADR-0109) emits a standard
`AV_FRAME_DATA_REGIONS_OF_INTEREST` `qoffset` map, and per-encoder consumers feed
it into each encoder's quantizer-steering hook. NVENC (`qpDeltaMap`, ADR-0114
Tier 1), QSV (`mfxExtMBQP`) and the native Vulkan-Video encoders
(`VK_KHR_video_encode_quantization_map`, Tier 2) all landed.

The flagship modern AV1 software encoder — SVT-AV1, `libsvtav1` / `av1_svt` — got
**zero** Pelorus steering. Stock `libsvtav1.c` (FFmpeg n8.1.1) consumes no ROI
side data at all: it reads frame pixels, Dolby Vision RPU side data, and HDR
metadata, and nothing else touches per-region quantization. This is the primary
remaining gap in the ROI-steering family, and SVT-AV1 is where AV1 quality work
actually happens.

SVT-AV1 4.1.0 (the installed `libSvtAv1Enc.so.4`) **does** expose a per-region
quantization hook, but it differs in shape from the per-block delta-QP maps the
HW encoders use:

- `EbSvtAv1EncConfiguration::enable_roi_map` (a `bool`, default off) switches on
  the ROI path at `svt_av1_enc_set_parameter` time.
- Per frame, a `SvtAv1RoiMapEvt` is attached through an `EbPrivDataNode` of
  `node_type == ROI_MAP_EVENT` on `EbBufferHeaderType::p_app_private`.
- `SvtAv1RoiMapEvt` carries a **dense per-64×64-superblock segment map**
  (`b64_seg_map`, raster order, stride = `ceil(width/64)`) plus up to 8
  (`MAX_SEGMENTS`) segment qindex deltas (`seg_qp[8]`) and `max_seg_id`.
- `seg_qp[i]` is a qindex **delta** added to the frame base qindex (verified in
  SVT-AV1 `Source/Lib/Encoder/Codec/EbSegmentation.c`
  `roi_map_setup_segmentation` → `feature_data[SEG_LVL_ALT_Q]`); negative ⇒ lower
  qindex ⇒ more bits, which matches `AVRegionOfInterest.qoffset`'s negative =
  better-quality convention. The App parser bounds `seg_qp` to `[-255, 255]`; the
  library clamps the effective `base + delta` qindex to a valid non-zero range.

The ROI-map ABI was introduced in SVT-AV1 1.6.0, so the consumer must be
version-gated like the QSV (`QSV_HAVE_MBQP`) and Vulkan (extension-probe) ones.

The FFmpeg-tree `libsvtav1.c` the *vmafx* fork ships also gained an unrelated ROI
bridge — but keyed off an offline `-qpfile` (a vmaf-tune saliency file), not the
in-band side data the Pelorus producer emits. That is a different channel
(ADR-0312 in the vmafx tree) and is not part of the Pelorus n8.1.1 patch base.

## Decision

Add a `pelorus_roi` AVOption (default off, registered on `libsvtav1`) that
consumes the **same** `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data the merged
`vf_pelorus_analyze` producer already emits, mapping it onto SVT-AV1's
per-superblock ROI segment map. Structurally it mirrors the NVENC/QSV ROI patches
(a hand-maintained unified diff in `ffmpeg-patches/files/svtav1-pelorus-roi.patch`
applied as its own commit by `generate.sh`, landing as patch `0012`), adapted to
SVT-AV1's segment-map ABI:

- **Init** (`eb_enc_init`): when `pelorus_roi` is set and the ROI ABI is present,
  compute the superblock grid (`ceil(w/64) × ceil(h/64)`) and set
  `enable_roi_map = true` **before** `svt_av1_enc_set_parameter`.
- **Per frame** (`eb_send_frame`): rasterize the ROI rectangles onto the SB grid
  (regions walked in reverse so the first region wins on overlap, matching the
  NVENC/QSV/libx264 convention), map each region's `qoffset ∈ [-1, 1]` onto a
  qindex delta over the AV1 255-step span, and **quantise the distinct deltas into
  ≤ 8 segments**. Segment 0 is reserved for the zero delta (background), so an SB
  no region covers keeps the encoder's default rate-control decision. When the
  8-segment budget is exhausted, a new delta snaps to the nearest existing
  segment so the region still gets biased. Attach the resulting `SvtAv1RoiMapEvt`
  via a stack-local `EbPrivDataNode`.
- **Lifetime** (the crash-risk seam): `svt_av1_enc_send_picture` shallow-copies
  the `EbPrivDataNode`, but `resource_coordination_process.c`'s
  `update_frame_event` stores the bare `SvtAv1RoiMapEvt*` into
  `enc_ctx->roi_map_evt` and dereferences it **later on async pipeline threads**.
  With SVT-AV1's lookahead, multiple frames are in flight, so a single reused
  buffer would race. Each ROI-bearing frame therefore **owns** a freshly-allocated
  event + segment map, held on a context singly-linked list and freed in
  `eb_enc_close()`.
- **Graceful degrade**: the whole path is compile-gated by
  `SVT_AV1_CHECK_VERSION(1, 6, 0)`. Built against an older SVT-AV1, the option
  emits a one-shot init warning and clears itself (pass-through). Zero behaviour
  change when off (the default).

`enable_roi_map` is rate-control-mode-agnostic in SVT-AV1 (no CQP gate, unlike
QSV), but as with the HW encoders the encoder's own variance AQ can override the
segment map, so constant-quality (`-crf` / `-qp`) is the recommended mode.

## Alternatives considered

| Option | Verdict |
|---|---|
| **Per-superblock segment map via `SvtAv1RoiMapEvt` (chosen)** | The only in-band per-region quantization hook SVT-AV1 exposes; consumes the existing standard side data; no new producer; version-gated and degrades cleanly. |
| Drive `svtav1-params` strings per frame | SVT-AV1's `svt_av1_enc_parse_parameter` is an init-time key=value parser, not a per-frame quantizer hook — no per-region path exists there. |
| Offline `-qpfile` bridge (the vmafx-fork channel) | Different channel (a saliency file, not in-band side data); needs a separate producer and a full second pass; does not satisfy "consume what `analyze roi=1` emits". |
| Map onto a per-block delta map like NVENC/QSV | SVT-AV1 has no per-block delta-QP map ABI; the segment map (≤ 8 deltas) is the native granularity. Quantising to 8 segments is the necessary adaptation, not a limitation we chose. |
| Reuse one event buffer per encoder | Races the async lookahead (the library keeps the bare pointer) — the per-frame-owned list is required for correctness. |

## Consequences

- SVT-AV1 now honors the same ROI side data as every other Pelorus-steered
  encoder, from one producer, with no new filter.
- Spatial fidelity is rounded to 64×64-superblock granularity and at most 8
  distinct qindex deltas per frame — coarser than the per-16×16/32×32 HW maps, but
  it is SVT-AV1's native ROI resolution. Object-sized banding ROIs (the
  `vf_pelorus_analyze` target) are well within that.
- Memory grows with the number of ROI-bearing frames (one small event + a
  `ceil(w/64)·ceil(h/64)`-byte segment map each; ~510 bytes/frame at 1080p),
  freed at close. Bounded and documented; acceptable for the clip lengths this
  targets.
- Patch-stack invariant: `0012` touches only `libavcodec/libsvtav1.c` and depends
  on `0002` (the analyze producer) for the side data; see
  [docs/rebase-notes.md](../rebase-notes.md).

## Verification

Built a real FFmpeg n8.1.1 + the full Pelorus stack incl. patch `0012`
(`--enable-gpl --enable-libsvtav1 --enable-vulkan --enable-libshaderc`), linked
against `libpelorus` 0.1.0 and SvtAv1Enc 4.1.0. `-h encoder=libsvtav1` shows
`-pelorus_roi`.

**On-hardware A/B (not syntax-only).** Source: a 960×270, 96-frame composite —
left half a banding-prone 10-bit gradient, right half `testsrc2` detail — so the
ROI is *localized* (analyze emits 19 regions per frame, concentrated on the
gradient half). Pipeline:
`pelorus_analyze_vulkan=roi=1:roi_strength=0.5 → libsvtav1` at matched CRF, on an
RTX 4090 Vulkan device.

- **No crash.** The encode runs to completion and produces valid output; the
  `Pelorus ROI map enabled: NxM superblocks` init log fires and the bitstream
  changes only when `-pelorus_roi 1` is set. (The previous two Pelorus filters
  shipped syntax-only and both crashed on first run; this one was executed.)
- **The ROI is honored**: at matched CRF the +ROI encode spends more bits
  (CRF 35: 202 KB → 213 KB, +5.6 %; CRF 45: 74 KB → 80 KB, +8.0 %), exactly the
  expected effect of a negative-`seg_qp` bias on the banding region.
- **CAMBI A/B** (no-reference banding, vmaf @ `/home/kilian/dev/vmaf`, lower is
  better), pooled mean over the clip:

  | CRF | base CAMBI | +ROI CAMBI | Δ banding | Δ bitrate |
  |---|---:|---:|---:|---:|
  | 35 | 3.9778 | 3.9196 | **−1.5 %** | +5.6 % |
  | 45 | 4.2876 | 4.2656 | **−0.5 %** | +8.0 % |

  The gain is real but **modest** — honestly far short of the NVENC −41 %
  (ADR-0114 v0.5): the synthetic gradient is already mild banding (CAMBI ≈ 4),
  SVT-AV1's 8-segment granularity is coarser than the HW per-block maps, and on a
  *whole-frame* uniform gradient base and +ROI come out byte-identical (a uniform
  delta over one segment is a global qindex shift). The mechanism is proven
  correct and honored; a representative BD-rate run on a harder, real
  banding-prone clip is a follow-up (no inflated number is claimed). See
  [docs/development/bench-results.md](../development/bench-results.md) v0.10 for
  the full table and commands.

## Film grain (deferred — documented why)

The task also asked to wire the grain estimate (`PEL_SEC_FILMGRAIN` /
`AV_FRAME_DATA_FILM_GRAIN_PARAMS` from `vf_pelorus_grain_estimate_vulkan`) into
SVT-AV1's film-grain synthesis. SVT-AV1 4.1.0 exposes
`EbSvtAv1EncConfiguration::fgs_table` (an `AomFilmGrain*`) and `film_grain_*`
config, but: (1) `fgs_table` is an **init-time, whole-stream** pointer set before
`svt_av1_enc_set_parameter`, with no per-frame film-grain priv-data node
(`FILM_GRAIN_PARAM` is present in the `PrivDataType` enum but **commented out** in
the public header — `//FILM_GRAIN_PARAM`), so the per-frame estimate the Pelorus
estimator emits has no in-band channel into SVT-AV1; (2) the FFmpeg `libsvtav1.c`
wrapper never sets `fgs_table` and FFmpeg's AV1 film-grain side data does **not**
round-trip through it. Wiring a single whole-stream `AomFilmGrain` would require
reconciling the `AomFilmGrain` ↔ `AVFilmGrainAOMParams` layout and choosing one
frame's estimate for the whole clip — a different design from the per-frame ROI
path here, and lower value than the ROI gap this PR closes (the AV1 *software*
film-grain leg already round-trips via native side data for `libaom`-style paths
per ADR-0118's context note). Deferred to a dedicated follow-up; this ADR ships
the ROI hook only.

## References

- req: "Add Pelorus encoder steering to SVT-AV1 (libsvtav1 / av1_svt) — currently
  the flagship modern AV1 encoder gets ZERO Pelorus steering. Dense per-block QP /
  ROI: consume AV_FRAME_DATA_REGIONS_OF_INTEREST → SVT-AV1's per-SB qindex/delta-q
  map. Film grain: consume the grain estimate → SVT-AV1's film-grain synthesis;
  wire it if tractable, if not document why."
- [ADR-0114](0114-encoder-steering.md) — encoder-steering strategy (the producer
  + the NVENC/QSV/Vulkan consumers this mirrors).
- [ADR-0109](0109-deband-control-plane.md) / `vf_pelorus_analyze` — the ROI
  producer.
- SVT-AV1 4.1.0 `Source/Lib/Encoder/Codec/EbSegmentation.c`
  (`roi_map_setup_segmentation`, `roi_map_apply_segmentation_based_quantization`)
  and `Source/Lib/Encoder/Codec/EbResourceCoordinationProcess.c`
  (`update_frame_event`) — the seg_qp delta semantics + the event-lifetime
  contract.
