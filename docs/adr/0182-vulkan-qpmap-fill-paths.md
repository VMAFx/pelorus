<!-- markdownlint-disable MD013 MD060 -->
# ADR-0182: Patch 0009 writes the QP map in the advertised format, by a fill path the encode queue family can record

- **Status**: Proposed
- **Implementation**: pending (#278)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: ffmpeg, vulkan, roi, encoder-steering, radv

## Context

`-pelorus_roi 1` on `h264_vulkan` and `hevc_vulkan` never changed the
bitstream on Mesa RADV (issue #278,
[research 0231](../research/0231-amd-radv-vulkan-encode-spike.md)).
RADV 26.2.4 exposes `VK_KHR_video_encode_quantization_map` with a delta map,
yet the probe of patch 0009 disabled it. [ADR-0166](0166-vulkan-qpmap-activation.md)
recorded RADV's map as "`R32_SINT` with the single usage `DELTA_MAP`" and
deferred a fill path for it. Three things were wrong or missing:

1. **The usage query.** `vkGetPhysicalDeviceVideoFormatPropertiesKHR` was
   called with `imageUsage = DELTA_MAP` alone. RADV answers with exactly the
   usages asked for; asked for `DELTA_MAP|TRANSFER_DST|STORAGE` it returns the
   same `R32_SINT` entries with all three (`qmprobe.c`, research 0231). The
   "single usage" in ADR-0166 was this query, not the driver. NVIDIA reports
   extra usages unasked, which is why the narrow query worked there.
2. **The format.** The probe demanded `R8_SINT`; the host raster and the
   compute shader wrote one byte per texel. RADV advertises `R32_SINT` only.
3. **The queue family.** Every fill is recorded on the encode command buffer.
   RADV's encode queue family has `VK_QUEUE_VIDEO_ENCODE_BIT_KHR` and nothing
   else, so it may record neither `vkCmdCopyBufferToImage`
   (`VUID-vkCmdCopyBufferToImage-commandBuffer-cmdpool`: transfer, graphics or
   compute) nor `vkCmdDispatch`. Fixing 1 and 2 alone would have selected the
   copy path and recorded an invalid command. The probe also read the queue
   family's abilities from `AVVulkanDeviceQueueFamily.flags`, which lists only
   the purposes FFmpeg picked the family for (`VIDEO_ENCODE` on NVIDIA too,
   although NVIDIA's encode family has `TRANSFER`).

RADV advertises the `R32_SINT` delta map in `OPTIMAL`, `LINEAR` and
DRM-modifier tiling, a 16x16 (H.264) or 64x64 (H.265) texel block, and a
`[-51, 51]` QP-delta range.

## Decision

Patch 0009 will take the map format the driver advertises and pick a fill path
that the encode queue family can record:

1. **Formats.** Delta maps accept `R8_SINT`, `R16_SINT` and `R32_SINT`;
   emphasis maps `R8_UNORM` and `R16_UNORM`. The host raster keeps one `int32`
   ΔQP per texel and stores it at the format's width; the delta clamp is the
   intersection of the `qoffset` span, the driver range and the texel range.
2. **Query.** Ask for the map usage plus every fill usage the encode family can
   record (`TRANSFER_DST`, `STORAGE`); if the driver offers nothing for that
   combination, ask for each alone, then for the map usage alone. The first
   query with a fillable entry decides; within it the best path, then the
   narrowest texel. DRM-modifier tiling is skipped (no modifier list).
3. **Fill paths, in order.** On-GPU raster (compute on the encode family,
   `STORAGE`, 8-bit formats only, since the shader declares `r8i`/`r8`); staging
   copy (transfer, graphics or compute on the encode family, `TRANSFER_DST`);
   host-mapped image (a `LINEAR` entry, nothing from the queue family). The
   family's abilities come from `vkGetPhysicalDeviceQueueFamilyProperties2`
   (`FFVulkanContext.qf_props`).
4. **Host-mapped image.** Created `PREINITIALIZED` in host-visible coherent
   memory and mapped once. Per frame the host rasterizes into it at the
   subresource row pitch; one barrier (`HOST` → `VIDEO_ENCODE`) moves it to
   `VIDEO_ENCODE_QUANTIZATION_MAP_KHR` before `vkCmdBeginVideoCodingKHR`, and
   one after `vkCmdEndVideoCodingKHR` moves it to `GENERAL`, the layout in
   which the host may write a linear image again.
5. **One map image per exec context**, indexed by `exec->idx`: the fence wait
   in `ff_vk_exec_start()` then orders every rewrite, host or GPU, after the
   encode that last read the image. The CPU-side record of a host-mapped
   image's layout moves to `GENERAL` only after `ff_vk_exec_submit()`
   succeeded.
6. **Init-time creation.** Every map image, its memory and the host raster
   scratch are created by `pelorus_qpmap_init()` after
   `ff_vk_exec_pool_init()` and before the video session; nothing is allocated
   per frame (HISS-03). The memory type (host-visible coherent for the
   host-mapped image, device-local otherwise) is checked against the image's
   `memoryTypeBits` first. A missing type or a failed image disables steering
   with a warning before the session is created with the QP-map flags.
7. **Extent.** The probe refuses a map larger than
   `VkVideoEncodeQuantizationMapCapabilitiesKHR::maxQuantizationMapExtent`
   (warning, pass-through) instead of clamping it. RADV allows 256x256 texels
   for H.264 and 128x68 for H.265 (64x64 px, 8192x4352).
8. **No silent choice.** `-v verbose` lists every advertised entry with the
   path it allows and the chosen format, tiling and path; with no fillable
   entry the probe warns and passes through.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Fill on a transfer or compute queue, then hand the image to the encode queue (ownership transfer and semaphore) | Works with `OPTIMAL` tiling; no host writes | A second command buffer and queue per frame, release/acquire barriers on two families, a semaphore in `vulkan_encode_issue()`; much more code in a hand-maintained FFmpeg diff | Rejected: the host-mapped path needs one barrier pair; revisit if a driver offers no `LINEAR` map |
| Keep the map in `VIDEO_ENCODE_QUANTIZATION_MAP_KHR` and write it from the host in that layout | No post-encode barrier | Host access to a linear image is defined only in `PREINITIALIZED` or `GENERAL` layout; the encode requires `VIDEO_ENCODE_QUANTIZATION_MAP_KHR` (`VUID-vkCmdEncodeVideoKHR-pNext-10314`) | Rejected: undefined behaviour |
| Widen the compute shader (one binding per width, or `shaderStorageImageWriteWithoutFormat`) | The on-GPU raster could write `R16`/`R32` | No tested driver has compute on its encode family, so the code could not run here; adds a feature requirement | Deferred: wider formats use a host path; revisit with a driver that has compute on its encode family |
| Prefer the host-mapped path over the staging copy everywhere | One path for all drivers | Changes NVIDIA's measured path and memory placement for no gain | Rejected: NVIDIA keeps the copy and its bytes |

## Consequences

- **Positive**: RADV steers both codecs and both signs of `qoffset`; NVIDIA's
  steered streams are byte-identical to the previous patch. Measurements in
  "Measured" below.
- **Negative**: the host-mapped path writes device-visible memory from the CPU
  every frame (40x23 texels for 640x360 H.264, 4 bytes each); the post-encode
  barrier is one more command per mapped frame.
- **Neutral / follow-ups**: `scripts/test-vulkan-qpmap-fill.py --self-test`
  runs the entry classifier and the host raster from the hand diff in a C
  harness; `scripts/check-vulkan-qpmap-contract.py --self-test` pins the query,
  the queue-capability source, the layouts, the slot, init-time creation, the
  extent check and the post-submit layout record. Open: an on-GPU raster for
  16- and 32-bit formats; RADV CBR/VBR (RADV advertises no emphasis map).

## Measured

Host build of the replayed 22-patch stack, 640x360, 24 frames, `-rc_mode cqp
-qp 30`, `addroi` over the left half, ICD pinned per driver
(`VK_ICD_FILENAMES`), implicit layers off. PSNR is the `psnr` filter on the
decoded stream, left (ROI) and right half. Before the fix every steered RADV
stream equalled its control (research 0231, reproduced on this build).

| Driver | Clip | Codec | qoffset | Control bytes / MD5 | Steered bytes / MD5 | Left dB, control → steered | Right dB, control → steered |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RADV 26.2.4 | detail | h264 | -0.3 | 91499 / `74a6709e73be` | 1084250 / `71f7441248d4` | 36.68 → 44.82 | 35.86 → 35.87 |
| RADV 26.2.4 | detail | h264 | +0.3 | 91499 / `74a6709e73be` | 84025 / `ed362c8fbdee` | 36.68 → 31.70 | 35.86 → 35.66 |
| RADV 26.2.4 | detail | hevc | -0.3 | 103003 / `3084d8e057d4` | 1197268 / `870ee6e4665f` | 36.80 → 45.41 | 35.99 → 36.01 |
| RADV 26.2.4 | detail | hevc | +0.3 | 103003 / `3084d8e057d4` | 87514 / `8a4199ecf90e` | 36.80 → 32.39 | 35.99 → 35.97 |
| RADV 26.2.4 | synth-banding | h264 | -0.3 | 2717 / `9df156cee26a` | 5120 / `0a5451036490` | 54.06 → 60.79 | 53.63 → 56.50 |
| RADV 26.2.4 | synth-banding | h264 | +0.3 | 2717 / `9df156cee26a` | 2695 / `6ca4c8f3fa02` | 54.06 → 40.97 | 53.63 → 53.40 |
| RADV 26.2.4 | synth-banding | hevc | -0.3 | 2279 / `7073d4d2ecd7` | 5655 / `a3b13e11774b` | 58.80 → 65.75 | 57.48 → 57.87 |
| RADV 26.2.4 | synth-banding | hevc | +0.3 | 2279 / `7073d4d2ecd7` | 2146 / `6ca932ca0472` | 58.80 → 47.05 | 57.48 → 56.46 |

- Every stream decodes to 24 of 24 frames. RADV picks `R32_SINT`, `LINEAR`,
  host-mapped fill, 40x23 texels (H.264) and 10x6 texels of 64x64 px (H.265).
  The `-0.3` H.264 detail stream is 12 times larger: QP 15 on a noisy half.
  On the nearly flat banding clip the right half gains too, from better
  references; on the detail clip it stays within 0.2 dB.
- RTX 4090, NVIDIA 615.78.08: all 20 encodes of the same matrix (16 steering
  plus 4 validation runs) are byte-identical to the previous patch; the probe
  still picks `R8_SINT`, `LINEAR`, staging copy, now from a
  `DELTA_MAP|TRANSFER_DST` query.
- Validation layer (`-init_hw_device vulkan=vk:0,debug=1`, one steered and
  one unsteered encode per codec and driver): the steered runs report the same
  VUID set as the unsteered ones. RADV: `VUID-vkDestroyDevice-device-05137`
  (three FFmpeg image views, also without `-pelorus_roi`) and
  `VUID-VkImageMemoryBarrier2-srcAccessMask-03915` (FFmpeg's source-frame
  barrier on a video-encode-only family). NVIDIA: `05137` and
  `VUID-vkCmdEncodeVideoKHR-pEncodeInfo-08206`, as before this change.
- After the review changes (init-time images and memory-type check, extent
  check, layout recorded after the submission) all 20 RADV and 20 NVIDIA
  encodes are byte-identical to the table and runs above, with the same VUID
  sets.
- Mesa 25.0.7 RADV (tester image driver, same FFmpeg build): `pelorus_roi:
  device does not enable VK_KHR_video_encode_quantization_map; QP-map
  steering disabled (pass-through).`, steered stream equal to the control.

## References

- Issue #278; [research 0231](../research/0231-amd-radv-vulkan-encode-spike.md)
  (RADV capability table, `qmprobe.c`).
- [ADR-0166](0166-vulkan-qpmap-activation.md) — the deferred "host-mapped
  `LINEAR` memory" row this ADR takes up.
- [ADR-0114](0114-encoder-steering.md) — Tier 2 "via Vulkan" steering.
- FFmpeg n9.0.2 (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`):
  `libavutil/hwcontext_vulkan.c` `PICK_QF` (purpose flags),
  `libavutil/vulkan.c` `ff_vk_exec_get()`/`ff_vk_exec_start()`.
- Vulkan-ValidationLayers `layers/core_checks/cc_video.cpp`
  (`VUID-vkCmdEncodeVideoKHR-pNext-10314`, map layout) and
  `cc_synchronization.cpp` (`HOST` stage allowed on every queue family).
- `vulkaninfo` on 2026-10-09: RADV encode family `QUEUE_VIDEO_ENCODE_BIT_KHR`
  only; NVIDIA 615.78.08 encode family `TRANSFER | SPARSE_BINDING |
  VIDEO_ENCODE`.
