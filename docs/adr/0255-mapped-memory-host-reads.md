<!-- markdownlint-disable MD013 MD060 -->
# ADR-0255: Copy mapped device memory before element-wise host reads

- **Status**: Accepted
- **Date**: 2026-11-11
- **Deciders**: Lusoris
- **Tags**: vulkan, performance, zero-copy

## Context

The [motion-estimation wall-time measurements](../metrics/mc.md) attribute almost all of the old host cost to two operations: the O(n²) p95 scan and element-wise reads of mapped GPU buffers. PR #545 guards the p95 half with `ffmpeg-patches/test/mc_stats_test.c`; it cannot detect a caller passing mapped memory directly to `attach_motion()` again. The current `mc_snapshot()` in `ffmpeg-patches/files/vf_pelorus_mc_vulkan.c` copies each mapped span once before host processing. Issue #458 needs that second half held too.

The existing [device-local allocation rule](0222-device-local-storage-buffers.md) already scans the relevant C files, headers and added side of hand-maintained patches. The same scan can reject direct mapped reads without a GPU runner.

## Decision

Extend `scripts/check-vulkan-buffer-domain.py`, fast test `vulkan-buffer-domain`, to classify every `mapped_mem` token after comment stripping. Permit complete pointer expressions only in `memcpy` arguments 0 and 1, `memset` argument 0, and the documented write-only argument 3 of `pel_mc_build_predictors` (`pelorus_mc_stats.h:66–84`) and `pelorus_qpmap_raster` (`vulkan-pelorus-qpmap.patch:1313–1345`); indices are zero-based. Permit plain cast stores and non-const local aliases only when every later use in the enclosing block is a plain indexed store. Refuse other uses, including compound assignments, and require one bulk `memcpy` before element-wise host reads. Keep three exact-file, exact-statement exceptions for issue #596, entered 2026-11-11 under MEL category D (120 days):

| File under `ffmpeg-patches/files/` | Exact statement after whitespace collapse | Reason |
| --- | --- | --- |
| `vf_pelorus_analyze_vulkan.c` | `base = (const uint32_t *)buf_vk->mapped_mem;` | Per-tile reads scale with resolution |
| `vf_pelorus_denoise_vulkan.c` | `*acc_out = (const PelorusDenoiseBuf *)buf_vk->mapped_mem;` | Fixed 160-word accumulator |
| `vf_pelorus_grain_estimate_vulkan.c` | `if (err >= 0) err = attach_estimate(s, in, (const PelorusGrainBuf *)buf_vk->mapped_mem);` | Fixed statistics struct |

Each exception must cover exactly one refused site. Zero matches, duplicate matches, unknown MEL categories and dates after 2027-03-11 fail the check; 2027-03-11 remains allowed. The pure `check_known()` receives its date explicitly; the CLI supplies today's date. An empty mapped-use scan also fails. `--files DIR` selects a source copy for both allocation and mapped-read checks; its default remains `ffmpeg-patches/files`.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Runtime timing test on a GPU | Needs a GPU runner; GPU wall-time regression coverage remains in #167 |
| clang-tidy or Semgrep rule | No project rule engine for this C flow; the existing regex scan already owns these files |
| Review only | Does not mechanically prevent the direct mapped argument from returning |

## Consequences

- **Positive**: The fast test rejects new direct mapped readers while preserving bulk copy-out, uploads and documented write-only sinks. Its self-tests plant readers, alias escapes, read-modify-write operations and exception failures.
- **Negative**: The lexical scan fails closed on `mapped_mem + off`, even when a particular offset could be safe. Pointer provenance from `vk->MapMemory` and wrapper implementations is not followed; the rule is not a general C data-flow analysis.
- **Neutral / follow-ups**: Issue #596 replaces the three known reads with copies before their exceptions expire. GPU wall time remains with #167. The patch stack and runtime code remain unchanged.

## References

- Issue [#458](https://github.com/VMAFx/pelorus/issues/458): motion-estimation speedup regression threshold.
- Issue [#596](https://github.com/VMAFx/pelorus/issues/596): three known mapped reads.
- [ADR-0222](0222-device-local-storage-buffers.md): device-local storage buffers.
- [ADR-0116](0116-pelorus-mc.md): motion-vector hints.
- [PR #545](https://github.com/VMAFx/pelorus/pull/545): host p95 regression threshold.
