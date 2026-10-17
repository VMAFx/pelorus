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
- **Distinguish storage and sample domains.** `FF_VK_REP_FLOAT` normalizes an
  integer image by its Vulkan storage container. It does not expand an
  LSB-aligned 10/12-bit code in `R16_UNORM` to the logical `[0,1]` sample range.
  Arithmetic filters obtain `sample_scale` from `AVPixFmtDescriptor`, multiply
  every load before doing math, then clamp and round transform results to the
  logical `code_max` before inverse scaling at the store boundary:
  `round(clamp(value, 0, 1) * code_max) / code_max / sample_scale`. `code_max`
  is a specialization constant, keeping denoise's push block within Vulkan's
  guaranteed 128-byte minimum. P010/P012 also require the descriptor `shift`;
  quantizing before inverse scaling maps the logical code back into the shifted
  storage lane and keeps its low padding bits zero. Raw whole-texel copies such
  as borderfix are exempt. `scripts/check-vulkan-storage-domain.py` enforces
  this family contract
  ([ADR-0147](../adr/0147-vulkan-sample-domain-and-components.md)).
- **Plane masks select physical planes.** A selected NV12/P010/P012 chroma
  plane contains both U and V, so scalar kernels must process both components
  under a specialization-time loop. On packed views they process only their
  defined component and preserve every other lane with read-modify-write.
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

Thresholds and strengths are expressed in the logical `[0,1]` sample domain.
The shipped shader establishes that domain explicitly with `sample_scale`, and
known integer layouts use `code_max` quantization before storage-domain
writeback; a storage-image load is not assumed to be logical already. Standalone
`libpelorus/shaders/*.comp` files use their documented integer reference domain
and exist to compile-check/read the algorithm, not as a second shipped shader.

## Input formats and interlacing

Every Pelorus Vulkan filter checks the software layout of its input frames when
the graph is configured and refuses a layout outside the measured set with
`<filter>: input format <name> is not supported; supported: <list>`
([ADR-0201](../adr/0201-vulkan-input-format-allow-list.md)). The set lives in
`ffmpeg-patches/files/pelorus_vulkan_input.h`.

| Layout | deband, mc, analyze, grain_estimate, borderfix | aa, dehalo, deblock, denoise |
| --- | --- | --- |
| `yuv420p`, `yuv422p`, `yuv444p` at 8, 10, 12, 16 bit | accepted | accepted |
| `nv12`/`p010`/`p012`/`p016`, `nv16`/`p210`/`p212`/`p216`, `nv24`/`p410`/`p412`/`p416` | accepted | accepted |
| `yuva420p` (alpha passes through unless `planes` selects it) | accepted | accepted |
| `rgba` (G, B, A preserved) | refused | accepted |
| anything else (`yuyv422`, `gbrp`, `gray`, `yuva444p`, ...) | refused | refused |

- The Vulkan hwcontext has no 9-bit or 14-bit YUV layout, so 8 and 16 bit are
  the depth boundaries the matrix tests.
- Convert other layouts in the graph first, for example
  `scale_vulkan=format=nv12` or `format=nv12` before `hwupload`.

A refused layout stops the graph at configuration with `EINVAL` (output from
the v0.4.0 stack plus this change, list shortened):

```console
$ ffmpeg -init_hw_device vulkan -f lavfi -i testsrc2=s=128x64:d=0.1 \
    -vf format=yuyv422,hwupload,pelorus_deband_vulkan,hwdownload,format=yuyv422 \
    -f null -
pelorus_deband_vulkan: input format yuyv422 is not supported; supported: yuv420p yuv420p10le ... yuva420p
Failed to configure input pad on Parsed_pelorus_deband_vulkan_2
```

Replacing `format=yuyv422` with `format=nv12` on both sides runs the same graph.

Known defect: on NVIDIA, aa and dehalo write wrong luma in the last 3-4 columns
of nv12 and nv24 frames whose width is not a multiple of 64
([#358](https://github.com/VMAFx/pelorus/issues/358)).

Interlaced frames (`AV_FRAME_FLAG_INTERLACED`): deband, denoise, mc, dehalo, aa
and deblock read neighbouring rows, which belong to the other field, so they
refuse such a frame with `<filter>: interlaced input (AV_FRAME_FLAG_INTERLACED)
is not supported`. Deinterlace first (`bwdif_vulkan`), or mark frames that are
progressive with `setfield=prog`. analyze, grain_estimate, borderfix and
scenecut accept interlaced frames and keep the flag.

Proof: `ffmpeg-patches/test/vulkan-format-matrix.sh` compares every accepted
layout with the yuv420p result, checks yuva420p alpha pass-through, and runs
each refusal and interlace row.

## Devices with one queue family

On a device with a single queue family, such as Mesa lavapipe, FFmpeg creates
frames as `VK_SHARING_MODE_EXCLUSIVE` images owned by that family. Stock FFmpeg
n9.0.2 then turns the first barrier on each frame into an ownership transfer to
`VK_QUEUE_FAMILY_IGNORED`, which the specification forbids
(`VUID-VkImageMemoryBarrier2-image-09118`). The validation layer then loses
the image layout: it reports `09059` and `09064` on the `hwupload` and
`hwdownload` host copies of every Vulkan filter chain, and `00344` and `09600`
where a shader indexes the image array with a literal (`grain_estimate`).
Patch 0001 of the shared FFmpeg fix series (Pelorus patch 0021 until
[ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md); FFmpeg pull request 24970)
keeps the frame's owning family in that barrier, so no transfer is formed.
Devices with several queue families use `VK_SHARING_MODE_CONCURRENT` and are not
affected. With the patch no tested device (lavapipe, ANV, RADV, RTX 4090) reports
`09059` or `09064`, so `ffmpeg-patches/test/vulkan-vuid-allowlist.txt` no longer
lists them and a run that shows either fails validation. Root cause and
measurements: `docs/rebase-notes.md` (the patch 0021 entry).

## Output pools for VAAPI and QSV

The filters that write new frames allocate FFmpeg's OPTIMAL pool by default,
which cannot be mapped to VAAPI. `tiling=drm` gives them a DRM-format-modifier
pool that `hwmap=derive_device=vaapi` maps without `hwdownload`; the modifier
rule and current limits are in
[Vulkan output pools for VAAPI and QSV](vulkan-drm-modifiers.md)
([ADR-0184](../adr/0184-vulkan-drm-modifier-output-pools.md)).

## Building the FFmpeg integration

```bash
ninja -C build install                       # install libpelorus (pkg-config)
FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/generate.sh
FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh
```
