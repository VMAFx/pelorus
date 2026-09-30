<!-- markdownlint-disable MD013 -->
# Agent guide — libpelorus/

The shared core: the Pelorus⇄vmafx side-data interop ABI plus the filter
parameter contracts. Parent: [../AGENTS.md](../AGENTS.md). Governing ADRs:
[0103](../docs/adr/0103-interop-sidedata-abi.md) (ABI),
[0105](../docs/adr/0105-libpelorus-license.md) (license).

## Scope

```text
libpelorus/
├── include/pelorus/   public ABI surface (stability-tagged)
│   ├── pelorus.h      version, pel_result
│   ├── interop.h      PelorusSideData blob + pack/parse (the cross-repo contract)
│   └── deband.h       smart-deband parameter contract
├── src/               interop.c, qp_report_csv.c, deband_params.c, version.c
├── shaders/           standalone reference .comp shaders (CI-compiled)
└── test/              interop_test.c — the shared ABI conformance fixture
                       path_utf8_test.c — the UTF-8 path contract (ADR-0149)
```

## Conventions

- Public names: `pel_` / `PELORUS_` / `pelorus_`; structs `PelorusXxx`. Every
  public function returns `pel_result` and null/range-checks its inputs.
- C11, K&R, 4-space, 100 cols. No banned functions (principles.md §1.2). No
  static-init side effects; no globals beyond `const` tables.
- `interop.c` + its headers are **vendored verbatim by vmafx** — keep them
  dependency-free (only `<stdint.h>`/`<stddef.h>`/`<stdlib.h>`/`<string.h>`).

## Rebase-sensitive invariants

1. **`PelorusSideData` and every section struct are a frozen wire ABI**
   (interop.h R1/R2): append-only, never reorder/resize/remove. The
   `_Static_assert` sizes in interop.h are load-bearing — if one trips, the ABI
   changed; bump `PELORUS_ABI_MINOR` and extend the conformance fixture, never
   "fix" the assert. (ADR-0103)
2. **`pelorus_sidedata_uuid` and `PELORUS_MAGIC_STR` are constants forever** —
   they are the on-frame routing key both repos match on. Changing either breaks
   every existing blob.
3. **Section offsets are 8-byte aligned in `pel_blob_pack`** so consumers can
   cast the returned pointer; the `PelorusFilmGrainSection` u64 `seed` depends on
   this. Don't drop the `PEL_ALIGN8`.
4. **The conformance fixture (`test/interop_test.c`) is shared with vmafx** — an
   N+1-minor blob must stay parseable by an N-minor consumer (R4). Add cases
   when you add fields; never weaken existing ones.
5. **`PEL_SEC_QPREPORT` (ABI 1.1)**: the `pel_qp_report_from_blocks()` signature,
   the `PelorusQpReportInput` layout, and the `qp_cell_out` ownership convention
   are stable once vmafx vendors `interop.c` — changing any of them requires a
   coordinated two-repo PR. The ABI 1.1 conformance case in `test/interop_test.c`
   is load-bearing; do not weaken it. (ADR-0119)
6. **Fixture files are exclusive and owner-only (ADR-0148).** Every file the
   conformance fixture writes goes through `write_private_fixture()`: POSIX
   `O_CREAT|O_EXCL|O_NOFOLLOW` with mode `0600`; Windows `CREATE_NEW` +
   `FILE_FLAG_OPEN_REPARSE_POINT` with the protected owner-only DACL
   `D:P(A;;FA;;;OW)`. Never create a fixture with `fopen(..., "w")`,
   `_sopen_s`, or a create-then-`chmod`. Never delete a path the fixture did
   not create. Keep the `umask(0)` and link regressions; the fixture is
   vendored byte for byte by VMAFx, so keep its body free of feature macros
   (the build supplies `_POSIX_C_SOURCE`).
7. **Library path arguments are UTF-8 on every platform; no narrow `fopen` of a
   caller path on Windows.** Every file open of a caller-supplied path goes
   through `open_utf8()` in `qp_report_csv.c`: POSIX is a literal `fopen`
   (byte-for-byte unchanged), Windows is a strict `MultiByteToWideChar(CP_UTF8,
   MB_ERR_INVALID_CHARS)` + `_wfopen`. Ill-formed UTF-8 is `PEL_ERR_INVALID`,
   any open failure stays `PEL_ERR_ABSENT`. A new path-taking API reuses the
   helper and extends `test/path_utf8_test.c`. vmafx mirrors
   `qp_report_csv.c` verbatim, so the helper stays static and depends only on
   kernel32. (ADR-0149)

## Don't

- Don't add a third-party dependency to `interop.c` — it must vendor cleanly
  into vmafx with no extra link deps.
- Don't reorder struct fields to "save padding" — the layout is the ABI.
- Don't `printf` from library code; return a `pel_result` and let the host log.
- Don't call narrow `fopen`/`open` on a caller-supplied path, or change the
  process locale or code page to make one work: route it through the ADR-0149
  UTF-8 opener.
