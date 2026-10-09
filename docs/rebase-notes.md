<!-- markdownlint-disable MD013 -->
# Rebase notes

Re-apply / re-test work created for the FFmpeg patch stack after an upstream
FFmpeg bump or a `libpelorus` ABI change. One entry per change that affects the
patches (ADR-0108 deliverable #6).

## Unreleased — Vulkan QP-map formats and fill paths (#278, ADR-0182; regenerates 0009)

- **Patch**: 0009 only. The `libavcodec/vulkan_encode.c` and
  `libavcodec/vulkan_encode.h` sections of
  `ffmpeg-patches/files/vulkan-pelorus-qpmap.patch` were rebuilt with
  `git diff <pinned commit> -- libavcodec/vulkan_encode.c libavcodec/vulkan_encode.h`
  in a tree with all 22 patches applied (no other patch touches those two
  files); the other sections and the shader are unchanged. The hunk headers'
  new-file line numbers, stale since an earlier hand edit, are now exact.
- **What changed**: the probe no longer assumes `R8_SINT`. It accepts the
  advertised `R8_SINT`/`R16_SINT`/`R32_SINT` delta or `R8_UNORM`/`R16_UNORM`
  emphasis format, asks `vkGetPhysicalDeviceVideoFormatPropertiesKHR` for the
  map usage together with the fill usages the encode queue family can record
  (then for the map usage alone), and picks a fill path: on-GPU raster (8-bit
  formats, compute on the encode family), staging copy (transfer-, graphics- or
  compute-capable encode family) or the new host-mapped `LINEAR` image (no
  queue requirement). The queue capabilities come from
  `FFVulkanContext.qf_props[qf_enc->idx]`, not from
  `AVVulkanDeviceQueueFamily.flags`, which lists only the purposes FFmpeg
  picked the family for. One map image per exec context, indexed by
  `exec->idx` (the old `qpmap_next` round robin is gone).
- **Rebase-sensitive invariants**: keep the host-mapped fill
  (`pelorus_qpmap_host_fill()`) before `vkCmdBeginVideoCodingKHR` and
  `pelorus_qpmap_host_release()` after `vkCmdEndVideoCodingKHR` in
  `vulkan_encode_issue()`; keep the map slot equal to `exec->idx`, so
  `ff_vk_exec_start()`'s fence wait guards the host write; keep the
  host-mapped image `PREINITIALIZED` in host-visible coherent memory. If
  upstream changes `FFVulkanContext.qf_props`, `tot_nb_qfs` or
  `FFVkExecContext.idx`, re-point the probe and the slot.
- **Static gates**: `scripts/check-vulkan-qpmap-contract.py --self-test` (now
  19 mutations) and the new C harness `scripts/test-vulkan-qpmap-fill.py
  --self-test` (entry classifier and host raster extracted from the hand diff,
  10 mutations) in the fast suite.
- **On-device gate**: the ADR-0166 gate, on RADV (Mesa 26.2 or newer, ICD
  pinned to `radeon_icd.json`) and NVIDIA: `-rc_mode cqp -pelorus_roi 1` must
  change the stream against the unsteered control, with
  `-init_hw_device vulkan=vk:0,debug=1` adding no VUID to the control's set.
  The RTX 4090 steered streams must stay byte-identical to the previous patch.

## Unreleased — patch 0022 (`hevc_nvenc` `udu_sei` header limit, #267, ADR-0181; cumulative on 0001–0021)

- **Patch**: `ffmpeg-patches/0022-nvenc-pelorus-udu-sei.patch` (canonical
  diff `files/nvenc-pelorus-udu-sei.patch`, header `files/pelorus_sei_fit.h`
  that `generate.sh` copies to `libavcodec/`, message
  `.commit-msg-nvenc-udu-sei.txt`). Applied after 0021, so no shipped patch is
  renumbered; 0001–0021 change only in their `[PATCH n/22]` subject line.
- **What it fixes**: NVENC's HEVC encoder fails `nvEncLockBitstream()` with
  `NV_ENC_ERR_OUT_OF_MEMORY` when a picture's VPS, SPS, PPS, AUD and SEI NAL
  units exceed 1024 bytes (Annex B, measured on driver 615.71.09,
  [research 0181](research/0181-hevc-nvenc-sei-header-budget.md)). For HEVC,
  `prepare_sei_data_array()` now charges each `SEI_UNREGISTERED` entry against
  768 bytes per picture less the queued A/53 and timecode SEI: whole when it
  fits, a Pelorus blob without its maps when that fits, else not written.
  `NvencContext` gains two counters; `ff_nvenc_encode_close()` logs them.
- **Rebase-sensitive**: the stock `udu_sei` loop in
  `prepare_sei_data_array()` (the hunk replaces its `av_memdup()` size),
  `NV_ENC_SEI_PAYLOAD` field names, and the Pelorus block at the end of
  `NvencContext` (after 0008's fields). `pelorus_sei_fit.h` mirrors interop
  offsets; `sei_fit_test.c` checks them with `offsetof()`, so a new section
  with maps needs its fields in `pel_sei_map_fields` and its bit in
  `PEL_SEI_STRIPPABLE`. Re-measure the limit after a driver or SDK change: the
  smoke `maps-1080p` case fails the same way as before if it shrinks.
- **Upstream**: the budget and drop path is useful to any `hevc_nvenc`
  `udu_sei` user (a 2 KiB foreign SEI also stops the stock encode); the map
  stripping is Pelorus-only. The stock failure, `ENOMEM` at the 1024-byte
  non-VCL limit with no hint at the cause, is reported as
  [FFmpeg issue #24972](https://code.ffmpeg.org/FFmpeg/FFmpeg/issues/24972).
  Keep 0022 until FFmpeg budgets or rejects oversized user SEI itself.
- **Regeneration**: `FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/generate.sh`, run twice: byte-identical.
- **Replay**: `JOBS=4 FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/test/build-and-run.sh` applies all 22 patches, links FFmpeg
  and runs `test/nvenc-udu-sei-smoke.sh` (exit 77 with the reason when the host
  has no NVENC or Vulkan device; any other failure fails the replay). The fast
  suite covers the header (`sei-fit`).

## Unreleased — analyze per-cell maps (#219, ADR-0177; regenerates 0002)

- **What changed**: `vf_pelorus_analyze_vulkan.c` fills the ABI 1.0 map
  fields (`cell_data_*`, `var_cell_*`, `edge_cell_*`) through the new private
  header `files/pelorus_analyze_maps.h` (grid, per-cell scores, packer), which
  `generate.sh` adds to patch 0002. New options `cell` and `maps`; the input
  pad's `config_props` is now `analyze_vulkan_config_input`, which sizes the
  grid, the per-cell buffers and the side-data `AVBufferPool` and refuses a
  grid above 2^20 cells. The ROI and scalar code moved into the header
  unchanged (`pel_an_fine_score`, `pel_an_coarse_score`).
- **Shader contract**: `pelorus_analyze.comp.glsl` gains specialization
  constant 1 (`cell_span`, cell / workgroup edge); the C side loads
  `{wg, wg, 1}` with `wg = pel_an_workgroup(cell)` and dispatches
  `grid_cols x grid_rows` workgroups. The push block and the descriptor order
  are unchanged.
- **pkg-config minimum stays `libpelorus >= 0.2.0`**: on ABI 1.4 headers the
  header packs with `pel_blob_pack_into()` (no allocation); below 1.4 it falls
  back to `pel_blob_pack()` plus a copy (`PEL_AN_PACK_INTO`). Both paths are in
  the fast suite (`analyze-maps`, and `analyze-maps-legacy-pack` built with
  `-DPEL_AN_FORCE_LEGACY_PACK`), and the filter compiles against the ABI 1.3
  headers of v0.3.0.
- **Rebase-sensitive**: `total_size` must end at the last map byte (the tester
  checks it against the blob length), and each map offset stays 8-aligned for
  `pel_blob_map()`.
- **Regeneration**: `FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/generate.sh`, run twice: byte-identical; only 0002 changes.
- **Replay**: `JOBS=4 FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/test/build-and-run.sh`. The fast suite covers the header
  (`analyze-maps`) and the reader layout (`interop-abi`); the hardware rows
  are in `test/vulkan-format-matrix.sh` (`maps-*`).

## Unreleased — patch 0021 (`ff_vk_frame_barrier` queue family on single-family devices; cumulative on 0001–0020)

- **Patch**: `ffmpeg-patches/0021-vulkan-frame-barrier-queue-family.patch`
  (canonical diff `files/vulkan-frame-barrier-queue-family.patch`, message
  `.commit-msg-vulkan-frame-barrier.txt`). Hand-maintained `libavutil/vulkan.c`
  diff applied by `generate.sh` after 0020, so no shipped patch is renumbered;
  0001–0020 change only in their `[PATCH n/21]` subject line.
- **What it fixes**: on a device with one queue family (Mesa lavapipe),
  `hwcontext_vulkan.c` creates `EXCLUSIVE` frames tracked as owned by that
  family, and every filter or codec call of `ff_vk_frame_barrier()` passes
  `VK_QUEUE_FAMILY_IGNORED`. The first barrier per frame became an ownership
  transfer to `IGNORED`, invalid under `VUID-VkImageMemoryBarrier2-image-09118`
  (Vulkan spec, "Queue Family Ownership Transfer": unequal indices define a
  transfer). Validation layer 1.4.363 records it as a release that is never
  acquired and keeps the old layout, so it reports `00344`, `09600`, `09059`
  and `09064`. The patch keeps the frame's owning family as the destination
  when the caller passes `IGNORED` and the tracked family is a concrete index:
  equal indices form no transfer, and the tracked family stays the one
  `hwcontext_vulkan.c` itself passes on such devices.
- **Unchanged**: frames tracked as `IGNORED` (every device with more than one
  queue family, where FFmpeg uses `CONCURRENT` images) and frames owned by
  `VK_QUEUE_FAMILY_EXTERNAL` or `VK_QUEUE_FAMILY_FOREIGN_EXT`.
- **Upstream**: FFmpeg master has the same code (checked 2026-10-09). The
  patch carries no Pelorus names and its header is a `git format-patch`
  message. It is upstream for review as
  [FFmpeg PR #24970](https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/24970)
  (rebased onto master `7bc3576910`). The validation layer's missing check is
  [KhronosGroup/Vulkan-ValidationLayers#13405](https://github.com/KhronosGroup/Vulkan-ValidationLayers/issues/13405),
  with the fix in
  [#13409](https://github.com/KhronosGroup/Vulkan-ValidationLayers/pull/13409).
  Drop 0021 on the first FFmpeg bump that contains #24970 or an equivalent
  fix; the later patches then move up one number (`ffmpeg-patches/AGENTS.md`
  rule 9).
- **Rebase-sensitive**: `ff_vk_frame_barrier()` in `libavutil/vulkan.c`
  (source of `srcQueueFamilyIndex`), `create_frame()` queue-family tracking and
  `switch_layout()` `dst_qf` in `libavutil/hwcontext_vulkan.c`. If upstream
  starts tracking `IGNORED` for single-family frames, the condition in 0021
  never fires and the patch can go.
- **Proof**: one FFmpeg tree, built with 0001–0020 ("before") and again after
  `git am` of 0021 ("after"). Validation layer 1.4.363, one ICD per run
  through `VK_DRIVER_FILES`; `testsrc2` 320x180, 5 frames,
  `format=yuv420p,hwupload,<filter>,hwdownload` for `pelorus_grain_estimate_vulkan`,
  `pelorus_analyze_vulkan`, `pelorus_deband_vulkan`, stock `gblur_vulkan` and
  `scdet_vulkan`; then `ffmpeg-patches/test/vulkan-format-matrix.sh` with
  `PELORUS_VALIDATE=1`. Counts of `00344`/`09600`/`09059`/`09064`, summed over
  the five filters (the layer stops repeating a message after 10 per run):

  | Device (queue families) | Five filters, before | after | Format matrix, before | after |
  | --- | --- | --- | --- | --- |
  | Mesa 26.2.2 lavapipe (1) | 1 / 5 / 35 / 50 | 0 / 0 / 0 / 0 | stops at row 31 on `09059` | 53/53 pass, no VUID |
  | Intel Arc A380, ANV (3) | 0 | 0 | 53/53, no VUID | 53/53, no VUID |
  | AMD RADV iGPU (5) | 0 | 0 | 53/53, no VUID | 53/53, no VUID |
  | RTX 4090, NVIDIA 615.71.09 (6) | 0 | 0 | 53/53 | 53/53 |

  On the RTX 4090 the remaining allow-listed VUIDs (`00174`, `00191`, `03909`,
  `07454`) keep the same counts before and after, in the five-filter runs and
  in the matrix, and the 40 matrix outputs are byte-identical.
- **Regeneration**: `FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/generate.sh`, run twice: byte-identical.
- **Replay**: `JOBS=4 FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/test/build-and-run.sh` applies all 21 patches and links
  FFmpeg and the static consumer.

## Unreleased — interop ABI 1.4 motion block size (#218; regenerates 0001, 0003, 0007)

- **What changed**: `vf_pelorus_mc_vulkan.c` writes
  `PelorusMotionSection.block_size_log2` (`pel_mc_bsize_log2()` in
  `files/pelorus_mc_stats.h`: 3, 4, 5 for `bsize` 8, 16, 32, else 0), and its
  entropy proxy moved into `mc_motion_entropy()` with the same arithmetic.
  `files/pelorus_sidedata.h` gains `pelorus_mc_block_pitch()`, which prefers
  the named edge and falls back to `pelorus_mc_cell_pitch()` for a 1.3 section
  or the value 0; `vf_pelorus_denoise_vulkan.c` calls it instead of the
  inference. 0001 changes because it carries `pelorus_sidedata.h`.
- **pkg-config minimum stays `libpelorus >= 0.2.0`**: the configure probes
  in the stack do not require ABI 1.4. Every `block_size_log2` use is
  therefore behind `#if PELORUS_ABI_MINOR >= 4`: the producer write in
  `vf_pelorus_mc_vulkan.c` and the reader in `pelorus_mc_block_pitch()`
  (`pelorus_sidedata.h`). Built against older libpelorus headers, the
  producer omits the field and the consumer always takes the documented
  inference (`pelorus_mc_cell_pitch()`). Keep the guard on any new use, or
  raise the probe minimum to the first release carrying ABI 1.4.
- **Rebase-sensitive**: a reader detects a 1.3 producer by the readable size,
  never by the value: keep `PEL_SD_FIELD_OK(got, PelorusMotionSection,
  block_size_log2)` in front of the field read.
- **Proof against both headers**: the filter sources that use the field or
  `pelorus_sidedata.h` (`vf_pelorus_mc_vulkan.c`,
  `vf_pelorus_denoise_vulkan.c`, `vf_pelorus_analyze_vulkan.c`,
  `vf_pelorus_scenecut.c`) compile against the ABI 1.3 `interop.h` of
  v0.3.0 and against 1.4; the preprocessed mc and denoise sources contain
  `block_size_log2` only with the 1.4 header.
- **Regeneration**: `FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/generate.sh`, run twice: byte-identical.
- **Replay**: `JOBS=4 FFMPEG_REPO=/absolute/path/to/ffmpeg
  ffmpeg-patches/test/build-and-run.sh` applies all 20 patches, links FFmpeg
  and the static consumer. The fast suite covers the new helpers without an
  FFmpeg tree (`mc-stats`, `ffmpeg-sidedata-consumers`).

## 0.3.0 — patches 0019 and 0020 (`udu_sei` for QSV and Vulkan Video; cumulative on 0001–0018)

- **Patches**: `ffmpeg-patches/0019-qsv-pelorus-udu-sei.patch` (canonical diff
  `files/qsv-pelorus-udu-sei.patch`) and
  `ffmpeg-patches/0020-vulkan-pelorus-udu-sei.patch` (`files/vulkan-pelorus-udu-sei.patch`).
  Hand-maintained libavcodec diffs applied by `generate.sh` after 0018, so no
  shipped patch is renumbered. They carry `AV_FRAME_DATA_SEI_UNREGISTERED`
  (the `PelorusSideData` blob) into the H.264 and HEVC bitstream, behind a
  `udu_sei` AVOption (default off) named like the stock NVENC option.
- **0019 touches**: `libavcodec/qsvenc.c` (new `qsvenc_add_udu_payloads`, called
  after `set_encode_ctrl_cb` and before `EncodeFrameAsync`), `qsvenc.h`
  (`QSVEncContext.udu_sei`), `qsvenc_h264.c` and `qsvenc_hevc.c` (the option).
  Each side-data entry becomes one `mfxPayload` (type 5, SEI header in front of
  UUID and data) in the free slots of `QSV_MAX_ENC_PAYLOAD`, freed by
  `free_encoder_ctrl`. Rebase risk: the payload slot count, `set_encode_ctrl_cb`
  order and `free_encoder_ctrl` ownership.
- **0020 touches**: `libavcodec/vulkan_encode_h264.c` and `vulkan_encode_h265.c`:
  a `UNIT_SEI_UDU` unit, the `udu_sei` option, a check in the picture-header
  preparation and CBS `SEI_TYPE_USER_DATA_UNREGISTERED` messages in
  `write_extra_headers`. Rebase risk: the `UnitElems` enum, the `FF_HW_BASE`
  picture header hooks and `ff_cbs_sei_add_message` (prefix flag).
- **Not covered**: AV1. `qsvenc_av1.c` and `vulkan_encode_av1.c` have no
  passthrough for unregistered or T.35 metadata OBUs; adding one is a separate
  decision.
- **Re-test after rebase**: replay the full stack with
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (it checks `udu_sei` on `h264_qsv`, `hevc_qsv`, `h264_vulkan`, `hevc_vulkan`),
  then run the tester stage `sidedata_roundtrip`
  ([usage](usage/tester.md)) on a device with each encoder.

## 0.3.0 — FFmpeg base bump n9.0.1 → n9.0.2

- **Immutable base**: `build-config.env` binds tag `n9.0.2` to peeled commit
  `946fcce07b6dcd0331c8cc609192aeff5e1924f8`. Every generator, replay, and
  focused regression consumes that file; `BASE_TAG` and workstation-specific
  checkout defaults are unsupported.
- **Regeneration**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/generate.sh`. Run it
  twice and compare all 18 numbered patches byte-for-byte.
- **Replay**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`.
  The gate must privately build libpelorus, apply `series.txt`, link FFmpeg,
  register 10 filters plus `pelorus_fgs`, and enable every available oneVPL,
  libaom, SVT-AV1, and NVENC consumer so its Pelorus AVOptions are present. It
  must also install the static FFmpeg libraries and compile/run an external
  `pkg-config --static libavfilter` consumer, asserting that the link flags close
  over `-lpelorus`. Every `git am` supplies the ephemeral `Pelorus-Replay`
  committer identity, neutralizes signing, hooks, and diff ordering through `-c`
  settings, and passes `--no-gpg-sign --no-verify`; preserve those overrides
  when changing either replay loop (`scripts/check-build-config.py` matches one
  shared command pattern for both).
- **Focused QSV gate**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/qsv-roi-regression.sh`.
- **Shader model**: canonical shipped sources are
  `ffmpeg-patches/files/vulkan/*.comp.glsl`, compiled to SPIR-V by FFmpeg 9.
  `libpelorus/shaders/*.comp` are standalone fast-gate references, not a
  lockstep delivery surface.
- **Compatibility floor**: interop-consuming filters require
  `libpelorus >= 0.2.0`; the Pelorus source release is `0.3.0`.

## 0.3.0 — patch 0010 (pelorus_fgs RDD 5 defaults and init validation)

- **Patch**: `ffmpeg-patches/0010-add-pelorus_fgs_bsf.patch`, regenerated from
  `files/h265_pelorus_fgs_bsf.c` and `.commit-msg-fgs-bsf.txt`
  ([ADR-0155](adr/0155-fgs-bsf-rdd5-profile.md)). The registration hunks are
  unchanged. The BSF now includes `libavutil/pixdesc.h`, reads
  `AVBSFContext.par_in` (`format`, `bits_per_raw_sample`) at init, and wraps
  `ff_cbs_bsf_generic_filter()` in its own `.filter` callback.
- **Rebase-sensitive upstream contracts**: `ff_h274_film_grain_params_supported()`
  in `libavcodec/h274.h` (model 0 only) and the clamp of the cutoffs to [2, 14]
  in `libavcodec/h274.c` justify the defaults and the explicit cutoffs. The
  `ses()` bounds on `comp_model_value` in
  `libavcodec/cbs_h265_syntax_template.c` are mirrored by
  `pel_fgs_model_value_max()`. If an FFmpeg bump changes any of these, update the
  BSF and `scripts/test-fgs-bsf-contract.py` together.
- **Re-test after rebase**: run the full replay, then a CPU build with
  `--enable-libx265 --enable-bsf=pelorus_fgs,trace_headers --enable-decoder=hevc`.
  Check four results: defaults insert a model-0 SEI with three model values
  (`trace_headers`); FFmpeg decodes it with visible grain (compare against
  `-export_side_data film_grain`); `model_id=1:scale_y=200` on 8-bit input fails
  init with a non-zero exit; and `intensity_low=200:intensity_high=10` fails
  init.

## 0.3.0 — patch 0006 (grain estimator rounding and H.274 model 0)

- **Patch**: `ffmpeg-patches/0006-add-vf_pelorus_grain_estimate_vulkan.patch`,
  regenerated from `files/vf_pelorus_grain_estimate_vulkan.c`,
  `files/vulkan/pelorus_grain_estimate.comp.glsl` and
  `.commit-msg-grain_estimate.txt`
  ([ADR-0161](adr/0161-grain-estimate-rounding-and-h274-mapping.md)). The
  registration hunks, the descriptor layout and the push constants are
  unchanged. The shader rounds each accumulator add, and the lag-1 bias and
  scale changed in both sources (`scripts/test-grain-accumulator-bounds.py`
  keeps them in step). The host code is split into goto-free helpers
  (`init_filter`, `record_estimator`, `run_estimator`, `attach_estimate`) to
  meet the HISS touched-file rule; the recorded command sequence is unchanged.
  The unused `buf_content` string now states the real SSBO sizes.
- **Rebase-sensitive upstream contract**: the H.274 calibration table in the
  filter is derived from `libavcodec/h274.c` (grain tables, `init_slice_c()`,
  the 8×8 generation and deblocking). If an FFmpeg bump touches that file, run
  `FFMPEG_REPO=/path/to/ffmpeg scripts/gen-h274-grain-calibration.py --check`
  and paste the regenerated table if it reports drift.
  `ff_h274_film_grain_params_supported()` in `libavcodec/h274.h` (model 0,
  8-bit 4:2:0) justifies the emitted model and log2 scale.
- **Re-test after rebase**: on a Vulkan device, run the estimator with
  `model=h274` on flat white Gaussian grain of two 8-bit code values and print
  the metadata. Expect `grain_lag1` near −0.167, `h274_cutoff_h` 14 and
  `h274_scale_y` 10, and AV1 `ar_coeffs_y` of about `{ -11 0 0 0 }` in
  `showinfo`.

- **Shared consumer header (BUG-003/004/005/015)**:
  `ffmpeg-patches/files/pelorus_sidedata.h` is installed into `libavfilter/` by
  patch 0001 (beside `pelorus_vulkan_sample.h`) and included by
  `vf_pelorus_analyze_vulkan.c` (0002), `vf_pelorus_denoise_vulkan.c` (0003) and
  `vf_pelorus_scenecut.c` (0016). It replaces their first-entry
  `av_frame_get_side_data(..., SEI_UNREGISTERED)` lookup with a newest-first scan
  of every Pelorus blob, size-checks each motion/confidence field read, and
  derives the denoise MC cell pitch from the producer block edge. On a rebase,
  keep the `cp` line in `generate.sh`'s first loop iteration and the three
  `#include "pelorus_sidedata.h"` lines. The helper is private to the patch
  stack; libpelorus's public API and ABI are unchanged. Fast-suite regression:
  `meson test -C build ffmpeg-sidedata-consumers`.

The older sections below preserve the release and benchmark environment in
which each change landed. Their unqualified gate names are historical
shorthand; use the pinned commands above for all current rebases.

## 0.3.0 — deblock/aa plane loops continue (BUG-028)

- `pelorus_deblock.comp.glsl` and the fast=0 loop of `pelorus_aa.comp.glsl`
  skip a plane that excludes `pos` with `continue` instead of `return`, so a
  later full-size plane (yuva420p alpha) is written. Only patches 0015 (aa) and
  0017 (deblock) change; the fast=1 aa loop is untouched (it already guards per
  plane, keeping its barriers workgroup-uniform). Re-apply: regenerate with
  `generate.sh` and replay the full series. Regression:
  `scripts/test-shader-plane-continue.py` (fast suite).

## 0.3.0 — encoder follow-ups (BUG-030, BUG-031, BUG-032; regenerates 0008, 0009, 0011, 0013)

- **Patches**: `0008` (nvenc ME hints), `0009` (Vulkan QP map) and `0013`
  (SVT-AV1 ROI) change content. `0011` (nvenc film grain) changes only hunk
  offsets, because `0008` adds lines to `libavcodec/nvenc.c`.
- **0008 (BUG-031)**: `nvenc_setup_me_hints()` no longer returns with
  `meHintCountsPerBlock` zero on a frame without a `PEL_SEC_MOTION` section.
  It fills the scratch buffer with one zero-MV candidate per 16x16 block
  (`nvenc_fill_zero_me_hints()`), sets one L0 candidate per block, and warns
  once through the new `NvencContext.me_hints_absent_warned` field. The
  `me_hints_warned` comment now names only the AQ/lookahead note. The
  `NvencContext` fields stay at the tail of the struct; rebase conflicts in that
  block keep both new fields.
- **0009 (BUG-032)**: `pelorus_qp_range()` delegates to the new
  `pelorus_qp_range_for(codec_id, bit_depth)`, which returns 255 for
  `AV_CODEC_ID_AV1`. The driver delta clamp (ADR-0166) is unchanged. A debug
  line per applied rectangle logs the resulting delta.
- **0013 (BUG-030)**: `svtav1_build_roi_evt()` calls the new
  `svtav1_neutral_roi_evt()` for a frame with no ROI side data or an all-zero
  map, and `svtav1_push_roi_evt()` carries the queueing that used to be inline.
  `SvtContext.roi_sticky` records whether the library's sticky event is
  non-neutral; a neutral event is built only on that transition.
- **Tests**: `nvenc-me-hints`, `svtav1-roi-sticky` (new C harnesses over the
  hand diffs) and an extended `vulkan-qpmap-contract` run in the fast suite.
- **Replay**: the stack applies cumulatively on `n9.0.2`; regenerate twice and
  compare all 18 patches byte-for-byte.

## 0.3.0 — ADR-0166 Vulkan QP-map activation (regenerates 0009)

- **Patch**: 0009 only. The hand-maintained source
  `ffmpeg-patches/files/vulkan-pelorus-qpmap.patch` now also touches
  `libavutil/hwcontext_vulkan.c`, `libavutil/vulkan_functions.h`,
  `libavutil/vulkan_loader.h`, and `libavcodec/vulkan_encode_h265.c`. No later
  patch touches these files or `vulkan_encode.[ch]`, so their file sections can
  be rebuilt by diffing a tree with the stack applied against the pinned commit;
  the `configure`, `libavcodec/vulkan/Makefile` and shader sections stay as they
  are, because later patches also edit `configure`.
- **Device enablement**: stock FFmpeg enables neither
  `VK_KHR_video_encode_quantization_map` nor its `videoEncodeQuantizationMap`
  feature. 0009 adds `FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP` (bit 54; re-check
  that the bit is still free after a bump), the `ff_vk_extensions_to_mask`
  mapping, the optional device-extension entry, and the `VulkanDeviceFeatures`
  member with its `FF_VK_STRUCT_EXT` link and `COPY_VAL`. If upstream adds its
  own flag or feature member for this extension, drop these hunks and point the
  probe at upstream's names.
- **Valid-usage invariants**: keep the map fill block **before**
  `vkCmdBeginVideoCodingKHR` in `vulkan_encode_issue()`; create the map image
  with the probed `ctx->qpmap_tiling`; keep
  `VK_VIDEO_SESSION_PARAMETERS_CREATE_QUANTIZATION_MAP_COMPATIBLE_BIT_KHR` on
  the session parameters; keep the H.265 `cu_qp_delta_enabled_flag` override in
  `init_sequence_headers()`; keep the delta clamp derived from
  `pelorus_qpmap_query_delta_range()`.
- **Static gate**: `scripts/check-vulkan-qpmap-contract.py --self-test` (fast
  suite) fails when any of the above disappears from the hand-maintained diff.
- **On-device gate**: encode with `-rc_mode cqp -pelorus_roi 1` and
  `-init_hw_device vulkan=vk:N,debug=1`, then repeat without `-pelorus_roi`.
  The QP-map run must not add VUIDs to the stock encoder's own set, and the
  decoded stream must stay intact outside the region of interest. ADR-0166
  records the 2026-10-03 RTX 4090, RADV and ANV results.

## 0.3.0 — mc predictor units and O(n) p95 (BUG-009, BUG-014)

- **Patch**: 0007 (mc) only. It now also installs the private header
  `libavfilter/pelorus_mc_stats.h` (canonical source
  `ffmpeg-patches/files/pelorus_mc_stats.h`, copied by `generate.sh` before the
  filter). The header is plain C with no libav* include, so Pelorus's fast suite
  compiles and runs it directly (`mc-stats`,
  `ffmpeg-patches/test/mc_stats_test.c`). Keep it plain C on a rebase.
- **Unit boundary**: the shader writes Q2 quarter-pel `mv_x`/`mv_y` but reads
  `prev_mv` and the `gpred_x`/`gpred_y` push constants as integer pel. The host
  converts with `pel_mc_build_predictors()` (round half away from zero) when it
  fills the next frame's `prev_mv` buffer. Do not reintroduce a raw copy of the
  readback into `prev_mv`. The push-constant layout and descriptor order are
  unchanged.
- **Readback**: after the dispatch, `mc_snapshot()` copies the mapped MV/SAD
  SSBOs into av_fast_malloc'd host scratch once, and all host passes read the
  scratch. Element-wise reads of the device-local mapped buffers were the
  dominant per-frame cost.
- **Consumers**: the `PEL_SEC_MOTION` grid stays Q2 and the summary scalars stay
  in pixels, so 0008 (NVENC ME hints) and the denoise `mc=1` path are untouched.

## 0.3.0 — denoise numerics (BUG-016, BUG-027; regenerates 0003)

- **Patches**: `0003` only (denoise filter + shader). No registration, option,
  numbering or ABI change; `PEL_SEC_DENOISE` layout is unchanged.
- **BUG-016**: the `meta=1` residual sums moved from per-pixel uint32 adds at a
  1e3 scale to per-workgroup partials added into 64-bit slices at scale 2^23.
  The `stat_buffer` layout and size changed (`sum_lo[64]`, `sum_hi[64]`,
  `cnt_y[16]`, `cnt_c[16]`); the C `PelorusDenoiseBuf`, `buf_content` string and
  shader block must stay in lockstep (checked by
  `scripts/test-denoise-accumulator-bounds.py`).
- **BUG-027**: tile=0 and tile=1 are separate pipelines, and the RTX 4090 driver
  lowered them differently (1 code value at 8/10/12-bit). Every output-path value
  in the shader is now `precise`, loop-invariant divisions are hoisted
  (`exp(x)` becomes `exp2(x * LOG2E)`), `mix()`/`smoothstep()` are spelled out.
  Output may differ from the previous release by 1 code value on a few pixels.
  The standalone reference `libpelorus/shaders/pelorus_denoise.comp` mirrors it.
- **Re-test after rebase**: replay the stack, then
  `ffmpeg-patches/test/vulkan-format-matrix.sh` on every available GPU vendor
  (it now asserts tile=0/tile=1 identity at 8/10/12-bit, semi-planar and `mc=1`).
  Overlap: PR #68 also edits `pelorus_denoise.comp.glsl` (`precise` in
  `pel_to_sample`, sampled-image reads); take both, keep `pel_to_sample` as is.

## 0.3.0 — ADR-0147 Vulkan sample domain and component preservation

- **Patches**: 0001 (deband + shared private header), 0002 (analyze), 0003
  (denoise), 0006 (grain estimate), 0007 (mc), 0014 (dehalo), 0015 (aa), and
  0017 (deblock). Borderfix remains a raw whole-texel copy and needs no numeric
  conversion.
- **Storage/sample boundary**: `FF_VK_REP_FLOAT` normalizes integer images by
  their Vulkan storage container. The new `pelorus_vulkan_sample.h` derives
  `sample_scale = storage_max / (((1 << depth) - 1) << shift)` from the software
  format descriptor and derives `code_max = (1 << depth) - 1`. Shaders multiply
  every arithmetic load into logical `[0,1]`; transforms round to `code_max`
  before inverse scaling at the store boundary so P010/P012 padding stays zero.
  Preserve this helper and its patch-0001 installation when registration hunks
  move on an upstream rebase.
- **Component boundary**: `planes` selects physical planes. NV12/P010/P012
  plane 1 contains U and V; aa, dehalo, deblock, and denoise specialize a
  two-component loop for it. Stores start from the input texel and replace only
  owned components, preserving V and packed-view lanes. Do not replace these
  with scalar `vec4(value)` stores.
- **Shipped source**: edit the canonical
  `ffmpeg-patches/files/vulkan/*.comp.glsl` files. The
  `libpelorus/shaders/*.comp` files are compile-checked standalone references,
  not runtime shader copies and not a lockstep delivery surface (ADR-0143).
- **Descriptor-array boundary**: analyze and MC read luma through a
  specialization-constant index, not a literal `[0]`. Preserve the matching C
  specialization entries when rebasing: otherwise `glslc` contracts the
  unsized image declarations to fixed one-element SPIR-V arrays while FFmpeg
  binds every plane. MC must also deduplicate dependency, view, and barrier
  setup when its first dispatch uses the current `AVVkFrame` as the reference.
- **Static gates**: run `scripts/test-vulkan-sample-scale.py`,
  `scripts/check-vulkan-storage-domain.py`, compile every shipped shader, and
  run the Pelorus fast suite before regenerating. Then prove a second generation
  is byte-identical and replay all 18 patches at the pinned FFmpeg commit.
- **On-device gate**: run `ffmpeg-patches/test/vulkan-format-matrix.sh` with
  validation enabled. The gate inspects stdout and stderr and must cover
  normalized analyzer equivalence on 8/10/12 bit layouts, transform
  equivalence, NV12/P010/P012 U+V survival, packed-lane preservation,
  direct/tiled paths, lookahead/MC, and selected/pass-through plane masks. A
  missing device is an unexecuted row, not passing evidence.

See [ADR-0147](adr/0147-vulkan-sample-domain-and-components.md) and the
[research digest](research/0147-vulkan-storage-domain.md) for the reproduced
pre-fix failures and derivation.

## 0.3.0 — NVENC AV1 film-grain bias and ROI qindex span (regenerates 0004, 0008, 0011)

- **Patches**: `0004` (nvenc ROI hand diff) and `0011` (nvenc film-grain hand
  diff) change content. `0008` (nvenc ME hints) changes only its hunk offsets
  and blob index, because `0004` adds 14 lines to `libavcodec/nvenc.c`.
- **0011 (BUG-019)**: `NV_ENC_FILM_GRAIN_PARAMS_AV1` mirrors the AV1
  `film_grain_params()` syntax elements, so `cbMult`/`cbLumaMult`/`crMult`/
  `crLumaMult` take the raw `+128`-biased value and `cbOffset`/`crOffset` the raw
  `+256`-biased value. `AVFilmGrainAOMParams` carries them unbiased (FFmpeg's
  AV1 grain synthesis, the AFGS1 parser, and libdav1d's export all agree), so
  `pel_fg_from_aom()` clamps to the signed range and adds the bias. FFmpeg's
  native `av1dec.c` copies the raw coded values instead; a source decoded that
  way into `-pelorus_film_grain` would be double-biased. Use libdav1d for
  grain passthrough. The `av1dec` fix is upstream for review as
  [FFmpeg PR #24974](https://code.ffmpeg.org/FFmpeg/FFmpeg/pulls/24974); once
  the FFmpeg base contains it, both decoders export the same values.
- **0004 (BUG-020)**: `pelorus_roi_qp_range()` returns `51 + 6*(bit_depth-8)`
  for H.264/HEVC and 255 for AV1. NVENC adds `qpDeltaMap` entries in the
  codec's own QP units (AV1 qindex for `av1_nvenc`), and the int8 map
  saturates larger AV1 deltas.
- **Regression gate**: the fast-suite test `nvenc-pelorus-mapping`
  (`scripts/test-nvenc-pelorus-mapping.py`) extracts `pel_fg_from_aom()` and
  `pelorus_roi_qp_range()` from the hand diffs and runs them in a C harness,
  using the installed ffnvcodec header when pkg-config finds it. Keep both
  function names and their `static` top-level form when rebasing, or the
  extractor fails loudly.
- **Re-test after rebase**: build with `--enable-nvenc --enable-libdav1d`, then
  on NVENC hardware (1) decode a libaom `film-grain-test=1` AV1 stream with
  `-c:v libdav1d -export_side_data film_grain`, encode it with
  `av1_nvenc -pelorus_film_grain 1`, and compare `trace_headers`
  `cb_mult`/`cb_luma_mult`/`cb_offset` against the source; and (2) encode
  `av1_nvenc -rc constqp -qp 160` with a full-frame `addroi` of
  `qoffset=-40/255` and `-pelorus_roi 1`. Its size and PSNR must track a plain
  `-qp 120` encode.

## 0.3.0 — ADR-0163 dehalo ring gate and pull (patch 0014)

- **Patch**: only 0014 (dehalo) changes. The registration hunks and the C
  descriptor, push-constant, and specialization layout are unchanged. The
  `edge` AVOption help text now names the edge-step unit.
- **Shader contract**: `files/vulkan/pelorus_dehalo.comp.glsl` reads up to
  `MAX_R + 2` px from the pixel (the 5×5 box-mean grid of the 3×3 `Repair`
  window), so `PEL_HALO` is 10 and `PEL_TILE` is 52 for the 32×32 workgroup.
  Keep the tile at least that deep: a shallower tile makes `tile=1` read
  shared memory out of bounds without failing to compile. Keep the `precise`
  qualifiers on the arithmetic locals; without them `tile=0` and `tile=1`
  differ by single rounding flips.
- **Gates**: compile the shipped shader, run the fast suite, prove a second
  generation byte-identical, and replay all 18 patches. On a device, run
  `ffmpeg-patches/test/vulkan-format-matrix.sh`: its dehalo rows assert ring
  overshoot removal, line-art preservation, and direct/tiled identity.

See [ADR-0163](adr/0163-dehalo-gate-and-pull.md).

## v0.2.0 — FFmpeg base bump n8.1.1 → n9.0.1 (whole stack)

The largest rebase so far: FFmpeg 9 removed the API the entire filter set was
built on. Full detail and the measured evidence are in
[ADR-0143](adr/0143-ffmpeg-9-migration.md).

- **Base tag**: `n8.1.1` → `n9.0.1` (released 2026-08-12).
- **Touches**: every `vf_pelorus_*_vulkan.c`, every encoder hand-diff in
  `ffmpeg-patches/files/`, `generate.sh`, and a NEW per-filter shader at
  `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl` installed to
  `libavfilter/vulkan/` and registered in that directory's `Makefile`.

**What upstream changed (all measured against a real n9.0.1 checkout):**

1. `libavutil/vulkan.h` deleted the runtime GLSL builder: `GLSLC`/`GLSLA`/`GLSLF`/
   `GLSLD`, `FFVulkanShader.src`, `ff_vk_shader_init()`, `ff_vk_shader_print()`.
   Shaders are now compiled to SPIR-V at build time and linked in as
   `ff_pelorus_<name>_comp_spv_data[]` / `_len`.
2. `configure`: `spirv_library` → `spirv_compiler`, and it is satisfied by
   `check_glslc` (a working `glslc`), NOT by `--enable-libshaderc`.
3. `ff_vk_shader_add_descriptor_set()` returns `void` and lost its trailing
   `print_to_shader_only` argument — so no `RET()` wrapper.
4. `ff_vk_filter_process_simple()` / `_2pass()` / `_Nin()` gained a `uint32_t wgc_z`
   before `push_src`; pass `1` for 2D image filters.
5. `ff_vk_shader_load()` takes `(uint32_t []){ x, y, z }`, not `int []`.
6. NVENC raised its minimum SDK to 11.1, deleting the SDK 8.1/9.0/9.1/10 feature
   macros — including `NVENC_HAVE_QP_MAP_MODE`, whose only consumer was patch 0004.
   **This one fails silently**: every `#ifdef` would simply evaluate false and the
   ROI feature would compile out. The gate was removed after confirming
   `qpMapMode`/`qpDeltaMap` are unconditional members in every SDK ≥ 11.1.
7. Deprecated NVENC presets and rate-control modes were removed, shrinking the
   per-codec AVOption tables (`nvenc_h264.c` −63, `nvenc_hevc.c` −55) and moving
   every hook point up. The three option tables were **not** hoisted into a shared
   one — they still exist per codec, so the hunks needed re-anchoring, not relocating.

**Re-test after rebase** (all four steps passed for this bump):

```bash
FFMPEG_REPO=/path/to/ffmpeg BASE_TAG=n9.0.1 ffmpeg-patches/generate.sh
# then, on a pristine n9.0.1 worktree:
for p in ffmpeg-patches/0*.patch; do git am --3way "$p"; done   # 18/18
./configure --enable-vulkan ...                                  # libpelorus found
make libavfilter/vf_pelorus_*.o libavfilter/vulkan/pelorus_*.comp.spv.o
nm -u <filter>.o | grep spv   # must match nm --defined-only <shader>.comp.spv.o
```

Verified for this bump: 18/18 patches apply to pristine n9.0.1, configure
succeeds, a full `make ffmpeg` **links**, and the binary registers 10 pelorus
filters plus the `pelorus_fgs` BSF. All 9 shader objects are pulled in by the
Makefile's own `OBJS` (not by naming them on a make command line — see the note
above about why that distinction matters).

**On-device, all passing**: 9/9 filters execute on each of NVIDIA RTX 4090,
Intel Arc A380 and AMD RADV; `planes=1` yields bit-exact chroma (`u:inf v:inf`)
on all six plane-selecting pixel-transform filters; the three analyzers are
byte-identical pass-throughs; `meta=1` exercises libpelorus at runtime; and a
five-filter chain runs on real 2160p content. **Validation layers: run and
clean** — all four VUID
types the Pelorus filters emit are also emitted by stock upstream filters doing the
same work (three by a bare `hwupload,hwdownload` chain with no filter; 07454 by
upstream `vf_scdet_vulkan`, the SSBO-readback analogue). No Pelorus-specific
validation error.

**Note on the sibling repo**: `VMAFx/vmafx` migrated its own stack the same week
(`7f6e6356b`) and reported no API change. That does not generalise — its patches
are filter-only around `vf_libvmaf.c` and never touched the Vulkan shader API.

> **Release labelling.** The `v0.1.0` tag shipped **only patches 0001 and 0002**
> (verified with `git ls-tree v0.1.0 ffmpeg-patches/`). Every later section below is
> therefore labelled `v0.2.0`. Patches **0003** (denoise) and **0004** (nvenc ROI) have
> no section of their own — they were landed before this file's per-patch convention
> settled, and the gap is recorded here rather than back-filled from memory.

## v0.1.0 — initial stack (base n8.1.1)

- **Patch**: `ffmpeg-patches/0001-add-vf_pelorus_deband_vulkan.patch`.
- **Touches**: `libavfilter/vf_pelorus_deband_vulkan.c` (new),
  `libavfilter/allfilters.c` (extern), `libavfilter/Makefile` (OBJS),
  `configure` (`pelorus_deband_vulkan_filter_deps="vulkan spirv_compiler"` +
  guarded `require_pkg_config libpelorus >= 0.2.0` and
  `pelorus_deband_vulkan_filter_extralibs="libpelorus_extralibs"`).
- **Consumes from libpelorus**: `pelorus/interop.h` (`pel_blob_pack`,
  `pel_blob_free`, `PelorusSideData`, `PelorusBandingSection`, `PEL_FOURCC`),
  `pelorus/deband.h` (`PEL_DEBAND_FLAG_*`). A change to any of these requires
  regenerating this patch in the same PR.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (full series
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
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay
  0001+0002, build, smoke `-h filter=pelorus_analyze_vulkan`).
- **Frozen control-plane AVOptions (ADR-0110)**: the deband `AVOption` names and
  ranges `range`, `thry`, `thrc`, `grainy`, `grainc`, `softness`, `detail`,
  `dither`, `dynamic`, `protect` are a stable contract vmafx's `vmaf-tune` hard-codes
  against. A rebase/regeneration must **not** rename or narrow these; a deliberate
  break is a coordinated two-repo PR. See
  [docs/api/control-plane.md](api/control-plane.md). `sample`, `blur`, `planes`,
  and `meta` are out-of-contract and free to evolve.

## v0.2.0 — patch 0005 (qsv ROI; cumulative on 0001–0004)

- **Patch**: `ffmpeg-patches/0005-qsv-pelorus-roi.patch`
  (source-of-truth `ffmpeg-patches/files/qsv-pelorus-roi.patch`).
- **Touches** (libavcodec edit, *not* a filter — hand-maintained unified diff in
  `files/`, applied as its own commit by `generate.sh`):
  `libavcodec/qsvenc.h` (`pelorus_roi`, diagnostics, and the overridable
  `QSV_HAVE_MBQP` guard), `libavcodec/qsvenc.c` (documented init preconditions,
  `qsvenc_setup_roi()` rasterizer, and per-frame control ownership),
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
  grep pelorus_roi` once built against a QSV-enabled toolchain. Run the focused
  deterministic gate exactly as
  `FFMPEG_REPO=/path/to/ffmpeg bash ffmpeg-patches/test/qsv-roi-regression.sh`;
  it consumes the root immutable FFmpeg pin, compiles the MBQP-present and
  forced-absent branches, and exercises the rasterizer under ASan/UBSan. The
  original n9.0.1 acceptance remains historical evidence; the current focused
  and cumulative gates pass against n9.0.2.
- **Ownership invariant (ADR-0146)**: the `mfxExtMBQP` header and its `DeltaQP`
  array are one contiguous allocation per ROI-bearing `QSVFrame`. Ownership is
  transferred through that frame's `mfxEncodeCtrl` and ends only when
  `clear_unused_frames()` observes the surface unlocked and calls
  `free_encoder_ctrl()`. Never restore context-wide mutable map scratch.
- **Layout/portability invariants (keep on regeneration)**: dense MBQP is only
  progressive HEVC+CQP on runtime API 1.28 or newer, and only when the final
  attached CodingOption3 buffer has `EnableMBQP=ON`. An `AVQSVContext` buffer
  with the same BufferId replaces the internal one and therefore controls this
  check. Cache the result after successful init/reset; do not scan
  `q->param.ExtParam` from frame submission because parameter retrieval leaves
  it pointing at a transient query list. H.264, runtime API 1.27 or older,
  non-CQP HEVC, interlaced input, and `QSV_HAVE_MBQP=0` fall back to stock
  `mfxExtEncoderROI`. The grid is 16×16 and
  sized from aligned `mfxFrameInfo.Width/Height`; rectangles clip to visible
  frame dimensions and padding cells remain zero. Keep checked `size_t`
  multiplication/addition and `UINT32_MAX` narrowing guards. `EnableMBQP` is an
  init request, not a runtime capability probe. Default remains OFF.

## v0.2.0 — patch 0006 (grain_estimate; cumulative on 0001–0005)

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
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay
  0001–0006, build, smoke `-h filter=pelorus_grain_estimate_vulkan`).
- **Shader source**: edit the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_grain_estimate.comp.glsl`; the
  `libpelorus/shaders` file is a standalone reference, not a lockstep artifact.

## v0.2.0 — patch 0007 (mc; cumulative on 0001–0006)

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
  (`pelorus_mc_vulkan_filter_deps="vulkan spirv_compiler"` + the
  guarded `require_pkg_config libpelorus …` line and
  `pelorus_mc_vulkan_filter_extralibs="libpelorus_extralibs"`, after the denoise
  entry).
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
- **Shader source**: edit the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl`; the
  `libpelorus/shaders` file is a standalone reference. Keep the host-side
  specialization and descriptor contract consistent with the shipped shader.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay
  0001–0007, build, smoke `-h filter=pelorus_mc_vulkan`).

## v0.2.0 — patch 0008 (nvenc ME hints; cumulative on 0001–0007)

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

## v0.2.0 — patch 0009 (vulkan QP-map; cumulative on 0001–0008)

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
  filled by a compute dispatch — the canonical
  `libavcodec/vulkan/pelorus_qpmap.comp.glsl` is compiled to SPIR-V at build
  time, loaded by `pelorus_qpmap_build_shader`, and registered against `enc_pool`;
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
  `ff_vk_exec_add_dep_buf`, and the build-time SPIR-V loader). A change to that shader surface on
  an upstream bump can break the dispatch — re-test by rebuilding the TU. Entire
  path `#ifdef VK_KHR_video_encode_quantization_map`; gated `-std=c17` clean.
- **Shader source**: edit the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_qpmap.comp.glsl`; the
  `libpelorus/shaders` file is a standalone reference, not a lockstep artifact.
  Keep its descriptor bindings and push-constant layout aligned with the C host.
- **Invariant**: one map image **per exec-pool slot**, round-robined per bound
  frame (a single shared image races at `async_depth>1`); keep this on regeneration.
- **Re-test after rebase**: replay 0001–0009; rebuild the `vulkan_encode`,
  `vulkan_encode_h264`, `vulkan_encode_h265`, `vulkan_encode_av1` TUs (the
  on-GPU raster is a real compile, not just `-fsyntax-only`); compile the
  canonical shader and standalone reference (`glslangValidator`). On-HW A/B blocked on
  a driver advertising the extension + encode-feedback flags (see ADR-0114 Tier 2).

## v0.2.0 — patch 0010 (pelorus_fgs BSF; cumulative on 0001–0009)

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

## v0.2.0 — patch 0011 (nvenc AV1 film grain; cumulative on 0001–0010)

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

## v0.2.0 — patch 0012 (libaom-av1 ROI; cumulative on 0001–0011)

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
  `libaom.h`), so no new `require_pkg_config` or per-filter `_extralibs`
  configure hunk is needed, unlike interop-dependent Vulkan filters.
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

## v0.2.0 — patch 0013 (svtav1 ROI; cumulative on 0001–0012)

- **Patch**: `ffmpeg-patches/0013-svtav1-pelorus-roi.patch` (hand-maintained
  unified diff `ffmpeg-patches/files/svtav1-pelorus-roi.patch`, applied as its own
  commit by `generate.sh`). ADR-0121.
- **Touches** (one file): `libavcodec/libsvtav1.c` — the `pelorus_roi` AVOption +
  ROI ctx fields (`roi_evts` list, `roi_b64_cols/rows`) in `SvtContext`; an
  `enable_roi_map` enable block in `eb_enc_init` (before
  `svt_av1_enc_set_parameter`); the `svtav1_build_roi_evt()` rasterizer + the
  `EbPrivDataNode`/`ROI_MAP_EVENT` attach in `eb_send_frame` (around
  `svt_av1_enc_send_picture`); the event free-list teardown in `eb_enc_close`.
- **Numbering**: committed after 0012 in `generate.sh` so it lands as **0013**; it
  touches only `libsvtav1.c`, so it has no dependency on the other encoder
  patches, but it relies on **0002** (`vf_pelorus_analyze`) to *produce* the ROI
  side data it consumes — keep it after 0002 in the series.
- **Consumes from SVT-AV1** (`<EbSvtAv1Enc.h>` / `<EbSvtAv1.h>`):
  `EbSvtAv1EncConfiguration::enable_roi_map`, `SvtAv1RoiMapEvt`
  (`b64_seg_map`/`seg_qp[8]`/`max_seg_id`/`start_picture_number`), `EbPrivDataNode`
  `ROI_MAP_EVENT` and `EbBufferHeaderType::p_app_private`, and
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
  (`MAX_SEGMENTS`); 64×64 superblock grid; default OFF. Assign the enabled value
  as integer `1`: SVT-AV1 2.x exposes `enable_roi_map` as `Bool` without
  exporting the C `true` macro, while newer SDKs expose it as `bool`; both accept
  the integer boolean without a transitive-header dependency.
- **Re-test after rebase**: full series replay via `git am --3way`; rebuild against
  an SVT-AV1-enabled toolchain (`./configure --enable-libsvtav1 … && make`) and
  smoke `ffmpeg -h encoder=libsvtav1 | grep pelorus_roi`; ideally re-run the
  on-HW A/B (`analyze roi=1 → libsvtav1 -pelorus_roi 1`, CAMBI) from

## v0.2.0 — patch 0014 (dehalo; cumulative on 0001–0013)

- **Patch**: `ffmpeg-patches/0014-add-vf_pelorus_dehalo_vulkan.patch`
  (canonical source `files/vf_pelorus_dehalo_vulkan.c`). A `vf_` filter drop-in,
  same per-filter registration model as the deband/analyze/denoise loop —
  **not** a libavcodec edit. **Cumulative on 0001–0013.**
- **Touches**: `libavfilter/vf_pelorus_dehalo_vulkan.c` (new) + the three
  registration files. `libavfilter/allfilters.c` (the `extern const FFFilter
  ff_vf_pelorus_dehalo_vulkan` line, inserted **before** the `denoise` entry —
  `deband < dehalo < denoise` alphabetically, so dehalo's context lines reference
  the deband-added line above it); `libavfilter/Makefile` (the
  `OBJS-$(CONFIG_PELORUS_DEHALO_VULKAN_FILTER) += vf_pelorus_dehalo_vulkan.o
  vulkan.o vulkan_filter.o` line, inserted **after** the deband OBJS line);
  `configure` (`pelorus_dehalo_vulkan_filter_deps="vulkan spirv_compiler"` —
  **deps only**).
- **Consumes from libpelorus**: **none.** Dehalo is a **pure pixel transform** —
  it does not link libpelorus, emits no interop side data, and so carries **NO
  `require_pkg_config libpelorus …` probe or
  `pelorus_dehalo_vulkan_filter_extralibs` assignment** (unlike the
  deband/analyze/denoise/grain filters). The `configure` registration is
  deps-only. Nothing in the interop ABI can break this filter on a rebase.
- **Consumes from FFmpeg**: the standard FFmpeg 9 Vulkan compute-filter surface
  (`FFVulkanContext`, lazy precompiled-SPIR-V load, `FF_VK_REP_FLOAT`, explicit
  descriptors, `ff_vk_filter_config_input`/`_output`, push constants) —
  the same surface the other `vf_pelorus_*_vulkan` filters use. A change to that
  surface on an upstream bump can break the dispatch; re-test by replaying.
- **Shader source**: the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_dehalo.comp.glsl` implements the
  single-pass `DeHalo_alpha` + `FineDehalo` algorithm (box-blur target →
  `lowsens`/`highsens` sensitivity mask →
  remove-only asymmetric `darkstr`/`brightstr` pull → dilated Sobel ring gate).
  The `libpelorus/shaders` file is a standalone reference.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay the full stack, link, and smoke
  `-h filter=pelorus_dehalo_vulkan`).

## v0.2.0 — patch 0015 (aa; cumulative on 0001–0014)

- **Patch**: `ffmpeg-patches/0015-add-vf_pelorus_aa_vulkan.patch`. A pure-transform
  AVFilter (anime warp-AA + line-darkening); committed by `generate.sh` after the
  prior filter/encoder patches so it lands as 0015 and does **not** renumber a
  shipped artifact. **Cumulative**: applies only on top of 0001–0014.
- **Touches**: `libavfilter/vf_pelorus_aa_vulkan.c` (new) + the same registration
  files as the other filters (extern/OBJS/deps), inserted **before** the analyze
  entries — `aa` sorts first among the `pelorus_*` filters (`aa` < `analyze` <
  `deband` < `denoise` < `grain_estimate` < `mc`), so its `allfilters.c` extern,
  `Makefile` OBJS, and `configure` `*_filter_deps` hunks insert ahead of the
  analyze lines and 0015's context references the analyze-added lines.
- **No consumed surfaces — no libpelorus link**: aa is a pure pixel transform. It
  emits no side data, reads no `PelorusSideData`, and does **not** link
  `libpelorus`. Registration is **deps-only**: `configure` carries
  `pelorus_aa_vulkan_filter_deps="vulkan spirv_compiler"` and there is **NO**
  `require_pkg_config libpelorus …` probe or
  `pelorus_aa_vulkan_filter_extralibs` assignment for this filter (unlike the
  deband/analyze/denoise/mc producers). It consumes only the stock Vulkan
  FFmpeg 9 compute-filter surface (precompiled-SPIR-V load, descriptors,
  push constants, `ff_vk_filter_process_simple`, and
  `ff_vk_filter_config_input`/`_output`) — the same `vf_gblur_vulkan`-style
  idiom. A change to that
  surface on an upstream bump can break the build — re-test by replaying the stack.
- **Shader source**: edit the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_aa.comp.glsl`; its `MAX_R = 8` bound and
  push-constant contract must match the host. The `libpelorus/shaders` file is a
  standalone reference.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay the full stack, link, and smoke `-h filter=pelorus_aa_vulkan`).

## v0.2.0 — patch 0016 (scenecut; cumulative on 0001–0015)

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
  - `configure`: a guarded
    `require_pkg_config libpelorus "<ver>" pelorus/interop.h` plus
    `pelorus_scenecut_filter_extralibs="libpelorus_extralibs"` (it links
    libpelorus to parse the side data), but **NO
    `pelorus_scenecut_filter_deps="vulkan
    spirv_compiler"`** and **NO `_deps` entry at all** — it is *not* a Vulkan
    filter, so it must not carry the `vulkan spirv_compiler` deps the
    `*_vulkan` filters need (an unknown/over-broad dep would gate the filter off
    on a box without Vulkan, which this consumer does not require). This is the
    inverse of the dehalo/aa pure-transform pattern (those are `_deps`-only with
    **no** libpelorus link; scenecut is **libpelorus-link-only with no `_deps`**).
- **No shader**: there is no `.comp.glsl` for this filter — it touches no
  pixels and runs no GPU code.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay
  0001–0016 via `git am --3way`, build, smoke `ffmpeg -h
  filter=pelorus_scenecut` and confirm the `force_idr` AVOption). Functional
  check: a multi-shot clip through `pelorus_mc_vulkan=meta=1,hwdownload,format=
  yuv420p,pelorus_scenecut` must force `pict_type=I` on the cut frames (inspect
  with `ffprobe -show_frames` / `-skip_frame nokey`).

## v0.2.0 — patch 0017 (deblock; cumulative on 0001–0016)

- **Patch**: `ffmpeg-patches/0017-add-vf_pelorus_deblock_vulkan.patch`
  (canonical source `files/vf_pelorus_deblock_vulkan.c`). A `vf_` filter drop-in,
  same per-filter registration model as the deband/analyze/denoise/dehalo/aa
  loop — **not** a libavcodec edit. Committed by `generate.sh` after the prior
  filter/encoder patches so it lands as 0017 and does **not** renumber a shipped
  artifact. **Cumulative on 0001–0016.**
- **Touches**: `libavfilter/vf_pelorus_deblock_vulkan.c` (new) + the three
  registration files. `libavfilter/allfilters.c` (the `extern const FFFilter
  ff_vf_pelorus_deblock_vulkan` line, inserted **before** the `dehalo` entry —
  `deband < deblock < dehalo` alphabetically, so deblock's context lines
  reference the deband-added line above it); `libavfilter/Makefile` (the
  `OBJS-$(CONFIG_PELORUS_DEBLOCK_VULKAN_FILTER) += vf_pelorus_deblock_vulkan.o
  vulkan.o vulkan_filter.o` line, inserted **after** the deband OBJS line);
  `configure` (`pelorus_deblock_vulkan_filter_deps="vulkan spirv_compiler"` —
  **deps only**).
- **No consumed surfaces — no libpelorus link**: deblock is a **pure pixel
  transform**. It emits no side data, reads no `PelorusSideData`, and does
  **not** link `libpelorus`, so it carries **NO `require_pkg_config libpelorus …`
  probe or `pelorus_deblock_vulkan_filter_extralibs` assignment** (unlike the
  deband/analyze/denoise/grain producers) — the `configure` registration is
  **deps-only**. Nothing in the interop ABI can break this filter on a rebase.
- **Consumes from FFmpeg**: the standard FFmpeg 9 Vulkan compute-filter surface
  (`FFVulkanContext`, lazy precompiled-SPIR-V load, `FF_VK_REP_FLOAT`, explicit
  descriptors, `ff_vk_filter_config_input`/`_output`,
  `ff_vk_filter_process_simple`, the push-const machinery) — the same surface the
  other `vf_pelorus_*_vulkan` filters use. A change to that surface on an upstream
  bump can break the dispatch; re-test by replaying.
- **Shader source**: the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_deblock.comp.glsl` implements the
  single-pass conditional `[1 2 1]` deblock — at
  the prior codec's block grid (`bsize`), within `edge` of a boundary, a
  cross-boundary `[1 2 1]` low-pass gated by the boundary step (`< thr` smooth,
  `>= thr` preserve) and blended by `str`. The `libpelorus/shaders` file is a
  standalone reference.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay the full stack, link, and smoke
  `-h filter=pelorus_deblock_vulkan`).

## v0.2.0 — patch 0018 (borderfix; cumulative on 0001–0017)

- **Patch**: `ffmpeg-patches/0018-add-vf_pelorus_borderfix_vulkan.patch`
  (canonical source `files/vf_pelorus_borderfix_vulkan.c`). A `vf_` filter
  drop-in, same per-filter registration model as the deband/analyze/denoise/
  dehalo/aa loop — **not** a libavcodec edit. Committed by `generate.sh` after the
  prior filter/encoder patches so it lands as 0018 and does **not** renumber a
  shipped artifact. **Cumulative on 0001–0017.**
- **Touches**: `libavfilter/vf_pelorus_borderfix_vulkan.c` (new) + the three
  registration files. `libavfilter/allfilters.c` (the `extern const FFFilter
  ff_vf_pelorus_borderfix_vulkan` line, inserted **before** the `deband` entry —
  `borderfix` sorts after `analyze` and before `deband` alphabetically (`analyze <
  borderfix < deband`), so its context lines reference the analyze-added line
  above it); `libavfilter/Makefile` (the
  `OBJS-$(CONFIG_PELORUS_BORDERFIX_VULKAN_FILTER) += vf_pelorus_borderfix_vulkan.o
  vulkan.o vulkan_filter.o` line, inserted **before** the deband OBJS line);
  `configure` (`pelorus_borderfix_vulkan_filter_deps="vulkan spirv_compiler"` —
  **deps only**).
- **No consumed surfaces — no libpelorus link**: borderfix is a **pure pixel
  transform**. It emits no side data, reads no `PelorusSideData`, and does **not**
  link `libpelorus`, so it carries **NO `require_pkg_config libpelorus …` probe
  or `pelorus_borderfix_vulkan_filter_extralibs` assignment** (unlike the
  deband/analyze/denoise/grain producers) — the `configure` registration is
  **deps-only**. Nothing in the interop ABI can break this filter on a rebase.
- **Consumes from FFmpeg**: the standard FFmpeg 9 Vulkan compute-filter surface
  (`FFVulkanContext`, lazy precompiled-SPIR-V load, `FF_VK_REP_FLOAT`, explicit
  descriptors, `ff_vk_filter_config_input`/`_output`,
  `ff_vk_filter_process_simple`, the push-const machinery) — the same surface the
  other `vf_pelorus_*_vulkan` filters use. A change to that surface on an upstream
  bump can break the dispatch; re-test by replaying.
- **Shader source**: the canonical shipped
  `ffmpeg-patches/files/vulkan/pelorus_borderfix.comp.glsl` implements the
  single-pass clamp-and-smear — each pixel's read
  coordinate is clamped onto the clean interior rect `[left, w−1−right] ×
  [top, h−1−bottom]` and the input sample read there is stored, smearing the
  nearest clean edge outward over the dirty band (all planes; band widths in each
  plane's own pixels). The `libpelorus/shaders` file is a standalone reference.
- **Re-test after rebase**:
  `FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`
  (replay the
  full stack, build, smoke `-h filter=pelorus_borderfix_vulkan`).

## v0.2.0 — historical ADR-0129 fix (delivery surface superseded by ADR-0143)

- **Historical patches**: `0001` (deband), `0007` (mc), `0014` (dehalo), `0015`
  (aa), `0017` (deblock), and `0018` (borderfix) were regenerated without
  renumbering. Under the former FFmpeg 8 runtime-GLSL builder, ADR-0129 fixed an
  unbalanced generated chroma-pass-through statement and corrected mc image-array
  descriptor counts.
- **Current FFmpeg 9 state**: no filter emits or compiles GLSL text from C.
  ADR-0143 removed `GLSLF`/`GLSLC` and the inline/reference lockstep surface.
  Each filter now loads build-time SPIR-V from its one canonical
  `ffmpeg-patches/files/vulkan/*.comp.glsl` source; the C descriptor array and
  shader bindings remain a hand-maintained contract.
- **Current re-test**: replay the full stack, then run each pixel filter on a
  Vulkan device with selected and pass-through plane masks. For semi-planar
  formats, prove both U and V survive and are processed when selected; for
  packed views, prove unowned lanes remain intact. The defect class is invisible
  to a `.o`-only or `git apply --check` gate.

## v0.2.0 — ADR-0130 mc sub-pel (regenerates 0007, 0008, 0011)

- **Patches**: `0007` (mc filter), `0008` (nvenc ME-hints hand-diff), `0011`
  (nvenc film-grain hand-diff — **cascade only**: its `@@` hunk offsets shift by
  the 2 lines `0008` adds to `nvenc.c`, no content change).
- **What changed** (see [ADR-0130](adr/0130-mc-subpel-quarterpel.md)): mc refines
  the integer block-match minimum to sub-pel (parabolic SAD fit) and emits the MV
  grid in **quarter-pel** (Q2 = round(pel*4)), conforming the
  `PelorusMotionSection` MV field to ADR-0113's ¼-pel spec. The summary scalars
  stay pixel-domain (host ÷4). The NVENC ME-hint consumer round-divides the grid
  by 4 into its integer-pixel `NV_ENC_EXTERNAL_ME_HINT` field.
- **ABI**: `PelorusMotionSection` is unchanged (32 bytes, frozen) — the quarter-pel
  unit is a documented convention (interop.h comment), **no `PELORUS_ABI_MINOR`
  bump**. The interop.h change is comment-only; vmafx reads the pixel-domain
  scalars (unchanged), not the raw grid, so the vendored mirror needs no update.
- **Encoder-TU caution**: `0008` edits `libavcodec/nvenc.c`, which the patch-stack
  CI does NOT compile (it builds only `vf_pelorus_*` objects). The `0008` hand-diff
  hunk header was hand-adjusted for the +2 lines; **re-verify by building with
  `--enable-nvenc`** (compile `libavcodec/nvenc.o`), not just `git apply --check`.
- **Shader delivery**: the sub-pel refinement ships in canonical
  `ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl`; `pelorus_mc.comp` is a
  compile-checked standalone reference, not a second implementation to keep in
  lockstep (ADR-0143).
- **Re-test after rebase**: replay the full stack, build with `--enable-nvenc`,
  then run `pelorus_mc_vulkan=meta=1` on a sub-pel-panned still — the measured
  `global_motion_x` must be fractional (integer-pel mc would round to 0/1).

## v0.2.0 — ADR-0131 MC→denoise warp (regenerates 0003, 0007)

- **Patches**: `0003` (denoise filter — the `mc=1` warp consumer), `0007` (mc —
  emits the new confidence section alongside the MV grid). No registration-hunk
  or numbering change.
- **ABI**: `PELORUS_ABI_MINOR` → **2**, new section bit `PEL_SEC_MOTION_CONF`
  (`1u<<6`) + `PelorusMotionConfSection` (16 bytes, `_Static_assert`).
  `PelorusMotionSection` is unchanged (32 bytes). `interop.c`'s
  `section_bit_valid()` switch gained the new case; the conformance fixture
  (`interop_test.c`) round-trips it. **Append-only** — an older consumer still
  parses every section it knows (R4). The vmafx-vendored `interop.c`/`interop.h`
  mirror is a follow-up (single-writer: Pelorus writes, vmafx reads; vmafx does
  not consume confidence, so the drift is forward-compatible).
- **Consumes from FFmpeg**: denoise's `mc` path adds two SSBO descriptor
  bindings (mv_grid=6, conf_grid=7; output_images moved to 8) and uses
  `ff_vk_get_pooled_buffer` + `ff_vk_exec_add_dep_buf` (the buffers must outlive
  the async `meta=0` submit) — re-verify these symbols survive an upstream bump.
- **Current shader source**: the shipped warp lives in canonical
  `ffmpeg-patches/files/vulkan/pelorus_denoise.comp.glsl`; the single-plane
  `libpelorus/shaders` file is a standalone reference. Keep the C descriptor,
  specialization, and push-constant contract aligned with the canonical shader.
- **Historical delivery note**: this feature originally split runtime GLSL
  across `denoise_helpers_glsl[]` and `denoise_glsl[]` to stay below C99's
  4095-character string-literal limit. ADR-0143 removed that runtime-string
  surface when FFmpeg 9 adopted build-time SPIR-V.
- **Re-test after rebase**: replay the stack, then A/B
  `pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=mc={0,1}` on a noisy
  high-motion clip vs a clean reference — `mc=1` must run (no validation error)
  and beat `mc=0` on the moving content; `meson test --suite=fast` must stay
  11/11 (the new conformance case).

## Shader plane bounds (regenerates 0001, 0018)

- **What changed**: `pelorus_deband.comp.glsl` and `pelorus_borderfix.comp.glsl`
  now `continue` (not `return`) when `pos` lies outside a plane, so a full-size
  plane that follows subsampled chroma (`yuva420p` alpha) is still written.
  `bayer8` in the deband shader is the canonical Bayer matrix, and the borderfix
  clamp bounds are order-safe. No C, descriptor, push-constant or spec-constant
  change; no rebase conflict surface beyond the two `.comp.glsl` files.
- **Re-test after rebase**: `meson test --suite=fast` (`shader-plane-bounds`),
  then run deband and borderfix on `yuva420p` and confirm the alpha plane is
  bit-exact versus the input.
- Other plane-loop shaders (`deblock`, `aa`) keep the early-return pattern and
  are not covered by this change.

## Fix wave 2026-10-03 — QSV EnableMBQP warning, SVT-AV1 ROI event reclamation (regenerates 0005, 0013)

- **Patches**: `0005` (qsv ROI) and `0013` (SVT-AV1 ROI); no registration or
  numbering change. Sources: `files/qsv-pelorus-roi.patch`,
  `files/svtav1-pelorus-roi.patch`.
- **qsv**: `qsvenc_pelorus_roi_update_mbqp_enabled()` now takes `avctx` and
  logs once when `pelorus_roi` is on but the final attached CodingOption3 has
  `EnableMBQP` off; `QSVEncContext` gains `pelorus_roi_mbqp_warned`. Re-verify
  that `ff_qsv_enc_init()` / `update_parameters()` still call it after
  `MFXVideoENCODE_Init` / `Reset`.
- **svtav1**: `SvtContext.roi_evts` is now a queue of `{event, input index}`
  slots, reclaimed from `eb_receive_packet()` and before each append. It relies
  on SVT-AV1 internals that must be re-read on a bump: `ROI_MAP_EVENT` data is
  never copied or freed by the library (`enc_handle.c`, `rc_process.c`),
  `enc_ctx->roi_map_evt` is sticky across frames
  (`resource_coordination_process.c`), packets leave in decode order and the
  mini-GOP is at most 64 frames. Checked against SVT-AV1 v2.3.0 and v4.2.0.
