# libpelorus unit + conformance tests.

interop_test = executable(
  'pelorus_interop_test',
  files('interop_test.c'),
  dependencies: libpelorus_dep,
)

# The shared ABI conformance fixture — vmafx runs the same checks against its
# vendored copy of interop.c, so an N+1-minor blob stays parseable by an
# N-minor consumer. See docs/adr/0103-interop-sidedata-abi.md.
test('interop-abi', interop_test, suite: 'fast')
