<!-- markdownlint-disable MD013 -->
# `pelorus_analyze_vulkan` — frame statistics analyzer

A pass-through Vulkan compute filter that measures each frame's banding /
variance / edge statistics on the GPU and attaches them as the Pelorus interop
sections a downstream vmafx `vf_libvmaf*` reads for perceptually-weighted
scoring. It changes no pixels. Decision: [ADR-0109](../adr/0109-analyze-filter.md);
ABI: [interop-abi.md](../api/interop-abi.md).

## What it does

A compute shader reduces the luma plane tile-by-tile (shared-memory reduction
into a 16-slice SSBO, summed on the host) to per-frame:

- **variance** — mean per-tile spatial variance (texture/activity proxy),
- **edge density** — mean per-tile gradient magnitude,
- **flat-area fraction** — share of low-variance (banding-prone) tiles,
- **banding risk** — coarse proxy = flat-area fraction (v0.1).

These populate `PEL_SEC_VARIANCE` (`global_variance`, `edge_density`,
`texture_energy`) and `PEL_SEC_BANDING` (`flat_area_fraction`,
`global_banding_risk`, `contour_strength_mean`), attached to the frame as the
UUID-keyed Pelorus side-data blob (producer `PLRA`). Per-cell maps are a future
append-only addition; v0.1 emits frame-level scalars.

It also emits `PEL_SEC_COMPLEXITY` (ADR-0132): a per-frame complexity scalar in
`[0,1]` (a normalized texture/edge energy, folding in `motion_component` when an
upstream `pelorus_mc` attached `PEL_SEC_MOTION`), EMA-smoothed across frames and
reset on a scene cut. It is the input to per-shot CRF steering; the autotune loop
learns the complexity→qoffset mapping. Validated to track content (flat ≈ 0 <
textured < high-motion).

With `roi=1` it additionally auto-detects banding-prone tiles and attaches
`AV_FRAME_DATA_REGIONS_OF_INTEREST` (a negative qoffset, scaled by
`roi_strength`) so a downstream encoder that honours ROI — `libx265`,
`pelorus`-patched `*_nvenc`/`*_qsv` — spends bits where contouring would show.
Detection is **two-scale** (ADR-0133, CAMBI alignment):

- a **fine** per-tile scale — a tile whose own variance sits in the
  banding-prone window (`flat`…`grad_hi`) and is not yet textured, and
- a **coarse** inter-tile scale — a flat tile carrying a small but non-zero
  inter-tile mean-luma gradient (≈1–12 code-values per tile), the signature of
  a shallow ramp (sky/shadow) that bands across many tiles yet is invisible to
  any single tile's variance.

A tile is flagged if either scale fires. The coarse scale closes the
single-scale blind spot: a 0x10→0x30 ramp (CAMBI 0.625) flagged 0 tiles before
and 27 after, and an A/B encode confirmed lower output CAMBI with no regression
on textured tiles (the coarse scale is gated to flats).

## Options

| Option | Type | Default | Meaning |
|---|---|---|---|
| `flat` | float 0–0.25 | 0.0015 | per-tile variance below which a tile is counted as banding-prone |
| `roi` | bool | 0 | auto-detect banding-prone tiles and attach `AV_FRAME_DATA_REGIONS_OF_INTEREST` so a downstream encoder spends bits there |
| `roi_strength` | float 0–1 | 0.333 | max \|qoffset\| (fraction of the QP range) applied to a fully banding tile |
| `grad_lo` | float 0–0.5 | 0.002 | min per-tile gradient counted as a real (banding) slope |
| `grad_hi` | float 0–0.5 | 0.01 | per-tile gradient at which banding risk peaks before the tile turns textured |

## Example

```bash
# analyze upstream of deband so the deband side-data carries measured stats,
# then score against the source with vmafx in one graph
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_analyze_vulkan,pelorus_deband_vulkan=range=15" \
       -c:v hevc_nvenc -cq 28 out.mkv      # codec-agnostic; or av1_nvenc / hevc_qsv
```

Output: the input video, unchanged, with a Pelorus side-data blob on every
frame. Inspect it from a downstream consumer via
`pel_blob_find_section(..., PEL_SEC_VARIANCE/PEL_SEC_BANDING, ...)`. The filter
logs nothing on success; errors surface as a non-zero ffmpeg exit with a
`[pelorus_analyze_vulkan]` message.

## Frame metadata

The interop blob is not muxed into the output file, so the same per-frame scalars
are *also* emitted as FFmpeg frame metadata ([ADR-0136](../adr/0136-analyze-frame-metadata.md),
the `vf_scdet` idiom) — the host-readable extraction path for per-shot CRF
steering, the autotune loop, and shell debugging:

| key | meaning |
|---|---|
| `lavfi.pelorus.complexity` | EMA-smoothed per-frame complexity `[0,1]` |
| `lavfi.pelorus.texture` | normalized texture/edge energy `[0,1]` |
| `lavfi.pelorus.motion` | motion component `[0,1]` (0 with no upstream `pelorus_mc`) |
| `lavfi.pelorus.variance` | mean per-tile luma variance |
| `lavfi.pelorus.edge` | mean per-tile edge density `[0,1]` |
| `lavfi.pelorus.banding` | flat-area fraction / banding risk `[0,1]` |
| `lavfi.pelorus.scene_cut` | `1` on a Pelorus scene cut, else `0` |

Read them with `ffprobe -show_frames -show_entries frame_tags` or, in a filter
graph, `metadata=mode=print`:

```bash
ffprobe -f lavfi -i "movie=in.mkv,format=yuv420p,hwupload,pelorus_analyze_vulkan,hwdownload" \
        -show_entries frame_tags=lavfi.pelorus.complexity,lavfi.pelorus.scene_cut -of csv
```

## Interactions & limitations

- **Requires a Vulkan hwframes context** (`AV_PIX_FMT_VULKAN`). It reads luma
  (plane 0) only — banding/variance are luma phenomena.
- **Codec-agnostic**: the statistics describe the *source*, so they're valid
  whatever encoder follows (HEVC, AV1, …).
- **Per-frame GPU→host sync**: one submit+wait per frame to read the
  accumulators back (fine for offline pre-encode; not tuned for low-latency
  live yet — see [ADR-0109](../adr/0109-analyze-filter.md)).
- **Place it before** the filters that should benefit (deband) so the measured
  blob is present; the blob round-trips the graph via `av_frame_copy_props`.
- **v0.1 scalars only**: `contour_strength_mean` is the mean tile variance and
  `global_banding_risk` is the flat-area fraction — coarse proxies until a
  dedicated contour estimator and per-cell maps land.
