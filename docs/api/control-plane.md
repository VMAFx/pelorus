<!-- markdownlint-disable MD013 -->
# Control-plane contracts — Pelorus filter and BSF AVOptions

The **autotune control-plane contracts** between Pelorus and vmafx. They freeze
the subsets of Pelorus filter and bitstream filter `AVOption`s that vmafx's
`vmaf-tune` autotune sweeps as stable, named surfaces across all ten video
filters and the `pelorus_fgs` bitstream filter. Decisions:
[ADR-0110](../adr/0110-avoption-control-plane-contract.md) (deband control-plane
contract) and [ADR-0196](../adr/0196-filter-knob-contracts.md) (per-filter JSON
knob contracts); coupling design:
[ADR-0106](../adr/0106-autotune-control-plane.md).

This is the **control plane** (how vmafx *drives* Pelorus), distinct from the
**data plane** ([interop ABI](interop-abi.md), how Pelorus *reports* perceptual
weighting back). The two are versioned independently.

## Stability tag

**Stable contracts, frozen from contract landing.** For `pelorus_deband_vulkan`,
the deband contract is frozen from v0.1.0 ([ADR-0110](../adr/0110-avoption-control-plane-contract.md)).
For all other filters and `pelorus_fgs`, published contracts in
[`docs/api/knobs/*.json`](knobs/) freeze every option with `tunable: true`
(`effect_class: "strength"`) from their contract landing under
[ADR-0196](../adr/0196-filter-knob-contracts.md).

Per [ADR-0196](../adr/0196-filter-knob-contracts.md) Consequences:

> **Breaking** — renaming, removing, narrowing the `[min, max]` range of,
> changing the `type` of, or removing/renumbering an enum constant of any knob
> with `tunable: true` (`effect_class: "strength"`), or flipping a knob from
> `tunable: true` to `false`. Requires a coordinated two-repository PR
> (Pelorus + VMAFx `FilterAdapter` / `vmaf-tune`) and a note in
> `docs/rebase-notes.md`.

*Widening* a `[min, max]` range, *adding* a new `tunable: true` knob, or changing
`tunable: false` (`shape`, `topology`, `reporting`) knobs is backward compatible
and can land in Pelorus alone.

The underlying contracts are the `AVOption` tables in `ffmpeg-patches/files/`
(with `PEL_VK_POOL_OPTIONS` expanded); for `pelorus_deband_vulkan`, the bounds
match the host-side validation in `libpelorus/src/deband_params.c` and
[`pelorus/deband.h`](../../libpelorus/include/pelorus/deband.h).

## Frozen knobs: `pelorus_deband_vulkan`

These are the `pelorus_deband_vulkan` options `vmaf-tune` may set and sweep.
Name, FFmpeg `AVOption` type, valid range (`min`–`max`), default, and one-line
semantics:

| AVOption | Type | Min | Max | Default | Semantics |
| --- | --- | --- | --- | --- | --- |
| `range` | int | 1 | 31 | 15 | reference-sampling radius in pixels |
| `thry` | double | 0.0 | 0.25 | 0.012 | luma flat-test threshold (normalized to full range) |
| `thrc` | double | 0.0 | 0.25 | 0.012 | chroma flat-test threshold (normalized) |
| `grainy` | double | 0.0 | 0.4 | 0.006 | luma grain amplitude (normalized; ≈1.5 LSB @ 8-bit) |
| `grainc` | double | 0.0 | 0.4 | 0.0 | chroma grain amplitude (normalized) |
| `softness` | float | 0.0 | 1.0 | 0.5 | soft-blend transition width (`0` = hard switch) |
| `detail` | float | 0.0 | 0.25 | 0.06 | detail-mask activity threshold (normalized) |
| `dither` | int (enum) | 0 | 2 | 2 (`bluenoise`) | grain / dither mode: `0`=none, `1`=bayer8, `2`=bluenoise |
| `dynamic` | bool | 0 | 1 | 1 | re-seed grain each frame |
| `protect` | bool | 0 | 1 | 1 | gate debanding off textured regions |

Notes on the `pelorus_deband_vulkan` contract:

- **Normalization is part of the contract.** `thry`/`thrc`/`grainy`/`grainc`/
  `detail` are normalized to full range, **bit-depth independent** — the filter
  works in a 16-bit internal domain (see `pelorus/deband.h`). An optimizer can
  sweep one search space for 8/10/12-bit content without rescaling.
