<!-- markdownlint-disable MD013 -->
# Control-plane contract — `vf_pelorus_deband_vulkan` AVOptions

The **autotune control-plane contract** between Pelorus and vmafx. It freezes
the subset of `vf_pelorus_deband_vulkan` AVOptions that vmafx's `vmaf-tune`
autotune sweeps as a stable, named surface. Decision:
[ADR-0110](../adr/0110-avoption-control-plane-contract.md); coupling design:
[ADR-0106](0106-autotune-control-plane.md).

This is the **control plane** (how vmafx *drives* Pelorus), distinct from the
**data plane** ([interop ABI](interop-abi.md), how Pelorus *reports* perceptual
weighting back). The two are versioned independently.

## Stability tag

**Stable contract, frozen from v0.1.0.** vmafx (the consumer — `vmaf-tune`,
integration-plan workstream D1) hard-codes its deband search space against the
names and ranges below. **Renaming, removing, narrowing the range of, or
changing the type of any knob in the frozen table is a BREAKING change** — it
requires a coordinated two-repo PR (Pelorus + vmafx `vmaf-tune`) landing
together. *Widening* a range or *adding* a new tunable knob is backward
compatible (autotune simply does not explore the new headroom until it is taught
to). The contract is the `AVOption` table in
`ffmpeg-patches/files/vf_pelorus_deband_vulkan.c`; the bounds match the
host-side validation in `libpelorus/src/deband_params.c` and
[`pelorus/deband.h`](../../libpelorus/include/pelorus/deband.h).

## Frozen knobs (autotune-relevant)

These are the options `vmaf-tune` may set and sweep. Name, FFmpeg `AVOption`
type, valid range (`min`–`max`), default, and one-line semantics:

| AVOption | Type | Min | Max | Default | Semantics |
|---|---|---|---|---|---|
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

Notes on the contract:

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
|---|---|
| `sample` (tap topology) | algorithm-shape choice, not a strength dimension; defaults are tuned |
| `blur` (flat-test mode) | algorithm-shape choice |
| `planes` (plane bitmask) | pipeline configuration, not perceptual strength |
| `meta` (attach interop side data) | reporting switch (data-plane), set by the integration harness, not the optimizer |

`vmaf-tune` must **not** include these in its search space; set them once per run
(or leave at default) outside the optimization loop.

## How vmafx consumes it

In ADR-0106's in-graph mode, `vmaf-tune` builds an FFmpeg filtergraph that runs
`pelorus_deband_vulkan` with optimizer-chosen values for the frozen knobs, then
scores the processed-then-encoded output with `libvmaf_tune` in the same pass:

```bash
# vmaf-tune sweeps thry/grainy/range (frozen knobs) against VMAF as the oracle
ffmpeg -i src.mkv -i src.mkv -filter_complex \
  "[0:v]hwupload,pelorus_deband_vulkan=range=15:thry=0.012:grainy=0.006,hwdownload,format=p010le[pre];
   [pre][1:v]libvmaf_tune=recommend_target_vmaf=93" -f null -
```

Out-of-graph mode (vmafx-server `/v1/score` / `vmaf-mcp` scoring a finished
encode) sweeps the same frozen knobs across a distributed search. Either way the
optimizer's parameter space is exactly the frozen table above. See
[docs/metrics/deband.md](../metrics/deband.md) §VMAF-measured tuning loop for the
filter-side view and [ADR-0106](0106-autotune-control-plane.md) for the coupling
modes.

## Changing the contract

1. **Backward-compatible** (widen a range, add a new tunable knob): land in
   Pelorus; document the new range/knob here; vmafx adopts it on its own
   schedule. No two-repo coordination required.
2. **Breaking** (rename, remove, narrow a range, change a type, repurpose a
   value): forbidden as an incidental change. Open a coordinated PR pair —
   Pelorus updates the `AVOption` table + `pel_deband_params_validate` + this doc;
   vmafx updates `vmaf-tune`'s search space — and land them together. Note the
   break in the commit body and this repo's `docs/rebase-notes.md`.
