<!-- markdownlint-disable MD013 MD060 -->
# ADR-0113: vf_pelorus_mc — GPU motion estimation feeding MC-temporal denoise (and, gated, NVENC ME hints)

- **Status**: Accepted
- **Date**: 2026-06-14 (accepted 2026-06-20)
- **Deciders**: Lusoris
- **Tags**: motion, vulkan, denoise, interop, nvenc, roadmap

> **Realized by** ADR-0130 (the sub-pel quarter-pel MV field) and ADR-0131 (the
> MC→denoise warp + per-block confidence gating, validated on the GPU). The
> `vf_pelorus_mc` estimator itself shipped under ADR-0115.

## Context

`vf_pelorus_denoise_vulkan` (ADR-0112) does **no** motion compensation: its
temporal taps are same-coordinate, gated by `tcut`. That is robust but leaves the
moving regions of a frame undenoised (the gate breaks on motion). Two in-filter
attempts to add motion compensation were built and **measured to fail** (ADR-0111
methodology, real GPU): winner-take-all block matching gave −28% vs the no-MC
−34% (it matches grain, not motion), and NLM-temporal over-blurred to VMAF 73–83
(worse than the noisy input) even on light grain. Root cause: any similarity
metric over **raw, noisy pixels**, computed *inside* the denoise dispatch, is
swamped. Robust motion needs a dedicated estimator producing a real MV field that
the denoiser then *warps* its fetch by — not a raw search bolted onto denoise.

## Decision

Add `vf_pelorus_mc_vulkan`: a GPU **block-matching EPZS** motion estimator (v1),
transcribed from FFmpeg's own `libavfilter/motion_estimation.c` (SAD cost +
predictor-seeded diamond descent) — the only candidate with a complete validated
reference in-tree (Lucas-Kanade / Horn-Schunck appear nowhere in ffmpeg;
`VK_NV_optical_flow` is half-wired in `libavutil/vulkan_functions.h` but NVIDIA-
only with no in-tree example, so it is a later optional fast-path behind the same
output contract). One workgroup per 16×16 block, SAD via subgroup/shared-memory
reduction, predictors seeded from spatial neighbours + a persistent previous-
frame MV SSBO.

- **Interop**: fill the **already-reserved** `PEL_SEC_MOTION` section
  (`interop.h`, bit `1u<<4`, `PelorusMotionSection` frozen at 32 bytes) with the
  frame scalars (global motion, magnitude mean/p95, entropy, scene-cut flag) and
  append the dense int16 ¼-pel `(dx,dy)` MV grid after the packed section — the
  `vf_pelorus_analyze` `attach_stats` pattern. **No ABI bump** for v1 (a per-cell
  confidence map later would append-only via `bump-abi`, mirrored into the
  vmafx-vendored `interop.c`).
- **Pipeline-order correction**: `analyze → mc → denoise → deband`. MC must sit
  **before** denoise (denoise is the first MV consumer); the current
  `.workingdir/PLAN.md` ordering puts `mc` last, which cannot feed the warp — fix
  it.
- **Denoise consumption (the fix)**: a `mc` AVOption sets the reserved
  `PEL_DENOISE_FLAG_MOTION_COMP`; the denoise filter parses `PEL_SEC_MOTION` from
  the input frame *before* dispatch, uploads the MV grid, and replaces
  `pel_prev(t, pos)` with `pel_prev_bilinear(t, pos + MV)` (bilinear sub-pel,
  per-pixel MV from a bilinearly-upsampled grid, chroma MV subsampling). The
  existing `tcut` gate stays as the occlusion safety-net. Edited in **lockstep**
  across `pelorus_denoise.comp` and the filter's inline GLSL (AGENTS rule 4).

## Encoder ME hints — feasible via our patch stack, gated on proof

