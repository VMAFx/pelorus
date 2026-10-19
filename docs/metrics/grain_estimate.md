<!-- markdownlint-disable MD013 -->
# vf_pelorus_grain_estimate_vulkan

**Stance:** `experimental` — Available to estimate film-grain parameters today; on synthetic AV1 grain its error is within that of noise_model and grav1synth at about 50 times their speed ([bench #203](https://github.com/VMAFx/pelorus/issues/203)); promotion requires pinned-corpus BD-rate evidence on real film grain ([milestone 14](https://github.com/VMAFx/pelorus/milestone/14)).

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
   locally-flat fields, where the residual is grain. Every band is measured
   at a chain of three gates, 1, 2 and 4 × `edge`; see
   [Gate chain](#gate-chain).
3. **Bin by intensity** — each surviving pixel's `resid²` is accumulated into
   one of 8 luma-intensity bins with a per-bin pixel count → per-band RMS
   residual = grain standard deviation at that intensity.
4. **Lag-1 correlation** — a per-band accumulator (`Σ resid·resid_right` over
   flat pixel pairs, at every gate of the chain), pooled over the bands at the
   gate each band took and normalized by the residual variance, gives the
   residual lag-1 coefficient. It seeds the AV1 AR model and selects the H.274
   cutoff.

The reduction is sliced (32 slices) to cut atomic contention, then summed on
the host. Each per-pixel add is rounded to the nearest fixed-point unit, and
the lag-1 product is stored with a bias of `0.08²` at a scale of 150000, so the
sums are unbiased and every uint32 accumulator stays bounded through DCI 8K
([ADR-0161](../adr/0161-grain-estimate-rounding-and-h274-mapping.md)). The
`grain-accumulator-bounds` fast test proves the bound and checks the rounded
path against a float reference.

The host maps the lag-1 coefficient to a symmetric lag-1 AV1 AR model and the
per-band RMS to the AV1 AOM piecewise scaling function `y_points[value,
scaling]`, both through a table calibrated against FFmpeg's AV1 synthesizer;
see [AV1 mapping](#av1-mapping). With `model=h274` it also derives H.274
model-0 values; see [H.274 model-0 mapping](#h274-model-0-mapping).

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
| `strength` | 1.0 | 0–64 | multiplies the calibrated AV1 scaling function: 1 reproduces the measured grain, 2 doubles its standard deviation; does not affect the H.274 values |
| `model` | `aom` | `aom`/`h274` | FGS model the estimate targets (`aom` = AV1; `h274` = HEVC/VVC: also emits the `lavfi.pelorus.h274_*` model-0 values) |
| `native` | on | bool | also attach a native `AV_FRAME_DATA_FILM_GRAIN_PARAMS` (AV1) so a downstream FFmpeg AV1 encoder honours it with no Pelorus BSF |
| `adapt` | on | bool | let a band whose measurement matches white grain climb to a wider gate (2, then 4 × `edge`), so heavy grain is not read weak; off reproduces the single gate ([Gate chain](#gate-chain)) |

`strength` is the main knob: the default reproduces the source grain; raise it
if the synthesized grain looks too subtle, lower it if too heavy. Before
ADR-0227 the default was 2.0 on an uncalibrated mapping; a graph that set
`strength=2.0` now gets twice the measured grain. The vmafx `vmaf-tune` autotune
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

## Input formats

Accepts planar and semi-planar YUV at 4:2:0, 4:2:2 and 4:4:4 with 8, 10, 12 or 16 bits, plus `yuva420p`. Any other software layout stops the graph at configuration with `pelorus_grain_estimate_vulkan: input format <name> is not supported; supported: <list>`; convert first, for example with `scale_vulkan=format=nv12` ([Input formats and interlacing](../backends/vulkan.md#input-formats-and-interlacing), [ADR-0201](../adr/0201-vulkan-input-format-allow-list.md)).

Interlaced frames are accepted and keep their interlaced flag.

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
An AR fit beyond the symmetric lag-1 model, explicit chroma-grain estimation, a
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
  -vf "hwupload,pelorus_grain_estimate_vulkan,pelorus_denoise_vulkan=strength=0.4,hwdownload,format=yuv420p" \
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
NVENC CUDA hop fails after 2 to 37 frames, so the recipe uses the Vulkan Video
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
| `lavfi.pelorus.grain_flat` | fraction of the frame the estimate was measured over (flat pixels / total; a band that took a wider gate counts the pixels within that gate). The estimate's confidence — heavy-edge frames give a small flat fraction and an unreliable sigma. |
| `lavfi.pelorus.grain_wide_bands` | bitmask, bit b set when luma band b took its statistics at a gate wider than `edge` ([Gate chain](#gate-chain)); 0 on clean and textured content and with `adapt=0` |
| `lavfi.pelorus.grain_gate_steps` | two bits per luma band, bits 2b and 2b+1: the gate band b took, 0 = `edge`, 1 = 2 × `edge`, 2 = 4 × `edge`; 0 with `adapt=0` |
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
therefore the confidence, of the sigma estimate. A band that took a wider gate
([Gate chain](#gate-chain)) counts the pixels within that gate, so on heavy grain a high
`grain_flat` together with a non-zero `grain_wide_bands` is expected.

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

## AV1 mapping

The AV1 parameters are derived through a table calibrated against FFmpeg's AV1
synthesizer ([ADR-0227](../adr/0227-av1-grain-calibration.md)):

1. **AR model** — the measured residual lag-1 correlation (`grain_lag1`) selects
   a symmetric lag-1 model: `ar_coeff_lag` 1, `ar_coeff_shift` 6, and the up and
   left taps (`ar_coeffs_y[1]`, `ar_coeffs_y[3]`) both set to `a`, 0–28. The
   estimator measures horizontal correlation only, so the grain is taken as
   isotropic. `a` is interpolated between the calibrated rows.
2. **Scaling function** — one `y_points` entry per populated band: the band RMS
   residual in 8-bit code values divided by that model's calibrated residual
   gain, times `strength`, rounded and clipped to 0–255, with `scaling_shift` 8.

When a band took a wider gate ([Gate chain](#gate-chain)), the open-gate table
no longer describes what that band measures: the wider gate still drops the
largest excursions of heavy grain, which lowers the band RMS and shifts its
lag-1 correlation by an amount that depends on `a`. A second table,
`pel_aom_gate_calib`, holds each model measured through 2 and 4 × `edge`, keyed
by the band's gated RMS over its gate
([ADR-0230](../adr/0230-grain-gate-chain.md)). For each `a` the filter predicts
the pooled lag-1 correlation the measurement would show, reading every band from
the open table or from the gated table at the band's own gate and pooling them
as the measurement does; `a` is interpolated where the prediction meets
`grain_lag1`, and each band's gain comes from the same rows. With no band above
the default gate the open-gate path runs unchanged.

The tables in `ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c` are
generated by `scripts/gen-av1-grain-calibration.py`. The script ports FFmpeg's
AV1 luma synthesis (Gaussian template, lag-1 AR filter, 32×32 block placement
with overlap) bit for bit, reads `gaussian_sequence` from
`libavcodec/aom_film_grain.c` at the pinned commit, and measures the result with
the estimator's own arithmetic. Check it after an FFmpeg bump:

```bash
FFMPEG_REPO=/path/to/ffmpeg scripts/gen-av1-grain-calibration.py --check
```

### Accuracy against noise_model and grav1synth

Measured round trip ([research 0203](../research/0203-grain-estimate-accuracy.md)).
Each truth is an AV1 film grain table with known parameters, rendered by libaom
3.15.2 (`aomenc --film-grain-table`, then `aomdec` with and without
`--skip-film-grain`) onto 24 frames of 640×360 flat luma stripes at the eight
band centres. Each estimator gets the grainy clip. libaom's `noise_model` and
`grav1synth diff` also get the grain-free clip as their denoised reference.
Each output table is rendered back the same way. The error is the rendered grain
standard deviation per stripe against the truth. Pelorus ran on an RTX 4090
(FFmpeg's default Vulkan device on the test host).

| Truth (scaling, AR) | Truth σ | noise_model | grav1synth | Pelorus before | Pelorus | Pelorus `edge=0.12` |
| --- | --- | --- | --- | --- | --- | --- |
| white, 8 | 1.04 | +4.7% | +3.7% | −78.6% | 0.0% | 0.0% |
| white, 24 | 3.02 | +1.2% | +0.1% | −73.0% | 0.0% | 0.0% |
| ramp 8→40 | 1.28–4.75 | 3.3% (worst +11.7%) | 3.0% (worst +10.2%) | 76.7% (worst −82.7%) | 3.5% (worst −15.4%) | 0.1% (worst +0.2%) |
| 24, lag-1 a = 20 | 3.42 | −1.3% | −1.4% | −80.0% | −2.8% | −1.5% |

Cells are the mean error over the eight stripes; the ramp row gives the mean
absolute error and the worst stripe. Lag-1 correlation of the rendered grain
for the AR truth (0.344 horizontal, 0.345 vertical): noise_model 0.337/0.340,
grav1synth 0.339/0.344, Pelorus 0.301/0.303 (`a` = 18), with `edge=0.12`
0.322/0.323 (`a` = 19), before the fix 0.025/0.001. Pelorus emits the truth
parameters exactly for the white rows and the lower six ramp bands. On the
grain-free clip it attaches no AV1 grain parameters, and both peers emit a zero
scaling function.

The Pelorus column predates the gate chain and equals `adapt=0`. With the gate
chain the ramp row comes back within 0.23% on every stripe; the other rows are
unchanged ([Gate chain](#gate-chain)).

Throughput, 48 frames of 1920×1080 white grain, wall time of the whole process:
Pelorus 97 fps (RTX 4090, measured for research 0203; `hwupload,hwdownload` alone runs at 127 fps),
noise_model 1.8 fps, grav1synth 1.7 fps (CPU, given the denoised reference, so
the time of the denoiser they need is not included; grav1synth includes a
container start).

### Gate chain

The default gate (0.06) drops flat pixels whose residual exceeds it, and grain
above about 3.5 code values pushes its largest residuals past that bound, so a
single gate reads heavy grain weak. A wider gate fixes that on flat content but
lets texture into the estimate on real content. The filter therefore measures
every band at a chain of three gates, 1, 2 and 4 × `edge`, and lets a band climb
from one gate to the next only while it behaves like grain
([ADR-0229](../adr/0229-grain-dual-gate.md),
[ADR-0230](../adr/0230-grain-gate-chain.md)). A band climbs from gate g while:

- gate g binds: it keeps less than 0.95 of the band's pixels;
- the band holds at least 1024 pixels within gate g;
- the gated RMS over gate g reaches 0.875 of what white Gaussian grain gives at
  gate g and the same kept fraction.

White grain's kept fraction and gated RMS both depend only on σ over the gate,
so they lie on one curve per gate, `pel_gate_curve` in the filter, generated by
`scripts/gen-grain-gate-curve.py` (`--check` fails on drift; rerun it when the
high-pass, the residual clamp or the default `edge` changes). Texture fails the
gate by structure, not by the tail of a distribution, and sits at 0.79 of the
curve or below on the textured clips measured; heavy grain sits at about 0.96.
The lag-1 product is kept at every gate too, so the pooled lag-1 correlation
reads each band at the gate it took. With `model=aom` the AV1 mapping
corrects for what the chosen gate still drops (see the AV1 mapping above).

`adapt` applies to both `model` values: the pooled RMS and lag-1 that the
H.274 mapping reads use the same per-band choice, but the H.274 table is
calibrated with the gate open and has no gated counterpart, and no heavy H.274
truth has been measured through the chain. Treat H.274 values on grain above
about 3.5 code values as unverified.

The curves, the gated AV1 table and the 0.875 threshold are calibrated at the
default `edge` (0.06), against a residual clamp that does not scale with `edge`.
The rule runs at any `edge`, but its texture rejection and the gated AV1 values
were measured only at the default; with a non-default `edge`, check `grain_wide_bands` on representative
textured content first, or set `adapt=0`.

Check which gate each band took (heavy white grain truth, then a textured clip;
640×360, the first of two frames shown):

```bash
ffmpeg -init_hw_device vulkan=vk -filter_hw_device vk \
  -f rawvideo -pix_fmt yuv420p -s 640x360 -r 24 -i white48.yuv -frames:v 2 \
  -vf "hwupload,pelorus_grain_estimate_vulkan=adapt=1,hwdownload,format=yuv420p,metadata=print:file=-" \
  -f null - 2>&1 | grep -E 'grain_(wide_bands|gate_steps)'
```

| Input | `adapt=1` | `adapt=0` |
| --- | --- | --- |
| white grain, σ 6 | `grain_wide_bands=255`, `grain_gate_steps=21845` (0x5555: every band at 2 × `edge`) | `0`, `0` |
| Big Buck Bunny | `0`, `0` | `0`, `0` |

Measured round trip on an RTX 4090 (driver 615.78.08), same method as above
([research 0411](../research/0411-grain-dual-gate.md),
[research 0414](../research/0414-grain-gate-chain.md)):

| Truth (scaling, AR) | Truth σ | `adapt=0` | `adapt=1` (default) |
| --- | --- | --- | --- |
| ramp 8→40 | 1.28–4.75 | 3.52% (worst −15.44%) | 0.14% (worst +0.23%) |
| white, 48 | 6.00 | −26.79% | +0.05% (worst +0.05%) |
| 48, lag-1 a = 20 | 6.77 | −33.20% | −1.95% (worst −3.37%) |
| ramp 8→64 | 1.53–7.42 | 14.30% (worst −37.47%) | 0.27% (worst +1.99%) |

The AR 48 truth comes back with `a` = 19 (truth 20); its rendered lag-1
correlation is 0.325 against 0.346 for the truth. On the 8→64 ramp the bands
from about 3.5 code values take 2 × `edge` and the top two take 4 × `edge`
(`grain_gate_steps=42304`). White 8, white 24, AR 24 and the 8→40 ramp come back
as before. On 48 frames each of six textured clips (Big Buck Bunny, glass half,
blue sky, news mixed coding, Sintel trailer, SVT waterfall) no band left the
default gate and `grain_sigma` is identical with `adapt` on and off. Throughput
at 1080p is within run-to-run noise of the dual gate.

Open: a band that mixes grain with texture keeps the default gate; grain heavy
enough to bind at 4 × `edge` (above about 14 code values) clips there; and the
H.274 mapping has no gated table.

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

## Scoring

For quality scoring of decoded synthesised grain vs the encoder's reconstruction,
see [grain-scoring.md](grain-scoring.md) ([#82](https://github.com/VMAFx/pelorus/issues/82),
[ADR-0207](../adr/0207-grain-scoring-decoded-output.md)).
