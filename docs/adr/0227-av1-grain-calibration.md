<!-- markdownlint-disable MD013 -->
# ADR-0227: The grain estimator maps to AV1 through a calibration against FFmpeg's AV1 synthesizer and refuses frames above DCI 8K

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: grain, av1, vulkan, ffmpeg, bench

## Context

`vf_pelorus_grain_estimate_vulkan` ([ADR-0115](0115-grain-estimate.md), [ADR-0161](0161-grain-estimate-rounding-and-h274-mapping.md)) measures grain as the residual of a 3×3 box high-pass over flat pixels: an RMS per luma band and one lag-1 correlation. For AV1 it wrote the scaling function as `band_rms · 255 · strength` with `strength` 2.0 by default, and the lag-1 coefficient times 64 into `ar_coeffs_y[0]`.

Issue [#203](https://github.com/VMAFx/pelorus/issues/203) asked for accuracy against libaom's `noise_model` and grav1synth. The bench ([research digest](../research/0203-grain-estimate-accuracy.md)) rendered known AV1 grain tables with `aomenc`, estimated them with all three tools, and rendered each estimate again. Three defects showed:

- **The scaling was 73% to 80% too weak.** The high-pass keeps about 0.11 of the synthesized grain's standard deviation, not 0.5 as the 2.0 default implied.
- **The AR coefficient went to the wrong tap.** In AV1's lag-1 order (`generate_grain_y_c`, `libavcodec/aom_film_grain_template.c`) index 0 is the (−1, −1) diagonal, so the rendered grain had a lag-1 correlation near 0 against a truth of 0.34. ADR-0161 had listed this as open.
- **The value written was the residual's correlation, not the grain's.** The high-pass turns white grain into a residual correlation of −0.155, so the old mapping wrote a negative coefficient for white grain.

ADR-0161 also left a guard for frames above the size its accumulator proof covers as a follow-up.

## Decision

1. **Calibrate against FFmpeg's own synthesizer.** `scripts/gen-av1-grain-calibration.py` ports FFmpeg's AV1 luma grain generation for a symmetric lag-1 model (`ar_coeffs_y[1] = ar_coeffs_y[3] = a`, `ar_coeff_shift` 6), runs the estimator's arithmetic on it with the edge gate open, and writes a table of (`a`, residual lag-1 correlation, residual gain) for `a` = 0 to 28 in steps of 4. The table lives in the filter as `pel_aom_calib`; `--check` fails when the source differs from a fresh run.
2. **Invert the table.** The host interpolates `a` from the measured residual correlation (monotonic from −0.155 to +0.047), takes the gain at that point, and writes `scaling = band_rms · 255 / gain · strength`, clamped to [0, 255].
3. **Write the up and left taps.** `ar_coeffs_y[1]` and `ar_coeffs_y[3]` carry `a`; every other tap is 0.
4. **`strength` defaults to 1.0**, meaning reproduce the measured grain. Values above 1 still exaggerate it.
5. **Refuse frames above DCI 8K.** `config_input` returns `AVERROR(EINVAL)` with a message naming the size when `w · h` exceeds 8192 × 4320 (`PEL_GRAIN_MAX_PIXELS`), the size up to which `scripts/test-grain-accumulator-bounds.py` proves the uint32 accumulators. That script reads the constant from the filter source, so the proof and the guard cannot drift apart.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Calibration table, symmetric lag-1, inverted on the residual correlation (this) | One measured value picks one model; exact against FFmpeg's synthesizer; table checked against a fresh run | One correlation for both taps; heavy grain still loses pixels to the edge gate | **Chosen** |
| Full Yule-Walker fit of a lag-3 AR model, as `noise_model` and grav1synth do | Anisotropic and longer-range grain | Needs the grain itself, which only a denoised reference gives; the estimator sees a high-passed residual and two moments, and the peers it would copy are no more accurate on these truths (−1.3% and −1.4% on the AR truth) | Rejected; a separate vertical tap is the next step (ENC-12) |
| Keep `strength` 2.0 as a heuristic and only move the tap | Smaller change | Every estimate stays 73% to 80% too weak; the default would need retuning for every content class | Rejected |
| Closed-form inversion from the AR model's theoretical variance and correlation | No table | Ignores the 3×3 high-pass, the band-limited Gaussian sequence, and FFmpeg's rounding and clipping; the H.274 equivalent was 6% off ([ADR-0161](0161-grain-estimate-rounding-and-h274-mapping.md)) | Rejected for a calibration against FFmpeg itself |
| Widen the edge gate with the measured grain level | Heavy grain within 0.2% (measured with `edge=0.12`) | Changes the flat-pixel selection for every filter that reads it and needs its own negative cases on textured content | Deferred to a follow-up issue |
| Clamp frames above DCI 8K to a sampled grid instead of refusing them | Works on any size | A second code path with its own accuracy question; no target encoder takes AV1 grain above 8K | Rejected |

## Consequences

- **Positive**: on synthetic AV1 grain the rendered estimate is exact for white grain, within 0.2% for a ramp up to about 3.5 code values, and 2.8% weak for AR grain with lag-1 correlation 0.30 against 0.34. `noise_model` and grav1synth are off by 0.1% to 4.7% on the mean and up to 11.7% on single stripes. A grain-free clip gets no AV1 grain parameters. Pelorus runs at 97 fps at 1080p against 1.7 to 1.8 fps for the peers, which also need a denoised reference.
- **Negative**: every AV1 estimate changes. The scaling at the default `strength` rises about 4.2 times (1 / (2 · 0.119)); a user who set `strength=2` explicitly now gets twice the measured grain instead of about a quarter of it. The default edge gate loses heavy grain: above about 3.5 code values the top ramp stripes come back 8.6% and 15.4% weak, and the AR estimate reads a lower correlation. A 8193 × 4320 or larger input now fails at configuration instead of risking accumulator overflow.
- **Neutral / follow-ups**: rerun `scripts/gen-av1-grain-calibration.py --check` after an FFmpeg bump that touches `libavcodec/aom_film_grain_template.c`. Open: an edge gate that widens with the measured grain, a separate vertical AR tap, chroma estimation, and a multi-interval H.274 mapping (ENC-12 in [0172](../research/0172-roadmap-inventory.md)). Promotion of the filter still needs BD-rate evidence on real film grain ([milestone 14](https://github.com/VMAFx/pelorus/milestone/14)).

## References

- [ADR-0115](0115-grain-estimate.md) (the estimator), [ADR-0161](0161-grain-estimate-rounding-and-h274-mapping.md) (fixed-point sums and the H.274 calibration this mirrors), [ADR-0147](0147-vulkan-sample-domain-and-components.md) (DCI 8K bound).
- FFmpeg n9.0.2 (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`): `libavcodec/aom_film_grain_template.c`, `libavutil/film_grain_params.h`.
- AV1 Bitstream & Decoding Process Specification, film grain synthesis process.
- libaom 3.15.2 `examples/noise_model.c`; grav1synth 0.2.0 `diff`.
- `scripts/gen-av1-grain-calibration.py`, `scripts/test-grain-accumulator-bounds.py`, [docs/metrics/grain_estimate.md](../metrics/grain_estimate.md), [research digest 0203](../research/0203-grain-estimate-accuracy.md).
- Source: issue [#203](https://github.com/VMAFx/pelorus/issues/203), milestone 14 order (paraphrased: measure the estimator against noise_model and grav1synth, with positive, negative and boundary cases).
