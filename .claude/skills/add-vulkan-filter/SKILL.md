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
   inline GLSL is not part of this model. Specialization IDs 253/254/255 are
   reserved for workgroup size. New FFmpeg C files use the LGPL-2.1 header and
   `Copyright 2026 Lusoris`.
4. Register C and shader objects, the alphabetical extern, and
   `*_filter_deps="vulkan spirv_compiler"`. Any interop producer or consumer
   using libpelorus separately uses `enabled pelorus_<name>_vulkan_filter &&
   require_pkg_config libpelorus "libpelorus >= 0.2.0" pelorus/interop.h
   pel_blob_pack && add_extralibs $libpelorus_extralibs`; pure transforms do
   not link libpelorus.
5. Add an explicit synthetic generator commit after the existing shipped patch
   sequence. Extend its filename map, `series.txt`, replay patch-count assertion,
   and filter-registration list; never shift existing patch identities. Update
   or derive every hardcoded patch-count label, comment, success message, and
   current AGENTS/docs reference.

For interop, attach `pel_blob_pack` output through an `AVBufferRef` callback
that calls `pel_blob_free`. A new section requires `bump-abi`.

Arithmetic filters use `pelorus_vulkan_sample.h`: scale storage loads into the
logical sample domain and divide owned transform results at store. Whole-texel
copies are exempt. A selected semi-planar chroma plane processes both U and V;
stores start from the input texel so every unowned packed component survives.

## Deliverables and verification

- Per-surface docs, ADR, changelog fragment, rebase note, and package AGENTS
  invariant ship with the implementation.
- Compile canonical and standalone shaders; run the Pelorus fast suite.
- Format/lint touched C and run shellcheck on touched shell.
- Use `ffmpeg-build-patches`, prove byte-stable regeneration, then use
  `ffmpeg-apply-patches` to link the pinned FFmpeg tree.
- Run applicable 8/10/12/16-bit, semi-planar U/V, selected/pass-through plane,
  direct/tiled, and validation-layer cases on available devices.

Keep the pipeline in `AV_PIX_FMT_VULKAN`; never insert an implicit download.
