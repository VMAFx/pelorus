<!-- markdownlint-disable MD013 -->
# pelorus_fgs — H.274 film-grain SEI bitstream filter (HEVC)

`pelorus_fgs` is a libavcodec **bitstream filter** (BSF) that inserts an ITU-T
H.274 **Film Grain Characteristics (FGC) SEI** message into an HEVC elementary
stream, so a conforming decoder re-synthesizes film grain that was removed
before encode. It is the HEVC/H.274 leg of the film-grain round-trip that
`vf_pelorus_grain_estimate_vulkan` ([ADR-0115](../adr/0115-grain-estimate.md))
started; the design is [ADR-0117](../adr/0117-grain-fgs-bsf.md).

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

## Honest scope — a static model via AVOptions

A BSF operates on `AVPacket`s, and no stock encoder forwards the estimator's
**per-frame** grain frame side data onto the coded packet, so per-frame
estimate→SEI plumbing through an arbitrary HEVC encoder is not expressible in
n8.1.1. `pelorus_fgs` therefore inserts a **static** FGC model the user supplies
via AVOptions — the canonical FFmpeg metadata-BSF contract (`hevc_metadata`,
`h264_metadata`, `av1_metadata` all set static parameters this way). It is
opt-in and a no-op by construction: it passes the stream through unchanged unless
at least one colour component is selected.

The estimator's parameters map directly onto the options (see the recipe below),
so the intended workflow is: run the estimator once, read its H.274 scalars and
per-band scaling, and pass the corresponding option values to `pelorus_fgs`.
Per-frame, time-varying grain models are a follow-up that would need a side-data
channel that does not exist in stock FFmpeg.

## Options

| Option | Default | Range | Meaning |
|---|---|---|---|
| `model_id` | 1 | 0–1 | H.274 `film_grain_model_id`: 0 = frequency filtering, 1 = auto-regression |
| `blending_mode` | 0 | 0–1 | H.274 `blending_mode_id`: 0 = additive, 1 = multiplicative |
| `log2_scale` | 8 | 0–15 | H.274 `log2_scale_factor` |
| `components` | `y` | bitmask | colour components that carry a model: `y` (1), `cb` (2), `cr` (4). `0` ⇒ pass-through |
| `intensity_low` | 0 | 0–255 | lower bound of the luma intensity interval the model covers |
| `intensity_high` | 255 | 0–255 | upper bound of the luma intensity interval the model covers |
| `scale_y` | 16 | 0–255 | luma grain scale (`comp_model_value[Y][0][0]`) |
| `scale_c` | 8 | 0–255 | chroma grain scale (`comp_model_value[Cb/Cr][0][0]`) when `cb`/`cr` selected |
| `persistence` | on | bool | `film_grain_characteristics_persistence_flag` (apply until cancelled) |
| `skip_existing` | on | bool | do not insert if the access unit already carries an FGC SEI (avoid double-stamping) |

v0.x emits a **single** intensity interval `[intensity_low, intensity_high]` with
one model value per selected component. Mapping the estimator's eight intensity
bands onto multiple FGC intensity intervals is a follow-up (ADR-0117).

## Mapping the estimator's output to the options

`vf_pelorus_grain_estimate_vulkan` (model=h274) emits, in the
`PEL_SEC_FILMGRAIN` interop section:

- `h274_model_id` → `model_id`
- `h274_blending_mode` → `blending_mode`
- `h274_log2_scale` → `log2_scale`

and a per-luma-band RMS residual (the grain standard deviation at each of eight
intensity bands). For the single-interval v0.x model, collapse the per-band RMS
to one luma scale:

- `scale_y` ≈ `clip(round(mean(band_rms) * strength * 255), 0, 255)`, using the
  same `strength` you passed to the estimator. Use a band-weighted mean if grain
  is concentrated in a luma range, and set `intensity_low`/`intensity_high` to
  that range to restrict where the synthesized grain applies.
- `scale_c` is the chroma counterpart; leave at the default unless you select
  `cb`/`cr` (the estimator derives chroma from luma by default).

These are guidance values, not a measured BD-rate-optimal mapping; tune against
the vmafx encoded-VMAF oracle ([ADR-0106](../adr/0106-autotune-control-plane.md)).

## Usage

```bash
# 1. Estimate grain on the source, denoise it out, encode HEVC with libx265.
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_grain_estimate_vulkan=model=h274:strength=2.0,pelorus_denoise_vulkan=strength=0.4,hwdownload,format=yuv420p" \
  -c:v libx265 -crf 28 grainless.hevc

# 2. Insert the H.274 FGC SEI so a decoder re-synthesizes the grain.
#    (Map the estimate's H.274 scalars + per-band RMS onto the options.)
ffmpeg -i grainless.hevc -c:v copy \
  -bsf:v "pelorus_fgs=model_id=1:blending_mode=0:log2_scale=8:scale_y=24:intensity_low=0:intensity_high=255" \
  -f hevc out.hevc

# Inspect the inserted SEI:
ffmpeg -i out.hevc -c:v copy -bsf:v trace_headers -f null - 2>&1 | grep -A12 "Film Grain Characteristics"
```

The BSF also runs inline during a transcode (`-bsf:v pelorus_fgs=...` on the
HEVC output), and over a remuxed `.mp4`/`.mkv` HEVC track.

## Verification (this PR)

End to end on a `testsrc2` clip encoded with libx265: `pelorus_fgs` inserted the
FGC SEI, and `trace_headers` parsed it back on every access unit with the exact
configured values (`model_id`, `blending_mode`, `log2_scale`, intensity interval,
`comp_model_value`, persistence). `components=0` produced a byte-identical
pass-through; `skip_existing=1` did not double-stamp an already-marked stream.
No BD-rate / visual-match proof is shipped here — that must be measured under the
[ADR-0111](../adr/0111-benchmark-methodology.md) methodology as a follow-up.

## Follow-ups

- Per-band → multi-interval FGC mapping (the estimator measures eight bands; v0.x
  collapses them to one interval).
- A side-data channel that carries the per-frame estimate onto the coded packet,
  enabling a time-varying model instead of a static one.
- The H.264 leg (the FGC SEI is also defined for AVC) and the VVC/H.266 leg.
