<!-- markdownlint-disable MD013 -->
# ADR-0110: Freeze the deband AVOption names + ranges as the vmafx-facing control-plane contract

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: interop, autotune, vmafx, ffmpeg, docs

## Context

[ADR-0106](0106-autotune-control-plane.md) decided that Pelorus reuses vmafx's
control plane for VMAF-in-the-loop tuning: Pelorus is the tunable, vmafx's
`vmaf-tune` (Optuna TPE recommend / per-shot / ladder) is the optimizer. In the
in-graph coupling mode the optimizer drives Pelorus by setting the
`vf_pelorus_deband_vulkan` AVOptions and reading the resulting VMAF — so the
optimizer must know, ahead of time and across releases, which knobs exist, their
types, and their valid bounds.

Today those facts live only in the filter's `AVOption` table
(`ffmpeg-patches/files/vf_pelorus_deband_vulkan.c`) and the validation bounds in
`libpelorus/src/deband_params.c`. If vmafx's autotune side (integration-plan
workstream D1) hard-codes a search space against an *informal* surface, a later
rename or range change in Pelorus silently desyncs the two repos: the optimizer
proposes an option the filter rejects, or sweeps a range the filter clamps,
with no compile-time or test-time signal. The honest fix is to declare the
autotune-relevant subset a **stable, versioned contract** so a change to it is a
deliberate, reviewable, two-repo event rather than incidental filter drift.

## Decision

We will **freeze the autotune-relevant `vf_pelorus_deband_vulkan` AVOption
names and their valid ranges/defaults as a stable control-plane contract**,
documented in [docs/api/control-plane.md](../api/control-plane.md), with vmafx's
`vmaf-tune` as the named consumer. The frozen knobs are `range`, `thry`, `thrc`,
`grainy`, `grainc`, `softness`, `detail`, `dither`, `dynamic`, and `protect`.
Renaming, removing, narrowing the range of, or changing the type of any frozen
knob is a **breaking change** that requires a coordinated PR on both repos
(Pelorus + vmafx `vmaf-tune`) landing together. The contract is versioned
independently of the data-plane interop ABI ([ADR-0103](0103-interop-sidedata-abi.md)),
consistent with ADR-0106's "control plane is separately versioned" note.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Freeze the AVOption names + ranges as a documented contract (this ADR) | Single source of truth; a break is a visible, reviewable two-repo event; zero new code | Adds a doc to keep in lockstep with the AVOption table | **Chosen** |
| No formal contract — let vmafx track the filter source | Nothing to maintain | Silent drift: a rename/range change in Pelorus desyncs autotune with no signal; couples vmafx to Pelorus's source layout | Rejected — exactly the failure ADR-0106 needs to prevent |
| A separate machine-readable config schema file (e.g. JSON/YAML the filter and autotune both load) | Machine-checkable; one artifact both sides parse | New parser + loader on both sides; a second source of truth to keep in sync with the `AVOption` table FFmpeg already owns; over-engineered for ten scalar knobs | Rejected for v0.1 — revisit if the knob count or repo count grows |

## Consequences

- **Positive**: vmafx workstream D1 can hard-code its deband search space against
  a named, stable surface; a Pelorus change that would break autotune is caught
  at review as a contract break, not in a downstream run.
- **Negative**: the contract doc and the filter `AVOption` table must stay in
  lockstep; a knob change now touches code + the contract doc + (if breaking)
  the vmafx repo.
- **Neutral / follow-ups**: the contract covers only the *tuning* subset —
  `sample`, `blur`, `planes`, and `meta` stay free to evolve (documented as
  out-of-contract). A future machine-readable schema (alternative 3) can wrap
  the same names without re-deciding the freeze. When `vf_pelorus_denoise` /
  film-grain knobs land, extend the contract with their own freeze section.

## References

- [ADR-0106](0106-autotune-control-plane.md) — control plane reuses vmafx.
- [docs/api/control-plane.md](../api/control-plane.md) — the frozen contract.
- [docs/api/interop-abi.md](../api/interop-abi.md) §Control plane (autotune).
- Filter `AVOption` table: `ffmpeg-patches/files/vf_pelorus_deband_vulkan.c`;
  validation bounds: `libpelorus/src/deband_params.c`,
  `libpelorus/include/pelorus/deband.h`.
- Source: `req` — "freeze the deband filter's AVOption names + valid ranges as a
  STABLE control-plane contract that vmafx's vmaf-tune autotune will hard-code
  against (integration plan workstream D5)".
