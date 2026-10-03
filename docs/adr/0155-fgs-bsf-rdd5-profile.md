<!-- markdownlint-disable MD013 -->
# ADR-0155: pelorus_fgs defaults to the SMPTE RDD 5 profile and rejects unwritable models at init

- **Status**: Proposed
- **Date**: 2026-10-03
- **Deciders**: Lusoris
- **Tags**: grain, fgs, h274, hevc, bsf, ffmpeg, sei

## Context

[ADR-0117](0117-grain-fgs-bsf.md) added `pelorus_fgs`, which inserts a static
H.274 Film Grain Characteristics (FGC) SEI into HEVC. Its defaults and its
mapping recipe took the grain estimator's H.274 scalars as they are:
`model_id=1` (auto-regression) and `log2_scale=8`. The estimator hard-codes
those two values (`map_aom()` in
`ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c`) and fits no
auto-regression model. An August 2026 bug hunt reported four defects against the
BSF (BUG-001, BUG-002, BUG-012, BUG-024). Each was checked against Rec. ITU-T
H.274 (V4, 01/2026), SMPTE RDD 5-2006, and FFmpeg n9.0.2 at the pinned commit,
and each was reproduced at runtime with a CPU-only patched FFmpeg and libx265:

- **The default round trip outputs no grain (BUG-001).** FFmpeg's only H.274
  synthesizer (`libavcodec/h274.c`) implements the SMPTE RDD 5 profile. In that
  profile `model_id` must be 0, `log2_scale_factor` must be in [2, 7], the
  cutoff frequencies must be in [2, 14], and at most three model values are
  allowed. `ff_h274_film_grain_params_supported()` (`libavcodec/h274.h`) accepts
  only model 0, so the HEVC decoder (`libavcodec/hevc/hevcdec.c`) logs
  `Unsupported film grain parameters. Ignoring film grain.` once and drops the
  grain. Switching to model 0 is not enough on its own: the synthesizer computes
  `(scale * pattern) >> (log2_scale_factor + 6)`, so at `log2_scale=8` every
  sample moves by 0 or −1. The measured luma sigma was 0.000 with the old
  defaults and 0.50 with `model_id=0`; neither is visible grain.
- **Model 0 values are incomplete, and chroma uses the luma interval
  (BUG-012).** The BSF wrote only `comp_model_value[c][i][0]`. H.274 infers
  absent cutoffs as 8, but `libavcodec/h274.c` reads them as zero and clamps
  them to 2, its coarsest pattern. Cb and Cr were also given the luma
  intensity interval, although H.274 matches each component's interval against
  that component's own block average.
- **An unwritable model produces an empty output and exit 0 (BUG-002).**
  `scale_y` and `scale_c` accepted 0..255 for every model. For model 1, H.274
  limits `comp_model_value` to [−2^(bitdepth−1), 2^(bitdepth−1) − 1], which is
  [−128, 127] at 8 bits (`ses()` in `libavcodec/cbs_h265_syntax_template.c`).
  The CBS writer rejected every access unit. The ffmpeg CLI logs a failed
  bitstream-filter packet and continues unless `-xerror` is set
  (`fftools/ffmpeg_mux.c`, `mux_packet_filter()`), so the run produced a 0-byte
  file and exited 0.
- **An inverted interval is accepted (BUG-024).** With
  `intensity_low > intensity_high` the interval matches no sample, so the SEI is
  inserted but no grain is ever synthesized.

## Decision

`pelorus_fgs` will default to the SMPTE RDD 5 profile and will validate every
model it can before the first packet:

- The defaults become `model_id=0` and `log2_scale=2`. New `cutoff_h` and
  `cutoff_v` options (default 8, the H.274 inferred value; range 2–14) are always
  written for model 0, with `num_model_values_minus1 = 2`. Model 1 keeps
  sigma-only output.
- New `intensity_low_c` and `intensity_high_c` options give Cb and Cr their own
  interval, defaulting to the full range.
- Init rejects an empty interval. It also rejects any `scale_y`/`scale_c` that
  exceeds the H.274 range for the configured model at the bit depth from the
  codec parameters' pixel format or from SPS extradata. Each failure is a clear
  error, and the ffmpeg CLI exits non-zero.
- When the bit depth is known only from in-band SPS, each access unit is
  checked before the SEI is added. A failure returns `AVERROR(EINVAL)`, logs the
  detailed error once, and says once that the output is incomplete.
