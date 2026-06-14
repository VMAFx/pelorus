<!-- markdownlint-disable MD013 -->
# ADR-0109: vf_pelorus_analyze emits measured banding/variance via GPU readback

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: analyze, vulkan, interop, ffmpeg

## Context

The deband filter ([ADR-0102](0102-flagship-smart-deband.md)) attaches a Pelorus
interop blob, but with a *config-derived* banding marker — it has no measured
view of the frame. For vmafx's perceptually-weighted scoring
([ADR-0103](0103-interop-sidedata-abi.md)) to be meaningful, something must
produce the real `PEL_SEC_BANDING` / `PEL_SEC_VARIANCE` data. Computing it on the
GPU (where the frame already lives) and reading back a small summary is far
cheaper than a CPU pass over decoded pixels.

## Decision

We will add `vf_pelorus_analyze_vulkan`: a pass-through filter that reduces each
frame's luma to per-frame banding / variance / edge statistics with a compute
shader (per-tile shared-memory reduction into a sliced SSBO to cut atomic
contention), reads back the accumulators from a host-visible pooled buffer, and
attaches the *measured* `PEL_SEC_VARIANCE` + `PEL_SEC_BANDING` sections. It
modifies no pixels. The GPU→host readback follows FFmpeg's own
`vf_scdet_vulkan` pattern (pooled `HOST_VISIBLE` buffer, `CmdFillBuffer` zero,
dispatch, host-read barrier, submit + wait, read `mapped_mem`). v0.1 emits
frame-level scalars (per-cell map payloads are a later, append-only addition).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| GPU reduction + small readback (this) | Frame already in VRAM; tiny readback; reuses the scdet pattern | A per-frame GPU→host sync point | **Chosen** |
| Compute the stats inside the deband shader | One pass | Deband runs per-plane with different geometry; conflates "measure" and "transform"; can't run analyze standalone | Rejected — separate concerns |
| CPU pass over decoded pixels | Simple, no Vulkan | Forces a VRAM→RAM download, defeating zero-copy | Rejected |
| Emit per-cell maps now | Richer weighting | Needs a pack-API extension for trailing map payloads + larger readback | Deferred (append-only, R1) |

## Consequences

- **Positive**: the interop data plane carries real measurements; `analyze →
  deband → encode` gives vmafx genuine per-frame weighting input. Codec-agnostic
  (the stats describe the source).
- **Negative**: a per-frame submit+wait sync point (acceptable for offline
  pre-encode; a future async/double-buffered readback can hide it).
- **Neutral / follow-ups**: per-cell maps (populate the section `*_offset`
  fields) need a `pel_blob_pack` "attachments" extension; banding-risk is a
  coarse low-variance-area proxy until a dedicated contour estimator lands.

## References

- `libavfilter/vf_scdet_vulkan.c` — the readback model.
- [docs/metrics/analyze.md](../metrics/analyze.md), [ADR-0103](0103-interop-sidedata-abi.md).
- Source: `req` — "if you have questions, where popup? and if not, why stop?"
  (proceed with the obvious next step: the measured-map producer before the
  vmafx consumer).
