<!-- markdownlint-disable MD013 MD060 -->
# ADR-0131: MC→denoise motion-compensated warp + per-block confidence

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

ADR-0113 specified, but never landed, the motion-compensated temporal warp:
`vf_pelorus_denoise_vulkan` averaged the **same-coordinate** sample of each
previous frame, which ghosts moving regions and capped the GPU denoise win at
−34% (vs the CPU stand-in's −89%). `vf_pelorus_mc` already produced the MV
field; ADR-0130 then refined it to sub-pel quarter-pel. The remaining work is
to make denoise *consume* that field — warp its temporal fetch by the per-pixel
MV — and to gate the warp so a noise-matched (untrustworthy) vector does not
drag a tap onto the wrong pixel.

## Decision

1. **Per-block confidence section (`PEL_SEC_MOTION_CONF`, bit `1u<<6`).** A new
   append-only ABI section (`PelorusMotionConfSection`, 16 bytes) carrying one
   `uint8` per cell on the shared grid: `255*(1 − clamp(per-pixel winning SAD /
   0.10))`, derived host-side from the SAD `mc` already reads back (no shader
   change in mc). `PELORUS_ABI_MINOR` → 2; `PelorusMotionSection` stays frozen.
   The conformance fixture round-trips it and asserts R3/R4 back-compat.

2. **The warp (`vf_pelorus_denoise_vulkan mc=1`).** When the option is set the
   filter parses `PEL_SEC_MOTION` + `PEL_SEC_MOTION_CONF` from the input frame,
   uploads the quarter-pel MV grid and the confidence grid as host-visible
   SSBOs (retained as exec dependencies so they survive an async, `meta=0`
   submit), and replaces the same-coordinate `pel_prev(t, pos)` with a
   bilinear-sampled `pel_prev_mc(t, pos + MV)` (nearest-cell MV — block-constant,
   not bilinear-upsampled, to avoid pulling vectors across motion boundaries;
   `mv_scale = 0.25` for quarter-pel; chroma MV subsampled by `log2_chroma`).

3. **Confidence + tcut gating.** The warped tap is blended toward the
   same-coordinate tap by the per-cell confidence (`mix(same, warped, conf)`),
   so a low-confidence MV degrades gracefully to the same-coordinate behaviour;
   the existing per-pixel `temporal_cut` gate then rejects bad/occluded taps
   either way. Both guards are cheap and complementary (per-block trust + per-
   pixel residual).

4. **Lockstep.** `pelorus_denoise.comp` mirrors the warp algorithmically; being
   single-plane it bakes the quarter-pel × chroma-subsampling scale into the
   host-provided `mv_scale`/`cell_*`, where the filter applies `chroma_shift`
   in-shader (the two push-const layouts already differ — AGENTS rule 4 is
   algorithmic lockstep, not byte-for-byte).

## Alternatives considered

- **Bilinear-upsampled MV field.** Smooths the block-MV grid but pulls wrong
  vectors across object edges; nearest-cell + the confidence/tcut guards is the
  safer v1. Revisit if an A/B shows block-edge artefacts.
- **No confidence; rely on `tcut` alone.** `tcut` is per-pixel and catches bad
  *taps*, but a confidently-wrong MV (noise match with a low residual at the
  warped point) can still mislead it; the per-block confidence is the cheaper,
  earlier guard. Shipping both is the point of the "+confidence" sequencing.
- **Extend `PelorusMotionSection` with a confidence field.** Forbidden — the
  struct is frozen at 32 bytes; a new section is the append-only path.
- **Compute confidence in the mc shader.** Unnecessary — mc already reads back
  the per-block winning SAD; confidence is a host-side transform of it.

## Consequences

- The denoise temporal term follows motion: validated on a noisy ~2.5 px/frame
  scroll, the warp (`mc=1`) reaches Y-SSIM 0.5520 vs clean where same-coordinate
  (`mc=0`) reaches 0.5478 and the noisy input is 0.5351 — the warp is active
  (mc=1 ≠ mc=0) and beats same-coordinate denoising on high-motion content.
  Larger gains are expected on a real high-motion corpus; a BD-VMAF demo on the
  BBB high-motion segment is the follow-up proof.
- Pipeline order is now load-bearing: `... pelorus_mc_vulkan=meta=1,
  pelorus_denoise_vulkan=mc=1 ...` (mc must precede denoise). Without an upstream
  mc the section is absent and denoise falls back to same-coordinate.
- ABI minor 2; the new section is documented in `docs/api/interop-abi.md`. The
  vmafx-vendored `interop.c` mirror is a follow-up — append-only means a vmafx
  built against minor 1 still parses every section it knows (R4); it simply does
  not see `PEL_SEC_MOTION_CONF`, which it does not consume.
- Patches `0003` (denoise) and `0007` (mc) regenerate; `pelorus_denoise.comp`
  updated in lockstep.

## References

- ADR-0113 (the warp strategy this realizes) — flipped to Accepted.
- ADR-0130 (the sub-pel quarter-pel MV field the warp consumes, `mv_scale=0.25`).
- `/bump-abi` (append-only section process); ADR-0103 / ADR-0109 (ABI rules).
- Validation: noisy fast-scroll A/B (`pelorus_mc_vulkan=meta=1,
  pelorus_denoise_vulkan=mc={0,1}`) vs a clean reference, lossless ffv1.
