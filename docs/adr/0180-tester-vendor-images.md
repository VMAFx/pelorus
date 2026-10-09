<!-- markdownlint-disable MD013 MD060 -->
# ADR-0180: Tester images per kit: NVIDIA ships no NVIDIA file and builds NVENC from the MIT headers only, Intel ships Debian's media stack with the Expat-licensed non-free media driver, and a report without its device names the missing docker option

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
that Debian ships in `non-free` because Intel's GPU kernels come without source;
the package itself is Expat-licensed (decision 4a).

Measured on the maintainer's workstation (RTX 4090, Arc A380 on the `xe`
kernel driver, Radeon iGPU; Docker 29.8.2, NVIDIA Container Toolkit 1.20.1):
`--gpus all` goes through the toolkit's CDI specification, which mounts
`libcuda`, `libnvidia-encode` and the Vulkan ICD whatever
`NVIDIA_DRIVER_CAPABILITIES` says, and adds the NVIDIA GPU's `card1` and
`renderD128` to `/dev/dri`. `--device /dev/dri` passes every DRM node of the
host. The toolkit documentation lists `graphics` as "required for rendering
OpenGL, EGL, and Vulkan applications" for the legacy (non-CDI) path
([research 0229](../research/0229-tester-vendor-images.md)).

The A380 stays on the `xe` kernel driver, which loads no HuC firmware on DG2.
The media driver's low-power encoder runs its bitrate control on HuC, so every
hardware bitrate-control mode (CBR, VBR, ICQ) fails there with every media
driver. oneVPL can do the bitrate control itself instead (`ExtBRC`, FFmpeg
`-extbrc 1`): it picks each frame's QP on the CPU and drives the driver in
constant QP. On the A380 that gives `h264_qsv` and `hevc_qsv` CBR within 2 % of
the target in the Intel image; `av1_qsv` gets no software bitrate control
([research 0180](../research/0180-qsv-bitrate-control-dg2-xe.md)).

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
`libmfx-gen1.2`, `libva2`, `libva-drm2` and `intel-media-va-driver-non-free`;
all but the media driver come from Debian `main` (decision 4a). FFmpeg adds `--enable-libvpl --enable-vaapi
--enable-libdrm --disable-xlib` (libdrm makes QSV pick the Intel render node by
vendor id instead of `renderD128`). On the Arc A380 bound to the `xe` kernel
driver, which loads no HuC firmware on DG2, the free driver cannot encode HEVC in
any rate-control mode (`Invalid FrameType:0`); the non-free build encodes HEVC
with constant QP. Without HuC no media driver encodes with hardware bitrate
control (CBR, VBR, ICQ) on DG2, so the steering and side-data legs use constant
QP; decision 8 adds bitrate-control legs. The A380 with HuC loaded is not
measured. A run that finds
only the free `intel-media-va-driver` installed (a host run, or an image built
by hand) gets `not_run` `hevc_qsv` legs whose reason names the installed free
driver and the missing `intel-media-va-driver-non-free`, both read from dpkg.

**4a. The Intel kit ships `intel-media-va-driver-non-free`.** The maintainer
corrected an earlier decision on 2026-10-09: the package is redistributable.
Debian ships it in the archive area `non-free`, but not because of its licence:
the `debian/copyright` of `intel-media-driver-non-free` 25.2.3+ds1-1 lists every
file as Expat (the bundled googletest, which is test code, as BSD-3-Clause) and
says the package is in `non-free` because "those kernels ... come without
source", so Debian cannot rebuild them. Expat needs no source offer. The
published Intel image therefore installs it, which gives `hevc_qsv` steering
and side-data legs on the measured Arc A380 (`xe` kernel driver, no HuC); other
Arc A-series configurations, and the free driver with HuC loaded, are not
measured.

The licence record admits it as an exception that names one package, not as a
tier: a `dpkg` component may carry `archive_component` (`non-free`, `contrib`
or `non-free-firmware`) and `archive_reason`, and `licensing.py check` then
accepts exactly that component's packages from that archive area, and the
matching `Components:` word in the apt sources, provided the component's
licence is redistributable. The Intel kit records `intel-media-va-driver-non-free`
as `MIT AND BSD-3-Clause`, source <https://github.com/intel/media-driver> and the
Debian source package. Any other package from `non-free` or `contrib`, and the
recorded package in a kit that does not record it, still fails the gate. The
`-source` image fetches the driver's Debian source package with `deb-src` of
`main non-free`. The earlier `INTEL_MEDIA_DRIVER` build argument, the
`intel-nonfree-local` kit and the NOT-FOR-REDISTRIBUTION marker are removed.

