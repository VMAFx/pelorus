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
