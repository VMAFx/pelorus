<!-- markdownlint-disable MD013 -->
# Rebase notes

Re-apply / re-test work created for the FFmpeg patch stack after an upstream
FFmpeg bump or a `libpelorus` ABI change. One entry per change that affects the
patches (ADR-0108 deliverable #6).

## v0.1.0 — initial stack (base n8.1.1)

- **Patch**: `ffmpeg-patches/0001-add-vf_pelorus_deband_vulkan.patch`.
- **Touches**: `libavfilter/vf_pelorus_deband_vulkan.c` (new),
  `libavfilter/allfilters.c` (extern), `libavfilter/Makefile` (OBJS),
  `configure` (`pelorus_deband_vulkan_filter_deps="vulkan spirv_library
  libpelorus"` + a soft `check_pkg_config libpelorus`).
- **Consumes from libpelorus**: `pelorus/interop.h` (`pel_blob_pack`,
  `pel_blob_free`, `PelorusSideData`, `PelorusBandingSection`, `PEL_FOURCC`),
  `pelorus/deband.h` (`PEL_DEBAND_FLAG_*`). A change to any of these requires
  regenerating this patch in the same PR.
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (full series
  replay onto a pristine base, build, smoke `-h filter=pelorus_deband_vulkan`).
- **Known reflow risk**: `configure`'s `*_filter_deps` block and the
  `check_pkg_config` block, and `allfilters.c`'s alphabetical extern list, are
  the hunks most likely to fuzz on an upstream bump. Regenerate with
  `generate.sh` against the new base tag rather than hand-resolving.

## v0.1.0 — patch 0002 (analyze; cumulative on 0001)

- **Patch**: `ffmpeg-patches/0002-add-vf_pelorus_analyze_vulkan.patch`.
- **Touches**: `libavfilter/vf_pelorus_analyze_vulkan.c` (new) + the same
  registration files as 0001 (extern/OBJS/deps/require), inserted *ahead* of the
  deband entries (analyze sorts first alphabetically) — so 0002's context lines
  reference 0001's added lines. **Cumulative**: it only applies on top of 0001.
- **Consumes from libpelorus**: `pelorus/interop.h` (`pel_blob_pack`,
  `pel_blob_free`, `PelorusSideData`, `PelorusVarianceSection`,
  `PelorusBandingSection`, `PEL_FOURCC`, `PEL_LAYOUT_*`).
- **Consumes from FFmpeg**: the `vf_scdet_vulkan` readback API surface
  (`ff_vk_get_pooled_buffer`, `ff_vk_exec_*`, `ff_vk_create_imageviews`,
  `ff_vk_shader_update_img_array`/`_desc_buffer`/`_push_const`,
  `ff_vk_frame_barrier`, `FFVkBuffer.mapped_mem`). A change to that surface on
  an upstream bump can break the readback path — re-test by replaying the stack.
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay
  0001+0002, build, smoke `-h filter=pelorus_analyze_vulkan`).
- **Frozen control-plane AVOptions (ADR-0110)**: the deband `AVOption` names and
  ranges `range`, `thry`, `thrc`, `grainy`, `grainc`, `softness`, `detail`,
  `dither`, `dynamic`, `protect` are a stable contract vmafx's `vmaf-tune` hard-codes
  against. A rebase/regeneration must **not** rename or narrow these; a deliberate
  break is a coordinated two-repo PR. See
  [docs/api/control-plane.md](api/control-plane.md). `sample`, `blur`, `planes`,
  and `meta` are out-of-contract and free to evolve.

## v0.1.0 — patch 0005 (qsv ROI; cumulative on 0001–0004)

- **Patch**: `ffmpeg-patches/0005-qsv-pelorus-roi.patch`
  (source-of-truth `ffmpeg-patches/files/qsv-pelorus-roi.patch`).
- **Touches** (libavcodec edit, *not* a filter — hand-maintained unified diff in
  `files/`, applied as its own commit by `generate.sh`):
  `libavcodec/qsvenc.h` (`pelorus_roi` + `qp_delta_map*` ctx fields,
  `QSV_HAVE_MBQP` guard), `libavcodec/qsvenc.c` (`EnableMBQP` init probe,
  `qsvenc_setup_roi()` rasterizer, scratch free in close),
  `libavcodec/qsvenc_h264.c` + `qsvenc_hevc.c` (the `pelorus_roi` AVOption).
- **Consumes from FFmpeg/oneVPL**: `AV_FRAME_DATA_REGIONS_OF_INTEREST`
  (`AVRegionOfInterest`, `self_size`/`qoffset`), the `mfxEncodeCtrl` ext-buffer
  chain (`enc_ctrl->ExtParam`/`NumExtParam`, `QSV_MAX_ENC_EXTPARAM`,
  `free_encoder_ctrl`), and `mfxExtMBQP` / `MFX_EXTBUFF_MBQP` /
  `MFX_MBQP_MODE_QP_DELTA` / `mfxExtCodingOption3::EnableMBQP`. A oneVPL/MSDK
  header bump that renames or repacks `mfxExtMBQP` or relocates `EnableMBQP`
  would break compilation — re-verify with a `cc -fsyntax-only` of the QSV TUs.
- **Known reflow risk**: the init anchor is the `extco3.Header.BufferId =
  MFX_EXTBUFF_CODING_OPTION3` block and the `set_encode_ctrl_cb` call site in
  `encode_frame`; the AVOption anchor is `QSV_COMMON_OPTS` / `QSV_OPTION_RDO` in
  both codec tables. Upstream churn in those areas can fuzz the hunks — regenerate
  with `generate.sh` against the new base rather than hand-resolving.
- **Re-test after rebase**: full series replay via
  `ffmpeg-patches/test/build-and-run.sh`; smoke `ffmpeg -h encoder=hevc_qsv |
  grep pelorus_roi` once built against a QSV-enabled toolchain.
- **Capability/portability invariants (keep on regeneration)**: 16×16 MBQP block
  alignment for both AVC and HEVC (SDK-documented; not a GPU coding-tree size);
  `EnableMBQP` only under CQP rate control (probe + one-shot warn + pass-through);
  `QSV_HAVE_MBQP` compile guard (oneVPL/MSDK ≥ 1.13); default OFF.

## v0.1.0 — patch 0006 (grain_estimate; cumulative on 0001–0005)

- **Patch**: `ffmpeg-patches/0006-add-vf_pelorus_grain_estimate_vulkan.patch`.
  Committed by `generate.sh` *after* the nvenc (0004) and qsv (0005) encoder
  patches, so it lands as 0006 and does **not** renumber a shipped artifact.
