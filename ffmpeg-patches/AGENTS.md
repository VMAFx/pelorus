<!-- markdownlint-disable MD013 -->
# Agent guide — ffmpeg-patches/

Pelorus ships ordered patch stack against exact FFmpeg tag and peeled commit
in root `build-config.env`, applied on top of shared FFmpeg fix series pinned
there (`FFMPEG_SERIES_*`, ADR-0185). Current baseline: FFmpeg n9.0.2, series
`v0.1.0-rc.1`. Parent rules in
[`../AGENTS.md`](../AGENTS.md) also apply;
[ADR-0104](../docs/adr/0104-ffmpeg-patch-stack.md) governs delivery model.

## Source and generated artifacts

```text
ffmpeg-patches/
├── files/                   canonical C and hand-maintained encoder diffs
│   ├── pelorus_vulkan_sample.h
│   └── vulkan/*.comp.glsl   canonical shipped shader sources
├── .commit-msg-*.txt        synthetic commit messages
├── 0001-*.patch … 0023-*    generated cumulative patch series (22; 0021 retired)
├── series.txt               apply order, after shared series
├── generate.sh              deterministic isolated regeneration
└── test/build-and-run.sh    pinned replay, build, link, and smoke gate
```

- Edit `files/`, shader sources, hand diffs, or commit-message inputs; never
  hand-edit numbered patch.
- Regenerate with `FFMPEG_REPO=/absolute/path ./generate.sh`. Script reads
  tag and commit from `build-config.env`, verifies tag peels to pinned
  commit, uses isolated worktree, must be byte-stable on second run.
- Shared series first, everywhere: `generate.sh`, both replay scripts, tester
  Containerfile call `scripts/fetch-ffmpeg-series.sh` (sha256 pin, `cosign`,
  `gh attestation verify`, base check; fail closed; needs network, cosign,
  authenticated `gh`), `git am` its `series.txt`, then Pelorus patches.
  `generate.sh` formats range above `SERIES_TIP`; names map by position.
- Generic FFmpeg fix (stock file, no Pelorus name) -> VMAFx/ffmpeg-patches,
  never this stack. Series bump = `FFMPEG_SERIES_TAG`, `_COMMIT`, `_SHA256`
  together, `--self-test`, regenerate twice, replay.
- Replay entire cumulative series with `test/build-and-run.sh`. Per-patch
  `git apply --check` = no substitute.
- New `libavfilter/*.c` files: FFmpeg LGPL-2.1 header naming Lusoris; LGPL-2.1-or-later per `REUSE.toml`. Scripts, tests outside FFmpeg tree: EUPL-1.2 SPDX header (ADR-0171).

## FFmpeg 9 Vulkan model

- Model filters on FFmpeg 9's `vf_gblur_vulkan.c` / `vf_nlmeans_vulkan.c`:
  `FFVulkanContext` first, lazy pipeline initialization, explicit descriptors,
  specialization constants, push constants matching std430, precompiled
  SPIR-V shader.
- Only shipped shader source for filter:
  `files/vulkan/pelorus_<name>.comp.glsl`. `generate.sh` copies it to
  `libavfilter/vulkan/`, registers its `.spv.o`; C filter links generated
  `ff_pelorus_<name>_comp_spv_data[]` symbol. Do not reintroduce runtime or
  inline GLSL. `libpelorus/shaders/*.comp` = standalone references compiled
  by Pelorus's fast gate, not second implementation to synchronize
  (ADR-0143).
- Compute filters use `*_filter_deps="vulkan spirv_compiler"`. `libpelorus`
  = no FFmpeg config component; must not appear in `_deps`. Filters that
  consume it use guarded `require_pkg_config` probe plus symbolic
  `*_filter_extralibs="libpelorus_extralibs"` assignment. Never add
  libpelorus to global executable extralibs: per-filter assignment also
  closes `avfilter_extralibs` and generated `libavfilter.pc` for static
  consumers. Pure transforms do not link it.
- Descriptor binding order and push-constant layout: hand-maintained across
  C and GLSL. Specialization IDs 253/254/255 reserved for workgroup size.