- **`thr*`/`grain*`/`detail` validation is tighter than `[0,1]`.** The filter
  accepts the `[min,max]` above; the host-side `pel_deband_params_validate`
  enforces the same upper caps (`thr ≤ 0.25`, `grain ≤ 0.4`, `detail ≤ 0.25`).
  An optimizer that respects this table will never trip validation.
- **`dither` enum values are frozen by integer.** `0/1/2` map to
  `none/bayer8/bluenoise`; the names and the integers are both part of the
  contract (the filter also accepts the string forms).
- **`range` is an integer search dimension** — TPE/grid should treat it as
  ordinal, not continuous.

## Out-of-contract options (NOT frozen — do not autotune)

`vf_pelorus_deband_vulkan` exposes other AVOptions that are **intentionally
excluded** from the control-plane freeze. They are pipeline-topology or
reporting switches, not perceptual-strength knobs, and may change without a
two-repo coordination:

| AVOption | Why excluded |
| --- | --- |
| `sample` (tap topology) | algorithm-shape choice, not a strength dimension; defaults are tuned |
| `blur` (flat-test mode) | algorithm-shape choice |
| `planes` (plane bitmask) | pipeline configuration, not perceptual strength |
| `meta` (attach interop side data) | reporting switch (data-plane), set by the integration harness, not the optimizer |
| `tiling` (pool image tiling) | hardware pool topology (`PEL_VK_POOL_OPTIONS`, [ADR-0184](../adr/0184-vulkan-drm-modifier-output-pools.md)) |
| `drm_modifiers` (DRM modifier allow-list) | hardware pool topology (`PEL_VK_POOL_OPTIONS`, [ADR-0184](../adr/0184-vulkan-drm-modifier-output-pools.md)) |

`vmaf-tune` must **not** include these in its search space; set them once per run
(or leave at default) outside the optimization loop.

## Per-filter JSON knob contracts (`docs/api/knobs/<filter>.json`)

Under [ADR-0196](../adr/0196-filter-knob-contracts.md), every Pelorus video
filter (`vf_pelorus_*`) and bitstream filter (`pelorus_fgs`) publishes a
machine-readable JSON contract in [`docs/api/knobs/`](knobs/), generated from
the `AVOption` tables in `ffmpeg-patches/files/` (with `PEL_VK_POOL_OPTIONS`
expanded from `ffmpeg-patches/files/pelorus_vulkan_pool.h`) and checked for
drift by [`scripts/check-filter-knob-contracts.py`](../../scripts/check-filter-knob-contracts.py).
The frozen deband table above remains authoritative for
`pelorus_deband_vulkan`, and the checker asserts that
[`docs/api/knobs/pelorus_deband_vulkan.json`](knobs/pelorus_deband_vulkan.json)
agrees with it on every knob name, type, bound, default, and frozen vs excluded
classification.

### Published contracts

