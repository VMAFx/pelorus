- Fixed three encoder-patch defects. `hevc_nvenc -pelorus_me_hints 1` (patch
  0008) failed the whole encode with `invalid param (8): SetupCEAHints failed`
  on any frame that carried no Pelorus motion data, because NVENC rejects a
  session opened for external hints when a frame supplies zero candidates per
  block. Such frames now submit one zero-MV candidate per 16x16 block and log one
  warning; frames with motion data are bit-identical to before (BUG-031).
  `av1_vulkan -pelorus_roi 1` (patch 0009) scaled the ROI `qoffset` by the
  H.264/HEVC QP span (51 at 8-bit) instead of the AV1 qindex span (255), giving
  AV1 regions a fifth of the requested delta; a `qoffset` of 0.2 now maps to
  delta 51 instead of 10, still clamped to the driver range (BUG-032).
  `libsvtav1 -pelorus_roi 1` (patch 0013) let a frame without ROI data, or with
  an all-zero-delta map, inherit the previous frame's ROI through SVT-AV1's
  sticky event pointer; the encoder now submits a neutral event on that frame
  (BUG-030). New fast-suite tests: `nvenc-me-hints`, `svtav1-roi-sticky`, and
  per-codec span checks in `vulkan-qpmap-contract`.
