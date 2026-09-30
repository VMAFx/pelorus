# libpelorus command-line demonstrators. These are not installed by default;
# they exercise the public ABI end-to-end (e.g. the closed-loop QP-feedback
# reader, ADR-0122) and back the per-PR "reproducer command" deliverable.

tool_deps = [libpelorus_dep]
if host_machine.system() == 'windows'
  # CommandLineToArgvW: the tool re-reads its arguments as UTF-8 from the
  # UTF-16 command line, because libpelorus paths are UTF-8 (ADR-0149).
  tool_deps += cc.find_library('shell32')
endif

pelorus_qp_report = executable(
  'pelorus_qp_report',
  files('pelorus_qp_report.c'),
  dependencies: tool_deps,
  install: false,
)