What stays banned is unchanged: FFmpeg `--enable-nonfree`, `--enable-cuda-nvcc`,
`--enable-cuda-sdk`, `libnpp`, `fdk-aac`, `decklink` and `libmpeghdec`, which
make the FFmpeg binary non-redistributable (ADR-0173 decision 5), and every
NVIDIA file. `check-build-config.py` lets the Containerfile name the one package
and enable `non-free` only in the stages `assembled-intel` and
`debian-sources-intel`; the same words anywhere else, `contrib`, and any second
non-free package are refused, each with a planted case in its self-test.

**5. Forbidden files and packages.** `licensing.json` gains `kits` (a component
may name the kits it belongs to) and `forbidden`: NVIDIA driver and CUDA file
names (`libcuda.so*`, `libnvidia-*.so*`, `nvidia_icd.json`, ...) and packages
(`nvidia-*`, `libnvidia-*`, `libcuda*`, ...).
A forbidden file or package fails `licensing.py check --kit <kit>` whoever owns
it, a dpkg package or a component glob, on top of the existing `main`-only
archive rule. `check-build-config.py` refuses a vendor driver, CUDA or non-free
package in any `apt-get install` of the Containerfile and a non-free or contrib
archive component, except the one package and stages named in decision 4a.

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
cannot pass with a failed leg or without a passing one. The unsteered control
encodes run without region-of-interest side data (`pelorus_analyze_vulkan=roi=0`),
because FFmpeg's stock QSV path applies ROI rectangles whether or not
`-pelorus_roi` is set; with side data in the control, `h264_qsv` looked unsteered.

**7. Workflow.** `tester-publish.yml` runs `build` and `publish` once per kit
(matrix, `fail-fast: false`, so a failing kit never cancels another kit's job
between its push and its signature). Every kit gets the same SBOM, provenance,
cosign, verification and anonymous-pull steps; both jobs run the documented
command without a device and validate the report against its kit.

**8. QSV bitrate-control legs.** For each QSV encoder in the kit's FFmpeg, the
steering stage adds two legs, marked with `rate_control` in the report (an
optional leg field; schema version 2 unchanged; tool 0.4.5):

- `cbr_extbrc`: CBR with `-extbrc 1`, oneVPL's software bitrate control.
- `cbr_hw`: CBR with the hardware's own bitrate control.

Both encode the `synth-motion` fixture (FFmpeg `testsrc2`, 640x360, 50 frames
at 25 fps, pinned by hash) eight times over, 400 frames, through
`pelorus_deband_vulkan` at 1 Mb/s with a 1 Mb buffer (`-b:v`, `-maxrate` and
`-bufsize` all `1000k`). A leg passes when all 400 frames decode and the
bitrate is within 15 % of the target. A `cbr_extbrc` encode must also log
`ExtBRC: ON`; for H.264 and HEVC that means software bitrate control, because
oneVPL then opens the driver in constant QP (vpl-gpu-rt
`hevcehw_base_va_lin.cpp`, `mfx_h264_encode_hw.cpp`). `av1_qsv` gets a named
`not_run` `cbr_extbrc` leg without an encode: oneVPL applies `ExtBRC` to AV1
only through its look-ahead tools (`LookAheadDepth` above 0,
`IsSwEncToolsImplicit` in `av1ehw_base_enctools_com.h`), which `av1_qsv` does
not enable by default, and on
the A380 the AV1 encode with `-extbrc 1` failed like hardware bitrate control,
with and without look-ahead.

When a bitrate-control encode fails, the leg runs a constant-QP encode of the
same input. If that fails too, the encoder is not usable on this host (a named
`not_run`, as in the steering legs). If it works:

- a failed `cbr_extbrc` encode is a `fail`;
- a failed `cbr_hw` encode is a named `not_run` when the encoder logs the
  no-HuC failure (`Invalid FrameType:0`, `GPU Hang (-21)` or
  `device failed (-17)`) and an Intel render node in the container is a DG2
  (PCI id `0x56xx`) bound to `xe`, read from `/sys/class/drm`. The reason
  reads "hardware bitrate control needs HuC firmware, and the xe kernel driver
  loads none on DG2 (Arc A-series)". When `/sys` names no Intel render node
  the same failure is a `not_run` that says the cause is not confirmed;
