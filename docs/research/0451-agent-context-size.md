<!-- markdownlint-disable MD013 -->
# Size of the always-loaded agent context

**Date:** 2026-10-31

**Decision:** [ADR-0238](../adr/0238-agent-context-reference-split.md).

**Scope:** `AGENTS.md` and its seven client projections on master `0b30eda` and after the move of the reference tables.

## Verdict

On master every projection was above at least one client limit. After the move, all of them are below 95% of the smallest limit and below twice the fleet median.

## Limits

| Client | Limit | Source |
| --- | --- | --- |
| Antigravity | 24000 bytes per rule file | fleet survey `context_survey.py`, `ANTIGRAVITY_FILE_CAP_BYTES` |
| Windsurf | 12000 characters per rule file | <https://docs.windsurf.com/llms-full.txt>, as cited in the survey |
| All clients | twice the fleet median of the client total | `context_survey.py check` |

The survey flags a file as near the cap at 95% of the limit, so the working target for `.windsurfrules` is at most 11400 characters.

## Measurements

Bytes with `wc -c`; characters with `LC_ALL=C.UTF-8 wc -m`.

| File | master `0b30eda` | after |
| --- | --- | --- |
| `AGENTS.md` | 24113 bytes | 11261 bytes |
| `.windsurfrules` | 24179 characters | 11327 characters |
| `.cursor/rules/hiss-invariants.mdc` | — | 11407 bytes |

Sections of `docs/development/agent-reference.md` (characters): layout and ownership 4113, enforcement 3608, skills and hooks 2212, pinned upstreams 1026, commands 925. Keeping any one of the two largest in `AGENTS.md` puts `.windsurfrules` above 12000.

Survey findings on master for this repository: Antigravity cap, Windsurf cap for `AGENTS.md` and `.windsurfrules`, and the median check for all eight clients (for example Codex 24113 bytes against a threshold of 16346).

## Reproduce

```sh
make compile-context
wc -c AGENTS.md .cursor/rules/hiss-invariants.mdc
LC_ALL=C.UTF-8 wc -m .windsurfrules
```
