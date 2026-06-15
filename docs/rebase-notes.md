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
