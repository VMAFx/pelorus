<!-- markdownlint-disable MD013 -->
# Agent guide — ffmpeg-patches/

The `vf_pelorus_*` Vulkan filters, shipped as a patch stack against FFmpeg
n8.1.1. Parent: [../AGENTS.md](../AGENTS.md). Governing ADR:
[0104](../docs/adr/0104-ffmpeg-patch-stack.md).

## Scope

```text
ffmpeg-patches/
├── files/                 canonical filter sources (EDIT HERE)
│   └── vf_pelorus_deband_vulkan.c
├── 0001-*.patch           generated artifacts (git format-patch)
├── series.txt             ordered apply list (cumulative stack)
├── generate.sh            regenerate patches from files/ (isolated worktree)
├── .commit-msg-0001.txt   commit message bodies used by generate.sh
└── test/build-and-run.sh  apply + configure + build + smoke gate
```

## Conventions

- **`files/` is the source of truth; the `*.patch` files are generated.** Never
  hand-edit a `.patch` — edit the source under `files/` and rerun `generate.sh`.
- New `libavfilter/*.c` files carry FFmpeg's **LGPL-2.1** header
  (`Copyright 2026 Lusoris`), not the BSD+Patent one (ADR-0105).
- Filters model `vf_gblur_vulkan.c` / `vf_nlmeans_vulkan.c`: `FFVulkanContext`
  first, lazy init, inline GLSL via `GLSLC`/`GLSLF`/`GLSLD`, push constants
  mirroring the C struct. See [docs/backends/vulkan.md](../docs/backends/vulkan.md).
- Filters are gated `*_filter_deps="vulkan spirv_library"` (no `libpelorus` —
  an unknown config name silently disables the filter, so `generate.sh`
  deliberately keeps it OUT of `_deps`). `libpelorus` is wired by a separate
  `enabled <filter> && require_pkg_config libpelorus "<ver>" pelorus/interop.h && add_extralibs $(...)`
  line, which both adds the cflags and links `-lpelorus`. Install libpelorus on
  the pkg-config path before configuring FFmpeg.
- Apply with `git am --3way`; the stack is cumulative.

## Rebase-sensitive invariants

1. **Registration touches three files**: `configure` (`*_filter_deps="vulkan
   spirv_library"` — *not* `libpelorus`, which is wired separately via
   `require_pkg_config libpelorus … && add_extralibs`),
   `libavfilter/Makefile` (the `OBJS-` line
   includes `vulkan.o vulkan_filter.o`), `libavfilter/allfilters.c` (the
   `extern const FFFilter ff_vf_*` line). An upstream rebase that reflows these
   files can drop a hunk — re-verify with a full series replay, not per-patch
   `git apply --check`.
2. **Each filter's inline GLSL must match its `libpelorus/shaders/*.comp`**
   reference shader (deband ↔ `pelorus_deband.comp`, analyze ↔
   `pelorus_analyze.comp`, denoise ↔ `pelorus_denoise.comp`, grain_estimate ↔
   `pelorus_grain_estimate.comp`, mc ↔ `pelorus_mc.comp`). Edit both together
   (root AGENTS.md hard rule 4). For mc, the filter's `PEL_MC_BLOCK_DIM`/
   `PEL_MC_SAD_SCALE` and the `s_sad[1024]` shared-array size must track the
   `.comp`'s `BLOCK_DIM`/`SAD_SCALE` / `BLOCK_DIM*BLOCK_DIM`.
3. **Any `libpelorus` surface the filter consumes** (a `PelorusSideData` field,
   a `deband.h` param) changing requires regenerating the patch in the same PR
   (root AGENTS.md hard rule 5); log it in [docs/rebase-notes.md](../docs/rebase-notes.md).
4. **GLSL reserved words** (`flat`, `sample`, `filter`, …) are not valid
   identifiers — the standalone shader hit this with `flat`. Lint by compiling.
