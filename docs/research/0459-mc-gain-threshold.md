<!-- markdownlint-disable MD013 -->
# mc subgroup SAD gain threshold

**Date:** 2026-11-12

**Decision:** [ADR-0256](../adr/0256-mc-sad-gain-wall-time-threshold.md).

**Scope:** issue #459; the `block_sad` subgroup reduction from PR #27 / `6bedd01`, its static shape guard (#548), and the hardware replay. Measurements below were supplied by the dispatching session on 2026-11-12; this implementation lane runs only the stub self-test.

## Verdict

Use the same binary's net bsize=8 / net bsize=32 wall-time ratio and compare it with the midpoint of the recorded subgroup and tree ratios. Record RTX 4090 and RADV RAPHAEL_MENDOCINO. Keep Arc A380 explicitly unguarded under #602: the subgroup reduction is slower there.

## Method

The canonical shader fixes the workgroup at 32×32 (`PEL_MC_BLOCK_DIM`, specialization IDs 253/254). `bsize` only gates fetching: bsize=8 has 64 active fetch lanes, a 1024-lane reduction, and 16 times as many blocks as bsize=32. The small block is reduction-bound; bsize=32 is fetch-bound.

Use FFmpeg n9.0.2 and the same device for all arms. Generate 64 frames with `-f lavfi -i testsrc2=size=1280x720:rate=30 -frames:v 64`. The chain is `format=yuv420p,hwupload[,pelorus_mc_vulkan=bsize=B],hwdownload,format=yuv420p`, output `-f null -`, and the wall time is `rtime` from `-benchmark` (`bench: utime=Xs stime=Ys rtime=Zs`). Subtract the passthrough median separately from each mc median.

To reproduce the comparison, copy the patched FFmpeg tree, then replace only its mc reduction with the generated tree shader and rebuild that copy:

```bash
python3 -I ffmpeg-patches/test/mc-gain-smoke.py --tree-shader \
  ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl \
  /absolute/path/to/ffmpeg-tree/libavfilter/vulkan/pelorus_mc.comp.glsl
FFMPEG_BIN=/absolute/path/to/subgroup/ffmpeg \
  python3 -I ffmpeg-patches/test/mc-gain-smoke.py --make-record \
  --tree-ffmpeg /absolute/path/to/ffmpeg-tree/ffmpeg --runs 11
```

The record mode interleaves passthrough, sub8, sub32, tree8 and tree32 for each enumerated device. Discard one warm-up per arm; alternate forward and reversed order for measured rounds. The default is 15 rounds, bounded to 5–50. Output is one JSON device entry per separated device; merge those entries into `mc-gain-record.json` while retaining the workload and unguarded list. Optional `vulkaninfo --summary` provides driver metadata with a 60 s deadline; otherwise it is `unknown`.

## Measurements

The initial absolute-time experiment used bsize=16. These are net wall times in seconds, subgroup versus tree:

| Device | Driver | Subgroup net s | Tree net s |
| --- | --- | --- | --- |
| RTX 4090, `0x2684` | NVIDIA 620.70.06 | 0.154 | 0.179 |
| AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO), `0x13c0` | Mesa 26.2.6 | 0.960 | 1.056 |
| Intel(R) Arc(tm) A380 Graphics (DG2), `0x56a5` | Mesa 26.2.6 ANV | 2.094 | 1.120 |

The ratio experiment used the workload above:

| Device | Runs | Subgroup R | SE | Tree R | SE |
| --- | --- | --- | --- | --- | --- |
| RTX 4090, `0x2684` | 11 | 3.339 | 0.202 | 5.526 | 0.238 |
| RADV RAPHAEL_MENDOCINO, `0x13c0` | 11 | 11.395 | 0.218 | 12.679 | 0.215 |
| RADV RAPHAEL_MENDOCINO, second session | 7 | 11.219 | not supplied | 12.622 | not supplied |

The subgroup and tree outputs were equal (SSIM 1.000000); this comparison concerns performance only.

## Why the ratio

An absolute-time midpoint has only a 9 % margin on RADV and flips with host load. Host speed scales both net arms to first order. The fetch-bound bsize=32 arm is approximately equal across variants: RADV net 0.306 versus 0.312 s; RTX 4090 net 0.124 versus 0.133 s. Dividing the reduction-bound arm by that reference within the same binary separates the reductions without depending on another filter's speed.

## Decision rule

For medians `p`, `m8`, `m32`, define `net8 = m8 - p`, `net32 = m32 - p`, `R = net8/net32`. Either net time at or below zero is inconclusive. For each arm use `sigma = 1.4826 × MAD`, `se(median) = 1.2533 × sigma/sqrt(n)` and propagate `se_R = R × sqrt((se8² + sep²)/net8² + (se32² + sep²)/net32²)`.

Let `mid = (ratio_subgroup + ratio_tree)/2` and `gap = ratio_tree - ratio_subgroup`. First reject `R < ratio_subgroup - gap` as stale-low; then reject `abs(R - mid) < 2 × se_R` as noisy. Otherwise `R <= mid` passes and larger ratios fail. New record entries require a separation exceeding `2 × sqrt(se_sub² + se_tree²)`.

A failure dominates all passes (exit 1). Passes with no inconclusive guarded device return 0. Other outcomes return 77. Inconclusive skip lines appear last, preventing the CI allowance `MC_GAIN_SKIP="no guarded device"` from masking a noisy guarded-device result.

## Arc A380 inversion

On Mesa 26.2.6 ANV, the Arc A380 subgroup reduction is about 85 % slower than the barrier tree. Issue #602 tracks this inversion. Its record entry is explicitly unguarded and names that issue; default checks enumerate the device but do not time it. Record generation can measure it and must refuse an entry when there is no separated gain.

## Limits

Records apply to a specific FFmpeg device name and PCI identifier. A driver change requires re-recording; stale-low catches ratios below the recorded range, including uniformly faster subgroup and tree implementations that might otherwise allow a reverted reduction to pass. Concurrent load may still cause inconclusive or failed results: rerun on an idle device. No GPU was exercised in this implementation lane. These measurements do not replace the broader GPU performance work in #167.

## Sources

- Dispatching-session measurements and FFmpeg n9.0.2 log strings supplied on 2026-11-12; baseline `origin/master` `5f2adde`, ADR skeleton `b13b7de`.
- `ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl`, `scripts/check-mc-sad-reduction.py` (#548), `ffmpeg-patches/test/mc-gain-smoke.py --self-test`.
- [Bench results v0.19](../development/bench-results.md), [ADR-0189](../adr/0189-named-nvenc-smoke-skip.md), [ADR-0256](../adr/0256-mc-sad-gain-wall-time-threshold.md).
