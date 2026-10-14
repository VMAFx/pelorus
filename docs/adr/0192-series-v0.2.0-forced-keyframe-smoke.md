<!-- markdownlint-disable MD013 MD060 -->
# ADR-0192: Pin the shared series at v0.2.0 and gate the forced-keyframe hang with a GPU smoke

- **Status**: Accepted
- **Date**: 2026-10-14
- **Deciders**: Lusoris
- **Tags**: vulkan, ffmpeg, hevc, testing, ci

## Context

Vulkan HEVC decode into `hevc_vulkan` with `-force_key_frames 2` stops at frame 61
and never finishes in a share of runs on FFmpeg `n9.0.2` (#309). The process makes
no progress until a timeout kills it. Stock FFmpeg reproduces it, without any
Pelorus filter.

A bisect between the release/9.0 branch point and FFmpeg master, at 60 runs per
step because the hang is intermittent, named master commit `d0d7d81317` ("vulkan:
de-phase queue rotation and pin video pools to a single queue") as the first good
commit. release/9.0 after `n9.0.2` does not carry it. On the release/9.0 tip
`7e93014c5b`, 25 of 60 runs hung; with the backport, 60 of 60 finished.

The fix is generic FFmpeg code. Hard rule 5 sends such a fix to the shared series
(VMAFx/ffmpeg-patches, [ADR-0185](0185-shared-ffmpeg-fix-series.md)), never to
this stack. The series carries it as patch 0008 since its `v0.2.0` release, and
the backport is open upstream as FFmpeg pull request 25060 against release/9.0.

No test in this repository runs `-force_key_frames` on a Vulkan encoder, so the
replay would not notice the hang coming back, for example after a base bump that
loses 0008.

## Decision

- `build-config.env` pins the shared series at `v0.2.0` (patches 0001 to 0008).
- `ffmpeg-patches/test/vulkan-forced-keyframe-smoke.sh` runs the reproducer 60
  times, each under a 30 s timeout, and passes only when every run encodes all
  frames. A hang (exit 124 or 137) fails it. `--self-test` inverts the verdict: it
  passes only when a run hangs, which proves the smoke still detects the defect on
  an unfixed binary.
- The input is a 120-frame 1280x720 HEVC clip whose only keyframe is frame 0.
  Without `INPUT`, the smoke generates it with the binary under test (lavfi
  `testsrc2` then `smptehdbars`, `hevc_vulkan -g 1000 -bf 0`). A binary that lacks
  any component the generation uses skips with the missing name.
- `build-and-run.sh` runs the smoke after the NVENC smokes. A skip passes only when
  `KEYFRAME_SMOKE_SKIP` names its reason ([ADR-0189](0189-named-nvenc-smoke-skip.md)).
  Both hosted CI lanes name `no Vulkan device with HEVC video`: one has no Vulkan
  driver, the other only lavapipe, which has no Vulkan video.

## Alternatives considered

- **Carry the backport in this stack.** Rejected: hard rule 5; a fix in stock
  FFmpeg code belongs in the series, where every consumer can pin it.
- **Commit the input clip.** Rejected: a binary test asset in the tree, while the
  binary under test can produce an equivalent clip. Measured: a clip generated
  this way makes an unfixed build hang (`--self-test` passed at run 1 of 60).
- **Fewer runs.** Rejected: the hang is intermittent. On unfixed builds the first
  hang came between run 1 and run 20 across six 60-run measurements; 10 runs would
  have missed one of them. 60 clean runs take about 49 s on an RTX 4090.
- **No CI wiring, manual runs only.** Rejected: the replay is where a lost 0008
  would show; on GPU hosts the smoke then runs with every replay, and on hosted
  lanes the named skip keeps any other skip reason failing.

## Consequences

- A GPU host with Vulkan HEVC encode and decode runs 60 encodes per replay.
- The pin also brings series patches 0005 to 0007: VAAPI import of P010 and
  P012 chroma that Vulkan exports as `GR1616`, the image format list in the
  Vulkan export query, and a CPU wait before mapping RADV frames to DRM. They
  touch `libavutil/hwcontext_vaapi.c` and `libavutil/hwcontext_vulkan.c`;
  Pelorus patch 0009 regenerates with a new blob index only.
- Hosted CI does not run the encodes; the smoke's planted cases are measured
  locally (see References), not in CI.
- Dropping 0008 from the series is due once the pinned FFmpeg base carries
  `d0d7d81317`; the smoke stays as the regression check.

## References

- Issue #309; FFmpeg master commit `d0d7d813178fec964716aa2c48f5896ce5c26284`;
  FFmpeg pull request 25060 (release/9.0 backport).
- [Research digest 0309](../research/0309-forced-keyframe-hang.md): bisect, release
  branch control, smoke measurements.
- VMAFx/ffmpeg-patches `v0.2.0` release (tarball, SBOM, provenance, signed
  `SHA256SUMS`).
