---
name: add-vulkan-filter
description: Use when adding a new vf_pelorus_*_vulkan compute filter (denoise, grain-estimate, mc, ...) — scaffolds the reference shader, the in-tree FFmpeg filter, registration, the interop wiring, and every per-PR deliverable (ADR, docs, changelog, patch, rebase note).
---

# /add-vulkan-filter

Scaffold a complete new Pelorus Vulkan filter. The two existing filters are the
canonical templates — copy the one whose shape matches:

- **transform** (frame-in → frame-out, modifies pixels): copy `deband`
  (`ff_vk_filter_process_simple`, inline GLSL, optional side-data emit).
- **analyzer** (pass-through, reads frame → emits side-data via GPU readback):
  copy `analyze` (the `vf_scdet_vulkan` readback pattern).

## Checklist (every box ships in the same PR)

1. **ADR** — `/new-adr <name>-filter`; record the decision + alternatives before coding.
2. **Reference shader** — `libpelorus/shaders/pelorus_<name>.comp`; add it to the
   `reference_shaders` list in `libpelorus/shaders/meson.build` (compiles in CI).
3. **Filter** — `ffmpeg-patches/files/vf_pelorus_<name>_vulkan.c`:
   - `FFVulkanContext` first; lazy `init_filter` on first frame; compute-queue +
     exec pool; descriptor set + push-const block mirroring the C struct (std430);
     inline GLSL via `GLSLC`/`GLSLF`/`GLSLD` — **kept in lockstep with the .comp**.
   - LGPL-2.1 header (`Copyright 2026 Lusoris`), not BSD.
   - If it consumes/produces interop: `#include <pelorus/interop.h>`, pack with
     `pel_blob_pack`, attach via `av_buffer_create(..., pel_sd_free, ...)` +
     `av_frame_new_side_data_from_buf`. (New section ⇒ `/bump-abi`.)
   - Avoid GLSL reserved words as identifiers (`flat`, `sample`, `filter`).
4. **Params contract** (optional) — `libpelorus/include/pelorus/<name>.h` +
   `src/<name>_params.c` if the filter shares tunables with the autotune loop.
5. **Patch** — add the registration block + commit-msg + `series.txt` line per
   `/ffmpeg-build-patches`, then regenerate and verify with `/ffmpeg-apply-patches`.
6. **Docs** — `docs/metrics/<name>.md` (what it does · options · runnable example ·
   output · interactions/limits — the ADR-0100 bar). Update `docs/architecture/overview.md`
   stage table + README modules/landed-so-far.
7. **Changelog** — `changelog.d/added/<adr>-<name>.md`; `/render-changelog`.
8. **Rebase note** — `docs/rebase-notes.md` entry (consumed-surfaces + re-test command).
9. **AGENTS** — note the shader-lockstep pair + any rebase-sensitive invariant.

## Verify

```sh
glslangValidator -V --target-env vulkan1.2 libpelorus/shaders/pelorus_<name>.comp -o /dev/null
meson test -C build --suite=fast
# type-check the filter against real FFmpeg headers (parity with vf_gblur_vulkan):
#   compile the TU in an n8.1.1 worktree; the only acceptable errors are the
#   environmental ff_vk_spirv_init / NULL_IF_CONFIG_SMALL (absent SPIR-V toolchain).
/ffmpeg-apply-patches
```

## Rules

- Zero-copy: the filter stays in `AV_PIX_FMT_VULKAN`; never insert an implicit
  `hwdownload` (principles.md §5).
- Determinism: per-pixel randomness is a hash of `(coord, frame_seed)`, not GPU state.
- Codec-agnostic where possible (deband/denoise/motion help HEVC + AV1 alike);
  only film-grain is codec-specific (AV1 AOM / HEVC-VVC H.274).
- Consider the superpowers skills: `test-driven-development`,
  `verification-before-completion`, `requesting-code-review` before declaring done.
