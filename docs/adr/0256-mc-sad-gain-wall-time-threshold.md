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

Run `ffmpeg-patches/test/mc-gain-smoke.py` after the documented-recipes smoke in `build-and-run.sh`. The default workload is 64 frames of `testsrc2=size=1280x720:rate=30`, uploaded as `yuv420p`, optionally processed by `pelorus_mc_vulkan=bsize=B`, downloaded, and sent to the null output. Use FFmpeg `-benchmark` rtime. Each enumerated device must match the verbose FFmpeg name and PCI identifier in `mc-gain-record.json`. Every arm run must select the same device as its enumeration run. The runner requires an explicit FFmpeg end marker: `Unable to find device with index N!`, or `No devices found` at index zero. Missing device output, failed or timed-out probes, and reaching the eight-index cap without an end marker are errors; the cap cannot silently omit a recorded device. The GPU-free enumeration cases in `mc-gain-smoke.py --self-test` cover these shapes.

For each guarded device, discard one warm-up per arm, then collect 15 interleaved rounds (configurable from 5 to 50), alternating forward and reversed arm order. Let `p`, `m8`, and `m32` be the passthrough, bsize=8, and bsize=32 medians. Define `net8 = m8 - p`, `net32 = m32 - p`, and `R = net8 / net32`. Non-positive net times are inconclusive. Host speed scales numerator and denominator, cancelling to first order; this is not an absolute time budget.

Use `sigma = 1.4826 × MAD` and `se(median) = 1.2533 × sigma / sqrt(n)`. Use the first-order delta method: `se_R = sqrt(se8² + R² × se32² + (R - 1)² × sep²) / net32`. Both net times share the same passthrough median, so its contribution has derivative `(R - 1)/net32` and cancels at `R = 1`; treating the net times as independent overstates uncertainty. The `ratio-error-exact` and `ratio-unity` cases in `mc-gain-smoke.py --self-test` pin this propagation. The record contains separated subgroup and tree ratios for NVIDIA GeForce RTX 4090 (`0x2684`, NVIDIA 620.70.06, 3.115 / 5.810) and AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO) (`0x13c0`, radv Mesa 26.2.6-arch3.1, 11.432 / 12.736), measured on 2026-11-12 with 15 rounds using `--make-record --tree-ffmpeg BIN`. See the [measurement digest](../research/0459-mc-gain-threshold.md).

Apply the rules in this order, with `mid = (ratio_subgroup + ratio_tree)/2` and `gap = ratio_tree - ratio_subgroup`:

1. `R < ratio_subgroup - gap`: inconclusive stale record; re-record after a workload or driver change. A uniformly faster subgroup and tree must not make a reverted reduction pass.
2. `abs(R - mid) < 2 × se_R`: inconclusive noise; rerun on an idle device.
3. `R <= mid`: pass, including equality with zero uncertainty. Otherwise fail, naming the record and the possible lost subgroup gain or changed driver.

Any failure returns 1, even alongside passes. A pass with no inconclusive guarded devices returns 0; all other results return 77. Unguarded and unrecorded devices print `SKIP: no guarded device`; an inconclusive guarded device prints `SKIP: inconclusive` last, so `MC_GAIN_SKIP="no guarded device"` cannot hide it. Missing or unresolvable `FFMPEG_BIN` also returns 77. Invalid records and failed measurements return 1. CI names only the absence of a guarded device.

Keep Arc A380 (`0x56a5`, ANV) in the explicit unguarded list with issue #602: its subgroup reduction is about 85 % slower than the tree. The default check enumerates it but never measures it. `--make-record --tree-ffmpeg BIN` interleaves five arms across the two builds and emits JSON device entries in record key order with three-decimal ratios only when `ratio_tree - ratio_subgroup > 2 × sqrt(se_sub² + se_tree²)`. Optional `vulkaninfo --summary` supplies driver metadata keyed by device name and lower-case PCI identifier, preserving different ICD device names with the same PCI ID; absent metadata is `unknown`. Before timing a guarded device, a known current driver different from its recorded driver produces `SKIP: inconclusive` with a stale-record diagnostic and a request to re-record. Unknown current metadata permits measurement. The driver cases in `mc-gain-smoke.py --self-test` verify both paths without starting `vulkaninfo`. `--tree-shader IN OUT` generates the barrier-tree comparison from the canonical source and verifies that the subgroup structural guard rejects it. The output must be a distinct file: the runner refuses identical paths, symlink aliases and hard links before writing; the alias cases verify unchanged source bytes. On a Windows host that cannot create symbolic or hard links, those two alias cases print a named `skip` line instead of failing; a link failure on any other host still fails the case.

