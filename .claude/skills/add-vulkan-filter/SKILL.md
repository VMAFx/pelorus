---
name: add-vulkan-filter
description: Use when adding a new vf_pelorus_*_vulkan compute filter or extending its shipped shader, host integration, options, or interop behavior.
---

# Add a Vulkan filter

Use FFmpeg 9's precompiled-SPIR-V model and preserve the released patch order.

## Implementation

1. Reserve an ADR and define the user-visible options, zero-copy behavior,
   sample domain, physical-plane/component ownership, and evidence boundary.
2. Add the one shipped shader source at
   `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`. Add a standalone
   `libpelorus/shaders/*.comp` reference only when it materially helps the fast
   gate; it is not a lockstep delivery copy.
3. Add `ffmpeg-patches/files/vf_pelorus_<name>_vulkan.c`, modeled on FFmpeg 9's
   `vf_gblur_vulkan.c` or `vf_nlmeans_vulkan.c`: `FFVulkanContext` first, lazy
   pipeline creation, explicit descriptor order, specialization constants,
   std430-compatible push constants, and linked precompiled SPIR-V. Runtime or
   inline GLSL is not part of this model.
4. Register C and shader objects, the alphabetical extern, and
   `*_filter_deps="vulkan spirv_compiler"`. Interop consumers separately use
   guarded `require_pkg_config libpelorus "libpelorus >= 0.2.0" ... &&
   add_extralibs`; pure transforms do not link libpelorus.
5. Add an explicit synthetic generator commit after the existing shipped patch
   sequence. Extend its filename map, `series.txt`, replay patch-count assertion,
   and filter-registration list; never shift existing patch identities.

For interop, attach `pel_blob_pack` output through an `AVBufferRef` callback
that calls `pel_blob_free`. A new section requires `bump-abi`.

## Deliverables and verification

- Per-surface docs, ADR, changelog fragment, rebase note, and package AGENTS
  invariant ship with the implementation.
- Compile canonical and standalone shaders; run the Pelorus fast suite.
- Use `ffmpeg-build-patches`, prove byte-stable regeneration, then use
  `ffmpeg-apply-patches` to link the pinned FFmpeg tree.
- Run applicable 8/10/12/16-bit, semi-planar U/V, selected/pass-through plane,
  direct/tiled, and validation-layer cases on available devices.

Keep the pipeline in `AV_PIX_FMT_VULKAN`; never insert an implicit download.
