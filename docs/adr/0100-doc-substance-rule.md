<!-- markdownlint-disable MD013 -->
# ADR-0100: Every user-discoverable surface ships human-readable docs in the same PR

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: docs, agents

## Context

Adopted from the vmafx sibling's identically-numbered rule. Code comments and
ADRs explain decisions to maintainers; they do not teach a human how to *use* a
surface. Without an in-PR docs requirement, usage docs lag the code and rot.

## Decision

We will require that **every PR adding or changing a user-discoverable surface
ships human-readable documentation under `docs/` in the same PR — no docs =
unmergeable.** User-discoverable means: an FFmpeg filter or AVOption, a public
`libpelorus` C API (`include/pelorus/`), an interop side-data section, a
`meson_options.txt` build flag, or a user-visible log/error/output change.
Internal refactors, no-user-delta bug fixes, and test-only changes are excluded.

Per-surface minimum bars:

| Surface | Bar |
|---|---|
| FFmpeg filter / AVOption | what it does · every option + default · a runnable `ffmpeg -vf` example · how output surfaces · interactions / limitations |
| Public C API | what it does · inputs/outputs incl. ownership + lifetime · thread-safety · ABI tag (stable/experimental) · runnable C snippet · error semantics (`pel_result`) |
| Interop section | what it carries · who writes / who reads · field semantics · versioning (which `PELORUS_ABI_MINOR`) |
| Build flag | what it enables · default · deps pulled in · runtime effect |

Docs land in the existing topic tree: filters → `docs/metrics/` + `docs/usage/`,
C API → `docs/api/`, Vulkan path → `docs/backends/`, build/release →
`docs/development/`, architecture → `docs/architecture/`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Docs-in-same-PR, per-surface bar | Docs never lag code; consistent with vmafx | Slightly heavier PRs | **Chosen** |
| Docs "eventually" / separate PR | Lighter PRs | Docs rot; the surface ships undocumented | Rejected |
| Rely on code comments / ADRs | No extra files | They explain *decisions*, not *usage* | Rejected |

## Consequences

- **Positive**: the repo stays self-teaching; reviewers have a concrete checklist.
- **Negative**: marginally larger PRs.
- **Neutral / follow-ups**: the PR template carries the checkbox.

## References

- vmafx ADR-0100 (project-wide doc-substance rule).
- Source: `req` — project rules adapted from golusoris/vmafx ("vmfax has the strictest").
