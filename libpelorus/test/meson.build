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
