<!-- markdownlint-disable MD013 MD060 -->
# Vulkan output pools for VAAPI and QSV (DRM format modifiers)

How a Pelorus filter's output reaches a VAAPI or QSV encoder on Linux without
`hwdownload`, and how the filter chooses the memory layout.

## Use it

Add `tiling=drm` to the last Pelorus filter that writes new frames, then map:

```bash
LIBVA_DRIVER_NAME=iHD ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD130 \
    -init_hw_device vulkan=vk@va -filter_hw_device vk -i input.mkv \
    -vf "format=nv12,hwupload,pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi" \
    -c:v h264_vaapi -rc_mode CQP -qp 20 out.mkv
```

Derive Vulkan from the VAAPI device (`vulkan=vk@va`) so both run on the same
GPU; `hwmap=derive_device=vaapi` then reuses that VAAPI device. For QSV, map on
from VAAPI, naming each hardware format so negotiation cannot pick another:

```bash
... pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi,hwmap=derive_device=qsv,format=qsv \
    -c:v hevc_qsv -q:v 24 out.mkv
```

Run with `-v verbose` to see the modifier the pool uses:
`tiling=drm: nv12 640x360 pool uses DRM format modifier 0x0100000000000009 (I915_FORMAT_MOD_4_TILED), chosen by the driver from 3`.

## Options

Filters that write new frames take both options: `pelorus_deband_vulkan`,
`pelorus_denoise_vulkan`, `pelorus_dehalo_vulkan`, `pelorus_aa_vulkan`,
`pelorus_deblock_vulkan`, `pelorus_borderfix_vulkan`.

| Option | Values | Meaning |
|---|---|---|
| `tiling` | `optimal` (default) | FFmpeg's pool: the input frames context when it fits, else `VK_IMAGE_TILING_OPTIMAL`. Cannot be mapped to VAAPI. |
| | `drm` | A pool with `VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT` and DMA-BUF export, which `hwmap` can map to DRM PRIME, VAAPI and, through VAAPI, QSV. |
| `drm_modifiers` | `0x...` list, `\|`-separated; empty (default) | With `tiling=drm`: the modifiers the consumer imports. Empty means every candidate. `drm_modifiers=0x0` asks for a linear surface. |

`analyze`, `grain_estimate` and `mc` pass their input frames through, so their
output is in the upstream pool: put a writing filter with `tiling=drm` last
before `hwmap`.

## Modifier selection rule

Implemented in `ffmpeg-patches/files/pelorus_drm_modifier.h` and
`pelorus_vulkan_pool.h`, tested by the fast test `drm-modifier`
([ADR-0184](../adr/0184-vulkan-drm-modifier-output-pools.md)).

1. The pool's Vulkan formats, usage and flags are the ones FFmpeg picks for
   this device and pixel format (read back from a throwaway OPTIMAL pool),
   without `VK_IMAGE_CREATE_ALIAS_BIT`.
2. A modifier from the driver's list for the pool format is a candidate when
   the exact pool image (usage, flags, view formats, DMA-BUF export, size) is
   supported with it, every plane view format (`R8`, `R8G8`, `R16`, `R16G16`
   for NV12 and P010) lists it with storage-image support, and it has no
   auxiliary memory plane (FFmpeg's DRM export describes one memory plane per
   format plane, so compression modifiers are left out).
3. The candidates are narrowed to `drm_modifiers` when it is set. The driver
   picks one from the result.
4. If nothing is left, the filter substitutes `DRM_FORMAT_MOD_LINEAR` and says
   so in a warning: `tiling=drm: no modifier in drm_modifiers supports storage
   images and DMA-BUF export for nv12; substituting DRM_FORMAT_MOD_LINEAR
   (0x0000000000000000)`.
5. If LINEAR is not a candidate either, or the device lacks
   `VK_EXT_image_drm_format_modifier`, `VK_EXT_external_memory_dma_buf` or
   `VK_KHR_external_memory_fd`, the filter fails. It never allocates OPTIMAL
   tiling when `tiling=drm` was asked for.

## What works today

Measured on an Arc A380 and a Radeon 610M
([research 0184](../research/0184-vulkan-drm-modifier-pools.md),
[research 0103](../research/0103-p010-amd-drm-map.md)):

| Path | Arc A380 (ANV, iHD) | Radeon 610M (RADV, radeonsi) |
|---|---|---|
| NV12 to VAAPI, odd sizes included | bit-exact | bit-exact |
| NV12 to QSV | bit-exact | not applicable |
| P010 to VAAPI | bit-exact | bit-exact |
| P010 to DRM PRIME | exports | exports |

Both stock FFmpeg `n9.0.2` defects are resolved by the shared FFmpeg fix
series `v0.2.0` (pinned in `build-config.env`, [ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md)):

