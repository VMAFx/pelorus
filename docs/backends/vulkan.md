<!-- markdownlint-disable MD013 -->
# Vulkan compute path

How Pelorus filters run on the GPU, and how to author a new one.

## Prerequisites

- A Vulkan loader + headers (Vulkan 1.2+) and a working ICD for the target GPU
  (NVIDIA / AMD / Intel).
- A **build-time** SPIR-V compiler: `glslc` (or `glslang`/`glslangValidator`) on
  PATH. FFmpeg 9 probes it with `check_glslc` and enables the `spirv_compiler`
  feature; the filters are gated `*_filter_deps="vulkan spirv_compiler"`.
  This replaced FFmpeg 8's `spirv_library` (libshaderc/libglslang linked into the
  binary to compile GLSL **at runtime**), which FFmpeg 9 removed — see ADR-0143.
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
   exec pool, `ff_vk_shader_load()` the shader (passing a `SPEC_LIST_CREATE`
   specialization list for anything the C side must fold in, such as the plane
   count and plane bitmask), declare the push-constant block + storage-image
   descriptor set with `ff_vk_shader_add_descriptor_set()`, then
   `ff_vk_shader_link()` against the **precompiled SPIR-V** linked in as
   `ff_pelorus_<name>_comp_spv_data[]`, and register with the exec pool.
   The shader itself is `libavfilter/vulkan/pelorus_<name>.comp.glsl`, compiled
   by `glslc` during the FFmpeg build (`%.spv: %.glsl` → `bin2c` → `.spv.o`).
   Nothing builds GLSL text at runtime any more (ADR-0143).
3. Per frame: `ff_vk_filter_process_simple(out, in, sampler, wgc_z, push, size)`
   binds, pushes constants, barriers, dispatches `ceil(w/lg)×ceil(h/lg)`,
   submits — all in VRAM. (`wgc_z` is new in FFmpeg 9; pass `1` for 2D filters.)
4. `av_frame_copy_props(out, in)` then (optionally) attach Pelorus side data.

## Authoring rules

- **Push constants mirror the C struct byte-for-byte** in std430: `vec4`/`uvec4`
  first, then 64-bit, then scalars. The `.comp.glsl` block field order/types must
  match the C struct. **Nothing checks this** — a `vec4` alignment mistake
  silently shifts every later field rather than failing the build, so walk the
  offsets by hand when you change either side.
- **Binding order is hand-maintained.** The `layout(set=,binding=)` declarations
  in the `.comp.glsl` must match the `FFVulkanDescriptorSetBinding` array order
  in the filter exactly. A swap silently reads the wrong image; it is not a
  compile error.
- **Specialization constants** carry anything the C side used to const-fold into
  generated GLSL (plane count, plane bitmask). `constant_id` 0..N — **253/254/255
  are reserved** by `ff_vk_shader_load()` for the workgroup size.
- **Determinism**: per-pixel randomness is a hash of `(coord, frame_seed)`, not
  GPU state.
- **One shader source.** `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl` is
  what ships. The `libpelorus/shaders/*.comp` files are standalone references
  compiled by the fast gate, **not** a second implementation to hand-synchronise
  — never re-introduce inline GLSL (ADR-0143; the old lockstep rule is retired).
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
