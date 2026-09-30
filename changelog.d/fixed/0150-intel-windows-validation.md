- Fixed `pelorus_analyze_vulkan`, `pelorus_grain_estimate_vulkan`, and
  `pelorus_mc_vulkan` labelling their forwarded input frames with a fresh output
  frames context whenever FFmpeg could not reuse the input one (linear tiling,
  missing usage bits). A following `hwdownload` rejected every frame; the output
  link now carries the input link's context. The format matrix gained a
  linear-input pass-through row ([ADR-0150](docs/adr/0150-intel-arc-b580-uhd770-validation.md)).
