<!-- markdownlint-disable MD013 MD060 -->
# Vulkan HEVC encode hangs at frame 61 with `-force_key_frames`

**Date:** 2026-10-14

**Decision:** [ADR-0192](../adr/0192-series-v0.2.0-forced-keyframe-smoke.md), issue
[#309](https://github.com/VMAFx/pelorus/issues/309).

**Scope:** stock FFmpeg, no Pelorus patch: `n9.0.2` (`946fcce07b`), the release/9.0 tip `7e93014c5b`
and FFmpeg master between the release/9.0 branch point `47e9d68e20` ("Bump minor for release/9.0")
and `ebfc9497f7`. RTX 4090 (Vulkan device 0), driver 615.78.08 (`vulkaninfo --summary` in
`genclip5/vulkaninfo-summary.txt`). The Arc A380 and the RADV iGPU on the same host were not
measured. Input: a 1280x720 `yuv420p` HEVC clip of 120 frames whose only keyframe is frame 0
(`ffprobe` packet flags: 1 key, 119 non-key).

Raw logs (local, not committed): `.workingdir/evidence/issue-309-bisect/` (bisect),
`.workingdir/evidence/issue-309-backport/` (release/9.0 controls, builds, `fate-source`),
`.workingdir/evidence/issue-309-smoke/` (smoke cases) and `.workingdir/evidence/issue-309-pin/`
(series pin, regeneration, replay).

## Verdict

Vulkan HEVC decode into `hevc_vulkan` with `-force_key_frames 2` stops at frame 61 in a share of runs
and never finishes; `timeout` kills it. FFmpeg master commit `d0d7d81317` ("vulkan: de-phase queue
rotation and pin video pools to a single queue") removes the hang on both release lines: picked onto
`n9.0.2` and onto the release/9.0 tip, 60 of 60 runs finish. The shared series carries the pick as
patch 0008 from `v0.2.0`.

## Method

One run is:

```sh
timeout -k 5 30 ffmpeg -hide_banner -nostdin -y -init_hw_device vulkan=vk:0 -filter_hw_device vk \
  -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan -i clip.mkv \
  -force_key_frames 2 -c:v hevc_vulkan -qp 28 out.mkv
```

A build counts as broken at its first run that `timeout` ends (exit 124 or 137), and as fixed after 60
runs that each exit 0 with 120 frames. The hang is intermittent, so a single clean run says nothing.
Every hung run's last progress line reads `frame=   61`.

## Bisect on master

`git bisect` with the terms broken and fixed, from `47e9d68e20` (broken) to `ebfc9497f7` (fixed),
limited to `libavcodec/*vulkan*`, `libavutil/*vulkan*`, `libavcodec/hw_base_encode*`,
`libavcodec/hevc/*vulkan*` and `fftools/`:

| Commit | Subject | Result |
| --- | --- | --- |
| `f93cd72dde` | fftools/ffprobe: drop duplicate HDR10+ window corner fields | 60/60 |
| `b6ac28aff6` | vulkan: use fixed-size dependency arrays in FFVkExecContext | hang at run 1 |
| `b16b5f2a01` | vulkan_hevc: move the inline session parameter structures off the stack | hang at run 6 |
| `92f292807e` | vulkan: properly align the GET_BITS_SMEM cache refills | 60/60 |
| `b7e8611b2a` | vulkan: enable unified image layouts when supported | 60/60 |
| `a1c83c513f` | vulkan: carry the execution context's busy state in a timeline semaphore | 60/60 |
| `8884301271` | vulkan: enable maintenance9 when supported | hang at run 1 |
| `d0d7d81317` | vulkan: de-phase queue rotation and pin video pools to a single queue | 60/60, first fixed |

## Controls

| Build | Result |
| --- | --- |
| `n9.0.2` | hang at run 1 |
| `n9.0.2` plus `d0d7d81317` (`466bb60b89`) | 60/60 |
| release/9.0 tip `7e93014c5b` | 25 of 60 runs hang, first at run 1 |
| release/9.0 tip plus `d0d7d81317` (`669f5ab083`, clean `cherry-pick -x`) | 60/60 |
| master `ebfc9497f7` with `d0d7d81317` reverted (`3957f745d2`) | 60/60 |

The revert control shows that later master commits also keep this reproducer from hanging, so the
pick is not the only possible fix on master. On both release lines it is the one measured fix.
release/9.0 builds before and after the pick emit the same ten compiler warnings, and `fate-source`
passes (`issue-309-backport/logs/build-rel90-base.make.log`, `build-rel90.make.log`,
`fate-source.log`).

## Smoke

`ffmpeg-patches/test/vulkan-forced-keyframe-smoke.sh` (sha256 `2e02db47`) runs the command above 60
times. Without `INPUT` it generates the clip with the binary under test; it counts the input frames
with a stream copy and fails when that copy fails or counts none. "Minimal" is the bisect
configuration (Vulkan HEVC decode and encode, Matroska, no lavfi graph decoders) on `n9.0.2` with series
patches 0001 to 0007; "minimal + 0008" adds the queue de-phase pick. `INPUT` is a clip that the system
`n9.0.2` generated. "No Vulkan driver" sets `VK_DRIVER_FILES` to a missing file. The planted wrappers
pass every call to a real binary except one: the clip generation (names `smptehdbars`) or the frame
count (writes `probe_count.mkv`). All cases ran on the same host, 2026-10-14 16:27 to 16:29
(`issue-309-smoke/genclip5/summary.txt`):

| Case | Binary | Exit | Output |
| --- | --- | --- | --- |
| `--self-test`, generated clip | system `n9.0.2` | 0 | hang at run 1, frame 61 |
| no Vulkan driver | system `n9.0.2` | 77 | `no Vulkan device with HEVC video` |
| no `INPUT` | minimal | 77 | `cannot generate the input clip: no wrapped_avframe in ... -decoders; set INPUT` |
| `INPUT` set, no Vulkan driver | minimal | 77 | `no Vulkan device with HEVC video` |
| generation fails (planted wrapper) | system `n9.0.2` | 1 | `generating the input clip failed: planted: generation refused` |
| frame count fails (planted wrapper) | minimal + 0008 | 1 | `counting the input frames failed: planted: count probe refused` |
| `INPUT` set | minimal + 0008 | 0 | 60/60, 120 frames, 48 s |
| `INPUT` set | minimal | 1 | hang at run 2, frame 61 |

### First hang per measurement

Every 60-run measurement of an unfixed build in this digest, in order of the run that first hung:

| First hang | Build | Source |
| --- | --- | --- |
| run 1 | `n9.0.2`; master `b6ac28aff6`, `8884301271` | `issue-309-bisect/summary.txt` |
| run 1 | release/9.0 tip `7e93014c5b` (25 of 60 hung) | `issue-309-backport/rel90-summary.txt` |
| run 1 | system `n9.0.2`, `--self-test` (twice) | `issue-309-smoke/genclip4/`, `genclip5/` |
| run 2 | minimal | `issue-309-smoke/genclip5/` |
| run 4 | system `n9.0.2`, `--self-test` | `issue-309-smoke/genclip3/` |
| run 6 | master `b16b5f2a01` | `issue-309-bisect/summary.txt` |
| run 9 | minimal | `issue-309-smoke/genclip4/` |
| run 20 | minimal | `issue-309-smoke/genclip3/` |

The minimal build with the same clip first hung at run 2, 9 and 20 in three measurements, so a smoke
of 10 runs would have passed one of them.

Before tagging `v0.2.0`, the eight series patches and the 22 Pelorus patches applied in order onto
`n9.0.2`. No Pelorus patch touches `libavutil/vulkan.c` or `libavcodec/ffv1enc_vulkan.c`, the two files
0008 changes.

## Open points

- The hang was measured on NVIDIA only. Whether Mesa drivers (ANV, RADV) show it is not known.
- Hosted CI has no Vulkan video device, so the replay there runs the smoke only up to its named skip.
- 0008 is dropped from the series once the pinned FFmpeg base carries `d0d7d81317`; FFmpeg pull
  request 25060 asks for the pick on release/9.0.
