<!-- markdownlint-disable MD013 MD060 -->
# AMD RADV Vulkan video encode for Pelorus steering

**Date:** 2026-10-09

**Decision:** encode: go. Steering: no-go today, one named patch defect away;
input to [ADR-0173](../adr/0173-tester-programme.md) and
[#231](https://github.com/VMAFx/pelorus/issues/231) (AMD image, 0.5)

**Scope:** Radeon 610M iGPU of a Ryzen 9 9950X3D (PCI ID `1002:13c0`, which
`lspci` names Granite Ridge and RADV names `RAPHAEL_MENDOCINO`; gfx1036, RDNA 2,
`amdgpu`, PCI `0000:7d:00.0`, `renderD129`) on one workstation, kernel
7.2.9-2-cachyos. Two
Mesa builds of RADV: Debian `25.0.7-2+deb13u1` inside the local tester image
`pelorus-tester:intel-extbrc` (`sha256:eb68e61ab978f3d88ffad999ae4e4acaa84f97918f58a9988f955599689aba89`,
FFmpeg `n9.0.2` plus the Pelorus patch stack, version string `ccedcda`), and the
host's Arch `26.2.4-arch3.1`. Only the AMD device was mapped into the container
and the Vulkan ICD was pinned to `radeon_icd.json`, so no other GPU and no
lavapipe was reachable. Clips: `synth-banding` (the tester fixture,
`tools/tester/fixtures.lock.json`) and `detail` (below), 640x360, 24 frames.

## Verdict

| Question | Answer |
| --- | --- |
| Does RADV encode with `h264_vulkan` and `hevc_vulkan`? | **Yes**, on both Mesa builds. Every stream decodes to 24 of 24 frames. |
| Does RADV encode AV1? | **No.** The device exposes `VK_KHR_video_encode_h264` and `_h265` only; this FFmpeg has no `av1_vulkan` encoder either. |
| Do the Pelorus filters and side-data carriers run on RADV? | **Yes.** The shipped tester run on the AMD device passes `sidedata_roundtrip` and `zero_copy_chain` for both codecs. |
| Does `-pelorus_roi 1` change the bitstream on RADV? | **No**, on either Mesa build and both codecs. The encoder probe disables the map and passes through. |
| Why? | Mesa 25.0.7 lacks `VK_KHR_video_encode_quantization_map`. Mesa 26.2.4 has it, but patch 0009 queries the map format with the map usage bit alone and demands `R8_SINT`; RADV advertises `R32_SINT`, and only with the usages asked for. |
| Can it work? | Probably, with a patch 0009 probe fix. Not tested: the spike rebuilt nothing. RADV does advertise a transfer- and storage-capable delta map, a [-51, 51] QP-delta range and a 16x16 (H.264) or 64x64 (H.265) texel block. |

Consequence for the AMD image in 0.5: it can ship with the encode, side-data and
zero-copy legs. The steering leg stays `not_run` (the tester already reports it
so) until patch 0009 is fixed and re-measured on Mesa 26.2 or newer.

## How the run was built

`.workingdir/evidence/amd-radv-spike/run.sh` (local, git-ignored) drives all
legs, one job at a time:

1. **Image as shipped.** `docker run --device /dev/dri/renderD129 --device /dev/dri/card0`
   with `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.json`. Mesa 25.0.7.
2. **Same FFmpeg binary on the host's Mesa 26.2.4.** The image carries no Mesa
   26, so the container also mounts the host's `/usr/lib` and
   `/usr/share/vulkan` read-only and starts FFmpeg through the host's
   `ld-linux-x86-64.so.2` with the image's FFmpeg and `libpelorus` first on the
   library path. This is a measurement device, not a shippable image. The
   device line proves the path: `driverInfo = Mesa 26.2.4-arch3.1`,
   `driverName = radv`, one device visible, and FFmpeg logs
   `Device 0 selected: ... (RADV RAPHAEL_MENDOCINO) (integrated) (0x13c0)`.
   Mesa 26.2.4 prints the CPU brand string as the `deviceName` of this iGPU;
   the ID `0x13c0` and the `RADV RAPHAEL_MENDOCINO` suffix identify the Raphael
   graphics core.
3. **Capability probe** (`qmprobe.c`, plain Vulkan, same ICD pin).
4. **The shipped tester** (`docker run ... pelorus-tester:intel-extbrc run`)
   on the AMD device, image Mesa.

Per-region quality is the `psnr` filter on a decoded clip against the source,
cropped to the left half (the ROI) and the right half. Streams are compared by
a packet MD5 (`-c copy -f md5`), because two identical Matroska encodes differ
in their muxer UID unless written bit-exact. The `detail` clip is
`testsrc2=size=640x360:rate=25,noise=alls=12:allf=t+u:all_seed=7`: the banding
fixture is almost flat, so a QP change moves it little.

## Baseline: stock rate control on RADV

Command shape (`spike.sh`): `-vf format=nv12,hwupload -c:v h264_vulkan -rc_mode cqp -qp 30`.
Sizes are bit-exact Matroska bytes on `synth-banding`; PSNR is the whole-clip
`average` of the `psnr` filter in dB.

| Codec | Mode | Mesa 25.0.7 bytes / dB | Mesa 26.2.4 bytes / dB |
| --- | --- | --- | --- |
| h264_vulkan | `cqp -qp 30` | 4078 / 53.98 | 2717 / 53.84 |
| h264_vulkan | `cbr -b:v 1M` | 85568 / 63.78 | 95625 / 60.40 |
| h264_vulkan | `vbr -b:v 1M -maxrate 2M` | 7028 / 62.07 | 9669 / 59.41 |
| h264_vulkan | `driver -b:v 1M` | 5166 / 54.60 | 8734 / 58.46 |
| hevc_vulkan | `cqp -qp 30` | 2135 / 58.09 | 2279 / 58.09 |
| hevc_vulkan | `cbr -b:v 1M` | 84784 / 65.72 | 89803 / 65.90 |
| hevc_vulkan | `vbr -b:v 1M -maxrate 2M` | 9695 / 64.92 | 9839 / 64.92 |
| hevc_vulkan | `driver -b:v 1M` | 3263 / 60.31 | 3809 / 60.54 |

- All sixteen encodes exit 0 and decode to 24 of 24 frames
  (`out-image/results.tsv`, `out-host/results.tsv`).
- Default rate control (`-qp 30` with no `-rc_mode`) is byte-identical to
  `-rc_mode cqp` on every run: the encoder picks constant QP.
- The two Mesa builds produce different streams for the same input, so a
  bitstream digest recorded on one Mesa version is not a regression reference
  for another.
- The CBR rows exceed the 1 Mbit/s target: the 24-frame clip is under one
  second and RADV spends its start-up budget. These rows show the mode is
  accepted, not that its accuracy is good. Measuring RADV bitrate accuracy
  needs a longer clip.

## Steering: `-pelorus_roi 1` on versus off

CQP, `-qp 30`, ROI rectangle on the left half through FFmpeg's `addroi` filter
(`addroi=x=0:y=0:w=iw/2:h=ih:qoffset=-0.3`, then `+0.3`). `showinfo` confirms
the side data reaches the encoder input
(`side data - Regions Of Interest: ... region: (0, 0) -> (320, 360), qp offset: -3/10`).
Mesa 26.2.4, the only build that exposes the quantization-map extension:

| Clip | Codec | qoffset | Off: bytes / packet MD5 | On: bytes / packet MD5 | Left dB / right dB, off and on |
| --- | --- | --- | --- | --- | --- |
| detail | h264_vulkan | -0.3 and +0.3 | 91499 / `74a6709e73be` | 91499 / `74a6709e73be` | 36.68 / 35.86 both |
| detail | hevc_vulkan | -0.3 and +0.3 | 103003 / `3084d8e057d4` | 103003 / `3084d8e057d4` | 36.80 / 35.99 both |
| synth-banding | h264_vulkan | -0.3 and +0.3 | 2717 / `9df156cee26a` | 2717 / `9df156cee26a` | 54.06 / 53.63 both |
| synth-banding | hevc_vulkan | -0.3 and +0.3 | 2279 / `7073d4d2ecd7` | 2279 / `7073d4d2ecd7` | 58.80 / 57.48 both |

All eight steered streams are identical to their unsteered controls, and the
`-0.3` and `+0.3` runs are identical to each other. The left-versus-right gap
(about 0.8 dB) is a property of the clip, not of the ROI: it is the same with
steering off. The tester's own chain gives the same result:
`pelorus_analyze_vulkan=roi=1` with and without `-pelorus_roi 1`, and with
`roi=0`, produce one packet MD5 per codec (`an-synth-banding-*` rows). Mesa
25.0.7 shows the same pattern (`out-image/results.tsv`).

The tester reports it the way the boundary demands: `steering_smoke` is
`not_run`, with `h264_vulkan (encoder reports it ignores the steering data:
pelorus_roi: device does not enable VK_KHR_video_encode_quantization_map; QP-map
steering disabled (pass-through).)` for the image's Mesa
(`tester-image/stdout.txt`).

### Why the map is not honoured

Hypothesis, then check:

1. *Mesa 25.0.7 has no map extension.* Confirmed: `vulkaninfo` lists
   `VK_KHR_video_encode_h264`, `_h265`, `_queue`, `video_maintenance1`, and not
   `VK_KHR_video_encode_quantization_map` (`vulkaninfo-radv-full.txt`). Patch
   0009 logs `device does not enable VK_KHR_video_encode_quantization_map`.
2. *Mesa 26.2.4 has the extension and feature, so the map should bind.*
   Falsified. FFmpeg enables the extension (`Using device extension
   VK_KHR_video_encode_quantization_map`, `videoEncodeQuantizationMap = true`),
   and the probe still disables the map:
   `pelorus_roi: no advertised quantization-map entry has format 14 with a
   fillable usage (TRANSFER_DST); disabled. Advertised: format 99 tiling 0/1/1000158000 usage 0x2000000 texel 16x16`
   (H.264; `64x64` for H.265). Format 14 is `R8_SINT`, format 99 is
   `R32_SINT`, usage `0x2000000` is `VIDEO_ENCODE_QUANTIZATION_DELTA_MAP`.
3. *RADV cannot make a fillable delta map.* Falsified by `qmprobe.c`. The
   advertised usages track the usage bits the caller asks for:

   | `imageUsage` in the query | Entries returned (H.264 and H.265) |
   | --- | --- |
   | `DELTA` | `R32_SINT`, 3 tilings, usages `DELTA_MAP` only |
   | `DELTA\|TRANSFER_DST` | `R32_SINT`, 3 tilings, `TRANSFER_DST DELTA_MAP` |
   | `DELTA\|STORAGE` | `R32_SINT`, 3 tilings, `STORAGE DELTA_MAP` |
   | `DELTA\|TRANSFER_DST\|STORAGE` | `R32_SINT`, 3 tilings, `TRANSFER_DST STORAGE DELTA_MAP` |

   Patch 0009 (`ffmpeg-patches/0009-vulkan-pelorus-qpmap.patch`, the
   `fmt_info.imageUsage = map_usage` assignment) queries with the map bit alone,
   so RADV answers with the map bit alone and the "fillable usage" test fails.
   Separately the patch requires `ctx->qpmap_format == R8_SINT`; RADV never
   advertises `R8_SINT` for a delta map, although
   `vkGetPhysicalDeviceImageFormatProperties2` accepts both `R8_SINT` and
   `R32_SINT` with `DELTA_MAP|TRANSFER_DST` and with `DELTA_MAP|STORAGE`
   (result 0). The map image is one byte per texel in the patch's host raster
   and its compute shader; RADV wants four.

So the failure is in Pelorus patch 0009's probe and format assumptions, not in
RADV's capability. The patch's own commit message records the symptom for this
driver ("RADV advertises only an R32_SINT map without a fillable usage"); the
probe shows the second half is a query artefact. No patch was changed here.

## Capability table (Mesa 26.2.4, `qmprobe-host.txt`)

| Property | H.264 main 4:2:0 8-bit | H.265 main 4:2:0 8-bit |
| --- | --- | --- |
| coded extent min / max | 128x128 / 4096x4096 | 130x128 / 8192x4352 |
| picture access and input granularity | 16x16 | 64x16 |
| `rateControlModes` | `DISABLED`, `CBR`, `VBR` | `DISABLED`, `CBR`, `VBR` |
| max bitrate, quality levels, layers | 1 000 000 000, 3, 4 | 1 000 000 000, 3, 4 |
| encode capability flags | `QUANTIZATION_DELTA_MAP` | `QUANTIZATION_DELTA_MAP` |
| `maxQuantizationMapExtent` | 256x256 | 128x68 |
| map texel block | 16x16 (one macroblock) | 64x64 (one CTB) |
| map QP delta range | [-51, 51] | [-51, 51] |
| map format and tilings | `R32_SINT`; 0, 1, `DRM_FORMAT_MODIFIER_EXT` | same |

`EMPHASIS_MAP` is not set. The emphasis query still returns one entry
(`format 1000156003`, texel 0x0), which is meaningless without the capability
flag; patch 0009 gates on the flag, so CBR and VBR emphasis steering is
correctly off.

## Unsupported on this stack

| Piece | Evidence |
| --- | --- |
| AV1 encode | No `VK_KHR_video_encode_av1` in `vulkaninfo`; `ffmpeg -c:v av1_vulkan` exits 8: `Unknown encoder 'av1_vulkan'` / `Error selecting an encoder`. The tester marks `av1_vulkan` "not built into this FFmpeg". |
| `VK_KHR_video_encode_quantization_map` on Mesa 25.0.7 | Absent from `vulkaninfo`; `pelorus_roi` logs the pass-through line above. |
| Pelorus delta-map steering on Mesa 26.2.4 | Probe disables it: format 14 expected, format 99 advertised, usage queried without a fill bit. |
| Emphasis map (CBR and VBR steering) | `VK_VIDEO_ENCODE_CAPABILITY_EMPHASIS_MAP_BIT_KHR` not set for either codec. |
| `-rc_mode driver` without a bitrate | Exit 234: `h264_vulkan: No bitrate specified!`, `Error while opening encoder`. With `-b:v 1M` it encodes. |
| `VK_LAYER_KHRONOS_validation` | Not in the image, so `format_matrix` is `not_run`. Validation on RADV was not measured. |
| AMF | Proprietary, no Pelorus patch, out of scope per #231. |

## Failing-first record

Before any claim, the image run on the AMD device with the image's Mesa reported
(`tester-image/stdout.txt`, report `kit: intel`, `execution_class: hardware`,
`evidence_claim: gpu`, `devices[0].driver: radv`):

| Stage | Result |
| --- | --- |
| `probe` | pass |
| `sidedata_roundtrip` | pass: `hevc_vulkan`, `h264_vulkan`, 4 frames each, blob in stream and decode tap, device 0 |
| `zero_copy_chain` | pass: native `h264_vulkan` has no hwupload, hwdownload or scale |
| `steering_smoke` | **not_run**: both Vulkan encoders ignore the steering data (reason above) |
| `format_matrix` | not_run: validation layer not installed |
| `libpelorus_suite`, `registration` | not_run: runners not in this kit version |

A claim of AMD steering support needs `steering_smoke` to pass on this device;
today it cannot.

## Open

- Fix patch 0009's probe: query the map format with
  `DELTA_MAP|TRANSFER_DST|STORAGE`, accept the format the driver advertises
  (`R32_SINT` on RADV, `R8_SINT` on NVIDIA), and write map texels at that
  width in both the compute shader and the host raster. Then repeat the
  steering table above on Mesa 26.2 or newer. This is the missing piece for a
  positive #231 result, tracked in
  [#278](https://github.com/VMAFx/pelorus/issues/278).
- An AMD image needs Mesa 26.2 or newer for the map extension; Debian trixie
  ships 25.0.7. The source (backports, a pinned Mesa build, or the host)
  needs its own decision and licence audit before an image.
- RADV bitrate accuracy, B-frames, 4:2:2 and 10-bit encode, and validation
  layer cleanliness were not measured.
- Other RDNA 2+ parts (a discrete card) may differ in map texel size and
  extent.

## Fixed (2026-10-09, #278)

[ADR-0182](../adr/0182-vulkan-qpmap-fill-paths.md) fixes the probe as the
first Open item proposes, with one finding this spike missed: RADV's encode
queue family has `VK_QUEUE_VIDEO_ENCODE_BIT_KHR` only, so it may record
neither the transfer copy nor the compute store. Patch 0009 now fills RADV's
`R32_SINT` map from the host through a mapped `LINEAR` image. The steering
table above, rerun on a host build with Mesa 26.2.4 (same fixtures and
commands):

| Clip | Codec | Left dB, off → `-0.3` | Left dB, off → `+0.3` | Bytes, off → `-0.3` / `+0.3` |
| --- | --- | --- | --- | --- |
| detail | h264_vulkan | 36.68 → 44.82 | 36.68 → 31.70 | 91499 → 1084250 / 84025 |
| detail | hevc_vulkan | 36.80 → 45.41 | 36.80 → 32.39 | 103003 → 1197268 / 87514 |
| synth-banding | h264_vulkan | 54.06 → 60.79 | 54.06 → 40.97 | 2717 → 5120 / 2695 |
| synth-banding | hevc_vulkan | 58.80 → 65.75 | 58.80 → 47.05 | 2279 → 5655 / 2146 |

Every steered stream differs from its control and decodes to 24 of 24
frames; on the detail clip the right half stays within 0.2 dB. The
validation layer reports the same VUIDs with and without `-pelorus_roi`.
Mesa 25.0.7 still passes through with the "device does not enable" warning.
ADR-0182 "Measured" has packet MD5s, the right half and the NVIDIA
regression run.

## Reproduce

```bash
# local evidence directory, git-ignored; needs docker, the image, host Mesa RADV, gcc
.workingdir/evidence/amd-radv-spike/run.sh
```

Outputs: `out-image/` and `out-host/` (logs, streams, `results.tsv`,
`exit.tsv`, `device.txt`), `qmprobe-host.txt`, `vulkaninfo-radv-*.txt`,
`tester-image/`.
