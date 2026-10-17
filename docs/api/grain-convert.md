<!-- markdownlint-disable MD013 -->
# Film Grain Parameter Conversion API

libpelorus provides a pure C conversion engine between AV1 (AOM) and H.274 (SEI Film Grain Characteristics model 0) parameter representations, along with a codec-neutral grain descriptor and fidelity loss metric. This enables Pelorus filters, transcoder pipelines, and quality orchestrators to translate film-grain parameters across codec boundaries with predictable fidelity and zero heap allocation.

- Header: [`pelorus/grain_convert.h`](../../libpelorus/include/pelorus/grain_convert.h)
- Implementation: [`libpelorus/src/grain_convert.c`](../../libpelorus/src/grain_convert.c), [`libpelorus/src/grain_convert_tables.c`](../../libpelorus/src/grain_convert_tables.c)
- Decision: [ADR-0205](../adr/0205-aom-h274-grain-conversion.md)
- Related: [Film Grain Table I/O API](grain-table.md), [ADR-0161](../adr/0161-grain-estimate-rounding-and-h274-mapping.md)

## Usage example

```c
#include <pelorus/grain_convert.h>
#include <pelorus/interop.h>
#include <stdio.h>
#include <string.h>

void convert_aom_to_h274_example(const PelorusFilmGrainSection *aom_params)
{
    PelorusH274GrainParams h274_out;
    PelorusGrainFidelity fidelity;

    pel_result r = pel_grain_aom_to_h274(aom_params, &h274_out, &fidelity);
    if (r != PEL_OK) {
        /* Input out of range or invalid */
        return;
    }

    /* Convert back from H.274 to AV1 */
    PelorusFilmGrainSection aom_round_trip;
    PelorusGrainFidelity rt_fidelity;
    r = pel_grain_h274_to_aom(&h274_out, aom_params->seed, &aom_round_trip, &rt_fidelity);
    if (r != PEL_OK) {
        return;
    }
}
```

## Model comparison

| Attribute | AV1 (AOM) | H.274 Model 0 (SMPTE RDD 5) |
| --- | --- | --- |
| **Synthesis model** | Causal 2D autoregressive (AR) filter + Gaussian pseudo-random sequence | 2D DCT frequency band-pass filtering (separable horizontal/vertical cutoffs) |
| **Spatial correlation** | AR coefficients: lag 0..3 ($2 \cdot \text{lag} \cdot (\text{lag}+1)$ coefficients per component) | Frequency cutoffs: $cut_h, cut_v \in [2, 14]$ per interval |
| **Intensity modulation** | Piecewise-linear scaling function ($N$ points, $\le 14$ luma, $\le 10$ chroma) | Piecewise-constant intensity intervals ($lower, upper$) with 8-bit scale multiplier |
| **Spatial sampling** | Evaluated per pixel (with UV chroma-from-luma and matrix offset) | Indexed by 8x8 block mean luma intensity |
| **Chroma handling** | Separate UV scaling points or CFL (`chroma_scaling_from_luma`); trailing cross-component coupling | 4:2:0 subsampling adaptation: scale halved, cutoffs doubled and clamped to 14 |
| **Coded format** | AV1 frame header / libaom table text (`filmgrn1`) | ITU-T H.274 / SMPTE RDD 5 SEI message NALU |

## Descriptor math (normative)

To enable comparison and conversion, both representations map onto `PelorusGrainDescriptor`:

- `present[c]`: 1 if component $c$ produces film grain, 0 otherwise.
- `sigma[c][i]`: grain root-mean-square (RMS) standard deviation at 8-bit intensity index $i \in [0, 255]$.
- `rho_h[c]`: lag-1 spatial autocorrelation along horizontal direction ($x$).
- `rho_v[c]`: lag-1 spatial autocorrelation along vertical direction ($y$).

### AV1 (AOM) description

1. **Presence**:
   - Luma ($c=0$): present if `num_y_points > 0`.
   - Chroma ($c=1,2$): present if `chroma_scaling_from_luma` or `num_uv_points[c-1] > 0`.
   - Absent components produce all zeros for $\sigma$, $\rho_h$, and $\rho_v$.
   - A component whose $\sigma$ is zero at every intensity is reported absent, so a flat zero curve and a missing curve compare equal (this holds for both descriptors).
2. **Piecewise-linear scaling**:
   - Evaluated for $i \in [0, 255]$ exactly matching FFmpeg's 8-bit `generate_scaling` (`libavcodec/aom_film_grain_template.c`, pinned n9.0.2).
   - Below first point: $scaling[i] = points[0].scale$.
   - Between adjacent points $(bx, by)$ and $(ex, ey)$: linear interpolation with 16-bit fixed point increment.
   - Above last point: $scaling[i] = points[N-1].scale$.
3. **Chroma intensity index**:
   - If $c = 0$ or `chroma_scaling_from_luma`: $index = i$.
   - Otherwise: $index = \text{clip}(((i \cdot \text{uv\_mult\_luma} + i \cdot \text{uv\_mult}) \gg 6) + \text{uv\_offset}, 0, 255)$ assuming co-located luma equals $i$.
