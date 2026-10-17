<!-- markdownlint-disable MD013 -->
# ADR-0197: Place VMAFx interop mirror on ADR-2478 host-C exception list

- **Status**: Accepted
- **Date**: 2026-10-16
- **Deciders**: Lusoris
- **Tags**: interop, vmafx, rust, c, abi

## Context

Pelorus is the GPU pre-encode pipeline; sibling VMAFx (Video Multi-Method Assessment Fusion extended) is the perceptual quality oracle and autotune control plane ([ADR-0106](0106-autotune-control-plane.md)). The two projects interface through the shared `PelorusSideData` interop ABI ([ADR-0103](0103-interop-sidedata-abi.md)).

Under [VMAFx ADR-1113](https://github.com/VMAFx/vmafx/blob/master/docs/adr/1113-vendor-pelorus-interop-abi.md), VMAFx vendors a byte-identical read-only mirror of ten Pelorus interop files (four headers, five C source units, and one conformance test fixture) pinned to released Pelorus commits ([docs/api/mirror-contract.md](../api/mirror-contract.md)). VMAFx parses and packs side-data blobs with these files without linking against `libpelorus`.

On 2026-10-08, VMAFx accepted [ADR-2478](https://github.com/VMAFx/vmafx/blob/master/docs/adr/2478-rust-core-migration.md), establishing a phased core migration to Rust. Under ADR-2478, all host C and C++ in VMAFx is to be retired by VMAFx 3.0 (phase P6), unless explicitly registered on a governance exception list defining the file, rule, reason, owner, re-evaluation trigger, and expiry.

As noted in [docs/research/0172-vmafx-scope-gap.md](../research/0172-vmafx-scope-gap.md) (gap G4) and [docs/research/0172-post-1-0-rust-coupling.md](../research/0172-post-1-0-rust-coupling.md), ADR-1113 (byte-identical C vendoring) and ADR-2478 (elimination of host C) create an architectural tension for the ten mirrored files. Issue [#164](https://github.com/VMAFx/pelorus/issues/164) in Pelorus milestone 0.5 asks for the decision; it blocks [#159](https://github.com/VMAFx/pelorus/issues/159) (Rust and Python bindings for the interop ABI).

## Decision

We choose **Option C: the ten VMAFx mirror files are placed on the VMAFx ADR-2478 host-C exception list** through VMAFx 3.0:

1. **Mirrored files**:
   - `core/include/libvmaf/pelorus/deband.h`
   - `core/include/libvmaf/pelorus/denoise.h`
   - `core/include/libvmaf/pelorus/interop.h`
   - `core/include/libvmaf/pelorus/pelorus.h`
   - `core/src/interop/pelorus_deband_params.c`
   - `core/src/interop/pelorus_denoise_params.c`
   - `core/src/interop/pelorus_interop.c`
   - `core/src/interop/pelorus_qp_report_csv.c`
   - `core/src/interop/pelorus_version.c`
   - `core/test/test_pelorus_interop.c`
2. **Exception metadata**:
   - **Owner**: `pelorus`
   - **Reason**: byte-identical ABI mirror ([VMAFx ADR-1113](https://github.com/VMAFx/vmafx/blob/master/docs/adr/1113-vendor-pelorus-interop-abi.md))
   - **Re-evaluation trigger**: Pelorus 2.1 Rust interop crate release ([#115](https://github.com/VMAFx/pelorus/issues/115))
   - **Expiry**: VMAFx 3.0
   - **Exception category**: Category A (interval stated in entry)
3. **Rust interop timing and scope**: Developing a Rust interop crate or Rust bindings before 1.0 would violate the established roadmap milestone ordering ([ADR-0172](0172-roadmap-milestone-map.md)). A standalone Rust interop crate versus generated bindings over `libpelorus` C will be re-decided at Pelorus milestone 2.1 ([#115](https://github.com/VMAFx/pelorus/issues/115)), aligning with VMAFx Rust Phase 2 (vmafx 2.3).
4. **Layout gate unchanged**: the `_Static_assert` size and offset pins in [`interop.h`](../../libpelorus/include/pelorus/interop.h), mirrored byte-identical into VMAFx, plus the shared conformance fixture ([`libpelorus/test/interop_test.c`](../../libpelorus/test/interop_test.c) in Pelorus, `core/test/test_pelorus_interop.c` in VMAFx) stay the layout gate. A planted field added to `PelorusBandingSection` stops compilation of any unit that includes the header (`static assertion failed: "banding section ABI"`), so the exception does not weaken the gate.
5. **Repository boundary**: The VMAFx exception-list row is authored and maintained in the `VMAFx/vmafx` repository, not here.

## Alternatives considered

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| Option A: Dependency-free Rust crate now (milestone 0.5) | Immediately conforms to VMAFx Rust-first direction; no host-C exceptions in VMAFx core | Requires a second ABI implementation in lockstep with C before 1.0; pulls forward milestone 2.1 work into 0.5 against roadmap milestone order ([#115](https://github.com/VMAFx/pelorus/issues/115), [ADR-0172](0172-roadmap-milestone-map.md)) | Rejected |
| Option B: Generated Rust bindings over `libpelorus` C | Reuses existing C implementation without duplicating wire parsing/packing logic | Imposes a C build and link dependency on VMAFx; breaks the self-contained, dependency-free vendoring model of [VMAFx ADR-1113](https://github.com/VMAFx/vmafx/blob/master/docs/adr/1113-vendor-pelorus-interop-abi.md) | Rejected |
| Option C: Retain the ten mirrored C files on the VMAFx ADR-2478 host-C exception list until Pelorus 2.1 (this ADR) | Preserves single-source byte-identical vendoring (ADR-1113) without new build dependencies; keeps pre-1.0 milestone focus on zero-copy and integration while scheduling Rust interop for milestone 2.1 ([#115](https://github.com/VMAFx/pelorus/issues/115)) aligning with VMAFx Phase 2 (vmafx 2.3); conformance fixture continues to gate layout | Carries ten C files on VMAFx's host-C exception list through VMAFx 2.x until VMAFx 3.0 | **Chosen** |

## Consequences

- **Positive**:
  - Pelorus avoids maintaining two parallel parsers during pre-1.0 ABI evolution while preserving byte-identical ABI vendoring.
  - VMAFx maintains a dependency-free build without linking against external C shared or static libraries.
  - The shared conformance fixture continues to prevent layout drift on every commit.
- **Negative**:
  - VMAFx carries ten C files under its ADR-2478 host-C exception list through VMAFx 2.x.
- **Follow-ups**:
  - At Pelorus milestone 2.1 ([#115](https://github.com/VMAFx/pelorus/issues/115)), re-evaluate shipping a dependency-free Rust interop crate versus generated bindings to retire the host-C exception before VMAFx 3.0.
  - VMAFx records the exception list row in its repository tracking ADR-2478.

## Update 2026-10-17

Pelorus and VMAFx renumbered their milestones onto the 0.x version line; the
decision is unchanged. The numbers above map as follows:

- Pelorus milestone 2.1 (Rust interop crate, [#115](https://github.com/VMAFx/pelorus/issues/115)) is now 0.13.0; "before 1.0" means before 0.9.0 (stable contract).
- VMAFx 2.3 (Rust P2) is now VMAFx milestone 0.6.0.
- VMAFx 3.0 (phase P6, host C removed) is now VMAFx milestone 1.2.0. The exception entries on VMAFx's list still name 3.0.

[docs/roadmap.md](../roadmap.md) carries the current numbers.

## References

- Issue: [#164](https://github.com/VMAFx/pelorus/issues/164) — VMAFx interop mirror host-C exception under ADR-2478 Rust migration.
- Issue: [#159](https://github.com/VMAFx/pelorus/issues/159) — Rust and Python bindings for the interop ABI; waits on this decision.
- Issue: [#115](https://github.com/VMAFx/pelorus/issues/115) — Milestone 2.1: Rust interop crate.
- [docs/research/0172-vmafx-scope-gap.md](../research/0172-vmafx-scope-gap.md) — gap G4 (Interop mirror under Rust migration).
- [docs/research/0172-post-1-0-rust-coupling.md](../research/0172-post-1-0-rust-coupling.md) — post-1.0 milestones and Rust coupling.
- VMAFx ADR-2478: [Rust Core Migration](https://github.com/VMAFx/vmafx/blob/master/docs/adr/2478-rust-core-migration.md).
- VMAFx ADR-1113: [Vendor Pelorus Interop ABI](https://github.com/VMAFx/vmafx/blob/master/docs/adr/1113-vendor-pelorus-interop-abi.md).
- [ADR-0103](0103-interop-sidedata-abi.md) — PelorusSideData single-source interop ABI.
- [ADR-0172](0172-roadmap-milestone-map.md) — Roadmap milestone map.
- [docs/api/mirror-contract.md](../api/mirror-contract.md) — VMAFx mirror contract.
