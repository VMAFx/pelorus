<!-- markdownlint-disable MD013 -->
# `pelorus_deband_vulkan` — smart deband

A Vulkan compute debander that flattens the false contours a hardware encoder
amplifies, then re-injects sub-visible blue-noise so the gradient survives
quantization. Zero-copy: it operates on `AV_PIX_FMT_VULKAN` frames and never
leaves VRAM. Algorithm background: [research 0101](../research/0101-smart-deband.md);
decision: [ADR-0102](../adr/0102-flagship-smart-deband.md).

## What it does

For each pixel it samples reference taps within `range` pixels at a
pseudo-random angle, decides whether the neighborhood is "flat" (per-plane
threshold), and if so blends toward the tap average — gated by a
local-variance detail mask so real texture/edges are left alone — then adds
zero-mean TPDF/blue-noise grain. Output is clamped to `[0,1]` in the Vulkan
float image domain.

## Options

| Option | Type | Default | Meaning |
|---|---|---|---|
| `range` | int 1–31 | 15 | reference-sampling radius in pixels |
| `thry` | float 0–0.25 | 0.012 | luma threshold (normalized to full range) |
| `thrc` | float 0–0.25 | 0.012 | chroma threshold |
| `grainy` | float 0–0.4 | 0.006 | luma grain amplitude (≈1.5 LSB @ 8-bit) |
| `grainc` | float 0–0.4 | 0.0 | chroma grain amplitude (keep low/zero) |
| `softness` | float 0–1 | 0.5 | soft-blend transition width; `0` = hard switch |
| `detail` | float 0–0.25 | 0.06 | detail-mask activity threshold |
| `sample` | column/square/row/square_rot | square | reference-tap topology |
| `blur` | average/allrefs | average | flat-test mode |
| `dither` | none/bayer8/bluenoise | bluenoise | grain pattern |
| `dynamic` | bool | 1 | re-seed grain each frame |
| `protect` | bool | 1 | gate debanding off textured regions |
| `planes` | int bitmask | 0xF | planes to process |
| `meta` | bool | 0 | attach the Pelorus interop side-data blob |

## Example

```bash
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_deband_vulkan=range=15:thry=0.012:dither=bluenoise:dynamic=1:protect=1" \
       -c:v hevc_nvenc -cq 28 out.mkv      # codec-agnostic; or av1_nvenc / hevc_qsv / hevc_vaapi
```

The filter is pure pre-processing — it improves HEVC (rivaling x265) and AV1
encodes identically; pick whatever hardware encoder you target.

Output: a debanded video stream; with `meta=1`, each frame additionally carries
a Pelorus side-data blob (`PEL_SEC_BANDING`) a downstream `vf_libvmaf*` can read
(see [interop ABI](../api/interop-abi.md)). The filter logs nothing on the happy
path; errors surface as a non-zero ffmpeg exit with an `[pelorus_deband_vulkan]`
message.

## Interactions & limitations

- **Requires a Vulkan hwframes context** (`AV_PIX_FMT_VULKAN` input). Pair with
  `hwupload`/`hwdownload` only at the pipeline edges; never mid-graph (it breaks
  zero-copy). See [usage/ffmpeg.md](../usage/ffmpeg.md).
- **`meta=1`** marks the banding section present with a config-derived summary;
  *measured* per-cell maps come from `vf_pelorus_analyze` (roadmap).
- **`coupling`** (all-planes-agree, requires 4:4:4) is intentionally not exposed
  yet — pre-encode wants per-plane independence.
- **VMAF caveat**: a per-frame deband-vs-source score may drop slightly (added
  dither reads as "error"); the win is on the encode round-trip. Tune amplitude
  with VMAF-in-the-loop (below), not against per-frame scores.

## VMAF-measured tuning loop

Deband strength trades quality vs bitrate per content. Use vmafx as the oracle
([ADR-0106](../adr/0106-autotune-control-plane.md)):

```bash
# in-graph, one pass: deband then score against the source
ffmpeg -i src.mkv -i src.mkv -filter_complex \
  "[0:v]hwupload,pelorus_deband_vulkan=thry=0.012,hwdownload,format=p010le[pre];
   [pre][1:v]libvmaf_tune=recommend_target_vmaf=93" -f null -
# sweep thry/grainy with vmaf-tune's optimizer; pick the (quality, bitrate) winner
```

Defaults are the dark-scene preset from research 0101; raise `thry` for heavier
banding, lower `grainy` if a per-title VMAF regresses.

The subset of options `vmaf-tune` sweeps (`range`, `thry`, `thrc`, `grainy`,
`grainc`, `softness`, `detail`, `dither`, `dynamic`, `protect`) is a **frozen
control-plane contract** — see [control-plane.md](../api/control-plane.md)
([ADR-0110](../adr/0110-avoption-control-plane-contract.md)). `sample`, `blur`,
`planes`, and `meta` are intentionally out of that contract.
