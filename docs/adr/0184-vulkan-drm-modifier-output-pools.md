<!-- markdownlint-disable MD013 MD060 -->
# ADR-0184: Opt-in DRM-format-modifier output pools so Pelorus frames map to VAAPI and QSV without hwdownload

- **Status**: Proposed
- **Implementation**: pending (#103)
- **Date**: 2026-10-10
- **Deciders**: Lusoris
- **Tags**: vulkan, zero-copy, vaapi, qsv, ffmpeg

## Context

Pelorus Vulkan filters write into pools that `ff_vk_filter_config_output()`
creates (`libavfilter/vulkan_filter.c:99-140` at `n9.0.2`): the input frames
context when it fits, else a new pool with `VK_IMAGE_TILING_OPTIMAL`. FFmpeg
exports a Vulkan frame as DMA-BUF only from a pool with
`VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT` (`libavutil/hwcontext_vulkan.c`
`vulkan_pool_alloc()`, `vulkan_map_to_drm()`), so
`hwmap=derive_device=vaapi` after a Pelorus filter fails with `Unable to
export the image as a FD!` on Intel and AMD, and every Linux VAAPI or QSV
recipe falls back to `hwdownload` (zero-copy audit,
[research 0172](../research/0172-zero-copy-audit.md) sections 1, 2a, 5, gap
ZC-G2). Measured on master: both the Arc A380 (ANV, iHD) and the Radeon 610M
(RADV, radeonsi) fail the map
([research 0184](../research/0184-vulkan-drm-modifier-pools.md)).

Generic FFmpeg changes, edits to stock files such as `vulkan_filter.c` or
`hwcontext_vulkan.c`, do not go into the Pelorus stack; they go to the shared
FFmpeg fix series and upstream (maintainer routing rule). The pool therefore
has to be built in Pelorus code.

Six filters write new frames: `deband`, `denoise`, `dehalo`, `aa`, `deblock`,
`borderfix`. `analyze`, `grain_estimate` and `mc` pass their input frame
through, so their output lives in the upstream pool.

## Decision

1. **Opt-in option.** The six filters gain `tiling` (`optimal`, the default,
   keeps `ff_vk_filter_config_output()`; `drm` builds a DRM-modifier pool) and
   `drm_modifiers` (the modifiers the consumer imports, `|`-separated). One
   header, `ffmpeg-patches/files/pelorus_vulkan_pool.h`, holds the option rows
   and `pel_vk_pool_config_output()`; each filter's output pad calls it. No
   stock FFmpeg file changes; the stack keeps 23 patches.
2. **Pool parameters come from FFmpeg.** A throwaway OPTIMAL frames context is
   initialised and read back for the Vulkan formats, usage and flags that
   `vulkan_frames_init()` picks on this device, because it adds usage bits
   (`TRANSFER_DST`, `HOST_TRANSFER`, `VIDEO_ENCODE_SRC`) that every listed
   modifier must support. `VK_IMAGE_CREATE_ALIAS_BIT` is dropped: ANV refuses
   it on tiled modifiers (`VK_ERROR_FORMAT_NOT_SUPPORTED`, measured) and the
   pool does not use aliasing. The create chain carries
   `VkImageDrmFormatModifierListCreateInfoEXT` and a `VkImageFormatListCreateInfo`
   of the image and plane view formats, owned by the frames context. After
   init the pool must match the read-back exactly, and the Vulkan filter
   context must take it, or the filter fails.
3. **Modifier rule** (`ffmpeg-patches/files/pelorus_drm_modifier.h`, fast test
   `drm-modifier`). A modifier is a candidate when
   `vkGetPhysicalDeviceImageFormatProperties2` accepts the exact pool image
   with it (usage, flags, view formats, DMA-BUF export, size), every plane view
   format lists it with `VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT`, and its memory
   plane count equals the format's plane count. The candidates are intersected
   with `drm_modifiers` (all of them when the list is empty) and passed as a
   list; the driver picks one, and the filter logs which one at verbose level.
4. **Fallback.** An empty intersection substitutes `DRM_FORMAT_MOD_LINEAR`
   when LINEAR is a candidate and says so in a warning naming it. Nothing
   usable, LINEAR included, or a device without
   `VK_EXT_image_drm_format_modifier`, `VK_EXT_external_memory_dma_buf` and
   `VK_KHR_external_memory_fd`, is an error: the filter never allocates OPTIMAL
   when `tiling=drm` was asked for.
5. **Stock defects stay out of this stack.** Three defects found on the way
   are in stock FFmpeg. The P010 import row and the export-capability
   format-list fix are queued in VMAFx/ffmpeg-patches; the RADV map
   synchronisation needs a root cause first (see Consequences). Until it is
   fixed, `tiling=drm` warns on AMD Vulkan drivers (RADV, AMDVLK, AMD
   proprietary) that mapped frames can carry unfinished pixels.
6. **No silent paths.** The filter warns when `drm_modifiers` is set without
   `tiling=drm`, names the offending token of a malformed list, and warns when
   FFmpeg was built without libdrm, where `hwmap` cannot map the pool at all.

The PR that carries this ADR implements it; the Implementation line keeps the
`pending (#103)` form that `scripts/adr/check-status.py` requires of a Proposed
ADR until the status sweep, as for ADR-0180 to ADR-0183.

## Alternatives considered

| Option | Verdict | Evidence |
| --- | --- | --- |
| Automatic `drm` when the next filter is `hwmap` to VAAPI or QSV | Rejected | At `config_output` only `outlink->dst` is known; a `format`, `split` or `null` filter in between hides the consumer, and reading `hwmap`'s private `derive_device` option is a heuristic. The default would also change for every Vulkan-encode and vmafx pipeline, which reuse the input pool today. An explicit option makes "mapping was requested" a fact the filter can refuse to break. |
| DRM tiling as the default for every pool | Rejected for now | Changes the pool of every existing recipe, drops the input-pool reuse that Vulkan decode to encode relies on (research 0172 path E), and the stock export path still raises validation errors (Consequences). Revisit when the upstream fixes land. |
| Patch `ff_vk_filter_config_output()` in `vulkan_filter.c` | Rejected | A stock file: the routing rule sends it upstream. A Pelorus-only header does the same for the six filters. |
| `hwmap=derive_device=vaapi:reverse=1` (no code) | Kept as a documented alternative, not the fix | Runs on master on both GPUs (`reverse-intel.log`, `reverse-amd.log`): VAAPI allocates the surface and FFmpeg imports it into Vulkan per plane. It only works when `hwmap` follows the producing filter directly (it overwrites that link's frames context, which `vf_hwmap.c` itself calls "the naughty bit"), the pool tiling is VAAPI's choice, and pixel correctness was not measured. |
| Pelorus picks one modifier by its own ranking | Rejected | The driver knows its preferred layout; passing the intersection as a list lets it choose (ANV picked `I915_FORMAT_MOD_4_TILED`, RADV `AMD_FMT_MOD(tile_version=3,tile=27,dcc=0)`). The chosen one is logged. |
| Probe the consumer by a trial VAAPI import at `config_output` | Rejected | libva offers no query for importable modifiers (`VASurfaceAttribDRMFormatModifiers` is create-only, `va.h`), so only a trial map would tell, which needs a VAAPI device and frames context inside a Vulkan filter. `drm_modifiers` lets the caller state the consumer's set instead. |
| Allow compression modifiers (aux planes) | Rejected | FFmpeg's DRM export writes one plane per format plane (`vulkan_map_to_drm()`, `aspect_plane = i` for one image), so a metadata plane would be exported wrongly. |

## Consequences

- Intel Arc A380 (ANV, iHD 26.3.5, `xe`): NV12 640x360 and 641x361 map to VAAPI
  without `hwdownload`; the mapped surface reads back bit-exact and
  `h264_vaapi` and `hevc_qsv` (VAAPI then QSV map) write the same stream as the
  `hwdownload` path. The forced LINEAR fallback reads back bit-exact.
- Stock defect 1, P010: Vulkan exports P010 as layers `R16` + `GR1616`;
  `hwcontext_vaapi.c` `vaapi_drm_format_map` accepts only `R16` + `RG1616` for
  P010 and P012, so VAAPI refuses it ("DRM format not supported by VAAPI").
  With a `GR1616` row added in a diagnostic build, P010 maps bit-exact on both
  GPUs. The `tiling=drm` P010 pool itself exports as DRM PRIME. State: the
  import row is queued in VMAFx/ffmpeg-patches.
- Stock defect 2, AMD synchronisation: on RADV with radeonsi, a VAAPI readback
  of the mapped surface sees unfinished Vulkan writes in 14-17 of 20 frames,
  for every modifier, LINEAR included; delaying the map hides it, and forcing
  `vulkan_map_to_drm()` onto its CPU `vkWaitSemaphores()` path instead of the
  sync_file export makes it bit-exact (20 of 20). The encoder streams in the
  same runs were bit-exact, but that is timing, not a guarantee. State: needs a
  root cause (sync_file export or radeonsi's wait on imported fences) before a
  fix; the filter warns on AMD drivers meanwhile.
- Stock defect 3, validation: any DRM-modifier pool triggers
  `VUID-VkPhysicalDeviceImageFormatInfo2-tiling-02313` from
  `try_export_flags()` (no format list in its query), and the export path adds
  `VUID-VkImageMemoryBarrier2-srcAccessMask-03909` plus, on DRM PRIME only
  runs, command-buffer reuse VUIDs (`03874`, `03875`, `00049`) and
  `VUID-VkSemaphoreGetFdInfoKHR-handleType-03254` in
  `vulkan_drm_export_sync_fd()`. The default path is unchanged: master and this
  change report no VUID on the `hwdownload` recipe. State: the
  export-capability format-list fix (`02313`) is queued in
  VMAFx/ffmpeg-patches; the others are observed, without a fix yet.
- `disable_multiplane=1` devices get one image per plane; VAAPI then refuses
  the two-object frame, while DRM PRIME consumers can take it.
- Pass-through filters (`analyze`, `grain_estimate`, `mc`) carry the upstream
  pool; put a writing filter with `tiling=drm` last before `hwmap`.
- Rebase-sensitive: the probe relies on `vulkan_frames_init()` choosing the
  same formats, usage and flags for OPTIMAL and DRM tiling; the post-init
  check fails loudly if a later FFmpeg changes that.

## References

- Issue [#103](https://github.com/VMAFx/pelorus/issues/103) (ZC-G2) and its acceptance list (req); maintainer routing rule: generic FFmpeg changes go to the shared fix series or upstream, not the Pelorus stack (paraphrased, req).
- Research: [0172 zero-copy audit](../research/0172-zero-copy-audit.md) sections 1, 2a, 5, 10; [0184 DRM-modifier pools](../research/0184-vulkan-drm-modifier-pools.md); evidence `.workingdir/evidence/vulkan-drm-modifier/` (local).
- FFmpeg `n9.0.2`: `libavfilter/vulkan_filter.c` (`ff_vk_filter_init_context`, `ff_vk_filter_config_output`), `libavutil/hwcontext_vulkan.c` (`try_export_flags`, `vulkan_pool_alloc`, `vulkan_frames_init`, `vulkan_drm_export_sync_fd`, `vulkan_map_to_drm`, `vulkan_drm_format_map`), `libavutil/hwcontext_vaapi.c` (`vaapi_drm_format_map`, `vaapi_map_from_drm`), `libavfilter/vf_hwmap.c` (reverse mapping).
- Vulkan 1.4 specification: `VK_EXT_image_drm_format_modifier` (`VkImageDrmFormatModifierListCreateInfoEXT`, `VkDrmFormatModifierPropertiesListEXT`), `VUID-VkPhysicalDeviceImageFormatInfo2-tiling-02313`; `drm_fourcc.h` (libdrm) modifier codes.