- Keep luma-only accesses to unsized per-plane image arrays behind explicit
  specialization constant, even when value always zero. Literal `[0]` lets
  `glslc` contract SPIR-V descriptor to one element while FFmpeg still binds
  every frame plane. C specialization list and GLSL `constant_id` = one
  contract.
- Current and reference inputs share one `AVVkFrame` -> enqueue its
  dependency, create its views, transition its image only once. Overlapping
  barriers for same image in one dependency = invalid on strict drivers.

## Rebase-sensitive invariants

1. Filter registration touches `configure`, `libavfilter/Makefile`,
   `libavfilter/allfilters.c`; Vulkan filters also register shader object in
   `libavfilter/vulkan/Makefile`. Link final FFmpeg binary: object-only build
   does not prove embedded SPIR-V or `-lpelorus` wiring.
2. Arithmetic filters convert storage-image values into logical sample domain
   via `pelorus_vulkan_sample.h`. `FF_VK_REP_FLOAT` only normalizes against
   Vulkan storage container. LSB-aligned planar 10/12-bit formats need
   `sample_scale`; P010/P012 require their descriptor shift. Multiply loads
   before math; divide transform results at store boundary. Quantized
   P010/P012 writeback derives `code_max` from descriptor shift as
   specialization constant, not push field. Denoise keeps its push block at
   or below Vulkan's 128-byte guaranteed minimum. Whole-texel copies such as
   borderfix: exempt (ADR-0147).
3. `planes` AVOption selects physical planes. Selected semi-planar chroma
   plane contains both U and V; both components must be processed. Scalar
   kernels use read-modify-write -> packed or otherwise unowned components
   preserved. Validate selected and pass-through paths on-device.
4. Any consumed `libpelorus` surface change -> its canonical consumer,
   regenerated numbered patch, docs, full-series replay in same PR. Public
   side-data ABI stays append-only.
5. GLSL reserved words = invalid identifiers. Compile every canonical shader
   before regeneration. Compilation alone does not validate descriptor order,
   component preservation, or runtime image formats.
6. NVENC and QSV ROI patches (0004/0005) = hand-maintained libavcodec diffs.
   QSV dense `mfxExtMBQP` and stock rectangle `mfxExtEncoderROI`: mutually
   exclusive. Dense MBQP eligible only when all hold:
   - progressive HEVC CQP;
   - runtime API 1.28 or newer;
   - state cached after successful init/reset says final attached
     `mfxExtCodingOption3` buffer, after external-buffer merging, has
     `EnableMBQP` enabled.

   `AVQSVContext` same-BufferId replacement owns that final value. Never
   rescan `q->param.ExtParam` per frame: parameter retrieval uses transient
   query list. Every other case retains stock ROI. Each dense-path frame owns
   one contiguous header+map allocation through `QSVFrame::enc_ctrl` until
   its surface unlocks. Never share mutable map scratch across asynchronous
   frames. Size 16x16 raster from aligned `mfxFrameInfo.Width/Height`, clip
   regions to visible frame, preserve zero padding, retain every
   overflow/narrowing check. Build affected encoder TU with oneVPL and run
   dedicated sanitizer regression (ADR-0146).
7. Other hand-maintained encoder/bitstream diffs (NVENC ME hints, qpmap, H.274
   FGS, NVENC film grain, libaom ROI, SVT-AV1 ROI): compile in their
   feature-enabled configuration. Default FFmpeg build may omit those TUs.
   Preserve libaom's one-shot diagnostic and non-fatal fallback while its
   non-RTC `AOME_SET_ROI_MAP` path rejects map.
8. `vf_pelorus_scenecut` = metadata-only: no Vulkan dependency or shader, but
   links libpelorus. `dehalo`, `aa`, `deblock`, `borderfix` = pure
   transforms: Vulkan/SPIR-V dependencies, no libpelorus link, no interop side
   data.
9. Number 0021 = retired (ADR-0185). Its `ff_vk_frame_barrier()` queue
   family fix (`VUID-VkImageMemoryBarrier2-image-09118`) = shared series
   patch 0001. Never reuse number; never renumber 0022/0023 (docs, ADRs,
   tester, `v0.4.0-rc.2` cite them). New patches append after 0023. Proof of
   fix = lavapipe validation run, unchanged.
