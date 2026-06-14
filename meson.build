# Pelorus — GPU pre-encode pipeline (libpelorus core + FFmpeg patch-stack).
# Meson + Ninja, mirroring the vmafx sibling. See docs/development/build.md.
project(
  'pelorus',
  'c',
  version: '0.1.0',
  license: 'BSD-2-Clause-Patent',
  default_options: [
    'c_std=c11',
    'warning_level=3',
    'werror=true',
    'default_library=shared',
  ],
  meson_version: '>=0.60.0',
)

cc = meson.get_compiler('c')

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
