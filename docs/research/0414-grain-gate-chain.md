<!-- markdownlint-disable MD013 MD060 -->
# Grain estimator gate chain on heavy AR grain

**Date:** 2026-10-19

**Decision:** [ADR-0230](../adr/0230-grain-gate-chain.md), issue [#414](https://github.com/VMAFx/pelorus/issues/414).

**Scope:** `vf_pelorus_grain_estimate_vulkan` with `model=aom` on FFmpeg `n9.0.2` plus shared series `v0.3.0` plus the Pelorus stack, master `148a4c9` (the dual gate of [ADR-0229](../adr/0229-grain-dual-gate.md)) against this change; libaom 3.15.2 (`aomenc`, `aomdec`) for rendering. Pelorus ran on an NVIDIA GeForce RTX 4090 (driver 615.78.08), FFmpeg's default Vulkan device on the test host.

Raw logs and the harness (local, not committed): `.workingdir/evidence/414/` (`errors.txt`, `est-a1.txt`, `est-a0.txt`, `probe.txt`, `gatecal.txt`, `calchk.txt`, `truth-stats.txt`, `trunc-by-scale.txt`, `cpu-model-fmax.txt`, `tput.txt`, `replay414.log`).

## Verdict

The gate chain closes both cases #411 left open. Under the default settings AV1 grain at a scaling of 48 with the lag-1 AR model of 20/64 comes back −1.95% on the mean (was −12.50%), with `a` = 19 against 20; the ramp to a scaling of 64 comes back at a mean absolute error of 0.27% with every stripe within 2% (worst was −4.81%). White grain at 48 improves from −0.44% to +0.05%. Every lighter truth, `adapt=0` and six textured clips are unchanged, and throughput is within noise.

The AR 48 outer two stripes sit one scaling step low (47 against 48), −3.37% on the worst; the mean meets 2%.

## Method

1. **Truths.** The #411 set ([0411](0411-grain-dual-gate.md)): white grain at scalings 8, 24 and 48; the lag-1 AR model of 20/64 at scalings 24 and 48; ramps 8→40 and 8→64. Rendering, estimating and scoring follow [0203](0203-grain-estimate-accuracy.md) steps 2 to 4.
2. **Both arms and both builds.** The filter with `adapt=1` and `adapt=0` on this change, and `adapt=1` on master, all on the same truths.
3. **Calibration check.** `calchk.txt` compares the residual lag-1 and gain of FFmpeg's ported AV1 synthesizer with the libaom-rendered truths at the open gate: white 48 −0.1561 / 0.11917 (port) against −0.1475 / 0.11881 (libaom); AR 20 at 48 −0.0092 / 0.11104 against −0.0039 / 0.11045. `gatecal.txt` is the generated gated table.
4. **Gate behaviour.** `probe.txt`: `grain_wide_bands`, `grain_gate_steps`, `grain_flat` and `grain_lag1` per frame on the heavy truths in both arms. `cpu-model-fmax.txt`: the estimator's arithmetic on the CPU over every truth and textured clip at a binding threshold of 0.995 and 0.999, with the fraction of frames each band spent at each gate.
5. **Textured negative case.** 48 frames at 640×360 of six clips: Big Buck Bunny, glass half, blue sky, news mixed coding, Sintel trailer and SVT waterfall. The Netflix bar scene clip of the #411 set is left out until its licence permits local measurement.
6. **Throughput.** 48 frames of 1920×1080 white 24 grain, `hwupload,pelorus_grain_estimate_vulkan,hwdownload`, three alternating runs of each build.

## Results

Mean error over the eight stripes (ramps: mean absolute error; worst stripe in brackets):

| Truth | Truth σ | `adapt=0` | master `adapt=1` | this change `adapt=1` |
| --- | --- | --- | --- | --- |
| white, 8 | 1.04 | 0.00% | 0.00% | 0.00% |
| white, 24 | 3.02 | +0.02% | +0.02% | +0.02% |
| ramp 8→40 | 1.28–4.75 | 3.52% (−15.44%) | 0.14% (+0.23%) | 0.14% (+0.23%) |
| 24, a = 20 | 3.42 | −2.81% | −2.81% | −2.81% |
| white, 48 | 6.00 | −26.79% | −0.44% (−2.06%) | +0.05% (+0.05%) |
| 48, a = 20 | 6.77 | −33.20% | −12.50% | −1.95% (−3.37%) |
| ramp 8→64 | 1.53–7.42 | 14.30% (−37.47%) | 0.89% (−4.81%) | 0.27% (+1.99%) |

The `adapt=0` column is identical on master and this change.

Estimated parameters on the heavy truths, this change:

| Truth | Scaling per stripe | `a` | Rendered lag-1 (estimate / truth) |
| --- | --- | --- | --- |
| white, 48 | 48 on all eight | 1 | 0.022 / 0.005 |
| 48, a = 20 | 47, 48 × 6, 47 | 19 | 0.325 / 0.346 |
| ramp 8→64 | 12, 18, 25, 32, 39, 46, 54, 60 | 1 | 0.022 / 0.006 |

On master the AR 48 truth came back at a scaling of 46 with `a` = 11 and a rendered lag-1 of 0.177.

Gates taken (`grain_gate_steps`): 21845 (every band at 2 × `edge`) for white 48 and AR 48; 42304 for the ramp to 64 (bands 3 to 5 at 2 × `edge`, bands 6 and 7 at 4 × `edge`); 0 for white 8, white 24 and AR 24. `grain_flat` on the heavy truths rises from 0.29 to 0.64 (`adapt=0`) to 0.96 to 0.98.

**Textured negative case.** No clip had a band above the default gate in any of its 48 frames, and the mean `grain_sigma` is identical in both arms and on master:

| Clip | `grain_sigma` mean | Frames with a wide band |
| --- | --- | --- |
| Big Buck Bunny | 0.013104 | 0 |
| glass half | 0.006540 | 0 |
| blue sky | 0.015459 | 0 |
| news mixed coding | 0.011945 | 0 |
| Sintel trailer | 0.004921 | 0 |
| SVT waterfall | 0.012680 | 0 |

**Throughput** (wall time of the whole process, 48 frames):

| Build | Run 1 | Run 2 | Run 3 |
| --- | --- | --- | --- |
| master | 94.4 fps | 115.1 fps | 112.9 fps |
| this change | 114.6 fps | 115.8 fps | 116.2 fps |

Master's first run was slower, cause not isolated; runs 2 and 3 of both builds differ by less than 3%.

## Why the gated table is needed

A gate that binds drops the largest residuals of a flat field. For AR grain those excursions carry more of the correlation, so the gated lag-1 correlation and the gated gain depend on `a`, on the gate and on how hard the gate binds. At 2 × `edge` and `a` = 20 the gated correlation is 0.009 below the open one at a scaling of 48 and 0.041 below at 64 (port of FFmpeg's synthesizer). The open-gate table moves about 0.03 per step of 4 in `a`, so reading a gated band through it picks too low an `a` and too low a gain. The gated table holds those values per gate, keyed by the gated RMS over the gate, and the host inverts the pooled prediction across all bands at once.

## Limits

- H.274: the same per-band gates feed the H.274 mapping, whose table is calibrated with the gate open; no heavy H.274 truth was measured.
- Grain above about 14 code values binds at 4 × `edge` as well and reads weak.
- At the heaviest rows of the 4 × `edge` block the gated correlation is not monotone in `a`; the inversion clamps to the table.
