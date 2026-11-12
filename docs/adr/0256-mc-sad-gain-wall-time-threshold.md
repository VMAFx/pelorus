<!-- markdownlint-disable MD013 MD060 -->
# ADR-0256: Guard the mc subgroup SAD gain with an in-binary wall-time ratio

- **Status**: Accepted
- **Date**: 2026-11-12
- **Deciders**: Lusoris
- **Tags**: vulkan, performance, testing

## Context

PR #27 (commit 6bedd01) replaced the barrier-tree reduction in `block_sad` of `ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl` with `subgroupAdd` plus a lane-0 combine ([bench results v0.19](../development/bench-results.md)). The gain has a static guard (`scripts/check-mc-sad-reduction.py`, PR #548), which holds the shape of the reduction but not its speed. Issue #459 asks for a wall-time threshold.

CI has no GPU runner. The hardware replay already requires explicitly named skips under [ADR-0189](0189-named-nvenc-smoke-skip.md); it can run a device-specific performance check without implying hardware coverage on CI. The workgroup is always 32×32 (`PEL_MC_BLOCK_DIM`, specialization IDs 253/254); `bsize` gates fetch lanes, not reduction lanes. At `bsize=8`, only 64 of 1024 lanes fetch and the grid has 16 times as many blocks as at `bsize=32`, making the former reduction-bound and the latter fetch-bound.

## Decision

Run `ffmpeg-patches/test/mc-gain-smoke.py` after the documented-recipes smoke in `build-and-run.sh`. The default workload is 64 frames of `testsrc2=size=1280x720:rate=30`, uploaded as `yuv420p`, optionally processed by `pelorus_mc_vulkan=bsize=B`, downloaded, and sent to the null output. Use FFmpeg `-benchmark` rtime. Each enumerated device must match the verbose FFmpeg name and PCI identifier in `mc-gain-record.json`. Every arm run must select the same device as its enumeration run.

For each guarded device, discard one warm-up per arm, then collect 15 interleaved rounds (configurable from 5 to 50), alternating forward and reversed arm order. Let `p`, `m8`, and `m32` be the passthrough, bsize=8, and bsize=32 medians. Define `net8 = m8 - p`, `net32 = m32 - p`, and `R = net8 / net32`. Non-positive net times are inconclusive. Host speed scales numerator and denominator, cancelling to first order; this is not an absolute time budget.

Use `sigma = 1.4826 × MAD` and `se(median) = 1.2533 × sigma / sqrt(n)`. Propagate uncertainty as `se_R = R × sqrt((se8² + sep²)/net8² + (se32² + sep²)/net32²)`. The record contains separated subgroup and tree ratios for RTX 4090 (`0x2684`, 3.339 / 5.526) and RADV RAPHAEL_MENDOCINO (`0x13c0`, 11.395 / 12.679), with driver, date and run count. See the [measurement digest](../research/0459-mc-gain-threshold.md).

Apply the rules in this order, with `mid = (ratio_subgroup + ratio_tree)/2` and `gap = ratio_tree - ratio_subgroup`:

1. `R < ratio_subgroup - gap`: inconclusive stale record; re-record after a workload or driver change. A uniformly faster subgroup and tree must not make a reverted reduction pass.
2. `abs(R - mid) < 2 × se_R`: inconclusive noise; rerun on an idle device.
3. `R <= mid`: pass, including equality with zero uncertainty. Otherwise fail, naming the record and the possible lost subgroup gain or changed driver.

Any failure returns 1, even alongside passes. A pass with no inconclusive guarded devices returns 0; all other results return 77. Unguarded and unrecorded devices print `SKIP: no guarded device`; an inconclusive guarded device prints `SKIP: inconclusive` last, so `MC_GAIN_SKIP="no guarded device"` cannot hide it. Missing or unresolvable `FFMPEG_BIN` also returns 77. Invalid records and failed measurements return 1. CI names only the absence of a guarded device.

Keep Arc A380 (`0x56a5`, ANV) in the explicit unguarded list with issue #602: its subgroup reduction is about 85 % slower than the tree. The default check enumerates it but never measures it. `--make-record --tree-ffmpeg BIN` interleaves five arms across the two builds and emits JSON device entries only when `ratio_tree - ratio_subgroup > 2 × sqrt(se_sub² + se_tree²)`. Optional `vulkaninfo --summary` supplies driver metadata; absent metadata is `unknown`. `--tree-shader IN OUT` generates the barrier-tree comparison from the canonical source and verifies that the subgroup structural guard rejects it.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Absolute net wall time against a midpoint | Simple | Host speed moves it past the margin | Host load flips the 9 % RADV margin |
| Ruler-filter normalisation | Removes some host variation | Depends on another filter's performance | Couples the guard to an unrelated filter's speed |
| Runtime reduction-switch AVOption | One build measures both variants | Adds a public option and another runtime path | Creates a product surface for a test |
| CI GPU runner | Continuous hardware measurements | No GPU runner exists | Cannot supply the current replay gate |
| Re-run the tree variant in every replay | Fresh direct comparison | Requires a second FFmpeg build per replay | Record the comparison once per device and driver instead |

## Consequences

- **Positive**: replay checks both reduction shape and measured gain on recorded devices; robust statistics, bounded runs, explicit device checks and named skips make failures reviewable. Stub self-tests and planted mutants protect decision boundaries without requiring a GPU.
- **Negative**: records cover individual device names and PCI identifiers and must be regenerated after driver changes. Noise or non-positive net times fail developer replays unless their inconclusive reason is explicitly named. Arc A380 remains unguarded until #602 is resolved; this does not replace #167.
- **Neutral / follow-ups**: `--tree-shader` and `--make-record` reproduce the record without a runtime reduction switch. Driver metadata is best-effort. The dispatching session validates real hardware and regenerates measured entries.

## References

- Issue #459, issue #602, PR #27, PR #548.
- [Research digest: mc subgroup gain threshold](../research/0459-mc-gain-threshold.md), measurements supplied on 2026-11-12.
- [Named smoke skips, ADR-0189](0189-named-nvenc-smoke-skip.md).
- `ffmpeg-patches/test/mc-gain-smoke.py`, `ffmpeg-patches/test/mc-gain-record.json`, `scripts/check-mc-sad-reduction.py`.
- FFmpeg n9.0.2 `libavutil/hwcontext_vulkan.c`: verbose selected-device log and missing-index diagnostic; pinned by `build-config.env`.
