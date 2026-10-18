<!-- markdownlint-disable MD013 MD060 -->
# ADR-0216: H.274 Film Grain Synthesis Floor Quantization Modeling

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: grain, av1, h274, quantization, conformance, descriptor
- **Related**: [ADR-0205](0205-aom-h274-grain-conversion.md), [ADR-0211](0211-grain-synth-conformance.md)

## Context

Issue [#82](https://github.com/VMAFx/pelorus/issues/82) tracked an unresolved discrepancy in H.274 film grain synthesis conformance noted in [ADR-0211](0211-grain-synth-conformance.md): the H.274 reference synthesizer (`ff_h274_apply_film_grain` in FFmpeg `libavcodec/h274.c`) produced chroma grain with measured standard deviation $\sigma$ up to 23.0% higher than predicted by `pel_grain_describe_h274()`, and 18.4% higher than AV1 reference synthesis (`ff_aom_apply_film_grain`).

Analysis of the reference synthesizers revealed that they employ different quantization mechanisms:

1. **AV1 / AOM Synthesis Rounds**:
   In FFmpeg `libavcodec/aom_film_grain_template.c:247`:

   ```c
   noise = round2(scaling[*src] * (grain), data->scaling_shift);
   ```

   where `round2(x, s) = (x + (1 << (s - 1))) >> s`. Adding half a unit before right-shifting performs round-to-nearest integer quantization (offset 0.5).

2. **H.274 Synthesis Floors**:
   In FFmpeg `libavcodec/h274.c` `synth_grain_8x8_c()`:

   ```c
   out[x] = (scale * db[x]) >> shift;
   ```

   In C signed arithmetic, right-shifting a signed integer performs arithmetic right shift (floor division towards $-\infty$, offset 0.0).

For a zero-mean Gaussian variable $X \sim \mathcal{N}(0, s^2)$, the standard deviation of its quantized value is given by $V_q(s, \text{off}) = \text{sd}(\lfloor X + \text{off} \rfloor)$, where floor corresponds to $\text{off} = 0.0$ and round corresponds to $\text{off} = 0.5$. The exact distribution over integer bins $k \in \mathbb{Z}$ is:

$$P(k) = \Phi\left(\frac{k + 1 - \text{off}}{s}\right) - \Phi\left(\frac{k - \text{off}}{s}\right) = \frac{1}{2} \left[ \text{erf}\left(\frac{k + 1 - \text{off}}{s\sqrt{2}}\right) - \text{erf}\left(\frac{k - \text{off}}{s\sqrt{2}}\right) \right]$$

Numerical evaluation of $V_q(s, \text{off})$ shows:

| Linear $\sigma$ ($s$) | Floor ($\text{off} = 0.0$) | Round ($\text{off} = 0.5$) | Ratio (Floor / Round) |
| :--- | :--- | :--- | :--- |
| 0.10 | 0.5000 | 0.0008 | 625.0 |
| 0.30 | 0.5017 | 0.3092 | 1.623 |
| 0.48 | 0.5697 | 0.5504 | 1.035 |
| 0.65 | 0.7115 | 0.7109 | 1.001 |
| 1.00 | 1.0408 | 1.0408 | 1.000 |
| 1.39 | 1.4197 | 1.4197 | 1.000 |

### Key Observations

- For $s > 0.65$ (typical luma grain), floor and round models produce equal output within 0.1%. Both quantizers inflate variance asymptotically by $+1/12$ ($\sqrt{s^2 + 1/12}$).
- For $s < 0.65$ (typical chroma grain around $s \approx 0.5$), the models diverge substantially. In particular, the floor model has a theoretical minimum non-zero standard deviation $\lim_{s \to 0^+} V_q(s, 0.0) = 0.5000$, whereas the round model smoothly approaches 0.0.
- Floor quantization is non-decreasing on $(0, 5]$, and converges to the uniform asymptotic $\sqrt{s^2 + 1/12}$ for $s \ge 5.0$.

## Decision

1. **H.274 Descriptor Models Floor Quantization**:
   `pel_grain_describe_h274()` models the arithmetic right-shift floor synthesis `(scale * db) >> shift`:
   $$\sigma[c][i] = V_q(\sigma_{\text{lin}}[c][i])$$
   using the exact Gaussian floor model $V_q(s) = \text{sd}(\lfloor X \rfloor)$. Autocorrelation coefficients $\rho_h, \rho_v$ remain energy-weighted.

2. **AOM Descriptor Remains Linear**:
   `pel_grain_describe_aom()` remains linear in this PR, matching master behavior.
   Superseded by [ADR-0217](0217-av1-grain-round-quantization.md): the AV1 descriptor now models `round2` quantization.

3. **Conversions Unchanged**:
   `pel_grain_aom_to_h274()` and `pel_grain_h274_to_aom()` retain their exact master conversion behavior. When `pel_grain_h274_to_aom()` converts parameters, it reads the linear H.274 sigma descriptor so that its generated AV1 parameters, scaling shift, and fidelity output remain bit-for-bit identical to master.

## Measured Follow-Up Facts

> [!NOTE]
> Systematic multi-seed evaluation in [ADR-0217](0217-av1-grain-round-quantization.md) demonstrated that the chroma_1 linear bias and 6% round overprediction below were single-seed 42 artifacts; the round model $R(s)$ matches the 100-seed measured mean within 0.9% across all scales without linear bias.

Investigation of reference synthesizer behavior in `.workingdir/evidence/82-chroma/uvmul.log` established the following facts:

1. **Chroma Linear Bias in AOM Descriptor**:
   Chroma UV scaling across $\times 2, \times 3, \times 6$ (where quantization effects are negligible) reveals that the linear AOM descriptor overestimates synthesized chroma_1 noise by 4.0% to 5.6% at every scale, whereas chroma_2 error remains $\le 0.7\%$.

2. **Round Model Overprediction at Low Scale**:
   At scale $\times 1$ ($s \approx 0.46$), applying a round2 Gaussian model with the linear bias removed still overpredicts synthesized noise by $\sim 6\%$ (predicted 0.531 vs measured 0.5007).

3. **Impact on Conversion**:
   The cause of both AOM-side deviations is not isolated by a test. Conversion therefore keeps the linear model on both sides until the AOM descriptor is resolved in follow-up work.

## Consequences

- The H.274 descriptor gap on chroma in [ADR-0211](0211-grain-synth-conformance.md) (23.0%) is closed, dropping error against reference synthesis to 3.9%.
- Conformance bounds for `h274_sigma_vs_desc` in `ffmpeg-patches/test/grain_conformance.c` are tightened ($1.2\times$ worst measured rounded up to 3 decimals):
  - Fixture A Luma: 0.115 tightened to 0.026 (worst measured 0.02165)
  - Fixture A Chroma: 0.280 tightened to 0.048 (worst measured 0.03919)
  - Fixture B Luma: 0.015 tightened to 0.005 (worst measured 0.00373)
- All other bounds (`aom_sigma_vs_desc`, `aom_vs_h274_sigma`, $\rho$) keep master values; the chroma AV1 vs H.274 synthesizer gap (18.4%) is unchanged by this decision.
- Fast unit test suite in `libpelorus/test/grain_convert_test.c` verifies floor $V_q$ minimum 0.5 as $s \to 0^+$, large $s$ within 1% of $\sqrt{s^2 + 1/12}$, monotonicity on $(0, 5]$, and H.274 chroma fixture descriptor matching $V_q(\text{linear})$.

## References

- Issue [#82](https://github.com/VMAFx/pelorus/issues/82): H.274 chroma sigma above descriptor.
- [ADR-0205](0205-aom-h274-grain-conversion.md): AV1 and H.274 grain conversion.
- [ADR-0211](0211-grain-synth-conformance.md): Reference synthesizer grain conformance.
- Evidence log: `.workingdir/evidence/82-chroma/uvmul.log`.