- a failed `cbr_hw` encode where FFmpeg logs `Selected ratecontrol mode is
  unsupported` is a named `not_run` (the driver offers no hardware bitrate
  control, as with a media driver that sees no HuC under `i915`);
- any other failed `cbr_hw` encode, and the no-HuC failure on any other
  GPU or kernel driver, is a `fail`.

A bitrate-control leg can fail the steering stage but cannot pass it alone: the
stage still needs a passing steering leg, and the validator refuses a passing
stage whose only passing legs carry `rate_control`. Each new rule has a planted
case (`brc_bitrate`, `brc_decode`, `brc_extbrc_engaged`, `brc_no_huc`,
`brc_hw_refused`) that fails the self-test when disabled.

On DG2 under `xe` each `cbr_hw` leg waits until oneVPL gives up, up to about
25 seconds (the A380 run's steering stage took 30 seconds in all). The leg runs
anyway: it is how a run notices a kernel that loads HuC on DG2.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| One FFmpeg with NVENC and QSV in every kit | One build, one binary across kits | The generic and NVIDIA images would carry libvpl and libva for nothing; the NVIDIA headers' notice in the Intel image | Ship only what the report needs (ADR-0173 decision 5); the kit flags differ, the licence flags do not |
| NVIDIA image on `nvidia/cuda` runtime base | NVIDIA's documented path | Ships CUDA runtime files under the CUDA EULA; vmafx ADR-1503 shows the cost | No NVIDIA file in the image (ADR-0173) |
| `nv-codec-headers` `n13.0.19.0` (what CI's Ubuntu ships) | Newest NVENC features (SDK 13) | Needs Linux driver 570 or newer; testers on older drivers get no NVENC | FFmpeg n9's floor `n12.1.14.0` covers drivers from 530 and Ada AV1 NVENC; a later bump is one ARG |
| Debian `libffmpeg-nvenc-dev` instead of the git pin | No git fetch | Version moves with Debian point releases; its source would not reach the `-source` image | A pinned commit, verified in the build, with its tree in `-source` |
| Free `intel-media-va-driver` only | Everything from Debian `main` | No HEVC QSV on the A380 under `xe` without HuC (`Invalid FrameType:0`); not measured with HuC | The package in `non-free` is Expat and redistributable (decision 4a) |
| AV1 decode through NVDEC or VA-API in the check | No new library | The judge would share the encoder's vendor stack; differs per kit | dav1d decodes the same way on every kit |
| Local-only non-free build (`INTEL_MEDIA_DRIVER=nonfree`, kit `intel-nonfree-local`, marker; the previous decision 4a) | Published image stays all-`main` | Rested on the premise that the package is not redistributable, which Debian's copyright file refutes; added a build argument, a kit, a marker and a second licence path; testers got no HEVC QSV evidence | The premise was wrong (decision 4a) |
| Name the Intel render node in the steering commands instead of linking libdrm | No new library | The node number differs per host and the tester would have to find it; FFmpeg's own vendor filter needs libdrm | libdrm (MIT, already pulled in by Mesa and libva) lets FFmpeg pick the Intel node |
| Tags `tester-<date>-<sha8>-<kit>` (ADR-0173 wording) | Matches the ADR-0173 text | `-source` then reads `...-nvidia-source`, kit last; differs from the Windows zip order | Kit first, like `tester-windows-<date>-<sha8>`; generic unchanged |
| One reason naming both options for every kit | No kit awareness in the report | A tester with an NVIDIA image is told about `/dev/dri`; intake cannot check it | Kit-specific reason, enforced by the validator |
| Bitrate-control legs in a new `rate_control` stage | The steering stage keeps one meaning | Changes the stage list ADR-0173 fixes; the validator would refuse every 0.4.4 report (eight stages) | Legs with an optional `rate_control` field keep schema 2 and older reports valid (decision 8) |
| Skip `cbr_hw` on a DG2 bound to `xe` | Up to about 25 seconds less per encoder | A kernel that starts loading HuC on DG2 would go unnoticed; the leg would assert a cause it never observed | The leg runs and names the cause only after the failure it predicts (decision 8) |
| Read HuC state from debugfs (`gt0/uc/huc_info`) | States HuC directly | Needs root and debugfs, which a container does not have | `/sys/class/drm` (driver and PCI id) is readable in the container |
| `cbr_extbrc` for `av1_qsv` with `-look_ahead_depth` | Would cover AV1 | The A380 encode failed with look-ahead too; the encoder default has none | Named `not_run` with the oneVPL reason |
| 10 % bitrate tolerance | Tighter | 200-frame runs on the A380 reached +4.5 %, 100-frame runs +17 %; content and GPUs other than the A380 are not measured | 15 % with 400 frames (A380: +1.0 to +2.1 %) |

## Consequences

- **Positive**: NVENC and QSV steering, side-data and AV1 legs run inside an
  image; a run without the device names the exact docker option; a planted
  NVIDIA library, or any non-free package other than the recorded media driver,
  fails the build; each leg is
  machine-readable at intake.
- **Negative**: three FFmpeg builds per workflow run (hosted minutes; the nightly
  builds all three); the Intel image carries Mesa's whole Vulkan driver package
  (RADV, lavapipe and others come with ANV); the NVIDIA image cannot use NVENC on
  drivers older than 530.41.03; the Intel image and its `-source` image take a
  package from `non-free`, so the licence record, the gate and the build-config
  check carry one named exception (decision 4a).
- **Neutral / follow-ups**: the AV1 boundary on a pre-Ada NVIDIA or pre-Arc Intel
  GPU and the WSL2 paths are not measured here (research 0229 lists them); the
  bitrate-control legs are measured on the A380 under `xe` only, VBR legs are
  not added, and each `cbr_hw` leg on DG2 under `xe` waits up to about 25
  seconds for oneVPL to give up (decision 8);
  `qsv-roi-regression.sh` stays a build-tree test, not an image stage; the AMD
  image (#231) can reuse the Intel kit's Mesa stack.

## References

- Issues [#229](https://github.com/VMAFx/pelorus/issues/229) and [#230](https://github.com/VMAFx/pelorus/issues/230), epic [#108](https://github.com/VMAFx/pelorus/issues/108); [research 0229](../research/0229-tester-vendor-images.md); [research 0172](../research/0172-tester-programme.md) T6, T7.
- NVIDIA Container Toolkit, specialized configurations: `NVIDIA_DRIVER_CAPABILITIES` (read 2026-10-09).
- VMAFx `docker/Dockerfile.tester` targets `final-cuda` and `final-sycl` (read-only precedent).
- Source: per user direction in the vendor-images task brief (paraphrased): one image per kit as Containerfile targets, kit-first tags with `-source`, no NVIDIA library in the image, the free media driver only (superseded by the correction below), and a `no_device` report that names the missing docker option.
- Source: maintainer decision for #230 on 2026-10-09, "Free + local non-free" (verbatim popup answer): the published Intel image stays free-only, and a local build may use the non-free media driver under a NOT-FOR-REDISTRIBUTION marker. Corrected the same day (next entry).
- Source: maintainer correction on 2026-10-09 (paraphrased): Debian's `intel-media-va-driver-non-free` is redistributable, Debian ships it, and it is in `non-free` only because Intel's GPU kernels come without source; the published `intel` kit installs it, and the ban on FFmpeg `--enable-nonfree` and the other nonfree components stays.
- Source: maintainer decision on 2026-10-09 (paraphrased): the A380 host stays on the `xe` kernel driver, which has no DG2 HuC support, and Pelorus adopts oneVPL software bitrate control (`-extbrc 1`) there; the tester adds a bitrate-control leg per QSV encoder and reports hardware bitrate control without HuC as a named `not_run`, never as a pass (decision 8).
- Evidence: [research 0180](../research/0180-qsv-bitrate-control-dg2-xe.md) (A380 under `xe`, Intel image and host stacks, 2026-10-09).
- Evidence: `debian/copyright` of `intel-media-driver-non-free` 25.2.3+ds1-1, <https://sources.debian.org/data/non-free/i/intel-media-driver-non-free/25.2.3+ds1-1/debian/copyright>: `Files: *` `License: Expat`; `media_driver/linux/ult/ult_app/googletest/*` `License: BSD-3-clause`; Comment: "We have to move those kernels to non-free as they come without source, i.e. we cannot rebuild them with intel-gen4asm (or similar)." (read 2026-10-09).
