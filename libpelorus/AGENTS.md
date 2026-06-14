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
├── src/               interop.c, deband_params.c, version.c
├── shaders/           standalone reference .comp shaders (CI-compiled)
└── test/              interop_test.c — the shared ABI conformance fixture
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

## Don't

- Don't add a third-party dependency to `interop.c` — it must vendor cleanly
  into vmafx with no extra link deps.
- Don't reorder struct fields to "save padding" — the layout is the ABI.
- Don't `printf` from library code; return a `pel_result` and let the host log.