- **Touches**: `libavfilter/vf_pelorus_grain_estimate_vulkan.c` (new) + the same
  registration files as the other filters (extern/OBJS/deps/require), inserted
  *after* the denoise entries (grain sorts last alphabetically among the
  `pelorus_*` filters: deband < denoise < grain_estimate). **Cumulative**: applies
  only on top of 0001–0005.
- **Consumes from libpelorus**: `pelorus/interop.h` (`pel_blob_pack`,
  `pel_blob_free`, `PelorusSideData`, `PelorusFilmGrainSection`,
  `PEL_SEC_FILMGRAIN`, `pel_grain_model`, `PEL_FOURCC`, `PEL_LAYOUT_*`). No ABI
  bump — the `PEL_SEC_FILMGRAIN` section was already reserved. A change to these
  requires regenerating this patch in the same PR.
- **Consumes from FFmpeg**: the `vf_pelorus_analyze` readback surface
  (`ff_vk_get_pooled_buffer`, `ff_vk_exec_*`, `ff_vk_create_imageviews`,
  `ff_vk_shader_update_img_array`/`_desc_buffer`/`_push_const`,
  `ff_vk_frame_barrier`, `FFVkBuffer.mapped_mem`) **and** the film-grain side-data
  API `libavutil/film_grain_params.h` (`av_film_grain_params_create_side_data`,
  `AVFilmGrainAOMParams`, `AV_FILM_GRAIN_PARAMS_AV1`). A change to either on an
  upstream bump can break the estimate emit — re-test by replaying the stack.
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay
  0001–0006, build, smoke `-h filter=pelorus_grain_estimate_vulkan`).
- **Shader lockstep**: `libpelorus/shaders/pelorus_grain_estimate.comp` and the
  filter's inline GLSL implement the same per-band HF-residual estimator; edit
  both together (AGENTS hard rule 4).

## v0.1.0 — patch 0007 (mc; cumulative on 0001–0006)

- **Patch**: `ffmpeg-patches/0007-add-vf_pelorus_mc_vulkan.patch`.
- **Numbering**: `vf_pelorus_mc_vulkan` is committed **last** in `generate.sh`
  (after the nvenc/qsv encoder patches and the grain filter) so it lands as
  **0007**; the shipped `0004-nvenc-pelorus-roi.patch` and
  `0005-qsv-pelorus-roi.patch` keep their numbers — adding `mc` does **not**
  renumber an existing artifact. `generate.sh` uses a standalone `mc` commit
  block (drop file + inject registration) after the grain filter commit.
- **Touches**: `libavfilter/vf_pelorus_mc_vulkan.c` (new) + the same registration
  files as the other filters — `allfilters.c` (extern, inserted *before*
  `ff_vf_perms`: `pelorus_mc` sorts after `pelorus_denoise`, before `perms`),
  `libavfilter/Makefile` (OBJS, after the denoise line), `configure`
  (`pelorus_mc_vulkan_filter_deps="vulkan spirv_library"` + the
  `require_pkg_config libpelorus … add_extralibs` line, after the denoise entry).
  **Cumulative**: applies only on top of 0001–0006.
- **Consumes from libpelorus**: `pelorus/interop.h` (`pel_blob_pack`,
  `pel_blob_free`, `pelorus_sidedata_uuid`/`PELORUS_SIDEDATA_UUID_LEN`,
  `PelorusSideData`, `PelorusSectionDir`, `PelorusMotionSection`, `PEL_SEC_MOTION`,
  `PEL_FOURCC`, `PEL_LAYOUT_*`). The MV grid is appended after the packed blob and
  the section's `mv_field_offset`/`mv_field_size` + `total_size` are patched in
  place — this reads `PelorusSideData.header_size` and `PelorusSectionDir.offset`,
  so a layout change to either struct (forbidden by the append-only ABI) would
  break the grid-append path; re-test if interop.h grows a header field.
- **Consumes from FFmpeg**: the denoise readback + multi-frame exec surface
  (`ff_vk_get_pooled_buffer`, `ff_vk_exec_*`, `ff_vk_create_imageviews`,
  `ff_vk_shader_update_img_array`/`_desc_buffer`/`_push_const`,
  `ff_vk_frame_barrier`, `FFVkBuffer.mapped_mem`, `av_frame_clone` for the
  1-frame causal reference). A change to that surface on an upstream bump can
  break the dispatch/readback — re-test by replaying the stack.
- **Shader lockstep**: `libpelorus/shaders/pelorus_mc.comp` and the filter's inline
  GLSL implement the same block-match ME; the filter's `PEL_MC_BLOCK_DIM`/
  `PEL_MC_SAD_SCALE` and `s_sad[1024]` size must track the `.comp` (AGENTS rule 4).
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay
  0001–0007, build, smoke `-h filter=pelorus_mc_vulkan`).

## v0.1.0 — patch 0008 (nvenc ME hints; cumulative on 0001–0007)

- **Patch**: `ffmpeg-patches/0008-nvenc-pelorus-me-hints.patch` (hand-maintained
  libavcodec diff in `files/nvenc-pelorus-me-hints.patch`, applied by
  `generate.sh` as its own commit after the `mc` filter — same model as the
  NVENC/QSV ROI patches, NOT a filter drop-in).
- **Numbering**: committed **last** in `generate.sh` so it lands as **0008**;
  no existing artifact renumbers. **Cumulative**: applies only on top of
  0001–0007 — it depends on 0007 (the `vf_pelorus_mc_vulkan` producer that emits
  `PEL_SEC_MOTION`) and shares the `NvencContext`/`nvenc_setup_rate_control`/
  `nvenc_send_frame` regions the **0004** NVENC ROI patch already touched.
- **Touches**: `libavcodec/nvenc.h` (a `NVENC_HAVE_EXTERNAL_ME_HINTS` guard added
  beside `NVENC_HAVE_QP_MAP_MODE` under the SDK-8.1 version check; new
  `NvencContext` fields after the ROI block: `pelorus_me_hints` + a guarded
  `me_hints`/`me_hints_count`/`_w`/`_h`/`_warned` block),
  `libavcodec/nvenc.c` (an inline Pelorus-blob reader + `nvenc_setup_me_hints`
  before `nvenc_send_frame`; an init block in `nvenc_setup_rate_control` after the
  ROI block; an `av_freep` in `ff_nvenc_encode_close`; a hook in `nvenc_send_frame`
  after the ROI hook), `libavcodec/nvenc_h264.c` + `libavcodec/nvenc_hevc.c` (the
  `pelorus_me_hints` AVOption, inserted after the `pelorus_roi` option 0004 added,
  before `b_adapt`). **AV1 is intentionally NOT given the option** — its external
  hints use a different per-superblock struct.