4. **White grain amplitude**:
   - $\sigma_w = 511.5251 / 2^{4 + \text{grain\_scale\_shift}}$, where $511.5251$ is the population RMS of FFmpeg's 2048-entry `gaussian_sequence`.
5. **AR filter energy and correlations**:
   - Evaluated by measuring the impulse response $h[y][x]$ of the causal 2D AR filter on a 64x64 grid with unit impulse at row 3, column 32 (centred, so up-right taps spread left without reaching the grid edge).
   - Coefficients scaled by $1 / 2^{\text{ar\_coeff\_shift}}$ and applied in causal raster order.
   - Energy gain: $g_{\text{ar}} = \sqrt{\sum_{y,x} h[y][x]^2}$ ($g_{\text{ar}} = 1.0$ for lag 0).
   - Spatial correlations: $\rho_h = \sum_{y,x} h[y][x] h[y][x+1] / \sum h^2$ and $\rho_v = \sum_{y,x} h[y][x] h[y+1][x] / \sum h^2$ ($\rho = 0$ for lag 0).
6. **Effective RMS**:
   - $\sigma[c][i] = scaling[index(i)] \cdot \sigma_w \cdot g_{\text{ar}} / 2^{\text{scaling\_shift}}$.

### H.274 Model 0 description

1. **Presence**: component is present if `component_model_present[c] != 0`.
2. **Interval matching**: for each intensity $i \in [0, 255]$, the first interval $s$ satisfying $lower[s] \le i \le upper[s]$ matches. If no interval matches, $\sigma[c][i] = 0$.
3. **Parameter lookup and chroma adaptation**:
   - Raw parameters: $scale = comp\_model\_value[c][s][0]$, $cut_h = comp\_model\_value[c][s][1]$ (default 8 if $< 2$ values), $cut_v = comp\_model\_value[c][s][2]$ (default 8 if $< 3$ values).
   - 4:2:0 chroma adaptation: $scale \gg= 1$, $cut_h = \min(cut_h \cdot 2, 14)$, $cut_v = \min(cut_v \cdot 2, 14)$.
4. **Calibration table lookup**:
   - Row $r = (cut_v - 2) \cdot 13 + (cut_h - 2)$ in `pel_h274_grain_stats[169]`.
   - $\sigma[c][i] = scale \cdot stat.gain \cdot 2^{2 - \text{log2\_scale\_factor}}$.
5. **Autocorrelation averaging**:
   - $\rho_h[c]$ and $\rho_v[c]$ are the $\sigma[c][i]^2$-weighted averages of $stat.rho\_h$ and $stat.rho\_v$ over all matching intensities ($0$ if all $\sigma = 0$).

### Fidelity metric (`pel_grain_compare`)

- Relative RMS error per component:
  $$\sigma_{\text{rel\_rms}}[c] = \frac{\sqrt{\frac{1}{256} \sum_{i=0}^{255} (\sigma_a[c][i] - \sigma_b[c][i])^2}}{\max\left(\sqrt{\frac{1}{256} \sum_{i=0}^{255} \sigma_a[c][i]^2}, 10^{-9}\right)}$$
  (defined as $0.0$ if both components are absent, $1.0$ if presence differs).
- Spatial correlation deltas:
  $$\Delta \rho_h[c] = |\rho_h^a[c] - \rho_h^b[c]|, \quad \Delta \rho_v[c] = |\rho_v^a[c] - \rho_v^b[c]|$$
- Composite loss:
  $$\text{loss} = \max_{c \in \{0,1,2\}} \left( \sigma_{\text{rel\_rms}}[c] + 0.5 \cdot (\Delta \rho_h[c] + \Delta \rho_v[c]) \right)$$

## Conversion algorithms

### AV1 to H.274 (`pel_grain_aom_to_h274`)

1. Describe the AV1 parameters with `pel_grain_describe_aom()`.
2. For each present component $c$:
   - Find $(cut_h, cut_v)$ minimizing $(stat.rho\_h - \rho_h[c])^2 + (stat.rho\_v - \rho_v[c])^2$ across calibration table rows.
   - For chroma ($c > 0$), search only the 36 rows reachable after 4:2:0 cutoff doubling and store the pre-adaptation cutoff ($2 \dots 7$).
3. Select `log2_scale_factor` as the largest integer in $2 \dots 7$ where all needed interval scales are $\le 255$.
4. Walk intensity $i = 0 \dots 255$, merging maximal runs of equal scale into intervals. Discard intervals where scale is 0. If all runs have scale 0, set `component_model_present[c] = 0`.
5. If `fidelity` is non-NULL, describe the generated H.274 parameters and evaluate `pel_grain_compare()`.

### H.274 to AV1 (`pel_grain_h274_to_aom`)

