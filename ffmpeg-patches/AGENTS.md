<!-- markdownlint-disable MD013 -->
# Agent guide — ffmpeg-patches/

Pelorus ships ordered patch stack against exact FFmpeg tag and peeled commit
in root `build-config.env`. Current baseline: FFmpeg n9.0.2. Parent rules in
[`../AGENTS.md`](../AGENTS.md) also apply;
[ADR-0104](../docs/adr/0104-ffmpeg-patch-stack.md) governs delivery model.

## Source and generated artifacts

```text
ffmpeg-patches/
├── files/                   canonical C and hand-maintained encoder diffs
│   ├── pelorus_vulkan_sample.h
│   └── vulkan/*.comp.glsl   canonical shipped shader sources
├── .commit-msg-*.txt        synthetic commit messages
├── 0001-*.patch … 0021-*    generated cumulative patch series
├── series.txt               apply order
├── generate.sh              deterministic isolated regeneration
└── test/build-and-run.sh    pinned replay, build, link, and smoke gate
```

- Edit `files/`, shader sources, hand diffs, or commit-message inputs; never
  hand-edit numbered patch.
- Regenerate with `FFMPEG_REPO=/absolute/path ./generate.sh`. Script reads
  tag and commit from `build-config.env`, verifies tag peels to pinned
  commit, uses isolated worktree, must be byte-stable on second run.
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
9. Patch 0021 = upstream-bound `libavutil/vulkan.c` fix, no Pelorus names:
   `ff_vk_frame_barrier()` keeps frame's concrete owning queue family when
   caller passes `VK_QUEUE_FAMILY_IGNORED` (single-family `EXCLUSIVE` frames;
   `VUID-VkImageMemoryBarrier2-image-09118`). Keep it last; drop on first
   FFmpeg bump with equivalent fix. Proof = lavapipe validation run.
10. Every ephemeral `git am` replay supplies `Pelorus-Replay` committer
   identity, neutralizes signing, hooks, and diff ordering, and passes
   `--no-gpg-sign --no-verify`. Do not rely on workstation's global Git
   configuration; hosted runner intentionally has no identity.

## Required checks

Before calling patch-stack change complete:

1. format touched C and shell-check touched shell;
2. compile all canonical GLSL and run Pelorus fast suite;
3. regenerate twice and compare bytes;
4. replay all 21 patches at pinned FFmpeg commit;
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
