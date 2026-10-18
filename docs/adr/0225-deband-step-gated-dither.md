<!-- markdownlint-disable MD013 MD060 -->
# ADR-0225: Deband dither only on code steps, taps kept inside the plane, bench pin on ordered dither

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: deband, vulkan, shader, bench, cambi, nvenc
- **Related**: [ADR-0102](0102-flagship-smart-deband.md), [ADR-0111](0111-benchmark-methodology.md), [ADR-0141](0141-deband-wide-band-reach.md)

## Context

Issue [#283](https://github.com/VMAFx/pelorus/issues/283): on the pinned bench clip `synth-banding`, the pinned deband settings (`range=15:thry=0.012:dither=bluenoise:dynamic=1:protect=1`) raise CAMBI after `hevc_nvenc` and `av1_nvenc` instead of lowering it. Bench v0.20 ([#300](https://github.com/VMAFx/pelorus/issues/300)) showed the same on the 10-bit pipeline with the filter defaults. Without an encoder the filter removes the banding (CAMBI 2.13 to 0.00 on the 8-bit clip), so the loss happens in the encode.

The measurements in [research 0283](../research/0283-deband-step-gated-dither.md) name three causes:

1. At the pinned ladder (CQ 28 to 46, about 20 to 35 kbps at 640x360) NVENC quantises the hashed TPDF dither away. It is high-frequency and, with `dynamic=1`, new on every frame, so it is neither compact in the transform nor predictable in time. The decoded banding is then whatever the encoder makes of the smoothed gradient. A static hashed pattern, `dither=none` and `range=31` all stay above the baseline.
2. Shader defect: `pel_fetch` clamps a tap to the plane edge. Near the border one tap of a mirrored pair repeats the edge row, so the tap average of a gradient is shifted towards the interior and the border band is flattened (row 0 of the clip: 168 of 640 pixels at +1, all positive, fading by row 40). On a spliced output, the border half and the interior half each carry about half of the CAMBI rise.
3. Dither on areas where no tap sees a code step hides no band and costs the encoder bits.

## Decision

1. In `pelorus_deband.comp.glsl` each tap offset is limited per axis to the distance from the plane edge, so both taps of a mirrored pair stay inside the plane. The sign of the offset is kept.
2. The dither term is multiplied by `maxd > 0`: grain is added only where at least one tap differs from the centre sample.
3. The bench pin in `scripts/bench/bench.sh` changes to `range=15:thry=0.012:dither=bayer8:grainy=0.012:dynamic=0:protect=1`. A static 8x8 ordered dither is periodic and the same on every frame, so the encoder keeps it at these rates.
4. The filter's option defaults do not change. The AVOption contract (`docs/api/knobs/pelorus_deband_vulkan.json`) and the frozen `vmaf-tune` subset stay as they are.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Retune the pin only (ordered dither, old shader) | No shader change | Measured: `av1_nvenc` CAMBI stays above the baseline (3.02 against 2.46) at three times the bitrate (76.2 kbps against 25.1) | Fails the acceptance on `av1_nvenc` |
| Shader fix only, pin unchanged | No bench change | Measured: CAMBI still above the baseline on both encoders (3.54 and 2.82) | The hashed dither is still quantised away |
| Change the filter defaults to the bench pin | One recipe for users and bench | Changes output of every existing pipeline and the `vmaf-tune` search start; measured only on one synthetic clip and `bbb` | Needs a real banding clip in `corpus.lock` first |
| Mirror out-of-plane taps instead of shrinking them | Keeps the tap distance | A mirrored tap at the border of a gradient still differs from the mirrored partner by the gradient slope, so the average is still biased (reasoned, not measured) | Shrinking keeps the pair symmetric around the centre |
| Stronger hashed grain (`grainy=0.02`) | No shader change | Measured in v0.20: CAMBI back only at 37 times the bitrate | Bitrate cost |

## Consequences

- **Positive**: on `synth-banding`, mean CAMBI over the pinned ladder goes from 3.61 to 1.76 with `hevc_nvenc` (baseline 2.97) and from 3.03 to 1.02 with `av1_nvenc` (baseline 2.46). On the 10-bit pipeline of v0.20 with the new pin, mean CAMBI is 0.053 against a baseline of 0.256. Flat areas get no grain, which also halves the bitrate cost of the ordered pin on the 10-bit clip (62.4 to 37.5 kbps mean).
- **Negative**: the pinned arm spends more bits at matched CQ on `synth-banding` (hevc 24.5 to 28.3 kbps, av1 25.6 to 33.4 kbps mean; baseline 25.8 and 25.1). At CQ 46 CAMBI is still above the baseline on both encoders. On `bbb`, the `av1_nvenc` VMAF BD-rate moves from +0.11 % to +1.13 %; `hevc_nvenc` moves from -0.67 % to +0.07 %, with lower CAMBI than before.
- **Neutral / follow-ups**: users who set `dither=bluenoise` get the same edge and gate behaviour; at the bench rates that dither still does not survive NVENC. A change of the defaults waits for a real banding-prone clip in `corpus.lock` (#300).

## References

- Issue: VMAFx/pelorus#283; earlier 10-bit run: VMAFx/pelorus#300 (bench v0.20).
- Research: [docs/research/0283-deband-step-gated-dither.md](../research/0283-deband-step-gated-dither.md).
- Results: [docs/development/bench-results.md](../development/bench-results.md) section v0.21.
