<!-- markdownlint-disable MD013 MD060 -->
# Research digest 0150 — Intel Arc B580 and UHD 770 on Windows

Evidence for [ADR-0150](../adr/0150-intel-arc-b580-uhd770-validation.md),
collected on 2026-09-30 with FFmpeg n9.0.2 plus the 18-patch Pelorus stack,
built natively with MSYS2 UCRT64 (gcc 16.2). Every run on this page used the
Vulkan validation layer unless it says otherwise.

## Hosts and tools

| Item | Arc B580 | UHD 770 |
|---|---|---|
| Architecture | Xe2 (Battlemage), discrete | Xe-LP, integrated (i9-12900K) |
| Windows driver | 101.9033, Vulkan 1.4.356 | 101.7092, Vulkan 1.4.323 |
| FFmpeg Vulkan index / DXGI adapter | 0 / 0 | 1 / 1 |
| QSV selector | `qsv=qs:hw_any,child_device=0` | `qsv=qs:hw_any,child_device=1` |
| oneVPL runtime | mfx-gen, API 2.17 | mfx-gen, API 2.15 |
| `maxPerStageDescriptorStorageImages` | 33554432 | **16** |
| `maxComputeSharedMemorySize` | 49152 | 32768 |

Windows 11 Pro 10.0.26300 and Vulkan loader 1.4.350. Two builds of
`VK_LAYER_KHRONOS_validation` were used: MSYS2 1.4.357 and LunarG SDK
1.4.341.1. The layer was selected with `VK_LAYER_PATH` and enabled with
`VK_INSTANCE_LAYERS`, as the format matrix does.

## Proving the layer is active

A missing layer produces no messages, so a clean log proves nothing on its own.
Four positive controls were run.

- **Core validation.** A small program loads `vulkan-1.dll` the same way
  `ffmpeg.exe` does and calls `vkCreateBuffer` with size 0 and usage 0. It gets
  `VUID-VkBufferCreateInfo-size-00912` and `VUID-VkBufferCreateInfo-None-09500`
  on stdout with either layer build on both GPUs. It gets no message without
  the layer.
- **Layer in `ffmpeg.exe`.** Running with `VK_LOADER_DEBUG=layer` prints
  `Insert instance layer "VK_LAYER_KHRONOS_validation"` and
  `Inserted device layer ...` inside the FFmpeg process.
- **Synchronization validation.** `VK_LAYER_VALIDATE_SYNC=1` together with two
  unsynchronized `vkCmdFillBuffer` calls on one range produces
  `SYNC-HAZARD-WRITE-AFTER-WRITE`. The same program without the setting stays
  silent.
- **GPU-assisted validation.** `VK_LAYER_GPUAV_ENABLE=1` makes the layer print
  its settings banner at instance creation (`VALIDATION-SETTINGS`,
  `WARNING-Setting-Limit-Adjusted`). No shader with a deliberate out-of-bounds
  access was run.

The layer writes to stdout when the application registers no messenger. The
matrix and the sweep inspect both stdout and stderr.

## Stock FFmpeg and driver behaviour that affects every Vulkan filter

### Multi-plane host-image-copy corruption (driver)

FFmpeg uses `VK_EXT_host_image_copy` for `hwupload` and `hwdownload` on both
GPUs, because both report ReBAR. With default device options every YUV format
is a single multi-planar `VkImage`. On both GPUs a bare
`hwupload,hwdownload` round trip corrupts every multi-plane YUV format tested
(yuv420p, nv12, p010le, yuv420p10le), and does so with linear tiling too. The
top half of luma and one chroma plane are overwritten by scaled copies of the
image. RGBA, which is single-plane, is exact.

A standalone reproducer (`vk-hostcopy-repro.c`) takes FFmpeg out of the path.
It creates one image and transitions it `UNDEFINED -> GENERAL`, which is the
only layout in `pCopySrcLayouts` and `pCopyDstLayouts` on both drivers. It then
uploads a pattern plane by plane with `vkCopyMemoryToImageEXT` and the
`PLANE_n` aspect, and reads it back with `vkCopyImageToMemoryEXT`. The
validation layer reports **0 diagnostics**. The result:

