# libpelorus unit + conformance tests.

# The fixture-file checks (ADR-0148) use POSIX.1-2008 open() flags, lstat() and
# symlink(); strict c11 hides them on glibc. vmafx builds its vendored copy with
# _GNU_SOURCE, which exposes the same set. On Windows the checks call advapi32,
# which Meson's default c_winlibs links (MSVC also via #pragma comment).
interop_test_args = []
if host_machine.system() != 'windows'
  interop_test_args += '-D_POSIX_C_SOURCE=200809L'
endif

interop_test = executable(
  'pelorus_interop_test',
  files('interop_test.c'),
  c_args: interop_test_args,
  dependencies: libpelorus_dep,
)

# The shared ABI conformance fixture — vmafx runs the same checks against its
# vendored copy of interop.c, so an N+1-minor blob stays parseable by an
# N-minor consumer. See docs/adr/0103-interop-sidedata-abi.md.
test('interop-abi', interop_test, suite: 'fast')

# ADR-0149: every libpelorus path argument is UTF-8. Built and run on every
# platform: on Windows it asserts the non-ASCII _wfopen round trip, ill-formed
# UTF-8 -> PEL_ERR_INVALID and a \\?\ path past MAX_PATH; on POSIX the literal
# fopen pass-through (a non-UTF-8 byte name still opens). Kept apart from the
# vmafx-shared conformance fixture above: vmafx mirrors interop_test.c only.
path_utf8_test = executable(
  'pelorus_path_utf8_test',
  files('path_utf8_test.c'),
  dependencies: libpelorus_dep,
)
test('path-utf8', path_utf8_test, suite: 'fast')

# ABI 1.4 encoder telemetry (ADR-0174): validation, non-allocating pack, the map
# reader, and the #221 rule that pelorus/telemetry.h compiles with no FFmpeg
# include path (this target has none). Not part of the VMAFx-mirrored fixture:
# telemetry.c is not mirrored at RC4.
telemetry_test = executable(
  'pelorus_telemetry_test',
  files('telemetry_test.c'),
  dependencies: libpelorus_dep,
)
test('enc-telemetry', telemetry_test, suite: 'fast')

# ADR-0175 encode record: SHA-256 (FIPS 180-4 vectors), canonical JSON, the
# worked example's digest from docs/api/encode-record.md, verify and mutations.
# Includes the private src/sha256.h to test the port directly.
encode_record_test = executable(
  'pelorus_encode_record_test',
  files('encode_record_test.c'),
  include_directories: include_directories('../src'),
  dependencies: libpelorus_dep,
)
test('encode-record', encode_record_test,
     args: [meson.project_source_root() / 'docs' / 'api' / 'encode-record.md'],
     suite: 'fast')
