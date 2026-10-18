<!-- markdownlint-disable MD013 MD060 -->
# AV1 film grain descriptor against reference synthesis: round2 quantization

**Date:** 2026-10-18

**Decision:** [ADR-0217](../adr/0217-av1-grain-round-quantization.md), issue [#384](https://github.com/VMAFx/pelorus/issues/384).

**Scope:** FFmpeg `n9.0.2` reference synthesiser `ff_aom_apply_film_grain` (`libavcodec/aom_film_grain_template.c`), Fixture A of `ffmpeg-patches/test/grain_conformance.c` (flat 512x512 8-bit YUV420P, luma levels 64, 128 and 192, chroma 128, 16-pixel border excluded), libpelorus at master `18e3914`.

Raw logs (local, not committed): `.workingdir/evidence/384-aom/` (`sweep2.log`, `exp2.c`, `master-unplanted.log`).

## Verdict

The whole gap between `pel_grain_describe_aom()` and the measured AV1 synthesis is small-sigma integer quantization. The synthesiser adds `round2(scaling[idx] * grain, scaling_shift)` (`aom_film_grain_template.c:247`, `:379`), which rounds the noise to the nearest integer. The standard deviation of a rounded Gaussian, $R(s) = \operatorname{sd}(\operatorname{round}(X))$ with $X \sim \mathcal{N}(0, s^2)$, matches the 100-seed mean within 0.9% in all 15 cases. There is no linear chroma_1 bias: at scale factor 6 every mean error is at most 0.4%.

The single-seed figures in issue #384 and in the follow-up facts of ADR-0216 (a 4.0% to 5.6% chroma_1 bias, a 6% overprediction of the round model) came from seed 42 alone. The per-seed standard deviation of the error is 1.6% to 1.7% for luma and 2.7% to 3.7% for chroma, and seed 42 sits 9% below the chroma_1 mean.

## Method

1. Fixture A's point scaling values (luma and both chroma planes) were multiplied by $k \in \{1, 2, 6\}$, so that $\sigma$ ranges from 0.49 (quantization dominates) to 8.4 (quantization negligible).
2. For each case, 100 seeds `(42 + i * 7919) % 65536` were synthesised. AV1 seeds are 16-bit: `pel_grain_aom_validate()` rejects larger values.
3. Per seed: error = (measured $\sigma$ - descriptor) / descriptor, with the master linear descriptor. The table lists the mean and standard deviation of that error, the mean measured $\sigma$ and $R(\text{linear descriptor})$.
4. The in-tree 32-seed check `fixture_a_<case>_aom_sigma_seedmean_vs_desc` in `ffmpeg-patches/test/grain_conformance.c` reproduces the $k = 1$ rows: run `ffmpeg-patches/test/grain-conformance.sh` against a master build and against this change.

## Results

| $k$ | Case | Linear descriptor | Measured mean | Mean error | Error sd | Seed 42 error | $R(s)$ | Measured vs $R(s)$ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | luma 64 | 0.7689 | 0.8187 | +6.48% | 1.58% | +9.44% | 0.8213 | -0.31% |
| 1 | luma 128 | 1.3980 | 1.4256 | +1.98% | 1.72% | +3.15% | 1.4275 | -0.13% |
| 1 | luma 192 | 0.9436 | 0.9918 | +5.10% | 1.62% | +6.98% | 0.9868 | +0.50% |
| 1 | chroma 1 | 0.4860 | 0.5516 | +13.51% | 3.74% | +3.03% | 0.5564 | -0.87% |
| 1 | chroma 2 | 0.6480 | 0.7058 | +8.92% | 3.06% | +7.12% | 0.7091 | -0.46% |
| 2 | luma 64 | 1.5378 | 1.5612 | +1.53% | 1.74% | +2.72% | 1.5646 | -0.22% |
| 2 | luma 128 | 2.7959 | 2.8014 | +0.20% | 1.72% | +2.22% | 2.8108 | -0.33% |
| 2 | luma 192 | 1.9222 | 1.9403 | +0.94% | 1.73% | +2.55% | 1.9438 | -0.18% |
| 2 | chroma 1 | 0.9720 | 1.0055 | +3.45% | 2.68% | +0.17% | 1.0139 | -0.83% |
| 2 | chroma 2 | 1.2960 | 1.3264 | +2.35% | 3.07% | +2.51% | 1.3277 | -0.10% |
| 6 | luma 64 | 4.6483 | 4.6379 | -0.22% | 1.64% | +1.70% | 4.6572 | -0.42% |
| 6 | luma 128 | 8.3878 | 8.3635 | -0.29% | 1.68% | +1.60% | 8.3928 | -0.35% |
| 6 | luma 192 | 5.7666 | 5.7599 | -0.12% | 1.68% | +1.81% | 5.7739 | -0.24% |
| 6 | chroma 1 | 2.9159 | 2.9199 | +0.14% | 2.73% | -4.38% | 2.9302 | -0.35% |
| 6 | chroma 2 | 3.8879 | 3.9025 | +0.38% | 2.99% | -0.40% | 3.8986 | +0.10% |

In-tree 32-seed check, $k = 1$, absolute mean error against the public descriptor (bound 0.010):

| Case | Master (linear) | This change ($R(s)$) |
| --- | --- | --- |
| luma 64 | 0.06751 | 0.00059 |
| luma 128 | 0.02282 | 0.00169 |
| luma 192 | 0.05351 | 0.00743 |
| chroma 1 | 0.13614 | 0.00772 |
| chroma 2 | 0.09302 | 0.00113 |

## Open

- The conversions still read the linear descriptors, so the chroma $\sigma$ gap between the AV1 and H.274 synthesisers (18.4% on Fixture A, ADR-0211) is unchanged. A quantization-aware conversion is a separate follow-up.
