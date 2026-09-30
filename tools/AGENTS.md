<!-- markdownlint-disable MD013 -->
# Agent guide — tools/

libpelorus command-line demonstrators. Parent: [../AGENTS.md](../AGENTS.md).
These exercise the public ABI end-to-end and back the per-PR "reproducer
command" deliverable (ADR-0108). Gated by the `tools` meson option (default on);
**not installed** — they are dev/demonstration binaries, not a shipped surface.

## Scope

```text
tools/
└── pelorus_qp_report.c   x265 --csv -> PEL_SEC_QPREPORT demonstrator (ADR-0122)
```

## Conventions

- Link only `libpelorus_dep`; no encoder SDK, no Vulkan. A tool is allowed to
  `printf` to stdout/stderr (it is an executable, not embeddable library code —
  the AGENTS.md §3 no-stdio rule applies to `libpelorus/src`, not here).
- Every libpelorus call is `pel_result`-checked; exit non-zero on error with
  `pel_result_str`.
- A path handed to libpelorus is UTF-8 (ADR-0149). On Windows `main()` gets its
  `argv` in the ANSI code page, so a tool rebuilds it from
  `CommandLineToArgvW(GetCommandLineW())` as UTF-8 (see `utf8_argv()` in
  `pelorus_qp_report.c`; links `shell32` on Windows). A command line that has
  no UTF-8 form (an unpaired surrogate) is refused on stderr with a non-zero
  exit. Diagnostics echo such paths as UTF-8 bytes through narrow stdio; a
  console not on code page 65001 shows them garbled, which is accepted because
  it affects messages only.
- A new tool adds one `executable(... install: false)` entry to `meson.build`
  and a one-line row in the scope table above.

## Invariants

- Tools never become a stability surface: no other code depends on their output
  format. They demonstrate; they do not define ABI.
