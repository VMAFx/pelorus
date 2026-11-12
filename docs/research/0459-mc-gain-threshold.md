<!-- markdownlint-disable MD013 -->
# mc subgroup SAD gain threshold

**Date:** 2026-11-12

**Decision:** [ADR-0256](../adr/0256-mc-sad-gain-wall-time-threshold.md).

**Scope:** issue #459; the `block_sad` subgroup reduction from PR #27 / `6bedd01`, its static shape guard (#548), and the hardware replay. The device measurements below are dated 2026-11-12 and use FFmpeg n9.0.2.

## Verdict

Use the same binary's net bsize=8 / net bsize=32 wall-time ratio and compare it with the midpoint of the recorded subgroup and tree ratios. Record NVIDIA GeForce RTX 4090 and RADV RAPHAEL_MENDOCINO. Keep Arc A380 explicitly unguarded under #602: the subgroup reduction is slower there.

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
  --tree-ffmpeg /absolute/path/to/ffmpeg-tree/ffmpeg
```

The record mode interleaves passthrough, sub8, sub32, tree8 and tree32 for each enumerated device. Discard one warm-up per arm; alternate forward and reversed order for measured rounds. The default is 15 rounds, bounded to 5–50. Output is one JSON device entry per separated device whose three-decimal line passes the shared record validators; rounding that collapses the ratio gap emits no line, and no emitted entries makes the command return 1. Each entry includes `workload_sha256`, the first 16 hexadecimal digits of SHA-256 over the sorted, compact JSON workload; the runner rejects another workload and requires exactly bsize=8/32. Merge those entries into `mc-gain-record.json` while retaining the workload and unguarded list. Optional `vulkaninfo --summary` provides driver metadata with a 60 s deadline; otherwise it is `unknown`. Metadata is keyed by device name and lower-case PCI ID. The runner stops only at FFmpeg's explicit missing-index marker, or `No devices found` at index zero; other missing device lines and exhaustion of the eight-index cap are errors (`enumerate_devices`, `mc-gain-smoke.py --self-test`). Shader generation refuses output that aliases its input, including symlinks and hard links (the alias self-test cases).

## Measurements

A separate interleaved A/B run on 2026-11-12 measured absolute wall times at bsize=16 and bsize=32. The bsize=16 net wall times in seconds are subgroup versus tree:

| Device | Driver | Subgroup net s | Tree net s |
| --- | --- | --- | --- |
| NVIDIA GeForce RTX 4090, `0x2684` | NVIDIA 620.70.06 | 0.154 | 0.179 |
| AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO), `0x13c0` | radv Mesa 26.2.6-arch3.1 | 0.960 | 1.056 |
| Intel(R) Arc(tm) A380 Graphics (DG2), `0x56a5` | Mesa 26.2.6 ANV | 2.094 | 1.120 |

The 2026-11-12 `--make-record --tree-ffmpeg` run used the workload above with the default 15 rounds. Its three-decimal record values are:

| Device | Driver | Runs | Subgroup R | Tree R |
| --- | --- | --- | --- | --- |
| NVIDIA GeForce RTX 4090, `0x2684` | NVIDIA 620.70.06 | 15 | 3.115 | 5.810 |
| AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO), `0x13c0` | radv Mesa 26.2.6-arch3.1 | 15 | 11.432 | 12.736 |

The subgroup and tree outputs were equal (SSIM 1.000000); this comparison concerns performance only.

## Why the ratio

An absolute-time midpoint has only a 9 % margin on RADV, and absolute times move with host and device speed. Host speed scales both net arms to first order. The separate interleaved A/B run on 2026-11-12 measured approximately equal net bsize=32 times across variants:

| Device | Driver | Subgroup net s | Tree net s |
| --- | --- | --- | --- |
| NVIDIA GeForce RTX 4090, `0x2684` | NVIDIA 620.70.06 | 0.124 | 0.133 |
| AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO), `0x13c0` | radv Mesa 26.2.6-arch3.1 | 0.306 | 0.312 |

Dividing the reduction-bound arm by that reference within the same binary separates the reductions without depending on another filter's speed.

## Decision rule

For medians `p`, `m8`, `m32`, define `net8 = m8 - p`, `net32 = m32 - p`, `R = net8/net32`. Either net time at or below zero is inconclusive. For each arm use `sigma = 1.4826 × MAD`, `se(median) = 1.2533 × sigma/sqrt(n)` and propagate `se_R = sqrt(se8² + R² × se32² + (R - 1)² × sep²) / net32`.

The shared passthrough median correlates the two net times; its derivative is `(R - 1)/net32`, so its uncertainty cancels at `R = 1`. The `ratio-error-exact` and `ratio-unity` self-test cases verify this formula.

Let `mid = (ratio_subgroup + ratio_tree)/2` and `gap = ratio_tree - ratio_subgroup`. First reject `R < ratio_subgroup - gap` as stale-low; then reject `abs(R - mid) < 2 × se_R` as noisy. Otherwise `R <= mid` passes and larger ratios fail. New record entries require a separation exceeding `2 × sqrt(se_sub² + se_tree²)`.

A failure dominates all passes (exit 1). Passes with no inconclusive guarded device return 0. Other outcomes return 77. Inconclusive skip lines appear last, preventing the CI allowance `MC_GAIN_SKIP="no guarded device"` from masking a noisy guarded-device result.

## Arc A380 inversion

On Mesa 26.2.6 ANV, the Arc A380 subgroup reduction is about 85 % slower than the barrier tree. Issue #602 tracks this inversion. Its record entry is explicitly unguarded and names that issue; default checks enumerate the device but do not time it. Record generation can measure it and must refuse an entry when there is no separated gain.

## Limits

Records apply to a specific FFmpeg device name and PCI identifier. A known current driver different from the recorded driver skips timing with `SKIP: inconclusive` and a stale-record diagnostic; unknown current metadata permits measurement (`check`, driver self-test cases). A driver change requires re-recording; stale-low catches ratios below the recorded range, including uniformly faster subgroup and tree implementations that might otherwise allow a reverted reduction to pass. The Meson `mc-gain` test runs serially to avoid sharing the GPU with other tests (`meson.build`). Concurrent external load may still cause inconclusive or failed results: rerun on an idle device. These measurements do not replace the broader GPU performance work in #167.

## Sources

- Measurements dated 2026-11-12 from the commands above and FFmpeg n9.0.2 log strings; baseline `origin/master` `5f2adde`.
- `ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl`, `scripts/check-mc-sad-reduction.py` (#548), `ffmpeg-patches/test/mc-gain-smoke.py --self-test`.
- [Bench results v0.19](../development/bench-results.md), [ADR-0189](../adr/0189-named-nvenc-smoke-skip.md), [ADR-0256](../adr/0256-mc-sad-gain-wall-time-threshold.md).
