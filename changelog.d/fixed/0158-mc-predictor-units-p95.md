- Fixed `pelorus_mc_vulkan` seeding each frame's search with predictors four
  times too far from the true motion (BUG-009). Since the quarter-pel output of
  [ADR-0130](docs/adr/0130-mc-subpel-quarterpel.md), the shader has emitted Q2
  vectors, but the host passed them back unconverted as the global-motion and
  collocated-block predictors, which the integer-pel search reads as whole
  pixels. The host now rounds the Q2 field to integer pel (half away from zero,
  the same rounding as the NVENC ME-hint consumer) before it seeds the next
  frame, and every MV field in the filter, both shaders, and
  `PelorusMotionSection` names its unit. On a 1080p synthetic pan the
  steady-state field now matches the known shift on 98.6–99.9% of blocks
  (2–10 px, diagonal, `bsize=8`), up from 6–84%. The `PEL_SEC_MOTION` grid and
  scalars keep their units, so the NVENC and denoise consumers are unchanged.
- Fixed the `motion_magnitude_p95` selection in `pelorus_mc_vulkan` scanning
  O(n²) per frame (BUG-014). It is now an O(n) radix select that the new
  `mc-stats` fast test proves bit-identical to the old scan. The host also copies
  the mapped MV and SAD buffers once per frame instead of reading device memory
  element by element, and keeps its scratch across frames. On an RTX 4090 a
  1080p `bsize=16` frame drops from 30–42 ms to 3–4.5 ms of wall time, and a
  2160p `bsize=8` frame from 1.3–1.6 s to 46–56 ms.
