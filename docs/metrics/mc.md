<!-- markdownlint-disable MD013 -->
# vf_pelorus_mc_vulkan

A GPU block-matching **motion estimator**, run as a zero-copy pass in VRAM. It
produces a per-block quarter-pel (sub-pel-refined) motion-vector field for the current frame
relative to the previous frame and attaches it as the pre-reserved
`PEL_SEC_MOTION` interop section. **The frame passes through unchanged** — this
is a producer (the analyzer shape), not a transform.

The field has two shipped, opt-in consumers. NVENC can feed it to
`NV_ENC_EXTERNAL_ME_HINT` as an encode-search hint (a speed path, not a quality
claim). `pelorus_denoise_vulkan=mc=1` consumes the same quarter-pel field plus
its confidence map to warp temporal taps, a quality-oriented path. It is not a
general-purpose optical-flow field. See
[ADR-0116](../adr/0116-pelorus-mc.md) for the producer decision,
[ADR-0113](../adr/0113-optical-flow-mc.md) for the motion-estimation strategy,
and [ADR-0114](../adr/0114-encoder-steering.md) Tier 3 for the gated NVENC
ME-hint consumer.

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
remain in whole luma pixels. The shipped shader is
`ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl`; the similarly named
`libpelorus/shaders/*.comp` file is a compile-checked standalone reference, not
a second shipped implementation. Luma loads are converted to the logical
sample domain before SAD evaluation.

### MV units

The search itself runs on the integer luma-pel grid; only its output is
refined to quarter-pel. Each MV-carrying field therefore has a fixed unit:

| Field | Unit |
| --- | --- |
| shader output `mv_x[]` / `mv_y[]` | Q2 quarter-pel luma |
| `PEL_SEC_MOTION` grid | Q2 quarter-pel luma, `int16` |
| `PelorusMotionSection` scalars | luma pixels (`float`) |
| shader input `prev_mv[]`, push constants `gpred_x` / `gpred_y`, `search` | integer luma pel |

The host is the single conversion point. When it rolls a frame's Q2 field into
the next frame's predictors it rounds each vector to integer pel, half away from
zero (the rounding the NVENC ME-hint consumer applies to the same grid), and
rounds the exact mean the same way for the global predictor. The helpers live
in `ffmpeg-patches/files/pelorus_mc_stats.h`, and the `mc-stats` fast test
covers them. Before this conversion existed, the host returned the Q2 values
unchanged, and the search read them as whole pixels, four times too far
(BUG-009).

### Host-side cost

After each dispatch the host copies the MV and SAD buffers out of device-local
mapped memory with one sequential copy per buffer. Every later pass (frame
scalars, confidence grid, predictors) then reads cached host memory. The copy
targets and the p95 scratch are allocated when the block grid first appears or
grows, never per frame in steady state. `motion_magnitude_p95` is an O(n) radix
select over the non-negative magnitudes, bit-identical to the earlier O(n²)
counting scan (BUG-014).

## Options

| Option | Default | Range | Meaning |
| --- | --- | --- | --- |
| `bsize` | 16 | 8–32 | motion-estimation block edge in luma pixels |
| `search` | 24 | 1–256 | max search radius per axis in luma pixels |
| `meta` | on | bool | attach the `PEL_SEC_MOTION` interop section (the MV field + scalars) |

`bsize` and `search` are device-agnostic — the same pipeline serves every block
size (the workgroup is `32 × 32`; lanes past `bsize` contribute 0), so the filter
is a product for any Vulkan GPU, not tuned to one device.

## Pipeline placement

The estimator reads the source luma; place it after `hwupload` and before any
pixel-modifying stage so its MVs describe the frames the encoder will see:

```text
hwupload → pelorus_analyze → pelorus_mc → pelorus_denoise → pelorus_deband → (hwdownload) → encoder
```

It keeps a 1-frame causal history (a clone — a refcount bump on the hwframe, no
pixel copy) as the reference. Frame 0 has no reference and emits a zero field.

## Interop (`meta=1`)

With `meta=1` the producer also emits **`PEL_SEC_MOTION_CONF`** (interop ABI
minor 2, ADR-0131): a per-block match-confidence field. The denoise `mc=1`
consumer uses it to gate the motion-compensated warp, so a weakly matched block
falls back to same-coordinate temporal averaging instead of dragging a bad
vector into the result. Append-only, so an older consumer that does not know the
section simply ignores it.

Emits the pre-reserved 32-byte `PEL_SEC_MOTION` section (append-only ABI, **no
version bump** — the section was reserved at ABI 1.0) plus the dense MV grid
appended after it (the `vf_pelorus_analyze` map-payload convention):

- `global_motion_x` / `global_motion_y` — mean block MV (pixels).
- `motion_magnitude_mean`, `motion_magnitude_p95` — MV magnitude mean and 95th
  percentile in pixels; the p95 is the `ceil(0.95 × N)`-th smallest block
  magnitude. The p95 is the robust pan/scene-cut signal; prefer it over the
  mean, which is diluted by aperture-ambiguous flat blocks.
