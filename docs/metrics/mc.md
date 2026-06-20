<!-- markdownlint-disable MD013 -->
# vf_pelorus_mc_vulkan

A GPU block-matching **motion estimator**, run as a zero-copy pass in VRAM. It
produces a per-block quarter-pel (sub-pel-refined) motion-vector field for the current frame
relative to the previous frame and attaches it as the pre-reserved
`PEL_SEC_MOTION` interop section. **The frame passes through unchanged** — this
is a producer (the analyzer shape), not a transform.

The value is **encode speed, not quality**: the MV field is a *hint* a downstream
encoder can feed its external motion search (e.g. NVENC's
`NV_ENC_EXTERNAL_ME_HINT`) so a fixed-function ASIC can skip or shorten its own
search. It is **not** a flow field for warping, and it does **not** feed the
denoiser (see the honesty note below). See
[ADR-0116](../adr/0116-pelorus-mc.md) for the producer decision,
[ADR-0113](../adr/0113-optical-flow-mc.md) for the motion-estimation strategy,
and [ADR-0114](../adr/0114-encoder-steering.md) Tier 3 for the gated NVENC
ME-hint consumer (a documented follow-up).

## Algorithm

A GPU adaptation of FFmpeg's block-matching EPZS
(`libavfilter/motion_estimation.c`, `ff_me_search_epzs` / `ff_me_search_ds`):

- **One workgroup per block.** A `bsize × bsize` block is owned by one compute
  workgroup. Its invocations cooperatively compute the **SAD** (sum of absolute
  luma differences) between the current block and the reference block displaced
  by a candidate MV, tree-reduced in shared memory.
- **Predictor-seeded diamond descent.** The candidate set is seeded with three
  predictors, then a small-diamond (`{(-1,0),(0,-1),(1,0),(0,1)}`, step-halving)
  hill-descent refines from the best, bounded by the search range. The
  predictors are:
  - the **zero MV** (static / locked-off content),
  - the previous frame's **global-motion MV** (camera pan / move),
  - the **collocated previous-frame block MV** (temporal continuity — a
    persistent MV SSBO ping-ponged frame to frame).
- **Why no spatial predictors.** A serial CPU EPZS also seeds from the current
  frame's left/top neighbour MVs. On the GPU every block runs concurrently, so
  reading a neighbour block's result mid-dispatch is a cross-workgroup data race;
  those predictors are omitted on purpose. The temporal + global predictors
  recover most of the benefit, and this frame's field becomes next frame's
  temporal predictor.

The MV `(dx, dy)` is **quarter-pel** in luma units (Q2 fixed-point, stored
`= round(pel * 4)`): the displacement such that `cur[pos] ≈ ref[pos + mv/4]`.
The integer block-match minimum is sub-pel refined by a parabolic fit of the SAD
surface across the minimum and its four axis-neighbours (ADR-0130). The
`PelorusMotionSection` summary scalars (`global_motion_*`, `motion_magnitude_*`)
remain in whole luma pixels. The standalone reference shader is
`libpelorus/shaders/pelorus_mc.comp`; the filter's inline GLSL implements the
byte-identical algorithm (kept in lockstep, AGENTS hard rule 4).

## Options

| Option | Default | Range | Meaning |
|---|---|---|---|
| `bsize` | 16 | 8–32 | motion-estimation block edge in luma pixels |
| `search` | 24 | 1–256 | max search radius per axis in luma pixels |
| `meta` | on | bool | attach the `PEL_SEC_MOTION` interop section (the MV field + scalars) |

`bsize` and `search` are device-agnostic — the same pipeline serves every block
size (the workgroup is `32 × 32`; lanes past `bsize` contribute 0), so the filter
is a product for any Vulkan GPU, not tuned to one device.

## Pipeline placement

The estimator reads the source luma; place it after `hwupload` and before any
pixel-modifying stage so its MVs describe the frames the encoder will see:

```
hwupload → pelorus_analyze → pelorus_mc → pelorus_denoise → pelorus_deband → (hwdownload) → encoder
```

It keeps a 1-frame causal history (a clone — a refcount bump on the hwframe, no
pixel copy) as the reference. Frame 0 has no reference and emits a zero field.

## Interop (`meta=1`)

Emits the pre-reserved 32-byte `PEL_SEC_MOTION` section (append-only ABI, **no
version bump** — the section was reserved at ABI 1.0) plus the dense MV grid
appended after it (the `vf_pelorus_analyze` map-payload convention):

- `global_motion_x` / `global_motion_y` — mean block MV (pixels).
- `motion_magnitude_mean`, `motion_magnitude_p95` — MV magnitude mean and 95th
  percentile (the p95 is the robust pan/scene-cut signal; prefer it over the
  mean, which is diluted by aperture-ambiguous flat blocks).
- `motion_entropy` — normalized mean deviation of block MVs from the global MV
  (0 = rigid global motion, higher = complex / independent block motion).
- `has_scene_cut` — set when the mean residual SAD is high (no good match
  anywhere — characteristic of a cut, not coherent motion).
- `mv_field_offset` / `mv_field_size` — the appended `int16 (dx,dy)` grid,
  `grid_cols × grid_rows` cells, row-major.

These are telemetry for vmafx and the input contract for the (deferred) NVENC
ME-hint consumer.

## Scope and honesty

- **Speed, not quality.** No BD-rate or speed number ships with this filter; the
  measured win belongs to the consumer PR (the NVENC ME-hint patch, ADR-0114
  Tier 3). The honest v1 claim is "the MV field is produced and its direction is
  correct".
- **Why it does not feed the denoiser.** Block-matching on raw pixels matches
  grain as readily as motion; ADR-0113 measured in-denoise raw-pixel MC to be
  noise-limited (−28% vs the no-MC −34%) and reverted it. A search *seed* tolerates
  that; a denoise *warp* does not.
- **Magnitude under-reads on flat content.** On partially-flat frames many blocks
  are aperture-ambiguous (a range of displacements gives near-equal SAD) and settle
  at a small wrong MV, diluting the *mean*. Use `motion_magnitude_p95` or weight by
  per-block SAD rather than trusting the raw global mean for magnitude.

## Verification (this PR)

Direction verified on synthetic pans of a static textured still
(`mandelbrot`, looped, `crop`-translated), `bsize=16 search=48`, on a Vulkan
device:

| Fixture | Expected | Observed `global_motion` (steady state) |
|---|---|---|
| crop pans right 10 px/frame (content moves left) | `dx > 0` | `(+7, +2)` |
| crop pans left 10 px/frame | `dx < 0` | `(−7, +1)` |
| static (no pan) | `(0, 0)` | `(0, 0)` exactly |

The sign is correct in every case and static is exact; the magnitude (≈7 vs the
true 10) under-reads as documented above (mean dilution on the mandelbrot's flat
interior). This is the GPU producer's expected behaviour for an ME *seed*.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_mc_vulkan=bsize=16:search=24,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 out.mkv
```

The MV field rides the frames as `AV_FRAME_DATA_SEI_UNREGISTERED` (UUID-keyed)
and round-trips the filtergraph via `av_frame_copy_props`. A consumer (vmafx, or
the future NVENC ME-hint patch) parses it with `pel_blob_find_section`.
