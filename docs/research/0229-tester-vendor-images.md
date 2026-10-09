<!-- markdownlint-disable MD013 MD060 -->
# NVIDIA and Intel tester images on one workstation

**Date:** 2026-10-09

**Decision:** input to [ADR-0180](../adr/0180-tester-vendor-images.md),
[#229](https://github.com/VMAFx/pelorus/issues/229) and
[#230](https://github.com/VMAFx/pelorus/issues/230)

**Scope:** `tools/tester/Containerfile` targets `final-nvidia` and `final-intel`
built locally at `-j4` from the pinned FFmpeg `n9.0.2` plus the patch stack,
Debian trixie (`debian:trixie-slim` digest from the Containerfile). Host: kernel
7.2.9, Docker 29.8.2 (rootful, cgroup v2, systemd driver), NVIDIA Container
Toolkit 1.20.1 with a CDI specification in `/var/run/cdi`, NVIDIA driver
615.71.09 on an RTX 4090, Arc A380 on the `xe` kernel driver, Radeon 610M on
`amdgpu`. Inside the Intel image: Mesa 25.0.7, `intel-media-va-driver-non-free`
25.2.3+ds1-1, `libmfx-gen1.2` 25.1.4, `libvpl2` 2.14.0.

## Verdict

| Kit | Without its device | With it | Licence gate |
| --- | --- | --- | --- |
| `nvidia` | exit 0, every GPU stage `no_device`: "start it with --gpus all" | **pass**, `execution_class` hardware, `evidence_claim` gpu; steering passes on `h264_nvenc`, `hevc_nvenc`, `av1_nvenc` and `hevc_vulkan` | planted `libnvidia-encode.so.1` refused |
| `intel` | exit 0, lavapipe only (`software_vulkan`), every GPU stage `no_device`: "start it with --device /dev/dri" | **pass** on the A380, `execution_class` hardware, `evidence_claim` gpu: `h264_qsv` and `hevc_qsv` steering change the bitstream, the side-data round trip passes on both | a planted second non-free package (`unrar`) refused; the recorded `intel-media-va-driver-non-free` accepted (below) |

All reports validate (schema 2, `--kit` checked). The NVIDIA kit meets the
positive acceptance of #229 on this host. The Intel kit meets the QSV steering
acceptance of #230 for `h264_qsv` and `hevc_qsv` with the non-free media driver
(below). The first revision of this note measured the free driver and a local
non-free build separately; the table and sections below keep both columns.

## Device access

- `--device /dev/dri` passes every DRM node of the host: `card0`..`card2` and
  `renderD128`..`renderD130` were all present in the container. An earlier
  observation of only `card0` did not reproduce; `--gpus all` alone, through
  the CDI specification, adds only the NVIDIA GPU's `card1` and `renderD128`.
- The render nodes are mode 0666 on this host and the container runs as root,
  so no `--group-add` was needed. With `--user`, a host whose render nodes are
  0660 needs `--group-add <gid of /dev/dri/renderD*>`; the report names it when
  the nodes are present but not readable (planted case only; the nodes here are
  readable by everyone). A run with `--user "$(id -u):$(id -g)" --group-add
  <gid>` first crashed: the fixture cache defaulted to `/.cache`, which an
  arbitrary user cannot create. The images now set `HOME=/tmp`, and an
  unwritable cache is a named stage failure instead of a traceback; the
  `--user` runs of both kits then match the root runs.
- `--gpus all` through CDI mounts `libcuda`, `libnvidia-encode` and
  `/etc/vulkan/icd.d/nvidia_icd.json` whatever `NVIDIA_DRIVER_CAPABILITIES`
  says: the ICD was present with `compute,utility,video` and with
  `compute,utility` alike. The toolkit documentation lists `graphics` as
  "required for rendering OpenGL, EGL, and Vulkan applications" for the
  legacy path, so the image sets `compute,utility,video,graphics`.

## The NVIDIA Vulkan ICD needs two Debian libraries

With the ICD mounted, `vulkaninfo` first failed with `libXext.so.6: cannot open
shared object file`, then, with `libxext6` installed, with `Could not get
'vkCreateInstance' via 'vk_icdGetInstanceProcAddr' for ICD libGLX_nvidia.so.0`.
`LD_DEBUG=libs` showed the driver opening `libEGL.so.1`, the GLVND dispatcher,
and not finding it. With `libegl1` (and its dependency `libglvnd0`) installed,
`vulkaninfo` lists the RTX 4090. The NVIDIA kit therefore installs `libegl1`
and `libxext6` from Debian `main`; neither is an NVIDIA file.

When the device and the ICD are present but the driver does not load, the report
now records the probe as `fail` with the loader's message, not `no_device`: no
docker option is missing in that case.

## NVIDIA results (RTX 4090, `--gpus all`)

| Leg | Steering smoke | Side-data round trip |
| --- | --- | --- |
| `h264_nvenc` | pass | pass |
| `hevc_nvenc` | pass | `not_run`: `-udu_sei 1` ends with `Cannot allocate memory` (below) |
| `av1_nvenc` | pass | no AV1 carrier |
| `hevc_vulkan` | pass | pass |
| `h264_vulkan` | `not_run`: the driver clamps negative QP deltas to 0 (the encoder says so) | pass |
| `av1_vulkan` | `not_run`: not built (needs Vulkan headers 1.4.317, trixie has 1.4.309) | none |

`zero_copy_chain` passes on the native `h264_vulkan` leg. `format_matrix` is
`not_run` because the image has no validation layer. Run time about 40 s after
the fixture is rendered.

`av1_nvenc` first came out as `not_run` "baseline output does not decode": the
image's FFmpeg had no software AV1 decoder, so the check could not decode any
AV1 stream. The NVIDIA and Intel kits now link dav1d (`libdav1d7`,
BSD-2-Clause, Debian `main`) so AV1 output is decoded in software,
independently of the encoder's vendor.

`hevc_nvenc -udu_sei 1` fails with `Cannot allocate memory` when
`pelorus_analyze_vulkan` writes its per-cell maps (the default since ADR-0177)
and succeeds with `maps=0`; `h264_nvenc` succeeds either way. Reproduce inside
the image:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk -f rawvideo -pix_fmt yuv420p -s 640x360 \
  -i src.yuv -frames:v 4 -vf "format=nv12,hwupload,pelorus_analyze_vulkan,pelorus_deband_vulkan,hwdownload,format=nv12" \
  -c:v hevc_nvenc -udu_sei 1 -f hevc out.hevc      # fails; with pelorus_analyze_vulkan=maps=0 it passes
```

The side-data stage records a carrier that cannot encode as a `not_run` leg
with the error line, so this shows in the report but does not fail it.

## Intel results (Arc A380 on `xe`, `--device /dev/dri`)

The Vulkan devices in the container were the A380 (ANV), the Radeon 610M (RADV)
and lavapipe. Findings, in the order they appeared:

1. **QSV opened the NVIDIA render node.** FFmpeg asks for a VA-API device with
   `vendor_id 0x8086`, but it filters render nodes by vendor only when built
   with libdrm. Without it, it tried `renderD128` (the RTX 4090), and iHD
   refused it (`unsupported drm device by media driver: nvid`). The Intel kit
   now builds with `--enable-libdrm`; QSV then opens the A380.
2. **ANV cannot open `h264_vulkan` here** (`Error while opening encoder`,
   Mesa warns that DG2 on `xe` is experimental), so the Vulkan legs ran on
   RADV, where patch 0009 logs `pelorus_roi: device does not enable
   VK_KHR_video_encode_quantization_map; QP-map steering disabled
   (pass-through)`. The steering rule did not recognise that statement and
   failed the legs; it now treats the patch's own `pelorus_roi: ... disabled`
   lines as a self-report (`not_run` with the line), with a planted case.
3. **QSV with the free media driver.** Unsteered and steered 8-frame encodes,
   decoded back with FFmpeg:

| Encoder | Free `intel-media-va-driver` 25.2.3 | Non-free 25.2.3 (the driver of the published kit) |
| --- | --- | --- |
| `h264_qsv` | encodes, 8 frames; with `-pelorus_roi` on and off byte-identical (both arms carried ROI side data, see 4) | same |
| `hevc_qsv` | every encode fails: `Invalid FrameType:0` | encodes, 8 frames; steered 6390 B against 2466 B unsteered |
| `av1_qsv` | encodes, 8 frames (no `-pelorus_roi`, so its leg is `not_run`) | same |

HEVC encode on this GPU needs the non-free media driver, which the published
Intel image now ships (decision 4a of ADR-0180); with only the free driver
installed (a host run, or an image built by hand) both `hevc_qsv` legs are
`not_run` and name the installed free driver and the missing non-free one
(read with `dpkg-query`). The non-free HEVC result (more than double the
bytes at the same `-q:v`) matches the Arc A-series low-power encode defect
recorded in [bench results](../development/bench-results.md); the A380 exposes
only the low-power entry point, so these runs prove the path, not quality.

### `h264_qsv` ROI works; the control arm was steered too

The first runs compared `-pelorus_roi 0` against `-pelorus_roi 1` with
`pelorus_analyze_vulkan=roi=1` in both arms. Patch 0005 keeps the dense map
for HEVC, so `h264_qsv` takes FFmpeg's stock `mfxExtEncoderROI` path in both
arms (`ffmpeg-patches/0005-qsv-pelorus-roi.patch`, the `!use_mbqp` branch
that calls `set_roi_encode_ctrl`), and the outputs matched. Varying the side
data instead, 8 frames at `-q:v 30`:

| `h264_qsv` arm | Free driver | Non-free driver |
| --- | --- | --- |
| `roi=0`, `-pelorus_roi 0` (no side data) | 1343 B | 1569 B |
| `roi=1`, `-pelorus_roi 0` (256 `ROI:` debug lines) | 2736 B | 3253 B |
| `roi=1`, `-pelorus_roi 1` | 2736 B, identical to the line above | 3253 B, identical |

So DG2 honours the ROI rectangles with either driver; neither the driver nor
patch 0005 was at fault. The unsteered encodes now run
`pelorus_analyze_vulkan=roi=0`; with that control the `h264_qsv` steering leg
passes on the A380 with either driver.

## The non-free media driver is redistributable

The first revision of this note, and ADR-0180 as first written, treated
`intel-media-va-driver-non-free` as not redistributable and confined it to a
local build with a NOT-FOR-REDISTRIBUTION marker. The maintainer corrected that
on 2026-10-09 and the Debian copyright file supports it. `debian/copyright` of
`intel-media-driver-non-free` 25.2.3+ds1-1
(<https://sources.debian.org/data/non-free/i/intel-media-driver-non-free/25.2.3+ds1-1/debian/copyright>,
read 2026-10-09): `Files: *`, `License: Expat`; only
`media_driver/linux/ult/ult_app/googletest/*` is BSD-3-clause (test code); the
header comment says the kernels move to `non-free` "as they come without
source, i.e. we cannot rebuild them with intel-gen4asm (or similar)". The
package sits in `non-free` for lack of kernel source, not for its terms.
Expat needs no source offer. The ban on FFmpeg `--enable-nonfree`
(`cuda-nvcc`, `cuda-sdk`, `libnpp`, `fdk-aac`, `decklink`, `libmpeghdec`) stays,
because those make the FFmpeg binary non-redistributable.

`docker build --target final-intel` (no build argument any more) on this host:
the licence stage records the driver as a component with `archive_component:
non-free` and its reason and accepts the tree (`licensing: image tree
accepted`, self-test 31 planted defects refused); `dpkg-query` in the image
lists `intel-media-va-driver-non-free:amd64 25.2.3+ds1-1` and no free driver.
`docker run --rm --device /dev/dri` on the A380: verdict pass, report kit
`intel`, `execution_class` hardware, `evidence_claim` gpu, the report validates
with `--kit intel`; `h264_qsv` and `hevc_qsv` steering change the bitstream at 8
and 16 frames, and the side-data round trip passes on `hevc_qsv` and
`h264_qsv`.

| Attempt | Result |
| --- | --- |
| `unrar` (Debian `non-free`) added to the assembled Intel stage, `--target final-intel` | build exit 1: `debian package unrar: archive component non-free is not permitted (['main'])` |
| `--target source-intel` | build exit 0; `/source/debian` holds `intel-media-driver-non-free_25.2.3+ds1-1.dsc`, `.orig.tar.xz` and `.debian.tar.xz`, and `installed-sources.txt` lists `intel-media-driver-non-free=25.2.3+ds1-1` |
| FFmpeg `--enable-nonfree` through `FFMPEG_LICENCE_FLAGS` | `--target build-intel`, build exit 1: `check-ffmpeg-licence: configure line has --enable-nonfree, which this image never ships` |
| the non-free package or the `non-free` area in any other stage, `contrib`, a second non-free package in the Intel kit | refused by `check-build-config.py` (planted cases in its self-test) |

## Licence gate proofs

Each planted image was built from the kit's `assembled-<kit>` stage and fed to
`--target final-<kit>` with `--build-context assembled-<kit>=docker-image://...`:

| Planted | Kit | Gate output |
| --- | --- | --- |
| empty `/usr/lib/x86_64-linux-gnu/libnvidia-encode.so.1` | nvidia | `forbidden file: usr/lib/x86_64-linux-gnu/libnvidia-encode.so.1`, `unrecorded file`; build exit 1 |
| `unrar` from `non-free`, beside the recorded media driver | intel | `debian package unrar: archive component non-free is not permitted (['main'])`, `unrecorded file: usr/bin/unrar`; build exit 1 |

The master gate, given the same planted trees in its self-test harness, named
neither defect (`.workingdir/evidence/229-vendor-images/vendor-failing-first-licensing.txt`,
local).

## Build cost

Cold builds at `-j4` on a loaded 32-core host: NVIDIA kit 3 min 55 s, Intel
kit 3 min 43 s. Image sizes: NVIDIA 965 MB, Intel 1.06 GB on disk.

## Not measured here

- The AV1 boundary on hardware: the RTX 4090 and the A380 both encode AV1, so
  "no AV1 encode on this GPU or driver" is proven by planted logs only.
- WSL2 (`/dev/dxg`, `/usr/lib/wsl/lib`) for either vendor.
- A legacy (non-CDI) NVIDIA Container Toolkit, where `graphics` matters.
- QSV on Arc B-series and Xe-LP (B580, UHD 770), where HEVC may encode with the
  free driver and the low-power defect is absent.
