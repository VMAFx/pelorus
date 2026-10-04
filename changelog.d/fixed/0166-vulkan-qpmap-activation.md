- Fixed `-pelorus_roi` on `h264_vulkan`, `hevc_vulkan` and `av1_vulkan`, which
  never activated: FFmpeg's Vulkan hwcontext enabled neither
  `VK_KHR_video_encode_quantization_map` nor its `videoEncodeQuantizationMap`
  feature (BUG-017). Patch 0009 now enables both, clamps delta-map values to
  the driver's per-codec range instead of the libx264 span (BUG-018), creates
  the map with the advertised tiling, records the map upload before the video
  coding scope, creates `QUANTIZATION_MAP_COMPATIBLE` session parameters, and
  enables H.265 `cu_qp_delta` so the stream stays decodable. Verified on an RTX
  4090; RADV disables steering with a warning because its map format cannot be
  filled yet ([ADR-0166](docs/adr/0166-vulkan-qpmap-activation.md)).