- **Consumes from FFmpeg/ffnvcodec**: `NVENC_EXTERNAL_ME_HINT`,
  `NVENC_EXTERNAL_ME_HINT_COUNTS_PER_BLOCKTYPE`,
  `NV_ENC_INITIALIZE_PARAMS::enableExternalMEHints`/`maxMEHintCountsPerBlock`,
  `NV_ENC_PIC_PARAMS::meExternalHints`/`meHintCountsPerBlock`,
  `NV_ENC_CAPS_SUPPORT_MEONLY_MODE`, `av_frame_get_side_data`,
  `AV_FRAME_DATA_SEI_UNREGISTERED`, `nvenc_check_cap`. A rename/struct change in
  the ffnvcodec header on an SDK bump (e.g. a reserved-field reshuffle in
  `NV_ENC_PIC_PARAMS`) can break the per-frame setup — re-test by replaying the
  stack and rebuilding `libavcodec/nvenc.o` against the new headers.
- **No libpelorus link**: unlike the producer (0007), this patch does **not**
  link `libpelorus` into `libavcodec`. It replicates the minimum blob parse
  inline (UUID + magic + `abi_major` + the `PelorusSectionDir` walk +
  `mv_field_offset`/`mv_field_size`). The byte offsets it hardcodes —
  `PelorusSideData` fields (`total_size`@12, `section_mask`@16, `section_count`@20,
  `header_size`@22, `grid_cols`@32, `grid_rows`@34), `PelorusSectionDir` (16-byte
  stride: id@0, offset@4, size@8), and `PelorusMotionSection` (mv_field_offset@20,
  mv_field_size@24) — track the frozen append-only interop ABI. A layout change to
  any of those structs (forbidden by interop.h R1/R2) would silently break this
  reader; re-test if interop.h ever grows a header field or reorders a section.
- **Re-test after rebase**: a full nvenc build + on-HW encode-speed A/B is the
  pending follow-up; for the patch-stack gate, replay 0001–0008
  (`git am --3way`) and rebuild the nvenc TUs against the ffnvcodec headers
  (`./configure --enable-nonfree --enable-nvenc … && make libavcodec/nvenc.o
  libavcodec/nvenc_h264.o libavcodec/nvenc_hevc.o`).

## v0.1.0 — patch 0009 (vulkan QP-map; cumulative on 0001–0008)

- **Patch**: `ffmpeg-patches/0009-vulkan-pelorus-qpmap.patch`
  (source-of-truth `ffmpeg-patches/files/vulkan-pelorus-qpmap.patch`). A
  libavcodec edit, *not* a filter — hand-maintained diff applied by `generate.sh`
  as its own commit after the encoder/filter patches. **Cumulative on 0001–0008.**
- **Touches** (no filter registration): `libavcodec/vulkan_encode.h` (`pelorus_roi`
  in `FFVkEncodeCommonOptions`; the `qpmap_*` state in `FFVulkanEncodeContext`,
  now including `qpmap_gpu`/`qpmap_shader_ready`/`qpmap_shd`/`qpmap_roi_pool`; the
  `PelorusQpRect` struct + `PELORUS_QPMAP_MAX_RECTS`; the `-pelorus_roi` AVOption
  in the shared `VULKAN_ENCODE_COMMON_OPTIONS` macro — this is what exposes it on
  `h264_vulkan`/`hevc_vulkan`/`av1_vulkan` at once),
  `libavcodec/vulkan_encode.c` (a `#include "libavutil/vulkan_spirv.h"` under the
  extension guard; the `pelorus_qpmap_*` probe/image/build_rects/build_shader/
  dispatch/upload/uninit block; the probe + on-GPU-vs-host decision after
  `init_rc`; the map bind after `CmdBeginVideoCodingKHR`; the session-create
  `ALLOW_ENCODE_*_MAP_BIT`; the session-params texel-size pNext). The three
  `vulkan_encode_{h264,h265,av1}.c` tables are **not** touched — they inherit the
  option from the shared macro (the rebase-fragile spot to watch).
- **On-GPU raster (the Tier-2 follow-up, now landed)**: the map texel image is
  filled by a compute dispatch — `pelorus_qpmap_build_shader` builds the inline
  GLSL via the SPIR-V compiler and registers it against `enc_pool`;
  `pelorus_qpmap_dispatch` uploads the coalesced ROI rect list to a pooled SSBO
  and `imageStore`s the map (GENERAL layout) before the GENERAL→
  VIDEO_ENCODE_QUANTIZATION_MAP barrier. It records on the **encode** command
  buffer, so it is gated on the encode queue family advertising
  `VK_QUEUE_COMPUTE_BIT` (probed: `ctx->qf_enc->flags & VK_QUEUE_COMPUTE_BIT`); the
  map image then needs `VK_IMAGE_USAGE_STORAGE_BIT` and stays EXCLUSIVE (one
  family, no ownership transfer). When the encode queue lacks compute or the
  shader fails to build, `qpmap_gpu` clears and the host raster + staging-copy
  path (`pelorus_qpmap_upload`) runs instead — functionally identical, same
  `qoffset`→ΔQP / reverse-scan convention.
- **Consumes from FFmpeg**: the Vulkan-Video encode surface + `VK_KHR_video_encode_quantization_map`
  (`VkVideoEncodeQuantizationMapInfoKHR`, the delta/emphasis capability flags,
  `VkVideoFormatQuantizationMapPropertiesKHR`) **and** the Vulkan compute-shader
  surface for the on-GPU raster (`ff_vk_spirv_init`/`FFVkSPIRVCompiler`,
  `ff_vk_shader_init`/`_add_descriptor_set`/`_add_push_const`/`_link`/
  `_register_exec`/`_free`, `ff_vk_exec_bind_shader`,
  `ff_vk_shader_update_img`/`_desc_buffer`/`_push_const`, `ff_vk_get_pooled_buffer`,
  `ff_vk_exec_add_dep_buf`, the GLSLC macros). A change to that shader surface on
  an upstream bump can break the dispatch — re-test by rebuilding the TU. Entire
  path `#ifdef VK_KHR_video_encode_quantization_map`; gated `-std=c17` clean.
- **Shader lockstep**: `libpelorus/shaders/pelorus_qpmap.comp` and the patch's
  inline GLSL implement the same per-texel reverse-scan rasterizer; edit both
  together (AGENTS hard rule 4). The reference `.comp` declares explicit `r8i`/`r8`
  formats; the inline copy uses FFmpeg's descriptor machinery (format-less
  writeonly storage images + an anonymous push block), as the other Pelorus
  shaders do — the `main()` body is what must match byte-for-byte.
- **Invariant**: one map image **per exec-pool slot**, round-robined per bound
  frame (a single shared image races at `async_depth>1`); keep this on regeneration.
