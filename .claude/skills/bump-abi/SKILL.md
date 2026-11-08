---
name: bump-abi
description: Use when extending the Pelorus interop side-data ABI — add a field or section the APPEND-ONLY way, bump PELORUS_ABI_MINOR, extend the conformance fixture, and update the consumers. Never reorder/resize/remove.
---

# /bump-abi

The `PelorusSideData` ABI (`libpelorus/include/pelorus/interop.h`) is a frozen
wire format shared with vmafx. Evolve it **append-only** (R1/R2): never reorder,
resize, repurpose, or remove a field/section bit.

## Add a field to an existing section

1. Add the field at the **end** of the struct, above its `APPEND-ONLY` marker;
   keep the struct's alignment. Prefer growing the struct over consuming a
   reserved `_pad`: a reader then detects an older producer by readable size
   (`got`), never by a zero value (ADR-0174 decision 9).
2. Update the `_Static_assert(sizeof(...) == N)` to the new size — a tripped
   assert means you changed the layout; recompute, don't delete it.
3. Bump `PELORUS_ABI_MINOR` across `interop.h` and `AGENTS.md`:
   ```sh
   python3 -I scripts/bump-abi.py --write --note "<summary>" [--adr NNNN]
   ```
   Verify consistency with `python3 -I scripts/bump-abi.py --check`, then run `praetorctl compile-context`.
4. Extend `libpelorus/test/interop_test.c`: set + read the new field, and assert
   an older consumer (smaller `consumer_known_size`) still parses (R4).
5. Update ABI history fixtures: add `fill_<sec>` in `libpelorus/test/abi_history/abi_history_fill.c`, pack wiring in `abi_history_pack.c`, and `check_<sec>` in `abi_history_check.c` gated `#if PELORUS_ABI_MINOR >= N` and append values to `abi_history_values.h`; run `python3 -I scripts/check-interop-abi-history.py --write` (adds `abi-1.<N>.bin`); point `interop-history-writer` in `meson.build` at the new files and add read tests. Previous minor never in a release tag -> delete its `abi-1.<N-1>*.bin` files and read tests in the same PR; a released minor's files stay (ADR-0247).
6. Update `docs/api/interop-abi.md`; add a `changelog.d/added/` fragment.

## Add a whole new section

1. Add a new bit to `enum pel_section` (next free bit; never reuse a retired one).
2. Define `PelorusXxxSection` (flat POD) + its `_Static_assert`.
3. Bump `PELORUS_ABI_MINOR` across sites with `python3 -I scripts/bump-abi.py --write --note "<summary>" [--adr NNNN]`, run `praetorctl compile-context`, and document writer/reader in the §sections table.
4. Extend the conformance fixture; update the ABI doc + changelog.
5. Update ABI history fixtures: add `fill_<sec>` in `libpelorus/test/abi_history/abi_history_fill.c`, pack wiring in `abi_history_pack.c`, and `check_<sec>` in `abi_history_check.c` gated `#if PELORUS_ABI_MINOR >= N` and append values to `abi_history_values.h`; run `python3 -I scripts/check-interop-abi-history.py --write` (adds `abi-1.<N>.bin`); point `interop-history-writer` in `meson.build` at the new files and add read tests. Previous minor never in a release tag -> delete its `abi-1.<N-1>*.bin` files and read tests in the same PR; a released minor's files stay (ADR-0247).
6. Write an ADR if the new data changes the interop contract materially.

## Rules

- A breaking change is **forbidden** — to change a field's meaning, mint a NEW
  section bit and leave the old one reserved. `PELORUS_ABI_MAJOR` should never bump.
- Pelorus is the sole writer; vmafx only reads (single-writer invariant).
- Coordinate with vmafx: it vendors `interop.c` + runs the same fixture.
- See ADR-0103, ADR-0109, and `docs/api/interop-abi.md`.
