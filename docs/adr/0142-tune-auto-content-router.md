<!-- markdownlint-disable MD013 -->
# ADR-0142: `tune=auto` — content-adaptive pre-encode router (design) + the grain-sigma detection enabler

- **Status**: Proposed (2026-06-27) — design accepted; first enabler (grain-sigma metadata) shipped in this PR; per-content legs validated + the router built incrementally
- **Date**: 2026-06-27
- **Deciders**: Lusoris

## Context

The session's measurement campaign (real `vmaf` v1.0.16 + v0.6.1-NEG, an x265-enabled
ffmpeg, on-hardware NVENC) established a **law** that reframes the whole product:

- Hardware encoders (NVENC/QSV/AMF) **are** worse than software (x265 beats NVENC ~0.005
  SSIM iso-bitrate — no psy-RD/RDOQ/trellis, weak temporal filter, weightp-off-with-B).
- **But the pre-encode gain that closes the gap is REDUCTIVE and CONTENT-DEPENDENT:**
  - **Additive** (pre-sharpen/CAS) is a **wash** on VMAF-NEG and costs bits — you cannot
    add your way past the deadzone (the HF-energy "recovery" is a false proxy).
  - **Reductive** (denoise/dehalo/demosquito/deblock) is real, but the magnitude scales
    with *how much removable impairment exists*: −34% BD-rate on the real GPU denoise at
    heavy grain, ~5–7% at moderate grain, **~0 on clean** (clean Bluray barely bands →
    deband marginal; same law).
  - **Source-side rate-control** (perceptual-AQ map, per-shot CRF) **loses** structurally
    (ADR-0135/0132) — the encoder's own RC wins. Never emit a QP map.
- And it is **mostly tuning**: the same filter is a false-negative with the wrong params.
  Measured: the default denoise leans spatial and *over-smoothed* (tied at high bitrate);
  retuned temporal-dominant (`blend=0.85 prev=4`) it recovers a gain (+0.7 NEG) at the
  same point — grain is temporally *incoherent*, so the temporal walk is the lever, not
  spatial strength.

There is therefore **no single best filter** — there is the **right tuned boost per
content class**, and a router that detects the class and applies it. This is `tune=auto`.

## Decision

Build a content-adaptive router (`tune=auto`) that reads `vf_pelorus_analyze`'s per-frame
metadata, classifies the content per shot (segmented by `scene_cut`, with hysteresis),
and applies the matching **reductive** boost with params **scaled to the measured
impairment**. The routing table (designed by an 8-class boost panel, grounded in the law):

| content class | detect (analyze.*) | boost | key params (scale with) | expected |
|---|---|---|---|---|
| **grainy/noisy** | `grain_sigma` over flat bands (NEW metadata) | denoise (temporal-dominant) + grain_estimate→FGS resynth | `strength,blend,sigmat ∝ grain_sigma` | **−34%** proven |
| **anime/2D** | high `edge` AND high `banding`/flat co-occurrence | dehalo + aa + deband, co-gated by one edge mask | per the anime tune | ~3–8% SSIMu |
| **textured live-action** | high `variance`/`edge`, low `motion` | denoise spatial-dominant (strip micro-noise only) | `blend 0.3–0.4` | medium |
| **re-encode/compressed** | edge-band temporal excess (NEW) | **new `demosquito`** (edge-band temporal, the band denoise refuses) | premise-gated | single-digit % |
| **dark/low-light/HDR** | `dark_frac` (NEW) | denoise luma-masked to dark tiles | dark-gated | ~5–15% sub-frame |
| **screen/text** | very-high `edge`, very-high flat fraction, low `motion` | deblock (flat-snap + ring-strip) | edge-preserving | medium |
| **high-motion/sports** | `motion` ≥ 0.6 (needs upstream `mc`) | denoise + mc-warp (ADR-0131, on cleaner pixels) | tcut tight | large |
| **clean/pristine** | all impairment signals below floor | **NO-OP** (emit zero filters) | — | ~0 by design (avoided loss) |

