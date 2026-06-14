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
     pelorus_deband_vulkan=range=15,
     pelorus_denoise_vulkan=sigma=2.0,
     pelorus_grain_estimate=meta=1"
```

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

## Notes

- Build FFmpeg with the Pelorus patch stack applied and `libpelorus` installed
  (see [ffmpeg-patches/README.md](../../ffmpeg-patches/README.md)).
- Prefer **10-bit** intermediate/output even for 8-bit delivery — it preserves
  the dithered gradient through quantization (research 0101).
- List options: `ffmpeg -h filter=pelorus_deband_vulkan`.
