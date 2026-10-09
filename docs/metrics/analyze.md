<!-- markdownlint-disable MD013 -->
# `pelorus_analyze_vulkan` — frame statistics analyzer

A pass-through Vulkan compute filter that measures each frame's banding /
variance / edge statistics on the GPU and attaches them as the Pelorus interop
sections a downstream vmafx `vf_libvmaf*` reads for perceptually-weighted
scoring. It changes no pixels. Decision: [ADR-0109](../adr/0109-analyze-filter.md);
ABI: [interop-abi.md](../api/interop-abi.md).

## What it does

A compute shader reduces the luma plane cell by cell (one workgroup per
`cell` x `cell` cell, a shared-memory reduction into a per-cell SSBO read back
by the host) to per-frame:

- **variance** — mean per-cell spatial variance (texture/activity proxy),
- **edge density** — mean per-cell gradient magnitude,
- **flat-area fraction** — share of low-variance (banding-prone) cells,
- **banding risk** — coarse proxy = flat-area fraction (v0.1).

Loads are converted from the Vulkan storage domain to the logical `[0,1]`
sample domain before statistics are accumulated. This is what makes the values
comparable across 8-bit, planar 10/12-bit, and shifted P010/P012 inputs; UNORM
storage normalization alone is not sufficient for every layout.

These populate `PEL_SEC_VARIANCE` (`global_variance`, `edge_density`,
`texture_energy`) and `PEL_SEC_BANDING` (`flat_area_fraction`,
`global_banding_risk`, `contour_strength_mean`), attached to the frame as the
UUID-keyed Pelorus side-data blob (producer `PLRA`).

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

## Per-cell maps

With `maps=1` (the default) the same blob carries three maps on the cell grid
the header names (`grid_cols` x `grid_rows`,
[ADR-0177](../adr/0177-analyze-per-cell-maps.md)):

| Map | Section fields | Element | Value |
| --- | --- | --- | --- |
| banding risk | `PelorusBandingSection.cell_data_*` | `uint8` | round(255 x the per-cell banding score that `roi=1` steers by) |
| variance | `PelorusVarianceSection.var_cell_*` | `float` | luma variance of the cell, [0, 1] sample domain; the grid mean is `global_variance` |
| edge density | `PelorusVarianceSection.edge_cell_*` | `uint8` | round(255 x edge density of the cell) |

The grid is `ceil(W / cell)` x `ceil(H / cell)`, row-major; partial last cells
cover only the pixels inside the frame, and a frame smaller than one cell is a
1x1 grid. The layout, the reader checks and a reading example are in
[interop-abi.md](../api/interop-abi.md#analysis-maps-vf_pelorus_analyze).
VMAFx's perceptual pooling (VMAFx ADR-1118) averages the banding and variance
maps; without them it falls back to the frame scalars.

The grid is limited to 2^20 cells. A larger one (above `8192x8192` at
`cell=8`) fails when the filter graph is configured:

```text
[Parsed_pelorus_analyze_vulkan_2 @ ...] 8200x8192 at cell=8 exceeds the grid limit of 1048576 cells and 65535 per side; use a larger cell
```

Measured on the Vulkan format matrix
(`ffmpeg-patches/test/vulkan-format-matrix.sh`, identical on an RTX 4090, an
Arc A380 and RADV): a 256x128 8-bit ramp with one code step every 4 pixels
gives a banding-map mean of 0.952, flat grey with `noise=alls=40` gives 0.000,
and the ramp as `p010le` matches `yuv420p`. Details:
[research 0177](../research/0177-analyze-per-cell-maps.md).

## Options

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `flat` | float 0–0.25 | 0.0015 | per-tile variance below which a tile is counted as banding-prone |
| `roi` | bool | 0 | auto-detect banding-prone tiles and attach `AV_FRAME_DATA_REGIONS_OF_INTEREST` so a downstream encoder spends bits there |
| `roi_strength` | float 0–1 | 0.333 | max \|qoffset\| (fraction of the QP range) applied to a fully banding tile |
| `grad_lo` | float 0–0.5 | 0.002 | min per-tile gradient counted as a real (banding) slope |
| `grad_hi` | float 0–0.5 | 0.01 | per-tile gradient at which banding risk peaks before the tile turns textured |
| `cell` | int 8–64 | 32 | cell edge in luma pixels, a power of two (8, 16, 32 or 64): the analysis tile, the map grid and the ROI rectangle unit |
| `maps` | bool | 1 | attach the per-cell banding, variance and edge maps; 0 keeps the scalar-only blob |

## Example

```bash
# analyze upstream of deband so the deband side-data carries measured stats,
# then score against the source with vmafx in one graph
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
       -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_analyze_vulkan,pelorus_deband_vulkan=range=15" \
       -c:v hevc_vulkan -pix_fmt vulkan -qp 28 out.mkv  # or av1_vulkan
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
| --- | --- |
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
- **Coarse scalars**: `contour_strength_mean` is the mean cell variance and
  `global_banding_risk` is the flat-area fraction, coarse proxies until a
  dedicated contour estimator lands. The banding map carries the per-cell
  score instead.
- **Thresholds are per cell**: `flat`, `grad_lo` and the coarse step window
  (1 to 12 code values between neighbouring cells) are tuned at `cell=32`. A
  smaller cell sees less of a shallow ramp: the matrix ramp scores 0.952 at
  `cell=32`, 0.788 at 64 and 0.085 at 8.
- **Light grain reads as banding-prone**: a flat field with low-amplitude
  noise sits in the same variance window as a ramp (`noise=alls=12` scores a
  banding-map mean of 0.88, `alls=30` 0.19, `alls=40` 0).
- **Blob size**: the maps add 6 bytes per cell, 12 KiB per frame at 1080p and
  48 KiB at 2160p with `cell=32`. An encoder with `udu_sei=1` writes them into
  the bitstream; use `maps=0` there unless a decoder-side reader needs them.
  `hevc_nvenc` keeps 768 bytes per picture for SEI and writes a blob that does
  not fit without its maps, the same bytes as `maps=0`, with a warning
  ([ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md),
  [`udu_sei`](../usage/ffmpeg.md#carrying-the-side-data-blob-into-the-bitstream-udu_sei)).
- **VMAFx reads the first blob**: its reader takes the first
  `SEI_UNREGISTERED` entry only, so put `pelorus_analyze_vulkan` before other
  Pelorus producers (as in the example above) in a graph VMAFx scores.
