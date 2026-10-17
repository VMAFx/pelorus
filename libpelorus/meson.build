# libpelorus — the shared core: the Pelorus<->vmafx interop ABI plus the filter
# parameter contracts. Both the FFmpeg vf_pelorus_* filters and the vmafx
# autotune loop link this library / vendor the same translation units.

libpelorus_inc = include_directories('include')
libpelorus_deps = []
libpelorus_locale_prefix = '''
#include <locale.h>
#include <stdlib.h>
'''
libpelorus_locale_probe_args = host_machine.system() == 'linux' ? ['-D_GNU_SOURCE'] : []

if host_machine.system() != 'windows'
  libpelorus_deps += cc.find_library('m', required: true)
endif

if host_machine.system() == 'windows'
  libpelorus_locale_symbols = ['_create_locale', '_free_locale', '_strtod_l']
  libpelorus_locale_platform = 'MSYS2 UCRT'
else
  libpelorus_locale_symbols = ['newlocale', 'freelocale', 'strtod_l']
  libpelorus_locale_platform = 'POSIX'
endif

foreach symbol : libpelorus_locale_symbols
  if not cc.has_function(symbol,
    prefix: libpelorus_locale_prefix,
    args: libpelorus_locale_probe_args,
  )
    error('libpelorus telemetry adapters require ' + libpelorus_locale_platform +
          ' locale function ' + symbol)
  endif
endforeach

libpelorus_sources = files(
  'src/interop.c',
  'src/qp_report_csv.c',
  'src/telemetry_adapters.c',
  'src/telemetry.c',
  'src/encode_record.c',
  'src/encode_options.c',
  'src/encode_options_x264.c',
  'src/encode_options_x265.c',
  'src/encode_options_svtav1.c',
  'src/encode_options_aom.c',
  'src/sha256.c',
  'src/deband_params.c',
  'src/denoise_params.c',
  'src/version.c',
)

libpelorus_headers = files(
  'include/pelorus/pelorus.h',
  'include/pelorus/interop.h',
  'include/pelorus/telemetry.h',
  'include/pelorus/encode_record.h',
  'include/pelorus/encode_options.h',
  'include/pelorus/deband.h',
  'include/pelorus/denoise.h',
)

libpelorus = library(
  'pelorus',
  libpelorus_sources,
  include_directories: libpelorus_inc,
  dependencies: libpelorus_deps,
  version: pelorus_core_version,
  soversion: '0',
  install: true,
  gnu_symbol_visibility: 'default',
)

install_headers(libpelorus_headers, subdir: 'pelorus')

libpelorus_dep = declare_dependency(
  link_with: libpelorus,
  include_directories: libpelorus_inc,
  dependencies: libpelorus_deps,
)

pkg = import('pkgconfig')
# No `subdirs:` — headers install to ${includedir}/pelorus/ and are included as
# <pelorus/interop.h>, so the cflags must point at ${includedir} itself.
pkg.generate(
  libpelorus,
  name: 'libpelorus',
  description: 'Pelorus GPU pre-encode core: side-data interop ABI + filter contracts',
  version: pelorus_core_version,
)

if get_option('tests')
  subdir('test')
endif

if get_option('shaders')
  subdir('shaders')
endif