1. Describe the H.274 parameters with `pel_grain_describe_h274()`.
2. For each present component $c$:
   - Select cutoffs from the dominant interval maximizing $\sum_{i} \sigma[c][i]^2$.
   - Configure a lag-1 causal AR model with shift 7 using separable coefficients: $[-ab, b, 0, a]$ where $a = \rho_h$ and $b = \rho_v$, clamped to $[-128, 127]$.
   - Compute AR energy gain $g_{\text{ar}}$ from the resulting AR filter impulse response.
3. Compute 14 luma and 10 chroma scaling points uniformly spaced across intensity ($x_k = \text{round}(k \cdot 255 / (N-1))$).
4. Select the largest `scaling_shift` in $8 \dots 11$ keeping all point values $y_k \le 255$.
5. Set `uv_mult = 64`, `uv_mult_luma = 0`, `uv_offset = 0` (identity index), `overlap_flag = 1`, and `seed` from caller (0..65535; larger values return `PEL_ERR_RANGE`).
6. If luma is absent but chroma is present, emit a flat zero luma curve (two points, scale 0): AV1 4:2:0 codes no chroma points when `num_y_points` is 0, and the zero curve adds no luma grain.
7. If `fidelity` is non-NULL, describe the generated AV1 parameters and evaluate `pel_grain_compare()`.

## Approximations and limits

1. **Chroma luma-coupling ignored**: AV1's trailing cross-component coupling coefficient in `ar_coeffs_uv` is ignored when evaluating chroma descriptors and conversion.
2. **Chroma intensity indexing**: In AV1 chroma descriptor evaluation, co-located luma intensity is assumed equal to chroma intensity $i$.
3. **8x8 block mean indexing in H.274**: FFmpeg's synthesizer (`libavcodec/h274.c`) selects the interval from the mean of each 8x8 block of the component's own plane, not per sample; the descriptor indexes by sample intensity.
4. **Block overlap and clipping ignored**: The descriptor models steady-state local grain amplitude without simulating AV1 32x32 block boundary blending or bitdepth output clamping.
5. **Autocorrelation range of H.274**: H.274 model 0 band-pass frequency filtering cannot synthesize zero autocorrelation ($\rho \approx 0.06 \dots 0.92$). AV1 white noise ($\rho = 0$) maps to maximum cutoff $(14, 14)$ with $\rho_h \approx 0.16, \rho_v \approx 0.06$.

## Error handling

All conversion and description functions return `pel_result`:

| Code | Meaning |
| --- | --- |
| `PEL_OK` | Success; outputs populated deterministically. |
| `PEL_ERR_INVALID` | NULL required argument pointer. Output buffers are left completely untouched. |
| `PEL_ERR_RANGE` | Parameter value out of bounds, overlapping intervals, non-monotonic points, or unsupported model/blending ID. Output buffers are left completely untouched. |

## Verification

The test suite in `libpelorus/test/grain_convert_test.c` runs in the fast suite (`grain-convert`):

- **Positive & identity**: identical descriptors compare with 0 loss and all fields 0; absent models convert both ways with 0 loss and report no component present.
- **White noise boundary**: AV1 lag 0 converts to H.274 cutoffs $(14, 14)$ with loss $> 0$, and the resulting H.274 parameters re-validate cleanly.
- **Round-trip fidelity**: AV1 -> H.274 -> AV1 round trip on realistic parameters verifies that each conversion hop succeeds and relative RMS error $\sigma_{\text{rel\_rms}}[0] < 0.026$.
- **H.274 to AV1 fidelity**: single-interval H.274 parameters convert to AV1 with intensity RMS at $i=128$ matching within $0.05\%$.
- **Negative & range rejection**: planted out-of-range fields (model ID, blending mode, scale factor, interval counts, interval bounds, overlapping intervals, scales, cutoffs, AV1 shifts, lag, non-increasing points, and NULL pointers) verify rejection with `PEL_ERR_RANGE` or `PEL_ERR_INVALID` and ensure output buffers pre-filled with `0xA5` remain byte-for-byte untouched.
- **Accepted boundaries**: verifies acceptance of boundary scale factors (2 and 7), cutoffs (2 and 14), 256 one-wide intervals, and scales (0 and 255).
- **Determinism**: verifies two conversions of identical inputs produce byte-identical (`memcmp`) outputs.
- **Seed range**: seed 65535 accepted and 65536 rejected by `pel_grain_describe_aom()` and `pel_grain_h274_to_aom()`, output untouched.
- **Chroma-only H.274**: converts to a valid AV1 set with a flat zero luma curve; the 4:2:0 chroma adaptation (scale halved, cutoffs doubled) is checked exactly against the statistics table row.
- **AR mirror symmetry**: an up-left and an up-right lag-1 tap of the same weight give the same $\sigma$, $\rho_h$ and $\rho_v$.
- **Statistics table**: `scripts/gen-h274-grain-calibration.py --stats --check` (needs `FFMPEG_REPO`) regenerates `pel_h274_grain_stats` from the pinned FFmpeg synthesizer and fails on any drift beyond 0.0005 gain or 0.002 correlation.
