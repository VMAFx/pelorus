<!-- markdownlint-disable MD013 -->
# ADR-0230: The grain estimator extends the dual gate to a chain of three gates and maps gated bands to AV1 through a gated calibration

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: grain, av1, vulkan, ffmpeg, bench

## Context

[ADR-0229](0229-grain-dual-gate.md) measures every band of `vf_pelorus_grain_estimate_vulkan` at `edge` and at 2 × `edge` and takes the wide statistics where the band behaves like white grain. It left two heavy-grain cases outside 2%, tracked in issue [#414](https://github.com/VMAFx/pelorus/issues/414):

- AV1 grain at a scaling of 48 with the lag-1 AR model of 20/64 read 12.5% weak. The lag-1 product was still accumulated at the default gate only, so the measured residual correlation fell (0.177 against 0.346 rendered), the AV1 calibration ([ADR-0227](0227-av1-grain-calibration.md)) picked `a` = 11 instead of 20, and the residual gain of that weaker model scaled the band low.
- The top stripe of a ramp to a scaling of 64 (7.4 code values) read 4.8% weak, because 2 × `edge` clips there as `edge` does at 3.5.

Moving the lag-1 product to the wide gate alone does not close the AR case. The wide gate still drops the largest excursions of heavy grain, and that changes the residual lag-1 correlation by an amount that grows with `a` and with the grain level: at 2 × `edge` and `a` = 20 the gated correlation sits 0.009 below the open one at a scaling of 48 and 0.041 below at 64, and at `a` = 28 and 64 it is 0.051 below. On the open-gate table one step of 4 in `a` moves the correlation by about 0.03, so these shifts read as one or more steps of `a`. The open-gate table therefore no longer describes what a gated band measures.

## Decision

1. **A chain of three gates.** The shader accumulates the gated sums and the lag-1 product per band and slice at 1, 2 and 4 × `edge`. A band climbs from gate g to the next while gate g binds (kept fraction below 0.95), the band holds at least 1024 pixels within gate g, and the gated RMS over gate g reaches 0.875 of the white grain curve for gate g. `scripts/gen-grain-gate-curve.py` writes one curve per gate. The threshold and the 1024-pixel floor are the ADR-0229 values.
2. **Lag-1 at the chosen gate.** The pooled lag-1 correlation sums each band's product and pair count at the gate that band took.
3. **Gated AV1 calibration.** `scripts/gen-av1-grain-calibration.py` measures each AR model of the open table (`a` = 0 to 28 in steps of 4) through gates 2 and 4 × `edge` at seven grain levels, with FFmpeg's synthesizer ported as before, and writes `pel_aom_gate_calib`: rows of gate, `a`, the gated RMS over the gate `q`, the gated residual lag-1 and the gated residual gain, sorted by `q` within each (gate, `a`) block. The rows are keyed by `q`, not by the kept fraction, because the kept fraction saturates at 1 while the residual clamp still lowers the RMS, so it repeats across rows. `--check` covers both tables.
4. **Inversion over the pooled prediction.** When any band took a wider gate, the host predicts for each `a` the pooled lag-1 correlation the measurement would show: each band's correlation from the open table at the default gate or from the gated table at its gate and `q`, weighted by its pair count and variance as the measurement pools them. `a` is interpolated where the prediction meets the measured lag-1, and each band's gain is interpolated at that `a` from the same rows. With no band above the default gate the open-gate path runs unchanged, so its output is identical to before.
5. **Report.** Frame metadata `lavfi.pelorus.grain_gate_steps` holds two bits per band with the gate taken (0, 1 or 2); `grain_wide_bands` keeps its meaning, a band above the default gate.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Gate chain, lag-1 per gate, gated AV1 table, pooled inversion (this) | AR 48 within 2% on the mean; ramp to 64 within 2% per stripe; textured clips and every lighter truth unchanged | Two more gates of sums in shared memory; a second calibration table; H.274 has no gated table | **Chosen** |
| Lag-1 at the wide gate, open-gate calibration unchanged | Small change | The gated correlation shifts with `a` and grain level as described above, so the open table reads heavy AR grain at too low an `a` and gain | Rejected |
| Gated table keyed by kept fraction | Same key as the gate curve | The kept fraction reaches 1 while the RMS still falls, so keys repeat and the interpolation is undefined | Rejected |
| A fourth gate at 8 × `edge` | Would cover grain above 14 code values | Grain that heavy is outside the measured truths; more texture reaches the widest gate | Deferred |
| Fit the grain distribution from a gated histogram | Would cover any grain level without gates | A histogram per band and slice in shared memory and a fit on the host per frame | Deferred |

## Consequences

- **Positive**: under the default settings AR grain at scaling 48 comes back −1.95% on the mean (worst stripe −3.37%, was −12.50%) with `a` = 19 against the truth's 20 and a rendered lag-1 of 0.325 against 0.346 (was 0.177). The ramp to 64 comes back at a mean absolute error of 0.27% (worst +1.99%, was −4.81%), and white grain at 48 at +0.05% on every stripe (was −0.44%, worst −2.06%). White 8, white 24, AR 24 and the ramp to 40 are unchanged, `adapt=0` is unchanged, and on six textured clips no band leaves the default gate. Throughput at 1080p on an RTX 4090 is within run-to-run noise ([research digest](../research/0414-grain-gate-chain.md)).
- **Negative**: the H.274 mapping reads the gated RMS and lag-1 through an open-gate table, and no heavy H.274 truth was measured; the docs flag H.274 values above about 3.5 code values as unverified. At high `q` the gated correlation at 4 × `edge` is not monotone in `a`; the inversion clamps to the table; this affects only the heaviest rows of the 4 × `edge` block, beyond the measured truths. A band that mixes grain and texture still keeps the default gate. The gate curves and the gated table are generated at the default `edge` (0.06) with a residual clamp that does not scale with it, so at a non-default `edge` the chain runs outside its calibration; the docs say so, and a guard or an `edge` sweep is a follow-up. The AR 48 outer stripes sit one scaling step low (47 against 48).
- **Neutral / follow-ups**: rerun both generators with `--check` after an FFmpeg bump or when the high-pass, the residual clamp or the default `edge` changes. `scripts/test-grain-accumulator-bounds.py` covers the added accumulators against the DCI 8K bound. The Netflix bar scene clip of the ADR-0229 textured set is left out of this measurement until its licence permits local measurement. Promotion still needs BD-rate evidence on real film grain ([milestone 14](https://github.com/VMAFx/pelorus/milestone/14)).

## References

- [ADR-0229](0229-grain-dual-gate.md) (dual gate this extends), [ADR-0227](0227-av1-grain-calibration.md) (AV1 calibration), [ADR-0115](0115-grain-estimate.md) (the estimator).
- `scripts/gen-av1-grain-calibration.py`, `scripts/gen-grain-gate-curve.py`, `scripts/test-grain-accumulator-bounds.py`, [docs/metrics/grain_estimate.md](../metrics/grain_estimate.md), [research digest 0414](../research/0414-grain-gate-chain.md).
- Source: issue [#414](https://github.com/VMAFx/pelorus/issues/414), milestone 14 order (paraphrased: bring heavy AR grain within the #411 bounds after the dual gate, in one pull request).
