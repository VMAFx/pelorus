<!-- markdownlint-disable MD013 -->
# The zero-copy FFmpeg pipeline

Pelorus filters are libavfilter Vulkan filters: they consume and produce
`AV_PIX_FMT_VULKAN` frames. The win comes from keeping frames in VRAM from
decode to encode — so `hwupload`/`hwdownload` belong only at the pipeline edges,
never between Pelorus stages.

**Codec-agnostic.** The filters pre-process pixels; the encoder is your choice.
The examples use `hevc_nvenc`, but swap in any hardware encoder —
`av1_nvenc`, `hevc_qsv` / `av1_qsv`, `hevc_vaapi` / `av1_vaapi`, `hevc_amf` /
`av1_amf`. Deband/denoise/motion-hints help HEVC (rivaling x265) and AV1 alike.

## Minimal: software decode → upload → deband → download → HW encode

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
       -i input.mkv \
       -vf "format=p010le,hwupload,
            pelorus_deband_vulkan=range=15:dither=bluenoise:dynamic=1,
            hwdownload,format=p010le" \
       -c:v hevc_nvenc -cq 28 out.mkv      # or av1_nvenc / hevc_qsv / hevc_vaapi
```

## Full zero-copy: Vulkan decode → deband → Vulkan HW encode

```bash
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_deband_vulkan=range=15:thry=0.012" \
       -c:v hevc_nvenc -cq 28 out.mkv      # HEVC; or av1_nvenc for AV1
```

When the decoder, filter, and encoder all speak Vulkan/VRAM, no frame touches
system RAM.

## Chaining stages (as more filters land)

```bash
-vf "pelorus_analyze_vulkan,
     pelorus_grain_estimate_vulkan=strength=2.0,
     pelorus_mc_vulkan=bsize=16:search=24,
     pelorus_denoise_vulkan=sigma=2.0,
     pelorus_deband_vulkan=range=15"
```

`pelorus_grain_estimate_vulkan` reads the **source** grain, so it runs before
denoise removes it; the encoder re-synthesizes the grain from the emitted params.
`pelorus_mc_vulkan` is a pass-through producer: it emits a per-block motion-vector
field (`PEL_SEC_MOTION`) as an encoder ME hint (encode-speed, not quality; the
NVENC `NV_ENC_EXTERNAL_ME_HINT` consumer is a gated follow-up — ADR-0116/0114).
AVOptions: `bsize` (block edge, default 16), `search` (radius, default 24), `meta`.

Each stage runs in VRAM; the Pelorus side-data blob accumulates sections and
rides every frame to the encoder.

## VMAF-in-the-loop (autotune with vmafx)

Score the processed-then-encoded output against the source, in the same graph:

```bash
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
`-pelorus_roi 1` AVOption to both that consumes the **same** side data into the
encoder's dense per-block delta-QP map (NVENC `qpDeltaMap`, QSV `mfxExtMBQP`):

```bash
# HEVC, NVENC, constant-QP (the clean mode for QP-map steering):
ffmpeg ... -vf "hwupload,pelorus_analyze_vulkan=roi=1,hwdownload,format=p010le" \
       -c:v hevc_nvenc -rc constqp -qp 30 -pelorus_roi 1 out.mkv

# HEVC, Intel QSV, CQP (global_quality); -pelorus_roi requires CQP rate control:
ffmpeg ... -vf "...,pelorus_analyze_vulkan=roi=1,..." \
       -c:v hevc_qsv -global_quality 30 -pelorus_roi 1 out.mkv
```

The option is registered on `hevc_qsv`, `h264_qsv`, `hevc_nvenc`, `h264_nvenc`,
`av1_nvenc` and `libsvtav1` (swap the encoder above accordingly; the SVT-AV1
mapping differs — see below). It defaults OFF (zero
behaviour change). Use **constant-QP** and the encoder's own spatial/temporal AQ
OFF: the encoder AQ overrides the delta-QP map, and VBR rate-control
redistribution erodes the perceptual win.

Capability degradation is graceful: on QSV under a non-CQP rate-control method
the option emits a one-shot warning and passes through unchanged; if FFmpeg was
built against a oneVPL/MediaSDK older than API 1.13 (no `mfxExtMBQP`) it likewise
warns once at init and no-ops. For QSV the dense per-block map fully supersedes
FFmpeg's coarse `mfxExtEncoderROI` rectangle path when the option is on (the two
are mutually exclusive). See [ADR-0114](../adr/0114-encoder-steering.md).

