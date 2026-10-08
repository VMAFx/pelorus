<!-- markdownlint-disable MD013 -->
# ADR-0172: Roadmap to 1.0 and beyond: milestone map 0.3 to 3.0 and four Ongoing buckets

- **Status**: Accepted
- **Date**: 2026-10-08
- **Deciders**: Lusoris
- **Tags**: roadmap, release, process, docs, vmafx
- **Related**: [ADR-0103](0103-interop-sidedata-abi.md), [ADR-0106](0106-autotune-control-plane.md), [ADR-0171](0171-eupl-relicense.md)

## Context

Pelorus is at v0.2.2 with two forge milestones (`0.1`, `0.2`) that no longer
describe the work: v0.1.0 to v0.2.2 have shipped, v0.3.0 has no milestone, and
the open work (bug wave, hardened release, telemetry and provenance schemas,
zero-copy, backends, codecs, 1.0) has no sequence. The sibling project vmafx
sequences its own plan in a roadmap page and an ADR
(vmafx [ADR-2001](https://github.com/VMAFx/vmafx/blob/master/docs/adr/2001-release-scope-1-0-and-roadmap-to-2-0.md),
[ADR-2342](https://github.com/VMAFx/vmafx/blob/master/docs/adr/2342-rc-map-amendment-2026-10.md))
and Pelorus has hard dependencies on that sequence: vmafx needs the telemetry
and provenance schemas before its `1.0.0-rc.4` (VMAFx/vmafx#2411), live
integration for its 1.1, encoder feedback for its 1.2, and its Rust migration
(2.3, 2.5, 3.0) decides what happens to the vendored interop files.

On 2026-10-08 the maintainer decided the shape of the plan. Paraphrased:

- **Milestones to 1.0.** A short run of 0.x milestones, each tied to a vmafx
  phase where one exists, ending in a 1.0 that freezes the public contract:
  public headers, AVOption and filter names, `lavfi.pelorus.*` keys, the
  append-only interop ABI and soversion 1. Proof of quality is not a 1.0 gate.
  It is an Ongoing bucket, so a missing benchmark never blocks a contract
  freeze.
- **What 1.0 contains.** Windows support, a prebuilt and attested
  Pelorus-enabled FFmpeg (GPL-3.0-or-later, never nonfree), AMD AMF steering, a
  docs portal and complete licensing, handled the way vmafx handles it.
  Post-1.0 milestones, including the Rust ones, are planned now.
- **Full scope.** The plan covers more codecs, more compute backends, a
  competitive audit (what others have or claim, where to win, where to close a
  gap, what nobody offers), a 100 percent zero-copy path from decode through
  Pelorus and the encoder to vmafx, and tester images with release candidates
  like vmafx's.
- **Placement.** Codecs and encoders land before 1.0. The backends milestone
  starts once vmafx's `libgpudispatch` (VMAFx/vmafx#1455) is done. macOS is a
  MoltenVK spike in 0.5 and Metal arrives with the backends; 1.0 lists macOS
  only if the spike passes. vmafx reads the remaining interop sections in 0.6.
- **Form.** One forge issue per work item, grouped by epics with native
  sub-issues, plus this ADR and `docs/roadmap.md`.
- **Hygiene folded into 0.3.** Hooks and the Makefile run the engine pinned at
  `PRAETOR_REF`; the master ruleset is the only branch protection.

Evidence for the work items is in the research digests listed under
References. The licence change itself is [ADR-0171](0171-eupl-relicense.md).

## Decision

We adopt the milestone map below. [docs/roadmap.md](../roadmap.md) holds the
outcome, exit criteria and epic links of each milestone and is the page readers
use. This ADR holds the reasons.

| Milestone | Theme | vmafx alignment |
| --- | --- | --- |
| 0.3 | Hardened release: bug wave, FFmpeg n9.0.2, Praetor `492a00f9`, SLSA L3, EUPL-1.2, v0.3.0 | none |
| 0.4 | Telemetry schemas (ABI 1.4) and tester kit; first release candidate | `1.0.0-rc.4` |
| 0.5 | Zero-copy and live integration | 1.1 |
| 0.6 | Conformance and encoder feedback | 1.2 |
| 0.7 | Multi-backend compute; starts when vmafx#1455 lands | vmafx `libgpudispatch` |
| 0.8 | Codec and encoder breadth | vmafx codec adapters |
| 1.0 | Stable contract, platforms and distribution, licensing | 1.0.0 |
| 1.1 | Content-adaptive tuning | none |
| 1.2 | Integrations and packaging | none |
| 2.0 | Breaking changes only | none |
| 2.1 | Rust interop crate | 2.3 (Rust P2) |
| 2.2 | Rust host runtime | 2.5 (Rust P4) |
| 3.0 | Host C removed | 3.0 (Rust P6) |

Four Ongoing buckets are rolling milestones that never close: Proven filters,
Code health and governance, Upstream and rebases, Research and competitive
watch.

Rules that keep the map true:

1. **Changing the map needs an ADR and a `docs/roadmap.md` edit in the same
   change.** Adding, removing, renaming, reordering or re-scoping a milestone,
   or moving a bucket, is a map change. Moving a single issue between
   milestones, adding or closing issues and re-sizing an epic are ordinary
   triage and need neither.
2. The forge milestones, epic titles and `docs/roadmap.md` use the same names.
   A mismatch is a defect in whichever of them was edited last.
3. A milestone's exit criteria are the contract for closing it. Evidence of
   quality (BD-rate, hardware matrices) is gathered in the Ongoing buckets and
   cited from exit criteria only where this ADR or `docs/roadmap.md` says so.
4. Once Accepted, this ADR is not edited. A map change is a new ADR that
   supersedes or amends it, as vmafx ADR-2342 amends ADR-2001.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Proven filters as milestone 0.7 before 1.0 | Every filter has numbers at 1.0 | Benchmark corpus, hardware and encoder baselines are open-ended; 1.0 and the vmafx phases would wait on them | Proof is an Ongoing bucket instead. 1.0 requires every filter to be labelled proven or experimental, not proven |
| Backends after 1.0 | Smaller 1.0; no dependency on vmafx#1455 for the freeze | 1.0 freezes filter and AVOption names; per-backend names would then need a breaking release | Backends come first as 0.7 so the frozen names include them; the milestone does not start before `libgpudispatch` is done |
| Codecs and encoders after 1.0 | Fewer legs to freeze | AMF steering is in 1.0 scope and vmafx tunes VVC, VP9 and more; grain legs depend on the codec work | Codec and encoder breadth is 0.8, before 1.0 |
| One interchange milestone (merge 0.5 and 0.6) | Fewer milestones; one release for all interchange work | Does not match vmafx's 1.1 (live integration) and 1.2 (encoder feedback) consumers; zero-copy and conformance have different exit tests | Split in two: 0.5 zero-copy and live integration, 0.6 conformance and encoder feedback |
| 1.0 as a product release with proof required | A 1.0 that makes quality claims | Ties the freeze to benchmarking that no current corpus supports; negative results would block the release | 1.0 is a contract freeze; claims are kept honest by the Ongoing evidence bucket and the claims ladder in the competitive audit |
| Roadmap in a Projects board or issue only | No document to maintain | Rationale drifts away from the plan; no review or history | Map lives in an ADR and a docs page, work lives in issues, as vmafx does |

## Consequences

- **Positive**: Each milestone has a theme, an exit test and an owner epic.
  vmafx can read its dependencies off one page. Competitive claims, patent
  review and the claims ladder have a defined place (Ongoing: Proven filters and
  the 1.0 licensing epic) instead of ad hoc notes.
- **Negative**: Thirteen release milestones and four buckets are a lot to keep
  current; the 0.7 start depends on another repository. A map change costs an
  ADR.
- **Neutral / follow-ups**: The forge milestones 1 and 2 were renamed to 0.3
  and 0.4 and 15 milestones were added; 21 epics and 21 labels exist
  (`.config/labels.yaml`). Issue bodies cite the research digests in
  `docs/research/0172-*.md`. No automatic check compares `docs/roadmap.md` with
  the forge; epics are linked by number, so a renumbering is a visible edit.

## References

Research digests (read 2026-10-08):
[competitive audit](../research/0172-competitive-audit.md),
[zero-copy hop audit](../research/0172-zero-copy-audit.md),
[vmafx scope gap](../research/0172-vmafx-scope-gap.md),
[tester programme](../research/0172-tester-programme.md),
[post-1.0 and Rust coupling](../research/0172-post-1-0-rust-coupling.md),
[item inventory](../research/0172-roadmap-inventory.md).

Maintainer decisions Q-010 to Q-023, recorded verbatim on 2026-10-08 in the
decision log (question, offered options, answer):

- Q-010 "Roadmap milestone shape to 1.0?" Options: as proposed (0.3-0.7 + 1.0), merge 0.5+0.6, evidence as Ongoing. Decision: 0.3 Hardened release, 0.4 Telemetry and provenance schemas (vmafx RC4), 0.5 Interchange and live integration (vmafx 1.1), 0.6 Conformance and encoder feedback (vmafx 1.2), 1.0 Stable contract; evidence is 'Ongoing — Proven filters', not a 1.0 gate (2026-10-08)
- Q-011 "What does Pelorus 1.0 mean?" Options: contract freeze, proof required, product 1.0. Decision: contract freeze: public headers, AVOption names, lavfi.pelorus.* keys, interop ABI append-only, soversion 1; every filter labelled proven or experimental (2026-10-08)
- Q-012 "1.0 scope?" Options: Windows, prebuilt FFmpeg, AMD AMF, docs portal. Decision: all four in 1.0: Windows supported, prebuilt attested Pelorus-enabled FFmpeg, AMD AMF steering, docs portal; licensing handled like vmafx (REUSE/notices/licence matrix); plan post-1.0 milestones too (Rust etc.) (2026-10-08)
- Q-013 "Forge plan granularity?" Options: issue per work item, epics with checklists. Decision: issue per work item: epics + child issues as native sub-issues + roadmap ADR and docs/roadmap.md (2026-10-08)
- Q-014 "Pelorus licence?" Options: keep BSD-2-Clause-Patent + LGPL (ADR-0105), EUPL-1.2 like vmafx. Decision: EUPL-1.2 default like vmafx, ASAP before v0.3.0; FFmpeg-tree files stay LGPL-2.1-or-later; vendored MIT keeps MIT; supersedes ADR-0105 (2026-10-08)
- Q-015 "Roadmap inputs beyond vmafx coupling?" Options: coupling only, full scope. Decision: full scope (user 2026-10-08): more codecs + more backends per vmafx's full plan; vmafx-style competitive audit (what competitors have/claim, beat them, close gaps, find whitespace nobody has); 100% zero-copy decode->Pelorus->encoder->vmafx; tester images + RC programme like vmafx and everything connected
- Q-016 "EUPL version clause?" Options: like vmafx, v1.2 only. Decision: like vmafx: no 'v1.2 only' statement (2026-10-08)
- Q-017 "FFmpeg co-copyright on vf_pelorus_* host files?" Options: add co-notice, Lusoris only. Decision: add 'the FFmpeg developers' co-notice on files following FFmpeg model filters (2026-10-08)
- Q-018 "Prebuilt FFmpeg licence flavour?" Options: GPL-3.0+, LGPL-2.1+, both. Decision: GPL-3.0-or-later like vmafx (--enable-gpl --enable-version3, x264/x265, -source image, never nonfree) (2026-10-08)
- Q-019 "Backends/codecs placement?" Options: before 1.0, after 1.0. Decision: backends milestone starts when vmafx libgpudispatch (#1455, vmafx RC5) is done; codecs/encoders before 1.0 (2026-10-08)
- Q-020 "macOS?" Options: MoltenVK spike, commit 1.0, out. Decision: MoltenVK spike in 0.5; Metal with backends milestone; 1.0 lists macOS only if spike passes (2026-10-08)
- Q-021 "Hook engine source?" Options: pin in repo, keep PATH. Decision: pin in repo: hooks + Makefile run engine at PRAETOR_REF (cached go install); report PATH coupling to Praetor (2026-10-08)
- Q-022 "Remove classic branch protection?" Options: remove, keep both. Decision: remove; ruleset #24738112 is the single source (2026-10-08)
- Q-023 "vmafx reads remaining PEL_SEC sections?" Options: go in 0.6, parked. Decision: go, planned in 0.6 as linked vmafx work (2026-10-08)

Related: vmafx ADR-2001 and ADR-2342; VMAFx/vmafx#2411 (RC4 interop mirror),
VMAFx/vmafx#1455 (`libgpudispatch`) and #2271 (stream-metadata schema).
