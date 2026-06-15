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
- Filters are gated `*_filter_deps="vulkan spirv_library libpelorus"` with a soft
  `check_pkg_config libpelorus` probe (idiomatic, cf. `libvmaf_cuda`); install
  libpelorus on the pkg-config path before configuring FFmpeg.
- Apply with `git am --3way`; the stack is cumulative.

## Rebase-sensitive invariants

1. **Registration touches three files**: `configure` (`*_filter_deps` with
   `libpelorus` + a soft `check_pkg_config libpelorus`),
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

## Don't

- Don't use per-patch `git apply --check` as the gate — patches are cumulative.
- Don't insert an implicit `hwdownload` between Pelorus stages; it breaks
  zero-copy (principles.md §5).
- Don't `av_free` a `pel_blob_pack` buffer — wrap it in an `AVBufferRef` with a
  `pel_blob_free` callback (allocator match).
