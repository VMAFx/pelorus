<!-- markdownlint-disable MD013 MD060 -->
# ADR-0212: Grain scoring covers AV1 through libdav1d only

- **Status**: Accepted
- **Implementation**: landed (#368)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: grain, fgs, av1, libdav1d, metrics, scoring, ci
- **Related**: [ADR-0207](0207-grain-scoring-decoded-output.md), [ADR-0189](0189-named-nvenc-smoke-skip.md), [ADR-0211](0211-grain-synth-conformance.md)

## Context

[ADR-0207](0207-grain-scoring-decoded-output.md) makes `scripts/grain-score.sh` score the decoded picture with grain and refuse the encoder's reconstruction. It hash-gates every scored file against two decodes of the stream: one with grain applied and one with `-export_side_data film_grain`. ADR-0207 covered HEVC only and named AV1 as a gap ([#368](https://github.com/VMAFx/pelorus/issues/368)).

AV1 has two software paths in FFmpeg n9.0.2:

- `libdav1d` applies grain by default and turns it off when the export flag is set (`libavcodec/libdav1d.c:222`), so the same two-decode gate works unchanged.
- The native `av1` decoder decodes only through a hwaccel. Run without one on the AV1 fixtures, it returns a framemd5 header and zero frames (measured with FFmpeg n9.0.2, 2026-10-18). Whether its hwaccel path honours the export flag was not measured.

FFmpeg picks `libdav1d` before `av1` when both are built, but nothing in the default selection says which decoder ran. The CI replay builds had no `--enable-libdav1d`.

## Decision

1. **Decoder.** For a stream whose first video stream is AV1, `grain-score.sh` decodes with `-c:v libdav1d` in all three stream decodes. A binary without the `libdav1d` decoder is refused with an error naming it. The script never falls back to the native `av1` decoder. `score.txt` records `codec:` and `decoder:`, so a score states the path that produced it.
2. **Fixtures.** Two recorded AV1 fixtures, 192x128 with 4 frames: `grain-av1-fgs.ivf`, encoded by libaom 3.15.2 with `film-grain-test=1`, and `grain-av1-nograin.ivf`, the same encode without grain. The libdav1d decode of the second hashes equal to the grain-free decode of the first.
3. **Replay leg.** `build-and-run.sh` enables libdav1d when `pkg-config` finds `dav1d`. The three CI replay jobs install `libdav1d-dev`. The new `test/grain-score-av1-smoke.sh` runs in every replay. It skips only when `GRAIN_SCORE_AV1_SMOKE_SKIP` names the printed reason ([ADR-0189](0189-named-nvenc-smoke-skip.md)).
4. **Cases.** The smoke scores the stream and expects a score that differs from the grain-free score. It accepts the grain-applied decode and refuses a planted recon. It refuses the no-grain stream as carrying no film grain. It refuses a binary whose decoder list lacks `libdav1d`.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Leave decoder selection to FFmpeg | Without libdav1d, FFmpeg selects the native decoder, which yields no frames in software, and the score names no decoder. Explicit selection fails loudly. |
| Strip grain from the AV1 fixture at test time | No FFmpeg bitstream filter clears `apply_grain` or `film_grain_params_present`, and re-encoding needs libaom in every lane. A committed no-grain encode of the same source is deterministic. |
| Generate fixtures at test time with libaom | The lavapipe and arm64 replay jobs build without libaom, so the AV1 cases would skip there. |
| Separate cases inside `grain-score-smoke.sh` | One exit 77 cannot say which half skipped; a second smoke with its own named skip keeps the HEVC cases independent of libdav1d. |

## Consequences

- AV1 grain scores come from libdav1d's grain-applied picture; a recon or a grain-free stream is refused.
- The replay binaries link libdav1d (BSD-2-Clause), which keeps the LGPL build.
- AV1 scoring through a hwaccel decoder is not supported; it needs its own measurement first.

## References

- [#368](https://github.com/VMAFx/pelorus/issues/368), [#82](https://github.com/VMAFx/pelorus/issues/82).
- [VMAFx/vmafx#2144](https://github.com/VMAFx/vmafx/issues/2144).
- Usage: [docs/metrics/grain-scoring.md](../metrics/grain-scoring.md).
