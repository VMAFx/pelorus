<!-- markdownlint-disable MD013 MD060 -->
# QSV bitrate control on an Arc A380 under xe, and the 13.8 dB ROI result re-measured

**Date:** 2026-10-09

**Decision:** [ADR-0180](../adr/0180-tester-vendor-images.md) decision 8

**Scope:** Intel Arc A380 (PCI id `8086:56a5`, DG2) bound to the `xe` kernel
driver, which loads no HuC firmware on DG2; Linux 7.2.9. Two software stacks:

| Stack | FFmpeg | oneVPL runtime | Media driver |
| --- | --- | --- | --- |
| Intel tester image (`final-intel`, built from `b45d873`) | `n9.0.2` with the Pelorus patch stack | `libmfx-gen1.2` 25.1.4, `libvpl2` 2.14.0 | `intel-media-va-driver-non-free` 25.2.3+ds1-1 |
| Host | master `7bc3576910`, stock | `vpl-gpu-rt` 26.3.5, `libvpl` 2.17.0 | `intel-media-driver` 26.3.5 |

The workstation ran other jobs, so the durations below are approximate.

## Verdict

- oneVPL's software bitrate control (`-extbrc 1`) gives `h264_qsv` and
  `hevc_qsv` working CBR on this GPU: within 2.1 % of the target over 400
  frames with a one-second buffer. The runtime picks each frame's QP and opens
  the driver in constant QP, which needs no HuC.
- Hardware bitrate control (CBR without `-extbrc`) fails on all three QSV
  encoders and produces no frame. The encoder logs `GPU Hang (-21)` or
  `Invalid FrameType:0`.
- `av1_qsv` gets no software bitrate control: with `-extbrc 1` it fails like
  hardware CBR, with and without look-ahead.
- Both QSV device initialisations work when `LIBVA_DRIVER_NAME` is unset or
  `iHD`. The `Error creating a MFX session: -9` seen earlier came from
  `LIBVA_DRIVER_NAME=nvidia` in the environment, not from the
  `child_device=` form.
- The 13.8 dB result of bench results v0.6 reproduces in constant QP, not as a
  bitrate-control artifact: `hevc_vaapi` with one ROI rectangle writes a
  corrupt stream on this GPU (PSNR-Y 13.81 dB at `qoffset=-0.15`, 41.02 dB
  without ROI). The same ROI through `hevc_qsv` on the same encoder is clean.
  The claim that every Arc A-series low-power encode is invalid does not hold.

## Software bitrate control (ExtBRC)

Input: the tester's `synth-motion` fixture (`testsrc2`, 640x360, 50 frames at
25 fps) looped, through `pelorus_deband_vulkan`, in the Intel image. CBR with
`-b:v`, `-maxrate` and `-bufsize`; the bitrate is the elementary stream size
over the clip duration; every stream decodes to every frame. Every run logs
`ExtBRC: ON` and `RateControlMethod: CBR`.

| Target | Buffer | Frames | `h264_qsv` | `hevc_qsv` |
| --- | --- | --- | --- | --- |
| 1 Mb/s | 2 Mb | 100 | +16.9 % | +7.9 % |
| 1 Mb/s | 2 Mb | 200 | +8.6 % | +4.1 % |
| 1 Mb/s | 2 Mb | 400 | +3.8 % | +2.1 % |
| 0.5 Mb/s | 1 Mb | 100 | +22.7 % | +17.4 % |
| 0.5 Mb/s | 1 Mb | 200 | +10.4 % | +7.7 % |
| 2 Mb/s | 4 Mb | 200 | −3.8 % | −3.6 % |
| 1 Mb/s | 1 Mb | 200 | +4.5 % | +2.0 % |
| **1 Mb/s** | **1 Mb** | **400** | **+1.8 %** | **+1.0 %** |
| 0.5 Mb/s | 0.5 Mb | 400 | +2.1 % | +1.5 % |
| 2 Mb/s | 2 Mb | 400 | −0.3 % | +2.0 % |

VBR works the same way: `-b:v 1000k -maxrate 1500k -bufsize 2000k -extbrc 1`
over 400 frames (without the deband filter) gave 1014 kb/s (`h264_qsv`) and
1001 kb/s (`hevc_qsv`), `RateControlMethod: VBR`, `ExtBRC: ON`.

