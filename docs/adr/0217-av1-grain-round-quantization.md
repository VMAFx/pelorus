<!-- markdownlint-disable MD013 MD060 -->
# ADR-0217: AV1 Film Grain Synthesis Round Quantization Modeling

- **Status**: Accepted
- **Implementation**: landed (#384)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: grain, av1, h274, quantization, conformance, descriptor
- **Related**: [ADR-0205](0205-aom-h274-grain-conversion.md), [ADR-0211](0211-grain-synth-conformance.md), [ADR-0216](0216-h274-grain-quantization.md)

## Context

Issue [#384](https://github.com/VMAFx/pelorus/issues/384) investigated discrepancies between the AV1 film grain descriptor `pel_grain_describe_aom()` and the reference AV1 synthesizer (`ff_aom_apply_film_grain` in FFmpeg `libavcodec/aom_film_grain_template.c`).

While [ADR-0216](0216-h274-grain-quantization.md) resolved H.274 floor quantization modeling, `pel_grain_describe_aom()` remained linear:

$$\sigma_{\text{lin}}[c][i] = \text{scaling}[idx] \cdot \sigma_w \cdot g_{\text{ar}} \cdot 2^{-\text{scaling\_shift}}$$

In AV1 reference synthesis (`libavcodec/aom_film_grain_template.c:247, 379`), noise is added to pixels via `round2`:

```c
noise = round2(scaling[val] * grain, data->scaling_shift);
```

where `round2(x, s) = (x + (1 << (s - 1))) >> s`. Adding half a unit before right-shifting performs round-to-nearest integer quantization ($X \in [k - 0.5, k + 0.5) \to k$).

For a zero-mean Gaussian variable $X \sim \mathcal{N}(0, s^2)$, the standard deviation of its rounded value is given by $R(s) = \text{sd}(\text{round}(X))$, corresponding to bin offset $\text{off} = 0.5$:

$$P(k) = \frac{1}{2} \left[ \text{erf}\left(\frac{k + 0.5}{s\sqrt{2}}\right) - \text{erf}\left(\frac{k - 0.5}{s\sqrt{2}}\right) \right]$$

### Correction of Issue #384 Claims

Preliminary claims in issue #384 suggested an unmodeled linear chroma_1 bias of 4.0% to 5.6% and a 6% overprediction of the round model. A 100-seed sweep over scale factors $k \in \{1, 2, 6\}$ ([research digest](../research/0384-av1-grain-rounding.md)) disproved both:

1. **No linear chroma bias**: at $k = 6$, where quantization is negligible, every mean error is at most 0.4%. The 4.0% to 5.6% chroma_1 figure in ADR-0216 and issue #384 came from seed 42 alone; the per-seed standard deviation of the error is 1.6% for luma and up to 3.7% for chroma_1.
2. **The round model matches synthesis**: $R(s)$ matches the 100-seed measured mean within 0.9% in all 15 cases ($\sigma$ from 0.49 to 8.4).

| Case ($k = 1$) | Linear descriptor | Measured mean (100 seeds) | $R(s)$ | Measured vs $R(s)$ |
| :--- | :--- | :--- | :--- | :--- |
| Luma 64 | 0.7689 | 0.8187 (+6.5%) | 0.8213 | -0.3% |
| Luma 128 | 1.3980 | 1.4256 (+2.0%) | 1.4275 | -0.1% |
| Luma 192 | 0.9436 | 0.9918 (+5.1%) | 0.9868 | +0.5% |
| Chroma 1 | 0.4860 | 0.5516 (+13.5%) | 0.5564 | -0.9% |
| Chroma 2 | 0.6480 | 0.7058 (+8.9%) | 0.7091 | -0.5% |

The entire discrepancy is small-sigma quantization.

## Decision

1. **Unified Bin-Offset Quantization Helper (HISS-19)**:
   In `libpelorus/src/grain_convert.c`, generalise `pel_quantized_sigma` to accept a bin offset:

   ```c
   float pel_quantized_sigma(float sigma_lin, float bin_offset);
   ```

   with bins $[k - \text{bin\_offset}, k + 1 - \text{bin\_offset})$. Named constants `PEL_QUANT_FLOOR_OFFSET = 0.0f` (H.274 floor) and `PEL_QUANT_ROUND_OFFSET = 0.5f` (AV1 round) are defined in `grain_convert_internal.h`. The asymptotic $\sqrt{s^2 + 1/12}$ for $s \ge 5.0$ and $[-64, 64)$ bounded summation are preserved.

2. **Public AV1 Descriptor Models Round Quantization**:
   `pel_grain_describe_aom()` returns $R(\sigma_{\text{lin}})$ per intensity. An internal helper `describe_aom_internal(aom, out, quantized)` mirrors `describe_h274_internal()`; the public API sets `quantized = 1`.

3. **Conversions Retain Master Output Bit-Identical**:
   `pel_grain_aom_to_h274()` builds H.274 parameters from the linear AOM descriptor (`quantized = 0`), ensuring emitted H.274 structs are bit-identical to master. `pel_grain_h274_to_aom()` remains unchanged (already reads linear H.274).

4. **Fidelity Compares Realised vs Realised**:
   Fidelity evaluation in both directions compares public realised descriptors: public rounded AOM descriptor vs public floor H.274 descriptor.

5. **Follow-Up for Quantization-Aware Conversion**:
   The synthesis chroma gap between AV1 round2 and H.274 floor (noted in [ADR-0211](0211-grain-synth-conformance.md)) is unaffected by descriptor modeling and will be addressed in a dedicated quantization-aware conversion follow-up.

## Alternatives considered

| Alternative | Why not chosen |
| :--- | :--- |
| Keep the AV1 descriptor linear | Underpredicts measured synthesis by up to 13.5% (chroma 1) at small $\sigma$; consumers of `pel_grain_describe_aom()` read a noise level the decoder never produces. |
| Linear chroma_1 correction factor | The bias it would correct does not exist: it was a seed-42 artefact and vanishes in the multi-seed mean at $k = 6$. |
| Quantization-aware conversions in the same change | Changes the emitted H.274 and AV1 parameters and needs its own conformance evidence against both synthesisers; kept separate so this change leaves conversion output bit-identical. |
| A second, AV1-only quantization helper | Duplicates the floor helper of ADR-0216 (HISS-19); one helper with a bin offset covers both. |

## Consequences

- The mean error of the AV1 descriptor against multi-seed reference synthesis drops from +13.5% (chroma 1) and +6.5% (luma 64) to at most 0.9% (research digest table).
- `ffmpeg-patches/test/grain_conformance.c` adds 32-seed mean checks `fixture_a_<case>_aom_sigma_seedmean_vs_desc` with bound 0.010; master measures 0.023 to 0.136 and fails them.
- The seed-42 checks `fixture_a_chroma_<p>_aom_sigma_vs_desc` (bound 0.090) are removed: with the rounded descriptor, seed 42 sits 10% from it on chroma 1 (one seed spreads 3.7% around the mean), and the 32-seed check gates the same case at 0.010. The seed-42 luma checks tighten from 0.120 to 0.030 and Fixture B from 0.030 to 0.021.
- Conversion output for Fixture A stays bit-identical to master: `test_conversions_bit_identical_to_master` in `libpelorus/test/grain_convert_test.c` pins a hash of both output structs.
- `fidelity` output of both conversions changes, because it now compares the rounded AV1 descriptor with the floor H.274 descriptor. Fixture A (`setup_realistic_aom` in `libpelorus/test/grain_convert_test.c`): `pel_grain_aom_to_h274()` loss 0.198 to 0.071, the follow-up `pel_grain_h274_to_aom()` loss 0.023 to 0.046. No test pins these values; the existing tests check only bounds.

## References

- Issue [#384](https://github.com/VMAFx/pelorus/issues/384): AV1 film grain descriptor bias.
- [ADR-0211](0211-grain-synth-conformance.md): Reference synthesizer grain conformance.
- [ADR-0216](0216-h274-grain-quantization.md): H.274 film grain synthesis floor quantization modeling.
- [Research digest](../research/0384-av1-grain-rounding.md): 100-seed sweep and 32-seed check results.
