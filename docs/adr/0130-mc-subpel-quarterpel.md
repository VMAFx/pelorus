<!-- markdownlint-disable MD013 MD060 -->
# ADR-0130: vf_pelorus_mc — sub-pel MV refinement + quarter-pel grid units

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

`vf_pelorus_mc_vulkan` produced an **integer-pel** block-matching MV field. The
roadmap's top lever — the ADR-0113 MC→denoise motion-compensated warp — needs a
sub-pel MV field to pay off: same-coordinate temporal averaging caps the GPU
denoise win at −34% (vs the CPU stand-in's −89%), and warping the fetch by an
*integer-pel* MV still misses the sub-pel component of real motion, leaving the
moving-region ghosting the warp was meant to remove. The producer's own header
flagged this: raw-pixel block-matching at integer precision is noise-limited, so
wiring the warp first would likely measure a negative. The decision (recorded
when sequencing the warp) was to **refine mc to sub-pel before building the
warp**.

Three facts shaped the wire format:

- ADR-0113 always specified the grid as **¼-pel `int16 (dx,dy)`**, but the
  producer wrote integer pixel values into it — a latent spec violation.
- The only shipped consumer, the NVENC external-ME-hint patch (0008), read the
  grid and fed `NV_ENC_EXTERNAL_ME_HINT.mvx` (integer-pixel, S12.0) **unscaled**
  — consistent with the integer producer, but 4× off from the ¼-pel spec.
- `NV_ENC_EXTERNAL_ME_HINT` is integer-pixel; the quarter-pel hint lives in a
  different (`*_ME_SB_HINT`, S12.2) struct the consumer does not use.

## Decision

1. **Sub-pel refinement (parabolic SAD fit).** After the integer diamond search
   converges, evaluate the block SAD at the integer minimum and its four
   axis-neighbours and fit a parabola per axis; the vertex gives a `[-0.5, 0.5]`
   sub-pel offset (`offset = 0.5·(s₋ − s₊) / (s₋ − 2·s₀ + s₊)`, taken only when
   the curvature `s₋ − 2·s₀ + s₊ > 0`, else the integer minimum stands). This
   reuses the existing cooperative `block_sad` (four extra workgroup reductions,
   no new fetches), with a leading `barrier()` to publish `s_best_*` to all
   lanes and a `barrier()` after each SAD to avoid a WAR race on `s_sad[]`.

2. **Quarter-pel grid units (Q2 fixed-point).** Emit `round((best + offset)·4)`
   as the stored `int16`. This makes the grid conform to ADR-0113's ¼-pel spec.
   The unit is a documented convention (interop.h comment), not a new struct
   field — `PelorusMotionSection` stays frozen at 32 bytes, **no ABI bump**.

3. **Consumer coordination.** The `PelorusMotionSection` summary scalars
   (`global_motion_*`, `motion_magnitude_*`, entropy) stay in **whole luma
   pixels** — the host derivation divides the quarter-pel grid by 4. The NVENC
   ME-hint consumer (patch 0008) now round-divides the quarter-pel grid by 4
   into its integer-pixel hint field, which also corrects the prior 4×-too-small
   hint (a plausible contributor to that path's measured no-gain; a retest is now
   unblocked).

4. **Lockstep.** The refinement is mirrored in `pelorus_mc.comp` (AGENTS rule 4).

## Alternatives considered

- **True half-pel search (bilinear-interpolated reference).** Evaluate SAD at
  the 8 half-pel positions using bilinear reference samples. More accurate near
  motion boundaries but costs 8 interpolated-fetch SAD passes; the parabolic fit
  is a standard, fetch-free first sub-pel estimate accurate to ~⅛-pel on smooth
  SAD surfaces and is the right v1 for a hint/warp field. Bilinear half-pel can
  follow if the warp A/B shows the parabolic estimate is the limiter.
- **Keep the grid integer-pel; carry a separate sub-pel fraction.** Rejected:
  doubles the wire payload and complicates every consumer for no gain over a
  single ¼-pel field.
- **Add a `mv_scale` field to `PelorusMotionSection`.** Rejected: would resize
  the frozen struct (an ABI break) to encode a constant the spec already fixes
  at ¼-pel. The scale is a documented convention, not run-time data.
- **Bump `PELORUS_ABI_MINOR`.** Not done: the struct layout is unchanged and the
  grid was always spec'd ¼-pel, so this conforms the producer to the existing
  contract rather than extending it. The summary scalars (the field vmafx reads)
  are unchanged.

## Consequences

- mc emits sub-pel-accurate quarter-pel MVs. Validated on a known ~0.51 px/frame
  scroll: the measured global motion is `gmx ≈ 0.55` px (fractional) where
  integer-pel mc rounded to 0/1; quarter-pel fractions are present across the
  field; `magmax` reaches the ×4-scaled search bound.
- The NVENC ME-hint consumer is now ¼-pel-correct; its retest is unblocked.
- The MC→denoise warp can consume the field with `mv_scale = 0.25` and obtain
  the sub-pel fetch the win depends on.
- Patches `0007` (mc) and `0008` (nvenc ME-hints) regenerate; `.comp` updated in
  lockstep; no ABI bump.

## References

- ADR-0113 (MC→denoise warp; ¼-pel grid spec, motion-estimation strategy).
- NVENC SDK `nvEncodeAPI.h`: `NV_ENC_EXTERNAL_ME_HINT.mvx` S12.0 integer-pixel;
  `NV_ENC_EXTERNAL_ME_SB_HINT` S12.2 quarter-pel.
- Validation: `pelorus_mc_vulkan=meta=1` on a `scroll`-panned still → fractional
  `global_motion_x`, sub-pel quarter-pel grid values.
