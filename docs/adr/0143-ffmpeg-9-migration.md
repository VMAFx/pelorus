<!-- markdownlint-disable MD013 -->
# ADR-0143: FFmpeg 9 migration — precompiled SPIR-V replaces the runtime inline-GLSL shader model

- **Status**: Proposed (2026-08-30) — base tag moves n8.1.1 → n9.0.1; all 11 Vulkan shaders move from runtime-built GLSL strings to build-time SPIR-V
- **Date**: 2026-08-30
- **Deciders**: Lusoris

## Context

FFmpeg 9.0.1 was released 2026-08-12. The Pelorus patch stack was pinned to n8.1.1,
two releases behind (n8.1.2, then 9.0/9.0.1). The sibling repo `VMAFx/vmafx` had already
migrated its own stack (its commit `7f6e6356b`, "migrate the patch stack from n8.1.1 to
n9.0.1"), and reported that **no API changed** and only two of seventeen patches needed
regeneration for line drift.

That result does not transfer. vmafx's patches are filter-only edits around
`vf_libvmaf.c`. Pelorus (a) hand-diffs five upstream encoder translation units and
(b) builds every one of its Vulkan shaders as a **runtime-constructed GLSL string**.
FFmpeg 9 deleted exactly that mechanism.

Measured against a real n9.0.1 checkout, `libavutil/vulkan.h` lost:

- the GLSL macro system — `GLSLC`, `GLSLA`, `GLSLF`, `GLSLD`, `INDENT*`, `C(N,S)`;
- `FFVulkanShader.src`, the `AVBPrint` those macros wrote into;
- `ff_vk_shader_init()` and `ff_vk_shader_print()`.

and gained a precompiled-SPIR-V path: `ff_vk_shader_load(shd, stage, spec, wg_size,
subgroup)` plus `ff_vk_shader_link(s, shd, spirv, spirv_len, entrypoint)`, with shader
source living at `libavfilter/vulkan/<name>.comp.glsl` and compiled by existing
`ffbuild/common.mak` rules (`%.spv: %.glsl` → `bin2c` → `.spv.o`) into a linked-in symbol
pair `ff_<name>_comp_spv_data[]` / `_len`.

Four further breaks were found the same way, three of them only by compiling:

- `configure`: `spirv_library` → `spirv_compiler` (0 occurrences of the old name remain).
- `ff_vk_shader_add_descriptor_set()` returns `void` and lost its
  `print_to_shader_only` argument.
- `ff_vk_filter_process_simple()` / `_2pass()` / `_Nin()` all gained a `uint32_t wgc_z`
  parameter before `push_src`.
- NVENC: n9 raised the minimum SDK to 11.1 and deleted the old version-gate macros,
  including `NVENC_HAVE_QP_MAP_MODE`, which patch 0004 keyed off.

## Decision

Move the base tag to **n9.0.1** and adopt the precompiled-SPIR-V shader model.

1. **The `.comp.glsl` becomes the single source of truth for each shader.** Shader text
   ships at `ffmpeg-patches/files/vulkan/pelorus_<name>.comp.glsl`, is installed to
   `libavfilter/vulkan/`, and is registered in `libavfilter/vulkan/Makefile`.
2. **Values the C code used to const-fold into generated GLSL become specialization
   constants.** Several filters unrolled a per-plane loop in C using the plane count and
   the `planes` bitmask; precompiled SPIR-V cannot. They become `constant_id` 0/1 fed by
   `SPEC_LIST_ADD`, with the shader looping over `planes`. IDs 253/254/255 are reserved
   by FFmpeg for the workgroup size.
3. **Control flow is preserved literally, not improved.** The old generator emitted
   `if (!IS_WITHIN(pos, size)) return;` per plane, so the first plane not containing the
   position ends the invocation and subsampled chroma is skipped for luma-only positions.
   The loop reproduces that with `return`, not `continue`. This is a rebase, not a
   redesign.
4. **The encoder hand-diffs are rebased, not redesigned** — same hook points, or the
   place upstream moved that logic to, called out explicitly per patch.

## Consequences

**AGENTS.md hard rule 4 is retired, and that is the main prize.** Pelorus previously
carried each shader twice: a reference `libpelorus/shaders/pelorus_<name>.comp` and a
hand-mirrored inline-GLSL copy inside the filter. The rule, the `shader-lockstep-warn`
hook, and a standing review burden all existed to police that duplication — and it had
already produced a real defect: ADR-0129's malformed split `GLSLF` emitted raw macro body
into the shader and failed compilation at runtime with shaderc `-22`, latent in two
filters and active in three. With one compiled source, that entire defect class is
designed out rather than policed.

**The shader gate gets stronger.** Shaders now fail the *build* rather than device
initialisation, so a broken shader can no longer reach a release as a runtime-only
failure — which is precisely how ADR-0129 escaped every static gate.

**Costs and risks.**

- Binary size grows slightly: SPIR-V is linked in rather than built on demand
  (mitigated upstream by `CONFIG_SHADER_COMPRESSION` gzip).
- Specialization constants replace const-folding, so a shader is now compiled once for
  all plane configurations instead of specialised per instance. No measured cost; the
  driver specialises at pipeline creation.
- **Binding order is now hand-written and unchecked by the compiler.** The `.glsl`
  `layout(set=,binding=)` declarations must match the C descriptor array order exactly;
  a mismatch silently reads the wrong image rather than failing to build. This replaces
  one silent-failure mode (lockstep drift) with a narrower one, and is the reason every
  migrated filter was reviewed specifically for binding order and push-constant offsets.
- **On-device execution has now been run, and passes.** ADR-0129's lesson (build-green
  is not runs-green) was the reason to gate on it, so this migration was verified on
  real hardware rather than declared done at compile time:
  - All 9 Vulkan filters execute on **three vendors** — NVIDIA RTX 4090, Intel Arc A380
    and AMD RADV (`vk:0/1/2`), 9/9 on each. The migration is not NVIDIA-specific.
  - The `planes=1` (luma-only) regime — the exact path the ADR-0129 defect lived in —
    gives **`u:inf v:inf`** against the unfiltered source for deband, denoise, dehalo,
    aa, deblock and borderfix: chroma is bit-exact, not merely non-crashing.
  - The three pass-through analyzers (analyze, grain_estimate, mc) report
    `y:inf u:inf v:inf`, i.e. they genuinely do not touch the frame.
  - `meta=1` round-trips through `libpelorus` at runtime, and `analyze` emits its
    `lavfi.pelorus.*` frame metadata.
  - A five-filter chain (borderfix → deblock → dehalo → aa → deband) runs on real
    2160p Big Buck Bunny content.
  - **Still not covered**: the Vulkan validation layers are not installed on this box
    (`vulkan-validation-layers` is packaged but needs a privileged install). The
    ADR-0129 `mc` descriptor bug was the kind only a strict driver or the layers
    reject, so a validation-enabled run remains a worthwhile follow-up.

## References

- FFmpeg n9.0.1 (`bf1b838f2a`), `libavutil/vulkan.h`, `libavfilter/vulkan/Makefile`,
  `ffbuild/common.mak`, and `libavfilter/vf_nlmeans_vulkan.c` / `vf_scdet_vulkan.c` as
  the upstream reference implementations of the new model.
- `VMAFx/vmafx` commit `7f6e6356b` — the sibling migration (filter-only, no API change).
- ADR-0104 (the patch-stack delivery model), ADR-0129 (the lockstep defect this retires).
- `.workingdir/FFMPEG9-MIGRATION-BRIEF.md` — the measured ground truth for this change.