5. **Encoder ROI patches (0004 nvenc, 0005 qsv) are libavcodec edits, not
   filters** — hand-maintained unified diffs in `files/`, applied as their own
   commits by `generate.sh` (no registration hunks). The QSV patch (0005) is
   compile-gated by `QSV_HAVE_MBQP` (oneVPL/MSDK API ≥ 1.13) and its dense
   `mfxExtMBQP` path is **mutually exclusive** with the stock `mfxExtEncoderROI`
   rectangle path (`set_roi_encode_ctrl` is guarded by `!q->pelorus_roi`) — never
   let both attach to one `mfxEncodeCtrl`. The 16×16 MBQP block size is the
   SDK-documented alignment for AVC *and* HEVC; do not substitute a GPU-specific
   coding-tree size. `EnableMBQP` is honored under CQP only — keep the init probe.
   An upstream qsvenc reflow can fuzz the `extco3`/`encode_frame`/`QSV_COMMON_OPTS`
   anchors; regenerate via `generate.sh`, never hand-edit `0005-*.patch`. See
   [docs/rebase-notes.md](../docs/rebase-notes.md).
6. **Software AV1 encoder ROI patches (0012 libaom) are libavcodec edits** — same
   hand-diff model as 0004/0005. libaom maps the ROI side data onto
   `AOME_SET_ROI_MAP`'s ≤8-segment delta-q map (segment 0 = background); on libaom
   ≤3.14.x `AOME_SET_ROI_MAP` returns `AOM_CODEC_INVALID_PARAM` on the non-RTC
   path, so failure is the *expected* runtime path — keep the one-shot
   `roi_warned_fail` diagnostic and the non-fatal fallback (the separate
   `roi_warned_coarse` flag must never gate it). Init anchor is `set_color_range`;
   no new link lib. Regenerate via `generate.sh`; never hand-edit `0012-*.patch`.

## Don't

- Don't use per-patch `git apply --check` as the gate — patches are cumulative.
- Don't insert an implicit `hwdownload` between Pelorus stages; it breaks
  zero-copy (principles.md §5).
- Don't `av_free` a `pel_blob_pack` buffer — wrap it in an `AVBufferRef` with a
  `pel_blob_free` callback (allocator match).
6. **`vf_pelorus_dehalo_vulkan` (patch 0014) is a pure pixel transform that does
   NOT link libpelorus** — its `configure` registration is **deps-only**
   (`pelorus_dehalo_vulkan_filter_deps="vulkan spirv_library"`, no
   `require_pkg_config libpelorus … && add_extralibs` hunk, unlike the
   deband/analyze/denoise/grain filters); it emits no interop side data. Its
   inline GLSL stays in lockstep with `libpelorus/shaders/pelorus_dehalo.comp`
   (root AGENTS.md hard rule 4).
6. **`vf_pelorus_aa_vulkan` (patch 0015) is a pure transform** — anime warp-AA +
   line-darkening — with **no `libpelorus` link**: deps-only registration
   (`pelorus_aa_vulkan_filter_deps="vulkan spirv_library"`, **no**
   `require_pkg_config libpelorus`/`add_extralibs` line), inserted **before** the
   analyze entries (`aa` < `analyze` alphabetically). Its inline GLSL stays in
   lockstep with `libpelorus/shaders/pelorus_aa.comp` (AGENTS hard rule 4).
7. **`vf_pelorus_scenecut` (patch 0016) is a metadata-only consumer, NOT a Vulkan
   filter** — no shader, no GPU work (`AVFILTER_FLAG_METADATA_ONLY`). Its
   `Makefile` OBJS line is a **plain `vf_pelorus_scenecut.o`** with **no
   `vulkan.o vulkan_filter.o`**, it carries **no `*_filter_deps="vulkan
   spirv_library"`** entry (it is not a compute filter), but it **does** link
   `libpelorus` via the `require_pkg_config libpelorus … && add_extralibs` line
   (it parses the side data). It **consumes `PEL_SEC_MOTION.has_scene_cut`** (the
   flag `vf_pelorus_mc_vulkan` emits) and sets `pict_type=I` for a vendor-neutral
   forced IDR — no per-encoder patch. An ABI reorder of `PelorusMotionSection`
   would break it (forbidden by interop.h R1/R2). See ADR-0126 and
   [docs/rebase-notes.md](../docs/rebase-notes.md).
