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
4. **Lag-1 correlation** — a global accumulator (`Σ resid·resid_right` over
   flat pixel pairs), normalized by the residual variance, gives the residual
   lag-1 coefficient. It seeds the AV1 AR model and selects the H.274 cutoff.

The reduction is sliced (32 slices) to cut atomic contention, then summed on
the host. Each per-pixel add is rounded to the nearest fixed-point unit, and
the lag-1 product is stored with a bias of `0.08²` at a scale of 150000, so the
sums are unbiased and every uint32 accumulator stays bounded through DCI 8K
([ADR-0161](../adr/0161-grain-estimate-rounding-and-h274-mapping.md)). The
`grain-accumulator-bounds` fast test proves the bound and checks the rounded
path against a float reference.

The host maps the per-band RMS to the AV1 AOM piecewise scaling function
`y_points[value, scaling]` (band centre → `value`, RMS·`strength` → `scaling`),
takes AV1-legal defaults for the shifts, and seeds `ar_coeffs_y[0]` from the
lag-1 coefficient. With `model=h274` it also derives H.274 model-0 values; see
[H.274 model-0 mapping](#h274-model-0-mapping).

The shipped shader is
`ffmpeg-patches/files/vulkan/pelorus_grain_estimate.comp.glsl`; the similarly
named `libpelorus/shaders/*.comp` file is a compile-checked standalone
reference, not a second shipped implementation. Loads are converted to the
logical sample domain before the estimator runs.

## Options

All thresholds are normalized in `[0,1]`, independent of bit depth.

| Option | Default | Range | Meaning |
| --- | --- | --- | --- |
| `edge` | 0.06 | 0–1 | 3×3 neighbourhood range above which a pixel is an edge and is excluded from the grain estimate |
| `strength` | 2.0 | 0–64 | scales the measured per-band RMS residual to the AV1 `[0,255]` scaling-function value (synthesis intensity); does not affect the H.274 values |
| `model` | `aom` | `aom`/`h274` | FGS model the estimate targets (`aom` = AV1; `h274` = HEVC/VVC: also emits the `lavfi.pelorus.h274_*` model-0 values) |
| `native` | on | bool | also attach a native `AV_FRAME_DATA_FILM_GRAIN_PARAMS` (AV1) so a downstream FFmpeg AV1 encoder honours it with no Pelorus BSF |

`strength` is the main knob: raise it if the synthesized grain looks too subtle,
lower it if too heavy. The vmafx `vmaf-tune` autotune
([ADR-0106](../adr/0106-autotune-control-plane.md)) can sweep it against the
encoded-VMAF oracle.

## Output

Two side-data channels are attached to each frame:

- **`PEL_SEC_FILMGRAIN`** (Pelorus interop, `AV_FRAME_DATA_SEI_UNREGISTERED`,
  UUID-keyed) — the codec-neutral intent plus the AV1 params field-for-field,
  the H.274 mode scalars (`model_id` 0, `blending_mode` 0, `log2_scale` 2: the
  SMPTE RDD 5 profile and the `pelorus_fgs` defaults), and the `grain_model`
  tag. No ABI bump: the section was reserved in
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

```text
hwupload → pelorus_grain_estimate → pelorus_denoise → pelorus_deband → (hwdownload) → encoder (synthesizes grain)
```

## FGS target (honest scope)

AV1 (AOM) is the authoritative target for v0.x: FFmpeg has a complete public
`AVFilmGrainAOMParams` struct and a native side-data channel, so the estimate is
consumable today with no extra bitstream plumbing. `av1_nvenc` also consumes the
estimate via the `-pelorus_film_grain` AVOption that drives NVENC's hardware AV1
film-grain synthesis ([ADR-0118](../adr/0118-nvenc-av1-filmgrain.md)). HEVC/H.265
has a different, deliberately manual leg: `pelorus_fgs` writes a static H.274
FGC SEI model supplied through BSF AVOptions
([ADR-0117](../adr/0117-grain-fgs-bsf.md), see
[grain-fgs-bsf.md](../usage/grain-fgs-bsf.md)). It does **not** read the
estimator's frame side data or metadata inline; copy the
`lavfi.pelorus.h274_*` values (see
[H.274 model-0 mapping](#h274-model-0-mapping)) into the BSF options yourself.
The full per-lag AR coefficient fit, explicit chroma-grain estimation, a
multi-interval H.274 model, and automatic per-frame / H.264 / VVC legs remain
deferred (ADR-0115). No BD-rate / visual-match proof is
shipped with this filter; it must be measured under the
[ADR-0111](../adr/0111-benchmark-methodology.md) methodology in a follow-up.

## Usage

```bash
# AV1: estimate grain on the source, denoise it, let the AV1 encoder
# re-synthesize it from the attached AV_FRAME_DATA_FILM_GRAIN_PARAMS.
# hwdownload is inherent: libaom-av1 takes system-memory frames.
# Not run on hardware: the test binary has no libaom.
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan=strength=2.0,pelorus_denoise_vulkan=strength=0.4,hwdownload,format=yuv420p" \
  -c:v libaom-av1 -crf 30 out.mkv

# Inspect the estimate (model only; no encode):
ffprobe -f lavfi -i "...,pelorus_grain_estimate_vulkan" -show_frames | grep -i film_grain
```

For HEVC/H.265 + VVC/H.266, select `model=h274`. The filter then emits the
H.274 model-0 values as frame metadata, named after the `pelorus_fgs` options
they feed:

```bash
# 1. Read the H.274 values the estimator derives from the source grain.
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv -frames:v 48 \
  -vf "hwupload,pelorus_grain_estimate_vulkan=model=h274,hwdownload,format=yuv420p,metadata=print:key=lavfi.pelorus.h274_scale_y,metadata=print:key=lavfi.pelorus.h274_cutoff_h" \
  -f null -

# 2. Denoise and encode, then insert a static H.274 FGC SEI with those values
#    (here scale_y=10, cutoff 14). pelorus_fgs does not read the metadata
#    inline, even when used in the same command.
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
  -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan -extra_hw_frames 3 -i in.mkv \
  -vf "pelorus_denoise_vulkan=strength=0.4" \
  -c:v hevc_vulkan -qp 28 \
  -bsf:v "pelorus_fgs=model_id=0:log2_scale=2:scale_y=10:cutoff_h=14:cutoff_v=14" out.mkv
```

Verified on an RTX 4090 (exit code 0, 120 frames, no `hwdownload`). Denoise keeps
`prev=3` frames, hence `-extra_hw_frames 3`
([frames the filters keep](../usage/ffmpeg.md#frames-the-filters-keep--extra_hw_frames)); denoise on the
NVENC CUDA hop fails after about 32 frames, so the recipe uses the Vulkan Video
encoder.

The `cutoff_h`/`cutoff_v` options are part of the `pelorus_fgs` SMPTE RDD 5
profile ([ADR-0155](../adr/0155-fgs-bsf-rdd5-profile.md)). Without them the
SEI carries only the scale, FFmpeg then reads both cutoffs as its coarsest pattern, and the
decoded grain is 52% to 79% weaker than the source in the measurements below.

AV1 software encoders can use the native `AV_FRAME_DATA_FILM_GRAIN_PARAMS`
channel, and `av1_nvenc` uses `-pelorus_film_grain` for the per-frame estimate.
HEVC uses the separate `pelorus_fgs` H.274 FGC SEI BSF
([ADR-0117](../adr/0117-grain-fgs-bsf.md)); that leg is static and manually
configured, not an automatic frame-side-data round trip. The H.264 and VVC legs
and a per-frame H.274 model remain deferred.

## Frame metadata (the `tune=auto` grain discriminator)

Alongside the side-data, the filter emits per-frame scalars as `lavfi.pelorus.*`
frame metadata (the [ADR-0136](../adr/0136-analyze-frame-metadata.md) `av_dict_set`
pattern — no interop ABI change), so the content-adaptive router
([ADR-0142](../adr/0142-tune-auto-content-router.md)) can read the grain estimate
without parsing the `PEL_SEC_FILMGRAIN` blob, and so the H.274 values can be
copied into `pelorus_fgs`:

| key | meaning |
| --- | --- |
| `lavfi.pelorus.grain_sigma` | peak per-band RMS residual (grain stddev) over the populated intensity bands, normalized `[0,1]`. Measured **only** on edge-gated locally-flat pixels, so real structure is excluded by construction — what survives is grain. The router's grain discriminator (≈ `<0.004` clean · `0.012–0.05` moderate · `>0.05` heavy). |
| `lavfi.pelorus.grain_flat` | fraction of the frame the estimate was measured over (flat pixels / total). The estimate's confidence — heavy-edge frames give a small flat fraction and an unreliable sigma. |
| `lavfi.pelorus.grain_lag1` | lag-1 correlation of the high-pass residual, `[-1,1]`. It is not the grain's own correlation: white grain reads about −0.167, because the 3×3 box mean is subtracted from both neighbours. |
| `lavfi.pelorus.h274_model_id` | `model=h274` only: H.274 `film_grain_model_id`, always 0 (frequency filtering) → `pelorus_fgs` `model_id` |
| `lavfi.pelorus.h274_log2_scale` | `model=h274` only: `log2_scale_factor`, always 2 → `log2_scale` |
| `lavfi.pelorus.h274_scale_y` | `model=h274` only: luma `comp_model_value[0][0][0]` in 8-bit code values, 0–255 → `scale_y` |
| `lavfi.pelorus.h274_cutoff_h`, `lavfi.pelorus.h274_cutoff_v` | `model=h274` only: horizontal and vertical high cutoff, 6–14, always equal → `cutoff_h`, `cutoff_v` |

Read them with `metadata=print` or `ffprobe -show_frames`:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan,hwdownload,format=yuv420p,metadata=print:key=lavfi.pelorus.grain_sigma" \
  -f null -
```

## Reading `grain_sigma` — always pair it with `grain_flat`

The estimator accumulates only over **flat** neighbourhoods: a pixel whose 3x3 range
exceeds `edge_thr` is skipped entirely, so the sigma reflects grain rather than edge
energy. `grain_flat` is the fraction of pixels that qualified — i.e. the **coverage**, and
therefore the confidence, of the sigma estimate.

That makes the two values only meaningful together:

| `grain_sigma` | `grain_flat` | meaning |
| --- | --- | --- |
| ~0 | ~1 | genuinely clean — lots of flat area, no grain found in it |
| moderate | 0.1–0.9 | normal case; sigma is well-supported |
| ~0 or erratic | ~0 | **starved, not clean** — almost nothing qualified as flat |

A consumer must not read `grain_sigma` alone. Measured on real content (BBB 640x360) with
seeded noise injected, the response is correctly monotonic in both:

| injected noise | `grain_sigma` | `grain_flat` |
| --- | --- | --- |
| 0 | 0.0132 | 0.331 |
| 4 | 0.0140 | 0.294 |
| 8 | 0.0158 | 0.166 |
| 12 | 0.0172 | 0.055 |
| 20 | 0.0214 | 0.005 |

But on a *pathologically flat* source — a uniform colour or a smooth gradient, uniformly
noised — essentially no neighbourhood stays under `edge_thr`, `grain_flat` collapses to
~0, and the sigma becomes 0 or an unstable estimate drawn from a handful of pixels.

**This matters for the `tune=auto` router (ADR-0142)**, which uses `grain_sigma` as its
grain-detection input: keyed on sigma alone, a starved estimate reads as "clean" and would
route heavy-grain content *away* from the denoise leg — the exact case where the largest
measured BD-rate win lives. Gate on `grain_flat` before trusting a low sigma.

## H.274 model-0 mapping

FFmpeg's H.274 synthesizer (`libavcodec/h274.c`) implements the SMPTE RDD 5
profile: model 0 (frequency filtering), additive blending, `log2_scale_factor`
2–7, cutoffs 2–14, 8-bit 4:2:0 only. In model 0 the cutoff sets the grain size
and the scale sets its strength, so the estimator derives both
([ADR-0161](../adr/0161-grain-estimate-rounding-and-h274-mapping.md)):

1. **Cutoff** — the one from 6 to 14 whose calibrated residual lag-1
   correlation is nearest `grain_lag1`. Cutoffs 2–5 are not used: their
   residual correlation repeats values that 6–8 already take.
2. **Scale** — the residual RMS pooled over all flat pixels, in 8-bit code
   values, divided by that cutoff's calibrated residual gain per unit of scale,
   rounded and clipped to 0–255. The scale is in 8-bit code values at every bit
   depth, because RDD 5 limits it to 8 bits and the HM/VTM variants shift the
   grain left by `bitdepth − 8` (ITU-T H.Sup21 (01/2025), 7.3.1.1, 7.3.2.1).
   `strength` does not apply.

The calibration table in `ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c`
is generated by `scripts/gen-h274-grain-calibration.py`. The script ports
FFmpeg's model-0 luma synthesis bit for bit, reads the grain tables from
`libavcodec/h274.c` at the pinned commit, and measures the result with the
estimator's own arithmetic. Check it after an FFmpeg bump:

```bash
FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py --check
```

Measured round trip: estimator on an Arc A380, then `pelorus_fgs` with the
emitted values (including the cutoffs) on a lossless libx265 encode of the
grain-free field, then FFmpeg's HEVC decoder. Each source is three 1920×1080
frames of grain on flat luma 115. The decoded grain is the difference between a
normal decode and an `-export_side_data film_grain` decode.

| Source grain | Source σ (8-bit) | Emitted `scale_y` / cutoff | Decoded σ | Error |
| --- | --- | --- | --- | --- |
| white Gaussian | 1.04 | 5 / 14 | 1.08 | +3.6% |
| white Gaussian | 2.02 | 10 / 14 | 2.04 | +1.0% |
| white Gaussian | 3.01 | 15 / 14 | 3.09 | +2.5% |
| separable AR(1), ρ = 0.5 | 2.02 | 10 / 12 | 1.84 | −9.0% |
| RDD 5, scale 16, cutoff 8 | 1.91 | 16 / 8 | 1.91 | −0.1% |
| RDD 5, scale 24, cutoff 12 | 4.38 | 22 / 12 | 4.01 | −8.5% |

White grain and RDD 5 grain up to about three code values come back within
±5%. Two cases come back weaker. Grain with more low-frequency energy than a
band-limited pattern of the same residual correlation (the AR(1) row) loses up
to 10%. Heavy grain near the `edge` gate (the 4.4-code row) loses about as much,
because the gate drops its largest residuals. RDD 5 cannot represent fully
white grain, so white sources come back as cutoff-14 grain with a lag-1
correlation of about +0.16 instead of 0. At a small scale one step is a large
fraction: scale 5 moves the strength by 20% per step.
