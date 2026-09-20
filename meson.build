# Pelorus — GPU pre-encode pipeline (libpelorus core + FFmpeg patch-stack).
# Meson + Ninja, mirroring the vmafx sibling. See docs/development/build.md.
project(
  'pelorus',
  'c',
  version: '0.2.2',
  license: 'BSD-2-Clause-Patent',
  default_options: [
    'c_std=c11',
    'warning_level=3',
    'werror=true',
    'default_library=shared',
  ],
  meson_version: '>=0.60.0',
)

fs = import('fs')
cc = meson.get_compiler('c')

# The version lives in two places: this project() call and pelorus.h's
# PELORUS_VERSION_* macros (the ABI-visible copy a consumer compiles against).
# Nothing used to assert they agreed, so they could silently diverge. Parse the
# header and fail configuration if they do.
_pelorus_h = fs.read('libpelorus/include/pelorus/pelorus.h')
_ver_str = _pelorus_h.split('#define PELORUS_VERSION_STR "')[1].split('"')[0]
if _ver_str != meson.project_version()
  error('version skew: meson.build says @0@ but pelorus.h PELORUS_VERSION_STR says @1@'
        .format(meson.project_version(), _ver_str))
endif

# Power-of-10 / CERT-leaning baseline: keep the warning surface wide. Project
# code is warning-clean to -Wall -Wextra; add a few high-signal extras.
add_project_arguments(
  cc.get_supported_arguments([
    '-Wshadow',
    '-Wpointer-arith',
    '-Wstrict-prototypes',
    '-Wmissing-prototypes',
    '-Wvla',
  ]),
  language: 'c',
)

subdir('libpelorus')

if get_option('tools')
  subdir('tools')
endif

# Since ADR-0143 each filter's GLSL binding declarations and its C
# FFVulkanDescriptorSetBinding array are two hand-maintained lists that must agree.
# Nothing in the compiler or the FFmpeg build checks that -- a swapped pair silently
# reads the wrong resource, which is corruption rather than a build failure. This is
# that check, and it is cheap enough to live in the fast gate.
python_prog = find_program('python3', required: true)
test('shader-binding-order',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-shader-bindings.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

test('build-config-sync',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-build-config.py',
            '--self-test'],
     workdir: meson.project_source_root(),
     suite: 'fast')

# ADR-0147: FF_VK_REP_FLOAT exposes the storage container's UNORM domain, not
# necessarily the logical sample domain.  Keep conversion and component
# preservation consistent across the entire arithmetic-filter family.
test('vulkan-storage-domain',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'check-vulkan-storage-domain.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')

test('vulkan-sample-scale',
     python_prog,
     args: [meson.project_source_root() / 'scripts' / 'test-vulkan-sample-scale.py'],
     workdir: meson.project_source_root(),
     suite: 'fast')
