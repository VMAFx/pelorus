<!-- markdownlint-disable MD013 -->
# pelorus_fgs — H.274 film-grain SEI bitstream filter (HEVC)

**Stance:** `experimental` — Available to insert static or per-frame H.274 film-grain metadata today; promotion requires pinned-corpus grain-synthesis BD-rate evidence ([bench #197](https://github.com/VMAFx/pelorus/issues/197)).

`pelorus_fgs` is a libavcodec **bitstream filter** (BSF) that inserts an ITU-T
H.274 **Film Grain Characteristics (FGC) SEI** message into an HEVC elementary
stream, so a conforming decoder re-synthesizes film grain that was removed
before encode. It is the HEVC/H.274 leg of the film-grain round-trip that
`vf_pelorus_grain_estimate_vulkan` ([ADR-0115](../adr/0115-grain-estimate.md))
started; the design is [ADR-0117](../adr/0117-grain-fgs-bsf.md).

**Modes.** By default (`source=options`), the BSF writes static values supplied
through its own AVOptions ([ADR-0117](../adr/0117-grain-fgs-bsf.md)). With
`source=sidedata`, it reads `PEL_SEC_FILMGRAIN` from Pelorus user-data-unregistered
SEI messages carried through the encoder and converts the AV1 parameters to H.274
per frame ([ADR-0206](../adr/0206-fgs-bsf-per-frame-bridge.md)).

## Why a BSF (and why HEVC specifically)

Grain is temporally incoherent, so a block encoder cannot inter-predict it and
re-codes it as residual every frame — a large, structureless bit tax. The win is
to **remove** the grain before encode (`pelorus_denoise_vulkan`) and
**re-synthesize** it at the decoder from a compact parameter set. The estimator
measures those parameters. Getting them into the bitstream then differs by codec:

- **AV1 already round-trips.** The estimator attaches a native
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS` (`AV_FILM_GRAIN_PARAMS_AV1`), and AV1 encoders
  consume it directly — no extra tooling. That leg is complete (ADR-0115).
- **HEVC had no automatic path.** Stock FFmpeg HEVC encoders do not carry
  film-grain frame side data into the bitstream. `pelorus_fgs` fills that gap by
  inserting the H.274 FGC SEI (HEVC SEI payload type 19) as a prefix NAL on each
  access unit. The same SEI covers VVC/H.266; this BSF targets HEVC.

## Honest scope — static AVOptions vs per-frame sidedata

`pelorus_fgs` supports two operational modes:

1. **Static mode (`source=options`, default):** Inserts a static FGC model supplied
   via AVOptions — the canonical FFmpeg metadata-BSF contract (`hevc_metadata`,
   `h264_metadata`, `av1_metadata` all set static parameters this way). It is
   opt-in and a no-op by construction: it passes the stream through unchanged unless
   at least one colour component is selected.
   The estimator's per-band grain measurement maps onto the options (see the recipe
   below), so the intended workflow is: run the estimator once, read its per-band
   scaling with suitable analysis tooling, manually derive the static values, and
   pass them to `pelorus_fgs`.
2. **Per-frame mode (`source=sidedata`):** Reads the Pelorus side-data blob from
   each access unit's user-data-unregistered SEI (written by encoders with `-udu_sei 1`),
   converts `PEL_SEC_FILMGRAIN` AV1 parameters to H.274 via `pel_grain_aom_to_h274`,
   and inserts an active or cancellation FGC SEI per frame ([ADR-0206](../adr/0206-fgs-bsf-per-frame-bridge.md)).

## Options

| Option | Default | Range | Meaning |
| --- | --- | --- | --- |
| `source` | `options` | `options`, `sidedata` | grain parameter source: `options` (0, static values from AVOptions), `sidedata` (1, per-frame Pelorus UDU SEI) |
| `model_id` | 0 | 0–1 | H.274 `film_grain_model_id`: 0 = frequency filtering, 1 = auto-regression (see [Decoder compatibility](#decoder-compatibility)) |
| `blending_mode` | 0 | 0–1 | H.274 `blending_mode_id`: 0 = additive, 1 = multiplicative |
| `log2_scale` | 2 | 0–15 | H.274 `log2_scale_factor`; each step up halves the grain amplitude. SMPTE RDD 5 allows 2–7 |
| `components` | `y` | bitmask | colour components that carry a model: `y` (1), `cb` (2), `cr` (4). `0` ⇒ pass-through |
| `intensity_low` | 0 | 0–255 | lower bound of the luma intensity interval the model covers |
| `intensity_high` | 255 | 0–255 | upper bound of the luma intensity interval the model covers |
| `intensity_low_c` | 0 | 0–255 | lower bound of the Cb/Cr intensity interval (matched against the chroma block average) |
| `intensity_high_c` | 255 | 0–255 | upper bound of the Cb/Cr intensity interval |
| `scale_y` | 16 | 0–255 | luma grain sigma (`comp_model_value[Y][0][0]`); `model_id` and the bit depth can lower the limit, see [Validation and failures](#validation-and-failures) |
| `scale_c` | 8 | 0–255 | chroma grain sigma (`comp_model_value[Cb/Cr][0][0]`) when `cb`/`cr` selected |
| `cutoff_h` | 8 | 2–14 | model 0 only: horizontal high cutoff frequency (`comp_model_value[c][0][1]`); larger values give finer grain |
| `cutoff_v` | 8 | 2–14 | model 0 only: vertical high cutoff frequency (`comp_model_value[c][0][2]`) |
| `persistence` | on | bool | `film_grain_characteristics_persistence_flag` (apply until cancelled) |
| `skip_existing` | on | bool | do not insert if the access unit already carries an FGC SEI (avoid double-stamping) |

Each selected component gets **one** intensity interval: luma uses
`[intensity_low, intensity_high]`, and Cb and Cr share
`[intensity_low_c, intensity_high_c]`. Mapping the estimator's eight intensity
bands onto multiple FGC intensity intervals is a follow-up (ADR-0117).

With `model_id=0` the SEI carries three model values per component: the sigma
and both cutoffs (`num_model_values_minus1 = 2`). H.274 would infer an absent
cutoff as 8, but FFmpeg's synthesizer does not apply that inference. It reads an
absent cutoff as 0 and clamps it to its coarsest pattern, so the BSF always
writes the cutoffs. With `model_id=1` the SEI carries the sigma only; H.274
infers the absent auto-regression correlations as zero, which gives
uncorrelated grain.

### Decoder compatibility

FFmpeg's H.274 synthesizer (`libavcodec/h274.c`) implements the SMPTE RDD 5-2006
profile: model 0, additive blending, `log2_scale` 2–7, cutoffs 2–14, and at
most three model values. The defaults stay inside that profile
([ADR-0155](../adr/0155-fgs-bsf-rdd5-profile.md)). Values that are valid H.274
but outside it still produce a valid SEI, and the BSF logs a warning at init:

- `model_id=1`: FFmpeg's HEVC decoder logs `Unsupported film grain parameters.
  Ignoring film grain.` once and outputs the frames without grain
  (`ff_h274_film_grain_params_supported()` in `libavcodec/h274.h`).
- `blending_mode=1`: FFmpeg blends additively regardless.
- `log2_scale` outside 2–7: at 8 or more, even `scale_y=255` yields less than
  one code value of grain.

### Validation and failures

The BSF rejects a configuration it cannot write before the first packet:

- An empty intensity interval (`intensity_low > intensity_high`, or the same for
  the `_c` pair) fails init with `AVERROR(EINVAL)`.
- `scale_y` and `scale_c` must fit the H.274 `comp_model_value` range at the
  stream's bit depth: up to 2^bitdepth − 1 for model 0 and up to
  2^(bitdepth − 1) − 1 for model 1. With 8-bit video and `model_id=1`, the limit
  is 127. The BSF reads the bit depth from the codec parameters (the encoder's or
  demuxer's pixel format) or from SPS extradata. If either is available, an
  out-of-range value fails init and `ffmpeg` exits non-zero with `Error
  initializing bitstream filter: pelorus_fgs`.
- If neither source gives the bit depth, the check runs on the first in-band SPS.
  Each access unit then fails with `AVERROR(EINVAL)`, and the BSF logs once that
  the output is incomplete. The `ffmpeg` CLI skips failed packets and still exits
  0 unless `-xerror` is given.

## Mapping the estimator's output to the options

With `model=h274`, `vf_pelorus_grain_estimate_vulkan` emits the H.274 model-0
values as frame metadata, named after the options they feed
([ADR-0161](../adr/0161-grain-estimate-rounding-and-h274-mapping.md)):

| Estimator metadata | Option |
| --- | --- |
| `lavfi.pelorus.h274_model_id` (0) | `model_id` |
| `lavfi.pelorus.h274_log2_scale` (2) | `log2_scale` |
| `lavfi.pelorus.h274_scale_y` | `scale_y` |
| `lavfi.pelorus.h274_cutoff_h`, `lavfi.pelorus.h274_cutoff_v` | `cutoff_h`, `cutoff_v` ([ADR-0155](../adr/0155-fgs-bsf-rdd5-profile.md)) |

The `PEL_SEC_FILMGRAIN` section carries the same mode scalars
(`h274_model_id` 0, `h274_blending_mode` 0, `h274_log2_scale` 2). The scale and
cutoff come from a table calibrated against FFmpeg's own model-0 synthesizer, so
FFmpeg's decoder reproduces the measured grain; the estimator's `strength`
option does not apply to them. The values cover one full-range luma interval.
Read them over a representative stretch of frames (for example the median),
pass them as static options, and leave `scale_c` at its default unless you
select `cb`/`cr` (the estimator derives chroma from luma). The measured round
trip and its limits are in
[grain_estimate.md](../metrics/grain_estimate.md#h274-model-0-mapping).

This is an offline/manual mapping, not code the BSF performs. The values
reproduce the measured grain, not a BD-rate-optimal model; tune against the
vmafx encoded-VMAF oracle ([ADR-0106](../adr/0106-autotune-control-plane.md)).

## Per-frame mode (source=sidedata)

With `source=sidedata`, `pelorus_fgs` operates on per-frame grain parameters rather than static options. During encode, the encoder passes Pelorus side-data sections in user-data-unregistered (UDU) SEI messages (via `-udu_sei 1`). The BSF parses each access unit's prefix UDU SEI messages, unwraps the Pelorus blob, extracts `PEL_SEC_FILMGRAIN`, and converts the AV1-model grain parameters to ITU-T H.274 via `pel_grain_aom_to_h274` ([ADR-0206](../adr/0206-fgs-bsf-per-frame-bridge.md)).

### Pipeline example

```bash
# Estimator only; add the denoiser after it. Frames stay in VRAM (Vulkan
# derived from CUDA, ADR-0191).
ffmpeg -init_hw_device cuda=cu:0 -init_hw_device vulkan=vk@cu -filter_hw_device vk -i in.mkv \
  -vf "format=nv12,hwupload,pelorus_grain_estimate_vulkan,hwupload=derive_device=cuda" \
  -c:v hevc_nvenc -bf 0 -udu_sei 1 -bsf:v "pelorus_fgs=source=sidedata" out.hevc
```

Measured on an RTX 4090 on 2026-10-17 with 8 noisy 320x192 pictures: exit code 0, the `-v verbose` log shows `Using input frames context (format cuda) with hevc_nvenc encoder`, `trace_headers` counts 8 active FGC SEI, and the decoder exports film grain parameters for all 8 pictures. `test/fgs-sidedata-smoke.sh` runs the same estimator and filter through system memory so that one graph also serves libx265.

hevc_nvenc writes the Pelorus carrier SEI ([ADR-0183](../adr/0183-sidedata-zero-free-carrier.md)) on every picture. libx265 4.3 (`X265_BUILD` 217) did not: with `-udu_sei 1` it wrote no foreign user data SEI on 2026-10-17, neither from the estimator nor from a decoded stream that carried one, so the filter found no section and wrote no FGC SEI. The smoke probes libx265 for this before it uses it and skips by name when the SEI is missing.

### Constraints and behavior

- **8-bit limit:** The converter domain is strictly 8-bit YUV420P. If the SPS luma or chroma bit depth is not 8, the BSF logs an error (`pelorus_fgs: sidedata mode supports 8-bit only (SPS bit depth != 8)`) and returns `AVERROR_PATCHWELCOME`.
- **Cancel behavior:** If `PEL_SEC_FILMGRAIN` is absent, has `apply == 0`, or carries `grain_model == PEL_GRAIN_NONE` (both `aom` and `h274` tags are converted, since the estimator fills the AV1 fields under either tag), no FGC SEI is emitted for that access unit. If a preceding access unit in the stream emitted an active FGC SEI with `persistence=1`, the BSF emits a cancellation SEI (`film_grain_characteristics_cancel_flag = 1`) once to terminate grain synthesis.
- **Option precedence:** In `source=sidedata` mode, component selections and model coefficients (`components`, `scale_*`, `intensity_*`, `cutoff_*`, `model_id`) are derived dynamically per frame from the side-data blob; the static AVOption values are ignored. The `components=0` passthrough rule does not apply in sidedata mode. The `persistence` and `skip_existing` options remain active.
- **Pelorus UDU SEI preserved:** The Pelorus UDU SEI messages remain in the bitstream so downstream quality oracles (such as VMAFx) can continue reading the side-data stream.
- **Fidelity loss:** The AV1-to-H.274 parameter conversion is an approximation because AV1 piecewise-linear scaling and AR-filter representations do not map losslessly to H.274 frequency-filtering parameters. See [grain-convert.md](../api/grain-convert.md) for details on conversion mathematics and fidelity bounds.

## Usage

```bash
# 1. Estimate grain on the source, denoise it out, encode HEVC with libx265.
# hwdownload is inherent: libx265 takes system-memory frames.
# Not run on hardware: the test binary has no libx265.
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan=model=h274:strength=2.0,pelorus_denoise_vulkan=strength=0.4,hwdownload,format=yuv420p" \
  -c:v libx265 -crf 28 grainless.hevc

# 2. Insert the H.274 FGC SEI so a decoder re-synthesizes the grain.
#    (Copy the estimator's lavfi.pelorus.h274_* metadata onto the options.)
ffmpeg -i grainless.hevc -c:v copy \
  -bsf:v "pelorus_fgs=model_id=0:blending_mode=0:log2_scale=2:scale_y=10:intensity_low=0:intensity_high=255" \
  -f hevc out.hevc

# FFmpeg applies H.274 grain when decoding; export it instead to decode clean:
ffmpeg -i out.hevc -f rawvideo with-grain.yuv
ffmpeg -export_side_data film_grain -i out.hevc -f rawvideo without-grain.yuv

# Inspect the inserted SEI:
ffmpeg -i out.hevc -c:v copy -bsf:v trace_headers -f null - 2>&1 | grep -A12 "Film Grain Characteristics"
```

The BSF also runs inline during a transcode (`-bsf:v pelorus_fgs=...` on the
HEVC output), and over a remuxed `.mp4`/`.mkv` HEVC track. "Inline" describes
packet placement only: its model still comes exclusively from those static
AVOptions, not from the filtergraph's frame side data.

## Verification

`ffmpeg-patches/test/fgs-sidedata-smoke.sh` runs in every stack replay ([build.md](../development/build.md)): one active FGC SEI per access unit with the Pelorus SEI kept (for an AOM-tagged and an H.274-tagged section), one cancel after the grain stops, no FGC SEI without the estimator, the 10-bit refusal, and the byte-identical `source=options` pass-through. Planted mutations (8-bit refusal removed, cancel removed, carrier UUID refused, H.274 tag refused, FGC insert skipped) each make it fail.

The fast suite's `fgs-bsf-contract` test (`scripts/test-fgs-bsf-contract.py
--self-test`) checks the source statically: RDD 5 defaults, a default grain
sigma of at least one code value, explicit cutoffs, per-component intervals, and
the init-time and per-access-unit range checks. Its self-test plants each of
those defects and requires the check to reject it.

Runtime evidence for [ADR-0155](../adr/0155-fgs-bsf-rdd5-profile.md), from a
CPU-only FFmpeg n9.0.2 build with the patch stack and libx265 (1-second
320x240 clips; grain sigma is the luma standard deviation between a normal
decode and a `-export_side_data film_grain` decode):

| Case | Before | After |
| --- | --- | --- |
| Defaults on flat grey, decoded by FFmpeg | `Ignoring film grain`; sigma 0.000 | model 0 with three model values; sigma 1.91 |
| `model_id=0` with the old `log2_scale=8` | sigma 0.50: every sample moves by 0 or −1, no visible grain | same output, plus an init warning that 8 is outside RDD 5 |
| `model_id=1:scale_y=200`, 8-bit | exit 0, 0-byte output, 25 packets dropped | init error, exit 234 |
| `intensity_low=200:intensity_high=10` | exit 0; the SEI matches no sample | init error, exit 234 |
| `model_id=1:scale_y=200`, 10-bit | — | written (limit 511) |
| Bit depth only in the SPS (BSF API, no pixel format) | — | 25 of 25 packets fail with `EINVAL`; one detailed error |

`trace_headers` parses the inserted SEI back on all 25 access units with the
configured values. `ffprobe -export_side_data film_grain -show_frames` reports
H.274 side data on all 25 frames. `components=0` is a byte-identical
pass-through, and `skip_existing=1` leaves an already-marked stream unchanged.
No BD-rate / visual-match proof is shipped; that must be measured under the
[ADR-0111](../adr/0111-benchmark-methodology.md) methodology as a follow-up.

## Follow-ups

- Per-band → multi-interval FGC mapping (the estimator measures eight bands; v0.x
  collapses them to one interval).
- A side-data channel that carries the per-frame estimate onto the coded packet:
  landed for HEVC UDU SEI (`source=sidedata`, [ADR-0206](../adr/0206-fgs-bsf-per-frame-bridge.md));
  inline packet side-data remains a follow-up.
- The H.264 leg (the FGC SEI is also defined for AVC) and the VVC/H.266 leg.
- Quality scoring: see [grain-scoring.md](../metrics/grain-scoring.md) for
  the scoring recipe and refusal rules for decoded grain synthesis vs
  reconstruction ([#82](https://github.com/VMAFx/pelorus/issues/82),
  [ADR-0207](../adr/0207-grain-scoring-decoded-output.md)).
