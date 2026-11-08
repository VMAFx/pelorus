# SPDX-FileCopyrightText: 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2

host_pipeline_test = executable(
  'pelorus_host_pipeline_test',
  files(
    'host_pipeline_carrier.c',
    'host_pipeline_common.c',
    'host_pipeline_grain.c',
    'host_pipeline_record.c',
    'host_pipeline_telemetry.c',
    'host_pipeline_test.c',
  ),
  dependencies: libpelorus_dep,
)

test(
  'host-pipeline',
  host_pipeline_test,
  args: [
    meson.current_source_dir() / '..' / 'fixtures' / 'grain' / 'libaom-3.15.2-fgtest1.tbl',
    meson.current_source_dir() / '..' / 'fixtures' / 'telemetry' / 'x265-4.3-qp32.csv',
  ],
  suite: ['fast', 'integration'],
)