Stock ffmpeg cannot inject MVs into any encoder (`nvenc.c`/`libx265.c`/SVT-AV1
wrappers expose nothing — verified, zero hits). **But Pelorus ships an ffmpeg
patch stack**, and the NVENC SDK 13.0 headers on the build host
(`ffnvcodec/nvEncodeAPI.h`) fully define the API: `enableExternalMEHints` +
`maxMEHintCountsPerBlock[2]` at init, and per-frame `meHintCountsPerBlock[2]` +
`meExternalHints` (`NVENC_EXTERNAL_ME_HINT`: integer mvx/mvy, refidx, dir,
partType; H.264/HEVC). So a **future** patch to `libavcodec/nvenc.c` (behind an
AVOption) can read our `PEL_SEC_MOTION` grid and feed NVENC's external-ME-hint
path — something stock ffmpeg cannot do, a genuine Pelorus differentiator.

This is **explicitly gated**, not promised: it is **NVENC-only**, **HEVC/H.264**,
**integer-pel**, and the RD benefit is **unverified** — NVENC's internal ME is
already strong, so external hints help only where our MVs beat its latency-bound
search (hard/large motion) and may be neutral or negative otherwise. It ships
**only** if a BD-rate experiment shows a win. v1 of `vf_pelorus_mc` is
**denoise-internal**; the encoder-hint patch is a separate, measured follow-up.

## Alternatives considered

| Option | Pros | Cons | Why not (for v1) |
|---|---|---|---|
| Block-matching EPZS on GPU (this) | Validated in-tree reference; predictor-seeded → robust on real motion; integer block MVs are exactly what warp needs | Blocky at motion boundaries; integer-pel search | **Chosen** — transcription not invention |
| In-denoise raw-pixel MC (block-match / NLM) | No second filter | **Measured to fail** (−28% / over-blur) — noise swamps the metric | Rejected (ADR-0111 data) |
| Pyramidal Lucas–Kanade / Horn–Schunck | Dense sub-pixel flow | Not in ffmpeg (invent from scratch); degrade on flat noisy regions / need many iters | Deferred |
| `VK_NV_optical_flow` (HW engine) | Dense HW flow, fast | NVIDIA-only; zero in-tree usage; orchestration written blind | Later fast-path behind same contract |
| CPU `vf_mestimate` side-data | No new GPU code | Breaks VRAM-resident zero-copy; CPU ME throughput | Rejected |

## Consequences

- **Positive**: gives the denoiser real motion compensation (warp the fetch),
  which is the only way to denoise moving regions without ghosting; fills the
  reserved motion interop plane for vmafx; opens the (gated) NVENC-hint path.
- **Negative / honest scope**: this is a **large** roadmap filter — ~2.5–4 weeks
  for v1 (EPZS compute + SAD reduction + predictor SSBO; the filter + readback;
  the denoise warp consumer in lockstep; docs/ADR/patch/validation). Not a
  one-PR change.
- **Risks**: block-MV seams (mitigate by bilinear grid upsample in the shader);
  bad/occluded MVs re-introducing ghosting (the `tcut` gate is the net);
  `copy_props` ordering (parse `PEL_SEC_MOTION` from `in` before dispatch);
  lockstep drift (edit both shader copies together); encoder-hint **overclaim**
  (state plainly: denoise-internal for v1).

## References

- `libavfilter/motion_estimation.c` (EPZS reference), `vf_mestimate.c`,
  `vf_minterpolate.c`, `libavutil/vulkan_functions.h` (`FF_VK_EXT_OPTICAL_FLOW`),
  `/usr/include/ffnvcodec/nvEncodeAPI.h` (`enableExternalMEHints` / `meExternalHints`).
- `libpelorus/include/pelorus/interop.h` (`PEL_SEC_MOTION`, `PelorusMotionSection`),
  [ADR-0112](0112-temporal-denoise.md) (the no-MC denoiser this extends),
  [ADR-0111](0111-benchmark-methodology.md) (the proof method + the MC-failure data).
- Source: `req` — "where motion comp? why stop then?" and "well, can we expose
  encoder hints?" (build robust GPU motion estimation to feed MC-temporal denoise,
  and determine whether the MVs can drive NVENC's external-ME-hint API — verified
  feasible via an nvenc.c patch, gated on a measured BD-rate win).
