<!-- markdownlint-disable MD013 -->
# Agent guide — libpelorus/

Shared core: Pelorus⇄vmafx side-data interop ABI plus filter parameter
contracts. Parent: [../AGENTS.md](../AGENTS.md). Governing ADRs:
[0103](../docs/adr/0103-interop-sidedata-abi.md) (ABI),
[0105](../docs/adr/0105-libpelorus-license.md) (license),
[0174](../docs/adr/0174-encoder-telemetry-abi-1-4.md) (ABI 1.4 telemetry),
[0175](../docs/adr/0175-encode-provenance-record.md) (encode record).

## Scope

```text
libpelorus/
├── include/pelorus/   public ABI surface (stability-tagged)
│   ├── pelorus.h      version, pel_result
│   ├── interop.h      PelorusSideData blob + pack/parse (the cross-repo contract)
│   ├── telemetry.h    encoder-telemetry input, no FFmpeg types (ABI 1.4)
│   ├── encode_record.h canonical encode record + digest (ADR-0175)
│   └── deband.h       smart-deband parameter contract
├── schema/            telemetry-fields.json — normative telemetry field names
├── src/               interop.c, qp_report_csv.c, deband_params.c, version.c,
│                      telemetry.c, encode_record.c, sha256.c (+ private sha256.h)
├── shaders/           standalone reference .comp shaders (CI-compiled)
└── test/              interop_test.c — the shared ABI conformance fixture
                       path_utf8_test.c — the UTF-8 path contract (ADR-0149)
                       telemetry_test.c, encode_record_test.c — not mirrored
```

## Conventions

- Public names: `pel_` / `PELORUS_` / `pelorus_`; structs `PelorusXxx`. Every
  public function returns `pel_result` and null/range-checks its inputs.
- C11, K&R, 4-space, 100 cols. No banned functions (principles.md §1.2). No
  static-init side effects; no globals beyond `const` tables.
- `interop.c` + its headers: **vendored verbatim by vmafx**. Keep them
  dependency-free (only `<stdint.h>`/`<stddef.h>`/`<stdlib.h>`/`<string.h>`).

## Rebase-sensitive invariants

1. **`PelorusSideData` and every section struct = frozen wire ABI**
   (interop.h R1/R2): append-only, never reorder/resize/remove.
   `_Static_assert` sizes in interop.h are load-bearing. One trips -> ABI
   changed: bump `PELORUS_ABI_MINOR` and extend conformance fixture; never
   "fix" assert. (ADR-0103)
2. **`pelorus_sidedata_uuid` and `PELORUS_MAGIC_STR` = constants forever**:
   on-frame routing key both repos match on. Changing either breaks every
   existing blob.
3. **Section offsets 8-byte aligned in `pel_blob_pack`** -> consumers can
   cast returned pointer; `PelorusFilmGrainSection` u64 `seed` depends on
   this. Keep `PEL_ALIGN8`.
4. **Conformance fixture (`test/interop_test.c`) shared with vmafx**: N+1-minor
   blob must stay parseable by N-minor consumer (R4). New field -> add cases;
   never weaken existing ones.
5. **`PEL_SEC_QPREPORT` (ABI 1.1)**: `pel_qp_report_from_blocks()` signature,
   `PelorusQpReportInput` layout and `qp_cell_out` ownership convention stay
   stable once vmafx vendors `interop.c`. Changing any of them -> coordinated
   two-repo PR. ABI 1.1 conformance case in `test/interop_test.c` is
   load-bearing; do not weaken it. (ADR-0119)
6. **Fixture files exclusive and owner-only (ADR-0148).** Every file conformance
   fixture writes goes through `write_private_fixture()`. POSIX:
   `O_CREAT|O_EXCL|O_NOFOLLOW`, mode `0600`. Windows: `CREATE_NEW` +
   `FILE_FLAG_OPEN_REPARSE_POINT`, protected owner-only DACL
   `D:P(A;;FA;;;OW)`. Never create fixture with `fopen(..., "w")`,
   `_sopen_s`, or create-then-`chmod`. Never delete path fixture did not
   create. Keep `umask(0)` and link regressions. VMAFx vendors fixture byte
   for byte -> keep its body free of feature macros (build supplies
   `_POSIX_C_SOURCE`).
7. **Library path arguments = UTF-8 on every platform; no narrow `fopen` of
   caller path on Windows.** Every file open of caller-supplied path goes
   through `open_utf8()` in `qp_report_csv.c`. POSIX: literal `fopen`
   (byte-for-byte unchanged). Windows: strict `MultiByteToWideChar(CP_UTF8,
   MB_ERR_INVALID_CHARS)` + `_wfsopen(..., _SH_DENYNO)`. Ill-formed UTF-8 = `PEL_ERR_INVALID`;
   any open failure stays `PEL_ERR_ABSENT`. New path-taking API reuses helper
   and extends `test/path_utf8_test.c`. vmafx mirrors `qp_report_csv.c`
   verbatim -> helper stays static, depends only on kernel32. (ADR-0149)

8. **ABI 1.4 (ADR-0174, ADR-0175).** `PEL_SEC_ENC_TELEMETRY` (bit 8, 104
   bytes), `PEL_SEC_ENCODE_RECORD` (bit 9, 48 bytes),
   `PelorusMotionSection.block_size_log2` (32 -> 36 bytes): size and
   member-offset asserts lock each. Reader detects 1.3 motion section by
   readable size, never by value. Maps, locators: 8-aligned blob-relative
   offsets; read only through `pel_blob_map()`.
9. **VMAFx mirror at RC4 = existing ten files only.** `telemetry.c`,
   `encode_record.c`, `sha256.c`, registry: not mirrored -> `test/interop_test.c`
   calls only `interop.c` API. New file: EUPL header, SPDX line, at most one
   `#include "pelorus/..."` (VMAFx `render_vendor`).
10. **Mirror contract.** Change to a file in `libpelorus/mirror-paths.txt`:
    must be on `master` and released before VMAFx pins it; follow
    `docs/api/mirror-contract.md`; PR carries the mirror checklist item.
    `scripts/check-mirror-contract.py` keeps page and list in step.
11. **Telemetry names = registry.** `PEL_TLM_F_*` bit, key in
    `schema/telemetry-fields.json`, API page: change together; bits
    append-only, never renumbered or reused. Fast suite runs
    `scripts/check-telemetry-field-parity.py` (SKIP until VMAFx/vmafx#2271
    publishes its list) plus its `--self-test`.
12. **Encode-record digest = Python reference.** Canonical text equals
    `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
    for every accepted record. Worked example in `docs/api/encode-record.md`
    = test input: change example and digest together. No recursion:
    canonicaliser keeps explicit 5-frame stack.

## Don't

- No third-party dependency in `interop.c`: must vendor cleanly into vmafx
  with no extra link deps.
- No struct field reorder to "save padding": layout = ABI.
- No `printf` from library code: return `pel_result`, host logs.
- No narrow `fopen`/`open` on caller-supplied path; no process locale or code
  page change to make one work: route it through ADR-0149 UTF-8 opener.
