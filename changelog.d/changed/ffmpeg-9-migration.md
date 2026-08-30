- **FFmpeg base tag moves n8.1.1 → n9.0.1**, and every Vulkan shader moves from a
  runtime-constructed GLSL string to **precompiled SPIR-V**. FFmpeg 9 deleted the inline-GLSL
  builder outright (`GLSLC`/`GLSLA`/`GLSLF`/`GLSLD`, `FFVulkanShader.src`, `ff_vk_shader_init`,
  `ff_vk_shader_print`), replacing it with `ff_vk_shader_load` + `ff_vk_shader_link` over a
  shader compiled at build time from `libavfilter/vulkan/pelorus_<name>.comp.glsl`. Also
  handled: `spirv_library` → `spirv_compiler` in `configure`, `ff_vk_shader_add_descriptor_set`
  becoming `void` and losing its `print_to_shader_only` argument, a new `uint32_t wgc_z`
  parameter on `ff_vk_filter_process_simple`/`_2pass`/`_Nin`, and NVENC's minimum SDK moving to
  11.1 (which deleted `NVENC_HAVE_QP_MAP_MODE`). Plane counts and plane bitmasks that the C
  code used to const-fold into generated GLSL are now specialization constants. **This retires
  AGENTS.md hard rule 4**: the `.comp` reference shader and the filter's inline copy are no
  longer two hand-synchronised sources, so the lockstep-drift defect class behind ADR-0129 is
  designed out rather than policed. ADR-0143.
