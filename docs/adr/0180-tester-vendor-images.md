<!-- markdownlint-disable MD013 MD060 -->
# ADR-0180: Tester images per kit: NVIDIA ships no NVIDIA file and builds NVENC from the MIT headers only, Intel ships Debian main's free media stack, and a report without its device names the missing docker option

- **Status**: Proposed
- **Implementation**: pending (#229)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: tester, licensing, supply-chain, ci, nvidia, intel

## Context

[ADR-0173](0173-tester-programme.md) plans NVIDIA and Intel tester images for
0.4 and fixes the licence rules: no vendor driver file in an image, FFmpeg
GPL-3.0-or-later and never nonfree. [ADR-0178](0178-tester-artifact-licence-record.md)
gates every file of the image against a licence record and expects the vendor
images to add their components and terms. Until now `tools/tester/Containerfile`
built one CPU image. Its FFmpeg had no NVENC or QSV, so the steering, side-data
and NVENC film-grain paths could not run inside an image.

The vendor stacks differ in where the driver lives. NVENC is loaded at run time
through the `nv-codec-headers` loader (MIT); the driver, its encoder library
and the Vulkan ICD are the host's and reach the container through the NVIDIA
Container Toolkit. QSV needs the oneVPL dispatcher and GPU runtime, a VA-API
media driver and, for the Pelorus filters, a Vulkan driver (Mesa ANV). All of
these are user-mode libraries in Debian `main`, except the media driver build
that Debian ships in `non-free` with closed kernels.

Measured on the maintainer's workstation (RTX 4090, Arc A380 on the `xe`
kernel driver, Radeon iGPU; Docker 29.8.2, NVIDIA Container Toolkit 1.20.1):
`--gpus all` goes through the toolkit's CDI specification, which mounts
`libcuda`, `libnvidia-encode` and the Vulkan ICD whatever
`NVIDIA_DRIVER_CAPABILITIES` says, and adds the NVIDIA GPU's `card1` and
`renderD128` to `/dev/dri`. `--device /dev/dri` passes every DRM node of the
host. The toolkit documentation lists `graphics` as "required for rendering
OpenGL, EGL, and Vulkan applications" for the legacy (non-CDI) path
([research 0229](../research/0229-tester-vendor-images.md)).

## Decision

**1. One image per kit.** `tools/tester/Containerfile` builds three kits from
one shared stage (toolchain, libpelorus, the patched FFmpeg tree): `generic`
(CPU, no GPU driver), `nvidia` and `intel`. Each kit has its own FFmpeg build,
licence gate stage, image and `-source` image (targets `final-<kit>` and
`source-<kit>`). The FFmpeg configure line differs by feature flags only:
`tools/tester/build-ffmpeg.sh` takes the licence flags from the shared stage,
refuses a kit flag that touches the licence, and runs the FFmpeg licence gate
after its planted-flag self-test on every kit.

**2. Tags.** The generic image keeps `tester-<YYYYMMDD>-<sha8>`; a vendor image
is `tester-<kit>-<YYYYMMDD>-<sha8>`, the same order as the Windows zip name of
ADR-0173 decision 1. Each has a `-source` tag. This replaces the suffix form
`tester-<date>-<sha8>-nvidia` of ADR-0173 decision 2 for the vendor images.

