<!-- markdownlint-disable MD013 -->
# ADR-0229: The grain estimator measures every band at two edge gates and takes the wide one only where the band behaves like grain

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: grain, av1, vulkan, ffmpeg, bench

## Context

`vf_pelorus_grain_estimate_vulkan` ([ADR-0115](0115-grain-estimate.md), [ADR-0227](0227-av1-grain-calibration.md)) keeps a pixel as flat when its 3×3 box high-pass residual stays below the edge gate `edge` (0.06 by default). The gate removes texture, but it also removes the tails of heavy grain: once the grain's standard deviation passes about 3.5 code values, the large excursions of a flat field fail the gate and the band RMS reads weak.

Issue [#411](https://github.com/VMAFx/pelorus/issues/411) measured this after #203: the ramp truth's top two stripes came back 8.6% and 15.4% weak, white grain at a scaling of 48 lost 26.8%, and `edge=0.12` fixed the ramp at the cost of letting more texture in on detailed content. The acceptance asked for heavy synthetic grain within 2% under the default settings, no regression on the #203 truths, and a textured case where the estimate does not rise.

A fixed wider gate fails the textured case. The question is which bands may use a wider gate.

## Decision

1. **Measure every band twice.** The shader accumulates, per band and slice, the gated sums at the default gate (`sumsq`, `cnt`), the same sums at a wide gate of 2 × `edge` (`sumsq_w`, `cnt_w`), and the count of all band pixels (`total`). The lag-1 product stays at the default gate.
2. **Recognise grain by its gate curve.** For white Gaussian grain the kept fraction `f = cnt / total` and the gated RMS over `edge`, `q`, both depend only on σ / `edge`, so they lie on one curve. `scripts/gen-grain-gate-curve.py` renders that curve with the estimator's arithmetic and writes it into the filter as `pel_gate_curve` (16 rows); `--check` fails when the source differs from a fresh run. Texture fails the gate by structure rather than by the tail of a distribution, so it lies well below the curve: 0.79 of it or less on seven textured clips, against 0.96 or more for the heavy grain truths ([research digest](../research/0411-grain-dual-gate.md)).
3. **Take the wide statistics only on grain-shaped bands.** A band takes `sumsq_w` and `cnt_w` when the default gate binds (`f` < 0.95), the band holds at least 1024 gated pixels, and `q` ≥ 0.875 × curve(`f`). Every other band keeps the default statistics. The AV1 and H.274 mappings read the band RMS as before.
4. **Report and switch.** Frame metadata `lavfi.pelorus.grain_wide_bands` is a bitmask of the bands that took the wide statistics. The AVOption `adapt` (bool, default 1) turns the rule off; `adapt=0` reproduces the single-gate estimate.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Two gates, wide one taken where (f, q) lies on the white grain curve (this) | Heavy grain within 2% on the mean; textured clips unchanged; one extra pair of sums per band | Lag-1 still at the default gate; a band that mixes grain and texture falls back to the default gate | **Chosen** |
| Raise the default `edge` to 0.12 | No new code | Lets texture into every estimate on detailed content; the textured negative case fails | Rejected |
| Gate tied to the previous frame's residual RMS, as #411 proposed | Follows the grain level | A feedback loop across frames: texture raises the RMS, which widens the gate, which admits more texture; needs a scene-cut reset | Rejected for a test inside the frame |
| Ratio threshold 0.7 | Selects heavy grain earlier | bbb, netflix-bar, news-mixedcoding and svt-waterfall bands select in the CPU model | Rejected |
| Ratio threshold 0.97 | Wider margin to texture | AR grain at scaling 48 selects in only 25% to 46% of frames | Rejected |
| Fit the grain distribution from the gated histogram | Would also cover grain beyond the wide gate | A histogram per band and slice in shared memory, and a fit on the host per frame | Deferred; [#414](https://github.com/VMAFx/pelorus/issues/414) tracks the remaining cases |

## Consequences

- **Positive**: under the default settings white grain at scaling 48 comes back −0.44% on the mean (was −26.8%), the ramp truth 8→40 within 0.23% per stripe (worst was −15.4%), and the ramp to scaling 64 at a mean absolute error of 0.89% (was 14.3%). The white 8, white 24 and AR truths of #203 are unchanged. On seven textured clips no band takes the wide statistics and `grain_sigma` is identical with `adapt` on and off. Throughput at 1080p on an RTX 4090 is within run-to-run noise (104.8 to 107.0 fps against 105.8 to 108.9 fps on master).
- **Negative**: the white 48 truth's top two stripes sit one scaling step low (47 against 48, −2.06% and −2.0%). Heavy AR grain stays 12.5% weak because the lag-1 product is still measured at the default gate (0.177 against 0.346), and the top stripe of the scaling 64 ramp reads 4.8% weak because even the wide gate clips at 7.4 code values; [#414](https://github.com/VMAFx/pelorus/issues/414) tracks both. `grain_flat` now counts the wide-gate pixels of wide bands, so it rises on heavy grain.
- **Neutral / follow-ups**: rerun `scripts/gen-grain-gate-curve.py --check` when the high-pass, the residual clamp or the default `edge` changes. `scripts/test-grain-accumulator-bounds.py` proves the new accumulators against the DCI 8K bound and fails when the shader's wide gate drifts from the host's. Promotion still needs BD-rate evidence on real film grain ([milestone 14](https://github.com/VMAFx/pelorus/milestone/14)).

## References

- [ADR-0115](0115-grain-estimate.md) (the estimator), [ADR-0227](0227-av1-grain-calibration.md) (AV1 calibration this builds on), [ADR-0161](0161-grain-estimate-rounding-and-h274-mapping.md) (fixed-point sums).
- `scripts/gen-grain-gate-curve.py`, `scripts/test-grain-accumulator-bounds.py`, [docs/metrics/grain_estimate.md](../metrics/grain_estimate.md), [research digest 0411](../research/0411-grain-dual-gate.md).
- Source: issue [#411](https://github.com/VMAFx/pelorus/issues/411), milestone 14 order (paraphrased: bring heavy grain within 2% under the default settings without raising the estimate on textured content).
