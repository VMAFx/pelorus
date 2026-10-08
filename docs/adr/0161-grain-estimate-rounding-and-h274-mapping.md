<!-- markdownlint-disable MD013 -->
# ADR-0161: The grain estimator rounds its fixed-point sums and maps to H.274 model 0 through a calibration against FFmpeg's synthesizer

- **Status**: Accepted
- **Date**: 2026-10-03
- **Deciders**: Lusoris
- **Tags**: grain, fgs, h274, av1, vulkan, ffmpeg

## Context

`vf_pelorus_grain_estimate_vulkan` ([ADR-0115](0115-grain-estimate.md))
measures grain as the residual of a 3×3 box high-pass over flat pixels. It
reduces two quantities with uint32 atomics: the squared residual per luma band,
and the lag-1 product `resid * resid_right`. An August 2026 bug hunt reported
two defects against it. Both were checked numerically and on three GPUs.

**The lag-1 reduction could not resolve real grain (BUG-010).** The shader
stored `uint((prod + 1.0) * 2000)`. The product of two residuals of
one-code-value grain is about 1e-5 in normalized units, while one fixed-point
unit was 5e-4, and the truncating `uint()` subtracted half a unit from every
add on average. The bias was 15 to 110 times the signal. The host clips the
coefficient to [−1, 1], so every frame reported −1.0 and the AV1 parameters
carried `ar_coeffs_y[0] = −64`, whatever the input. The squared-residual sum
used a fine enough scale (3e5) but also truncated, which made the RMS 5.3% low
at a grain standard deviation of one 8-bit code value and 1.5% low at two
codes. A float64 simulation of the shader path
(`scripts/test-grain-accumulator-bounds.py`) and GPU runs on an Arc A380
agree on these figures.

**The H.274 values could not be synthesized (BUG-029).** `map_aom()`
hard-coded `h274_model_id = 1` and `h274_log2_scale = 8` in
`PEL_SEC_FILMGRAIN`, and the filter emitted no H.274 model values. FFmpeg's
only H.274 synthesizer (`libavcodec/h274.c` at n9.0.2) implements the SMPTE
RDD 5 profile: model 0, `log2_scale_factor` 2 to 7, cutoffs 2 to 14, 8-bit
4:2:0 (`ff_h274_film_grain_params_supported()` in `libavcodec/h274.h`). A
stream built from the old scalars decodes with `Unsupported film grain
parameters. Ignoring film grain.` and a grain standard deviation of 0.

Model 0 is not described by a standard deviation alone. The cutoff frequency
sets how much of a 64×64 DCT block carries noise, so it sets the grain size,
and the 3×3 high-pass sees a different fraction of the grain at each cutoff.
At cutoff 8, the residual holds 58% of the grain's standard deviation, while
for white grain it holds 94%. A mapping that ignores the cutoff therefore
misses by tens of percent.

## Decision

We will round every per-pixel fixed-point add to the nearest unit in both
accumulators, and size the lag-1 accumulator to its real range. The bias
becomes `RES_CLAMP²` = 0.0064 (the most negative product of clamped
residuals), and the scale becomes 150000. Each add is then at most 1921 units,
the same as the squared-residual add. The fast-suite test proves that both
sums fit uint32 at DCI 8K. It also checks the rounded path against a float
reference on one-code-value grain, within 2% for the RMS and 0.02 for the
lag-1 coefficient.

We will target the RDD 5 profile for H.274. `PEL_SEC_FILMGRAIN` carries
`h274_model_id = 0`, `h274_blending_mode = 0` and `h274_log2_scale = 2`. These
are also the `pelorus_fgs` defaults once its RDD 5 profile change (ADR-0155, in
review) lands; this base still defaults to 1 and 8. With `model=h274`, the
filter also emits
`lavfi.pelorus.h274_model_id`, `h274_log2_scale`, `h274_scale_y`,
`h274_cutoff_h` and `h274_cutoff_v`. Each key is named after the `pelorus_fgs`
option it feeds.

The cutoff is the one, from 6 to 14, whose calibrated residual lag-1
correlation is nearest the measured one. The scale is the pooled residual RMS
in 8-bit code values divided by that cutoff's calibrated residual gain, rounded
and clipped to 0..255. The calibration table in
`ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c` comes from
`scripts/gen-h274-grain-calibration.py`. The script ports FFmpeg's model-0 luma
synthesis bit for bit, reads the grain tables from `libavcodec/h274.c` at the
pinned commit, and measures the result with the estimator's arithmetic. The
scale is in 8-bit code values at every bit depth: RDD 5 limits the scale to 8
bits, and the HM/VTM variants shift the grain left by `bitdepth − 8` before
blending (ITU-T H.Sup21 (01/2025), 7.3.1.1 and 7.3.2.1). The `strength` option
keeps its meaning for the AV1 scaling function only, so the H.274 values
reproduce the measured grain at unit gain.