| Format | Optimal | Linear |
|---|---|---|
| R8_UNORM, R8G8B8A8_UNORM | exact | exact |
| G8_B8_R8_3PLANE_420 | 3072/9216 bytes differ | 3072/9216 bytes differ |
| G8_B8R8_2PLANE_420 | 3577/9216 bytes differ | 3577/9216 bytes differ |
| G16_B16R16_2PLANE_420 | 7153/18432 bytes differ | 7153/18432 bytes differ |

Both drivers give identical numbers, so this is an Intel Windows driver defect.
FFmpeg's separate invalid host transition (below) does not cause it.

The device options that give a byte-exact round trip are:

| Device spec | Arc B580 | UHD 770 |
|---|---|---|
| default | YUV corrupt, RGBA exact | YUV corrupt, RGBA `VK_ERROR_OUT_OF_HOST_MEMORY` |
| `disable_multiplane=1` | exact | `VK_ERROR_OUT_OF_HOST_MEMORY` |
| `linear_images=1` | YUV corrupt | YUV corrupt |
| `linear_images=1,disable_multiplane=1` | exact | exact |

On the UHD 770, `hwupload` fails to allocate any optimal single-plane image
that has host-transfer usage. Filter output pools, which have no host-transfer
usage, allocate normally. A standalone allocation of such an image without
FFmpeg's external-memory export chain succeeds. The export chain is therefore
the suspected trigger, but this was not isolated.

### Two validation messages from stock code

| VUID | Source | Evidence |
|---|---|---|
| `VkFormatProperties2-pNext-pNext` | `vkfmt_from_pixfmt2()` reuses one `VkFormatProperties2 -> VkFormatProperties3` chain for a second query. The driver zeroes the whole `VkFormatProperties3`, header included, for unsupported formats: 72 of 224 formats on the B580 and 46 on the UHD 770, measured without the layer. | Emitted by a bare `hwupload,hwdownload`. |
| `VkHostImageLayoutTransitionInfo-oldLayout-09230` | `vulkan_transfer_host()` host-transitions an image out of `TRANSFER_DST_OPTIMAL`, which is not in `pCopySrcLayouts` (`GENERAL` only). | Emitted by a bare round trip and by stock `hflip`, `gblur` and `avgblur` Vulkan filters. |

Pelorus never calls `vkGetPhysicalDeviceFormatProperties2` or
`vkTransitionImageLayoutEXT`, so both messages come from stock code by entry
point. Synchronization validation reports no hazard for the stock filters.

### Other stock observations

- `hflip_vulkan` in n9.0.2 writes to `size.x - pos.x`, one column off.
- Stock `blackdetect_vulkan` and `scdet_vulkan` forward their input frames but
  advertise a new frames context whenever `ff_vk_filter_init_context()` cannot
  reuse the input one, for example with linear tiling. `hwdownload` then
  rejects the frames with `Input frame is not the in the configured hwframe
  context`.
- n9.0.2 has no Vulkan-to-D3D11 or Vulkan-to-QSV mapping on Windows. The
  hand-off to QSV is `hwdownload` followed by a system-memory encode (shw-1).

## Pelorus defects found and fixed

### Pass-through filters mislabel forwarded frames

`pelorus_analyze_vulkan`, `pelorus_grain_estimate_vulkan` and
`pelorus_mc_vulkan` use the same idiom as the stock pass-through filters and
had the same defect. With linear input every row of these filters failed at
`hwdownload`. Their output link now carries the input link's `hw_frames_ctx`.
After the fix the linear rows and the Vulkan-decode rows pass on both GPUs.
The stock filters still fail.

### Shared-memory variants were not bit-identical at 10 and 12 bit