### SVT-AV1 software (ADR-0121)

The same `-pelorus_roi 1` AVOption is registered on `libsvtav1` (the `av1_svt`
flagship modern AV1 software encoder). Vanilla `libsvtav1` consumes **no** ROI
side data at all — this is the primary gap that patch fills. It consumes the
**same** `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data, but maps it onto
SVT-AV1's native **per-superblock ROI segment map** (`SvtAv1RoiMapEvt`) rather
than a per-block delta-QP map, because that is the ABI SVT-AV1 exposes:

```bash
# AV1, SVT-AV1, constant-quality CRF (the clean mode for ROI segment steering):
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
`enable_roi_map` is turned on at init.

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
# HEVC, native Vulkan-Video encoder, constant-QP (zero-copy end to end):
ffmpeg -init_hw_device vulkan ... \
       -vf "hwupload,pelorus_analyze_vulkan=roi=1" \
       -c:v hevc_vulkan -rc_mode cqp -qp 30 -pelorus_roi 1 out.mkv
```

The map kind is chosen automatically from the negotiated rate-control mode: a
signed **delta-QP map** (`R8_SINT`) under CQP, or an **emphasis map**
(`R8_UNORM`) under CBR/VBR. The path is fully **runtime-probed**: the
`VK_KHR_video_encode_quantization_map` extension must be enabled, the codec must
advertise the matching delta/emphasis capability flag, and a usable map format +
texel size must be returned for the profile. Any miss degrades to a one-shot
warning + pass-through; a frame with no ROI binds no map (zero behaviour change).

The map texel image is filled **on the GPU**: a `pelorus_qpmap` compute shader
(`libpelorus/shaders/pelorus_qpmap.comp`, mirrored inline in the patch) reads the
coalesced ROI rectangle list from a small SSBO and `imageStore`s the per-texel
delta/emphasis directly, eliminating the host per-texel raster + staging upload.
This on-GPU path is preferred whenever the encode queue family also advertises
`VK_QUEUE_COMPUTE_BIT` (so the dispatch records on the encode command buffer with
no cross-queue ownership transfer); otherwise it transparently falls back to the
host raster + `vkCmdCopyBufferToImage` path. Both share the same `qoffset`→ΔQP
convention and default off.

> **Driver maturity.** This Nov-2024 extension has uneven beta Linux driver
> coverage. Some drivers (including this project's current dev box) fail
> `*_vulkan` *encode* at init with "Driver does not support required encode
> feedback flags (BUFFER_OFFSET and BYTES_WRITTEN)" — a driver gap unrelated to
> the QP-map path, which simply never runs there. The patch is compile-verified;
> an on-HW BD-rate A/B is a follow-up once a driver advertising the extension is
> available. See [ADR-0114](../adr/0114-encoder-steering.md) Tier 2.

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
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_mc_vulkan=bsize=16:search=24,hwdownload,format=p010le" \
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
no `PEL_SEC_MOTION` section the hint buffer is left unset for that frame, so
NVENC runs its own search. On-hardware A/B (RTX 4090, `hevc_nvenc -preset p7`,
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
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan,hwdownload,format=p010le" \
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
set maps field-for-field onto the NVENC struct.

The option defaults OFF (zero behaviour change) and is registered on `av1_nvenc`
only — H.264/HEVC NVENC have no AV1 film grain. It is compile-gated by a new
`NVENC_HAVE_AV1_FILM_GRAIN` macro (ffnvcodec SDK 12.0+, where the AV1 encoder and
the film-grain struct landed); an FFmpeg built against older headers warns once
at init and passes through. If a frame carries no usable AV1 grain estimate the
update flag stays clear, so NVENC keeps the previous params (or the zero-init
no-op). No grain-match or BD-rate number ships yet — the estimate is wired into
NVENC's AV1 film-grain config and the on-hardware proof is a documented
follow-up. See [ADR-0118](../adr/0118-nvenc-av1-filmgrain.md),
[ADR-0115](../adr/0115-grain-estimate.md), and
[ADR-0114](../adr/0114-encoder-steering.md).

## Notes

- Build FFmpeg with the Pelorus patch stack applied and `libpelorus` installed
  (see [ffmpeg-patches/README.md](../../ffmpeg-patches/README.md)).
- Prefer **10-bit** intermediate/output even for 8-bit delivery — it preserves
  the dithered gradient through quantization (research 0101).
- List options: `ffmpeg -h filter=pelorus_deband_vulkan`.
