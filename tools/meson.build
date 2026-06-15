# libpelorus command-line demonstrators. These are not installed by default;
# they exercise the public ABI end-to-end (e.g. the closed-loop QP-feedback
# reader, ADR-0122) and back the per-PR "reproducer command" deliverable.

pelorus_qp_report = executable(
  'pelorus_qp_report',
  files('pelorus_qp_report.c'),
  dependencies: libpelorus_dep,
  install: false,
)
