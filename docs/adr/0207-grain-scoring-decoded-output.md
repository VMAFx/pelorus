<!-- markdownlint-disable MD013 MD060 -->
# ADR-0207: Score the decoded grain-synthesised picture, never the reconstruction

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: grain, fgs, h274, hevc, metrics, scoring, vmafx
- **Related**: [ADR-0115](0115-grain-estimate.md), [ADR-0117](0117-grain-fgs-bsf.md), [ADR-0206](0206-fgs-bsf-per-frame-bridge.md)

## Context

The Pelorus grain path denoises before the encoder, estimates the grain and carries it as film grain characteristics SEI ([ADR-0206](0206-fgs-bsf-per-frame-bridge.md)). The decoder adds the grain back. The encoder's reconstruction never holds that grain, so a quality score taken on it describes a picture no viewer sees: against the grainy source it counts the removed grain as coding error, and against a denoised intermediate it ignores the synthesised grain. #82 item 4 asks for a scoring recipe, and the VMAFx control plane needs the same rule ([VMAFx/vmafx#2144](https://github.com/VMAFx/vmafx/issues/2144)).

FFmpeg n9.0.2 applies H.274 grain in the HEVC decoder by default (`libavcodec/hevc/hevcdec.c:3337-3340`, `:3488`) and skips it when `-export_side_data film_grain` is set. Both outputs are available from one binary without extra filters.

## Decision

1. **Script.** `scripts/grain-score.sh` scores a stream's decoded, grain-applied picture against a reference with `psnr`, `ssim` or `libvmaf`, and writes `score.txt` with `decode_path: grain-applied`.
2. **Hash gate.** The script decodes the stream twice to `framemd5`, with and without grain. Equal hashes: the stream has no grain, exit 1. The file it scores (a given `--distorted` file or its own decode) must hash equal to the grain-applied decode; equal to the grain-free decode is refused by name, equal to neither is refused.
3. **No metric fallback.** A metric filter missing from the binary is an error naming the filter.
4. **Replay smoke.** `ffmpeg-patches/test/grain-score-smoke.sh` runs in every stack replay on a committed 192x128 HEVC fixture with one FGC SEI per frame. A planted recon file must be refused. A host without an HEVC decoder or `psnr` skips only when `GRAIN_SCORE_SMOKE_SKIP` names the reason ([ADR-0189](0189-named-nvenc-smoke-skip.md)).

## Alternatives considered

| Option | Why not |
| --- | --- |
| Document the rule only | Nothing stops a recon from being scored; the issue asks for a test that refuses it. |
| Compare file names or container tags to detect a recon | A renamed or re-muxed recon passes; frame hashes identify the pixels. |
| Generate the fixture at test time with an encoder plus `pelorus_fgs` | Needs a GPU or a GPL encoder; the LGPL CI replay has neither, so the smoke would always skip there. |
| Cover AV1 in the same change | No AV1 fixture with grain and no libdav1d in the CI build yet; left as a stated gap. |

## Consequences

- Grain scores from Pelorus tooling come from the decoded picture with grain.
- A score of the recon is an error, not a silent number.
- AV1 streams are not covered until an AV1 fixture and a libdav1d replay leg exist.
- VMAF scoring needs a binary with `libvmaf`; the CI replay binary has none, so the smoke checks the refusal for that case.

## References

- [#82](https://github.com/VMAFx/pelorus/issues/82), item 4 (scoring recipe).
- [VMAFx/vmafx#2144](https://github.com/VMAFx/vmafx/issues/2144).
- A. Norkin, N. Birkbeck, "Film Grain Synthesis for AV1 Video Codec", DCC 2018, [doi:10.1109/dcc.2018.00008](https://doi.org/10.1109/dcc.2018.00008).
- Usage: [docs/metrics/grain-scoring.md](../metrics/grain-scoring.md).