**Critical constraints the router must respect** (verified against the analyze source):
`analyze.texture` is a *derived* `0.5·var + 0.5·edge` blend (not orthogonal) — classify on
`variance`+`edge` directly; `analyze.motion` is 0 unless an upstream `pelorus_mc` attached
`PEL_SEC_MOTION` — every motion-gated route needs `mc` in the graph.

**The detection gap (this PR's first enabler).** Three detectors need host-side scalars
that did not exist (only `analyze` emitted metadata; `grain_estimate`/`denoise`/`mc` emitted
none): `grain_sigma`, `noise_sigma`, `dark_frac`. All are addable via the ADR-0136
`av_dict_set` pattern — **no interop ABI change, no shader change, the value is already
computed**. This PR ships the first and highest-leverage one: `vf_pelorus_grain_estimate`
now emits `lavfi.pelorus.grain_sigma` (peak per-band RMS residual over the edge-gated flat
bands — structure is excluded by construction, so what survives is grain stddev) and
`lavfi.pelorus.grain_flat` (the flat fraction it was measured over = confidence). Verified
discriminating: heavy-grain clip 0.0191 vs clean-ish Bluray 0.0116. The grainy leg is the
proven `−34%` lever, so unblocking its detection is the right first step.

## Validation priority (each leg iso-bitrate-proven before it enters the router)

1. **grainy** `strength ∝ grain_sigma` scaling (ablate vs a flat preset) — proven lever, just the law.
2. **clean** NO-OP guard (default ties/beats the full chain on pristine clips — the avoided-loss).
3. **high-motion** denoise (the denoise half is proven on BBB; validate on real sports).
4. **textured** micro-noise strip (stand-in shows −19…−23% bits; confirm it survives the gate).
5. **anime** stack (dehalo/aa/deband ablations on real impaired anime).
6. **re-encode** demosquito **premise-check first** (edge-band temporal excess ≫ flat, and denoise leaves it untouched).
7. **dark/HDR** dark-masked denoise (rides the proven lever; the survival-dither leg is the ADR-0141 risk).

Every leg: tune on one clip, **confirm on a held-out clip**, iso-bitrate, VMAF-NEG vs clean —
so we find *real* tuned gains, not params overfit to one clip's metric.

## A parallel track — encoder-integration patches

Distinct from the pre-encode filter lane: patch FFmpeg's `nvenc`/`qsv`/`amf` wrappers to
expose/tune hardware features they underuse (temporal-AQ, deeper lookahead, weighted
prediction for the fade weakness, `b_ref_mode`, the native emphasis-level map our analyze
maps could drive; oneVPL per-block QP / low-power AQ for Intel). Tracked as a parallel
research+validate track — **check the current SDKs** (not memory) for what FFmpeg actually
fails to expose, and prove each patch iso-bitrate on real hardware (the NVENC ME-hints patch
was a zero-gain reminder that encoder patches can also be no-gains).

## Consequences

- This PR: `grain_estimate` emits `grain_sigma`/`grain_flat` (filter-only, no ABI/shader
  change, side-data path untouched). Foundation for the grainy route.
- Follow-ups (separate PRs, in priority order): the remaining detection enablers
  (`noise_sigma`, `dark_frac`, `edge_temporal_excess`), the per-leg validations, the
  `tune=auto` router filter/preset, and the encoder-patch track.
- Reinforces the project lane: reductive preprocessing tuned per content, never RC-competing,
  never additive-past-the-deadzone.

## References

- bench-results v0.2/v0.3 (denoise BD-rate), v0.16/ADR-0141 (additive wash + over-reach), the
  session campaign (NVENC<x265, content-dependence, tuning-recovers-false-negatives).
- ADR-0136 (analyze frame metadata — the av_dict_set pattern), ADR-0115 (grain_estimate),
  ADR-0112/0131/0137 (denoise + MC-warp + lookahead), ADR-0123/0124/0125 (anime tune),
  ADR-0127 (deblock), ADR-0133 (coarse banding), ADR-0135/0132 (RC-competing negatives).
- User direction: build a per-content tuner because the gains are per-content and mostly a
  matter of finding the right tuned boost for each content type; re-check apparent negatives
  for wrong-params false-negatives; and investigate encoder-side integration patches.
