<!-- markdownlint-disable MD013 -->
# Grain scoring: score the decoded picture, never the reconstruction

**Stance:** `experimental` — Available for HEVC (H.274 FGC SEI) and AV1 (film grain parameters, decoded with libdav1d) ([#82](https://github.com/VMAFx/pelorus/issues/82), [#368](https://github.com/VMAFx/pelorus/issues/368), [ADR-0207](../adr/0207-grain-scoring-decoded-output.md), [ADR-0212](../adr/0212-grain-scoring-av1-libdav1d.md)).

A stream with film grain synthesis carries grain as parameters, not as coded pixels. The decoder adds the grain to the picture it outputs. The encoder's reconstruction (recon) never contains that grain, so a score taken on the recon measures a picture no viewer sees. `scripts/grain-score.sh` scores the decoded, grain-synthesised picture and refuses a recon.

## Why the recon is the wrong input

The Pelorus grain path removes grain before the encoder ([`pelorus_denoise_vulkan`](denoise.md)), estimates its parameters ([`pelorus_grain_estimate_vulkan`](grain_estimate.md)) and writes them into the stream ([`pelorus_fgs`](../usage/grain-fgs-bsf.md)). Norkin and Birkbeck describe this split for AV1: the encoder transmits a grain model relative to the denoised signal, and the decoder renoises the output ([DCC 2018, doi:10.1109/dcc.2018.00008](https://doi.org/10.1109/dcc.2018.00008)).

- The recon is the denoised signal plus coding error. Scored against the grainy source, it counts the removed grain as coding error. Scored against a denoised intermediate, it ignores the synthesised grain completely.
- The decoded picture is what the viewer sees. Synthesised grain is random, so it lowers pixel-difference metrics such as PSNR against the source even when it looks right. The score has to include that effect, and only the decoded picture shows it.

The VMAFx quality control plane needs the same rule for its scores ([VMAFx/vmafx#2144](https://github.com/VMAFx/vmafx/issues/2144)).

## Recipe

```bash
scripts/grain-score.sh \
  --ffmpeg /path/to/ffmpeg \
  --reference source.y4m \
  --stream encoded.hevc \
  --metric psnr \
  --out /path/to/score-dir
```

| Option | Required | Meaning |
| --- | --- | --- |
| `--ffmpeg BIN` | yes | FFmpeg binary that decodes and scores |
| `--reference SRC` | yes | reference file, or `lavfi:<graph>` for a generated source |
| `--stream ENC.hevc\|ENC.ivf\|ENC.obu` | yes | encoded stream carrying film grain metadata (.hevc, .ivf, .obu) |
| `--distorted FILE` | no | an already decoded file to score instead of decoding the stream; checked as below |
| `--metric psnr\|ssim\|vmaf` | yes | metric filter: `psnr`, `ssim` or `libvmaf` |
| `--out DIR` | yes | evidence directory; `score.txt` holds metric, value, `decode_path: grain-applied`, `codec:` and `decoder:` |

## What the script checks

1. It decodes the stream twice to per-frame MD5 (`-f framemd5`): once as the decoder does by default, with grain applied, and once with `-export_side_data film_grain`, which skips the grain and so yields the recon.
2. Both decodes equal: the stream carries no grain to synthesise. Exit 1.
3. The file it will score (the `--distorted` file, or its own grain-applied decode) is hashed the same way. Equal to the grain-free decode: exit 1 naming the `grain-free reconstruction`. Equal to neither decode: exit 1. Only a match with the grain-applied decode is scored.
4. The metric filter is missing from `ffmpeg -filters`: exit 1 naming it. The script never switches to another metric.

FFmpeg n9.0.2 source behind step 1:

- HEVC: `libavcodec/hevc/hevcdec.c:3337-3340` sets `needs_fg` only when the export flag is unset; `hevcdec.c:3488` and `:3491` apply H.274 or AOM grain.
- AV1 with libdav1d: `libavcodec/libdav1d.c:222` turns off `apply_grain` when the export flag is set. The script forces `-c:v libdav1d` and refuses without it; the native `av1` decoder without a hwaccel outputs no frames (measured with n9.0.2, ADR-0212).

## Verification

`ffmpeg-patches/test/grain-score-smoke.sh` runs in every stack replay. It needs only the HEVC decoder and the `psnr` filter, so it also runs in the LGPL CI build. It uses the fixture `ffmpeg-patches/test/fixtures/grain-h274-fgc.hevc` (192x128, 4 frames, one FGC SEI per frame) and checks:

| Case | Expected |
| --- | --- |
| stream scored | exit 0, PSNR differs from the PSNR of the grain-free decode |
| `--distorted` = grain-applied decode | exit 0 |
| `--distorted` = grain-free decode (planted recon) | exit 1, names `grain-free reconstruction` |
| stream with prefix SEI removed | exit 1, no film grain |
| `--metric vmaf` without libvmaf | exit 1, names `libvmaf` |

`ffmpeg-patches/test/grain-score-av1-smoke.sh` verifies the AV1 leg via `libdav1d` (ADR-0212). It uses the fixtures `ffmpeg-patches/test/fixtures/grain-av1-fgs.ivf` (192x128, 4 frames, libaom film-grain-test=1) and `grain-av1-nograin.ivf` (same encode, no grain) and checks:

| Case | Expected |
| --- | --- |
| a1 stream scored | exit 0, `decoder: libdav1d`, PSNR differs from the PSNR of the grain-free decode |
| a2 `--distorted` = grain-applied decode | exit 0 |
| a3 `--distorted` = grain-free decode (planted recon) | exit 1, names `grain-free reconstruction` |
| a4 `--stream grain-av1-nograin.ivf` | exit 1, no film grain |
| a5 ffmpeg without libdav1d | exit 1, names `libdav1d` |

## References

- [`pelorus_fgs`](../usage/grain-fgs-bsf.md): inserts H.274 FGC SEI into HEVC streams.
- [`pelorus_grain_estimate_vulkan`](grain_estimate.md): grain parameter estimator.
- [ADR-0207](../adr/0207-grain-scoring-decoded-output.md): decision record for this recipe.
- [ADR-0212](../adr/0212-grain-scoring-av1-libdav1d.md): AV1 scoring covers libdav1d only.
