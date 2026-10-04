<!-- markdownlint-disable MD013 MD060 -->
# ADR-0166: Patch 0009 enables the Vulkan quantization-map extension and follows the driver's map contract

- **Status**: Proposed
- **Date**: 2026-10-03
- **Deciders**: Lusoris
- **Tags**: ffmpeg, vulkan, roi, encoder-steering, hwcontext, validation

## Context

Patch 0009 (`ffmpeg-patches/files/vulkan-pelorus-qpmap.patch`, ADR-0114 Tier 2)
lets `h264_vulkan`, `hevc_vulkan` and `av1_vulkan` turn
`AV_FRAME_DATA_REGIONS_OF_INTEREST` into a `VK_KHR_video_encode_quantization_map`
delta or emphasis map. It shipped compile-verified only. Running it on the
workstation's GPUs on 2026-10-03 showed that it could not work anywhere:

- **BUG-017.** The probe only checked whether the device had enabled the
  extension. FFmpeg n9.0.2 never enables it: it is absent from
  `optional_device_exts[]` in `libavutil/hwcontext_vulkan.c`, so
  `-pelorus_roi 1` logged "device does not enable
  VK_KHR_video_encode_quantization_map" and passed through on the RTX 4090 and
  on RADV. Forcing the extension through the hwcontext `device_extensions`
  option is not a workaround: the `videoEncodeQuantizationMap` feature stays
  off, and the validation layer reports
  `VUID-VkVideoSessionCreateInfoKHR-flags-10264` and
  `VUID-VkImageCreateInfo-usage-10251`.
- **BUG-018.** Delta values were clamped to the libx264 span `[-51, 51]`. The
  driver reports its own range per codec (`minQpDelta`/`maxQpDelta`,
  `minQIndexDelta`/`maxQIndexDelta`), and the Vulkan specification leaves the
  block QP undefined for a value outside it (`chapters/video/h264_encode.adoc`,
  `h265_encode.adoc`, `av1_encode.adoc`). NVIDIA 615.71.09 reports `[0, 51]`
  for H.264 and H.265 and `[0, 255]` for AV1, so every negative delta the probe
  allowed was out of range.
- **Defects behind those two.** With the extension forced on, the layer also
  reported that the map image used `OPTIMAL` tiling although NVIDIA advertises
  its `R8_SINT` delta map in `LINEAR` tiling only
  (`VUID-VkImageViewCreateInfo-usage-10259`), that the map upload was recorded
  inside the video coding scope (`VUID-vkCmdCopyBufferToImage-videocoding`), and
  that the session parameters were not `QUANTIZATION_MAP_COMPATIBLE`
  (`VUID-vkCmdEncodeVideoKHR-pNext-10315`). Once those were fixed, `hevc_vulkan`
  produced undecodable pictures (17 dB PSNR): its CQP PPS has
  `cu_qp_delta_enabled_flag = 0`, so the per-CU QP the map requests cannot be
  signalled.

RADV (Mesa 26.2.4) advertises its delta map only as `R32_SINT` with the single
usage `VIDEO_ENCODE_QUANTIZATION_DELTA_MAP`, so neither the transfer copy nor the
compute store can fill it. ANV on the Arc A380 does not expose the extension.

## Decision

Patch 0009 will enable the extension and its feature inside FFmpeg's Vulkan
hwcontext, and the encoder path will follow every map property the driver
reports instead of assuming one:

1. `libavutil/vulkan_functions.h` gains `FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP`,
   `libavutil/vulkan_loader.h` maps the extension name to it, and
   `libavutil/hwcontext_vulkan.c` lists the extension as optional and chains,
   copies and enables `VkPhysicalDeviceVideoEncodeQuantizationMapFeaturesKHR`.
   The probe requires the extension flag and the enabled feature.
2. Delta-map values are clamped to the intersection of the `qoffset` QP span,
   the driver's per-codec delta range (re-queried with
   `vkGetPhysicalDeviceVideoCapabilitiesKHR`), and the `R8_SINT` texel range.
   Out-of-range requests are clamped, not shifted. A driver range that excludes
   0 disables steering; a range without negative values warns that lower-QP
   regions get no extra bits.
3. The probe selects a format entry whose advertised usages cover the fill path
   and creates the map image with that entry's tiling; with no such entry it
   lists what the driver offers and disables steering.
