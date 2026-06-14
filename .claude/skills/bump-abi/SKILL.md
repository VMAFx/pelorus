---
name: bump-abi
description: Use when extending the Pelorus interop side-data ABI — add a field or section the APPEND-ONLY way, bump PELORUS_ABI_MINOR, extend the conformance fixture, and update the consumers. Never reorder/resize/remove.
---

# /bump-abi

The `PelorusSideData` ABI (`libpelorus/include/pelorus/interop.h`) is a frozen
wire format shared with vmafx. Evolve it **append-only** (R1/R2): never reorder,
resize, repurpose, or remove a field/section bit.

## Add a field to an existing section

1. Add the field at the **end** of the struct, above its `APPEND-ONLY` marker
   (consume reserved `_pad` if present; keep the struct's alignment).
2. Update the `_Static_assert(sizeof(...) == N)` to the new size — a tripped
   assert means you changed the layout; recompute, don't delete it.
3. Bump `PELORUS_ABI_MINOR`.
4. Extend `libpelorus/test/interop_test.c`: set + read the new field, and assert
   an older consumer (smaller `consumer_known_size`) still parses (R4).
5. Update `docs/api/interop-abi.md`; add a `changelog.d/added/` fragment.

## Add a whole new section

1. Add a new bit to `enum pel_section` (next free bit; never reuse a retired one).
2. Define `PelorusXxxSection` (flat POD) + its `_Static_assert`.
3. Bump `PELORUS_ABI_MINOR`; document writer/reader in the §sections table.
4. Extend the conformance fixture; update the ABI doc + changelog.
5. Write an ADR if the new data changes the interop contract materially.

## Rules

- A breaking change is **forbidden** — to change a field's meaning, mint a NEW
  section bit and leave the old one reserved. `PELORUS_ABI_MAJOR` should never bump.
- Pelorus is the sole writer; vmafx only reads (single-writer invariant).
- Coordinate with vmafx: it vendors `interop.c` + runs the same fixture.
- See ADR-0103, ADR-0109, and `docs/api/interop-abi.md`.
