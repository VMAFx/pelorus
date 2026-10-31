<!-- markdownlint-disable MD013 -->
# ADR-0238: Move the agent reference tables out of `AGENTS.md`

- **Status**: Accepted
- **Date**: 2026-10-31
- **Deciders**: Lusoris
- **Tags**: governance, standards, docs
- **Supersedes**: the "Enforcement here" placement clause of [ADR-0187](0187-praetor-engine-7cf9cbb-engine-launcher.md); the rest of ADR-0187 stays Accepted

## Context

ADR-0187 put the per-rule enforcement facts and the engine-binary notes into the Pelorus part of `AGENTS.md`, as the section "Enforcement here". Together with the layout, skills, hooks, command and pin tables, the file grew to 24113 bytes. `compile-context` copies it into seven always-loaded projections (`CLAUDE.md`, `AGENTS.readonly.md`, `.codex/rules.md`, `.cursor/rules/hiss-invariants.mdc`, `.gemini/GEMINI.md`, `.github/copilot-instructions.md`, `.windsurfrules`).

Measured on 2026-10-31 with the fleet survey (`context_survey.py check`), that size breaks two client limits and the fleet median:

- Antigravity reads at most 24000 bytes per rule file; `AGENTS.md` had 24113.
- Windsurf reads at most 12000 characters per rule file; `.windsurfrules` had 24179.
- Every client total was more than twice the fleet median (for example Codex 24113 bytes against a 16346-byte threshold).

A client that truncates the file silently drops the rules at its tail, which here were the delivery rules.

## Decision

`AGENTS.md` keeps what an agent needs on every turn: the engine harness block as rendered, the mission, the hard rules, the short command list, reviewer routing and the delivery rules. The reference tables (enforcement per HISS rule, layout and ownership, skills, hooks, the full command table, pinned upstreams) move unchanged to `docs/development/agent-reference.md`, and `AGENTS.md` links to it. The FFmpeg pin line stays in `AGENTS.md`, because `scripts/check-build-config.py` reads it there.

The target is below 95% of the smallest cap, so that the survey's near-cap warning stays quiet: at most 11400 characters for `.windsurfrules`.

## Alternatives considered

| Option | Result | Why not chosen |
| --- | --- | --- |
| Keep "Enforcement here" in `AGENTS.md` and shorten the other sections | The enforcement section alone is 3608 characters; with it `.windsurfrules` would hold about 14900, above the Windsurf cap | Does not reach the cap |
| Split the tables into nested scoped `AGENTS.md` files | Clients load nested files only for edits under that folder; the enforcement table covers the whole tree | Rules would be missing for most edits |
| Shorten the rules themselves | Changes the contract that reviewers enforce | A size fix should not change what the rules say |
| Import the reference with `@path` | Claude Code and Gemini CLI expand imports into the loaded text | The size stays the same; the fleet rules forbid bulk imports |
| Move the tables to a linked reference page (chosen) | 11261 bytes for `AGENTS.md`, 11327 characters for `.windsurfrules` | — |

## Consequences

- Agents read the reference page on demand, before touching an unfamiliar path. A table changed there no longer reaches the projections, so `compile-context` is not needed for it.
- New top-level packages add their layout row to the reference page, not to `AGENTS.md`.
- `.windsurfrules` has 73 characters of headroom below the near-cap line (11400); growth beyond that needs another move, not a waiver.

## References

- [Research digest 0451](../research/0451-agent-context-size.md): the measured sizes and limits.
- [ADR-0187](0187-praetor-engine-7cf9cbb-engine-launcher.md): the placement this record supersedes.
- Fleet context-hygiene rule, ledger id `fleet-context-hygiene-all-clients-20261029` (paraphrased): instruction files import no bulk and stay under each client's cap.
- Windsurf rule-file limit: <https://docs.windsurf.com/llms-full.txt> (read 2026-10-29 by the survey's authors).
