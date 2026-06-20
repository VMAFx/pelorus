# Compile the standalone reference shaders to SPIR-V as a build-time check that
# the GLSL is valid (the in-tree FFmpeg filter compiles its inline copy at
# runtime; this keeps the reference shader honest). glslang is optional — skip
# gracefully when it is not installed.

glslang = find_program('glslangValidator', required: false)

reference_shaders = ['pelorus_deband', 'pelorus_analyze', 'pelorus_denoise',
                     'pelorus_grain_estimate', 'pelorus_mc', 'pelorus_qpmap',
                     'pelorus_dehalo',
                     'pelorus_aa']

if glslang.found()
  foreach shader : reference_shaders
    custom_target(
      shader + '.spv',
      input: shader + '.comp',
      output: shader + '.spv',
      command: [glslang, '-V', '--target-env', 'vulkan1.2', '@INPUT@', '-o', '@OUTPUT@'],
      build_by_default: true,
    )
    test('shader-' + shader + '-compile', glslang,
         args: ['-V', '--target-env', 'vulkan1.2',
                files(shader + '.comp'), '-o', '/dev/null'],
         suite: 'fast')
  endforeach
else
  message('glslangValidator not found — skipping reference-shader compilation')
endif
