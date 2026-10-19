<!-- markdownlint-disable MD013 MD060 -->
# Grain estimator dual edge gate on heavy grain and textured content

**Date:** 2026-10-19

**Decision:** [ADR-0229](../adr/0229-grain-dual-gate.md), issue [#411](https://github.com/VMAFx/pelorus/issues/411).

**Scope:** `vf_pelorus_grain_estimate_vulkan` with `model=aom` on FFmpeg `n9.0.2` plus shared series `v0.3.0` plus the Pelorus stack, master `2ccfe0f` against this change; libaom 3.15.2 (`aomenc`, `aomdec`) for rendering. Pelorus ran on an NVIDIA GeForce RTX 4090 (driver 615.78.08), FFmpeg's default Vulkan device on the test host.

Raw logs and the harness (local, not committed): `.workingdir/evidence/411/` (`truths.sh`, `est411.sh`, `cmp.py`, `wide.sh`, `tput.sh`, `model.py`, `rund.sh`, `curve.py`, `runc.sh`, `ratio.py`, and their outputs).

## Verdict

The dual gate meets the #411 acceptance on white heavy grain and on the ramps, keeps the #203 truths unchanged, and leaves seven textured clips untouched. Under the default settings white grain at a scaling of 48 (6 code values) comes back −0.44% on the mean, against −26.8% with a single gate; the ramp 8→40 is within 0.23% on every stripe, against −15.4% on the worst.

Two cases still miss 2% and move to [#414](https://github.com/VMAFx/pelorus/issues/414): AR grain at a scaling of 48 reads 12.5% weak, because its lag-1 correlation is still measured at the default gate, and the top stripe of a ramp to 64 (7.4 code values) reads 4.8% weak, because even the wide gate clips there. The white 48 truth's top two stripes sit one scaling step low (47 against 48), which is −2.06% and −2.0%, just outside a 2% stripe bound.

## Method

1. **Truths.** The #203 harness ([0203](0203-grain-estimate-accuracy.md)) with three heavy tables added: white grain at a flat scaling of 48; scaling 48 with the lag-1 AR model of 20/64 on the up and left taps; and a ramp from 8 at value 0 to 64 at 255. Rendering, estimating and scoring follow 0203 steps 2 to 4 (`aomenc` on 24 frames of 640×360 stripes, rendered grain standard deviation per stripe against the truth's).
2. **Both arms.** `est411.sh` runs the filter with `adapt=1` (the default) and `adapt=0`, which reproduces the single gate of master.
3. **Textured negative case.** 48 frames at 640×360 of seven clips from the #135 corpus: Big Buck Bunny, Netflix bar scene, glass half, blue sky, news mixed coding, Sintel trailer and SVT waterfall. Recorded per clip: the mean `grain_sigma` and the frames with any `grain_wide_bands` bit set.
4. **Threshold.** `model.py` and `rund.sh` run the estimator's arithmetic on the CPU over every clip at ratio thresholds of 0.7, 0.875 and 0.97; `curve.py`, `runc.sh` and `ratio.py` print each band's kept fraction, its gated RMS over `edge`, and that RMS over the white grain curve at the same fraction.
5. **Throughput.** `tput.sh`: 48 frames of 1920×1080 white 24 grain, `hwupload,pelorus_grain_estimate_vulkan,hwdownload`, three runs of each build.

## Results

Mean error over the eight stripes (ramps: mean absolute error, worst stripe):

| Truth | Truth σ | `adapt=0` (master) | `adapt=1` |
| --- | --- | --- | --- |
| white, 8 | 1.04 | 0.00% | 0.00% |
| white, 24 | 3.02 | +0.02% | +0.02% |
| ramp 8→40 | 1.28–4.75 | 3.52% (−15.44%) | 0.14% (+0.23%) |
| 24, a = 20 | 3.42 | −2.81% | −2.81% |
| white, 48 | 6.00 | −26.79% | −0.44% (−2.06%) |
| 48, a = 20 | 6.77 | −33.20% | −12.50% |
| ramp 8→64 | 1.53–7.42 | 14.30% (−37.47%) | 0.89% (−4.81%) |

Lag-1 correlation of the rendered AR grain (horizontal) against the truth: 0.301 against 0.344 at scaling 24 in both arms; 0.177 against 0.346 at scaling 48 in both arms.

Bands that took the wide statistics (`grain_wide_bands`): 255 for white 48 and AR 48, 248 for the ramp to 64, 224 for the ramp to 40, 0 for the white 8, white 24 and AR 24 truths.

**Textured negative case.** No clip set a `grain_wide_bands` bit in any of its 48 frames, and the mean `grain_sigma` is identical in both arms:

| Clip | `grain_sigma` mean | Frames with a wide band |
| --- | --- | --- |
| Big Buck Bunny | 0.013104 | 0 |
| Netflix bar scene | 0.012347 | 0 |
| glass half | 0.006540 | 0 |
| blue sky | 0.015459 | 0 |
| news mixed coding | 0.011945 | 0 |
| Sintel trailer | 0.004921 | 0 |
| SVT waterfall | 0.012680 | 0 |

**Threshold.** Gated RMS over the white grain curve, for bands whose default gate binds (kept fraction below 0.95):

| Clip | Lowest | Highest |
| --- | --- | --- |
| ramp 8→40 | 0.986 | 0.999 |
| white, 48 | 0.991 | 0.998 |
| 48, a = 20 | 0.958 | 0.960 |
| ramp 8→64 | 0.994 | 1.000 |
| Big Buck Bunny | 0.602 | 0.768 |
| Netflix bar scene | 0.329 | 0.716 |
| glass half | 0.128 | 0.439 |
| blue sky | 0.157 | 0.641 |
| news mixed coding | 0.464 | 0.790 |
| Sintel trailer | 0.168 | 0.252 |
| SVT waterfall | 0.521 | 0.756 |

The white 8, white 24 and AR 24 truths have no band below a kept fraction of 0.95. 0.875 sits between texture's highest 0.79 and grain's lowest 0.96. In the CPU model a threshold of 0.7 selects bands of Big Buck Bunny, Netflix bar scene, news mixed coding and SVT waterfall; 0.97 selects the AR 48 bands in only 25% to 46% of frames, which costs 6% to 12% on that truth.

**Throughput** (wall time of the whole process, 48 frames):

| Build | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| master | 105.8 fps | 106.3 fps | 108.9 fps |
| this change | 104.8 fps | 107.0 fps | 106.4 fps |

The difference is inside run-to-run noise.

## Why the AR and top-stripe cases stay weak

The lag-1 product is accumulated at the default gate only. On heavy AR grain the default gate drops the correlated excursions, so the measured residual correlation falls and the AV1 calibration ([ADR-0227](../adr/0227-av1-grain-calibration.md)) picks a lower AR coefficient and a lower residual gain; the scaling then comes back low even though the band RMS is right. At 7.4 code values the wide gate (2 × `edge`, 0.12) starts to clip as the default gate does at 3.5. [#414](https://github.com/VMAFx/pelorus/issues/414) tracks a wide-gate lag-1 and a further gate step.
