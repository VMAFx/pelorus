<!-- markdownlint-disable MD013 -->
# ADR-0112: vf_pelorus_denoise — causal edge-preserving spatio-temporal denoise

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: denoise, vulkan, interop, ffmpeg

## Context

Temporal denoise is the biggest BD-rate lever in the Pelorus build order:
sensor/film grain is temporally incoherent, so the encoder cannot inter-predict
it and re-codes it as residual every frame. Removing it before encode lets the
fixed bit budget go to structural detail. ADR-0111's clean-referenced benchmark
quantified the opportunity with a stand-in temporal denoiser: **−42.94% BD-rate**
on high-motion BBB and **−88.94%** on a locked-off scene (both + seeded grain).
The interop ABI already reserved the `PEL_SEC_DENOISE` slot (ADR-0103); this ADR
fills it with a real Vulkan filter.

## Decision

Add `vf_pelorus_denoise_vulkan`: a single-pass Vulkan compute filter
(`PEL_DENOISER_BILATERAL_TEMPORAL`) over a **causal** window — the current frame
plus *N* previous frames held in VRAM as cheap `av_frame_clone` refs — with **no
motion compensation** (same-coordinate temporal taps; MC is a later filter,
`PEL_DENOISE_FLAG_MOTION_COMP` is reserved-off).

- **Spatial term**: an NLM-lite joint bilateral over a small window whose *range*
  weight is a 3×3-patch SSD. A real edge has high patch SSD across it, so its
  weight collapses and the filter refuses to average across the edge (preserves
  text / facial lines); flat-region noise (low patch SSD) is averaged away. The
  centre carries a self-weight of 1 (so the denominator is never zero).
- **Temporal term**: the same-coordinate sample of each previous frame, gated by
  a per-pixel similarity threshold — a delta above `temporal_cut` breaks the walk
  so motion / scene-cuts cannot ghost — and decayed by `temporal_decay` per frame.
- **Combine**: `num = (1−blend)·numS + blend·numT`, `den` likewise,
  `out = mix(cur, num/den, strength)`. `patch=0` ⇒ temporal-only; `blend=0` ⇒
  spatial-only; all-temporal-gated ⇒ collapses to the spatial fallback.

The exec is the **hand-rolled explicit path** (modelled on
`vf_pelorus_analyze_vulkan`), not `ff_vk_filter_process_Nin`, because `meta=1`
residual emission needs a stats SSBO bound *in the denoise dispatch* plus a
submit+wait readback, and the causal window length varies — neither of which the
library helper exposes. With `meta=1` the filter free-rides a residual reduction
on the dispatch and emits the pre-reserved 28-byte `PEL_SEC_DENOISE` section
(residual energy, applied strength, noise-σ estimate, PSNR-vs-input); with
`meta=0` the SSBO/readback/sync are skipped entirely. The host-side params are
the `pelorus/denoise.h` contract (normalized [0,1], `{Y,Cb,Cr,A}` plane order);
the standalone reference shader `libpelorus/shaders/pelorus_denoise.comp` and the
filter's inline GLSL stay in lockstep (AGENTS hard rule 4).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Causal NLM-lite + gated temporal (this) | No latency/flush; edge-preserving; cheap; prerequisite-free | Static-region gain only (no MC) | **Chosen** — best gain/risk for a first kernel |
| Centred window (atadenoise model, prev/cur/next) | Symmetric taps | Forces 1-frame latency + a `request_frame` EOF flush | Rejected — needless complexity for a pre-encode pass |
| Motion-compensated temporal | Cleans moving regions too | Needs the optical-flow / ME pass (build-order step 5) first; warp-artifact failure modes | Deferred to `vf_pelorus_mc` |
| NLM-temporal with integral images | Strongest spatial denoise | The 3-shader device-address SAT pipeline; high correctness risk first time | Rejected for v0.x |
| `ff_vk_filter_process_Nin` for the exec | Less code | No hook to bind the residual SSBO mid-dispatch or a variable window | Rejected — cannot serve `meta=1` |

## Consequences

- **Positive**: fills the denoise data plane; `analyze → denoise → deband →
  encode` gives vmafx genuine residual telemetry and the encoder a clean field.
  Codec-agnostic; zero-copy in VRAM.
- **Negative**: no-MC means the gain is concentrated in static/slow regions; fast
  full-frame motion sees little benefit (honest envelope, see ADR-0111).
- **Neutral / follow-ups**: `PEL_DENOISE_FLAG_AUTO_SIGMA` (variance-fed σ from
  `PEL_SEC_VARIANCE`) and `MOTION_COMP` are reserved flags, behaviour deferred.
  The ADR-0111 stand-in result must be re-proven with this Vulkan filter.

## References

- `ffmpeg-patches/files/vf_pelorus_denoise_vulkan.c`,
  `libpelorus/shaders/pelorus_denoise.comp`,
  `libpelorus/include/pelorus/denoise.h`; [docs/metrics/denoise.md](../metrics/denoise.md).
- [ADR-0103](0103-interop-sidedata-abi.md) (the reserved `PEL_SEC_DENOISE` slot),
  [ADR-0109](0109-analyze-filter.md) (the readback pattern reused),
  [ADR-0111](0111-benchmark-methodology.md) (the clean-referenced proof of the gain).
- `libavfilter/vf_bwdif_vulkan.c` (N-input binding-order contract).
- Source: `req` — "well but we are looking for gains lol?" (temporal denoise is
  the biggest BD-rate lever; this filter realises it on the GPU).
