<!-- markdownlint-disable MD013 -->
# vf_pelorus_grain_estimate_vulkan

A film-grain-synthesis (FGS) parameter **estimator**, run as a zero-copy
pass-through pass in VRAM. It is the dual of the denoise lever
([ADR-0112](../adr/0112-temporal-denoise.md)): grain is temporally incoherent,
so a block encoder cannot inter-predict it and re-codes it as residual every
frame — an enormous, structureless bit tax. The win is to **remove** the grain
before encode (`pelorus_denoise`) and **re-synthesize** it at the decoder from a
compact parameter set. This filter measures those parameters. See
[ADR-0115](../adr/0115-grain-estimate.md) for the design.

The frame passes through unchanged; only side data is added. Grain is the one
Pelorus stage that is codec-*specific* (deband / denoise / motion help any
encoder): AV1 carries an AOM `film_grain` model in the bitstream, HEVC/H.265 +
VVC/H.266 carry the ITU-T H.274 film-grain-characteristics (FGC) SEI.

## Algorithm

Grain is high-frequency, near-zero-mean additive noise whose strength varies
with local intensity — exactly what the AV1 / H.274 piecewise **scaling
function** models. A single Vulkan compute pass measures it, per intensity band:

1. **High-pass** — `resid = luma − mean3x3` (a 3×3 box low-pass removes the
   structural content; the residual is grain).
2. **Edge gate** — a pixel whose 3×3 neighbourhood range exceeds `edge` is an
   edge / texture and is **excluded**; the estimate is taken only over
   locally-flat fields, where the residual is grain.
3. **Bin by intensity** — each surviving pixel's `resid²` is accumulated into
   one of 8 luma-intensity bins with a per-bin pixel count → per-band RMS
   residual = grain standard deviation at that intensity.
4. **AR proxy** — a global lag-1 spatial-correlation accumulator
   (`Σ resid·resid_right` over flat pixels) gives a coarse autocorrelation
   coefficient → a conservative AR seed.

The reduction is sliced (16 slices) to cut atomic contention and summed on the
host. The host maps the per-band RMS to the AV1 AOM piecewise scaling function
`y_points[value, scaling]` (band centre → `value`, RMS·`strength` → `scaling`),
takes AV1-legal defaults for the shifts, and seeds `ar_coeffs_y[0]` from the
lag-1 coefficient.

The standalone reference shader is
`libpelorus/shaders/pelorus_grain_estimate.comp`; the filter's inline GLSL
implements the same algorithm (kept in lockstep, AGENTS hard rule 4).

## Options

All thresholds are normalized in `[0,1]`, independent of bit depth.

| Option | Default | Range | Meaning |
|---|---|---|---|
| `edge` | 0.06 | 0–1 | 3×3 neighbourhood range above which a pixel is an edge and is excluded from the grain estimate |
| `strength` | 2.0 | 0–64 | scales the measured per-band RMS residual to the AV1 `[0,255]` scaling-function value (synthesis intensity) |
| `model` | `aom` | `aom`/`h274` | FGS model the estimate targets (`aom` = AV1; `h274` = HEVC/VVC SEI scalars) |
| `native` | on | bool | also attach a native `AV_FRAME_DATA_FILM_GRAIN_PARAMS` (AV1) so a downstream FFmpeg AV1 encoder honours it with no Pelorus BSF |

`strength` is the main knob: raise it if the synthesized grain looks too subtle,
lower it if too heavy. The vmafx `vmaf-tune` autotune
([ADR-0106](../adr/0106-autotune-control-plane.md)) can sweep it against the
encoded-VMAF oracle.

## Output

Two side-data channels are attached to each frame:

- **`PEL_SEC_FILMGRAIN`** (Pelorus interop, `AV_FRAME_DATA_SEI_UNREGISTERED`,
  UUID-keyed) — the codec-neutral intent plus the AV1 params field-for-field,
  the H.274 mode scalars (`model_id`, `blending_mode`, `log2_scale`), and the
  `grain_model` tag. No ABI bump: the section was reserved in
  [ADR-0103](../adr/0103-interop-sidedata-abi.md). For vmafx and downstream
  Pelorus tooling.
- **`AV_FRAME_DATA_FILM_GRAIN_PARAMS`** (`AV_FILM_GRAIN_PARAMS_AV1`, when
  `native=1`) — the AV1 AOM params on the standard FFmpeg channel, so an AV1
  encoder / muxer that already reads it acts with no extra plumbing (the same
  "emit a standard side-data channel the encoder already reads" strategy the
  analyze filter uses for ROI, [ADR-0114](../adr/0114-encoder-steering.md)).

## Pipeline placement

