<!-- markdownlint-disable MD013 -->
# Vulkan export contract

A program that imports a Pelorus Vulkan frame into another API (CUDA, SYCL,
HIP, VAAPI, a second Vulkan device) needs four facts about the frame's pool:
its tiling, how many images make up a frame, whether the device can export
memory, and which handle type to ask for. Pelorus adds no option and no side
data for this. The facts are already in FFmpeg's public frames and device
contexts, and this page says which fields to read. Decision record:
[ADR-0221](../adr/0221-vulkan-export-contract.md) (issue
[#104](https://github.com/VMAFx/pelorus/issues/104)).

## Which pools

Six filters write new frames into their own output pool: `deband`, `denoise`,
`dehalo`, `aa`, `deblock`, `borderfix`. Each takes `tiling=optimal` (the
default) or `tiling=drm`
([ADR-0184](../adr/0184-vulkan-drm-modifier-pools.md)). `analyze`,
`grain_estimate` and `mc` pass their input frame through, so their output is
in the upstream pool and this contract describes that pool only when it is
one of the six.

## What to read

Start from the frame: `frame->hw_frames_ctx` is an `AVHWFramesContext`, its
`hwctx` is the `AVVulkanFramesContext`, its `device_ctx->hwctx` is the
`AVVulkanDeviceContext`, and `frame->data[0]` is the `AVVkFrame`.

| Fact | Field | Values |
| --- | --- | --- |
| tiling | `AVVulkanFramesContext.tiling`, repeated in `AVVkFrame.tiling` | `VK_IMAGE_TILING_OPTIMAL` for `tiling=optimal`; `VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT` for `tiling=drm` |
| images per frame | `AVVulkanFramesContext.format[]` up to the first `VK_FORMAT_UNDEFINED` | 1 multi-plane image; one image per plane (`av_pix_fmt_count_planes(sw_format)`) when the device option `disable_multiplane=1` or `AVVulkanFramesContext.flags` bit `AV_VK_FRAME_FLAG_DISABLE_MULTIPLANE` asks for it; the device option does not set the flag (n9.0.2 `libavutil/hwcontext_vulkan.c:2956`), so a consumer counts `format[]` and never reads the flag |
| memory per image | `AVVkFrame.mem[i]`, `size[i]`, `offset[i]` | one `VkDeviceMemory` per image |
| device can export | `AVVulkanDeviceContext.enabled_dev_extensions` | `VK_KHR_external_memory_fd`; a DRM pool also needs `VK_EXT_external_memory_dma_buf` and `VK_EXT_image_drm_format_modifier` |
| synchronisation | `AVVkFrame.sem[i]`, `sem_value[i]`, `AVVulkanFramesContext.lock_frame` / `unlock_frame` | timeline semaphores; hold the frame lock while reading or signalling them |

## Handle type

FFmpeg picks the export handle type when it creates the pool, from the tiling
and the device's external-memory extensions (`vulkan_pool_alloc()` in
`libavutil/hwcontext_vulkan.c` at `n9.0.2`, lines 2855-2871):

| Platform | Tiling | Device extension | Handle type |
| --- | --- | --- | --- |
| Linux, BSD | `OPTIMAL` | `VK_KHR_external_memory_fd` | `VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT` |
| Linux, BSD | `DRM_FORMAT_MODIFIER_EXT` | `VK_EXT_external_memory_dma_buf` | `VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT` |
| Windows | any | `VK_KHR_external_memory_win32` | `VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_WIN32_BIT` |

FFmpeg adds a handle type only when the driver accepts it for the pool's
format and usage (`try_export_flags()`, line 2766). A pool can therefore lack
every handle type without an error, and the frames context records no flag for
it.

## Optimal multi-plane pools carry no export handle on n9.0.2

For an `OPTIMAL` pool, `try_export_flags()` asks the driver about
`format[0]`, the pool's tiling and usage, and `VK_IMAGE_CREATE_ALIAS_BIT` as
the only create flag. A multi-plane image with storage usage (the default
Pelorus output, `NV12` as `G8_B8R8_2PLANE_420_UNORM`) is created with
`MUTABLE_FORMAT | EXTENDED_USAGE | ALIAS`, and the driver refuses the
`ALIAS`-only query with `VK_ERROR_FORMAT_NOT_SUPPORTED`. FFmpeg then allocates
the memory without any export handle type. Measured on an RTX 4090
(proprietary driver), an Arc A380 (Mesa ANV) and a Radeon iGPU (Mesa RADV):
the query fails on all three, and passes with the real create flags
([research 0104](../research/0104-vulkan-export-contract.md)).

Calling `vkGetMemoryFdKHR` on such memory is invalid usage. One driver
returned a file descriptor anyway, the other two returned an error, so the
call's result proves nothing. Upstream FFmpeg fixed the query on its master
branch (commit `63d0237033`, "Query export support with the real image
flags"); no release carries it yet. The shared fix series takes a backport,
and this section is re-decided when the pinned series carries the fix.

| Pool | Images | Export info | Exported in place |
| --- | --- | --- | --- |
| `tiling=optimal`, multi-plane | 1 | none | no |
| `tiling=optimal`, `disable_multiplane=1` | one per plane | `OPAQUE_FD` | yes |
| `tiling=drm` | 1 | `DMA_BUF` | yes |

## How a consumer decides

The consumer reads the export info before it exports anything. FFmpeg keeps
that info in local variables of `vulkan_pool_alloc()`, so the consumer
repeats the query FFmpeg made: `vkGetPhysicalDeviceImageFormatProperties2`
with `format[0]`, `tiling`, `usage`, `VK_IMAGE_CREATE_ALIAS_BIT` and an
external-image info for `OPAQUE_FD`, and it requires
`VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT` in the result. A Pelorus DRM pool
needs no repeat query: Pelorus lists only modifiers for which the driver
accepts `DMA_BUF` export with the pool's real create flags
(`pel_vk_pool_mod_image()` in `ffmpeg-patches/files/pelorus_vulkan_pool.h`,
[ADR-0184](../adr/0184-vulkan-drm-modifier-pools.md)).

- Export info present: call `vkGetMemoryFdKHR` on each `AVVkFrame.mem[i]` with
  the handle type from the table above.
- Export info absent: never call `vkGetMemoryFdKHR`. For in-place import, ask
  for a pool that exports, with `tiling=drm` on the Pelorus filter or
  `disable_multiplane=1` on the Vulkan device. Otherwise copy the frame and
  name the copy in the output (no silent fallback).

VMAFx tracks its side of this in a VMAFx issue linked from #104.

## Test

`ffmpeg-patches/test/vulkan-export-contract.c` runs in the replay
(`ffmpeg-patches/test/build-and-run.sh`, step "vulkan export contract") and so
on every FFmpeg rebase. It feeds `pelorus_deband_vulkan` and asserts tiling,
image count, device extensions and the export info, and exports only the pools
whose export info offers it:

| Case | Kind | Expectation |
| --- | --- | --- |
| `optimal-nv12` | positive | `OPTIMAL`, 1 image, no export info, not exported |
| `drm-nv12` | positive | `DRM_FORMAT_MODIFIER_EXT`, 1 image, `DMA_BUF` export; skipped by name when the device lacks the DRM extensions |
| `split-641x361` | boundary | odd size, `disable_multiplane=1`: 2 images, `OPAQUE_FD` export info, one export each |
| `wrong-tiling` | planted | an `OPTIMAL` pool checked as DRM must be refused |
| `no-fd-ext` | planted | the device's extension list without `VK_KHR_external_memory_fd` must be refused |
| `export-expected` | planted | an `OPTIMAL` multi-plane pool checked as exportable must be refused |

When the pinned FFmpeg carries the upstream query fix, `optimal-nv12` fails
with `export info 1, expected 0`; that failure is the trigger to re-decide the
contract.

Exit 77 (no Vulkan device, or no DRM extensions) passes only when
`EXPORT_CONTRACT_SKIP` names the printed reason (ADR-0189). Windows replays
skip the step by name, since Windows exports `OPAQUE_WIN32` handles, not fds.
Run it by hand against a replay's static prefix with
`VULKAN_DEVICE=<index>` to pick a device.
