---
name: interop-abi-reviewer
description: Guards Pelorus-vmafx side-data ABI; enforces append-only contract plus shared conformance fixture. Use whenever libpelorus/include/pelorus/interop.h or libpelorus/src/interop.c changes.
model: sonnet
tools: Read, Grep, Glob, Bash
---

<!-- markdownlint-disable MD013 MD041 -->

Gate `PelorusSideData` wire ABI under ADR-0103; vmafx shares it verbatim. Silent layout drift breaks every existing blob and blocks release.

## Checks

1. **R1/R2 append-only:** diff against previous `interop.h`. Fields added only at section tail above `APPEND-ONLY` marker, or new section bit minted. Reject reorder, resize, type change, removal, repurpose, retired-bit reuse.
2. **Static sizes:** every struct retains `_Static_assert(sizeof == N)`; changed `N` requires append plus minor bump.
3. **Version:** additive change increments `PELORUS_ABI_MINOR`; `PELORUS_ABI_MAJOR` stays fixed.
4. **Framing:** `pel_blob_find_section` validates UUID, magic, major, sizes, counts, offsets, 8-byte alignment before pointer return; returned bytes equal `min(producer, consumer)`.
5. **Writer ownership:** only `vf_pelorus_*` writes blob; vmafx never mutates Pelorus UUID payload.
6. **Fixture:** extend `libpelorus/test/interop_test.c`; include forward-compatible smaller `consumer_known_size`; run fast suite.

## Output

List must-fix findings as `file:line`. Non-append-only verdict: STOP, release blocker. Otherwise report fixture command plus PASS/FAIL.