The filter also emits `lavfi.pelorus.grain_lag1`, the measured residual lag-1
coefficient, for every model.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Round, bias = `RES_CLAMP²`, lag-1 scale 1.5e5 (this) | Unbiased sums; lag-1 within 0.004 of float on three GPUs; overflow ceiling about 71 million pixels, up from 68 million | Still a fixed-point approximation | **Chosen** |
| Use the full uint32 headroom (squared scale 6e5, lag-1 scale 3e5) | Finer units | 1.1% margin at DCI 8K; overflow from about 36 million pixels instead of 71 million, unguarded; no measurable gain once adds are rounded | Rejected |
| 64-bit atomics (`GL_EXT_shader_atomic_int64`) | No overflow analysis | A device feature not guaranteed on every target driver | Rejected |
| Per-workgroup shared-memory float reduction, one atomic per workgroup | Fewer atomics, one rounding per workgroup | Restructures a shader that returns early before any barrier; more change than the precision target needs | Rejected |
| Signed integer atomics instead of a bias | No bias bookkeeping | Same range as the bias; changes the SSBO type for no gain | Rejected |
| H.274: fixed cutoff 8 (the BSF default), scale from the RMS | Fewer outputs | White grain decodes 60% too strong, because cutoff-8 grain keeps much less of its energy in the 3×3 residual | Rejected |
| H.274: closed form `σ = scale·√((h+1)(v+1))/16/2^log2_scale` | No table | Ignores FFmpeg's fixed-point patterns and deblocking (about 6% weaker at cutoff 8, [grain-fgs-bsf.md](../usage/grain-fgs-bsf.md)) and the estimator's high-pass gain | Rejected for a calibration against FFmpeg itself |
| Append H.274 model values to `PEL_SEC_FILMGRAIN` | Machine-readable per frame for vmafx | ABI minor bump, conformance fixture and consumer changes; no consumer needs it yet | Deferred |
| Attach `AV_FRAME_DATA_FILM_GRAIN_PARAMS` of type H.274 | Native FFmpeg channel | No FFmpeg n9.0.2 encoder reads it; only decoders, `showinfo` and `libplacebo` use the type | Deferred |
| Choose `log2_scale` per frame to keep the scale large | Finer steps for faint grain (scale 5 at one code value moves 20% per step) | Departs from the BSF default and changes per frame | Rejected for now |

## Consequences

- **Positive**: the estimate now matches its float reference. On an Arc A380,
  an RTX 4090 and a RADV iGPU, the results are identical: the RMS is within 1%
  of the reference, and the lag-1 coefficient within 0.004 (it was pinned at
  −1.0). Through `pelorus_fgs` with explicit cutoffs and FFmpeg's HEVC
  decoder, white grain of one to three code values comes back within +1% to
  +4% of its source standard deviation. RDD 5 grain at cutoff 8 comes back
  with its exact parameters (scale 16, cutoff 8; −0.1%).
- **Negative**: every AV1 estimate changes, because `ar_coeffs_y[0]` is no
  longer a constant −64 (white grain now gives about −11). `grain_sigma` rises
  slightly for light grain, by up to 6% at one code value, which moves values
  near the `tune=auto` clean threshold (0.004). Grain with more low-frequency
  energy than a band-limited RDD 5 pattern of the same residual correlation
  comes back weaker: −9% for a separable AR(1) source with ρ = 0.5. Heavy grain
  near the edge gate also comes back weaker: −8.5% at 4.4 code values. The
  cutoffs reach FFmpeg only if `pelorus_fgs` writes all three model values. The
  BSF on this base writes only the scale, so FFmpeg uses its coarsest pattern,
  and the measured result is 52% to 79% too weak. The ADR-0155 work on
  `pelorus_fgs` adds the `cutoff_h`/`cutoff_v` options.
- **Neutral / follow-ups**: `grain-accumulator-bounds` gains `--self-test`,
  which plants nine defects, among them the pre-fix arithmetic, and requires
  each to be rejected. Rerun `scripts/gen-h274-grain-calibration.py --check`
  after an FFmpeg bump that touches `libavcodec/h274.c`. Open items found along
  the way: `ar_coeffs_y[0]` is the (−1, −1) diagonal tap in AV1's lag-1 order
  (`libavcodec/aom_film_grain_template.c`), not the left neighbour, and it
  receives the residual-domain coefficient, not the grain's own correlation.
  Two further follow-ups are a multi-interval H.274 mapping from the eight
  bands and a guard for frames above the proven size.

## References

- [ADR-0115](0115-grain-estimate.md) (the estimator),
  [ADR-0147](0147-vulkan-sample-domain-and-components.md) (DCI 8K bound),
  [ADR-0117](0117-grain-fgs-bsf.md) (the `pelorus_fgs` BSF).
- FFmpeg n9.0.2 (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`):
  `libavcodec/h274.c`, `libavcodec/h274.h`, `libavcodec/h2645_sei.c`,
  `libavcodec/aom_film_grain_template.c`.
- ITU-T H.Sup21 (01/2025), *Film grain synthesis technology for video
  applications*, 7.3.1.1 (RDD 5 restrictions) and 7.3.2.1 (HM/VTM bit-depth
  handling); SMPTE RDD 5-2006.
- `scripts/test-grain-accumulator-bounds.py`,
  `scripts/gen-h274-grain-calibration.py`,
  [docs/metrics/grain_estimate.md](../metrics/grain_estimate.md).
- Source: `req`, bug-wave brief. BUG-010: "lag-1 correlation accumulated as
  uint with CORR_GS=2000 and CORR_BIAS=1.0 and truncating uint(); claim: the
  product range spans only ~25 codes, so the AR coefficient estimate is
  coarse/biased." BUG-029: "emits h274_model_id=1 and h274_log2_scale=8.
  FFmpeg's H.274 synthesiser supports only model 0 and RDD 5 (log2_scale
  2..7) [...] Make the estimator emit model 0 / a scale in 2..7 consistent with
  the fgs BSF defaults, mapping its measured sigma to H.274 model-0 parameters
  correctly; document."
