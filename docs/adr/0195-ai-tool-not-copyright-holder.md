<!-- markdownlint-disable MD013 MD060 -->
# ADR-0195: AI coding tools are not copyright holders; file headers must agree with REUSE annotations

- **Status**: Accepted
- **Date**: 2026-10-15
- **Deciders**: Lusoris
- **Tags**: licensing, reuse, governance, compliance

## Context

Pelorus is licensed under the EUPL-1.2, with sources joining the FFmpeg tree
under LGPL-2.1-or-later and vendored dependencies under MIT
([ADR-0171](0171-eupl-relicense.md)). The repository follows the REUSE
specification 3.3 ([`REUSE.toml`](../../REUSE.toml)), checked by `reuse lint`.
Hard rules require every new C, shell, and Python file to carry a copyright
notice and an SPDX licence identifier. Project and fleet rules prohibit AI
co-author trailers and "generated with" footers in commits, pull request texts,
and file headers.

With the adoption of AI coding assistants and autonomous agent workflows,
licence provenance questions arose (#172):

1. Can an AI coding tool, model, or agent be named as a copyright holder in
   source file headers, `REUSE.toml`, or documentation?
2. How should an engine gate treat accidental or legacy `Co-authored-by:`
   commit trailers naming AI tools if they appear in git history?
3. How must a file's embedded SPDX header relate to the repository's
   `REUSE.toml` annotations?
4. How should licence provenance and header integrity be enforced across the
   fleet?

Praetor issue cordanaLLM/praetor#907 specifies a dedicated licence provenance
gate within the Praetor engine to automate this verification.

## Decision

1. **AI tools are never copyright holders**:
   - An AI coding tool, assistant, or model (such as Claude, GitHub Copilot,
     OpenAI Codex, Antigravity, or similar systems) is never a copyright
     holder.
   - An AI tool or model must never appear in `SPDX-FileCopyrightText`,
     `Copyright` lines, `REUSE.toml`, or `docs/licensing.md`.
   - Copyright in Pelorus code is held by its human contributors or Lusoris.
     This is project policy; it follows the human-authorship premise of
     copyright law, and it does not replace legal advice (counsel review of
     the licence model is tracked separately in #174).

2. **No AI attribution in trailers, PR bodies, or file headers**:
   - In accordance with project rules, commits, pull request descriptions,
     and source headers must not carry AI co-author trailers or attribution
     footers.
   - If a `Co-authored-by:` trailer naming an AI system appears in history, it
     is an attribution remark, not a copyright claim. The licence provenance
     gate must not treat commit trailers as copyright statements.

3. **Header agreement rule**:
   - Under a `precedence = "closest"` annotation, a file's own header must
     agree with the matching `REUSE.toml` annotation in licence identifier and
     rights holders.
   - Under a `precedence = "override"` annotation, `REUSE.toml` is
     authoritative. A header that names a licence must name the annotation's
     licence, and every holder the header names must appear in the
     annotation. The annotation may add co-holders the header does not name.
   - This is the shape of the nine Vulkan filter host files
     (`ffmpeg-patches/files/vf_pelorus_*_vulkan.c`). Their headers name only
     Lusoris with FFmpeg's LGPL-2.1-or-later boilerplate. Their `override`
     annotation adds "2000-2026 the FFmpeg developers", which keeps the files
     and the patch stack byte-stable ([ADR-0171](0171-eupl-relicense.md),
     `docs/licensing.md`).

4. **Enforcement and acceptance cases**:
   - The rule will be enforced by Praetor's licence provenance gate
     (cordanaLLM/praetor#907) once the pinned engine includes the check.
   - Until that engine pin lands, compliance is maintained by `reuse lint`,
     manual review, and `REUSE.toml` validation.
   - Vendored code exceptions (superpowers MIT, interfig MIT) pass only
     through explicit, declared `REUSE.toml` annotations.
   - Acceptance cases defining gate behaviour are committed as data fixtures in
     `scripts/fixtures/licence-provenance/cases.json`.

## Alternatives considered

- **Local copy of the gate in this repository.** Rejected: the fleet follows
  the "one behaviour, one implementation" invariant (HISS-19). Governance
  gates belong in the Praetor engine so that all downstream repositories share
  identical verification logic without duplication or divergence.
- **Naming the AI tool as a co-holder.** Rejected: a tool is not an author,
  and a holder line has to name a person or entity that can hold rights.
  VMAFx ADR-0861 made the same call for the VMAFx repository.
- **Add the FFmpeg developers line to the nine Vulkan host file headers.**
  Rejected: it changes shipped files and the generated patch stack for no
  licence gain. The `override` annotation already records the co-holder.

## Consequences

- No source file, documentation file, or `REUSE.toml` entry may attribute
  copyright to an AI model or tool.
- Embedded file headers and `REUSE.toml` annotations must remain in strict
  agreement.
- Acceptance fixtures under `scripts/fixtures/licence-provenance/` serve as the
  authoritative test vector suite for upstream Praetor gate development.
- `docs/licensing.md` documents this policy.

## References

- Issue #172 (Licence provenance and AI tool copyright policy).
- Upstream Praetor issue cordanaLLM/praetor#907 (Licence provenance gate).
- [ADR-0171](0171-eupl-relicense.md) (Pelorus is EUPL-1.2; FFmpeg tree files LGPL-2.1-or-later).
- [ADR-0105](0105-libpelorus-license.md) (Initial licence architecture).
- VMAFx ADR-0861 (AI coding assistants are not copyright holders).
- REUSE Specification 3.3 (<https://reuse.software/spec-3.3/>).
