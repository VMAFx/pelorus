- **Control-plane contract** (`docs/api/control-plane.md`): froze the
  autotune-relevant `vf_pelorus_deband_vulkan` AVOptions (`range`, `thry`,
  `thrc`, `grainy`, `grainc`, `softness`, `detail`, `dither`, `dynamic`,
  `protect`) as a stable, versioned contract that vmafx's `vmaf-tune` autotune
  hard-codes against. Renaming/removing/narrowing any frozen knob is a breaking
  change requiring a coordinated two-repo PR. ADR-0110 (builds on ADR-0106).
