# libpelorus command-line demonstrators. These are not installed by default;
# they exercise the public ABI end-to-end (e.g. the closed-loop QP-feedback
# reader, ADR-0122) and back the per-PR "reproducer command" deliverable.

tool_deps = [libpelorus_dep]
if host_machine.system() == 'windows'
  # CommandLineToArgvW: the tool re-reads its arguments as UTF-8 from the
  # UTF-16 command line, because libpelorus paths are UTF-8 (ADR-0149).
  tool_deps += cc.find_library('shell32')
endif

pelorus_tool_common = static_library(
  'pelorus_tool_common',
  files('pelorus_tool_common.c'),
  dependencies: tool_deps,
  install: false,
)

pelorus_qp_report = executable(
  'pelorus_qp_report',
  files('pelorus_qp_report.c'),
  link_with: pelorus_tool_common,
  dependencies: tool_deps,
  install: false,
)

pelorus_zones = executable(
  'pelorus_zones',
  files('pelorus_zones.c'),
  link_with: pelorus_tool_common,
  dependencies: tool_deps,
  install: false,
)

pelorus_qloop = executable(
  'pelorus_qloop',
  files('pelorus_qloop.c'),
  link_with: pelorus_tool_common,
  dependencies: tool_deps,
  install: false,
)

pelorus_tool_test = executable(
  'pelorus_tool_test',
  files('pelorus_tool_test.c'),
  link_with: pelorus_tool_common,
  dependencies: tool_deps,
  install: false,
)
test('tool-argv', pelorus_tool_test, suite: 'fast')

# Fast-suite CLI argument validation per tool (#496).
_telemetry_csv = meson.project_source_root() / 'libpelorus' / 'test' / 'fixtures' / 'telemetry' / 'x265-4.3-qp32.csv'
test('tool-qp-report-no-args', pelorus_qp_report, args: [], should_fail: true, suite: 'fast')
test('tool-qp-report-bad-qp', pelorus_qp_report, args: [_telemetry_csv, '--requested-qp', '1x'], should_fail: true, suite: 'fast')
test('tool-qp-report-extra-pos', pelorus_qp_report, args: [_telemetry_csv, 'extra.csv'], should_fail: true, suite: 'fast')
test('tool-qp-report-missing-val', pelorus_qp_report, args: [_telemetry_csv, '--requested-qp'], should_fail: true, suite: 'fast')
test('tool-qp-report-ok', pelorus_qp_report, args: [_telemetry_csv, '--requested-qp', '32.0'], suite: 'fast')

test('tool-zones-no-args', pelorus_zones, args: [], should_fail: true, suite: 'fast')
test('tool-zones-unknown-format', pelorus_zones, args: ['--format', 'invalid'], should_fail: true, suite: 'fast')
test('tool-zones-bad-crf', pelorus_zones, args: ['--format', 'zonefile', '--base-crf', '1x'], should_fail: true, suite: 'fast')
test('tool-zones-extra-pos', pelorus_zones, args: ['--format', 'zonefile', 'extra'], should_fail: true, suite: 'fast')

test('tool-qloop-no-args', pelorus_qloop, args: [], should_fail: true, suite: 'fast')
test('tool-qloop-bad-arg', pelorus_qloop,
     args: ['--target', '1x', '--tolerance', '1.0', '--min', '10.0', '--max', '45.0',
            '--start', '20.0', '--resolution', '0.5', '--max-iter', '10', '--direction', '1'],
     should_fail: true, suite: 'fast')
test('tool-qloop-dup-arg', pelorus_qloop,
     args: ['--target', '90.0', '--target', '90.0', '--tolerance', '1.0', '--min', '10.0', '--max', '45.0',
            '--start', '20.0', '--resolution', '0.5', '--max-iter', '10', '--direction', '1'],
     should_fail: true, suite: 'fast')
test('tool-qloop-missing-val', pelorus_qloop,
     args: ['--target', '90.0', '--tolerance', '1.0', '--min', '10.0', '--max', '45.0',
            '--start', '20.0', '--resolution', '0.5', '--max-iter', '10', '--direction'],
     should_fail: true, suite: 'fast')

bash_prog = find_program('bash', required: false)
if bash_prog.found()
  test('x265-zone-honour',
       bash_prog,
       args: [meson.project_source_root() / 'scripts' / 'quality' / 'x265-zone-honour.sh', pelorus_zones],
       suite: ['slow', 'encoder'])
  test('qloop-x265-reference',
       bash_prog,
       args: [meson.project_source_root() / 'scripts' / 'quality' / 'qloop-x265-reference.sh', pelorus_qloop, pelorus_zones],
       suite: ['slow', 'encoder'])
endif

# Release upgrade probe (scripts/test-abi-upgrade.sh). Built here so the
# clang-tidy lane sees it against the current headers; the script also builds
# it against the previous release. Not on Windows: the script that drives it
# is a bash release step and the probe opens paths with plain fopen.
if host_machine.system() != 'windows'
  abi_upgrade_probe = executable(
    'abi_upgrade_probe',
    files('abi-upgrade/abi_upgrade_probe.c'),
    dependencies: [libpelorus_dep],
    install: false,
  )
endif
