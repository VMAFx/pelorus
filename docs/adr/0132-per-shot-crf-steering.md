<!-- markdownlint-disable MD013 MD060 -->
# ADR-0132: Per-shot complexity-budget CRF steering (design)

- **Status**: Proposed
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

The proven per-tile ROI steering (ADR-0114, auto-ROI: −41% banding on NVENC,
−47% vs x265 AQ) redistributes bits **within** a frame. The single biggest
remaining BD-rate lever in modern adaptive pipelines is redistributing bits
**across shots** by complexity — the "per-shot encoding" pattern (SVT-AV1
`--enable-variance-boost`, x265 per-shot CRF, Netflix per-shot optimization):
spend fewer bits on shots the eye forgives (high motion → motion masking; dark/
flat → low salience) and more on shots that need them (slow detailed pans), at a
better quality-per-bit than a single global CRF.

Pelorus already produces every input signal: `vf_pelorus_analyze` (per-tile
variance / edge / banding), `vf_pelorus_mc` (global/peak motion magnitude,
MV-field entropy, scene-cut flag), and `vf_pelorus_scenecut` (shot boundaries via
`pict_type=I`). What is missing is (a) an aggregate per-shot **complexity
scalar**, (b) a **complexity → CRF/qoffset mapping**, and (c) an **apply
mechanism** that does not require a new encoder fork.

## Decision

Build per-shot CRF steering on the **already-proven ROI/qpmap channel**, in
three layers, smallest-first:

1. **Complexity scalar (producer).** Aggregate the per-frame signals analyze and
   mc already compute into a single normalized `complexity ∈ [0,1]`:
   `C = w_v·variance + w_e·edge_density + w_m·motion_magnitude + w_b·banding_risk`
   (weights tunable; all terms already in `PelorusVarianceSection` /
   `PelorusMotionSection`). Smooth within a shot with an EMA that **resets on
   `has_scene_cut`** (no explicit shot-state needed; the scene-cut flag delimits
   shots). Emit `C` append-only (a small field on a new
   `PEL_SEC_COMPLEXITY` section via `/bump-abi`, mirrored to vmafx).

2. **Apply via a per-frame uniform qoffset (no new encoder mechanism).** Emit a
   full-frame `AVRegionOfInterest` with `qoffset = g(C)` on the **same
   `AV_FRAME_DATA_REGIONS_OF_INTEREST` channel** the auto-ROI already rides
   (summed with the per-tile offsets). Cheap shots get a positive offset (fewer
   bits), expensive-and-salient shots a negative offset (more bits). This reuses
   the entire proven steering path (libx265/NVENC/QSV/libaom/SVT-AV1 consumers)
   with **zero new patches** — it is a producer + a mapping, not an encoder
   change.

3. **Learn the mapping `g(C)` with the autotune loop (ADR-0106), do not
   hand-tune it.** The direction and magnitude of `g` are content- and
   metric-dependent; a fixed hand-tuned curve **risks regressing BD-rate** (give
   a high-motion shot fewer bits and a metric that doesn't model masking
   penalizes it). The autotune loop sweeps `g`'s parameters against the
   encoded-VMAF/SSIMULACRA2-at-bitrate oracle, **gated on `honored_fraction`**
   (the anti-gaming check) and a multi-metric objective, per-shot-segmented by
   `mc.has_scene_cut`, prior-seeded by `C`. Ship `g` **off by default** until
   the autotune proves a positive BD-rate on the corpus.

## Alternatives considered

- **Segment-split → per-segment CRF → concat.** The classic per-shot pipeline.
  Rejected as the *primary* path: it is a wrapper/orchestration tool, not a
  zero-copy filter, and breaks the single-pass VRAM pipeline. Worth a
  `scripts/` reference implementation for an offline BD-rate ceiling, but the
  in-pipeline qoffset path is the product.
- **Two-pass with a complexity-informed second pass.** Doubles encode cost;
  the qoffset channel gets most of the win in one pass.
- **A new fork patch for per-frame CRF.** Unnecessary — the ROI/qpmap channel
  already carries a per-frame qoffset on every shipped backend.
- **Hand-tuned `g(C)` shipped on by default.** Rejected — regression risk
  without the autotune oracle; this is exactly the metric-gaming trap
  `honored_fraction` exists to catch.

## Consequences

- The first buildable, low-risk piece is the **complexity scalar producer**
  (analyze/mc aggregate + EMA + `PEL_SEC_COMPLEXITY`), independently testable
  (it tracks content complexity) and useful to the autotune loop regardless.
- The apply layer rides proven infra → no new encoder patch, no new on-HW proof
  burden beyond the BD-rate A/B.
- The mapping is gated behind the autotune loop, so per-shot CRF lands **safe by
  construction** (proven-positive-or-off), not as a hand-tuned gamble.
- Dependency order: complexity producer → autotune integration (ADR-0106) →
  default-on once proven. Each is its own PR.

## Validation plan

BD-rate A/B (per-shot-CRF qoffset vs flat CRF) on genuinely multi-shot content
(the full-BBB segments already used for the warp bench span static / chase /
action), encoded on the proven channel, scored with VMAF **and** SSIMULACRA2 +
CAMBI (multi-metric, to defeat single-metric gaming), with the mapping
autotune-optimized. The honest bar: a positive BD-rate at iso-multi-metric
quality, or it stays off.

## References

- ADR-0114 (per-tile ROI steering — the proven channel this extends).
- ADR-0106 (autotune control plane — learns `g(C)`).
- `PelorusVarianceSection` / `PelorusMotionSection` (the complexity inputs);
  `vf_pelorus_scenecut` (shot boundaries); `bench-results.md` v0.4 ROI proof.