- Valid H.274 values outside RDD 5 (`model_id=1`, `blending_mode=1`,
  `log2_scale` outside 2–7) stay accepted but log an init warning that names
  FFmpeg's behaviour.

The estimator's `h274_*` scalars are left unchanged. The usage documentation
now says not to copy them, and gives a `scale_y` recipe derived from H.274
equations (27) to (31).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| RDD 5 defaults, explicit cutoffs, init validation (this) | Default output is decodable by FFmpeg and RDD 5 decoders; bad options fail before any output; H.274-legal values remain available | New options to document; per-AU failures without a known bit depth still cannot change the CLI exit code | **Chosen** |
| Documentation-only fix (keep `model_id=1`, `log2_scale=8`) | No behaviour change | The default stays a no-op that only a careful reader avoids; BUG-002 and BUG-024 remain | Rejected: the evidence shows the defaults themselves are wrong |
| Reject `model_id=1` and non-RDD 5 values outright | No configuration can be silently ignored by FFmpeg | Removes H.274-legal output that another decoder may implement; narrows the ADR-0117 option contract | Rejected: warn instead and keep the option |
| Validate model 1 at init against the 8-bit bound when the bit depth is unknown | Init always fails for that case | Rejects legal 10-bit values (limit 511) | Rejected: fall back to the per-SPS check |
| Also change the estimator to emit `h274_model_id=0`, `h274_log2_scale=2` | Producer and consumer agree | Changes another filter and patch (0006) outside this fix's scope; the BSF does not read those fields | Deferred: follow-up |

## Consequences

- **Positive**: the default `pelorus_fgs` output is synthesized by FFmpeg
  (measured luma sigma 1.91 on a flat grey clip at `scale_y=16`); a model the
  CBS writer cannot serialize fails at init with exit 234 instead of producing
  a 0-byte file; chroma intervals behave as H.274 specifies.
- **Negative**: the default changes for existing command lines that relied on
  `model_id=1` or `log2_scale=8`. Such a command produced no visible grain
  through FFmpeg before, so no working pipeline loses grain. If the bit depth is
  known only from in-band SPS, a range error still yields exit 0 from the CLI
  without `-xerror`; the BSF logs that the output is incomplete.
- **Neutral / follow-ups**: the fast suite gains `fgs-bsf-contract`
  (`scripts/test-fgs-bsf-contract.py --self-test`). Follow-ups are to align the
  estimator's `h274_*` scalars, add a multi-interval mapping, and measure the
  ADR-0111 BD-rate and visual match.

## References

- Rec. ITU-T H.274 (V4, 01/2026), film grain characteristics SEI semantics:
  `fg_comp_model_value` ranges and inference, equations (27) to (31).
- SMPTE RDD 5-2006, subclause 1.4 (bitstream constraints) and 1.5 (the
  bit-accurate grain synthesis FFmpeg implements).
- FFmpeg n9.0.2 (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`):
  `libavcodec/h274.c`, `libavcodec/h274.h`, `libavcodec/hevc/hevcdec.c`,
  `libavcodec/cbs_h265_syntax_template.c`, `libavcodec/cbs_bsf.c`,
  `fftools/ffmpeg_mux.c`.
- [ADR-0117](0117-grain-fgs-bsf.md) (the BSF and its option contract),
  [ADR-0115](0115-grain-estimate.md) (the estimator and its H.274 scalars),
  [ADR-0111](0111-benchmark-methodology.md) (deferred quality proof).
- `ffmpeg-patches/files/h265_pelorus_fgs_bsf.c`,
  `ffmpeg-patches/0010-add-pelorus_fgs_bsf.patch`,
  [docs/usage/grain-fgs-bsf.md](../usage/grain-fgs-bsf.md).
- Source: `req` — bug-wave brief for BUG-001: "Verify against ITU-T H.274 film
  grain characteristics semantics (model 0 = frequency filtering, 1 =
  auto-regression) and what grain_estimate actually feeds; fix default or doc as
  the evidence dictates." BUG-002: "validate per model_id at init with a clear
  error, and make a CBS write failure fail the packet loudly instead of
  producing silent empty output." BUG-012: "Emit correct model values for model
  0 (cut frequencies) and per-component intervals." BUG-024:
  "intensity_low > intensity_high accepted. Validate."
