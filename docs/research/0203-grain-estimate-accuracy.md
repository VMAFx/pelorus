<!-- markdownlint-disable MD013 MD060 -->
# Grain estimator accuracy against noise_model and grav1synth

**Date:** 2026-10-19

**Decision:** [ADR-0227](../adr/0227-av1-grain-calibration.md), issue [#203](https://github.com/VMAFx/pelorus/issues/203).

**Scope:** `vf_pelorus_grain_estimate_vulkan` with `model=aom` on FFmpeg `n9.0.2` plus shared series `v0.3.0` plus the Pelorus stack; libaom 3.15.2 (`aomenc`, `aomdec`, `examples/noise_model`); grav1synth 0.2.0 (`grav1synth diff`). Pelorus ran on an NVIDIA GeForce RTX 4090 (driver 615.78.08), FFmpeg's default Vulkan device on the test host.

Raw logs and the harness (local, not committed): `.workingdir/evidence/203/` (`grain203.py`, `truth.sh`, `est-*.sh`, `tput.sh`, `guard8k.sh`, `gatecheck.py`, `summary-calib.txt`).

## Verdict

Before this change the estimator's AV1 output was 73% to 80% too weak on every truth, and its AR coefficient went to the wrong tap. The scaling function was the residual RMS times 255 times `strength`, with no account of how much of the synthesized grain the 3×3 high-pass keeps (about 0.11 of it), and the lag-1 coefficient went into `ar_coeffs_y[0]`, which is the up-left tap in AV1's order (`generate_grain_y_c`, `libavcodec/aom_film_grain_template.c`).

With the calibrated mapping, white grain and ramp grain up to about 3.5 code values come back within 0.2%. The AR truth comes back 2.8% weak with lag-1 correlation 0.30 against 0.34. Both peers are off by 0.1% to 4.7% on the mean and up to 11.7% on single ramp stripes. Grain above about 3.5 code values comes back weak under the default edge gate: the top ramp stripe is 15% weak. With `edge=0.12` every stripe is within 0.2%. Pelorus runs at 97 fps at 1080p; the CPU peers run at 1.7 to 1.8 fps without their denoiser.

## Method

1. **Truths.** Five AV1 film grain tables with known parameters (`grain203.py table`): no grain; white grain with a flat scaling of 8 and of 24; a ramp from 8 at value 0 to 40 at 255; and a scaling of 24 with a lag-1 AR model of 20/64 on the up and left taps. All use `scaling_shift` 8 and `ar_coeff_shift` 6.
2. **Render.** `render.sh`: `aomenc --film-grain-table` at `--end-usage=q --cq-level=4` on 24 frames of 640×360 flat luma stripes at the eight Pelorus band centres, then `aomdec` with and without `--skip-film-grain`. This gives a grainy clip and its grain-free twin.
3. **Estimate.** Pelorus reads the grainy clip (`est-pel.sh`); its per-frame AV1 parameters from `showinfo` are averaged into one table. noise_model and `grav1synth diff` get the grainy clip and the grain-free twin as the denoised reference (`est-nm.sh`, `est-g1s.sh`).
4. **Score.** Each output table is rendered through step 2. The error is the rendered grain standard deviation per stripe (6-pixel margin) against the truth's, plus the horizontal and vertical lag-1 correlation of the rendered grain. The peers fit lag-3 models with other shifts, so parameters are compared through their rendered grain, not field by field.

## Results

Mean error over the eight stripes (ramp: mean absolute error, worst stripe):

| Truth | Truth σ | noise_model | grav1synth | Pelorus before | Pelorus | Pelorus `edge=0.12` |
| --- | --- | --- | --- | --- | --- | --- |
| white, 8 | 1.04 | +4.7% | +3.7% | −78.6% | 0.0% | 0.0% |
| white, 24 | 3.02 | +1.2% | +0.1% | −73.0% | 0.0% | 0.0% |
| ramp 8→40 | 1.28–4.75 | 3.3% (+11.7%) | 3.0% (+10.2%) | 76.7% (−82.7%) | 3.5% (−15.4%) | 0.1% (+0.2%) |
| 24, a = 20 | 3.42 | −1.3% | −1.4% | −80.0% | −2.8% | −1.5% |

Lag-1 correlation of the rendered AR grain, horizontal / vertical:

| Truth | noise_model | grav1synth | Pelorus before | Pelorus | Pelorus `edge=0.12` |
| --- | --- | --- | --- | --- | --- |
| 0.344 / 0.345 | 0.337 / 0.340 | 0.339 / 0.344 | 0.025 / 0.001 | 0.301 / 0.303 | 0.322 / 0.323 |

Parameters Pelorus emits: scaling 8 and 24 for the white truths (exact), ramp `[10, 14, 18, 22, 26, 29, 31, 32]` against the truth's `[10, 14, 18, 22, 26, 30, 34, 38]` at the band centres (exact with `edge=0.12`), and `a` = 18 for the AR truth's 20 (19 with `edge=0.12`).

**Negative case.** On the grain-free clip Pelorus attaches no AV1 film grain parameters (24 frames, only the `PEL_SEC_FILMGRAIN` interop SEI), and noise_model and grav1synth emit a zero scaling function. None of the three invents grain.

**Boundary.** `guard8k.sh`: an 8192×4320 frame passes; an 8193×4320 frame stops the graph at configuration with `8193x4320 (35393760 pixels) exceeds the DCI 8K limit of 35389440 pixels` and `EINVAL`. The limit is the size up to which `scripts/test-grain-accumulator-bounds.py` proves the uint32 accumulators.

**Throughput** (`tput.sh`, 48 frames of 1920×1080 white grain, wall time of the whole process including start-up):

| Estimator | Time | Rate |
| --- | --- | --- |
| Pelorus (`hwupload,pelorus_grain_estimate_vulkan,hwdownload`) | 0.49 s | 97 fps |
| `hwupload,hwdownload` alone | 0.38 s | 127 fps |
| noise_model (given the denoised reference) | 26.9 s | 1.8 fps |
| grav1synth diff (given the denoised reference, container start included) | 27.7 s | 1.7 fps |

## Why the calibration has the shape it has

`scripts/gen-av1-grain-calibration.py` ports FFmpeg's AV1 luma synthesis and measures it with the estimator's arithmetic with the edge gate open. The residual gain falls from 0.119 at `a` = 0 to 0.111 at `a` = 28, and the residual lag-1 correlation rises monotonically from −0.155 to +0.047, so one measured correlation picks one model.

The rendered AR truth reads slightly below its calibrated correlation through the GPU. `gatecheck.py` shows why: at a scaling of 24 the default gate lowers the residual correlation by 0.002 to 0.011 and the gain by under 1%, because it drops the pixels with the largest local range. At a scaling of 48 (about 6 code values) the flat fraction falls to about 0.3, the gain to 0.085 against 0.12 with the gate open, and the correlation is no longer usable. That is the heavy-grain limit in the docs; a gate that widens with the measured grain is a follow-up.

## Limits

- Flat synthetic content only. Real film grain on textured content is the promotion evidence for milestone 14, not this digest.
- Luma only: chroma grain is still derived from luma (`chroma_scaling_from_luma`).
- One horizontal correlation drives both AR taps; anisotropic or longer-range grain is fitted as the nearest symmetric lag-1 model.