- **Re-test after rebase**: replay 0001–0009; rebuild the `vulkan_encode`,
  `vulkan_encode_h264`, `vulkan_encode_h265`, `vulkan_encode_av1` TUs (the
  on-GPU raster is a real compile, not just `-fsyntax-only`); compile the
  reference shader (`glslangValidator`) and the inline GLSL. On-HW A/B blocked on
  a driver advertising the extension + encode-feedback flags (see ADR-0114 Tier 2).

## v0.1.0 — patch 0010 (pelorus_fgs BSF; cumulative on 0001–0009)

- **Patch**: `ffmpeg-patches/0010-add-pelorus_fgs_bsf.patch`. A **bitstream
  filter**, not an AVFilter: drops `libavcodec/bsf/pelorus_fgs.c` (canonical source
  `files/h265_pelorus_fgs_bsf.c`) and registers via the libavcodec surfaces —
  `bitstream_filters.c` (extern, before `ff_pgs_frame_merge_bsf`), `bsf/Makefile`
  (OBJS, before `prores_metadata`), `configure` (`pelorus_fgs_bsf_select="cbs_h265"`,
  before `smpte436m_to_eia608_bsf_select`; the BSF is auto-discovered by
  `find_things`). **Cumulative on 0001–0009.**
- **Does NOT link libpelorus**: consumes the H.274 model via AVOptions, built on
  CBS (`cbs_h265`), so no `require_pkg_config` hunk. Inserts an H.274 FGC SEI
  (`H265_SEI_TYPE_FILM_GRAIN_CHARACTERISTICS`) per access unit; `components=0` is
  byte-identical pass-through; caches SPS colour fields the FGC SEI omits.
- **Re-test after rebase**: replay 0001–0010; build with `--enable-bsf=pelorus_fgs`
  (auto), smoke a `pelorus_fgs`-filtered HEVC stream and confirm the FGC SEI via
  `trace_headers`. AV1 round-trips via native side data (no BSF); H.264/VVC legs
  are follow-ups.

## v0.1.0 — patch 0011 (nvenc AV1 film grain; cumulative on 0001–0010)

- **Patch**: `ffmpeg-patches/0011-nvenc-pelorus-film-grain.patch` (hand-maintained
  libavcodec diff in `files/nvenc-pelorus-film-grain.patch`, applied by
  `generate.sh` as its own commit after the `pelorus_fgs` BSF — same model as the
  NVENC/QSV ROI + ME-hint patches, NOT a filter drop-in).
- **Numbering**: committed **last** in `generate.sh` so it lands as **0011**; no
  existing artifact renumbers (only the `[PATCH NN/10]`→`[PATCH NN/11]` Subject
  count changes across the series). **Cumulative on 0001–0010** — depends on 0006
  (the `vf_pelorus_grain_estimate_vulkan` producer that emits `PEL_SEC_FILMGRAIN`
  and the native AV1 grain side data) and shares the `NvencContext` /
  `nvenc_send_frame` regions the **0004** ROI and **0008** ME-hint patches touched.
- **Touches**: `libavcodec/nvenc.h` (a `NVENC_HAVE_AV1_FILM_GRAIN` macro under the
  SDK-12.0 version check + two `NvencContext` fields: `pelorus_film_grain` and a
  guarded `NV_ENC_FILM_GRAIN_PARAMS_AV1 fg_params` + `fg_warned`),
  `libavcodec/nvenc.c` (an enable block in `nvenc_setup_av1_config`; the
  `pel_fg_from_aom` / `pel_fg_from_interop` / `nvenc_setup_film_grain` helpers
  before the ROI block; a call in `nvenc_send_frame` after the ME-hint call),
  `libavcodec/nvenc_av1.c` (the `pelorus_film_grain` AVOption, after the
  `pelorus_roi` option 0004 added, before `b_adapt`). **`av1_nvenc` only** — H.264
  and HEVC NVENC carry no AV1 film grain, so they are intentionally NOT given the
  option.
- **Rebase-fragile spots**: (1) the enable block anchors on the AV1-unique
  `av1->numFwdRefs`/`av1->numBwdRefs` pair — do **not** anchor on the
  temporal-filter tail, which the H.264/HEVC/AV1 configs *share* (an earlier draft
  mis-landed the AV1-only `av1->enableFilmGrainParams` block inside
  `nvenc_setup_h264_config`, a compile error); (2) the `nvenc_send_frame` hook sits
  after the `NVENC_HAVE_EXTERNAL_ME_HINTS` setup call (patch 0008), so 0011 depends
  on 0008's context window.
- **Consumes from ffnvcodec**: `NV_ENC_FILM_GRAIN_PARAMS_AV1`,
  `NV_ENC_CONFIG_AV1::enableFilmGrainParams`/`filmGrainParams`,
  `NV_ENC_PIC_PARAMS_AV1::filmGrainParamsUpdate`/`filmGrainParams` (whole path
  `#ifdef NVENC_HAVE_AV1_FILM_GRAIN`, SDK 12.0+). A struct/field rename on an SDK
  bump can break the mapping — re-test by replaying and rebuilding the nvenc TUs.
- **Consumes from FFmpeg**: `libavutil/film_grain_params.h` (`AVFilmGrainParams`,
  `AVFilmGrainAOMParams`, `AV_FILM_GRAIN_PARAMS_AV1`, `codec.aom`),
  `av_frame_get_side_data`, `AV_FRAME_DATA_FILM_GRAIN_PARAMS`,
  `AV_FRAME_DATA_SEI_UNREGISTERED`.
- **No libpelorus link**: prefers the native `AV_FRAME_DATA_FILM_GRAIN_PARAMS`
  channel; parses `PEL_SEC_FILMGRAIN` inline as a fallback. The byte offsets it
  hardcodes track the frozen `PelorusFilmGrainSection` (interop.h, 216 bytes:
  `num_y_points`@8, `num_uv_points`@12, `scaling_shift`@20, `ar_coeff_lag`@24,
  `ar_coeff_shift`@28, `grain_scale_shift`@32, `uv_mult`@36, `uv_mult_luma`@44,
  `uv_offset`@52, `apply`@60, `chroma_scaling_from_luma`@61, `overlap_flag`@62,
  `limit_output_range`@63, `y_points`@64, `uv_points`@92, `ar_coeffs_y`@132,
  `ar_coeffs_uv`@156) plus the `PelorusSideData` header and `PelorusSectionDir`
  walk. A layout change to any of those structs (forbidden by interop.h R1/R2)
  would silently break the reader; re-check if interop.h grows an appended field.
