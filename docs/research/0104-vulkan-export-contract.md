<!-- markdownlint-disable MD013 MD060 -->
# Export info of the Pelorus Vulkan output pools

**Date:** 2026-10-18

**Decision:** [ADR-0221](../adr/0221-vulkan-export-contract.md), issue [#104](https://github.com/VMAFx/pelorus/issues/104).

**Scope:** FFmpeg `n9.0.2` plus shared series `v0.3.0` plus the Pelorus stack. NVIDIA GeForce RTX 4090 (driver 615.78.08), Intel Arc A380 (Mesa 26.2.4, ANV, Xe KMD), AMD Radeon iGPU of a Ryzen 9 9950X3D (Mesa 26.2.4, RADV). Pools from `pelorus_deband_vulkan` on `testsrc2`, `format=nv12,hwupload`.

Raw logs (local, not committed): `.workingdir/evidence/104/` (`probe.c`, `probe.txt`, `replay*.log`, `dev0.log`, `dev1.log`, `dev2.log`, `replay-r6.log`, `dev0-r6.log`, `dev1-r6.log`, `dev2-r6.log`).

## Verdict

On `n9.0.2` an `OPTIMAL` multi-plane storage pool is allocated without any export handle type on all three devices. FFmpeg's export query passes `VK_IMAGE_CREATE_ALIAS_BIT` as the only create flag, and the drivers refuse that for `G8_B8R8_2PLANE_420_UNORM` with storage usage. Calling `vkGetMemoryFdKHR` on such memory is invalid usage, and its result is not a signal: the 4090 returned a file descriptor, the A380 and the iGPU returned errors. Split-plane pools (`disable_multiplane=1`) and Pelorus DRM pools export on all three devices. Upstream FFmpeg master fixed the query in `63d0237033` (2026-08-07); no release tag carries it.

## Method

1. `test/vulkan-export-contract.c` in its first form exported every image of each pool with `vkGetMemoryFdKHR` (`OPAQUE_FD` for `OPTIMAL`, `DMA_BUF` for DRM). It ran in the replay on the 4090, then from the kept build with `VULKAN_DEVICE=0`, `1` and `2`.
2. `libavutil/hwcontext_vulkan.c` at `n9.0.2` was read: `vulkan_pool_alloc()` (lines 2838-2905) calls `try_export_flags()` (line 2766) and adds a handle type only when `vkGetPhysicalDeviceImageFormatProperties2` succeeds; for non-DRM tiling the query's `flags` is `VK_IMAGE_CREATE_ALIAS_BIT`. Shared series patch 0006 adds the format list to the DRM branch of that query only.
3. `probe.c` repeats that query on each device for `G8_B8R8_2PLANE_420_UNORM` and `R8_UNORM`, with and without storage usage, once with `ALIAS` only (`0x400`) and once with the flags FFmpeg creates a multi-plane image with (`MUTABLE_FORMAT | EXTENDED_USAGE | ALIAS`, `0x508`).

## Results

Export of each image in the first test form:

| Case | RTX 4090 | Arc A380 | RADV iGPU |
| --- | --- | --- | --- |
| `optimal-nv12` (`OPAQUE_FD`) | fd returned | `-10` (`VK_ERROR_TOO_MANY_OBJECTS`) | `-2` (`VK_ERROR_OUT_OF_DEVICE_MEMORY`) |
| `drm-nv12` (`DMA_BUF`) | fd | fd | fd |
| `split-641x361` (`OPAQUE_FD`, 2 images) | fd | fd | fd |

Export query, `OPTIMAL`, `OPAQUE_FD` (result code, `externalMemoryFeatures`), identical on all three devices except RADV's features `0x7`:

| Format | Usage | Flags `0x400` | Flags `0x508` |
| --- | --- | --- | --- |
| `G8_B8R8_2PLANE_420_UNORM` | `0xf` (with storage) | `-11` (`VK_ERROR_FORMAT_NOT_SUPPORTED`) | `0`, `0x6` |
| `G8_B8R8_2PLANE_420_UNORM` | `0x7` (no storage) | `0`, `0x6` | `0`, `0x6` |
| `R8_UNORM` | `0xf` | `0`, `0x6` | `0`, `0x6` |

The test in its final form (export info first, fd export only where the info offers it) passed in replay r6 on the 4090 and from the kept build on all three devices, with the same line on each:

| Case | RTX 4090 | Arc A380 | RADV iGPU |
| --- | --- | --- | --- |
| `optimal-nv12` | no export handle, not exported | same | same |
| `drm-nv12` | fd export per image | same | same |
| `split-641x361` | fd export per image, 2 images | same | same |
| `wrong-tiling`, `no-fd-ext`, `export-expected` (planted) | refused | refused | refused |

## Consequence

The contract tells the consumer to repeat FFmpeg's query and never export without export info; the test asserts no export info for `optimal-nv12` and exports only `drm-nv12` and `split-641x361`. When the pinned FFmpeg carries `63d0237033`, `optimal-nv12` fails with export info present, and the contract is re-decided.
