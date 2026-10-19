<!-- markdownlint-disable MD013 MD060 -->
# ADR-0221: Pelorus output pools carry their export facts in FFmpeg's frames and device contexts

- **Status**: Accepted
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: vulkan, zero-copy, interop, vmafx, ffmpeg

## Context

VMAFx copies every Vulkan frame once on the GPU because it cannot tell
whether an FFmpeg pool can be exported (issue
[#104](https://github.com/VMAFx/pelorus/issues/104), zero-copy gap ZC-G10 in
[research 0172](../research/0172-zero-copy-audit.md)). VMAFx imports one
exportable image per plane with `OPAQUE_FD` memory (VMAFx/vmafx#2375, merged
as `db110bd6ffd0`).

The facts a consumer needs already exist in FFmpeg's public API at `n9.0.2`:
`AVVulkanFramesContext.tiling` and `format[]`, `AVVkFrame.mem[]`, and
`AVVulkanDeviceContext.enabled_dev_extensions`. The image count comes from
`format[]` only: the device option `disable_multiplane=1` splits the planes
without setting `AV_VK_FRAME_FLAG_DISABLE_MULTIPLANE` in
`AVVulkanFramesContext.flags` (`libavutil/hwcontext_vulkan.c` line 2956; the
first replay of the test measured 2 images with the flag clear). `AVVulkanFramesContext` has no
handle-type field. FFmpeg derives the handle type when it creates the pool:
`OPAQUE_FD` for non-DRM tiling with `VK_KHR_external_memory_fd`, `DMA_BUF` for
`DRM_FORMAT_MODIFIER_EXT` with `VK_EXT_external_memory_dma_buf`, `OPAQUE_WIN32`
on Windows (`vulkan_pool_alloc()`, `libavutil/hwcontext_vulkan.c` lines
2855-2871), and only when the driver accepts that type for the format
(`try_export_flags()`, line 2766).

That query is wrong for the default Pelorus pool. For non-DRM tiling it passes
`VK_IMAGE_CREATE_ALIAS_BIT` as the only create flag, while a multi-plane
storage image is created with `MUTABLE_FORMAT | EXTENDED_USAGE | ALIAS`. On an
RTX 4090, an Arc A380 and a Radeon iGPU the driver refuses the `ALIAS`-only
query for `G8_B8R8_2PLANE_420_UNORM` with storage usage and accepts it with
the real flags, so an `OPTIMAL` NV12 pool gets no export handle type. Calling
`vkGetMemoryFdKHR` on that memory is invalid usage: the 4090 returned a file
descriptor, the A380 and the iGPU returned errors. DRM pools and split-plane
pools export on all three. Upstream FFmpeg fixed the query on master in
`63d0237033` (2026-08-07); no release tag carries it
([research 0104](../research/0104-vulkan-export-contract.md)).

The Pelorus pools come from `ffmpeg-patches/files/pelorus_vulkan_pool.h`
([ADR-0184](0184-vulkan-drm-modifier-pools.md)), which uses the stock
allocator for both tilings.

## Decision

1. **Contract, not a new signal.** Pelorus documents which existing fields
   carry tiling, image count, device extensions and handle type
   ([docs/api/vulkan-export-contract.md](../api/vulkan-export-contract.md)).
   No AVOption, no side-data section, no ABI change.
2. **The stock rule is the handle-type rule.** The page states FFmpeg's
   derivation; Pelorus does not restate it in code.
3. **The consumer reads the export info and never exports without it.** It
   repeats FFmpeg's export query (format, tiling, usage, `ALIAS` only,
   `OPAQUE_FD`) for an `OPTIMAL` pool; a DRM pool exports `DMA_BUF`. With
   export info it exports each image's memory. Without it, the consumer asks
   for `tiling=drm` or `disable_multiplane=1` for in-place import, or copies
   and names the copy in its output. On `n9.0.2` an `OPTIMAL` multi-plane pool
   has no export info.
4. **A replay test holds the contract.**
   `ffmpeg-patches/test/vulkan-export-contract.c` runs in
   `ffmpeg-patches/test/build-and-run.sh` on every rebase: tiling, image count,
   device extensions and export info, with an fd export for each pool whose
   export info offers one. Cases: an `OPTIMAL` NV12 pool (no export info, not
   exported), a DRM pool, an odd-sized split-plane pool, plus a planted wrong
   tiling, a planted extension list without `VK_KHR_external_memory_fd` and a
   planted export claim on the `OPTIMAL` pool, all three of which the checker
   must refuse. `scripts/check-build-config.py` refuses a replay
   script without the step. A field that moves or disappears after a rebase
   fails the replay and is re-decided.

## Alternatives considered

| Option | Why not |
| --- | --- |
| An `exportable` AVOption or read-only output flag on each filter | A second source of a fact FFmpeg already records; it can disagree with the pool after a rebase, and it would not cover pools Pelorus does not allocate |
| A side-data section with tiling and handle type per frame | Needs an ABI minor bump and a VMAFx reader for facts already one pointer away from the frame |
| A frames-context capability bit for exportable memory in FFmpeg | Generic FFmpeg change; belongs upstream or in the shared fix series, not the Pelorus stack, and nothing blocks the consumer meanwhile |

## Consequences

- **Positive**: VMAFx can import Pelorus frames in place using fields it
  already has; each rebase re-checks the contract on real hardware.
- **Negative**: The default `OPTIMAL` multi-plane pool cannot be imported in
  place on `n9.0.2`; the consumer needs `tiling=drm`, `disable_multiplane=1`
  or a copy. FFmpeg records no flag for a pool without export info, so the
  consumer repeats FFmpeg's query.
- **Neutral / follow-ups**: The shared fix series (VMAFx/ffmpeg-patches)
  backports `63d0237033`. When the pinned series carries it, `optimal-nv12`
  fails with export info present, and this decision is re-made. The VMAFx side
  (read the export info, never export without it, name the copy fallback) is
  tracked in a VMAFx issue linked from #104. The test is not in a clang-tidy lane yet (exception in
  `.standards.yaml`, VMAFx/pelorus#94).

## References

- Controller ledger `pelorus-104-export-contract` (2026-10-18, paraphrased):
  Pelorus owns the signal as a documented frames-context contract with a test
  and no new option, side data or ABI change.
- Controller ledger `pelorus-104-handle-types` (2026-10-18, paraphrased):
  document the stock handle-type rule; the consumer confirms by fd export and
  names its copy fallback; the test re-runs on each rebase.
- Controller ledger `pelorus-104-optimal-export` (2026-10-18, paraphrased):
  replaces the fd-export confirmation above; the consumer reads the export
  info and never exports without it, uses `tiling=drm` or
  `disable_multiplane=1` for in-place import or names a copy; the shared
  series takes the upstream query fix and the contract is re-decided when the
  pin carries it.
- [Research 0104](../research/0104-vulkan-export-contract.md): export-query
  probe and per-device runs.
