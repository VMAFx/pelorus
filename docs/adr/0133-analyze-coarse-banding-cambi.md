<!-- markdownlint-disable MD013 -->
# ADR-0133: Coarse inter-tile banding scale for analyze ROI (CAMBI alignment)

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

`vf_pelorus_analyze_vulkan` drives auto-ROI (ADR-0114): it reduces each frame
into 32×32-pixel tiles and flags banding-prone tiles so the encoder spends bits
where contouring would otherwise show. The v0.1 detector is **single-scale** —
a tile is banding-prone when its *own* spatial variance sits in a narrow window
(`var_lo = 2e-6 ≤ var < flat_thr`): textured tiles (high variance) mask banding
and are excluded; dead-flat tiles (variance below `var_lo`) are excluded because
a constant tile has nothing to band.

That `var_lo` floor is the blind spot. A shallow luma ramp — a sky, a shadow
falloff, a slow vignette — spans many tiles; within any single 32×32 tile the
gradient is so gentle that the tile's own variance falls *below* `var_lo`, so
the detector rejects it. Yet the ramp bands visibly across tiles, which is
exactly what CAMBI (the contrast-aware multi-scale banding index) measures by
detecting near-constant regions and their inter-region steps at several
downsampled scales.

Measured on a synthetic 0x10→0x30 diagonal ramp: CAMBI scores it **0.625**
(visible banding), but per-tile variance is ≈1.3e-6 < `var_lo`, so the
single-scale detector flagged **0** tiles and auto-ROI ignored the ramp
entirely. The detector and the perceptual metric disagree precisely on the
content class ROI exists to protect.

## Decision

Add a **second, coarser detection scale** — the inter-tile mean-luma gradient —
and flag a tile if **either** scale fires (`score = max(fine, coarse)`).

1. The compute shader already derives each tile's mean luma; emit it as a 5th
   struct-of-arrays span (`tile[4·N + idx] = uint(mean·GS)`) alongside the
   existing var/edge/grad/valid spans. One extra `uint` per tile.
2. On the host, `tile_coarse_band_score()` reads the tile-mean field and, for a
   tile whose own variance is below `flat_thr` (still gated to flats — texture
   masks banding), computes the inter-tile mean gradient over its 4-neighbour
   stencil. A **small but non-zero** step (≈1..12 code-values per tile) is the
   signature of a shallow ramp and scores banding-prone, contrast-weighted by
   the step amplitude. Below ≈1 code/tile is noise; above ≈12 codes/tile is a
   real edge, not banding — both score zero.

This is a **two-scale** CAMBI alignment (per-tile + inter-tile), not the full
multi-scale pyramid. It is filter-internal: no interop ABI change, no new
section bit. The standalone `pelorus_analyze.comp` reference stays frame-scalar
(it never modelled the per-tile readback); its header now documents that the
inline filter is authoritative for the per-tile ROI path.

## Alternatives considered

- **Full CAMBI multi-scale pyramid** (downsample ×½/×¼/×⅛, detect per scale).
  The faithful implementation, but several extra GPU passes + host work for a
  pre-encode hint, and the two-scale form already captures the dominant missed
  class (shallow ramps spanning many tiles). Deferred as a future refinement.
- **Lower the `var_lo` floor** so the existing single-scale detector accepts
  shallower per-tile variance. Rejected: sensor/film noise raises per-tile
  variance without banding, so a lower floor false-positives on flat-but-noisy
  content; the inter-tile gradient distinguishes a coherent ramp from noise.
- **Run CAMBI itself on the GPU as the detector.** CAMBI is a quality *metric*
  (reference-grade, multi-scale, relatively heavy), not a fast per-frame
  pre-encode signal. Aligning the cheap detector *toward* CAMBI is the goal;
  reimplementing CAMBI in the hot path is not.

## Consequences

- Auto-ROI now covers shallow gradients it structurally ignored — a capability
  gain, not a threshold tweak. The fine scale is unchanged, so already-detected
  content is unaffected.
- Storage: per-tile SoA grows 4→5 `uint` (≈4 KB extra at 1080p) — negligible.
- The coarse scale is gated to flat tiles, so detail/texture cannot trip it; no
  regression on textured content (the var gate excludes it).
- On a *full-frame* gradient the encode gain degenerates: every tile is flagged,
  so ROI cannot differentially reallocate and approaches a uniform QP bump. The
  win is on **mixed** content (gradient beside texture), where ROI moves bits
  from masked detail to the banding ramp.

## Validation

- **Detection**: on the CAMBI-0.625 ramp the two-scale detector flags 27/920
  tiles (vs 0 for the var-floor); a constant gray frame flags 0 (no
  false-positive).
- **Encode A/B** (hevc_nvenc, cq34, `-pelorus_roi 1`, steep 0x08→0x38 ramp,
  CAMBI of the decoded output): baseline 12.81 → CAMBI-aligned auto-ROI 12.65 at
  +6.7 % bits — correct sign (less banding), no regression. Modest magnitude is
  expected for a full-frame ramp (see Consequences).
- Fast gate (`meson test --suite=fast`) green: analyze `.comp` compiles, interop
  conformance unchanged.

## References

- ADR-0114 (per-tile ROI steering — the channel this sharpens).
- ADR-0109 (the analyze filter); ADR-0132 (per-shot CRF, the sibling consumer of
  analyze's per-frame signals).
- Z. Tandon et al., "CAMBI: Contrast-aware multiscale banding index" (Netflix,
  2021) — the multi-scale banding model this aligns toward.
- `docs/metrics/analyze.md`; `docs/development/bench-results.md`.