- **Re-test after rebase**: replay 0001–0011 (`git am --3way`); rebuild the nvenc
  TUs against the ffnvcodec headers (`./configure --enable-nonfree --enable-nvenc
  --enable-ffnvcodec --enable-encoder=av1_nvenc,hevc_nvenc,h264_nvenc … && make
  libavcodec/nvenc.o libavcodec/nvenc_av1.o`, warning-clean); build `ffmpeg` and
  confirm `-h encoder=av1_nvenc` shows `-pelorus_film_grain`, absent from
  `hevc_nvenc`/`h264_nvenc`. On-HW grain-match / BD-rate is a follow-up
  (ADR-0118 / ADR-0111); needs an SDK-12.0+ driver.

## v0.1.0 — patch 0012 (libaom-av1 ROI; cumulative on 0001–0011)

- **Patch**: `ffmpeg-patches/0012-libaom-pelorus-roi.patch` (hand-maintained
  libavcodec diff in `files/libaom-pelorus-roi.patch`, applied by `generate.sh`
  as its own commit after the NVENC AV1 film-grain patch — same model as the
  NVENC/QSV ROI patches, NOT a filter drop-in).
- **Numbering**: committed **last** in `generate.sh` so it lands as **0012**; no
  existing artifact renumbers. **Independent of the encoder-specific patches** —
  touches only `libavcodec/libaomenc.c`, which nothing else in the stack edits,
  so it is reorder-tolerant within the libavcodec group. It does depend
  conceptually on the **0002** `vf_pelorus_analyze` ROI producer (the source of
  the `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data), but not on its source text.
- **Touches**: `libavcodec/libaomenc.c` only — five hunks: (1) five
  `AOMContext` fields (`pelorus_roi`, `roi_seg_map`, `roi_mi_rows`,
  `roi_mi_cols`, `roi_warned`) after `aom_params`; (2) `roi_seg_map` free +
  grid reset in `aom_free` after `ff_dovi_ctx_unref`; (3) the grid allocation in
  `aom_init` anchored **after `set_color_range(avctx);`** (a unique, stable
  anchor); (4) the `pelorus_aom_*` bridge helpers + the `pelorus_aom_apply_roi`
  call, inserted before `static int aom_encode(`, with the per-frame hook after
  the `add_hdr_plus` call inside the `if (frame)` block; (5) the `pelorus_roi`
  AVOption after `enable-smooth-interintra`, before the
  `#if AOM_ENCODER_ABI_VERSION >= 23 { "aom-params" ... }` block.
- **Rebase-fragile spots**: (1) the `aom_init` allocation anchors on
  `set_color_range(avctx);` followed by `AV1E_SET_SUPERBLOCK_SIZE` — if upstream
  reorders the init codecctl sequence, re-pick a unique anchor inside `aom_init`;
  (2) the AVOption insertion is between `enable-smooth-interintra` and the
  ABI-gated `aom-params` entry — keep it outside the `#if`; (3) the bridge does
  **not** link any new lib (it uses only `aom/aomcx.h` already pulled in via
  `libaom.h`), so no `require_pkg_config` / `add_extralibs` configure hunk is
  needed, unlike the Vulkan filters.
- **Consumes from aom**: `AOME_SET_ROI_MAP`, `aom_roi_map_t`,
  `AOM_MAX_SEGMENTS` (`aom/aomcx.h`). A struct/enum change on an aom version bump
  could break the mapping — re-replay and rebuild `libavcodec/libaomenc.o`.
- **Consumes from FFmpeg**: `AV_FRAME_DATA_REGIONS_OF_INTEREST`,
  `AVRegionOfInterest` (`self_size`/`top`/`bottom`/`left`/`right`/`qoffset`),
  `av_frame_get_side_data`, `av_clip`/`av_clipf`/`lrintf`.
- **Known upstream limitation (verified, not a patch defect)**: on libaom
  3.14.1, `AOME_SET_ROI_MAP` returns `AOM_CODEC_INVALID_PARAM` in FFmpeg's
  encoder configuration (the control is wired only for the RTC delta-q path
  upstream), so the map is currently ignored and the bridge degrades gracefully
  (one warning, no bias). It becomes effective for free once a libaom release
  enables the control on the path FFmpeg uses; re-run the analyze→libaom A/B and
  capture the CAMBI gain then. See ADR-0120.
- **Re-test after rebase**: replay 0001–0012 (`git am --3way`); rebuild
  `libavcodec/libaomenc.o` (`./configure --enable-gpl --enable-libaom … && make
  libavcodec/libaomenc.o`, warning-clean); build `ffmpeg` and confirm
  `-h encoder=libaom-av1` shows `-pelorus_roi`. On-HW: `analyze roi=1 →
  libaom-av1 -pelorus_roi 1` on a banding clip must not crash and must produce
  valid output (the quality gain awaits the upstream fix above).
## v0.1.0 — patch 0013 (svtav1 ROI; cumulative on 0001–0011)

- **Patch**: `ffmpeg-patches/0013-svtav1-pelorus-roi.patch` (hand-maintained
  unified diff `ffmpeg-patches/files/svtav1-pelorus-roi.patch`, applied as its own
  commit by `generate.sh`). ADR-0121.
- **Touches** (one file): `libavcodec/libsvtav1.c` — the `pelorus_roi` AVOption +
  ROI ctx fields (`roi_evts` list, `roi_b64_cols/rows`) in `SvtContext`; an
  `enable_roi_map` enable block in `eb_enc_init` (before
  `svt_av1_enc_set_parameter`); the `svtav1_build_roi_evt()` rasterizer + the
  `EbPrivDataNode`/`ROI_MAP_EVENT` attach in `eb_send_frame` (around
  `svt_av1_enc_send_picture`); the event free-list teardown in `eb_enc_close`.
- **Numbering**: committed **last** in `generate.sh` so it lands as **0012**; it
  touches only `libsvtav1.c`, so it has no dependency on the other encoder
  patches, but it relies on **0002** (`vf_pelorus_analyze`) to *produce* the ROI
  side data it consumes — keep it after 0002 in the series.
- **Consumes from SVT-AV1** (`<EbSvtAv1Enc.h>` / `<EbSvtAv1.h>`):
  `EbSvtAv1EncConfiguration::enable_roi_map`, `SvtAv1RoiMapEvt`
  (`b64_seg_map`/`seg_qp[8]`/`max_seg_id`/`start_picture_number`), `EbPrivDataNode`
  + `ROI_MAP_EVENT` + `EbBufferHeaderType::p_app_private`, and
  `SVT_AV1_CHECK_VERSION`. A struct/field rename or a change to the `seg_qp` delta
  semantics on an SVT-AV1 major bump would silently mis-bias — re-verify against
  `Source/Lib/Encoder/Codec/EbSegmentation.c` (`SEG_LVL_ALT_Q` add) and
  `EbResourceCoordinationProcess.c` (`update_frame_event` keeps the bare pointer).
- **Consumes from FFmpeg**: `AV_FRAME_DATA_REGIONS_OF_INTEREST` (`AVRegionOfInterest`,
  `self_size`/`top`/`bottom`/`left`/`right`/`qoffset`), `av_frame_get_side_data`,
  `av_pix_fmt_desc_get`, `av_clip`/`av_clipf`/`lrintf`.
- **Rebase-fragile spots**: (1) the `eb_enc_init` enable block anchors on the
  `config_enc_params(...)` return-check immediately before
  `svt_av1_enc_set_parameter` — `enable_roi_map` **must** be set before that call;
  (2) the `eb_send_frame` attach anchors on the Dolby-Vision tail
  (`AVERROR_INVALIDDATA`) and the `svt_av1_enc_send_picture` call — both wrapped in
  `#if SVT_AV1_CHECK_VERSION(1, 6, 0)`; (3) the AVOption anchors on the
  `svtav1-params` option row. Upstream churn (the vmafx fork's own `-qpfile` ROI
  bridge edits the same regions on its tree, but the Pelorus base is **pristine**
  n8.1.1 which has none of it) can fuzz these — regenerate with `generate.sh`
  rather than hand-resolving.
- **Lifetime invariant (keep on regeneration)**: each ROI-bearing frame owns its
  `SvtAv1RoiMapEvt` + `b64_seg_map` on the `roi_evts` list, freed in
  `eb_enc_close()`. Do **not** "optimise" to a single reused buffer — the library
  stores the bare pointer and dereferences it later on async pipeline threads, so
  reuse races the lookahead.
- **Capability/portability invariants**: `SVT_AV1_CHECK_VERSION(1, 6, 0)` compile
  guard (older SVT-AV1 → one-shot init warning + pass-through); ≤ 8 segments
  (`MAX_SEGMENTS`); 64×64 superblock grid; default OFF.
- **Re-test after rebase**: full series replay via `git am --3way`; rebuild against
  an SVT-AV1-enabled toolchain (`./configure --enable-libsvtav1 … && make`) and
  smoke `ffmpeg -h encoder=libsvtav1 | grep pelorus_roi`; ideally re-run the
  on-HW A/B (`analyze roi=1 → libsvtav1 -pelorus_roi 1`, CAMBI) from

## v0.1.0 — patch 0014 (dehalo; cumulative on 0001–0011)

- **Patch**: `ffmpeg-patches/0014-add-vf_pelorus_dehalo_vulkan.patch`
  (canonical source `files/vf_pelorus_dehalo_vulkan.c`). A `vf_` filter drop-in,
  same per-filter registration model as the deband/analyze/denoise loop —
  **not** a libavcodec edit. **Cumulative on 0001–0011.**
- **Touches**: `libavfilter/vf_pelorus_dehalo_vulkan.c` (new) + the three
  registration files. `libavfilter/allfilters.c` (the `extern const FFFilter
  ff_vf_pelorus_dehalo_vulkan` line, inserted **before** the `denoise` entry —
  `deband < dehalo < denoise` alphabetically, so dehalo's context lines reference
  the deband-added line above it); `libavfilter/Makefile` (the
  `OBJS-$(CONFIG_PELORUS_DEHALO_VULKAN_FILTER) += vf_pelorus_dehalo_vulkan.o
  vulkan.o vulkan_filter.o` line, inserted **after** the deband OBJS line);
  `configure` (`pelorus_dehalo_vulkan_filter_deps="vulkan spirv_library"` —
  **deps only**).
- **Consumes from libpelorus**: **none.** Dehalo is a **pure pixel transform** —
  it does not link libpelorus, emits no interop side data, and so carries **NO
  `require_pkg_config libpelorus … && add_extralibs` hunk** (unlike the
  deband/analyze/denoise/grain filters). The `configure` registration is
  deps-only. Nothing in the interop ABI can break this filter on a rebase.
- **Consumes from FFmpeg**: the standard `vf_*_vulkan` compute-filter surface
  (`FFVulkanContext`, lazy SPIR-V init, `FF_VK_REP_FLOAT`, `GLSLC`/`GLSLF`/`GLSLD`
  inline GLSL, `ff_vk_filter_config_input`/`_output`, the push-const machinery) —
  the same surface the other `vf_pelorus_*_vulkan` filters use. A change to that
  surface on an upstream bump can break the dispatch; re-test by replaying.
- **Shader lockstep**: `libpelorus/shaders/pelorus_dehalo.comp` and the patch's
  inline GLSL implement the same single-pass `DeHalo_alpha` + `FineDehalo`
  algorithm (box-blur target → `lowsens`/`highsens` sensitivity mask →
  remove-only asymmetric `darkstr`/`brightstr` pull → dilated Sobel ring gate).
  Edit both together (AGENTS hard rule 4).
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay
## v0.1.0 — patch 0015 (aa; cumulative on 0001–0011)

- **Patch**: `ffmpeg-patches/0015-add-vf_pelorus_aa_vulkan.patch`. A pure-transform
  AVFilter (anime warp-AA + line-darkening); committed by `generate.sh` after the
  prior filter/encoder patches so it lands as 0012 and does **not** renumber a
  shipped artifact. **Cumulative**: applies only on top of 0001–0011.
- **Touches**: `libavfilter/vf_pelorus_aa_vulkan.c` (new) + the same registration
  files as the other filters (extern/OBJS/deps), inserted **before** the analyze
  entries — `aa` sorts first among the `pelorus_*` filters (`aa` < `analyze` <
  `deband` < `denoise` < `grain_estimate` < `mc`), so its `allfilters.c` extern,
  `Makefile` OBJS, and `configure` `*_filter_deps` hunks insert ahead of the
  analyze lines and 0012's context references the analyze added lines.
- **No consumed surfaces — no libpelorus link**: aa is a pure pixel transform. It
  emits no side data, reads no `PelorusSideData`, and does **not** link
  `libpelorus`. Registration is **deps-only**: `configure` carries
  `pelorus_aa_vulkan_filter_deps="vulkan spirv_library"` and there is **NO**
  `require_pkg_config libpelorus … && add_extralibs` line for this filter (unlike
  the deband/analyze/denoise/mc producers). It consumes only the stock Vulkan
  compute-filter surface (`ff_vk_spirv_init`, `ff_vk_shader_init`/
  `_add_descriptor_set`/`_add_push_const`/`_link`/`_register_exec`,
  `ff_vk_filter_process_simple`, `ff_vk_filter_config_input`/`_output`, the GLSLC/
  GLSLF/GLSLD macros) — the same `vf_gblur_vulkan`-style idiom. A change to that
  surface on an upstream bump can break the build — re-test by replaying the stack.
- **Shader lockstep**: `libpelorus/shaders/pelorus_aa.comp` and the filter's inline
  GLSL implement the same warp-AA (blurred-Sobel edge map → gradient warp →
  bilinear resample) + optional Sobel-gated line-darkening; the only intended
  difference is the working domain (`.comp` reads `r16ui`/÷65535, inline reads
  `FF_VK_REP_FLOAT` UNORM already in `[0,1]`). The inline `MAX_R = 8` warp-radius
  bound must track the `.comp`. Edit both together (AGENTS hard rule 4).
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay

## v0.1.0 — patch 0016 (scenecut; cumulative on 0001–0015)

- **Patch**: `ffmpeg-patches/0016-add-vf_pelorus_scenecut.patch` (canonical source
  `files/vf_pelorus_scenecut.c`). A `vf_` filter drop-in (same per-filter
  registration model as the deband/analyze/denoise loop), but a **metadata-only
  consumer** — **NOT a Vulkan filter**: no shader, no GPU dispatch, no
  `vf_pelorus_scenecut.comp`. **Cumulative on 0001–0015.** ADR-0126.
- **CONSUMED surface (the rebase-fragile dependency)**:
  `PEL_SEC_MOTION.has_scene_cut` from `libpelorus/include/pelorus/interop.h` —
  the filter reads the frame's `AV_FRAME_DATA_SEI_UNREGISTERED` blob, finds the
  motion section with `pel_blob_find_section(…, PEL_SEC_MOTION,
  sizeof(PelorusMotionSection), …)`, and bounds-checks `got >=
  offsetof(PelorusMotionSection, has_scene_cut) + sizeof(uint8_t)` before reading
  `mo->has_scene_cut`. **A future ABI reorder of `PelorusMotionSection`** (or any
  change to where `has_scene_cut` sits in it) **breaks this consumer** — the
  append-only ABI (interop.h R1/R2) forbids that reorder, but if interop.h ever
  grows/relocates a motion field, regenerate this patch in the same PR and
  re-verify the offset check. Also consumes `pel_blob_is_present` and
  `PEL_SEC_MOTION`/`PelorusMotionSection` from interop.h. No ABI bump — the
  section was reserved at ABI 1.0; this is consumer-only code.
- **Consumes from FFmpeg**: the metadata-filter surface only —
  `av_frame_get_side_data`, `AV_FRAME_DATA_SEI_UNREGISTERED`, `AVFrame.pict_type`
  (`AV_PICTURE_TYPE_I`), `AVFrame.flags` (`AV_FRAME_FLAG_KEY`),
  `AVFILTER_FLAG_METADATA_ONLY`, `ff_filter_frame`, the `AVOption`/`FFFilter`
  machinery. **No** `ff_vk_*` / Vulkan surface is used (it is not a compute
  filter). A change to the metadata-filter surface on an upstream bump can break
  the build — re-test by replaying the stack.
- **Registration anchors** (the three files, same idiom — but with the
  Vulkan-filter differences called out):
  - `libavfilter/allfilters.c`: the `extern const FFFilter
    ff_vf_pelorus_scenecut` line inserted **before** the `ff_vf_perms` entry
    (`pelorus_scenecut` sorts after the other `pelorus_*` filters — `…mc` <
    `scenecut` — and before `perms`), so its context references the preceding
    `pelorus_*` added lines.
  - `libavfilter/Makefile`: the `OBJS-$(CONFIG_PELORUS_SCENECUT_FILTER) +=
    vf_pelorus_scenecut.o` line inserted **after** the `mc` OBJS line —
    **PLAIN `.o`, NO `vulkan.o vulkan_filter.o`** (it is not a compute filter;
    unlike every `vf_pelorus_*_vulkan` OBJS line, it pulls in no Vulkan objects).
  - `configure`: **`require_pkg_config libpelorus "<ver>" pelorus/interop.h &&
    add_extralibs $(…)`** for `pelorus_scenecut` (it links libpelorus to parse
    the side data), but **NO `pelorus_scenecut_filter_deps="vulkan
    spirv_library"`** and **NO `_deps` entry at all** — it is *not* a Vulkan
    filter, so it must not carry the `vulkan spirv_library` deps the
    `*_vulkan` filters need (an unknown/over-broad dep would gate the filter off
    on a box without Vulkan, which this consumer does not require). This is the
    inverse of the dehalo/aa pure-transform pattern (those are `_deps`-only with
    **no** libpelorus link; scenecut is **libpelorus-link-only with no `_deps`**).
- **No shader lockstep**: there is no `.comp` for this filter — it touches no
  pixels and runs no GPU code, so AGENTS hard rule 4 does not apply.
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay
  0001–0016 via `git am --3way`, build, smoke `ffmpeg -h
  filter=pelorus_scenecut` and confirm the `force_idr` AVOption). Functional
  check: a multi-shot clip through `pelorus_mc_vulkan=meta=1,hwdownload,format=
  yuv420p,pelorus_scenecut` must force `pict_type=I` on the cut frames (inspect
  with `ffprobe -show_frames` / `-skip_frame nokey`).
## v0.1.0 — patch 0017 (deblock; cumulative on 0001–0011)

- **Patch**: `ffmpeg-patches/0017-add-vf_pelorus_deblock_vulkan.patch`
  (canonical source `files/vf_pelorus_deblock_vulkan.c`). A `vf_` filter drop-in,
  same per-filter registration model as the deband/analyze/denoise/dehalo/aa
  loop — **not** a libavcodec edit. Committed by `generate.sh` after the prior
  filter/encoder patches so it lands as 0016 and does **not** renumber a shipped
  artifact. **Cumulative on 0001–0011.**
- **Touches**: `libavfilter/vf_pelorus_deblock_vulkan.c` (new) + the three
  registration files. `libavfilter/allfilters.c` (the `extern const FFFilter
  ff_vf_pelorus_deblock_vulkan` line, inserted **before** the `dehalo` entry —
  `deband < deblock < dehalo` alphabetically, so deblock's context lines
  reference the deband-added line above it); `libavfilter/Makefile` (the
  `OBJS-$(CONFIG_PELORUS_DEBLOCK_VULKAN_FILTER) += vf_pelorus_deblock_vulkan.o
  vulkan.o vulkan_filter.o` line, inserted **after** the deband OBJS line);
  `configure` (`pelorus_deblock_vulkan_filter_deps="vulkan spirv_library"` —
  **deps only**).
- **No consumed surfaces — no libpelorus link**: deblock is a **pure pixel
  transform**. It emits no side data, reads no `PelorusSideData`, and does
  **not** link `libpelorus`, so it carries **NO `require_pkg_config libpelorus …
  && add_extralibs` hunk** (unlike the deband/analyze/denoise/grain producers) —
  the `configure` registration is **deps-only**. Nothing in the interop ABI can
  break this filter on a rebase.
- **Consumes from FFmpeg**: the standard `vf_*_vulkan` compute-filter surface
  (`FFVulkanContext`, lazy SPIR-V init, `FF_VK_REP_FLOAT`, `GLSLC`/`GLSLF`/`GLSLD`
  inline GLSL, `ff_vk_filter_config_input`/`_output`,
  `ff_vk_filter_process_simple`, the push-const machinery) — the same surface the
  other `vf_pelorus_*_vulkan` filters use. A change to that surface on an upstream
  bump can break the dispatch; re-test by replaying.
- **Shader lockstep**: `libpelorus/shaders/pelorus_deblock.comp` and the patch's
  inline GLSL implement the same single-pass conditional `[1 2 1]` deblock — at
  the prior codec's block grid (`bsize`), within `edge` of a boundary, a
  cross-boundary `[1 2 1]` low-pass gated by the boundary step (`< thr` smooth,
  `>= thr` preserve) and blended by `str`. The only intended difference is the
  working domain (`.comp` reads `r16ui`/÷65535, inline reads `FF_VK_REP_FLOAT`
  UNORM already in `[0,1]`). Edit both together (AGENTS hard rule 4).
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay the
## v0.1.0 — patch 0018 (borderfix; cumulative on 0001–0011)

- **Patch**: `ffmpeg-patches/0018-add-vf_pelorus_borderfix_vulkan.patch`
  (canonical source `files/vf_pelorus_borderfix_vulkan.c`). A `vf_` filter
  drop-in, same per-filter registration model as the deband/analyze/denoise/
  dehalo/aa loop — **not** a libavcodec edit. Committed by `generate.sh` after the
  prior filter/encoder patches so it lands as 0016 and does **not** renumber a
  shipped artifact. **Cumulative on 0001–0011.**
- **Touches**: `libavfilter/vf_pelorus_borderfix_vulkan.c` (new) + the three
  registration files. `libavfilter/allfilters.c` (the `extern const FFFilter
  ff_vf_pelorus_borderfix_vulkan` line, inserted **before** the `deband` entry —
  `borderfix` sorts after `analyze` and before `deband` alphabetically (`analyze <
  borderfix < deband`), so its context lines reference the analyze-added line
  above it); `libavfilter/Makefile` (the
  `OBJS-$(CONFIG_PELORUS_BORDERFIX_VULKAN_FILTER) += vf_pelorus_borderfix_vulkan.o
  vulkan.o vulkan_filter.o` line, inserted **before** the deband OBJS line);
  `configure` (`pelorus_borderfix_vulkan_filter_deps="vulkan spirv_library"` —
  **deps only**).
- **No consumed surfaces — no libpelorus link**: borderfix is a **pure pixel
  transform**. It emits no side data, reads no `PelorusSideData`, and does **not**
  link `libpelorus`, so it carries **NO `require_pkg_config libpelorus … &&
  add_extralibs` hunk** (unlike the deband/analyze/denoise/grain producers) — the
  `configure` registration is **deps-only**. Nothing in the interop ABI can break
  this filter on a rebase.
- **Consumes from FFmpeg**: the standard `vf_*_vulkan` compute-filter surface
  (`FFVulkanContext`, lazy SPIR-V init, `FF_VK_REP_FLOAT`, `GLSLC`/`GLSLF`/`GLSLD`
  inline GLSL, `ff_vk_filter_config_input`/`_output`,
  `ff_vk_filter_process_simple`, the push-const machinery) — the same surface the
  other `vf_pelorus_*_vulkan` filters use. A change to that surface on an upstream
  bump can break the dispatch; re-test by replaying.
- **Shader lockstep**: `libpelorus/shaders/pelorus_borderfix.comp` and the patch's
  inline GLSL implement the same single-pass clamp-and-smear — each pixel's read
  coordinate is clamped onto the clean interior rect `[left, w−1−right] ×
  [top, h−1−bottom]` and the input sample read there is stored, smearing the
  nearest clean edge outward over the dirty band (all planes; band widths in each
  plane's own pixels). The only intended difference is the working domain (`.comp`
  reads `r16ui`/÷65535, inline reads `FF_VK_REP_FLOAT` UNORM already in `[0,1]`).
  Edit both together (AGENTS hard rule 4).
- **Re-test after rebase**: `ffmpeg-patches/test/build-and-run.sh` (replay the
  full stack, build, smoke `-h filter=pelorus_borderfix_vulkan`).

## v0.1.0 — ADR-0129 fix (regenerates 0001, 0007, 0014, 0015, 0017, 0018)

- **Patches**: `0001` (deband), `0007` (mc), `0014` (dehalo), `0015` (aa),
  `0017` (deblock), `0018` (borderfix) — regenerated from `files/`, **no**
  renumbering and **no** registration-hunk change. Each diff is confined to the
  filter's `init_filter` body.
- **What changed** (two runtime-only defect classes; see
  [ADR-0129](adr/0129-inline-glsl-chroma-passthrough-fix.md) and the AGENTS
  rebase-sensitive invariant 7): **(a)** the chroma-passthrough else-branch in
  the five pixel filters is now a single balanced
  `GLSLF(1, imageStore(output_images[%i], pos, imageLoad(input_images[%i], pos)); ,i, i);`
  — it was a paren-unbalanced two-line split that emitted raw `av_bprintf`
  macro-body text into the shader (shaderc `unexpected COMMA` → `-22`).
  **(b)** `mc`'s `cur_image`/`ref_image` descriptor bindings are now
  `.elems = av_pix_fmt_count_planes(input_format)` — they were `.elems = 1`,
  which overran the array because `ff_vk_shader_update_img_array` writes one
  descriptor per plane. The `mc` source comment that rationalized `.elems = 1`
  ("we only ever read plane 0") is superseded by the corrected comment.
- **No surface change**: the `.comp` files are untouched (the chroma loop and
  the descriptor bindings are C-emitted, never in the `.comp`; lockstep
  preserved); no registration hunk, `configure` line, or `libpelorus` surface
  moves — an upstream FFmpeg bump re-flows nothing new here.
- **Re-test after rebase**: replay the full stack, then run each pixel filter on
  a Vulkan device in **both** plane-mask regimes (default and `planes=1`) — the
  defect is invisible to a `.o`-only or `git apply --check` gate. Confirm chroma
  is bit-exact against a no-op `hwupload,hwdownload` round-trip (`U`/`V` infinite
  PSNR).
