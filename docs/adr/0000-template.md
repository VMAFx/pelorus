<!-- markdownlint-disable MD013 MD041 -->
> **Allocator**: run `scripts/adr/next-free.sh --claim <your-topic-slug>` to
> reserve a number before creating this file. It writes a
> `docs/adr/NNNN-<slug>.md.stub`; rename to `.md` when you commit. Release an
> abandoned slot with `scripts/adr/next-free.sh --release <NNNN>`.

# ADR-0000: <short, declarative title>

- **Status**: Proposed | Accepted | Deprecated | Superseded by [ADR-NNNN](NNNN-title.md)
- **Date**: YYYY-MM-DD
- **Deciders**: <names / handles>
- **Tags**: <comma-separated — e.g. `interop`, `vulkan`, `ffmpeg`, `build`, `license`, `docs`>

## Context

What problem are we solving? What forces are at play (technical, organisational,
regulatory)? Cite constraints from [principles.md](../principles.md) where
relevant. One or two paragraphs.

## Decision

State the decision in active voice: "We will use X." One paragraph max.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Option A | … | … | … |
| Option B | … | … | … |

At minimum list the runner-up. If this section is empty, the decision wasn't
real — it was a default.

## Consequences

- **Positive**: what becomes easier, faster, safer.
- **Negative**: what becomes harder, slower, more expensive.
- **Neutral / follow-ups**: new tests, docs, migration steps, deprecations.

## References

- Upstream docs, RFCs, prior ADRs, related PRs `#NNN`.
- Source: `req` (direct user quote) or `Q<round>.<q>` (verbatim popup answer).
