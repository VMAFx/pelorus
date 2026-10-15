<!-- markdownlint-disable MD013 MD060 -->
# DRM-modifier pools on shared FFmpeg series v0.2.0: P010 and AMD synchronisation

**Date:** 2026-10-15

**Decision:** [ADR-0184 Update 2026-10-15](../adr/0184-vulkan-drm-modifier-output-pools.md#update-2026-10-15),
[ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md), issue
[#103](https://github.com/VMAFx/pelorus/issues/103) (final part: P010 + AMD
`tiling=drm`). Follows initial research
[0184](0184-vulkan-drm-modifier-pools.md).

**Scope:** FFmpeg `n9.0.2` (`946fcce0`) with the shared FFmpeg fix series
`v0.2.0` (patches 0001–0008, verified by `scripts/fetch-ffmpeg-series.sh`) plus
the 22 Pelorus patches; `build-and-run.sh` rc 0 (`build-and-run.log`). Tested
binary: `scratch/pel103-keep`. Host: kernel 7.2.10-1-cachyos, Mesa 26.2.4 (ANV,
RADV, radeonsi), iHD 26.3.5. Intel Arc A380 (`renderD130`) and AMD Radeon 610M
(`renderD129`).

**Multi-GPU environment pin:** On multi-GPU hosts, an active
`LIBVA_DRIVER_NAME=nvidia` in the session environment can make VAAPI load
the NVIDIA VAAPI driver on another GPU, which fails every case with misleading errors. Runs explicitly pin
`LIBVA_DRIVER_NAME` and `VK_DRIVER_FILES` per GPU.

## Verdict

- P010 maps, reads back and encodes (`hevc_vaapi` Main10) bit-exact on both Intel
  and AMD GPUs. The stock FFmpeg layer mismatch is fixed by shared series patch
  0005 (`GR1616` row in `vaapi_drm_format_map`) and patch 0006
  (`VkImageFormatListCreateInfo` in `try_export_flags()`).
- AMD synchronization race is resolved by shared series patch 0007 (CPU
  `vkWaitSemaphores()` before DMA-BUF export on RADV). Mapped surfaces read back
  bit-exact across 20-frame repetitions.
- The AMD warning branch in `pel_vk_pool_warn_limits()` is removed from
  `ffmpeg-patches/files/pelorus_vulkan_pool.h`.
- `ffmpeg-patches/test/vulkan-drm-map-smoke.sh` requires P010 by default
  (`STOCK_FFMPEG=1` opt-out for unpatched stock FFmpeg), adds `EXPECT_VAAPI`
  driver validation, and passes `--self-test` (rc 0).

## Measurements

Raw logs are local (`.workingdir/evidence/issue-103-rest/`, not committed). The smoke printed `Vulkan device: AMD Ryzen 9 9950X3D 16-Core Processor (RADV RAPHAEL_MENDOCINO)` and `VAAPI driver: Mesa Gallium driver 26.2.4 ... (radeonsi, ...)` on the AMD runs, `Intel(R) Arc(tm) A380 Graphics (DG2)` and `Intel iHD driver ... 26.3.5` on the Intel run.

| run | GPU | P010=1 | rc | result |
|---|---|---|---|---|
| intel-a380 | A380 ANV/iHD renderD130, QSV=1 | yes | 0 | 7/7 PASS incl p010-640x360 bit-exact, qsv-nv12 |
| amd-radv | Radeon 610M RADV/radeonsi renderD129 | yes | 0 | 6/6 PASS incl p010 bit-exact |
| amd-radv-rep1..3 FRAMES=20 | RADV | yes | 0,0,0 | 6/6 each, readback bit-exact |
| control-B1 (2026-10-10 binary, series 0001-0006, no RADV wait) | RADV | no | 1 | FAIL nv12-640x360 readback -> smoke detects the race |
| control-B2 (B1 + RADV wait) | RADV | no | 0 | 6/6 PASS |

Smoke `--self-test`: rc 0.

## Control analysis

1. **Control B1 (without RADV wait):** Built with series 0001–0006 (no patch
   0007). On the Radeon 610M, `nv12-640x360` readback failed due to the
   synchronization race between Vulkan writes and radeonsi reads. This proves
   that `vulkan-drm-map-smoke.sh` reliably detects the synchronization race.
2. **Control B2 (B1 + RADV wait):** Applying patch 0007's CPU wait before map
   allowed all 6 cases to pass with exit code 0.
3. **Current series v0.2.0:** All test cases pass on both Intel (7/7 pass,
   including `p010-640x360` bit-exact and `qsv-nv12`) and AMD (6/6 pass,
   including `p010-640x360` bit-exact). Repetitions with `FRAMES=20` (runs
   `amd-radv-rep1..3`) pass consistently with rc 0 and bit-exact readback.
