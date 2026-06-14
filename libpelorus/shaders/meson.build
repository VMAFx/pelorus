# Compile the standalone reference shaders to SPIR-V as a build-time check that
# the GLSL is valid (the in-tree FFmpeg filter compiles its inline copy at
# runtime; this keeps the reference shader honest). glslang is optional — skip
# gracefully when it is not installed.

glslang = find_program('glslangValidator', required: false)

if glslang.found()
  pelorus_deband_spv = custom_target(
    'pelorus_deband.spv',
    input: 'pelorus_deband.comp',
    output: 'pelorus_deband.spv',
    command: [glslang, '-V', '--target-env', 'vulkan1.2', '@INPUT@', '-o', '@OUTPUT@'],
    build_by_default: true,
  )
  test('shader-deband-compile', glslang,
       args: ['-V', '--target-env', 'vulkan1.2',
              files('pelorus_deband.comp'), '-o', '/dev/null'],
       suite: 'fast')
else
  message('glslangValidator not found — skipping reference-shader compilation')
endif