- `motion_entropy` — normalized mean deviation of block MVs from the global MV
  (0 = rigid global motion, higher = complex / independent block motion).
- `has_scene_cut` — set when the mean residual SAD is high (no good match
  anywhere — characteristic of a cut, not coherent motion).
- `mv_field_offset` / `mv_field_size` — the appended `int16 (dx,dy)` grid,
  `grid_cols × grid_rows` cells, row-major.

These are telemetry for vmafx and the input contract for the shipped denoise
warp and NVENC ME-hint consumers.

## Scope and honesty

- **The NVENC leg is speed-only and optional.** On the measured RTX 4090 case it
  did not improve speed (roughly 2–3% slower at p7); do not infer a speed win from
  the existence of the hint consumer.
- **The denoise leg is confidence-gated.** ADR-0113's earlier un-gated raw-pixel
  warp was noise-limited (−28% vs the no-MC −34%). The shipped ADR-0131 path adds
  `PEL_SEC_MOTION_CONF` and `tcut` fallback so weak vectors use same-coordinate
  temporal averaging. This makes the consumer usable, but does not turn the old
  stand-in number into a current-filter BD-rate claim.
- **Magnitude under-reads on flat content.** On partially-flat frames many blocks
  are aperture-ambiguous (a range of displacements gives near-equal SAD) and settle
  at a small wrong MV, diluting the *mean*. Use `motion_magnitude_p95` or weight by
  per-block SAD rather than trusting the raw global mean for magnitude.
- **Start-up and large search radii.** Frame 1 has only the zero predictor, so a
  pan wider than the texture's correlation length needs a few frames to lock on
  through the temporal predictor. The global predictor is the plain mean, so
  wrong blocks pull it off the true pan; with `search=48` the 5 px pan below
  needed about 20 frames to converge.

## Verification

Measured with a noise-textured still (`testsrc2` + uniform noise + `gblur`,
looped), translated by `crop` with a known per-frame shift, 1920×1080 NV12,
30 frames, on an RTX 4090. "Exact" is the share of interior blocks whose Q2
vector equals the true shift; "steady" averages frames 10–29. Before is the
filter without the BUG-009 predictor conversion.

| Pan (px/frame) | Options | Before: exact / mean vector | After: exact / mean vector |
| --- | --- | --- | --- |
| (2, 0) | `bsize=16:search=24` | 83.6% / (1.73, 0.03) | 99.8% / (2.00, 0.00) |
| (6, 0) | `bsize=16:search=24` | 30.0% / (7.87, 6.15) | 99.8% / (6.00, 0.00) |
| (10, 0) | `bsize=16:search=24` | 63.0% / (10.52, 0.36) | 99.8% / (10.00, 0.00) |
| (3, −2) | `bsize=16:search=24` | 20.8% / (6.30, −5.91) | 99.9% / (3.00, −2.00) |
| (7, 3) | `bsize=16:search=24` | 6.2% / (7.62, 7.15) | 98.6% / (6.92, 2.96) |
| (2, 0) | `bsize=8:search=24` | 52.1% / (1.67, 0.00) | 99.7% / (2.00, 0.00) |
| (5, 0) | `bsize=16:search=48` | 16.6% / (−11.46, 13.25) | 86.1% / (3.02, −0.03) |
| static | `bsize=16:search=24` | 100% / (0, 0) | 100% / (0, 0) |

Over the same frames the mean per-block confidence rose from 218–249 to
249–255, and a hard cut spliced at frame 15 was flagged on that frame alone,
both before and after. The `search=48` case reaches 99.8% at frame 21 and 100%
from frame 22 (see the scope note above). The emitted p95 equals an O(n²) recomputation from the
emitted grid on every frame.

Wall time per frame (`ffmpeg -benchmark` rtime, median of interleaved before
and after runs, whole pipeline including source generation and upload). The
two values are two benchmark sessions on a shared host:

| Grid | Before | After |
| --- | --- | --- |
| 1080p, `bsize=16` (8160 blocks) | 30.1 / 41.9 ms | 4.5 / 3.0 ms |
| 1080p, `bsize=8` (32400 blocks) | 157.0 / 182.6 ms | 11.5 / 9.4 ms |
| 2160p, `bsize=8` (129600 blocks) | 1340.8 / 1570.3 ms | 56.3 / 45.7 ms |

Almost all of the old cost was host-side: the O(n²) p95 scan and element-wise
reads of the mapped GPU buffers.

The earlier mandelbrot pan check, which read about 7 px for a 10 px pan, was
taken with the 4× predictor error and is superseded by the table above.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_mc_vulkan=bsize=16:search=24:meta=1,pelorus_denoise_vulkan=mc=1,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 out.mkv
```

The MV field rides the frames as `AV_FRAME_DATA_SEI_UNREGISTERED` (UUID-keyed)
and round-trips the filtergraph via `av_frame_copy_props`. vmafx, the denoise
`mc=1` path, and the NVENC ME-hint patch consume the same versioned section;
the first two can use libpelorus parsing, while the codec-local NVENC bridge
uses its bounded compatible parser.
