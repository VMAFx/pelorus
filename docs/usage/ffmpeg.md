<!-- markdownlint-disable MD013 -->
# The zero-copy FFmpeg pipeline

Pelorus filters are libavfilter Vulkan filters: they consume and produce
`AV_PIX_FMT_VULKAN` frames. The win comes from keeping frames in VRAM from
decode to encode, so a recipe for a hardware encoder never contains
`hwdownload`. `hwdownload` stays only in recipes for software encoders
(libaom, SVT-AV1, x265), where it is inherent: those encoders read system
memory. `scripts/check-doc-recipes.py` enforces both rules on every recipe in
this repository.

**Codec-agnostic.** The filters pre-process pixels; the encoder is your choice,
but FFmpeg hardware-frame domains must match, and each encoder family has its
own boundary:

| Encoder | Boundary from `AV_PIX_FMT_VULKAN` | Copy | Section |
| --- | --- | --- | --- |
| Vulkan Video (`*_vulkan`) | none: same frames context | none | [Full zero-copy](#full-zero-copy-vulkan-decode--filters--vulkan-hw-encode) |
| NVENC | `hwupload` to CUDA, `disable_multiplane=1` | VRAM to VRAM copy | [NVENC](#nvenc-nvdec--vulkan-filters--nvenc) |
| VAAPI, QSV (Linux, Intel) | `tiling=drm` on the last writing filter, then `hwmap` | none (map) | [VAAPI and QSV](#zero-copy-into-vaapi-and-qsv-encoders-linux) |
| AMF | none exists in FFmpeg n9.0.2 | not available | [AMF](#amf-and-windows) |
| libaom, SVT-AV1, x265 | `hwdownload` | host round trip, inherent | [Software encoders](#software-encoders-hwdownload-is-inherent) |

## Hardware status of the recipes

Every recipe on this page is one of three kinds. "Verified" means it ran on
the named GPU with the named binary, ended with exit code 0 and the log shows
the claimed path (no `hwdownload`, no auto-inserted scale, the encoder taking
a hardware frames context). Anything else says so next to the recipe.

| Recipe | GPU | Status |
| --- | --- | --- |
| NVENC through the CUDA hop | RTX 4090, driver 615.78.08 | verified behind NVDEC for graphs ending in one writing filter (3000 frames) and behind a software decoder for the listed graphs; other NVDEC graphs fail, measured ([#296](https://github.com/VMAFx/pelorus/issues/296)) |
| VAAPI and QSV through `tiling=drm`, NV12 | Arc A380, iHD, `xe` | verified |
| Vulkan decode to Vulkan encode | RTX 4090 | verified for `hevc_vulkan` with the graphs listed below; the AV1 variant not run |
| P010 into VAAPI and QSV | any | not working: stock FFmpeg defect ([Vulkan output pools](../backends/vulkan-drm-modifiers.md)) |
| `tiling=drm` on AMD | Ryzen iGPU, RADV | maps, but frames can be read unfinished; not a working recipe |
| AMF | none | not run on hardware: no AMF runtime on the Linux test host, and FFmpeg has no Vulkan-to-AMF path |
| libaom, SVT-AV1, x265 recipes | none | not re-run on hardware: the test binary has none of these encoders; the graph shape is the same as the verified ones |

The verified runs used an FFmpeg `n9.0.2` build with the shared fix series
(patches 0001 to 0004) and the Pelorus patch stack at `50b625b`, on clips of
120 frames at 1280x720. Host: NVIDIA driver 615.78.08, Linux 7.2.9, Arc A380 on
the `xe` kernel driver with iHD.

## NVENC: NVDEC → Vulkan filters → NVENC

FFmpeg `n9.0.2` has no Vulkan-to-NVENC map. The two hops between CUDA and
Vulkan are `hwupload` calls that copy within VRAM; no frame reaches system
memory. The Vulkan device needs `disable_multiplane=1`, because the CUDA
interop refuses a multiplane NV12 or P010 image (one image for two planes):

```bash
ffmpeg -init_hw_device vulkan=vk:0,disable_multiplane=1 -filter_hw_device vk \
       -hwaccel cuda -hwaccel_output_format cuda -i input.mkv \
       -vf "hwupload,pelorus_deband_vulkan=range=15:dither=bluenoise:dynamic=1,hwupload=derive_device=cuda" \
       -c:v hevc_nvenc -cq 28 out.mkv      # or av1_nvenc / h264_nvenc
```

Verified on an RTX 4090: exit code 0, 120 frames; the log shows `pixfmt:cuda`
at the graph input, `Transferred CUDA image to Vulkan!`, `Transferred Vulkan
image to CUDA!` and `Using input frames context (format cuda) with hevc_nvenc
encoder`. Without `disable_multiplane=1` the same command fails with `Cannot
map a multiplane Vulkan image (1 image(s) for 2 plane(s)) to CUDA; create the
Vulkan device with the disable_multiplane=1 option` (exit code 218).

The failure listed below needs the first hop (NVDEC `cuda` frames to
`hwupload` to Vulkan). With a software decoder, put the upload first:

```bash
ffmpeg -init_hw_device vulkan=vk:0,disable_multiplane=1 -filter_hw_device vk -i input.mkv \
       -vf "format=nv12,hwupload,<filters>,hwupload=derive_device=cuda" \
       -c:v hevc_nvenc -cq 28 out.mkv
```

Verified on an RTX 4090, exit code 0, no CUDA error, no `hwdownload` in the
log, 8-bit input: every graph in the failing rows below, 120 frames each; deband
twice and `pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=3:mc=1` also at
3000 frames. The 10-bit form (`format=p010le`) was not run.

Which graphs work on this hop was measured, not derived (RTX 4090, 120-frame
clip, exit code 0 means all frames encoded):

| Graph between the two `hwupload` calls | Result |
| --- | --- |
| one of `pelorus_deband_vulkan`, `pelorus_aa_vulkan`, `pelorus_deblock_vulkan`, `pelorus_dehalo_vulkan` | works |
| `pelorus_analyze_vulkan` or `pelorus_mc_vulkan` followed by one writing filter (deband) | works |
| `pelorus_analyze_vulkan=roi=1`, `pelorus_dehalo_vulkan`, `pelorus_aa_vulkan`, `pelorus_deband_vulkan`, 10-bit | works |
| `pelorus_grain_estimate_vulkan` followed by deband | works |
| `pelorus_mc_vulkan` followed by `pelorus_scenecut` and deband | works |
| `pelorus_denoise_vulkan` alone or after `pelorus_mc_vulkan`; deband twice; `pelorus_borderfix_vulkan` twice; `pelorus_grain_estimate_vulkan` with denoise | fails after 2 to 37 frames: `cuWaitExternalSemaphoresAsync failed -> CUDA_ERROR_INVALID_VALUE` (exit code 187) |
| a pass-through filter alone (`pelorus_analyze_vulkan`, `pelorus_mc_vulkan`, `pelorus_scenecut`) | fails the same way |
| the failing graphs above, behind a software decoder | works (120 frames; deband twice and mc with denoise also 3000 frames) |
| deband alone, `mc` with deband, `aa` alone, behind NVDEC | works also at 3000 frames |

A larger upload pool (`hwupload=extra_hw_frames=64`) and an explicitly created
CUDA device did not change the failures. The cause is not found ([#296](https://github.com/VMAFx/pelorus/issues/296)). For the
failing graphs, use a software decoder with the same graph, or the Vulkan Video
encoder below.

## Full zero-copy: Vulkan decode → filters → Vulkan HW encode

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
       -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_deband_vulkan=range=15:thry=0.012" \
       -c:v hevc_vulkan -pix_fmt vulkan -qp 28 out.mkv  # or av1_vulkan for AV1
```

When the decoder, filter, and encoder all speak Vulkan/VRAM, no frame touches
system RAM. The deband graph above was not run for this page. These ran on
the RTX 4090 with `hevc_vulkan -qp 28` and exit code 0 (120 frames): borderfix twice,
`pelorus_mc_vulkan=meta=1` with `pelorus_denoise_vulkan=mc=1` (with
`-extra_hw_frames 4`), and `pelorus_grain_estimate_vulkan` with
`pelorus_denoise_vulkan`.

## Zero-copy into VAAPI and QSV encoders (Linux)

`tiling=drm` on the last Pelorus filter that writes frames lets `hwmap` hand
its output to a VAAPI encoder, or on to QSV, without `hwdownload` (8-bit NV12
only; P010 and AMD wait on FFmpeg fixes, see
[Vulkan output pools](../backends/vulkan-drm-modifiers.md)). Derive Vulkan from
the VAAPI device so both run on the same GPU, and decode with VAAPI so the
decoder output maps into Vulkan too. VAAPI encoder, verified on an Arc A380
(exit code 0, 120 frames):

```bash
LIBVA_DRIVER_NAME=iHD ffmpeg -v verbose -init_hw_device vaapi=va:/dev/dri/renderD130 \
       -init_hw_device vulkan=vk@va \
       -hwaccel vaapi -hwaccel_device va -hwaccel_output_format vaapi -i input.mkv \
       -vf "hwmap=derive_device=vulkan,pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi" \
       -c:v h264_vaapi -rc_mode CQP -qp 20 out.mkv
```

With `-v verbose` the filter names the pool's layout, for example on an Arc
A380:

```text
[Parsed_pelorus_deband_vulkan_1 @ 0x...] tiling=drm: nv12 1280x720 pool uses DRM format modifier 0x0100000000000009 (I915_FORMAT_MOD_4_TILED), chosen by the driver from 3
```

The log has no `hwdownload` and no scale filter, and the encoder reports
`Using input frames context (format vaapi) with h264_vaapi encoder`.

QSV encoder, mapped on from VAAPI; each hardware format is named so that format
negotiation cannot pick another. Verified on the same card (exit code 0,
120 frames):

```bash
LIBVA_DRIVER_NAME=iHD ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD130 \
       -init_hw_device vulkan=vk@va \
       -hwaccel vaapi -hwaccel_device va -hwaccel_output_format vaapi -i input.mkv \
       -vf "hwmap=derive_device=vulkan,pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi,hwmap=derive_device=qsv,format=qsv" \
       -c:v hevc_qsv -q:v 24 out.mkv
```

With a software decoder, replace the decode options and the first `hwmap` by
`-i input.mkv -vf "format=nv12,hwupload,..."` (measured in ADR-0184, not
re-run for this page).

On an Arc A380 under `xe`, VAAPI encoders need constant QP (`-rc_mode CQP -qp
N`) and QSV bitrate control needs `-extbrc 1` (see below).

## Frames the filters keep: `-extra_hw_frames`

Two filters hold on to input frames after they have output them. Hardware
decoders allocate a fixed pool of surfaces, so frames a filter keeps are
surfaces the decoder cannot reuse.

<!-- gate: retained-frames -->

| Filter | Frames kept | Default | Maximum |
| --- | --- | --- | --- |
| `pelorus_mc_vulkan` | the previous input frame, always | 1 | 1 |
| `pelorus_denoise_vulkan` | `prev` earlier frames plus the held frame when `lookahead=1` | 3 | 5 |

The default is `prev=3`, `lookahead=0`; the maximum is `prev=4` and
`lookahead=1`. `scripts/check-doc-recipes.py` reads the numbers from
`PEL_DENOISE_MAX_PREV`, the option defaults in
`vf_pelorus_denoise_vulkan.c` and the single `AVFrame *prev` of
`vf_pelorus_mc_vulkan.c`, and fails when this table or an `-extra_hw_frames`
value in a recipe disagrees. A chain that holds frames in both filters needs the
sum.

Pass `-extra_hw_frames N` before `-i` with N at least the number of frames the
chain keeps, so the decoder pool has room for them:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
       -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan -extra_hw_frames 4 \
       -i input.mkv \
       -vf "pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=3:mc=1" \
       -c:v hevc_vulkan -qp 28 out.mkv
```

Verified (RTX 4090, exit code 0, 120 frames; `mc` 1 + `prev=3` = 4). Starvation
without the option was not reproduced: the same chain behind VAAPI decode on
the Arc A380 and NVDEC with `mc` ran to completion without it, so the option is
a safeguard sized by the table, not a measured fix.

## AMF and Windows

FFmpeg `n9.0.2` has no Vulkan-to-AMF and no Vulkan-to-D3D11 path, and AMF takes
software, D3D11 or DXVA2 frames only ([research 0172](../research/0172-zero-copy-audit.md),
hop matrix rows D and G). A Windows or AMF recipe therefore cannot avoid a host
copy today, and none is given. Not run on hardware: the test host is Linux
without an AMF runtime.

## Software encoders: `hwdownload` is inherent

libaom, SVT-AV1 and x265 read frames from system memory, so the Pelorus output
must be downloaded. Keep Pelorus stages in VRAM and download once, last:

```bash
# hwdownload is inherent: libsvtav1 takes system-memory frames.
# Not run on hardware: the test binary has no libsvtav1.
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk -i input.mkv \
       -vf "format=p010le,hwupload,pelorus_deband_vulkan,hwdownload,format=p010le" \
       -c:v libsvtav1 -crf 35 -preset 6 out.mkv
```

## Chaining stages

```bash
-vf "pelorus_analyze_vulkan,
     pelorus_grain_estimate_vulkan=strength=2.0,
     pelorus_mc_vulkan=bsize=16:search=24:meta=1,
     pelorus_denoise_vulkan=sigma=0.03:mc=1,
     pelorus_deband_vulkan=range=15"
```

`pelorus_grain_estimate_vulkan` reads the **source** grain, so it runs before
denoise removes it. AV1 consumers can re-synthesize grain from the emitted native
frame params; the HEVC `pelorus_fgs` BSF instead requires a static model supplied
manually through its AVOptions and does not read those frame params inline.
`pelorus_mc_vulkan` is a pass-through producer: with `meta=1` it emits per-block
motion and confidence fields. `pelorus_denoise_vulkan=mc=1` consumes them for a
confidence-gated temporal warp, while the optional NVENC
`NV_ENC_EXTERNAL_ME_HINT` consumer uses the motion field as an encode-search
hint. AVOptions: `bsize` (block edge, default 16), `search` (radius, default 24),
`meta`.

Each stage runs in VRAM; the Pelorus side-data blob accumulates sections and
rides every frame to the encoder.

## VMAF-in-the-loop (autotune with vmafx)

Score the processed-then-encoded output against the source, in the same graph:

```bash
# hwdownload is inherent here: libvmaf_tune scores system-memory frames.
# Not run on hardware: the test binary has no libvmaf_tune.
ffmpeg -i src.mkv -i src.mkv -filter_complex \
  "[0:v]hwupload,pelorus_deband_vulkan=thry=0.012:meta=1,hwdownload,format=p010le[pre];
   [pre][1:v]libvmaf_tune=recommend_target_vmaf=93:model=version=vmaf_v0.6.1" \
  -f null -
```

`libvmaf_tune` (from the vmafx FFmpeg patches) logs a `recommended_crf=` line and
can read the Pelorus banding/variance sections for perceptual weighting. For a
distributed sweep, score finished encodes via `vmafx-server` `POST /v1/score` or
the `vmaf-mcp` `vmaf_score_encoded` tool. See
[ADR-0106](../adr/0106-autotune-control-plane.md).

## Encoder ROI steering (NVENC / QSV)

`vf_pelorus_analyze roi=1` emits `AV_FRAME_DATA_REGIONS_OF_INTEREST` (a per-cell
banding/quality `qoffset` map). Vanilla NVENC ignores ROI side data and vanilla
QSV honors only coarse rectangle regions; the Pelorus patch stack adds a
`-pelorus_roi 1` AVOption to both. NVENC consumes the **same** side data into
`qpDeltaMap`; progressive HEVC QSV under CQP on runtime API 1.28 or newer can
consume it through a dense `mfxExtMBQP` delta map:

```bash
# HEVC, NVENC, constant-QP (the clean mode for QP-map steering). Verified on
# an RTX 4090, exit code 0, 120 frames, software decode. Behind NVDEC this graph
# fails on the CUDA hop (#296); a graph ending in one writing filter (for
# example deband) works there.
ffmpeg -init_hw_device vulkan=vk:0,disable_multiplane=1 -filter_hw_device vk \
       -i input.mkv \
       -vf "format=nv12,hwupload,pelorus_analyze_vulkan=roi=1,hwupload=derive_device=cuda" \
       -c:v hevc_nvenc -rc constqp -qp 30 -pelorus_roi 1 out.mkv

# HEVC, Intel QSV, progressive CQP (-q:v also sets AV_CODEC_FLAG_QSCALE).
# Verified on an Arc A380, exit code 0, 120 frames; the log says "Pelorus ROI:
# requesting per-frame HEVC delta-QP maps (mfxExtMBQP)". 8-bit NV12 only.
LIBVA_DRIVER_NAME=iHD ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD130 -init_hw_device vulkan=vk@va \
       -hwaccel vaapi -hwaccel_device va -hwaccel_output_format vaapi -i input.mkv \
       -vf "hwmap=derive_device=vulkan,pelorus_analyze_vulkan=roi=1,pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi,hwmap=derive_device=qsv,format=qsv" \
       -c:v hevc_qsv -q:v 30 -pelorus_roi 1 out.mkv
```

The option is registered on `hevc_qsv`, `h264_qsv`, `hevc_nvenc`, `h264_nvenc`,
`av1_nvenc` and `libsvtav1` (swap the encoder above accordingly; the SVT-AV1
mapping differs — see below). It defaults OFF (zero
behaviour change). Use **constant-QP** and the encoder's own spatial/temporal AQ
OFF: the encoder AQ overrides the delta-QP map, and VBR rate-control
redistribution erodes the perceptual win.

NVENC adds each `qpDeltaMap` entry to the rate-control QP in the codec's own QP
units, so a region's delta is `qoffset` scaled by a per-codec span:
`51 + 6 × (bit_depth − 8)` H.264/HEVC QP steps for `h264_nvenc`/`hevc_nvenc`,
and 255 AV1 qindex steps for `av1_nvenc` (the same scale as its
`-qp`/`-qmin`/`-qmax`, and the scale the libaom, SVT-AV1, VAAPI and D3D12 AV1 ROI
paths use). The map holds signed bytes, so an AV1 delta saturates at
[−128, 127] qindex; the analyze filter's default `roi_strength=0.333` stays inside
that range.

For QSV, use `-q:v N` (or otherwise set `AV_CODEC_FLAG_QSCALE`) to select CQP.
`-global_quality N` alone selects ICQ in FFmpeg n9.0.2 and therefore cannot use
the dense MBQP path. The patch does not add `-pelorus_roi` to `av1_qsv`.

QSV selects the dense path only for progressive HEVC+CQP when the runtime API is
1.28 or newer and the build headers expose `mfxExtMBQP` (oneVPL/MediaSDK API
1.13 or newer). H.264, older runtimes, non-CQP HEVC, interlaced HEVC, and builds
without that header surface retain FFmpeg's stock per-region
`mfxExtEncoderROI` steering; the option reports the fallback instead of
disabling ROI. On a dense-path frame the map and header are owned by that frame
until its asynchronous QSV surface unlocks. The map grid uses oneVPL's aligned
storage dimensions while ROI coordinates are clipped to the visible frame,
leaving storage-padding cells at zero.

`EnableMBQP=ON` is an initialization request, not a runtime capability probe.
Dense selection caches the final attached CodingOption3 value after init/reset
and requires that field to remain ON after any `AVQSVContext` replacement. The
patch never attaches `mfxExtMBQP` and `mfxExtEncoderROI` to the same frame.
See [QSV ROI steering](../backends/qsv-roi.md),
[ADR-0114](../adr/0114-encoder-steering.md), and its QSV contract correction
[ADR-0146](../adr/0146-qsv-roi-frame-ownership.md).

### Intel Arc A-series on the `xe` kernel driver: bitrate control

The `xe` kernel driver loads no HuC firmware on DG2 (Arc A-series), and the
media driver's low-power encoder, the only one DG2 has, runs its bitrate
control on HuC. On such a host every hardware bitrate-control mode (CBR, VBR,
ICQ) fails without writing a frame: `h264_qsv` logs `GPU Hang (-21)` or
`Invalid FrameType:0`, `hevc_qsv` and `av1_qsv` `Invalid FrameType:0`. VA-API
(`-rc_mode CBR`) fails the same way with `Input/output error`. Constant QP
(`-q:v N`) works.

For CBR or VBR on `h264_qsv` and `hevc_qsv`, add `-extbrc 1`. oneVPL then
picks each frame's QP on the CPU and drives the driver in constant QP, which
needs no HuC; the encoder log says `ExtBRC: ON`. On an A380 this held CBR within
2 % of the target over 16 seconds:

```bash
LIBVA_DRIVER_NAME=iHD ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD130 -init_hw_device vulkan=vk@va \
       -hwaccel vaapi -hwaccel_device va -hwaccel_output_format vaapi -i input.mkv \
       -vf "hwmap=derive_device=vulkan,pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi,hwmap=derive_device=qsv,format=qsv" \
       -c:v hevc_qsv -b:v 4M -maxrate 4M -bufsize 4M -extbrc 1 out.mkv
```

- ICQ (`-global_quality N` without a bitrate) has no software counterpart and
  stays unavailable; so does `av1_qsv` bitrate control, because oneVPL applies
  `-extbrc` to AV1 only through look-ahead tools that also failed on the A380.
  Use `-q:v N` for AV1.
- With `-extbrc 1` the session is not constant QP, so `-pelorus_roi 1` on
  `hevc_qsv` takes the stock ROI rectangles, not the dense map
  ([QSV ROI steering](../backends/qsv-roi.md)).
- VA-API encoders (`hevc_vaapi`, `h264_vaapi`) have no software bitrate control:
  constant QP only on this setup. ROI side data through `hevc_vaapi` produces a
  corrupt stream on the A380 even in constant QP; steer through `hevc_qsv`.

To name the GPU instead of letting FFmpeg pick the Intel render node, either
form works:

```bash
-init_hw_device vaapi=va:/dev/dri/renderD130 -init_hw_device qsv=qs@va -filter_hw_device qs
-init_hw_device qsv=qs:hw,child_device=/dev/dri/renderD130 -filter_hw_device qs
```

followed by `-vf "...,hwupload=extra_hw_frames=64"` for QSV frames. If the
environment sets `LIBVA_DRIVER_NAME` for another GPU (for example `nvidia` on a
host whose desktop runs on an NVIDIA card), set it to `iHD` for the Intel
command: otherwise libva loads the other driver and QSV fails with `Error
creating a MFX session: -9`, whichever form is used. Measurements:
[research 0180](../research/0180-qsv-bitrate-control-dg2-xe.md); the tester's
bitrate-control legs check this on every Intel run
([tester kit stages](tester.md#qsv-bitrate-control-legs)).

### SVT-AV1 software (ADR-0121)

The same `-pelorus_roi 1` AVOption is registered on `libsvtav1` (the `av1_svt`
flagship modern AV1 software encoder). Vanilla `libsvtav1` consumes **no** ROI
side data at all — this is the primary gap that patch fills. It consumes the
**same** `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data, but maps it onto
SVT-AV1's native **per-superblock ROI segment map** (`SvtAv1RoiMapEvt`) rather
than a per-block delta-QP map, because that is the ABI SVT-AV1 exposes:

```bash
# AV1, SVT-AV1, constant-quality CRF (the clean mode for ROI segment steering).
# hwdownload is inherent: libsvtav1 takes system-memory frames.
# Not run on hardware: the test binary has no libsvtav1.
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk -i in.mkv \
       -vf "format=p010le,hwupload,pelorus_analyze_vulkan=roi=1,hwdownload,format=p010le" \
       -c:v libsvtav1 -crf 35 -preset 6 -pelorus_roi 1 out.mkv
```

Each frame's ROI rectangles are rasterized onto the 64×64-superblock grid and
quantised into up to 8 AV1 segments (`MAX_SEGMENTS`); each segment carries a
`seg_qp` qindex *delta* added to the frame base qindex (negative = lower qindex =
more bits, matching the `qoffset` sign). Segment 0 is the zero-delta background,
so superblocks no region covers keep the encoder's default decision. The map is
attached per frame via SVT-AV1's `ROI_MAP_EVENT` private-data node and
`enable_roi_map` is turned on at init. SVT-AV1 never copies or frees the event
(it keeps the bare pointer, and a frame without an ROI node inherits the last
one), so the encoder owns each event and frees it once a newer event has
replaced it and the frames before that newer event are encoded (judged from the
packet count with a 128-frame margin). Memory stays flat over the stream length
instead of growing per frame; `-v verbose` prints the built and peak-live event
counts at close.

Because the library's last event is sticky, a frame that carries no ROI side data
(or an ROI whose deltas all round to zero) would silently keep the previous
frame's ROI. SVT-AV1 documents no reset call, so on the first such frame after a
non-neutral event the encoder submits a neutral event (every superblock in
segment 0, zero delta); the following frames inherit that neutral map. Measured
with libsvtav1 4.2.0, CRF 30, 90 frames, ROI on the first 33 frames only: before
the fix the remaining frames were byte-identical to an encode with the ROI on
all frames (1130764 bytes); with the fix they match an encode without any ROI
(2034761 vs 2036142 bytes).

Use **constant-quality** (`-crf` / `-qp`); SVT-AV1's own variance AQ can override
the segment map, and VBR rate-control redistribution erodes the win (same caveat
as NVENC/QSV). The whole path is compile-gated by SVT-AV1 ≥ 1.6.0 (the release
that introduced the ROI-map ABI); built against an older SVT-AV1 the option warns
once at init and no-ops. Defaults OFF (zero behaviour change). See
[ADR-0121](../adr/0121-svtav1-steering.md).

### Cross-vendor "via Vulkan" (ADR-0114 Tier 2)

The same `-pelorus_roi 1` AVOption is registered on the native Vulkan-Video
encoders `h264_vulkan`, `hevc_vulkan` and `av1_vulkan` (one shared edit in
`vulkan_encode.c`, so all three gain it at once). It consumes the **same**
`AV_FRAME_DATA_REGIONS_OF_INTEREST` side data through
`VK_KHR_video_encode_quantization_map` — one producer steers bit allocation on
every GPU vendor's Vulkan encoder, with no host roundtrip:

```bash
# HEVC, native Vulkan-Video encoder, constant-QP (GPU-resident after upload).
# Not run on hardware for this page: the verified Vulkan-encoder runs used Vulkan decode and no ROI option.
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk -i input.mkv \
       -vf "format=p010le,hwupload,pelorus_analyze_vulkan=roi=1" \
       -c:v hevc_vulkan -rc_mode cqp -qp 30 -pelorus_roi 1 out.mkv
```

The map kind is chosen automatically from the negotiated rate-control mode: a
signed **delta-QP map** under CQP, or an **emphasis map** under CBR/VBR. Each
is written in the texel format the driver advertises: `R8_SINT`, `R16_SINT`
or `R32_SINT` for the delta map (NVIDIA advertises `R8_SINT`, RADV
`R32_SINT`), `R8_UNORM` or `R16_UNORM` for the emphasis map. Use
`-rc_mode cqp` for the delta map; no driver tested so far advertises the
emphasis map.

The path is fully **runtime-probed** and any miss degrades to a one-shot
warning plus pass-through:

- **Device enablement.** The device must enable both the
  `VK_KHR_video_encode_quantization_map` extension and its
  `videoEncodeQuantizationMap` feature. Patch 0009 adds both to FFmpeg's
  optional Vulkan device extensions and features, so a device created by FFmpeg
  (`-init_hw_device vulkan=…`, or one derived from another hardware device)
  enables them automatically whenever the driver supports them; check the
  `-v verbose` line `Using device extension VK_KHR_video_encode_quantization_map`.
  An application that passes its own `AVVulkanDeviceContext` must enable both
  itself. Passing the extension alone through the `device_extensions` option
  is not enough: without the feature the session is invalid
  (`VUID-VkVideoSessionCreateInfoKHR-flags-10264`).
- **Map format and fill path.** The codec must advertise the delta or emphasis
  capability flag, and `vkGetPhysicalDeviceVideoFormatPropertiesKHR` must
  return an entry in one of the formats above that some fill path can write
  (see below). Drivers answer that query with the image usages asked for, so
  the probe asks for the map usage together with the fill usages the encode
  queue family can record, then for the map usage alone. The map image is
  created with the chosen entry's tiling. With no fillable entry the probe
  warns `pelorus_roi: none of the N advertised delta-map format entries is
  fillable on this encode queue family (...); disabled.` and `-v verbose`
  lists every entry with the fill path it allows.
- **Delta range.** Each delta-map value is clamped to the driver's per-codec
  range (`minQpDelta`/`maxQpDelta` for H.264/H.265, `minQIndexDelta`/
  `maxQIndexDelta` for AV1) as well as to the `qoffset` span; a value outside
  the driver range would leave the block QP undefined. When the range excludes
  negative values the probe warns that regions asking for a *lower* QP get no
  extra bits. The value is also clamped to the range of the map's texel
  format.
- **Map size.** A picture whose map (width and height over the texel block,
  rounded up) exceeds the driver's `maxQuantizationMapExtent` disables
  steering with a warning; the map is never clamped. RADV allows 256x256
  texels for H.264 (16x16 px) and 128x68 for H.265 (64x64 px, 8192x4352).

The `qoffset` span is per codec: `51 + 6 × (bit_depth − 8)` QP steps for H.264
and H.265, and 255 qindex steps for AV1 (the NVENC, libaom and SVT-AV1 scale; the
H.26x span would give an AV1 region a fifth of the requested delta). `-v debug`
prints each applied rectangle (`pelorus_roi: texels (0,0)-(10,12) qoffset 0.200 ->
delta 51 (span 255, clamp [0, 127])`).

`-v verbose` prints the chosen map (`Pelorus QP-map steering enabled: delta map,
format 99 (4 bytes/texel), 40x23 texels (16x16 px/texel, linear tiling), dQP
[-51, 51], fill: host raster into a mapped linear image.`; format 99 is
`R32_SINT`, 14 is `R8_SINT`) and `-v debug` prints one
`pelorus_roi: frame N: host-mapped map slot S, R region(s)` line per frame that
binds a map (`host` for the staging copy, `on-GPU` for the compute raster). A
frame with no ROI side data binds no map (zero behaviour change). With a map
bound, `hevc_vulkan` signals per-CU QP deltas (`cu_qp_delta_enabled_flag`) even
under CQP.

Every fill is recorded on the encode command buffer, so the encode queue
family's own capabilities decide how the map can be written
([ADR-0182](../adr/0182-vulkan-qpmap-fill-paths.md)). The probe takes the first
path in this order that an advertised entry allows:

1. **On-GPU raster.** The canonical build-time
   `libavcodec/vulkan/pelorus_qpmap.comp.glsl` compute shader reads the
   coalesced ROI rectangle list from a small SSBO and `imageStore`s the
   per-texel delta/emphasis. Needs `VK_QUEUE_COMPUTE_BIT` on the encode queue
   family, `STORAGE` usage, and an 8-bit map format (`R8_SINT`/`R8_UNORM`, the
   formats the shader declares). No tested driver has compute on its encode
   queue family.
2. **Staging copy.** The host rasterizes into a staging buffer and
   `vkCmdCopyBufferToImage` uploads it. Needs a transfer-, graphics- or
   compute-capable encode queue family and `TRANSFER_DST` usage. NVIDIA uses
   this path.
3. **Host-mapped image.** The host rasterizes straight into a persistently
   mapped `LINEAR` map image in host-visible memory; the command buffer only
   moves the image into `VIDEO_ENCODE_QUANTIZATION_MAP_KHR` layout before the
   encode and back to `GENERAL` after it. Needs a `LINEAR` entry and nothing
   from the queue family. RADV, whose encode queue family has
   `VK_QUEUE_VIDEO_ENCODE_BIT_KHR` only, uses this path.

There is one map image per encode execution context, created with its memory
when the encoder opens, before the video session; a missing memory type or a
failed image disables steering there with a warning, and nothing is allocated
per frame. A context's fence wait guards the image before the host or the GPU
writes it again. All paths share
the same `qoffset`→ΔQP convention, are recorded before the video coding scope
begins, and default off.

> **Driver status (measured 2026-10-03, ADR-0166; RADV row 2026-10-09, ADR-0182).**
>
> | Device / driver | Extension + feature | Delta map advertised | Result |
> | --- | --- | --- | --- |
> | RTX 4090, NVIDIA 615.71.09 | yes | `R8_SINT`, LINEAR only, ΔQP [0, 51] (H.264/H.265), ΔqIndex [0, 255] (AV1) | Steering active on all three encoders; only **positive** offsets (raise QP) take effect |
> | Radeon 610M iGPU, RADV (Mesa 26.2.4) | yes | `R32_SINT`, OPTIMAL, LINEAR and DRM-modifier tiling, usages as queried, ΔQP [-51, 51] (H.264/H.265); encode queue family `VIDEO_ENCODE` only | Steering active on `h264_vulkan` and `hevc_vulkan` through the host-mapped `LINEAR` map; **both** signs of `qoffset` take effect |
> | Arc A380, ANV (Mesa 26.2.4) | no | — | Disabled with a warning (no extension; video encode itself needs `ANV_DEBUG=video-encode`) |
>
> On NVIDIA a `+0.3` `qoffset` over the left half of a 1080p clip at `-qp 30`
> raised the decoded H.264 macroblock QP there from 30 to about 45, cut the
> stream by 30 % (HEVC: 50 %) and left the other half's PSNR unchanged. The
> negative offsets `pelorus_analyze_vulkan roi=1` emits ("spend more bits here")
> clamp to 0 on this driver, so they bind a neutral map. `av1_vulkan` on this
> driver already emits streams libdav1d cannot fully decode without
> `-pelorus_roi`, so its quality was not measured.
>
> On RADV a `-0.3` `qoffset` over the left half of a noisy 640x360 clip at
> `-qp 30` raised that half's PSNR from 36.7 to 44.8 dB (H.264; HEVC 36.8 to
> 45.4 dB) and `+0.3` lowered it to 31.7 dB (HEVC 32.4 dB), with the right
> half within 0.2 dB. Mesa 25.0 RADV has no quantization-map extension and
> passes through with the "device does not enable" warning. See
> [ADR-0114](../adr/0114-encoder-steering.md) Tier 2,
> [ADR-0166](../adr/0166-vulkan-qpmap-activation.md) and
> [ADR-0182](../adr/0182-vulkan-qpmap-fill-paths.md).

## Encoder motion-search seeding (NVENC external ME hints)

`vf_pelorus_mc_vulkan` emits a per-block integer-pel motion-vector field as the
`PEL_SEC_MOTION` interop section (see [metrics/mc.md](../metrics/mc.md)). Vanilla
NVENC always runs the ASIC's own motion search and cannot be seeded with
externally-computed vectors; the Pelorus patch stack adds a `-pelorus_me_hints 1`
AVOption (NVENC, **H.264/HEVC only**) that reads that MV field and feeds it to
NVENC's external-ME-hint input (`enableExternalMEHints` +
`NV_ENC_PIC_PARAMS::meExternalHints`):

```bash
# HEVC, NVENC: produce the MV field, then let NVENC seed its search from it.
# Verified on an RTX 4090, exit code 0, 120 frames, software decode. Behind
# NVDEC this graph fails on the CUDA hop (#296); a graph ending in one writing
# filter (for example deband) works there.
ffmpeg -init_hw_device vulkan=vk:0,disable_multiplane=1 -filter_hw_device vk \
  -i in.mkv \
  -vf "format=nv12,hwupload,pelorus_mc_vulkan=bsize=16:search=24,hwupload=derive_device=cuda" \
  -c:v hevc_nvenc -preset p5 -cq 28 -pelorus_me_hints 1 out.mkv
```

**The value is encode SPEED, not quality.** The MV field is a *hint* the
fixed-function encoder can use to skip or shorten its own motion search; it does
not change the rate-control target. One L0 candidate is supplied per 16×16 block
(the SDK-documented external-hint granularity for AVC/HEVC); the producer's block
grid is resampled onto that 16×16 grid by center-block sampling, and each vector
is clamped to NVENC's hint bitfield range (mvx S12, mvy S10).

The option defaults OFF (zero behaviour change). Capability degradation is
graceful: AV1 is skipped (NVENC's AV1 path uses a different per-superblock hint
struct), a device that reports no external-ME support warns once and passes
through, and an FFmpeg built against ffnvcodec headers without the external-ME
structs (pre-SDK-8.1) no-ops at init with a one-shot warning. If a frame carries
no `PEL_SEC_MOTION` section (no `pelorus_mc_vulkan` in the chain, or a frame the
filter produced no vectors for), the patch submits one zero-MV candidate per
16×16 block for that frame and logs one warning per encoder instance. A session
opened for external hints requires a populated hint buffer on every frame:
submitting zero candidates fails the whole encode on the device (`EncodePicture
failed!: invalid param (8): SetupCEAHints failed`, RTX 4090, driver 615.71.09),
and the SDK documents no other "no hint" value. A zero-MV seed is neutral but not
identical to hints-off: the ASIC still searches around the co-located block, so
the stream differs from an encode without `-pelorus_me_hints`. On-hardware A/B (RTX 4090, `hevc_nvenc -preset p7`,
1280×720, 600 frames): hints engaged but produced a ~2–3% *slowdown* (hints-off
114 fps vs hints-on 110 fps) — the per-frame hint upload outweighs ME-search
savings on Ada VDEnc at p7. Kept default off and documented as an honest negative
(see bench-results.md v0.9). It may still help on slower ME engines or
higher-motion content.
See [ADR-0114](../adr/0114-encoder-steering.md) Tier 3 and
[ADR-0116](../adr/0116-pelorus-mc.md).

## Hardware AV1 film grain (NVENC)

`vf_pelorus_grain_estimate_vulkan` measures film grain and emits AV1 (AOM)
film-grain-synthesis parameters as a native `AV_FRAME_DATA_FILM_GRAIN_PARAMS`
(`AV_FILM_GRAIN_PARAMS_AV1`) and the `PEL_SEC_FILMGRAIN` interop section (see
[metrics/grain_estimate.md](../metrics/grain_estimate.md)). AV1 *software*
encoders already consume that native side data; HEVC is covered by the
`pelorus_fgs` BSF (see [grain-fgs-bsf.md](grain-fgs-bsf.md)). Stock `av1_nvenc`
does neither — it never enables NVENC's hardware AV1 film-grain synthesis, so
the estimate is dropped and the grain is coded as residual. The Pelorus patch
stack adds a `-pelorus_film_grain 1` AVOption (**`av1_nvenc` only**) that carries
the estimate into NVENC's AV1 film-grain config so the decoder re-synthesizes
the grain:

```bash
# AV1, NVENC: estimate the grain, then let NVENC re-synthesize it in hardware.
# Verified on an RTX 4090, exit code 0, 120 frames (8-bit input), software
# decode. Behind NVDEC this graph fails on the CUDA hop (#296); a graph ending in
# one writing filter (for example deband) works there.
ffmpeg -init_hw_device vulkan=vk:0,disable_multiplane=1 -filter_hw_device vk \
  -i in.mkv \
  -vf "format=nv12,hwupload,pelorus_grain_estimate_vulkan,hwupload=derive_device=cuda" \
  -c:v av1_nvenc -cq 32 -pelorus_film_grain 1 out.mkv
```

The win is the same as all FGS: the encoder spends a few bytes of grain model
instead of megabits of structureless residual, and the viewer still sees grain.
Pair it with `pelorus_denoise_vulkan` upstream (remove the grain before encode,
re-synthesize it at decode) for the full noise-tax recovery.

How it works: at init the patch sets `NV_ENC_CONFIG_AV1::enableFilmGrainParams`
and points `filmGrainParams` at a persistent `NV_ENC_FILM_GRAIN_PARAMS_AV1`; per
frame it refills that struct from the estimate (native channel preferred, the
interop section as a fallback) and raises the AV1 pic-params
`filmGrainParamsUpdate` flag (a time-varying model). The `AVFilmGrainAOMParams`
set maps field-for-field onto the NVENC struct. NVENC takes the raw AV1 syntax
values, so the chroma multipliers gain the AV1 `+128` bias and the chroma offsets
the `+256` bias on the way in (`AVFilmGrainAOMParams` and the
`PEL_SEC_FILMGRAIN` section carry them unbiased, as dav1d exports them).

The option defaults OFF (zero behaviour change) and is registered on `av1_nvenc`
only — H.264/HEVC NVENC have no AV1 film grain. It is compile-gated by a new
`NVENC_HAVE_AV1_FILM_GRAIN` macro (ffnvcodec SDK 12.0+, where the AV1 encoder and
the film-grain struct landed); an FFmpeg built against older headers warns once
at init and passes through. If a frame carries no usable AV1 grain estimate the
update flag stays clear, so NVENC keeps the previous params (or the zero-init
no-op). On an RTX 4090 (driver 615.71.09) the encoded stream signals
`film_grain_params_present=1` with `apply_grain=1` in every frame header, and
dav1d synthesizes the grain at decode. A libaom film-grain test vector decoded
through libdav1d keeps every film-grain syntax value except `grain_seed`, which
NVENC chooses itself. No grain-match or BD-rate number ships yet. See
[ADR-0118](../adr/0118-nvenc-av1-filmgrain.md),
[ADR-0115](../adr/0115-grain-estimate.md), and
[ADR-0114](../adr/0114-encoder-steering.md).

## Notes

- Build FFmpeg with the Pelorus patch stack applied and `libpelorus` installed
  (see [ffmpeg-patches/README.md](../../ffmpeg-patches/README.md)).
- Prefer **10-bit** intermediate/output even for 8-bit delivery — it preserves
  the dithered gradient through quantization (research 0101).
- List options: `ffmpeg -h filter=pelorus_deband_vulkan`.

## Carrying the side-data blob into the bitstream (`udu_sei`)

The `PelorusSideData` blob rides each frame as `AV_FRAME_DATA_SEI_UNREGISTERED`.
Encoders write it into the H.264 or HEVC stream as a user-data-unregistered SEI
message when `-udu_sei 1` is set (default off):

| Encoder | Source of the option | Form in the stream | Size limit per picture |
| --- | --- | --- | --- |
| `h264_nvenc` | stock FFmpeg; truncation drop in shared series patch 0004, carrier in patch 0022 | zero-free carrier | none found; carriers tested to 60 017 bytes (RTX 4090) |
| `hevc_nvenc` | stock FFmpeg; limit in shared series patch 0003, truncation drop in 0004, carrier and map stripping in patch 0022 | zero-free carrier | 768 bytes of SEI (see below) |
| `h264_qsv` | patch 0019, budget in patch 0023 | blob | 40 960 bytes of SEI per picture (see below) |
| `hevc_qsv` | patch 0019, budget in patch 0023 | blob | 4 040 bytes of SEI per picture (see below) |
| `h264_vulkan`, `hevc_vulkan` | patch 0020 | blob | none measured |

On an RTX 4090 with driver 615.78.08, NVENC writes a SEI truncated when the payload needs more
emulation prevention bytes than about a third of its length (`ceil(P / 3) + 3`,
pinned on that driver), and the decoder then drops it without an error
(issue #284). The analyze maps of flat content are mostly zero bytes
and reach that. Both NVENC encoders therefore write every Pelorus blob in its
zero-free carrier form: UUID `3f9b37b8-fd9a-4621-920e-9b78b55cf9b5`, then the
COBS-encoded blob, with no zero byte, one byte longer per 254 bytes
([ADR-0183](../adr/0183-sidedata-zero-free-carrier.md),
[interop ABI 1.5](../api/interop-abi.md#zero-free-carrier-form-abi-15)). A reader
of the decoded frames turns it back into the blob with `pel_blob_unwrap()`;
`-loglevel verbose` names the form once. A payload from another producer is
written as it is, unless NVENC would truncate it: patch 0004 of the shared
FFmpeg fix series ([ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md)) then
leaves it out and warns (`Not writing a N-byte user data unregistered SEI at
pts ...: NVENC would write it truncated`): the first time as a warning, later
at `-loglevel verbose`, with the total when the encoder closes. Before, the
truncated SEI reached the stream and the decoder discarded it. A carrier holds
no zero byte and is never affected:

```text
[h264_nvenc] udu_sei: Pelorus side data is written in its zero-free carrier form (UUID 3f9b37b8-fd9a-4621-920e-9b78b55cf9b5), which needs no emulation prevention.
```

A reader built before ABI 1.5 (VMAFx before its re-pin) ignores the carrier and
sees no Pelorus data on frames from an NVENC stream.

`hevc_nvenc` fails a picture whose parameter sets and SEI exceed 1024 bytes
([ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md),
[research 0181](../research/0181-hevc-nvenc-sei-header-budget.md)). Patch 0003
of the shared series keeps 768 of them for the side data, in frame order, and
patch 0022 strips a Pelorus blob to fit:

- an entry that fits is written whole; a Pelorus blob is charged as its
  carrier, which needs no emulation prevention;
- a Pelorus blob that does not fit is written without its per-cell maps. Every
  scalar section stays, and the carrier unwraps to what
  `pelorus_analyze_vulkan=maps=0` writes. The analyze maps fit only up to about
  90 cells (flat content: 10x6 cells at 320x180), so at real frame sizes the
  stream carries the scalars and the maps stay in the filter graph;
- any other entry that does not fit is not written.

The encoder warns at the first stripped blob and the first dropped entry, logs
later ones at `-loglevel verbose`, and prints both totals when it closes:

```text
[hevc_nvenc] udu_sei: frame pts 0: the 12424-byte Pelorus side data is written without its per-cell maps (184 bytes): hevc_nvenc writes at most 1024 bytes of parameter sets and SEI per picture.
[hevc_nvenc] Not writing a 2017-byte user data unregistered SEI at pts 0: hevc_nvenc fails a picture whose parameter sets and SEI exceed 1024 bytes.
```

Without these patches the encode stopped with `Failed locking bitstream buffer: out
of memory` (`-12`). `ffmpeg-patches/test/nvenc-udu-sei-smoke.sh` checks the
cases, the flat-content carriers of issue #284 included, on an NVIDIA host and
exits 77 with the reason elsewhere.

```bash
# Verified on an Arc A380, exit code 0, 120 frames.
LIBVA_DRIVER_NAME=iHD ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD130 -init_hw_device vulkan=vk@va \
       -hwaccel vaapi -hwaccel_device va -hwaccel_output_format vaapi -i input.mkv \
       -vf "hwmap=derive_device=vulkan,pelorus_analyze_vulkan,pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi,hwmap=derive_device=qsv,format=qsv" \
       -c:v hevc_qsv -udu_sei 1 -q:v 24 out.mkv
```

### QSV: the SEI space per picture

The QSV runtime has a fixed amount of room for SEI in each picture, and larger payloads damage the
encoded access unit: on `hevc_qsv` no picture of the stream decodes any more, on `h264_qsv` the encode
fails ([#286](https://github.com/VMAFx/pelorus/issues/286), [research 0286](../research/0286-qsv-udu-sei-budget.md)).
The room is per picture, not per payload: it is the sum of the SEI messages as handed to the runtime
(type byte, size bytes and payload), and on `h264_qsv` it also counts the emulation prevention bytes.
Measured on an Intel Arc A380 (`iHD` 26.3.5, `vpl-gpu-rt` 26.3.5, `libvpl` 2.17.0) at 640x360, 1080p and
2160p alike, the limits are 4 107 bytes for `hevc_qsv` and about 42 420 bytes for `h264_qsv`. Patch 0023
keeps 4 040 and 40 960 bytes, less what the A/53 caption payload already uses, and treats each
`SEI_UNREGISTERED` entry in frame order like `hevc_nvenc` does ([ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md)):

- an entry that fits is written whole;
- a Pelorus blob that does not fit is written without its per-cell maps (every scalar section stays, the
  maps are absent as the ABI defines), which is what `pelorus_analyze_vulkan=maps=0` writes;
- any other entry that does not fit, and a blob whose scalars do not fit, is not written.

QSV writes the plain blob; the zero-free carrier is for NVENC. At 1080p the `cell=32` blob (12 424
bytes) therefore reaches an `hevc_qsv` stream as its 184 scalar bytes and an `h264_qsv` stream whole;
the `cell=16` blob (49 144 bytes) loses its maps on both. Only two payloads per picture are queued
(`QSV_MAX_ENC_PAYLOAD`); the warning of patch 0019 names a third. The encoder warns at the first
stripped blob and the first dropped entry, logs later ones at `-loglevel verbose`, and prints both totals
when it closes:

```text
[hevc_qsv] udu_sei: frame pts 0: the 12424-byte Pelorus side data is written without its per-cell maps (184 bytes): the QSV runtime reserves too little SEI space per picture for more.
```

On the A380, 48 analyze blobs (184 to 49 144 bytes) through both encoders gave no damaged access unit.
Another runtime or GPU needs a new measurement; the budgets are the constants of
`pelorus_sei_fit_qsv.h`.

Known issue: `h264_qsv` with `-udu_sei 1` can crash intermittently, more often with larger payloads
(2 KB: none in 80 runs; 16 KB: about 12 to 15 in 80; 30 KB: 11 to 30 in 80; none in 60 runs without
payloads, none in 80 on `hevc_qsv`). The crash happens inside Intel's QSV runtime and reproduces with stock
FFmpeg (its A/53 captions too), so Pelorus does not cause it, and the budget does not prevent it. A fix
is being worked on outside Pelorus ([research 0286](../research/0286-qsv-udu-sei-budget.md)).

AV1 encoders do not carry it. The tester stage `sidedata_roundtrip` reads the
blob back from the stream and from a decode: [tester kit stages](tester.md).