- **P010 to VAAPI.** Stock FFmpeg exported a P010 Vulkan frame as layers `R16` +
  `GR1616`, whereas VAAPI import accepted P010 only as `R16` + `RG1616`
  (`libavutil/hwcontext_vaapi.c`, `vaapi_drm_format_map`). Shared series patch
  0005 adds the `GR1616` row to `vaapi_drm_format_map`, and patch 0006 fixes
  the image format list in the Vulkan export query (`try_export_flags()`).
  P010 maps bit-exact on both Intel and AMD.
- **AMD synchronisation.** On a Radeon 610M (RADV, radeonsi) a VAAPI readback
  of the mapped frame previously showed unfinished Vulkan writes in 14-17 of 20
  frames when relying on the sync_file export branch of `vulkan_map_to_drm()`.
  Shared series patch 0007 resolves this by taking the CPU `vkWaitSemaphores()`
  path before DMA-BUF export on RADV. Measured control: B1 (series 0001-0006,
  no RADV wait) fails `nv12-640x360` readback, while B2 and the current series
  pass all cases (including 3 repetitions of 20 frames with bit-exact readback).
  The AMD warning in `pelorus_vulkan_pool.h` was removed.

## Failure modes

| Symptom | Message | Cause |
|---|---|---|
| Filter fails at start | `tiling=drm needs VK_EXT_image_drm_format_modifier, VK_EXT_external_memory_dma_buf and VK_KHR_external_memory_fd on the device; refusing to allocate OPTIMAL tiling instead` | The Vulkan device lacks a DRM-modifier or DMA-BUF extension. |
| Filter fails at start | `tiling=drm: no DRM format modifier, DRM_FORMAT_MOD_LINEAR included, supports storage images and DMA-BUF export for nv12 on this device; refusing to allocate OPTIMAL tiling instead` | No modifier passes the rule, not even LINEAR. |
| Warning, then the pool is linear | `tiling=drm: no modifier in drm_modifiers supports storage images and DMA-BUF export for nv12; substituting DRM_FORMAT_MOD_LINEAR (0x0000000000000000)` | None of the listed modifiers is usable on this GPU. |
| Filter fails at start | `drm_modifiers: cannot parse "zz" in "0x9\|zz": want at most 64 modifiers, 0x hex or decimal, separated by '\|', not DRM_FORMAT_MOD_INVALID` | Malformed `drm_modifiers`; the first quoted value is the bad entry. |
| Warning only | `drm_modifiers is ignored without tiling=drm` | `drm_modifiers` set while `tiling=optimal`. |
| Warning, then `hwmap` fails with `Failed to map frame: -38` | `tiling=drm: this FFmpeg is built without libdrm, so hwmap cannot map the pool to DRM PRIME or VAAPI` | FFmpeg configured without `--enable-libdrm`. |
| `hwmap` to VAAPI fails for P010 | `DRM format not supported by VAAPI.` then `Failed to map frame` | Stock FFmpeg P010 layer format mismatch (applies only to FFmpeg builds without shared series v0.2.0 patch 0005). |
| `hwmap` fails with `Unable to export the image as a FD!` | (from FFmpeg) | The filter before `hwmap` runs with `tiling=optimal`, or is a pass-through filter (`analyze`, `grain_estimate`, `mc`). |

A device created with `disable_multiplane=1` (the CUDA interop setting) puts
each plane in its own image; VAAPI refuses such two-object frames.

## Verify on your GPU

The smoke script checks the zero-copy pipeline against readback and encode.
Explicitly set `LIBVA_DRIVER_NAME` and `VK_DRIVER_FILES` per GPU so multi-GPU
hosts or environment overrides (such as `LIBVA_DRIVER_NAME=nvidia`) do not load
the wrong driver:

Intel Arc A380 (`renderD130`, `iHD`, `intel_icd.json`, `QSV=1`):

```bash
LIBVA_DRIVER_NAME=iHD \
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/intel_icd.json \
RENDER_NODE=/dev/dri/renderD130 \
EXPECT_DEVICE="Arc" \
EXPECT_VAAPI="Intel iHD" \
QSV=1 \
FFMPEG_BIN=/path/to/ffmpeg \
ffmpeg-patches/test/vulkan-drm-map-smoke.sh
```

AMD Radeon 610M (`renderD129`, `radeonsi`, `radeon_icd.json`):

```bash
LIBVA_DRIVER_NAME=radeonsi \
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.json \
RENDER_NODE=/dev/dri/renderD129 \
EXPECT_DEVICE="RADV" \
EXPECT_VAAPI="radeonsi" \
FFMPEG_BIN=/path/to/ffmpeg \
ffmpeg-patches/test/vulkan-drm-map-smoke.sh
```

The script needs an FFmpeg built with `--enable-libdrm` (as
`ffmpeg-patches/test/build-and-run.sh` does when libdrm is installed). It exits
77 with the reason when the binary or GPU is missing, and `--self-test` checks
its own checks without a GPU.