10. Patch 0022 = Pelorus half of NVENC `udu_sei`, one call
   (`pelorus_udu_carrier()`) between series 0003's `av_memdup()` and
   `nvenc_hevc_sei_fits()` in `prepare_sei_data_array()`: blob -> zero-free
   carrier on `h264_nvenc` and `hevc_nvenc` (ADR-0183); on `hevc_nvenc` blob
   whose carrier misses series' `sei_budget` loses maps first (ADR-0181).
   Series owns budget (0003: 1024 non-VCL bytes, 768 for SEI), drop of
   truncated SEI (0004: `epb > ceil(P/3)+3`), their log lines. Never
   re-add budget or drop code here; never edit series lines. Rebase-sensitive:
   hook position, `sei_budget` name, `NV_ENC_SEI_PAYLOAD` copy owned by loop.
   `files/pelorus_sei_fit.h` = size arithmetic (mirror of series
   `nvenc_sei_nal_size()`), map stripping, COBS carrier encoder; mirrors
   interop offsets and `interop.c` encoder; fast test `sei-fit` checks offsets
   with `offsetof()` and carrier bytes against `pel_blob_carrier_encode()`.
   Budget charges carrier, never blob. New section with maps -> add its fields
   to `pel_sei_map_fields`, its bit to `PEL_SEI_STRIPPABLE`. GPU proof =
   `test/nvenc-udu-sei-smoke.sh` (exit 77 = no NVENC/Vulkan).
11. Every ephemeral `git am` replay supplies `Pelorus-Replay` committer
   identity, neutralizes signing, hooks, and diff ordering, and passes
   `--no-gpg-sign --no-verify`. Do not rely on workstation's global Git
   configuration; hosted runner intentionally has no identity.
12. Patch 0023 = QSV `udu_sei` SEI budget (#286). Needs 0019 (changes its
   `qsvenc_add_udu_payloads()`) and 0022 (`pelorus_sei_fit.h`).
   `files/pelorus_sei_fit_qsv.h` = per-picture budget (sum of `mfxPayload.BufSize`;
   H.264 adds emulation prevention bytes), reuses 0022 stripper; plain blob form,
   never the NVENC carrier. Budgets 4040 B HEVC, 40960 B H.264, margin under
   Arc A380 limits (docs/usage/ffmpeg.md). Fast test `sei-fit` covers it.
   GPU proof = `tools/tester` `sidedata_roundtrip` on `hevc_qsv`, `h264_qsv`.

13. Output pools (#103, ADR-0184): six frame-writing filters (deband, denoise,
   dehalo, aa, deblock, borderfix) route output pad through
   `files/pelorus_vulkan_pool.h`; `tiling=drm` = DRM-modifier pool, modifier
   rule in `files/pelorus_drm_modifier.h` (fast test `drm-modifier`). Both
   headers enter with 0001. Never patch stock `vulkan_filter.c` or
   `hwcontext_*.c` for this: P010 `GR1616` import row and export-capability
   format-list fix queued in VMAFx/ffmpeg-patches; AMD sync_file needs root
   cause, filter warns on AMD drivers until then. Never fall back to OPTIMAL
   when `drm` requested. New frame-writing filter: same option and pad.
   GPU proof = `test/vulkan-drm-map-smoke.sh` (exit 77 = no binary/GPU).

## Required checks

Before calling patch-stack change complete:

1. format touched C and shell-check touched shell;
2. compile all canonical GLSL and run Pelorus fast suite;
3. regenerate twice and compare bytes;
4. replay shared series plus all 22 patches at pinned FFmpeg commit;
5. build/link/smoke relevant feature-enabled FFmpeg configuration;
6. install static FFmpeg libraries and compile/run external
   `pkg-config --static libavfilter` consumer, asserting its link flags include
   `-lpelorus`; and
7. run affected Vulkan formats and plane masks on hardware. No device
   available -> record that row as unexecuted; compile success = no runtime
   evidence.

Never insert implicit `hwdownload` between Pelorus stages: pipeline must stay
zero-copy. Never release `pel_blob_pack` allocation with `av_free`; wrap it in
`AVBufferRef` whose callback calls `pel_blob_free`.