Grain estimation reads the **source** grain, so it runs *before* denoise (which
removes it). The downstream encoder synthesizes the grain back from the emitted
params:

```
hwupload → pelorus_grain_estimate → pelorus_denoise → pelorus_deband → (hwdownload) → encoder (synthesizes grain)
```

## FGS target (honest scope)

AV1 (AOM) is the authoritative target for v0.x: FFmpeg has a complete public
`AVFilmGrainAOMParams` struct and a native side-data channel, so the estimate is
consumable today with no extra bitstream plumbing. Two more encoder legs now
ship: HEVC/H.265 round-trips through the `pelorus_fgs` H.274 FGC SEI bitstream
filter ([ADR-0117](../adr/0117-grain-fgs-bsf.md), see
[grain-fgs-bsf.md](../usage/grain-fgs-bsf.md)), and `av1_nvenc` consumes the
estimate via the `-pelorus_film_grain` AVOption that drives NVENC's hardware AV1
film-grain synthesis ([ADR-0118](../adr/0118-nvenc-av1-filmgrain.md)). **Honest
caveat: the H.274 model written by `pelorus_fgs` is static** (one frequency-domain
model for the clip, not per-frame), and the full per-lag AR coefficient fit,
explicit chroma-grain estimation, the H.274 component-model tables, and the
per-frame / H.264 / VVC legs remain deferred (ADR-0115). The estimator and the
parameter contract are complete and codec-neutral. No BD-rate / visual-match
proof is shipped with this filter; it must be measured under the
[ADR-0111](../adr/0111-benchmark-methodology.md) methodology in a follow-up.

## Usage

```bash
# AV1: estimate grain on the source, denoise it, let the AV1 encoder
# re-synthesize it from the attached AV_FRAME_DATA_FILM_GRAIN_PARAMS.
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan=strength=2.0,pelorus_denoise_vulkan=strength=0.4,hwdownload,format=yuv420p" \
  -c:v libaom-av1 -crf 30 out.mkv

# Inspect the estimate (model only; no encode):
ffprobe -f lavfi -i "...,pelorus_grain_estimate_vulkan" -show_frames | grep -i film_grain
```

For HEVC/H.265 + VVC/H.266, select `model=h274` to populate the H.274 mode
scalars in `PEL_SEC_FILMGRAIN`:

```bash
# HEVC: estimate + denoise, then re-synthesize the grain in the HEVC stream via
# the pelorus_fgs H.274 FGC SEI bitstream filter (ADR-0117). The H.274 scalars
# carried in PEL_SEC_FILMGRAIN drive the SEI the BSF writes. The H.274 model is
# static (one model per clip), not per-frame.
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan=model=h274:strength=2.0,pelorus_denoise_vulkan=strength=0.4,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 -bsf:v pelorus_fgs out.mkv
```

All three legs now round-trip end-to-end: AV1 software encoders via the native
`AV_FRAME_DATA_FILM_GRAIN_PARAMS` channel, HEVC via the `pelorus_fgs` H.274 FGC
SEI bitstream filter ([ADR-0117](../adr/0117-grain-fgs-bsf.md), static model),
and `av1_nvenc` via `-pelorus_film_grain` into NVENC's hardware AV1 film-grain
synthesis ([ADR-0118](../adr/0118-nvenc-av1-filmgrain.md)). The H.264 and VVC
legs and a per-frame H.274 model remain deferred.

## Frame metadata (the `tune=auto` grain discriminator)

Alongside the side-data, the filter emits two per-frame scalars as `lavfi.pelorus.*`
frame metadata (the [ADR-0136](../adr/0136-analyze-frame-metadata.md) `av_dict_set`
pattern — no interop ABI or shader change), so the content-adaptive router
([ADR-0142](../adr/0142-tune-auto-content-router.md)) can read the grain estimate
without parsing the `PEL_SEC_FILMGRAIN` blob:

| key | meaning |
|---|---|
| `lavfi.pelorus.grain_sigma` | peak per-band RMS residual (grain stddev) over the populated intensity bands, normalized `[0,1]`. Measured **only** on edge-gated locally-flat pixels, so real structure is excluded by construction — what survives is grain. The router's grain discriminator (≈ `<0.004` clean · `0.012–0.05` moderate · `>0.05` heavy). |
| `lavfi.pelorus.grain_flat` | fraction of the frame the estimate was measured over (flat pixels / total). The estimate's confidence — heavy-edge frames give a small flat fraction and an unreliable sigma. |

Read them with `metadata=print` or `ffprobe -show_frames`:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan,hwdownload,format=yuv420p,metadata=print:key=lavfi.pelorus.grain_sigma" \
  -f null -
```
