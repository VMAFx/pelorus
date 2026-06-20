<!-- markdownlint-disable MD013 -->
# ADR-0134: Shared-memory tiling of the denoise NLM spatial window (opt-in)

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

`vf_pelorus_denoise_vulkan`'s spatial term is an NLM-lite joint bilateral: for
each pixel it scans a `(2·patchR+1)²` search window (patchR ≤ 3 → up to 7×7) and,
per search offset, computes a 3×3-patch SSD. Every SSD reads 9 current-frame
samples for the centre patch and 9 for the neighbour patch, so a single output
pixel issues on the order of **441 image loads** at patchR=3 — all drawn from an
overlapping `(2·patchR+3)² ≈ 81`-pixel window. The window is read ~9× over.

A prior experiment (the reverted fp16 attempt, see the roadmap note) established
the kernel is **fetch/memory-bound, not ALU-bound**: replacing the SSD math with
fp16 changed throughput by < 0 on all three dev GPUs. The bottleneck is the
redundant global image traffic, not arithmetic.

## Decision

Add an opt-in `tile` AVOption (default **0**). When `tile=1`, each workgroup
cooperatively loads its `16×16` output region plus a `PEL_HALO`-pixel ring
(`PEL_HALO = max patchR (3) + the 1-px patch ring = 4`, so a 24×24 tile) of the
current plane into a `shared float s_tile[]` once per plane, then every spatial
read (`PEL_SPATIAL(off)`) hits shared memory instead of the image. The temporal
taps (previous frames, motion-compensated fetch) are unchanged — they are few and
scattered, not the redundant hot path.

The cooperative load runs in **uniform control flow** (before the per-thread
bounds-cull / `IS_WITHIN` guard) so its `barrier()`s are workgroup-uniform; a
leading barrier protects the prior plane's readers before the next plane
overwrites `s_tile`. Output is **bit-identical** to the direct-fetch path (same
clamped samples, same math, same order) — verified at SSIM 1.000000.

Default is **0 (off)** — flagship-first. The win is GPU-dependent (below), and
the project's primary target (RTX 4090 / NVENC) is the case that does *not*
benefit; `tile=1` is opt-in for the GPUs that do.

## Alternatives considered

- **fp16 inner loop.** Rejected — measured perf-neutral-to-negative (the kernel
  is memory-bound, and scalar `float16_t` isn't packed `f16vec` so it gets no
  2:1 rate; the f32↔f16 casts on the f32 image fetches are pure overhead, −12%
  on AMD). Wrong tool for a fetch-bound kernel.
- **Register-cache only the (invariant) centre patch.** Cuts ~half the loads but
  a dynamically-indexed `float[9]` risks spilling to local memory; shared-memory
  tiling captures both the centre and the overlapping neighbour reads.
- **Default tile=1 (or unconditional).** Rejected — regresses the flagship 4090
  ~1–4% (its 72 MB L2 already caches the window, so tiling only adds the
  barrier + shared-load overhead). The owner chose flagship-first; opt-in keeps
  the default path untouched while exposing the large win to GPUs that need it.

## Consequences

- Bit-identical, so safe to enable anywhere; purely a throughput knob.
- One `shared float[576]` (2.3 KB) when `tile=1`; a small occupancy cost that is
  exactly why cache-rich GPUs see no gain.
- The standalone `pelorus_denoise.comp` reference now models (and CI
  compile-checks) the tiled path; the direct-fetch default is the trivial
  `PEL_SPATIAL(o) = pel_cur(...)` expansion.

## Validation

Measured on the 3-GPU dev box (8× chained denoise, patch=3, 1080p, warm; output
SSIM vs the direct path = 1.000000 at patchR 1/2/3 and 8/10-bit):

| GPU | direct (tile=0) | tiled (tile=1) | Δ |
|---|---|---|---|
| Intel Arc A380 | 11.48 s | 3.94 s | **+65.7 % (2.9×)** |
| AMD RADV (iGPU) | 7.91 s | 7.94 s | −0.3 % (noise) |
| NVIDIA RTX 4090 | 1.19 s | 1.20 s | −1 to −4 % |

The win tracks memory-bandwidth pressure: huge on the bandwidth-limited Arc,
negligible where a large L2 already absorbs the redundant fetches.

## References

- ADR-0107/0113 (the denoise filter + the MC→denoise warp it tiles around).
- The reverted fp16 finding (`.workingdir/OPPORTUNITIES.md`) — the evidence the
  kernel is memory-bound, which motivated tiling over ALU-precision changes.
- `docs/metrics/denoise.md`; `docs/development/bench-results.md`.
