<!-- markdownlint-disable MD013 -->
# Agent guide — tools/

libpelorus command-line demonstrators. Parent: [../AGENTS.md](../AGENTS.md).
They exercise public ABI end-to-end and back per-PR "reproducer command"
deliverable (ADR-0108). Gated by `tools` meson option (default on).
**Not installed**: dev/demonstration binaries, not shipped surface.

## Scope

```text
tools/
├── pelorus_qp_report.c   x265 --csv -> PEL_SEC_QPREPORT demonstrator (ADR-0122)
└── tester/               tester report program, stage runners, fixtures + lock, schema; Python stdlib only (ADR-0173)
```

## Conventions

- Link only `libpelorus_dep`; no encoder SDK, no Vulkan. Tool may `printf` to
  stdout/stderr: executable, not embeddable library code. AGENTS.md §3
  no-stdio rule covers `libpelorus/src`, not here.
- Every libpelorus call `pel_result`-checked; error -> non-zero exit with
  `pel_result_str`.
- Path handed to libpelorus = UTF-8 (ADR-0149). On Windows `main()` gets its
  `argv` in ANSI code page -> tool rebuilds it from
  `CommandLineToArgvW(GetCommandLineW())` as UTF-8 (see `utf8_argv()` in
  `pelorus_qp_report.c`; links `shell32` on Windows). Command line with no
  UTF-8 form (unpaired surrogate) -> refused on stderr, non-zero exit.
  Diagnostics echo such paths as UTF-8 bytes through narrow stdio. Console
  not on code page 65001 shows them garbled; accepted, affects messages only.
- New tool: one `executable(... install: false)` entry in `meson.build` plus
  one-line row in scope table above.

## Invariants

- Tools never become stability surface: no other code depends on their output
  format. They demonstrate; they do not define ABI.

## tester/

- `python3 -I tools/tester/pelorus_tester_report.py`; stdlib only, no cross-repo
  import. Functions <= 60 LOC, no recursion (iterative walks, bounded), every
  subprocess bounded in time and bytes.
- Report schema = `report.schema.json`; program checks the keyword subset it
  names. Changing a field -> schema, program, `--self-test` planted case, and
  `docs/development/tester.md` in one change.
- New validator rule -> planted bad case + RULES entry so
  `--self-test --disable <rule>` goes red.
- Stage runners = `pelorus_tester_stages.py`; pass rules = small pure functions,
  each with planted case. Fixtures = `pelorus_tester_fixtures.py` +
  `fixtures.lock.json`; every entry pinned by SHA-256 + licence record.
  Stage or fixture change -> `docs/usage/tester.md` same change.
  Device, encoder or layer missing -> `not_run`/`no_device` + reason;
  `PELORUS_VALIDATE=1` + layer missing -> `fail`. No silent fallback.
- Siblings load by path (`importlib`): `python3 -I` drops script dir from `sys.path`.
