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
  fuse it into an FMA on one path only. Default-path output can change in rare
  10/12-bit samples (usually by one code value; a threshold-gated dehalo row
  moved more, locally); 8-bit output is unchanged. A 10-bit
  direct/tiled denoise row joined the format matrix. An aa `fast=1` residual on
  the UHD 770 (linear single-plane input) remains open ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
- Fixed `pelorus_denoise_vulkan` exceeding
  `maxPerStageDescriptorStorageImages` (16 on the Intel UHD 770) with 21
  storage-image descriptors for 3-plane formats
  (`VUID-VkPipelineLayoutCreateInfo-descriptorType-03020`). Its six read-only
  frames are now sampled images read with `texelFetch()`; the fast gate checks
  a 16-descriptor storage-image budget for every filter ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
- The Vulkan format matrix now runs on Intel's Windows drivers: it treats
  `VUID-VkFormatProperties2-pNext-pNext` and
  `VUID-VkHostImageLayoutTransitionInfo-oldLayout-09230` as known-upstream.
  Both are raised inside entry points only stock FFmpeg calls, and a bare
  `hwupload,hwdownload` emits them. On those drivers, run it with
  `VULKAN_DEVICE="0,disable_multiplane=1"` (Arc B580) or
  `"1,linear_images=1,disable_multiplane=1"` (UHD 770), because the default
  multi-plane host copy corrupts frames in the driver ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
- Corrected documentation that overstated behaviour, after real encodes on
  Intel Arc B580 and UHD 770: `pelorus_scenecut` needs
  `-force_key_frames source` with the `ffmpeg` command line (fftools rewrites
  `pict_type`); `libaom-av1` and `libsvtav1` ignore
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS`, so only `av1_nvenc -pelorus_film_grain`
  consumes `grain_estimate native=1`; `pelorus_fgs components=0` is
  NAL-identical rather than byte-identical, and FFmpeg's decoder synthesizes
  only H.274 model 0; Intel's Windows oneVPL runtimes reject HEVC `EnableMBQP`,
  so `hevc_qsv -pelorus_roi 1` silently uses stock rectangles there. The Vulkan
  backend page documents the Windows Intel device modes
  ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
