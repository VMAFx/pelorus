- Fixed `pelorus_analyze_vulkan`, `pelorus_grain_estimate_vulkan`, and
  `pelorus_mc_vulkan` labelling their forwarded input frames with a fresh output
  frames context whenever FFmpeg could not reuse the input one (linear tiling,
  missing usage bits). A following `hwdownload` rejected every frame; the output
  link now carries the input link's context. The format matrix gained a
  linear-input pass-through row ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
- Fixed `pelorus_denoise_vulkan=tile=1`, `pelorus_dehalo_vulkan=tile=1`, and
  `pelorus_aa_vulkan=fast=1` drifting from their direct paths by one code value
  at 10/12-bit: the storage-to-sample product (plus denoise's patch SSD and
  aa's squared Sobel magnitude) is now `precise`, so a driver can no longer
  fuse it into an FMA on one path only. Default-path output can change by one
  code value in rare 10/12-bit samples; 8-bit output is unchanged. A 10-bit
  direct/tiled denoise row joined the format matrix. An aa `fast=1` residual on
  the UHD 770 (linear single-plane input) remains open ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
- Fixed `pelorus_denoise_vulkan` exceeding
  `maxPerStageDescriptorStorageImages` (16 on the Intel UHD 770) with 21
  storage-image descriptors for 3-plane formats
  (`VUID-VkPipelineLayoutCreateInfo-descriptorType-03020`). Its six read-only
  frames are now sampled images read with `texelFetch()`; the fast gate checks
  a 16-descriptor storage-image budget for every filter ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
