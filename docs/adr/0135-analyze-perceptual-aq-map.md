<!-- markdownlint-disable MD013 -->
# ADR-0135: Source-side perceptual bit-allocation map for analyze — REJECTED on measurement

- **Status**: Rejected (2026-06-20) — implemented, measured, does not beat variance-AQ
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

`vf_pelorus_analyze_vulkan` already emits a banding-only auto-ROI (`roi=1`,
ADR-0114/0133): negative qoffsets on flat banding-prone tiles. The proposal here
was to **generalize** that to a full-frame *perceptual bit-allocation map* (an
`aq=1` mode) — a source-side, JND-weighted importance map that both protects
perceptually-important tiles (flats, edges, banding) AND **reclaims** bits from
high-masking textured tiles, i.e. perceptual adaptive quantization computed on
the clean source, aiming to **beat the encoder's in-loop variance-AQ** at
iso-bitrate. All inputs (per-tile var/edge/grad/mean) are already on the host, so
the change was ~80 host-side lines, no shader and no interop-ABI change, reusing
the existing `AV_FRAME_DATA_REGIONS_OF_INTEREST` channel.

The design was grounded in a research workflow (verified against x265 4.1 source):
variance-AQ is a within-frame `qp ∝ log2(variance)` mask with a single global
strength, normalized to its own per-frame mean, and it *starves* the lowest-
variance tiles — exactly the smooth gradients that contour. A source map can, in
principle, beat it via a luma-adaptive (Chou-Li) + texture-masking (NAMM) JND
threshold, a coherent-edge protect floor, and the CAMBI banding floor, inverting
the sign on the tiles variance-AQ gets backwards.

## Decision

**Rejected — do not merge.** The model was implemented in full (Chou-Li luma
adaptation `LA = 17·(1−√(B/127))+3`, literature-verified; NAMM texture-masking
fusion; edge-coherence protect floor; CAMBI banding floor; signed mean-centered
qoffsets with a per-frame mean-subtraction to enforce iso-bitrate redistribution;
a `reclaim_gain` knob; the dense-map per-tile rect cap) and benchmarked against
NVENC's own AQ. It **strongly reduces banding but at a net overall-fidelity cost,
and on real non-banding content it is a pure loss** — it does not beat variance-AQ
on the metric that matters (SSIMULACRA2), so it fails the pre-registered
kill-criterion. The existing `roi=1` banding mode already captures the banding
benefit *without* the texture-starving harm.

## Validation (the measurement that rejected it)

CBR RD curves (4 points, mean-over-frames), hevc_nvenc, A/B = NVENC's own AQ vs
`aq=1` map + AQ-off, BD-rate via `bd_rate.py` (negative = win). Metrics:
SSIMULACRA2 (overall perceptual fidelity, higher = better) and CAMBI (banding,
lower = better); PSNR as a guard.

**Mixed synthetic composite (gradient + texture), `reclaim_gain` sweep:**

| `reclaim_gain` | SSIMULACRA2 BD-rate | PSNR BD-rate | CAMBI (per-point) |
|---|---|---|---|
| 0.7 | **+13.05 %** (loss) | +7.21 % (loss) | −14 % … −25 % (win) |
| 1.0 (bit-neutral) | **+19.89 %** (loss) | +13.53 % (loss) | larger win |

More texture-starving → larger banding win but worse fidelity; the CAMBI BD-rate
is undefined (`nan`) because the baseline CAMBI is *flat* across bitrate — NVENC's
AQ never fixes the gradient, so there is no equal-CAMBI bitrate to integrate.

**Real BBB content (sky + foreground, `reclaim_gain=0.7`):** CAMBI ≈ 0 for both
arms (no banding to fix), so the map only starved texture for no benefit —
SSIMULACRA2 dropped at every point (e.g. 11.28 → 6.15, 35.18 → 32.93) and bitrate
rose 3–10 %. A pure loss on the common (non-banding) case.

The map is also **not bit-neutral**: even at CBR it ran 3–9 % over the baseline,
because adding bits to flats costs more than reclaiming from texture frees within
the frame budget.

## Why it loses (the iso-bitrate tension)

At iso-bitrate, reducing banding means moving bits *from* texture *to* flats.
SSIMULACRA2 (and PSNR) weight texture/detail fidelity heavily, so the only way
the trade nets positive is if the starved texture is *truly imperceptible* — and
the cheap scalar masking proxy (`coh = e/(0.5+8v)` from per-tile `t_edge`/`t_var`,
no structure tensor) cannot reliably distinguish a maskable stochastic texture
from perceptible detail, so it starves visible detail and SSIMULACRA2 punishes it.

## Alternatives considered / follow-up condition

- **`roi=1` banding-only (the incumbent).** Protects banding tiles, never starves
  texture, keeps the encoder's AQ on — captures the banding win with no fidelity
  cost. It dominates `aq` for banding-prone content; `aq` has no niche over it.
- **A more accurate masking discriminator** (structure-tensor / oriented-edge
  detection + a Mannos-Sakrison CSF spatial-frequency term) — would need new GPU
  output (a directional moment per tile), and only *might* let the reclaim side
  starve solely-imperceptible texture. This is the only path that could revive the
  idea; it is a much larger, GPU-side effort and is **not** pursued now.
- **Per-shot CRF steering (ADR-0132)** remains the open BD-rate lever; it allocates
  across *shots* (temporal) rather than within-frame (spatial), a different axis
  the encoder's per-frame AQ does not cover.

## Consequences

- No code merged; master unchanged. The design + the rejecting measurement are
  preserved here and in `docs/development/bench-results.md` v0.12 so the lever is
  not re-attempted blindly (cf. the fp16 and subgroup-reduction negatives this
  cycle). The signed-qoffset / dense-map plumbing is recoverable from this ADR if
  the structure-tensor follow-up is ever taken up.

## References

- ADR-0114 (the ROI channel), ADR-0133 (the CAMBI-aligned banding ROI this would
  have generalized), ADR-0132 (per-shot CRF — the remaining BD-rate lever).
- x265 4.1 `slicetype.cpp` `calcAdaptiveQuantFrame` (the variance-AQ baseline);
  Chou & Li 1995 JND luma-masking model.
- `docs/development/bench-results.md` v0.12.