`tile=1` in denoise and dehalo, and `fast=1` in aa, cache values in shared
memory that the direct path computes inline. At 10 and 12 bit, `sample_scale`
is not a power of two, and the driver can fuse the storage-to-sample product
into a later FMA on one path only.

Before the fix, the 160x96 sweep showed:

- denoise `tile=1` differing from `tile=0` by 1 code on single 10-bit samples,
  on both GPUs;
- aa `fast=1` differing by 1 code on 1 to 3 samples at 10 bit, on the UHD 770.

The fix makes the storage-to-sample product `precise` (SPIR-V NoContraction)
in all three shaders. It also makes the denoise patch-SSD accumulator and the
aa squared magnitude `precise`. With only the product fixed, a 640x360 stress
run (12 noisy frames, all planes) still left 23 to 111 differing bytes in
10-bit denoise on the B580. The accumulator fixed those. After the full fix, the
same stress run in six formats gives:

| Pair | Arc B580 | UHD 770 |
|---|---|---|
| denoise tile (defaults; and patch=3) | identical | identical |
| dehalo tile (two option sets) | identical | identical |
| aa fast (two option sets) | identical | planar formats: 1 to 9 bytes differ (residual below) |

A new matrix row compares direct and tiled denoise at 320x192 in yuv420p10le
and p010le. It fails on the B580 when the two denoise `precise` qualifiers are
reverted, and passes with them.

The fix changes default-path output slightly. At 10 and 12 bit, aa and denoise
move by 1 code on at most 6 samples in a 5-frame 160x96 row. 8-bit output does
not change. One dehalo row (`edge=0:ring=8`, 10 bit, B580) changed 7 samples
by up to 24 codes. Without NoContraction, `a*s - b*s` can be fused so that it
returns a tiny non-zero residue even when `a == b`. With `edge=0` that residue
opens the line-art gate on a flat area. The precise form gives an exact zero,
which is what the algorithm intends.

**Residual, open.** On the UHD 770 in the
`linear_images=1,disable_multiplane=1` mode, aa `fast=1` still differs from
`fast=0` by at most 1 code value, on a few top-row samples, in planar formats
only. It is deterministic and absent in both multi-plane modes and on the B580.
At the one sample examined, the fast result matched the B580. Making every
arithmetic step on the path `precise` did not change it, so the cause is not
float contraction. The documented bit-identity of aa `fast=1` is therefore not
established on this device and mode.

### Denoise exceeded the UHD 770 storage-image limit

Denoise bound seven per-plane storage-image arrays: the current frame, four
previous frames, the output and the next frame. For a 3-plane format that is 21
descriptors, against the UHD 770's limit of 16. The validation layer reported
`VUID-VkPipelineLayoutCreateInfo-descriptorType-03020` in 52 rows. The six
read-only frames are now sampled images read with `texelFetch()`, which leaves
one storage array. The UHD 770 allows 200 sampled images per stage. The fast
gate now fails if any filter's storage-image bindings times 4 planes exceed
16. `texelFetch()` returns the same UNORM value as `imageLoad()`: unselected
planes, which are copied from the fetched texel, remain bit-exact in every
format of the sweep and the matrix.

## Per-GPU results after the fixes

Format matrix (`ffmpeg-patches/test/vulkan-format-matrix.sh`,
`PELORUS_VALIDATE=1`). Device specs: `0,disable_multiplane=1` (B580) and
`1,linear_images=1,disable_multiplane=1` (UHD 770). With both layer builds,
every row passes on both GPUs, and the only diagnostics are the two stock VUIDs
above. Unchanged, the matrix stops at the device probe on stock
`VkFormatProperties2-pNext-pNext`.

The option sweep ran 482 runs per GPU with core and synchronization
validation:

- every documented option of the nine Vulkan filters at a non-default value,
  and at range extremes, in yuv420p, nv12, yuv420p10le and p010le at 160x96;
- defaults in yuv420p12le, p012le, yuv444p and yuv422p10le;
- odd 157x93 frames in yuv420p, nv12 and p010le.