4. The map fill is recorded before `vkCmdBeginVideoCodingKHR`, the session
   parameters are created `QUANTIZATION_MAP_COMPATIBLE`, and `hevc_vulkan` sets
   `cu_qp_delta_enabled_flag` whenever a map is enabled.
5. Probe failures log at warning level, because the user asked for steering.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Enable extension and feature in `hwcontext_vulkan.c` | Works for every FFmpeg-created or derived device; no user action | Patch 0009 now edits libavutil, which is more to rebase | **Chosen** |
| Document `-init_hw_device vulkan=…,device_extensions=VK_KHR_video_encode_quantization_map` | No libavutil hunk | The option cannot enable the feature, so the result breaks VUID-10264; users must know the extension name | Rejected: invalid usage |
| Shift all deltas so the minimum maps to the driver's minimum (raise QP outside the regions instead) | Negative `qoffset` would still have an effect on NVIDIA | Changes the frame's overall rate and meaning of CQP; differs from the NVENC/QSV/libaom/SVT-AV1 conventions | Rejected: clamping keeps one convention; revisit with BD-rate data |
| Fill RADV's `R32_SINT` map through host-mapped `LINEAR` memory | Would enable steering on RADV | Host writes need `GENERAL`/`PREINITIALIZED` layout while encode needs `VIDEO_ENCODE_QUANTIZATION_MAP_KHR`; a new per-frame layout and synchronisation design | Deferred: disabled with a precise warning instead |
| Leave `cu_qp_delta_enabled_flag` to driver overrides | No `vulkan_encode_h265.c` hunk | NVIDIA reports a PPS override but keeps the flag off; the stream is broken | Rejected: measured |

## Consequences

- **Positive**: `-pelorus_roi 1` now steers all three NVIDIA Vulkan encoders.
  In a 60-frame 1080p `-qp 30` test with a `+0.3` region over the left half,
  the decoded H.264 macroblock QP there rose from 30 to about 45 while the right
  half stayed at 30; the stream shrank by 30 % (H.264) and 50 % (HEVC) and the
  right half's PSNR did not change. The QP-map path adds no VUID to the stock
  encoder's own set.
- **Negative**: on NVIDIA, negative offsets, which is what
  `pelorus_analyze_vulkan roi=1` emits, clamp to 0 and have no effect. RADV
  steering stays disabled. Patch 0009 grows by libavutil hunks and one
  `vulkan_encode_h265.c` hunk.
- **Neutral / follow-ups**: `scripts/check-vulkan-qpmap-contract.py
  --self-test` in the fast suite pins every repair in the hand-maintained diff.
  Open: a policy for drivers without negative deltas, a fill path for RADV's
  `R32_SINT` map, the AV1 `qoffset` scale (qindex 0..255 rather than the QP
  span, as libaom/SVT-AV1 use), and the upstream `av1_vulkan` stream defects on
  NVIDIA (`VUID-vkCmdEncodeVideoKHR-pStdPictureInfo-10350`, undecodable frames
  without `-pelorus_roi`).

## References

- [ADR-0114](0114-encoder-steering.md) — Tier 2 "via Vulkan" steering this ADR repairs.
- [ADR-0104](0104-ffmpeg-patch-stack.md) — patch-stack delivery model.
- FFmpeg n9.0.2 (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`):
  `libavutil/hwcontext_vulkan.c` `optional_device_exts[]`,
  `libavcodec/vulkan_encode_h265.c` `init_sequence_headers()`.
- Vulkan-Docs `chapters/videocoding.adoc` and `chapters/video/{encode,h264_encode,h265_encode,av1_encode}.adoc`
  (main, fetched 2026-10-03): VUIDs 10264, 10251, 10259, 10315, 06811 and the
  out-of-range delta wording.
- `vulkaninfo` on 2026-10-03: NVIDIA 615.71.09, Mesa 26.2.4 (RADV, ANV).
- Source: `req` — "the quantization-map path only checks whether the hwcontext's
  ENABLED device extensions include VK_KHR_video_encode_quantization_map ...
  delta-QP clamped to the x264 range instead of the driver-reported ... caps"
  (bug-wave brief, BUG-017 and BUG-018).
