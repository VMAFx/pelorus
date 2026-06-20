# libpelorus — the shared core: the Pelorus<->vmafx interop ABI plus the filter
# parameter contracts. Both the FFmpeg vf_pelorus_* filters and the vmafx
# autotune loop link this library / vendor the same translation units.

libpelorus_inc = include_directories('include')

libpelorus_sources = files(
  'src/interop.c',
  'src/qp_report_csv.c',
  'src/deband_params.c',
  'src/denoise_params.c',
  'src/version.c',
)

libpelorus_headers = files(
  'include/pelorus/pelorus.h',
  'include/pelorus/interop.h',
  'include/pelorus/deband.h',
  'include/pelorus/denoise.h',
)

libpelorus = library(
  'pelorus',
  libpelorus_sources,
  include_directories: libpelorus_inc,
  version: meson.project_version(),
  soversion: '0',
  install: true,
  gnu_symbol_visibility: 'default',
)

install_headers(libpelorus_headers, subdir: 'pelorus')

libpelorus_dep = declare_dependency(
  link_with: libpelorus,
  include_directories: libpelorus_inc,
)

pkg = import('pkgconfig')
# No `subdirs:` — headers install to ${includedir}/pelorus/ and are included as
# <pelorus/interop.h>, so the cflags must point at ${includedir} itself.
pkg.generate(
  libpelorus,
  name: 'libpelorus',
  description: 'Pelorus GPU pre-encode core: side-data interop ABI + filter contracts',
  version: meson.project_version(),
)

if get_option('tests')
  subdir('test')
endif

if get_option('shaders')
  subdir('shaders')
endif
