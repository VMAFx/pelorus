---
name: ffmpeg-build-patches
description: Use when regenerating the FFmpeg patch stack after changing canonical inputs under ffmpeg-patches/files or commit-message templates.
---

# Regenerate the FFmpeg patch stack

`files/`, `.commit-msg-*.txt`, and `generate.sh` are source; numbered patches
are deterministic artifacts. Root `build-config.env` owns the exact FFmpeg tag
and commit.

## Regenerate

From the repository root:

```sh
FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/generate.sh
```

`FFMPEG_REPO` is required. `BASE_TAG` is not an input. The generator verifies
the qualified tag against the pinned commit, uses a run-owned hook-neutral
worktree, and preserves the caller checkout.

Run the command twice and prove every numbered patch is byte-identical. Then
use `ffmpeg-apply-patches`; regeneration alone is not verification.

## Add a filter without renumbering shipped patches

1. Add host C under `ffmpeg-patches/files/` and the one shipped shader source at
   `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`.
2. Add `.commit-msg-<name>.txt` and an explicit synthetic commit block after the
   existing shipped sequence. Do not insert it into an earlier generator loop.
3. Use `install_vk_shader`; register the C object, shader `.spv.o`, extern, and
   `*_filter_deps="vulkan spirv_compiler"`.
4. For any interop producer or consumer using libpelorus, add
   `enabled pelorus_<name>_vulkan_filter && require_pkg_config libpelorus
   "libpelorus >= 0.2.0" pelorus/interop.h pel_blob_pack && add_extralibs
   $libpelorus_extralibs`. Pure transforms do not link it.
5. Extend the generator's deterministic filename map, `series.txt`, replay
   patch-count assertion, and filter-registration list together. Update or
   derive every hardcoded patch-count label, success message, comment, and
   current AGENTS/docs reference; do not leave an “18 patches” cache behind.

`libpelorus/shaders/*.comp` may model the algorithm for the standalone fast
gate; it is not a second shipped implementation or a lockstep artifact.

## Completion

- source and generated patches are in the same change;
- two generations are byte-identical;
- `python3 scripts/check-build-config.py --self-test` passes; and
- full pinned replay links FFmpeg and verifies filter plus BSF registration.
