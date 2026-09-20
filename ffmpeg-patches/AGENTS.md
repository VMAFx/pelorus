<!-- markdownlint-disable MD013 -->
# Agent guide — ffmpeg-patches/

Pelorus ships an ordered patch stack against the exact FFmpeg tag and peeled
commit in root `build-config.env`. The current baseline is FFmpeg n9.0.2.
Parent rules in [`../AGENTS.md`](../AGENTS.md) also apply;
[ADR-0104](../docs/adr/0104-ffmpeg-patch-stack.md) governs the delivery model.

## Source and generated artifacts

```text
ffmpeg-patches/
├── files/                   canonical C and hand-maintained encoder diffs
│   ├── pelorus_vulkan_sample.h
│   └── vulkan/*.comp.glsl   canonical shipped shader sources
├── .commit-msg-*.txt        synthetic commit messages
├── 0001-*.patch … 0018-*    generated cumulative patch series
├── series.txt               apply order
├── generate.sh              deterministic isolated regeneration
└── test/build-and-run.sh    pinned replay, build, link, and smoke gate
```

- Edit `files/`, shader sources, hand diffs, or commit-message inputs; never
  hand-edit a numbered patch.
- Regenerate with `FFMPEG_REPO=/absolute/path ./generate.sh`. The script reads
  the tag and commit from `build-config.env`, verifies that the tag peels to the
  pinned commit, uses an isolated worktree, and must be byte-stable on a second
  run.
- Replay the entire cumulative series with `test/build-and-run.sh`. Per-patch
  `git apply --check` is not a substitute.
- New `libavfilter/*.c` files carry FFmpeg's LGPL-2.1 header (`Copyright 2026
  Lusoris`), not the libpelorus BSD-2-Clause-Patent header (ADR-0105).

## FFmpeg 9 Vulkan model

- Model filters on FFmpeg 9's `vf_gblur_vulkan.c` / `vf_nlmeans_vulkan.c`:
  `FFVulkanContext` first, lazy pipeline initialization, explicit descriptors,
  specialization constants, push constants matching std430, and a precompiled
  SPIR-V shader.
- The only shipped shader source for a filter is
  `files/vulkan/pelorus_<name>.comp.glsl`. `generate.sh` copies it to
  `libavfilter/vulkan/`, registers its `.spv.o`, and the C filter links the
  generated `ff_pelorus_<name>_comp_spv_data[]` symbol. Do not reintroduce
  runtime or inline GLSL. `libpelorus/shaders/*.comp` are standalone references
  compiled by Pelorus's fast gate, not a second implementation to synchronize
  (ADR-0143).
- Compute filters use `*_filter_deps="vulkan spirv_compiler"`. `libpelorus` is
  not an FFmpeg config component and must not appear in `_deps`; filters that
  consume it use a separate guarded `require_pkg_config ... && add_extralibs`
  line. Pure transforms do not link it.
- Descriptor binding order and push-constant layout are hand-maintained across
  C and GLSL. Specialization IDs 253/254/255 are reserved for workgroup size.

## Rebase-sensitive invariants

1. Filter registration touches `configure`, `libavfilter/Makefile`, and
   `libavfilter/allfilters.c`; Vulkan filters also register the shader object in
   `libavfilter/vulkan/Makefile`. Link the final FFmpeg binary: an object-only
   build does not prove the embedded SPIR-V or `-lpelorus` wiring.
2. Arithmetic filters convert storage-image values into the logical sample
   domain using `pelorus_vulkan_sample.h`. `FF_VK_REP_FLOAT` only normalizes
   against the Vulkan storage container; LSB-aligned planar 10/12-bit formats
   need `sample_scale`, while P010/P012 require their descriptor shift. Multiply
   loads before math and divide transform results at the store boundary.
   Whole-texel copies such as borderfix are exempt (ADR-0147).
3. The `planes` AVOption selects physical planes. A selected semi-planar chroma
   plane contains both U and V and both components must be processed. Scalar
   kernels use read-modify-write so packed or otherwise unowned components are
   preserved. Validate selected and pass-through paths on-device.
4. Any consumed `libpelorus` surface change requires its canonical consumer,
   regenerated numbered patch, docs, and full-series replay in the same PR.
   The public side-data ABI remains append-only.
5. GLSL reserved words are invalid identifiers. Compile every canonical shader
   before regeneration; compilation alone does not validate descriptor order,
   component preservation, or runtime image formats.
6. NVENC and QSV ROI patches (0004/0005) are hand-maintained libavcodec diffs.
   QSV dense `mfxExtMBQP` and stock rectangle `mfxExtEncoderROI` are mutually
   exclusive. Dense MBQP is eligible only for progressive HEVC CQP on runtime
   API 1.28 or newer and when state cached after successful init/reset says the
   final attached `mfxExtCodingOption3` buffer, after external-buffer merging,
   has `EnableMBQP` enabled. An `AVQSVContext` same-BufferId replacement owns
   that final value. Never rescan `q->param.ExtParam` per frame: parameter
   retrieval uses a transient query list. Every other case retains stock ROI.
   Each dense-path frame owns one contiguous header+map
   allocation through `QSVFrame::enc_ctrl` until its surface unlocks; never
   share mutable map scratch across asynchronous frames. Size the 16x16 raster
   from aligned `mfxFrameInfo.Width/Height`, clip regions to the visible frame,
   preserve zero padding, and retain every overflow/narrowing check. Build the
   affected encoder TU with oneVPL and run the dedicated sanitizer regression
   (ADR-0146).
7. Other hand-maintained encoder/bitstream diffs (NVENC ME hints, qpmap, H.274
   FGS, NVENC film grain, libaom ROI, SVT-AV1 ROI) must be compiled in their
   feature-enabled configuration. A default FFmpeg build may omit those TUs.
   Preserve libaom's one-shot diagnostic and non-fatal fallback while its
   non-RTC `AOME_SET_ROI_MAP` path rejects the map.
8. `vf_pelorus_scenecut` is metadata-only: no Vulkan dependency or shader, but
   it links libpelorus. `dehalo`, `aa`, `deblock`, and `borderfix` are pure
   transforms: Vulkan/SPIR-V dependencies, no libpelorus link, no interop side
   data.

## Required checks

Before calling a patch-stack change complete:

1. format touched C and shell-check touched shell;
2. compile all canonical GLSL and run the Pelorus fast suite;
3. regenerate twice and compare bytes;
4. replay all 18 patches at the pinned FFmpeg commit;
5. build/link/smoke the relevant feature-enabled FFmpeg configuration; and
6. run the affected Vulkan formats and plane masks on hardware. If no device is
   available, record that row as unexecuted rather than treating compile success
   as runtime evidence.

Never insert an implicit `hwdownload` between Pelorus stages; the pipeline must
remain zero-copy. Never release a `pel_blob_pack` allocation with `av_free`;
wrap it in an `AVBufferRef` whose callback calls `pel_blob_free`.
