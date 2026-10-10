<!-- markdownlint-disable MD013 MD060 -->
# ADR-0186: pelorus_scenecut runs on hardware frames, not after hwdownload

- **Status**: Proposed
- **Implementation**: pending (#102)
- **Date**: 2026-10-10
- **Deciders**: Lusoris
- **Tags**: scenecut, zero-copy, encoder-steering, docs

## Context

[ADR-0126](0126-scenecut-idr.md) (Accepted, so its body is frozen)
tells users to run `pelorus_scenecut` after `hwdownload`, just before the
encoder, and the filter source comment and the README repeated it. Every
hardware-encoder recipe that followed that placement round-tripped full frames
through system memory, which the zero-copy requirement forbids
([research 0172](../research/0172-zero-copy-audit.md) sections 1 and 6, gap ZC-G13).

The source read in that research said the placement is not required: the
filter has no format callback, so format negotiation accepts hardware formats,
and the rule for filters that are not frame-aware copies the input
`hw_frames_ctx` to the output. The filter touches only the frame's side data,
`pict_type` and the key flag. It had not been run on hardware.

## Decision

1. **Placement.** Run `pelorus_scenecut` on the frames as they are, after
   `pelorus_mc_vulkan=meta=1` and the last Pelorus stage, before the frames
   leave VRAM. No `hwdownload` is needed for a hardware encoder. A software
   encoder still downloads; the filter works on either side of that
   `hwdownload`.
2. **Scope of the supersession.** This ADR replaces only the sentence of
   ADR-0126 that places the filter after `hwdownload`. Everything else in
   ADR-0126 (the `pict_type == I` mechanism, the `force_idr` option, no
   per-encoder patch) stands.
3. **The command line still needs `-force_key_frames source`**, for the
   reason in [scenecut](../metrics/scenecut.md): `fftools` overwrites
   `pict_type` before the encoder otherwise.
4. **The filter source comment** in `ffmpeg-patches/files/vf_pelorus_scenecut.c`
   ("Run it after hwdownload") is corrected together with a regenerated patch
   stack; that change is a follow-up of this PR (see Consequences).

## Alternatives considered

| Option | Verdict | Evidence |
| --- | --- | --- |
| Keep "after `hwdownload`" | Rejected | Forces a host round trip in every hardware recipe for a filter that does no pixel work; measured below to be unnecessary. |
| Add a format callback that accepts only software formats | Rejected | Would make the wrong placement a hard error but also remove the working one, and needs a patch-stack change for no gain. |
| Move the cut detection into the encoder patches | Rejected | ADR-0126 chose a vendor-neutral consumer so that no per-encoder patch is needed; hardware frames honour `pict_type` as they are. |

## Consequences

Measured on 2026-10-10 with FFmpeg `n9.0.2`, the shared fix series 0001 to
0004 and the Pelorus stack at `50b625b`, on a 120-frame 1280x720 clip with a
hard cut at frame 60 and a source keyframe at frame 0 only. With
`pelorus_scenecut` the encoder output has `I` pictures at frames 0 and 60;
without it at frame 0 only.

| Encoder | GPU | Graph | Exit code |
| --- | --- | --- | --- |
| `hevc_vulkan` | RTX 4090 | Vulkan decode, `mc`, `scenecut`, `-force_key_frames source` | 0 |
| `hevc_nvenc` | RTX 4090 | NVDEC, `hwupload` to Vulkan, `mc`, deband, `scenecut`, `hwupload` to CUDA | 0 |
| `hevc_qsv` | Arc A380 | VAAPI decode, `mc`, deband `tiling=drm`, `scenecut`, map to VAAPI and QSV | 0 |

- Not measured: AMF (no AMF runtime on the test host) and the software
  encoders (the test binary has none).
- The filter source comment and the regenerated patch `0016` are not in this
  change: regenerating the stack needs the full FFmpeg replay. Next step:
  edit the comment, run `ffmpeg-patches/generate.sh`, then
  `ffmpeg-patches/test/build-and-run.sh`.
- `hevc_vulkan` stalled at the cut once when `-g 250` was set together with
  `-force_key_frames source`, and also on a plain `-force_key_frames 2` without
  any Pelorus filter, so the stall is not caused by this filter.

## References

- Issue [#102](https://github.com/VMAFx/pelorus/issues/102) (ZC-G13) and its proposal to correct the ADR-0126 placement through a new ADR (req).
- [ADR-0126](0126-scenecut-idr.md), [research 0172](../research/0172-zero-copy-audit.md) sections 1 and 5, [scenecut metrics page](../metrics/scenecut.md).
- FFmpeg `n9.0.2`: `libavfilter/avfilter.c` (frames-context rule for filters that are not frame-aware), `libavcodec/nvenc.c`, `libavcodec/qsvenc.c`, `libavcodec/hw_base_encode.c` (`pict_type == I` on hardware frames).
- Evidence: `.workingdir/evidence/issue-102-recipes/results.md` (local).
