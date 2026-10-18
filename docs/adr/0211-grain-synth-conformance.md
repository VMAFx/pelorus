<!-- markdownlint-disable MD013 MD060 -->
# ADR-0211: Film grain synthesis conformance against reference decoders

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: grain, av1, h274, conformance, testing, replay
- **Related**: [ADR-0189](0189-named-nvenc-smoke-skip.md), [ADR-0205](0205-aom-h274-grain-conversion.md), [ADR-0206](0206-fgs-bsf-per-frame-bridge.md), [ADR-0207](0207-grain-scoring-decoded-output.md)

## Context

Issue [#82](https://github.com/VMAFx/pelorus/issues/82) checklist item 4 requires that "The conformance test synthesises grain with both decoders' reference implementations on the converted parameters and bounds the difference."

[ADR-0205](0205-aom-h274-grain-conversion.md) established the codec-neutral descriptor domain (`PelorusGrainDescriptor`), normative descriptor mathematics, and bidirectional deterministic conversion (`pel_grain_aom_to_h274()`, `pel_grain_h274_to_aom()`). During ADR-0205, pixel-domain synthesis inside `libpelorus` was rejected because vendoring full decoders would violate zero-allocation lightweight library principles and duplicate upstream code.

Consequently, ground-truth conformance testing must execute against FFmpeg's authoritative reference synthesizers at the pinned release (`n9.0.2` / `946fcce07b`):

- `ff_aom_apply_film_grain()` (`libavcodec/aom_film_grain.h`, AV1 autoregressive model)
- `ff_h274_apply_film_grain()` (`libavcodec/h274.h`, SMPTE RDD 5 / H.274 model 0 frequency filtering, YUV420P 8-bit)

Both functions share the contract `int f(AVFrame *out, const AVFrame *in, const AVFilmGrainParams *params)`. Because these reference implementations reside in FFmpeg's static libraries (`libavcodec.a` and `libavutil.a`), conformance runs as part of the FFmpeg patch stack replay rather than inside the fast suite.

## Decision

1. **Standalone conformance harness (`ffmpeg-patches/test/grain_conformance.c`)**:
   - Compiles against an FFmpeg build tree (`libavcodec.a`, `libavutil.a`, `-lm`, `-lpthread`) and `libpelorus`. Pure CPU.
   - Evaluates flat 512x512 8-bit YUV420P frames across luma intensities 64, 128, and 192 (chroma 128).
   - Pixel measurement computes RMS $\sigma$ (standard deviation) and lag-1 spatial autocorrelation ($\rho_h$, $\rho_v$) on `(out - in)` excluding a 16-pixel border.
2. **Two parameter regimes**:
   - **Fixture A (divergent models)**: Realistic AV1 parameters (`setup_realistic_aom` from `grain_convert_test.c` with lag-1 AR coefficients) converted via `pel_grain_aom_to_h274()`.
   - **Fixture B (coinciding models)**: Single-interval H.274 model 0 parameters converted via `pel_grain_h274_to_aom()` where both synthesizers target identical flat intensity behavior.
3. **Bounded assertions**:
   - (a) Synthesizer measured $\sigma$ agrees with libpelorus descriptor prediction (`pel_grain_describe_aom` and `pel_grain_describe_h274`).
   - (b) AV1-synthesized vs H.274-synthesized measured $\sigma$ relative difference is bounded.
   - (c) Lag-1 autocorrelation differences $|\Delta \rho_h|$ and $|\Delta \rho_v|$ are bounded.
   - Bounds are set by measuring ground truth and taking $1.2\times$ worst measured value rounded up.
4. **Determinism and negative verification**:
   - Determinism asserts byte-identical output across consecutive synthesis runs with identical seeds.
   - Negative testing (`--plant <scale|cutoff|shift>`) verifies rejection: `scale` doubles H.274 scale, `cutoff` forces 2/2 frequency cutoffs, `shift` increments AV1 `scaling_shift`. Each planted condition must exit 1 with the failing check named.
5. **Replay integration (`ffmpeg-patches/test/grain-conformance.sh`)**:
   - Wired into `ffmpeg-patches/test/build-and-run.sh` after `grain-score-smoke.sh`.
   - Exits 77 with a named `SKIP:` reason if `libavcodec.a` or symbols `ff_aom_apply_film_grain` / `ff_h274_apply_film_grain` are missing, gated by `GRAIN_CONFORMANCE_SKIP` ([ADR-0189](0189-named-nvenc-smoke-skip.md)).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Reference synthesizers in replay (chosen) | Tests actual decoder binaries; zero vendoring; authoritative ground truth | Requires built FFmpeg tree; runs in replay, not fast suite | Chosen |
| Python re-implementation | Runnable without FFmpeg compilation | Python script is not the reference implementation; discrepancies could hide decoder bugs | Refused: a port is not the reference |
| libaom's standalone `grain_synthesis.c` | Official AOM reference | FFmpeg's `aom_film_grain` is what our pipeline and decoders execute; libaom is an optional dependency | Refused as primary |
| Vendor FFmpeg synthesizers in fast suite | Runs during `meson test` | License mixing (LGPL-2.1 vs EUPL-1.2 in libpelorus), code duplication, maintenance overhead | Refused: violates library separation |

## Consequences

- Conformance between AV1 and H.274 film-grain synthesis is proven against real reference decoders.
- Conformance runs automatically during every FFmpeg patch stack replay.
- Measured bounds establish baseline tolerances across synthesisers:

| Metric | Fixture A Measured | Fixture A Bound | Fixture B Measured | Fixture B Bound |
| :--- | :--- | :--- | :--- | :--- |
| Luma $\sigma$ vs AV1 descriptor | 0.0944 (9.4%) | 0.120 (12.0%) | 0.0249 (2.5%) | 0.030 (3.0%) |
| Luma $\sigma$ vs H.274 descriptor | 0.0921 (9.2%) | 0.115 (11.5%) | 0.0113 (1.1%) | 0.015 (1.5%) |
| Luma $\sigma$ AV1 vs H.274 synth | 0.0299 (3.0%) | 0.036 (3.6%) | 0.0130 (1.3%) | 0.016 (1.6%) |
| Luma $\|\Delta \rho_h\|$ AV1 vs H.274 | 0.0455 | 0.055 | 0.0315 | 0.038 |
| Luma $\|\Delta \rho_v\|$ AV1 vs H.274 | 0.0607 | 0.075 | 0.0288 | 0.035 |
| Chroma $\sigma$ vs AV1 descriptor | 0.0712 (7.1%) | 0.090 (9.0%) | - | - |
| Chroma $\sigma$ vs H.274 descriptor | 0.2297 (23.0%) | 0.280 (28.0%) | - | - |
| Chroma $\sigma$ AV1 vs H.274 synth | 0.1837 (18.4%) | 0.230 (23.0%) | - | - |
| Chroma $\|\Delta \rho_h\|$ AV1 vs H.274 | 0.0690 | 0.085 | - | - |
| Chroma $\|\Delta \rho_v\|$ AV1 vs H.274 | 0.1312 | 0.160 | - | - |

> [!NOTE]
> Superseded in part by [ADR-0216](0216-h274-grain-quantization.md).
>
> The chroma $\sigma$ gap of the H.274 synthesizer against the descriptor (23.0%) came from floor quantization in `libavcodec/h274.c` (`(scale * db) >> shift`), which the descriptor did not model. With the floor model of [ADR-0216](0216-h274-grain-quantization.md) the measured gap is 3.9% (issue #82). The chroma $\sigma$ gap between the AV1 and H.274 synthesizers (18.4%) is unchanged and remains open.

## References

- Issue [#82](https://github.com/VMAFx/pelorus/issues/82), item 4 (reference synthesizer conformance).
- [ADR-0189](0189-named-nvenc-smoke-skip.md): Named smoke skips.
- [ADR-0205](0205-aom-h274-grain-conversion.md): AV1 and H.274 grain conversion.
- Upstream decoders: FFmpeg `libavcodec/aom_film_grain.c`, `libavcodec/h274.c`.
