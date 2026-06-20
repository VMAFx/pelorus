<!-- markdownlint-disable MD013 MD060 -->
# ADR-0126: vf_pelorus_scenecut — scene-cut → forced IDR (vendor-neutral encoder steering)

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

A scene cut that lands **mid-GOP** wastes bits. The encoder predicts the cut
frame across the previous, unrelated picture — a near-useless reference, so the
residual is large — and then, at the next periodic IDR, re-sends what it could
have keyed on at the cut. Two expensive frames where one well-placed keyframe
would do. Aligning a fresh GOP to the cut both removes the bad inter-prediction
and lets the encoder open a clean reference exactly where the content changes.

Pelorus already *measures* the cut. `vf_pelorus_mc_vulkan` (ADR-0116) runs a GPU
block-matching motion estimator and, in its `PEL_SEC_MOTION` interop section,
sets `has_scene_cut` when the mean residual SAD is high (no coherent match
anywhere — the signature of a cut, not motion). That flag rides the frame as
UUID-keyed side data (`AV_FRAME_DATA_SEI_UNREGISTERED`) and survives the
filtergraph via `av_frame_copy_props`. Nothing consumed it yet.

ADR-0114 framed encoder steering as a set of tiers feeding Pelorus's GPU
measurements into the encoder. Those tiers are all **spatial** — ROI / delta-QP
maps that redistribute bits *within* a frame. The roadmap flagged a separate,
**temporal/structural** hook as a high-leverage, near-zero-GPU-work win: the
scene-cut signal `mc` already produces, turned into a forced keyframe at the cut.
That hook was unexploited. This ADR completes it.

## Decision

Add `vf_pelorus_scenecut`: a tiny **metadata-only consumer** (not a Vulkan
filter, no shader, no GPU work, `AVFILTER_FLAG_METADATA_ONLY`). It reads
`PEL_SEC_MOTION.has_scene_cut` from the frame's Pelorus side data (via
`pel_blob_find_section`) and, on a cut frame, sets `frame->pict_type =
AV_PICTURE_TYPE_I` and `AV_FRAME_FLAG_KEY`. The downstream encoder honours a
forced `pict_type == I` as a keyframe, so it opens a fresh GOP exactly at the
cut. No pixels are touched; no side data is emitted.

The steering is **vendor-neutral by construction**: `pict_type` is the standard
libavcodec frame-level keyframe-request mechanism, honoured by every encoder we
target — x264, x265, NVENC, QSV, SVT-AV1 — with **no per-encoder patch**. Unlike
the ROI tiers (which need a fork patch per encoder because the vendors do not
honour ROI side data in vanilla, an ADR-0114 caveat), the scene-cut→IDR hook
ships as a plain `vf_` consumer that links only `libpelorus`.

A single `force_idr` AVOption (bool, default on) gates the behaviour; off is a
transparent pass-through.

**Pipeline placement**: run it **after `hwdownload`**, just before the encoder.
The motion side data is metadata and rides the frame through `hwdownload` and the
rest of the graph via `av_frame_copy_props`, so the consumer sees the same
`has_scene_cut` flag `mc` set upstream in VRAM, while operating on a plain
system-memory frame (it does no GPU work and needs no Vulkan device).

## Alternatives considered

| Option | Why not chosen |
|---|---|
| **A per-encoder forced-IDR fork patch** (the model the ROI patches use: a libavcodec edit per encoder) | Rejected — unnecessary. ROI needs per-encoder patches only because the vendors ignore ROI side data in vanilla. The `pict_type == I` keyframe-request path is **already vendor-neutral** and honoured by every target encoder with no patch, so a fork patch would add maintenance surface and rebase risk for zero benefit. |
| **Fold the consumer into `vf_pelorus_mc` (or `vf_pelorus_analyze`)** | Rejected — keep it composable and optional. `mc` is the *producer* (it runs in VRAM and emits the flag); the IDR decision belongs *after* `hwdownload`, right before the encoder, and users who want the motion side data for other consumers (vmafx telemetry, the NVENC ME-hint patch) should not be forced into a keyframe policy. A separate, default-gateable stage keeps the two concerns independent. |
| **Let the encoder's own scene-cut detector force the keyframe** (x264/x265 `scenecut`, NVENC adaptive I) | Rejected — the GPU pre-pass already measured the cut. The encoder's detector spends bitrate-domain analysis (a lookahead pass over the very pixels Pelorus already analysed) to re-derive a signal Pelorus produced for free in VRAM; reusing the measured flag is cheaper and lets the encoder's own detector stay off without losing cut-aligned GOPs. |

## Consequences

- **Positive**: a near-zero-cost temporal/structural steering hook that completes
  the ADR-0114 strategy on the structural axis; vendor-neutral with no patch
  (one `vf_` consumer, codec-agnostic across x264/x265/NVENC/QSV/SVT-AV1);
  composable and default-gateable; reuses the `has_scene_cut` flag `mc` already
  measures, so it adds no GPU work and no new ABI surface (it *consumes* the
  pre-reserved `PEL_SEC_MOTION` section, no version bump).
- **Negative / honest envelope**: the producer (`mc`'s `has_scene_cut`), this
  consumer, and the `pict_type == I` mechanism are **build-verified** — the flag
  is read and the picture type is set. But the **end-to-end BD-rate proof — a
  measured A/B of scene-cut-aligned GOPs vs periodic IDR on real multi-shot
  content — is the documented follow-up. No number is claimed in this PR.** The
  proof must be measured under the [ADR-0111](0111-benchmark-methodology.md)
  methodology against the clean ground truth (BD-rate at iso-quality over an
  RD-ladder on multi-shot footage, where the cut placement actually bites).
- **Follow-ups (documented, not in this PR)**: (1) the BD-rate A/B above;
  (2) tuning the `mc` scene-cut SAD threshold against the measured A/B if the
  cut detector over-/under-fires; (3) optionally surfacing a min-GOP guard so a
  burst of cuts cannot force IDRs closer than the encoder's keyint-min.

## References

- `ffmpeg-patches/files/vf_pelorus_scenecut.c` (the consumer; patch 0016 on this
  branch); [docs/metrics/scenecut.md](../metrics/scenecut.md).
- [ADR-0114](0114-encoder-steering.md) (the encoder-steering tiers this completes
  on the temporal/structural axis), [ADR-0116](0116-pelorus-mc.md) (the producer
  that emits `PEL_SEC_MOTION.has_scene_cut`), [ADR-0113](0113-optical-flow-mc.md)
  (the motion-estimation strategy), [ADR-0103](0103-interop-sidedata-abi.md) (the
  append-only side-data ABI it consumes), [ADR-0100](0100-doc-substance-rule.md)
  (the per-surface doc bar), [ADR-0111](0111-benchmark-methodology.md) (the
  clean-referenced proof methodology the follow-up must use).
- Source: `req` — extend the ADR-0114 encoder-steering strategy with the
  temporal/structural hook (scene-cut → forced IDR) flagged as a high-leverage,
  near-zero-GPU-work win: a vendor-neutral metadata consumer that turns the
  `has_scene_cut` flag `vf_pelorus_mc` already emits into a forced keyframe at the
  cut.