The overshoot of short runs is the initial buffer fill; a one-second buffer
and 16 seconds of video keep it near 2 %. The tester legs use the bold row
with a 15 % tolerance. The host stack (FFmpeg master, runtime 26.3.5) gave
1085.5 kb/s and 1040.2 kb/s for the 200-frame, 2 Mb buffer case, the image
1085.6 kb/s and 1041.1 kb/s.

Why constant QP reaches the driver: with `ExtBRC` on and CBR or VBR, the
H.264 encoder sets `m_enabledSwBrc` ("in the case of SWBRC driver works in CQP
mode", vpl-gpu-rt `mfx_h264_encode_hw.cpp`) and the HEVC encoder maps the rate
control to `VA_RC_CQP` (`ConvertRateControlMFX2VAAPI`, `hevcehw_base_va_lin.cpp`),
both at tag `intel-onevpl-25.1.4`, the runtime in the image.

## Hardware bitrate control

Same input and target, no `-extbrc`, Intel image:

| Encoder | Exit | Encoder's line | Time to fail |
| --- | --- | --- | --- |
| `h264_qsv` | 251 | `Error during encoding: GPU Hang (-21)` | under 1 s |
| `hevc_qsv` | 183 | `Invalid FrameType:0.` | about 11 s |
| `av1_qsv` | 183 | `Invalid FrameType:0.` | about 9 s |

On the host stack `h264_qsv` failed with `Invalid FrameType:0.` and
`hevc_qsv` took about 25 s. FFmpeg prints `Invalid FrameType:0` when the
runtime hands back an empty bitstream after a failed synchronisation. An
earlier instrumented build, in ICQ and VBR, showed the underlying statuses
`MFX_ERR_GPU_HANG` (−21) for H.264 and `MFX_ERR_DEVICE_FAILED` (−17) for HEVC
and AV1. The strings are from `libavcodec/qsv.c` and `qsvenc.c` in FFmpeg n9.
The kernel logs nothing for these QSV failures, and later encodes on the same
device work. During the re-measure session below, the kernel did log
`Engine memory CAT error: class=vcs`, `Engine reset: engine_class=vcs` and
`Timedout job ... in ffmpeg`, and wrote a device coredump, six times between
19:02 and 19:04. Repeating the VA-API and QSV bitrate-control encodes
afterwards (H.264 and HEVC, CBR and ICQ, with and without an ROI, 640x360 and
1280x720, Arch media driver 26.3.5 and Debian 25.2.3 non-free) logged nothing,
so the trigger of those resets is not identified. In the end-to-end tester run
the whole steering stage, three hardware CBR legs included, took 30 seconds.

The media driver's README lists HuC as necessary for low-power bitrate
control. On `xe` the driver does not ask the kernel whether HuC is loaded; it
sets `hasHuc = 1` (`mos_bufmgr_xe.c`, 26.3.5), advertises CBR, VBR and ICQ, and
the encode then fails on the GPU instead of being refused. Under `i915` without
HuC the driver reads the HuC status and advertises constant QP only on DG2's
low-power encoder, so the runtime should refuse CBR at initialisation and
FFmpeg print `Selected ratecontrol mode is unsupported`; that case is not
measured. The hardcoded flag is reported upstream as
[intel/media-driver#2046](https://github.com/intel/media-driver/issues/2046).

## AV1 with -extbrc

| Options | Log | Result |
| --- | --- | --- |
| `-extbrc 1` (look-ahead 0, the default) | `ExtBRC: ON` | `Invalid FrameType:0.`, exit 183 |
| `-extbrc 1 -look_ahead_depth 40` | `ExtBRC: ON`, `GopRefDist: 8` | same |
| `-extbrc 1 -look_ahead_depth 8 -bf 7` | same | same |

oneVPL reaches software bitrate control for AV1 only through its look-ahead
tools: `IsSwEncToolsImplicit` in `av1ehw_base_enctools_com.h` needs
`LookAheadDepth` above 0 and `ExtBRC` on (25.1.4 and main). With look-ahead the
encode still fails here, so whether that path needs HuC is not settled. The
`ExtBRC: ON` line alone therefore proves nothing for AV1; for H.264 and HEVC it
does, because the runtime switches the driver to constant QP whenever it is on.

## Device initialisation and LIBVA_DRIVER_NAME

Host stack, `hevc_qsv`, 25 frames, input uploaded with
`hwupload=extra_hw_frames=64` (QSV frames) or passed as software frames:

| Form | `LIBVA_DRIVER_NAME=iHD` | `LIBVA_DRIVER_NAME=nvidia` |
| --- | --- | --- |
| `-init_hw_device vaapi=va:/dev/dri/renderD130 -init_hw_device qsv=qs@va` | encodes | fails: libva opens the NVIDIA VA driver (`Failed to get device id from the driver`) |
| `-init_hw_device qsv=qs:hw,child_device=/dev/dri/renderD130` | encodes | fails: `Error creating a MFX session: -9` |
| no device option (FFmpeg picks the Intel node by vendor id) | encodes | fails: `Error creating a MFX session: -9` |

The image's FFmpeg encodes with both explicit forms (its environment sets no
`LIBVA_DRIVER_NAME`). A host that sets `LIBVA_DRIVER_NAME` for another GPU
must set it to `iHD` for Intel commands. `scripts/bench/` runs no VA-API or QSV
encoder (it uses NVENC), so it needs no change.

## The 13.8 dB result re-measured

Bench results v0.6 recorded a broken `hevc_vaapi` encode with ROI on the A380
(PSNR 13.8 dB), v0.8 a QSV run with `-pelorus_roi 1` whose bitrate rose by
45-108 % with more banding, and both were attributed to an "Arc A-series
low-power encode bug". The commands were not recorded. The reconstruction
follows the v0.4 composite clip: top half a dark smooth gradient
(`gradients`, as in `synth-banding`), bottom half `mandelbrot`, 640x360, 60
frames at 25 fps; one `addroi` rectangle over the top half; constant QP in both
arms. Script: `remeasure.sh` in the evidence directory below.

| Arm | Exit | kb/s | PSNR-Y (dB) | Note |
| --- | --- | --- | --- | --- |
| `hevc_vaapi -rc_mode CQP -qp 30` | 0 | 271.5 | 41.02 | |
| same, ROI `qoffset=-0.3` | 0 | 316.6 | 9.70 | corrupt |
| same with `-qp 30` and no `-rc_mode` | 0 | 316.6 | 9.70 | FFmpeg picks CQP |
| `hevc_vaapi -rc_mode CBR` 400 kb/s, with and without ROI | 251 | - | - | no frame |
| `hevc_vaapi -rc_mode ICQ`, ROI | 251 | - | - | no frame |
| `h264_vaapi -rc_mode CBR` 1 Mb/s (`synth-motion`, image only) | 251 | - | - | `Error encoding a frame: Input/output error` |
| `hevc_qsv -low_power 1 -q:v 30` | 0 | 121.0 | 38.51 | |
| same, ROI (stock rectangles) | 0 | 144.7 | 38.57 | |
| same, ROI, `-pelorus_roi 1` (dense map, image only) | 0 | 308.6 | 38.58 | |
| `hevc_qsv` CBR 400 kb/s `-extbrc 1` | 0 | 457.5 | 44.53 | |
| same, ROI | 0 | 464.9 | 43.67 | |
| `hevc_qsv` CBR 400 kb/s, hardware bitrate control | 183 | - | - | no frame |

Both stacks gave the same numbers; the VA-API ROI streams are byte-identical
between media driver 25.2.3 and 26.3.5.

The VA-API corruption depends on the ROI, not on its strength, and covers the
whole picture:

| `qoffset` | −0.05 | −0.1 | **−0.15** | −0.2 | −0.3 | +0.1 |
| --- | --- | --- | --- | --- | --- | --- |
| PSNR-Y (dB) | 24.15 | 15.50 | **13.81** | 11.84 | 9.70 | 19.21 |

| Stream | Top half (ROI) | Bottom half |
| --- | --- | --- |
| `hevc_vaapi`, no ROI | 52.51 dB | 38.17 dB |
| `hevc_vaapi`, ROI −0.15 | 24.77 dB | 10.98 dB |
| `hevc_qsv`, no ROI | 52.55 dB | 35.58 dB |
| `hevc_qsv`, ROI rectangles | 56.93 dB | 35.59 dB |
| `hevc_qsv`, `-pelorus_roi 1` | 59.81 dB | 35.59 dB |
| `hevc_qsv` `-extbrc 1`, no ROI | 55.03 dB | 41.71 dB |
| `hevc_qsv` `-extbrc 1`, ROI | 58.84 dB | 40.73 dB |

At `qoffset=-0.15`, the ROI strength of the v0.5 NVENC run, PSNR-Y is
13.81 dB, the recorded value. FFmpeg sends the ROI to VA-API as a QP delta of
`qoffset` times 51 (`vaapi_encode.c`), so −0.15 is a delta of −7, inside the
range the driver's native ROI handles. In constant QP the media driver's HEVC
ROI strategies (`encode_hevc_vdenc_roi_strategy.cpp`, `CreateStrategy`) use HuC
only when VDEnc bitrate control is on, so HuC is not the cause by the code.
The exact failure point is not traced.

VMAF and CAMBI (vmafx `vmaf` 3.2.0, CAMBI lower is less banding) on the image
streams:

| Arm | VMAF | CAMBI |
| --- | --- | --- |
| `hevc_qsv -q:v 30` | 83.11 | 4.901 |
| ROI rectangles | 83.61 | 4.450 |
| `-pelorus_roi 1` | 83.80 | 4.311 |
| `-extbrc 1` 400 kb/s | 92.22 | 4.610 |
| `-extbrc 1` 400 kb/s, ROI | 91.65 | 4.351 |
| `hevc_vaapi` CQP | 86.91 | 4.323 |
| `hevc_vaapi` CQP, ROI (corrupt) | 83.45 | 3.330 |

VMAF and CAMBI do not flag the corrupt VA-API stream; PSNR does.

Conclusions for bench results:

- v0.6: the 13.8 dB stands as a measurement and is explained by the VA-API ROI
  path of the media driver on this GPU, in constant QP. It is not a
  bitrate-control or HuC artifact, and no source was found for "fixed only in
  Arc B (Battlemage)".
- v0.8: QSV ROI on the same low-power encoder decodes clean and puts the bits
  in the ROI. At the same QP, `-pelorus_roi 1` raised the bitrate by 155 % here
  for a −15 QP region over half the picture, the expected cost of a fixed-QP
  steering demo, and CAMBI fell from 4.90 to 4.31. The v0.8 bitrate rise is no
  evidence of a defect. Its CAMBI increase is not reproduced on this clip; its
  own clip and command were not recorded. The map is the same on every frame
  here, so this run says nothing about per-frame map ownership (ADR-0146).
- With `-extbrc 1` at matched bitrate (457.5 against 464.9 kb/s), the ROI moves
  3.8 dB into the top half and costs 1.0 dB in the bottom half.

## Not measured

- The A380 with HuC loaded (`i915`): whether hardware bitrate control passes
  there, and how ExtBRC compares with it in quality.
- Other Arc A-series cards, Arc B-series, integrated Intel GPUs.
- ExtBRC on content other than `testsrc2`, and VBR through the Pelorus filter chain.
- The cause inside the media driver of the VA-API ROI corruption.

## Reproduction

```bash
# A380 render node; set LIBVA_DRIVER_NAME=iHD on a host that sets it for another GPU
docker run --rm --device /dev/dri/renderD130 -v "$PWD/report:/report" pelorus-tester:intel
LIBVA_DRIVER_NAME=iHD ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD130 -init_hw_device qsv=qs@va \
  -filter_hw_device qs -f lavfi -i testsrc2=size=640x360:rate=25 -frames:v 400 \
  -vf format=nv12,hwupload=extra_hw_frames=64 \
  -c:v hevc_qsv -b:v 1000k -maxrate 1000k -bufsize 1000k -extbrc 1 -f hevc out.hevc
```

Evidence (logs, scripts, tables): `.workingdir/evidence/extbrc-adoption/`
(local, git-ignored), with the earlier ExtBRC proof in
`.workingdir/evidence/xe-dg2-huc/`.