**3. NVIDIA kit.** FFmpeg is built with `--enable-ffnvcodec --enable-nvenc`
against `nv-codec-headers` `n12.1.14.0` (commit `1889e62e`, MIT), the floor
FFmpeg n9 accepts (Linux driver 530.41.03 or newer). The headers' copyright and
permission notices ship in the image; their tree is in the `-source` image. No
NVIDIA file is in the image: no CUDA SDK, no `cuda-nvcc`, no driver package.
The host's Vulkan ICD needs `libXext.so.6` and the GLVND `libEGL.so.1`, so the
image installs Debian's `libxext6` and `libegl1`.
The image sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility,video,graphics` so a
legacy toolkit mounts the Vulkan ICD too, and adds `/usr/lib/wsl/lib` to the
library path for Docker Desktop on WSL2 (unproven). Run line:
`docker run --rm --gpus all -v "$PWD/report:/report" <image>`.

**4. Intel kit.** The image installs `mesa-vulkan-drivers` (ANV), `libvpl2`,
`libmfx-gen1.2`, `libva2`, `libva-drm2` and the free `intel-media-va-driver`,
all from Debian `main`; FFmpeg adds `--enable-libvpl --enable-vaapi
--enable-libdrm --disable-xlib` (libdrm makes QSV pick the Intel render node by
vendor id instead of `renderD128`). On the Arc A380 the free driver cannot
encode HEVC (`Invalid FrameType:0`), so `hevc_qsv` is a `not_run` leg there;
the free-only rule stands. Run line: `docker run --rm --device /dev/dri -v
"$PWD/report:/report" <image>`; a non-root `--user` also needs `--group-add`
with the group of `/dev/dri/renderD*`.

**5. Forbidden files and packages.** `licensing.json` gains `kits` (a component
may name the kits it belongs to) and `forbidden`: NVIDIA driver and CUDA file
names (`libcuda.so*`, `libnvidia-*.so*`, `nvidia_icd.json`, ...) and packages
(`intel-media-va-driver-non-free`, `nvidia-*`, `libnvidia-*`, `libcuda*`, ...).
A forbidden file or package fails `licensing.py check --kit <kit>` whoever owns
it, a dpkg package or a component glob, on top of the existing `main`-only
archive rule. `check-build-config.py` refuses a vendor driver, CUDA or non-free
package in any `apt-get install` of the Containerfile and a non-free or contrib
archive component.

**AV1 decode.** The NVIDIA and Intel kits link dav1d (`libdav1d7`,
BSD-2-Clause, Debian `main`; `--enable-libdav1d`) so the steering smoke decodes
the AV1 output of `av1_nvenc` and `av1_qsv` in software, independently of the
encoder's vendor; FFmpeg's native AV1 decoder needs a hardware accelerator.

**6. Report.** The image sets `PELORUS_TESTER_KIT`; the report records it as
`kit` (optional field, schema version 2 unchanged; absent means `source`). A
GPU stage without its device is `no_device` and its reason names the docker
option the kit lacks: `--gpus all` (NVIDIA; `NVIDIA_DRIVER_CAPABILITIES` when
the device nodes are present but no Vulkan driver was mounted) and `--device
/dev/dri` (Intel; `--group-add` when the render nodes are present but not
readable). The validator refuses a vendor-kit report whose `no_device` reason
names none of them. The steering and side-data stages record one `legs` entry
per encoder (`pass`, `fail` or `not_run` with a reason); the steering smoke
tries `h264_nvenc`, `h264_qsv`, `hevc_qsv` and `av1_qsv` besides the earlier
encoders. An AV1 encoder whose first encode fails as unsupported is a `not_run`
leg reading "no AV1 encode on this GPU or driver", and an encoder without
`-pelorus_roi` (today `av1_qsv`) is a `not_run` leg naming the option. A stage
cannot pass with a failed leg or without a passing one.

**7. Workflow.** `tester-publish.yml` runs `build` and `publish` once per kit
(matrix, `fail-fast: false`, so a failing kit never cancels another kit's job
between its push and its signature). Every kit gets the same SBOM, provenance,
cosign, verification and anonymous-pull steps; both jobs run the documented
command without a device and validate the report against its kit.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| One FFmpeg with NVENC and QSV in every kit | One build, one binary across kits | The generic and NVIDIA images would carry libvpl and libva for nothing; the NVIDIA headers' notice in the Intel image | Ship only what the report needs (ADR-0173 decision 5); the kit flags differ, the licence flags do not |
| NVIDIA image on `nvidia/cuda` runtime base | NVIDIA's documented path | Ships CUDA runtime files under the CUDA EULA; vmafx ADR-1503 shows the cost | No NVIDIA file in the image (ADR-0173) |
| `nv-codec-headers` `n13.0.19.0` (what CI's Ubuntu ships) | Newest NVENC features (SDK 13) | Needs Linux driver 570 or newer; testers on older drivers get no NVENC | FFmpeg n9's floor `n12.1.14.0` covers drivers from 530 and Ada AV1 NVENC; a later bump is one ARG |
| Debian `libffmpeg-nvenc-dev` instead of the git pin | No git fetch | Version moves with Debian point releases; its source would not reach the `-source` image | A pinned commit, verified in the build, with its tree in `-source` |
| `intel-media-va-driver-non-free` | Encode on some older Intel generations | Debian `non-free`; closed kernels | Free variant only; the gate forbids the package |
| AV1 decode through NVDEC or VA-API in the check | No new library | The judge would share the encoder's vendor stack; differs per kit | dav1d decodes the same way on every kit |
| Name the Intel render node in the steering commands instead of linking libdrm | No new library | The node number differs per host and the tester would have to find it; FFmpeg's own vendor filter needs libdrm | libdrm (MIT, already pulled in by Mesa and libva) lets FFmpeg pick the Intel node |
| Tags `tester-<date>-<sha8>-<kit>` (ADR-0173 wording) | Matches the ADR-0173 text | `-source` then reads `...-nvidia-source`, kit last; differs from the Windows zip order | Kit first, like `tester-windows-<date>-<sha8>`; generic unchanged |
| One reason naming both options for every kit | No kit awareness in the report | A tester with an NVIDIA image is told about `/dev/dri`; intake cannot check it | Kit-specific reason, enforced by the validator |

## Consequences

- **Positive**: NVENC and QSV steering, side-data and AV1 legs run inside an
  image; a run without the device names the exact docker option; a planted
  NVIDIA library or the non-free media driver fails the build; each leg is
  machine-readable at intake.
- **Negative**: three FFmpeg builds per workflow run (hosted minutes; the nightly
  builds all three); the Intel image carries Mesa's whole Vulkan driver package
  (RADV, lavapipe and others come with ANV); the NVIDIA image cannot use NVENC on
  drivers older than 530.41.03; with the free media driver an Arc A-series GPU
  gives no HEVC QSV leg, and the A380 gives no positive QSV steering result at
  all (research 0229).
- **Neutral / follow-ups**: the AV1 boundary on a pre-Ada NVIDIA or pre-Arc Intel
  GPU and the WSL2 paths are not measured here (research 0229 lists them);
  `qsv-roi-regression.sh` stays a build-tree test, not an image stage; the AMD
  image (#231) can reuse the Intel kit's Mesa stack.

## References

- Issues [#229](https://github.com/VMAFx/pelorus/issues/229) and [#230](https://github.com/VMAFx/pelorus/issues/230), epic [#108](https://github.com/VMAFx/pelorus/issues/108); [research 0229](../research/0229-tester-vendor-images.md); [research 0172](../research/0172-tester-programme.md) T6, T7.
- NVIDIA Container Toolkit, specialized configurations: `NVIDIA_DRIVER_CAPABILITIES` (read 2026-10-09).
- VMAFx `docker/Dockerfile.tester` targets `final-cuda` and `final-sycl` (read-only precedent).
- Source: per user direction in the vendor-images task brief (paraphrased): one image per kit as Containerfile targets, kit-first tags with `-source`, no NVIDIA library in the image, the free media driver only, and a `no_device` report that names the missing docker option.