Each run checks:

- exit status and frame count;
- every validation message, recorded verbatim;
- identity for the analyzers and the no-ops;
- `planes=1` chroma identity (PSNR u/v inf) and `planes=2` luma identity;
- zero P010/P012 padding bits;
- side data and metadata;
- the documented bit-identical variants.

| GPU | Before | After |
|---|---|---|
| Arc B580 | 31 failing: 27 harness size errors, 4 denoise tile at 10 bit | 0 failing |
| UHD 770 | 137 failing: 77 pass-through exits, 52 with VUID 03020, 10 tile/fast | 0 failing |

A GPU-assisted-validation subset of 198 runs per GPU covered every filter's
defaults, the tile/fast/lookahead/MC paths, the largest windows and the odd
sizes. It reported no descriptor, bounds or shared-memory-race errors.

## Encoder steering and the H.274 BSF (both GPUs)

Every encode was run twice and all were deterministic.

- **QSV HEVC, `-pelorus_roi 1`.** The output is byte-identical to
  `-pelorus_roi 0`. The mfx-gen runtimes (2.17 and 2.15) return
  `MFX_WRN_INCOMPATIBLE_VIDEO_PARAM` from `MFXVideoENCODE_Query` and clear
  `mfxExtCodingOption3::EnableMBQP` for HEVC CQP, with LowPower unset, on or
  off. They keep it for AVC. FFmpeg does not log that warning, so the dense
  MBQP path is unreachable and the fallback to stock rectangles is silent. The
  stock rectangles do move the encode on the B580: +1.45 dB in the flagged
  half, +3.4% size. On the UHD 770 they produce a bitstream identical to no
  ROI. Under ICQ, ROI has no effect on either GPU. The documented fallback
  warnings for ICQ and H.264 are emitted.
- **av1_qsv.** It has no `pelorus_roi` option, and ROI side data does not
  change its output.
- **libsvtav1, `-pelorus_roi 1`.** It changes the encode (+19.6% size). With
  `-pelorus_roi 0` the output is identical to no ROI.
- **libaom-av1.** libaom 3.15 still rejects `AOME_SET_ROI_MAP` (`res=8`). The
  one-shot warning and the non-fatal fallback work as documented.
- **Scene cut.** `pelorus_mc_vulkan=meta=1` followed by `pelorus_scenecut`
  marks the hard cut. With the `ffmpeg` command line the keyframe appears only
  when `-force_key_frames source` is given, because fftools overwrites
  `frame->pict_type`.
- **grain_estimate `native=1`.** libaom-av1 and libsvtav1 receive
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS` and ignore it (`film_grain_params_present
  = 0`).
- **pelorus_fgs.** It inserts one FGC SEI per access unit with the configured
  fields, and `skip_existing` prevents double stamping. `components=0` keeps
  every NAL unit, but CBS reassembly rewrites 4-byte start codes. FFmpeg's HEVC
  decoder synthesizes grain for `film_grain_model_id=0` and ignores model 1
  (`Unsupported film grain parameters`).

## Reproducers

The scratch scripts for every step are kept with the stream's evidence:

- the canary programs (`vk-validation-canary.c`, `vk-sync-canary.c`);
- `vk-fmtprops3-sweep.c`;
- `vk-hostcopy-repro.c`;
- `vpl-mbqp-query.c`;
- the sweep driver;
- the stress and steering scripts.

The repository gate is the format matrix. Run it on Windows Intel with:

```bash
FFMPEG_BIN=/path/to/ffmpeg.exe VULKAN_DEVICE="0,disable_multiplane=1" \
  PELORUS_VALIDATE=1 ffmpeg-patches/test/vulkan-format-matrix.sh      # Arc B580
FFMPEG_BIN=/path/to/ffmpeg.exe VULKAN_DEVICE="1,linear_images=1,disable_multiplane=1" \
  PELORUS_VALIDATE=1 ffmpeg-patches/test/vulkan-format-matrix.sh      # UHD 770
```
