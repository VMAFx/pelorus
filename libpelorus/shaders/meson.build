# Compile the standalone reference shaders to SPIR-V as a build-time check that
# the GLSL stays valid. These are references, not what ships: since FFmpeg 9 the
# filters execute ffmpeg-patches/files/vulkan/pelorus_*.comp.glsl, compiled to
# SPIR-V during the FFmpeg build (ADR-0143). Both sets are gated here so a broken
# shader fails the local gate rather than an FFmpeg build or a GPU run.

# `required: get_option('shaders')` rather than `required: false`: with the
# option on (the default) a missing glslang is a hard error. It used to degrade
# to a message(), which meant CI could report a green "fast" suite having run
# ZERO shader tests -- a gate that can silently vanish is not a gate.
glslang = find_program('glslangValidator', required: get_option('shaders'))
glslc = find_program('glslc', required: false)

reference_shaders = ['pelorus_deband', 'pelorus_analyze', 'pelorus_denoise',
                     'pelorus_grain_estimate', 'pelorus_mc', 'pelorus_qpmap',
                     'pelorus_dehalo',
                     'pelorus_aa',
                     'pelorus_deblock',
                     'pelorus_borderfix']

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

# The SHIPPED shaders. Compiled with the exact flags FFmpeg's build uses
# (ffbuild/config.mak: GLSLCFLAGS), so a shader that would break an FFmpeg build
# breaks here first. They carry no `#version` line -- `-std=460` supplies it --
# so compiling one without these flags fails confusingly.
shipped_shaders = ['pelorus_deband', 'pelorus_analyze', 'pelorus_denoise',
                   'pelorus_grain_estimate', 'pelorus_mc', 'pelorus_dehalo',
                   'pelorus_aa', 'pelorus_deblock', 'pelorus_borderfix',
                   'pelorus_qpmap']

if glslc.found()
  foreach shader : shipped_shaders
    src = files('../../ffmpeg-patches/files/vulkan/' + shader + '.comp.glsl')
    test('shipped-shader-' + shader + '-compile', glslc,
         args: ['--target-env=vulkan1.4', '--target-spv=spv1.6', '-std=460', '-O',
                src, '-o', '/dev/null'],
         suite: 'fast')
  endforeach
else
  warning('glslc not found — the SHIPPED .comp.glsl shaders are not compile-checked')
endif
