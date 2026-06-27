<!-- markdownlint-disable MD013 -->
# ADR-0141: Wide-band reach for deband (multi-radius / extended range) — REJECTED on measurement

- **Status**: Rejected (2026-06-27) — designed + built + measured; the pre-encode gain does not survive the encoder
- **Date**: 2026-06-27
- **Deciders**: Lusoris

## Context

`vf_pelorus_deband_vulkan` samples one random-distance ring of 4 reference taps
at `dist = r·range` (the vf_deband algorithm), with `range` capped at 31. A band
of width `W` needs reference taps that reach across the sub-threshold step into
the adjacent plateau; a pixel in the middle of the band is ~`W/2` from either
edge, so bands wider than ~`2·range` (≈62 px) have an un-reachable middle and
keep residual banding.

Bench [v0.15](../development/bench-results.md) established the **pre-encode**
premise: on a wide (~107 px) low-amplitude gradient (dither off), CAMBI falls
monotonically with `range` (7.84 → 6.21 → 5.15 → 4.53 → 4.30 at range 8/15/24/31)
and is **still descending at the cap** — the spatial pass is reach-limited. v0.15
explicitly deferred two gates to the build: a detail-preservation check, and a
**post-encode durability** check (does the gain survive the encoder, per the
v0.1.0 "8-bit crushes the dither and re-bands" lesson). This ADR is that build,
and the durability gate **rejected it.**

The proposal: extend the spatial reach so the durable plateau-flattening spans
wide bands. Two mechanisms were designed by a 5-agent judge panel and one
(in-pass dilated multi-ring with a near-flat mix gate) was built behind an opt-in
`reach` AVOption (default 0, byte-identical); the alternative (raise the `range`
cap) was measured directly.

## Decision

**Rejected.** Do not ship wide-band reach (neither the dilated multi-ring nor a
raised `range` cap). The pre-encode CAMBI improvement is real but does **not**
survive the encoder at any quality level, and is in fact counterproductive
post-encode. The existing `range ≤ 31` is at or near the durable optimum. The
`reach` code was reverted; this ADR records the measurement so the idea is not
re-litigated without new (real-corpus, 10-bit) evidence.

## Validation (the measurement that rejected it)

Synthetic wide ~107 px low-amplitude gradient; CAMBI via the v0.15-unblocked
tooling (`vmaf --backend cpu --feature cambi`, read the JSON it flushes before
its teardown sleep). Two builds were measured against each other.

**1. The dilated multi-ring (the panel's pick) is erratic even pre-encode.**
At range=31, dither off, the `reach` knob gave CAMBI 4.30 / 4.30 / 4.39 / 3.70 at
reach 0/1/2/3 — non-monotonic and weak. The straddling-**mean** dilutes the
spanning far-taps with the in-plateau near ring (`wideAvg ≈ halfway`), so a
single octave's shift rounds away at output precision. `reach=0` was verified
**byte-identical** to master (the opt-in default path is a true no-op).

**2. Raising the `range` cap is clean pre-encode and detail-safe** — and still
loses post-encode. Pure larger range (single ring, dither off) gave a clean
monotonic pre-encode CAMBI drop (4.30 → 4.01 → 3.80 → 3.62 → 3.55 at range
31/48/62/96/127), with detail-clip (mandelbrot) SSIM **flat at ~0.998 across the
whole sweep** — so the panel's "large range is sparse/noisy" fear was unfounded,
and larger range strictly dominates the multi-ring (cheaper, monotonic, detail-safe).

**3. The pre-encode gain does not survive the encoder — at any bitrate.** The
banding source (8-bit-grid coarse banding in a 10-bit container, CAMBI 7.84) was
debanded then encoded `hevc_nvenc -profile main10 -pix_fmt p010`, decoded, and
re-scored (10-bit, dither off):

| `range` | pre-encode | post cq28 | post cq18 | post cq10 |
|---:|---:|---:|---:|---:|
| baseline (no deband) | 7.84 | 5.64 | 6.57 | — |
| 31 | 2.30 | **5.00** | **5.08** | **4.98** |
| 62 | — | 5.20 | — | — |
| 127 | 0.75 | 5.37 | 5.49 | 5.47 |

The trend **inverts**: wider range is far better *pre*-encode (range=127 → 0.75)
but **monotonically worse** *post*-encode (5.00 → 5.20 → 5.37), and the inversion
holds even near-lossless (cq10: 5.47 vs 4.98). Deband at the existing range *does*
help durably (baseline 5.64 → range=31 5.00); extending reach beyond it is a pure
loss.

## Why it loses (the over-smoothing → re-banding mechanism)

The encoder's DCT + quantization is the dominant banding source, and you cannot
pre-smooth your way out of it. A narrow deband (range=31) softens each step into
a short local transition, preserving the staircase shape the encoder re-quantizes
into similar narrow steps. A wide deband (range=127) flattens the staircase into
a very gentle ramp (great pre-encode CAMBI), but the encoder quantizes that gentle
ramp **coarsely** — and a coarse quantization of a gentle ramp produces *wider*
bands (each quant level now spans a larger spatial extent). Smoother pre-encode
input → wider post-encode bands. This is the v0.1.0 finding ("the encoder
quantizes the dither away and re-bands") sharpened: not only does the spatial
gain not survive, **over**-reaching makes the durable result worse.

## Alternatives considered / follow-up condition

- **Dilated multi-ring (built, reverted)** — erratic pre-encode, dominated by
  raised range; doubly refuted by the durability result.
- **Downsample pre-pass (f3kdb multi-scale)** — the heavier "robust far-tap"
  fallback. Not built: it targets the same pre-encode wide-band CAMBI that the
  durability test shows does not survive, so it would inherit the same negative
  at far higher blast radius (second image + dispatch + two-shader lockstep).
- **Revival condition**: real 10-bit banding-prone content (night-sky / slow
  gradient pan) the corpus lacks, *and* a measured post-encode BD-rate win over
  `range ≤ 31` on it. The mechanism above and v0.1.0 both predict the same
  negative, so this is low-probability; tracked in OPPORTUNITIES, not planned.

## Consequences

- No code change ships. `range` stays capped at 31; no `reach` option. The
  `.comp` ↔ inline-GLSL lockstep is untouched.
- Bench [v0.16](../development/bench-results.md) records the negative and completes
  v0.15's deferred durability gate (v0.15's pre-encode "headroom" is now annotated
  as pre-encode-only).
- Reinforces the project rule: **a pre-encode metric gain is not a result until it
  is validated post-encode.** The deband's durable lever remains 10-bit output +
  the existing dither/range, not wider spatial reach.

## References

- bench v0.15 (the pre-encode premise) and v0.1.0 (8-bit re-banding), `docs/development/bench-results.md`
- `docs/research/0101-smart-deband.md` — the deband algorithm + dither-survival rationale
- ADR-0135 (perceptual-AQ, rejected) and ADR-0132 (per-shot CRF, negative) — sibling "rejected on measurement" decisions
