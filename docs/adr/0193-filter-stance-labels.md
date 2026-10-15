<!-- markdownlint-disable MD013 MD060 -->
# ADR-0193: Label every filter with its evidence stance

- **Status**: Accepted
- **Date**: 2026-10-15
- **Deciders**: Lusoris
- **Tags**: filters, evidence, documentation, testing

## Context

The README module inventory used free-form status phrases such as "Working",
"Built (tuning pending)", and "Estimator built". Those phrases mixed
implementation state with evidence strength. A filter can be implemented and
useful without having a measured bitrate-quality result on the project's pinned
public corpus.

The Ongoing — Proven filters milestone has no closed item. Its corpus and method
issues (#131 and #135), filter benchmarks (#197 to #205, #283, and #300), and
epic #123 remain open. The inventory therefore has no evidence that meets one
shared promotion rule. All ten filters and the `pelorus_fgs` bitstream filter
start as experimental.

## Decision

1. Source registrations are the inventory. Every `.p.name` registered by
   `ffmpeg-patches/files/vf_pelorus_*.c` and every Pelorus BSF registration must
   have one README module row and one per-component page stance.
2. The README row carries a machine-readable `Stance` cell and an
   `Evidence / what promotes it` sentence. The component page repeats the stance
   and the same sentence near its title.
3. The evidence sentence says what the component is for today and names the
   missing benchmark that would promote it. An experimental row links its open
   benchmark issue when one exists.
4. `proven` means a measured result on the pinned public corpus, with a link to
   the evidence (a `docs/research/` page or a hardware report) in the same table
   row. `experimental` means everything else. A stance is one sentence that says
   what the filter is for today and what evidence would promote it.
5. `scripts/check-filter-stance.py` enforces the source inventory, the two exact
   labels, evidence links for proven rows, and agreement with the component
   pages. Its temporary-tree self-test plants one defect for every rule.

Stance is a quality-evidence label only. It does not decide which names the
1.0 contract freezes: issue #152 freezes every filter name, AVOption name and
`lavfi.pelorus.*` key regardless of stance, with exemptions only as explicit
per-option ADR entries. This decision defines the labels and their
documentation check.

## Alternatives considered

- **Keep the free-form status column.** Rejected: implementation readiness and
  measured performance remain indistinguishable, and no stable token exists for
  the 1.0 contract.
- **Treat every working implementation as proven.** Rejected: build and smoke
  tests establish function, not a bitrate-quality result on the pinned public
  corpus.
- **Maintain a hand-written filter list in the checker.** Rejected: a newly
  registered filter could avoid the documentation gate. The source list is the
  inventory instead.
- **Require every filter to be proven before 1.0.** Rejected here: the roadmap
  makes proof an ongoing milestone, while issue #152 owns the contract freeze.

## Consequences

- Every current filter and BSF is visibly experimental even when its
  implementation is complete.
- A promotion changes the README and page stance together and adds an existing
  repository-local evidence link.
- Adding a filter source without a matching row and page fails `docs-check`.
- Renaming or removing a source registration leaves an unknown README row and
  fails `docs-check`.
- The labels make no quality claim by themselves; benchmark evidence remains in
  the open Proven filters milestone.

## References

- Issue #154; issue #152; epic #123.
- Ongoing — Proven filters milestone: issues #131, #135, #197 to #205, #283,
  and #300.
- [ADR-0172](0172-roadmap-milestone-map.md), which makes proof ongoing and
  requires a proven or experimental label before 1.0.
