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
   `pelorus_analyze.comp`). Edit both together (root AGENTS.md hard rule 4).
3. **Any `libpelorus` surface the filter consumes** (a `PelorusSideData` field,
   a `deband.h` param) changing requires regenerating the patch in the same PR
   (root AGENTS.md hard rule 5); log it in [docs/rebase-notes.md](../docs/rebase-notes.md).
4. **GLSL reserved words** (`flat`, `sample`, `filter`, …) are not valid
   identifiers — the standalone shader hit this with `flat`. Lint by compiling.

## Don't

- Don't use per-patch `git apply --check` as the gate — patches are cumulative.
- Don't insert an implicit `hwdownload` between Pelorus stages; it breaks
  zero-copy (principles.md §5).
- Don't `av_free` a `pel_blob_pack` buffer — wrap it in an `AVBufferRef` with a
  `pel_blob_free` callback (allocator match).
