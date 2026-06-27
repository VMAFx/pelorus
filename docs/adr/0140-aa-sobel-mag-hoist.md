<!-- markdownlint-disable MD013 -->
# ADR-0140: Hoist the redundant aa sobel-mag into shared memory (opt-in `fast`)

- **Status**: Accepted
- **Date**: 2026-06-27
- **Deciders**: Lusoris

## Context

A premise-check (the negative-space twin of this decision) established that
`vf_pelorus_aa_vulkan` is **ALU-bound, not fetch-bound**: stripping the sobel
arithmetic while keeping every `imageLoad` collapsed the Arc rtime ~7× (−86%), so
shared-memory *tiling* (which caches fetches, ADR-0134) was refuted for aa and not
built. The cost is the sobel: `emask()` is invoked ~4× per pixel (the gx/gy
central differences), and each reduces a 17×17 window of `sobel_mag()` (a `sqrt`
+ ~18 FMA each) — ~1156 `sobel_mag` calls/px. The same `sobel_mag(cell)` is
recomputed across the four overlapping `emask` windows AND across neighbouring
pixels' overlapping windows: a massive redundant ALU bill.

## Decision

Add an opt-in `fast` AVOption (default **0**). When `fast=1`, the workgroup
cooperatively computes each cell's `sobel_mag` **once** into a `shared float
s_sobel[]` (its output region + a `PEL_HALO = MAX_R + 1` ring covering the
emask windows + the ±1 central-difference offset), and `emask()` reduces from the
cache instead of recomputing `sobel_mag()` per call. This is the ADR-0134 tiling
idiom applied to a *computed result* rather than a raw pixel: each `sobel_mag`
runs ~once per cell amortized instead of ~1156×/px.

The cooperative compute runs in uniform control flow (before the per-thread
`IS_WITHIN` guard) so its barrier is workgroup-uniform; `s_sobel` caches the exact
output of the same `sobel_mag()` (identical float ops, identical order), so
`fast=1` is **bit-identical** to `fast=0`. The `.comp` reference models the hoist
(the convention denoise uses for `tile=1`). Default 0 keeps today's path exactly.

Unlike tiling (a fetch win, only on bandwidth-limited GPUs), this is an **ALU**
win, so it helps every GPU.

## Validation

`fast=1` vs `fast=0` bit-identical: `cmp` 0 differing bytes + SSIM 1.000000 every
frame, on both Arc A380 and RTX 4090. 8×-chained aa, blur=8, 1080p, warm,
filter-only rtime (interleaved A/B ×3):

| GPU | fast=0 | fast=1 | speedup |
|---|---:|---:|---|
| Intel Arc A380 | 69.64 s | 5.52 s | **12.6× (−92.1 %)** |
| NVIDIA RTX 4090 | 3.10 s | 1.18 s | **2.6× (−62.0 %)** |

`s_sobel` is 10000 B (inline 50²) / 4624 B (`.comp` 34²) — fits the Arc A380's
49152 B shared-memory limit with margin. `meson test --suite=fast` 11/11; a
`vulkan-shader-reviewer` pass found no must-fix defects (barrier uniformity for
all plane configs, exact-fit cache bounds, push-const layout, lockstep all
confirmed).

## Consequences

- Bit-identical → safe to enable anywhere; a pure throughput knob. The win is
  concentrated in the edge-map gradient (the dominant cost); the `darkstr>0`
  line-darken + the `bilinear` warp scatter still read the image directly (the
  warp is unbounded, line-darken is off by default), so the headline numbers are
  at the default `darkstr=0`.
- aa was the slowest filter in the stack (≈70 s for an 8×-chain on Arc); `fast=1`
  makes it practical there. The `tune=anime` pipeline (which leans on aa) enables
  it.
- The aa(ALU) / dehalo+denoise(fetch) split now maps which optimization fits
  which kernel — tiling for fetch-bound box-blur/NLM windows, result-hoisting for
  ALU-bound stencils.

## References

- ADR-0134 (the shared-memory idiom this reuses), ADR-0124 (the aa filter),
  ADR-0125 (`tune=anime`). The aa-tiling premise-refutation
  (`.workingdir/OPPORTUNITIES.md`) is the negative-space twin that located this lever.
