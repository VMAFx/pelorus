<!-- markdownlint-disable MD013 MD060 -->
# ADR-0205: AV1 (AOM) and H.274 film grain parameter conversion with fidelity estimate

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: grain, av1, h274, fgs, api
- **Related**: [ADR-0161](0161-grain-estimate-rounding-and-h274-mapping.md), [ADR-0204](0204-libaom-grain-table-io.md)

## Context

Film grain synthesis in video pipelines uses codec-specific mathematical models. AV1 adopts an autoregressive (AR) causal filter combined with piecewise-linear intensity scaling functions (`PelorusFilmGrainSection`, mirroring FFmpeg's `AVFilmGrainAOMParams`). Conversely, HEVC/H.265, VVC/H.266, and H.264 SEI use H.274 Film Grain Characteristics (FGC) model 0, which specifies band-pass frequency cutoffs and piecewise-constant intensity intervals (`PelorusH274GrainParams`, mirroring `AVFilmGrainH274Params`).

Cross-codec transcoding, quality assessment, and encoder steering require bidirectional parameter conversion between AV1 and H.274 representations without losing intensity-dependent grain amplitude or spatial correlation structure. Issue #82 requires deterministic bidirectional conversion (`pel_grain_aom_to_h274()`, `pel_grain_h274_to_aom()`) and fidelity measurement (`pel_grain_compare()`) in libpelorus.

## Decision

1. **Codec-neutral descriptor domain**: Define `PelorusGrainDescriptor` measuring per-component intensity-dependent grain RMS $\sigma[c][i]$ ($i \in [0, 255]$) and lag-1 spatial autocorrelations $(\rho_h[c], \rho_v[c])$.
2. **Normative AOM descriptor math**:
   - Component presence: luma requires `num_y_points > 0`; chroma requires `chroma_scaling_from_luma` or `num_uv_points > 0`.
   - Scaling function: 8-bit piecewise linear interpolation matching FFmpeg's `generate_scaling` (`libavcodec/aom_film_grain_template.c`, pinned n9.0.2).
   - Chroma intensity index: identity when `chroma_scaling_from_luma`; otherwise clipped $((i \cdot \text{uv\_mult\_luma} + i \cdot \text{uv\_mult}) \gg 6) + \text{uv\_offset}$.
   - White grain RMS $\sigma_w = 511.5251 / 2^{4 + \text{grain\_scale\_shift}}$, where $511.5251$ is the population RMS of FFmpeg's 2048-entry `gaussian_sequence`.
   - Causal 2D AR filter impulse response on a 64x64 grid (impulse at row 3, column 32) to derive AR energy gain $g_{\text{ar}} = \sqrt{\sum h^2}$ and spatial autocorrelations $\rho_h, \rho_v$.
   - Effective grain RMS: $\sigma[c][i] = \text{scaling}[\text{index}(i)] \cdot \sigma_w \cdot g_{\text{ar}} / 2^{\text{scaling\_shift}}$.
3. **Normative H.274 descriptor math**:
   - Pre-computed calibration table `pel_h274_grain_stats[169]` (reusing the calibration methodology of ADR-0161) measuring FFmpeg's SMPTE RDD 5 model 0 synthesizer across cutoff pairs $(cut_h, cut_v) \in [2, 14]^2$ at log2 scale factor 2.
   - First matching interval $[lower, upper]$ provides scale, $cut_h$, and $cut_v$.
   - 4:2:0 chroma adaptation matching FFmpeg: scale right-shifted by 1; cutoffs doubled and clamped to 14.
   - $\sigma[c][i] = \text{scale} \cdot \text{stat.gain} \cdot 2^{2 - \text{log2\_scale\_factor}}$.
   - Component spatial autocorrelations $\rho_h[c], \rho_v[c]$ computed as $\sigma^2$-weighted means across matching intervals.
4. **Fidelity metric (`pel_grain_compare`)**:
   - Per-component relative RMS error: $\sigma_{\text{rel\_rms}}[c] = \text{RMS}(\sigma_a - \sigma_b) / \max(\text{RMS}(\sigma_a), 10^{-9})$ (0 if both absent, 1 if presence differs).
   - Spatial correlation deltas: $\Delta \rho_h[c] = |\rho_h^a - \rho_h^b|$ and $\Delta \rho_v[c] = |\rho_v^a - \rho_v^b|$ (0 if both absent).
   - Composite loss: $\text{loss} = \max_c (\sigma_{\text{rel\_rms}}[c] + 0.5 \cdot (\Delta \rho_h[c] + \Delta \rho_v[c]))$.
5. **Deterministic conversion**:
   - `pel_grain_aom_to_h274()`: selects cutoff pairs minimizing correlation Euclidean distance (chroma constrained to reachable pre-adaptation cutoffs 2..7); selects largest `log2_scale_factor` in 2..7 keeping all scales $\le 255$; merges contiguous equal-scale runs into intervals, discarding zero-scale intervals.
   - `pel_grain_h274_to_aom()`: selects cutoffs from the dominant interval maximizing $\sum \sigma^2$; sets lag 1 separable AR model $[-ab, b, 0, a]$; generates 14 luma and 10 chroma scaling points, selecting the largest `scaling_shift` in 8..11 keeping all values $\le 255$.
6. **Zero heap allocation and strict range validation**: All buffers are caller-provided; out-of-range fields return `PEL_ERR_RANGE` without clamping; invalid pointers return `PEL_ERR_INVALID`; output buffers are untouched on error and deterministic (`memset`) on success. The AV1 range rules (including seed 0..65535) live once in `pel_grain_aom_validate()`, shared with the grain-table reader and writer.
7. **Silent components are absent**: a component with zero $\sigma$ at every intensity is reported absent in both descriptors. `pel_grain_h274_to_aom()` emits a flat zero luma curve when only chroma carries grain, because AV1 4:2:0 codes no chroma points without luma points.
8. **Generated statistics table**: `scripts/gen-h274-grain-calibration.py --stats` writes `libpelorus/src/grain_convert_tables.c` from FFmpeg's `h274.c` at the pinned base; `--stats --check` fails on drift.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Codec-neutral descriptor with calibration table (chosen) | Zero heap allocation; deterministic; preserves intensity dependence and spatial autocorrelation; fast evaluation | Depends on pre-computed calibration table | Chosen |
| Pixel-domain synthesis and fitting | High statistical ground truth | Requires vendoring both full synthesizers; CPU intensive; violates zero-allocation lightweight library principles | Rejected: excessive complexity and runtime overhead |
| Single scalar strength mapping | Trivial implementation | Completely discards intensity intervals and frequency cutoff shapes; severe quality mismatch | Rejected: unacceptable fidelity loss across intensity range |
| Continuous closed-form formula for H.274 | No table required | Deviates from FFmpeg fixed-point synthesis, deblocking, and DCT quantization behavior (ADR-0161) | Rejected: fails to match decoder synthesis |

## Consequences

- **Positive**: libpelorus provides bidirectional, zero-allocation conversion between AV1 and H.274 film grain parameters with bounded fidelity loss; applications can convert parameters and evaluate fidelity before passing to encoders or BSFs.
- **Negative**: Transcoding introduces unavoidable representation loss due to differing structural assumptions (e.g., H.274 cannot achieve zero autocorrelation; AV1 AR cannot reproduce sharp frequency cutoffs).
- **Neutral**: Public API append-only; interop ABI is unaffected.

## References

- [Issue #82](https://github.com/VMAFx/pelorus/issues/82): Film grain parameter interchange.
- [ADR-0161](0161-grain-estimate-rounding-and-h274-mapping.md): H.274 model 0 calibration.
- [ADR-0204](0204-libaom-grain-table-io.md): Film grain table I/O.
- Upstream references: FFmpeg `libavcodec/aom_film_grain_template.c`, `libavcodec/h274.c`.
