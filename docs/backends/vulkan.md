<!-- markdownlint-disable MD013 -->
# Vulkan compute path

How Pelorus filters run on the GPU, and how to author a new one.

## Prerequisites

- A Vulkan loader + headers (Vulkan 1.2+) and a working ICD for the target GPU
  (NVIDIA / AMD / Intel).
- A SPIR-V compiler FFmpeg can use: libshaderc **or** libglslang — FFmpeg's
  `spirv_library` feature. The filters are gated
  `*_filter_deps="vulkan spirv_library"`.
- `glslangValidator` for the standalone reference-shader compile check
  (optional; `meson` skips it gracefully if absent).

## How a filter runs

Pelorus filters use FFmpeg's libavfilter Vulkan infrastructure
(`libavfilter/vulkan_filter.h`, `libavutil/vulkan.h`), modeled on
`vf_gblur_vulkan.c` / `vf_nlmeans_vulkan.c`:

1. `FFVulkanContext vkctx` is the first struct member; the generic
   `ff_vk_filter_init` / `config_input` / `config_output` populate input/output
   formats from the hardware frames context.
2. The pipeline is built **lazily on the first frame** (formats are only known
   then): find a compute queue (`ff_vk_qf_find(VK_QUEUE_COMPUTE_BIT)`), init an
   exec pool, declare the push-constant block + storage-image descriptor set,
   emit GLSL (inline via the `GLSLC`/`GLSLF`/`GLSLD` macros), compile to SPIR-V,
   link, register.
3. Per frame: `ff_vk_filter_process_simple(out, in, ...)` binds, pushes
   constants, barriers, dispatches `ceil(w/lg)×ceil(h/lg)`, submits — all in VRAM.
4. `av_frame_copy_props(out, in)` then (optionally) attach Pelorus side data.

## Authoring rules

- **Push constants mirror the C struct byte-for-byte** in std430: `vec4`/`uvec4`
  first, then 64-bit, then scalars. The emitted GLSL block field order/types
  must match the C struct.
- **Determinism**: per-pixel randomness is a hash of `(coord, frame_seed)`, not
  GPU state.
- **Lockstep**: a standalone reference shader under `libpelorus/shaders/*.comp`
  documents the algorithm and is compiled in CI; the in-tree filter embeds an
  equivalent via the GLSL macros. Edit both together (AGENTS.md hard rule 4).
- **Avoid GLSL reserved words** as identifiers (`flat`, `sample`, …).
- See [docs/principles.md §5](../principles.md) for the full Vulkan-usage
  contract (zero-copy, validation-clean, one queue family, lazy init).

## Numerical note

The standalone reference shader works in a 16-bit integer domain (`r16ui`); the
in-tree filter works in FFmpeg's normalized float image domain
(`FF_VK_REP_FLOAT`), so thresholds/grain are already in `[0,1]` and the explicit
dither-down stage collapses (the output image format carries the depth). The two
implement the same algorithm; the only difference is the working domain.

## Building the FFmpeg integration

```bash
ninja -C build install                       # install libpelorus (pkg-config)
cd ffmpeg-patches && ./generate.sh            # regenerate the patch stack
./test/build-and-run.sh                       # apply onto n8.1.1, build, smoke
```
