<!-- markdownlint-disable MD013 MD060 -->
# ADR-0116: vf_pelorus_mc_vulkan v1 — a standalone GPU motion-vector-hint producer (encode-speed, not quality)

- **Status**: Proposed
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: motion, vulkan, interop, nvenc, encoder, roadmap

## Context

[ADR-0113](0113-optical-flow-mc.md) decided to build a GPU block-matching motion
estimator and framed its **v1** as *denoise-internal* — the MV field would feed
`vf_pelorus_denoise_vulkan`'s temporal warp. That ADR also recorded two findings
that shape this one: (1) two in-filter motion-compensated denoise attempts were
built and **measured to fail** (winner-take-all block matching gave −28% vs the
no-MC −34%; NLM-temporal over-blurred to VMAF 73–83) because a similarity metric
over raw, noisy pixels is swamped — that path is **reverted, not to be rebuilt**;
and (2) stock FFmpeg cannot inject MVs into any encoder, but the Pelorus patch
stack can reach NVENC's `NV_ENC_EXTERNAL_ME_HINT` API, which
[ADR-0114](0114-encoder-steering.md) Tier 3 confirms is the **only** vendor
external-ME-hint hook (AMF/QSV/Vulkan-Video expose none).

Building the denoise warp consumer first couples a large, unproven quality lever
to the estimator and re-opens the noise-limited failure mode (block-matching on
raw pixels matches grain as readily as motion). The independently shippable,
honestly-framed unit is the **producer**: a GPU motion estimator that emits a
per-block integer-pel MV field as interop side data. Its value is **encode
speed** — a fixed-function encoder ASIC can seed its motion search from our hints
and skip or shorten its own — not quality.

## Decision

We will ship `vf_pelorus_mc_vulkan` v1 as a **standalone MV-hint producer**: a GPU
block-matching estimator (one workgroup per block; cooperative SAD reduced in
shared memory; a predictor-seeded small-diamond descent transcribed from FFmpeg's
`libavfilter/motion_estimation.c`, `ff_me_search_epzs`/`_ds`) that fills the
already-reserved `PEL_SEC_MOTION` section (`interop.h`, bit `1u<<4`,
`PelorusMotionSection`, 32 bytes, ABI 1.0 — **no ABI bump**) with the frame
scalars and appends the dense `int16 (dx,dy)` per-block MV grid after the packed
section (the `vf_pelorus_analyze` map-payload pattern). The frame passes through
unchanged. The predictors are the zero MV, the previous frame's global-motion MV,
and the collocated previous-frame block MV (a persistent MV SSBO ping-ponged
frame to frame) — every seed is resolved **before** the dispatch, so there is no
cross-workgroup intra-frame neighbour race; intra-frame spatial predictors are
deliberately omitted for that reason. Block size and search range are AVOptions
(`bsize`, `search`) with device-agnostic defaults (16, 24) so the filter is a
product for any Vulkan GPU, not tuned to one box.

The NVENC ME-hint **consumer** (a `libavcodec/nvenc.c` patch reading
`PEL_SEC_MOTION` into `meExternalHints`) and the denoise warp consumer are both
**deferred follow-ups**, each gated on a measured win (ADR-0114 Tier 3 / ADR-0113).
The honest v1 claim is "the MV field is produced and plausible" (direction
verified on synthetic pans); the measured speedup belongs to the consumer PR.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Standalone MV-hint producer (this) | Independently shippable; honest scope (speed); fills the reserved interop plane; unblocks NVENC + denoise consumers behind one contract | No measured end-to-end win until a consumer lands | **Chosen** — smallest correct unit |
| Denoise-internal MC first (ADR-0113 v1) | One fewer side-data round-trip | Couples the estimator to an unproven quality lever; re-opens the noise-limited failure mode (block-match on raw pixels = grain) | Deferred to a consumer PR |
| Intra-frame spatial (left/top) EPZS predictors | Closer to the serial CPU EPZS; better MVs on coherent motion | Reading a neighbour block's result mid-dispatch is a cross-workgroup data race | Rejected — use temporal + global predictors only |
| `VK_NV_optical_flow` HW engine | Dense HW flow, fast | NVIDIA-only; zero in-tree usage; orchestration written blind | Later optional fast-path behind the same `PEL_SEC_MOTION` contract |
| CPU `vf_mestimate` side-data | No new GPU code | Breaks VRAM-resident zero-copy; CPU ME throughput | Rejected (ADR-0113) |

## Consequences

- **Positive**: a self-contained, honestly-scoped filter that fills the reserved
  motion interop plane for vmafx and unblocks both the NVENC ME-hint consumer and
  a future denoise warp consumer behind one stable side-data contract; no ABI
  bump (the section was reserved at ABI 1.0); zero-copy, codec-agnostic producer.
- **Negative / honest scope**: no measured BD-rate or speed win ships with v1 —
  that is the consumer PR's job; block-MV magnitude on partially-flat content
  under-reads the true global motion (the mean is diluted by aperture-ambiguous
  blocks), so consumers should weight by per-block SAD / use `motion_magnitude_p95`
  rather than the raw mean.
- **Risks / honesty**: block-matching on raw pixels matches grain as readily as
  motion (ADR-0113) — acceptable for an encoder **search seed**, which is why this
  does **not** feed the denoiser; over-claiming an encode-speed or quality number
  before the consumer is measured is the failure mode to avoid (state plainly:
  "MV field produced + direction-verified" only).

## References

- [ADR-0113](0113-optical-flow-mc.md) (the motion-estimation strategy + the
  MC-failure data this v1 deliberately does not rebuild),
  [ADR-0114](0114-encoder-steering.md) Tier 3 (NVENC external ME hints are the
  only vendor hook; the consumer is producer-gated),
  [ADR-0111](0111-benchmark-methodology.md) (how a speed/quality claim must be
  measured before it ships).
- `libavfilter/motion_estimation.c` (`ff_me_search_epzs` / `ff_me_search_ds`,
  SAD cost), `libpelorus/include/pelorus/interop.h` (`PEL_SEC_MOTION`,
  `PelorusMotionSection`), `/usr/include/ffnvcodec/nvEncodeAPI.h`
  (`enableExternalMEHints`, `meExternalHints`, `NV_ENC_EXTERNAL_ME_HINT`).
- Source: `req` — build the motion estimator as the hardest roadmap item, one
  solid first implementation: the Vulkan compute block-match pass producing a
  per-block integer-pel MV field emitted as Pelorus MV-hint side data, framed
  honestly as encode speed (the NVENC ME-hint consumer) rather than quality, with
  the consumer patch a documented follow-up.