| Filter / BSF | Contract | Source | `strength` (`tunable: true`) | `shape` | `topology` | `reporting` |
| --- | --- | --- | --- | --- | --- | --- |
| `pelorus_aa_vulkan` | [`pelorus_aa_vulkan.json`](knobs/pelorus_aa_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_aa_vulkan.c` | `blur`, `depth`, `thresh`, `darkstr`, `edge` | — | `planes`, `fast`, `tiling`, `drm_modifiers` | — |
| `pelorus_analyze_vulkan` | [`pelorus_analyze_vulkan.json`](knobs/pelorus_analyze_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_analyze_vulkan.c` | `flat`, `roi_strength`, `grad_lo`, `grad_hi` | `cell` | — | `roi`, `maps` |
| `pelorus_borderfix_vulkan` | [`pelorus_borderfix_vulkan.json`](knobs/pelorus_borderfix_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_borderfix_vulkan.c` | — | `left`, `right`, `top`, `bottom` | `planes`, `tiling`, `drm_modifiers` | — |
| `pelorus_deband_vulkan` | [`pelorus_deband_vulkan.json`](knobs/pelorus_deband_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_deband_vulkan.c` | `range`, `thry`, `thrc`, `grainy`, `grainc`, `softness`, `detail`, `dither`, `dynamic`, `protect` | `sample`, `blur` | `planes`, `tiling`, `drm_modifiers` | `meta` |
| `pelorus_deblock_vulkan` | [`pelorus_deblock_vulkan.json`](knobs/pelorus_deblock_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_deblock_vulkan.c` | `thr`, `str` | `bsize`, `edge` | `planes`, `tiling`, `drm_modifiers` | — |
| `pelorus_dehalo_vulkan` | [`pelorus_dehalo_vulkan.json`](knobs/pelorus_dehalo_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_dehalo_vulkan.c` | `blur`, `darkstr`, `brightstr`, `lowsens`, `highsens`, `edge`, `ring` | — | `planes`, `tile`, `tiling`, `drm_modifiers` | — |
| `pelorus_denoise_vulkan` | [`pelorus_denoise_vulkan.json`](knobs/pelorus_denoise_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_denoise_vulkan.c` | `sigma`, `sigmac`, `sigmat`, `strength`, `strengthc`, `blend`, `tdecay`, `tcut`, `protect` | `patch`, `prev` | `planes`, `mc`, `tile`, `lookahead`, `tiling`, `drm_modifiers` | `meta` |
| `pelorus_grain_estimate_vulkan` | [`pelorus_grain_estimate_vulkan.json`](knobs/pelorus_grain_estimate_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c` | `edge`, `strength` | `model` | — | `native` |
| `pelorus_mc_vulkan` | [`pelorus_mc_vulkan.json`](knobs/pelorus_mc_vulkan.json) | `ffmpeg-patches/files/vf_pelorus_mc_vulkan.c` | — | `bsize`, `search` | — | `meta` |
| `pelorus_scenecut` | [`pelorus_scenecut.json`](knobs/pelorus_scenecut.json) | `ffmpeg-patches/files/vf_pelorus_scenecut.c` | — | — | `force_idr` | — |
| `pelorus_fgs` | [`pelorus_fgs.json`](knobs/pelorus_fgs.json) | `ffmpeg-patches/files/h265_pelorus_fgs_bsf.c` | `log2_scale`, `scale_y`, `scale_c`, `cutoff_h`, `cutoff_v` | `model_id`, `blending_mode`, `intensity_low`, `intensity_high`, `intensity_low_c`, `intensity_high_c` | `components`, `persistence`, `skip_existing` | — |

### JSON contract format (`schema_version: 1`)

Each `docs/api/knobs/<filter>.json` file is a UTF-8 JSON object with stable key
order:

| Field | Scope | Type | Description |
| --- | --- | --- | --- |
| `schema_version` | root | `int` | Contract schema version (`1`). |
| `filter` | root | `string` | Canonical FFmpeg `.p.name` (for example `"pelorus_deband_vulkan"` or `"pelorus_fgs"`). |
| `kind` | root | `string` | `"vf"` for video filters (`FFFilter`) or `"bsf"` for bitstream filters (`FFBitStreamFilter`). |
| `source` | root | `string` | Repository-relative C source path under `ffmpeg-patches/files/`. |
| `options` | root | `array<object>` | Ordered list of `AVOption` descriptors (excluding `AV_OPT_TYPE_CONST` rows, which are nested under their parent option's `constants`). |
| `name` | `options[]` | `string` | `AVOption` name passed on the FFmpeg filtergraph (`<filter>=<name>=<value>`). |
| `type` | `options[]` | `string` | Normalized option type: `"int"`, `"int64"`, `"double"`, `"float"`, `"bool"`, `"string"`, `"flags"`, or `"const-enum"` (`AV_OPT_TYPE_INT` with associated `AV_OPT_TYPE_CONST` rows). |
| `min` | `options[]` | `int \| float \| null` | Inclusive lower bound from `AVOption.min` (`null` for `"string"`). |
| `max` | `options[]` | `int \| float \| null` | Inclusive upper bound from `AVOption.max` (`null` for `"string"`). |
| `default` | `options[]` | `int \| float \| string \| null` | Default scalar or string from `AVOption.default_val` (`0`/`1` for `"bool"`). |
| `unit` | `options[]` | `string \| null` | `AVOption.unit` enum group tag when the option has named `AV_OPT_TYPE_CONST` rows, else `null`. Represents FFmpeg's internal enum group identifier, not a physical unit; physical units appear only in `help` (a dedicated physical units field is out of scope and tracked as a follow-up). |
| `constants` | `options[]` | `array<object>` | Named constants sharing `.unit`, each `{ "name": string, "value": int, "help": string }` (`[]` when `unit` is `null`). |
| `effect_class` | `options[]` | `string` | One of `"strength"` (perceptual intensity/threshold knob), `"shape"` (algorithm geometry or target model), `"topology"` (planes, pool tiling, shared-memory or temporal plumbing), or `"reporting"` (side-data emission switch). |
| `tunable` | `options[]` | `bool` | `true` if and only if `effect_class == "strength"`. |
| `help` | `options[]` | `string` | Help string from the `AVOption` row. |

### Inspecting tunable knobs

To list the `tunable: true` search dimensions from any published contract, filter on `o["tunable"]`:

```bash
python3 -I -c 'import json; d = json.load(open("docs/api/knobs/pelorus_deband_vulkan.json")); print([o["name"] for o in d["options"] if o["tunable"]])'
```

Output:

```text
['range', 'thry', 'thrc', 'grainy', 'grainc', 'softness', 'detail', 'dither', 'dynamic', 'protect']
```

## How vmafx consumes it

In ADR-0106's in-graph mode, `vmaf-tune` builds an FFmpeg filtergraph that runs
`pelorus_deband_vulkan` (or any other Pelorus prefilter via VMAFx's
`FilterAdapter` protocol, VMAFx ADR-1116) with optimizer-chosen values for the
`tunable: true` (`effect_class: "strength"`) knobs, then scores the
processed-then-encoded output with `libvmaf_tune` in the same pass. This pipeline
is illustrative (the command requires `libvmaf_tune` from the sibling vmafx
FFmpeg patches, which is not linked into the Pelorus test binary; see
[docs/usage/ffmpeg.md](../usage/ffmpeg.md#vmaf-in-the-loop-autotune-with-vmafx)):

```bash
# vmaf-tune sweeps thry/grainy/range (frozen knobs) against VMAF as the oracle
ffmpeg -i src.mkv -i src.mkv -filter_complex \
  "[0:v]hwupload,pelorus_deband_vulkan=range=15:thry=0.012:grainy=0.006,hwdownload,format=p010le[pre];
   [pre][1:v]libvmaf_tune=recommend_target_vmaf=93" -f null -
```

VMAFx's `FilterAdapter` loader (`pkg/prefilter` and `tools/vmaf-tune`, per
VMAFx ADR-1116) is intended to consume `docs/api/knobs/<filter>.json` directly
(today vmafx hard-codes only `pelorus_deband_vulkan`, as recorded in
[docs/research/0172-vmafx-scope-gap.md](../research/0172-vmafx-scope-gap.md):231
gap G7):

1. **Search-space construction**: options with `"tunable": true`
   (`"effect_class": "strength"`) define the optimizer's search dimensions:
   `"double"` and `"float"` map to continuous `[min, max]` intervals, `"int"`
   maps to integer `[min, max]`, `"bool"` maps to `{0, 1}`, and `"const-enum"`
   maps to the categorical integer/name set in `constants`.
2. **Fixed run configuration**: options with `"tunable": false` (`"shape"`,
   `"topology"`, `"reporting"`) are excluded from the search loop and exposed as
   per-invocation adapter settings (for example `tiling=drm` on VAAPI/QSV chains
   or `meta=1` when perceptual side data is requested).

Out-of-graph mode (vmafx-server `/v1/score` / `vmaf-mcp` scoring a finished
encode) sweeps the same `tunable: true` knobs across a distributed search. See
[docs/metrics/deband.md](../metrics/deband.md) §VMAF-measured tuning loop for the
filter-side view and [ADR-0106](../adr/0106-autotune-control-plane.md) for the
coupling modes.

## Changing the contract

1. **Backward-compatible** (widen a range, add a new tunable knob, or adjust a
   `tunable: false` knob): land in Pelorus; if adding a new option or filter,
   record its `effect_class` in `EFFECT_CLASS_MAP` in
   `scripts/check-filter-knob-contracts.py` first, then run
   `python3 -I scripts/check-filter-knob-contracts.py --write` (and update the
   table here if `pelorus_deband_vulkan` changed); vmafx adopts it on its own
   schedule. No two-repo coordination required.
2. **Breaking** (rename, remove, narrow a range, change a type, or remove/revalue
   an enum constant on any `tunable: true` knob): forbidden as an incidental
   change. Open a coordinated PR pair — Pelorus updates the `AVOption` table +
   host validation + `docs/api/knobs/<filter>.json` + this doc; vmafx updates
   `FilterAdapter` / `vmaf-tune`'s search space — and land them together. Note
   the break in the commit body and this repo's `docs/rebase-notes.md`.
