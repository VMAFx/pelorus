<!-- markdownlint-disable MD013 -->
# ADR-0139: Shared-memory tiling of the dehalo box-blur window (opt-in)

- **Status**: Accepted
- **Date**: 2026-06-27
- **Deciders**: Lusoris

## Context

`vf_pelorus_dehalo_vulkan`'s halo estimate is a `box_blur()` over a
`(2·MAX_R+1)²` window (17×17 at max reach), and the filter invokes it ~5× per
pixel (the centre + an L/R/U/D cross) → ~1445 `imageLoad`/px from a heavily
overlapping window, with trivial ALU (adds + one divide). This is the same
redundant-fetch profile that motivated the denoise shared-memory tiling
(ADR-0134, +65% on Arc).

A premise-check confirmed dehalo is **fetch-bound** (unlike `aa`, whose sobel
makes it ALU-bound — that tiling was refuted): an ALU-strip variant that keeps
the full loop trip-count but reduces each box-blur to one `imageLoad` collapsed
the Arc rtime 11.5 s → 3.4 s (−70%), i.e. the fetches, not the ALU, are the cost.

## Decision

Add an opt-in `tile` AVOption (default **0**). When `tile=1`, the 32×32 workgroup
cooperatively loads its output region plus a `PEL_HALO = MAX_R + 1`-pixel ring
(covering the union of all five box-blur windows + the Sobel/contrast 3×3 + the
ring scan) into a `shared float s_tile[]` once per plane, and every spatial read
(`pel_luma`) hits shared memory instead of the image. The tile load runs in
uniform control flow (before the per-thread `IS_WITHIN` guard) so its barriers
are workgroup-uniform; the tiled read mirrors the direct read's edge-clamp
exactly, so `tile=1` is **bit-identical** to `tile=0`. The `.comp` reference is
kept in lockstep (AGENTS rule 4). Default 0 (flagship-first), matching `tile` on
denoise — the win is on bandwidth-limited GPUs.

## Validation

8×-chained dehalo, blur=8 (17×17), 1080p, warm, filter-only rtime; `tile=1`
output bit-identical to `tile=0` (`cmp` 0 differing bytes, SSIM 1.000000 every
frame, both GPUs):

| GPU | tile=0 | tile=1 | Δ |
|---|---:|---:|---|
| Intel Arc A380 | ~12.2 s | ~7.5 s | **−38 % (1.6×)** |
| NVIDIA RTX 4090 | ~1.2–1.5 s | ~1.25–1.6 s | ~neutral |

The win tracks memory-bandwidth pressure — large on the Arc, nil where a large L2
caches the window — so `tile` defaults off and is opt-in for weak / integrated /
mobile GPUs (and the `tune=anime` pipeline, which leans on dehalo).

## Consequences

- Bit-identical, so safe to enable anywhere; a throughput knob only. One
  `shared float[2500]` (10 KB) when `tile=1`.
- Second filter to adopt the ADR-0134 tiling idiom; the `aa` ALU-bound refutation
  + this fetch-bound confirmation map which kernels the pattern fits.

## References

- ADR-0134 (the denoise tiling idiom this clones + the +65% precedent),
  ADR-0123 (the dehalo filter), ADR-0125 (the `tune=anime` consumer).
- The aa-tiling premise-refutation (ALU-bound) — the negative-space twin
  (`.workingdir/OPPORTUNITIES.md`).
