<!-- markdownlint-disable MD013 MD060 -->
# DRM-modifier output pools: Vulkan to VAAPI and QSV without hwdownload

**Date:** 2026-10-10

**Decision:** [ADR-0184](../adr/0184-vulkan-drm-modifier-output-pools.md), issue
[#103](https://github.com/VMAFx/pelorus/issues/103) (zero-copy gap ZC-G2 of
[research 0172](0172-zero-copy-audit.md)).

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`) with the 23-patch stack of `master`
at `cb562e2`, then with `tiling=drm`; configured with Vulkan, VAAPI, libdrm
and oneVPL. Intel Arc A380 (`xe`, render node `renderD130`, Mesa ANV 26.2.4,
`iHD` 26.3.5) and AMD Radeon 610M (Raphael iGPU, `renderD129`, Mesa RADV and
radeonsi 26.2.4). Vulkan was derived from the VAAPI device of the same render
node, and every log names the selected device. The host ran other jobs, so no
timing is reported. Raw logs: `.workingdir/evidence/vulkan-drm-modifier/`
(local).

## Verdict

- On master both GPUs fail `pelorus_deband_vulkan,hwmap=derive_device=vaapi`
  with `Unable to export the image as a FD!`.
- With `tiling=drm`, NV12 maps on the Arc A380 with no `hwdownload`: the mapped
  surface reads back bit-exact, and `h264_vaapi`, and `hevc_qsv` through a second
  map to QSV, write the same stream as the `hwdownload` path. 641x361 behaves
  the same.
- P010 needs a stock FFmpeg fix; AMD needs a stock synchronisation fix. Both
  are proven by diagnostic builds. The P010 import row and the export-capability
  format-list fix are queued in VMAFx/ffmpeg-patches; the RADV synchronisation
  needs a root cause first.

## Method

`ffmpeg-patches/test/vulkan-drm-map-smoke.sh` runs each case twice from
`testsrc2`: through the zero-copy map, and through `hwdownload` plus
`hwupload`. It compares per-frame MD5 of a readback of the mapped VAAPI
surface with a readback of the Vulkan frame, then the encoder streams
(bit-exact or PSNR). A Vulkan probe (`probe/modprobe.c`) lists each driver's
modifiers for NV12 and P010 with storage features and the image format query
the pool uses.

## Modifiers the drivers offer (NV12 and P010)

| GPU | Modifier | Memory planes | Storage on plane views | Pool query | `ALIAS_BIT` |
|---|---|---|---|---|---|
| A380 | `DRM_FORMAT_MOD_LINEAR` | 2 | yes | ok, exportable | ok |
| A380 | `I915_FORMAT_MOD_X_TILED` | 2 | yes | ok, exportable | refused |
| A380 | `I915_FORMAT_MOD_4_TILED` | 2 | yes | ok, exportable | refused |
| 610M | four `AMD_FMT_MOD` (tile versions 1 and 3), LINEAR | 2 | yes, except P010 `0x0200000000000a01`, not listed for `R16G16` | ok, exportable | ok |

The driver picked `I915_FORMAT_MOD_4_TILED` on the A380 and
`AMD_FMT_MOD(tile_version=3,tile=27,dcc=0)` (`0x0200000000401b03`) on the 610M.
No driver offered a compression modifier with storage for these formats.

## Results, stock FFmpeg plus the change

| Case | A380 (ANV, iHD) | 610M (RADV, radeonsi) |
|---|---|---|
| master, `hwmap` after deband | fails: `Unable to export the image as a FD!` | fails, same message |
| NV12 640x360, readback | bit-exact, 10 of 10 frames | 14-17 of 20 frames differ (stock defect 2) |
| NV12 640x360, encode vs `hwdownload` | `h264_vaapi` bit-exact | `h264_vaapi` bit-exact (20 frames) |
| NV12 641x361 | readback and `h264_vaapi` bit-exact | not reached (readback fails first) |
| NV12 to QSV (`hevc_qsv -q:v 24`) | bit-exact | not applicable |
| P010 to VAAPI | `DRM format not supported by VAAPI` (stock defect 1) | same |
| P010 pool to DRM PRIME | 10 frames exported | not run separately |
| negative: `drm_modifiers=0x0c00000000000001` | warning names `DRM_FORMAT_MOD_LINEAR`; readback bit-exact; `h264_vaapi` 77.9 dB vs `hwdownload` | readback fails (stock defect 2) |
| malformed `drm_modifiers=zz` | filter fails: `drm_modifiers: cannot parse` | same |

On the A380 the LINEAR surface is encoded slightly differently by `iHD` (77.9
dB, 64 673 against 62 982 bytes) although the readbacks are identical, so the
encoder input is the same pixels. The smoke accepts 50 dB or more for the
stream when it is not bit-exact; the readback check stays exact.

## Stock defects, proven with diagnostic builds

Diagnostic builds changed stock files in a scratch tree only, never the stack.

1. **P010 layer format.** `vulkan_map_to_drm()` names the P010 chroma layer
   `DRM_FORMAT_GR1616` (first match of `vulkan_drm_format_map`), and
   `hwcontext_vaapi.c` `vaapi_drm_format_map` lists P010 and P012 only with
   `DRM_FORMAT_RG1616`. Adding the `GR1616` rows (as NV12 already has for
   `GR88`) makes P010 map, read back and encode (`hevc_vaapi` Main10)
   bit-exact on both GPUs.
2. **AMD acquire synchronisation.** The readback differences on the 610M occur
   with every modifier, LINEAR included. A `realtime` filter between deband and
   `hwmap` (about 33 ms per frame) brings them to 1 of 20. Taking the CPU
   `vkWaitSemaphores()` branch of `vulkan_map_to_drm()` instead of the
   `vulkan_drm_export_sync_fd()` sync_file makes the readback bit-exact (20 of
   20, twice, tiled and LINEAR). With both diagnostic fixes the whole smoke
   passes on both GPUs, P010 included.
3. **Validation.** With `VK_LAYER_KHRONOS_validation`, the master `hwdownload`
   recipe and the same recipe on the changed build report no VUID on either
   GPU. A `tiling=drm` pool reports
   `VUID-VkPhysicalDeviceImageFormatInfo2-tiling-02313` without any map
   (`try_export_flags()` queries a mutable DRM-modifier image without a format
   list), and the map to VAAPI adds
   `VUID-VkImageMemoryBarrier2-srcAccessMask-03909`, on both GPUs. A map to DRM
   PRIME alone adds command-buffer reuse VUIDs (`vkQueueSubmit2` `03874`,
   `03875`, `vkBeginCommandBuffer` `00049`) and
   `VUID-VkSemaphoreGetFdInfoKHR-handleType-03254` from the sync_file export,
   and one such run did not exit under the layer. The Pelorus queries carry the
   format list; no VUID names a Pelorus call.

## Alternative measured: reverse hwmap

`hwmap=derive_device=vaapi:reverse=1` after deband ran on master on both GPUs
(30 frames into `h264_vaapi`): VAAPI allocates the surface and FFmpeg imports it
into Vulkan per plane. Pixel correctness was not measured. ADR-0184 explains why
it is documented, not adopted.

## Limits of this evidence

- One Intel and one AMD GPU, one Mesa and one media driver version each.
- 10 to 20 frames per case at 640x360 and 641x361; 4K and long runs not
  measured.
- `xe` loads no HuC on DG2, so VAAPI and QSV run constant QP (research 0180).
- The AMD encoder streams were bit-exact, but defect 2 makes that timing, not a
  guarantee.
