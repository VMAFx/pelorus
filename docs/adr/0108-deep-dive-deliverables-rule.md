<!-- markdownlint-disable MD013 -->
# ADR-0108: Fork-local PRs ship the deep-dive deliverables

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: docs, agents, process

## Context

Adopted from the vmafx sibling. Pelorus carries an FFmpeg patch stack and a
cross-repo ABI; both are rebase- and drift-sensitive. A consistent set of
per-PR artifacts keeps the "why", the rebase story, and the changelog in sync
with the code.

## Decision

We will require every **fork-local** PR (anything not a verbatim upstream
port) to ship, in the same PR:

1. **Research digest** — `docs/research/NNNN-topic.md` (or "no digest needed: trivial").
2. **Decision matrix** — the accompanying ADR's `## Alternatives considered`
   table (or "no alternatives: only-one-way fix").
3. **`AGENTS.md` invariant note** — a one-line rebase-sensitive invariant in the
   relevant package's `AGENTS.md` (or "no rebase-sensitive invariants").
4. **Reproducer / smoke-test** — one runnable command in the PR description
   (e.g. `meson test --suite=fast`, an `ffmpeg -vf` line,
   `ffmpeg-patches/test/build-and-run.sh`).
5. **Changelog fragment** — `changelog.d/<section>/<topic>.md` (rendered into
   `CHANGELOG.md`; never edit `CHANGELOG.md` directly).
6. **Rebase note** — a `docs/rebase-notes.md` entry when the change affects the
   FFmpeg patch stack (or "no rebase impact: REASON").

Skipped deliverables are surfaced in the PR description with a one-line reason.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Six deliverables, opt-out-with-reason | Keeps why/rebase/changelog in sync; matches vmafx | Process overhead on small PRs | **Chosen** (opt-outs keep it light) |
| Only require an ADR | Lighter | Changelog + rebase notes drift; research lost | Rejected |
| Nothing beyond code review | Lightest | State and rationale rot across sessions | Rejected |

## Consequences

- **Positive**: rationale, changelog, and rebase impact never lag the code.
- **Negative**: process overhead, mitigated by per-item opt-outs.
- **Neutral / follow-ups**: the PR template carries the checklist; CI checks the
  changelog fragment exists.

## References

- vmafx ADR-0108 (deep-dive deliverables) + ADR-0221 (changelog fragments).
- Source: `req` — project rules adapted from golusoris/vmafx.
