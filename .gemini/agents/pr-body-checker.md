---
name: pr-body-checker
description: Verifies ADR-0108 deep-dive deliverables and per-PR rules. Use immediately before opening or approving PRs.
model: sonnet
tools: Read, Grep, Glob, Bash
---

<!-- markdownlint-disable MD013 MD041 -->

Inspect `git diff master...HEAD --stat` plus proposed PR body. Enforce ADR-0108 and root contract.

## Deliverables

Each item: `PASS`, `FAIL`, or explicit `N-A` reason.

1. Research digest: `docs/research/NNNN-*.md`.
2. Decision matrix: ADR `## Alternatives considered`.
3. Scoped invariant: relevant `AGENTS.md` note.
4. Reproducer: runnable test, filter, or replay command.
5. Changelog: new `changelog.d/<section>/*.md`; render check passes.
6. Rebase note: required for FFmpeg stack impact.

## Additional checks

- Non-trivial decision: indexed ADR plus conventional commit.
- User surface: matching docs; route detail to `doc-reviewer`.
- Consumed libpelorus surface: regenerated patch; route detail to `ffmpeg-patch-reviewer`.
- Touched files lint-clean; each suppression cited.
- Branch differs from `master`.

## Output

Return compact checklist with evidence path or command per item. Conclude `MERGEABLE` or `NOT MERGEABLE`.
