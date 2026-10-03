- Fixed `pelorus_dehalo_vulkan` corrupting line-art (BUG-011). The old gate
  compared `edge` with a raw Sobel magnitude that reads 4× the step, and a thin
  stroke's symmetric core reads a zero Sobel, so the filter rewrote stroke
  pixels by up to 129 codes (clean synthetic image: 41.5 dB PSNR; now 76.0 dB,
  line pixels unchanged). `edge` is now an edge step (Sobel magnitude / 4), the
  near-line scan tests the edge mask in both polarities and excludes the gap
  between close edges, and the pull follows `DeHalo_alpha` again
  (`MaskedMerge(halos, clp, so)` plus the `Repair` clamp). This is a harm-fix,
  not halo removal: halo RMS at the defaults drops by at most 1.3%, because
  steep halo flanks still count as line-art. Real halo removal (a feathered
  `FineDehalo`-style mask) is tracked as a separate follow-up
  ([ADR-0163](docs/adr/0163-dehalo-gate-and-pull.md)).