Each guarded entry in `mc-gain-record.json` requires `workload_sha256`, the first 16 hexadecimal digits of SHA-256 over the workload's sorted, compact JSON representation. Check mode rejects an entry bound to another workload. `--make-record` validates the same schema, workload bounds and device entries, including duplicate-device refusal, while allowing old workload hashes so a changed workload can be measured. Replace matching device entries with the emitted lines; after a workload change, replace the whole `devices` list because old entries are bound to the old workload and merging would retain stale entries or duplicate devices. The workload requires exactly bsize=8 and bsize=32 because the metric and arm labels describe net8/net32; size, rate and frame-count bounds still apply. The shipped entries retain their measured workload and ratios. Record generation includes the fingerprint, parses each three-decimal JSON line back through the shared field and entry validators, and refuses rounding that collapses the gain. `--make-record` returns 1 with `no device entries emitted` if no line survives. The workload and record cases in `mc-gain-smoke.py --self-test` verify these constraints.

The `mc-gain` test in `meson.build` runs serially because its GPU wall-time arms must not share the device with other Meson tests, including `doc-recipes`. Independent external GPU load still requires an idle-device rerun.

A formatter suppression comment lets a file pack statements onto long lines and pass a file-length cap that the formatted code would exceed. Refuse formatter suppression comments in tracked Python through the [ADR-0241](0241-suppression-marker-binding.md) checker, `scripts/check-suppression-markers.py`: `fmt: skip/off/on`, `yapf: disable/enable`, and `autopep8: off/on`, case-insensitively with spaces around the colon allowed. Tokenized comments are findings; marker text in string literals remains text. Split files to meet the cap while retaining normal formatting. The checker also verifies that every module-level `_self_test` helper is reachable from `self_test` through direct function calls, using a worklist bounded by the function count. The `*_self_test` helpers imported from `scripts/suppression_selftests.py` also count as checker helpers and must be reachable from `self_test`. Its planted disconnected and indirect-call fixtures protect this wiring, including the formatter cases and encoding helpers (`python3 -I scripts/check-suppression-markers.py --self-test`).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Absolute net wall time against a midpoint | Simple | Host speed moves it past the margin | The RADV margin is 9 % of an absolute time that moves with host and device speed |
| Ruler-filter normalisation | Removes some host variation | Depends on another filter's performance | Couples the guard to an unrelated filter's speed |
| Runtime reduction-switch AVOption | One build measures both variants | Adds a public option and another runtime path | Creates a product surface for a test |
| CI GPU runner | Continuous hardware measurements | No GPU runner exists | Cannot supply the current replay gate |
| Re-run the tree variant in every replay | Fresh direct comparison | Requires a second FFmpeg build per replay | Record the comparison once per device and driver instead |

## Consequences

- **Positive**: replay checks both reduction shape and measured gain on recorded devices; robust statistics, bounded runs, explicit device checks and named skips make failures reviewable. The self-tests in `mc-gain-self-test.py` and `mc-gain-self-test-review.py` and the FFmpeg stub in `mc-gain-stub-ffmpeg.py` protect decision boundaries without requiring a GPU; `mc-gain-smoke.py --self-test` loads them on demand.
- **Negative**: records cover individual device names and PCI identifiers and must be regenerated after driver changes. Noise or non-positive net times fail developer replays unless their inconclusive reason is explicitly named. Arc A380 remains unguarded until #602 is resolved; this does not replace #167.
- **Neutral / follow-ups**: `--tree-shader` and `--make-record` reproduce the record without a runtime reduction switch. Driver metadata is best-effort. The record contains the dated device measurements reproduced by the commands in the measurement digest.

## References

- Issue #459, issue #602, PR #27, PR #548.
- [Research digest: mc subgroup gain threshold](../research/0459-mc-gain-threshold.md), measurements dated 2026-11-12.
- [Named smoke skips, ADR-0189](0189-named-nvenc-smoke-skip.md).
- `ffmpeg-patches/test/mc-gain-smoke.py`, `ffmpeg-patches/test/mc-gain-record.json`, `scripts/check-mc-sad-reduction.py`.
- FFmpeg n9.0.2 `libavutil/hwcontext_vulkan.c`: verbose selected-device log and missing-index diagnostic; pinned by `build-config.env`.
