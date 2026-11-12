<!-- markdownlint-disable MD013 MD060 -->
# ADR-0256: Guard the mc subgroup SAD gain with an in-binary wall-time ratio

- **Status**: Proposed
- **Date**: 2026-11-12
- **Deciders**: Lusoris
- **Tags**: vulkan, performance, testing
- **Implementation**: pending (#459)

## Context

PR #27 (commit 6bedd01) replaced the barrier-tree reduction in `block_sad` of `ffmpeg-patches/files/vulkan/pelorus_mc.comp.glsl` with `subgroupAdd` plus a lane-0 combine ([bench results v0.19](../development/bench-results.md)). The gain has a static guard (`scripts/check-mc-sad-reduction.py`, PR #548), which holds the shape of the reduction but not its speed. Issue #459 asks for a wall-time threshold.

## Decision

Pending: filled in by the implementing commit.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Absolute net wall time against a midpoint | Simple | Host speed moves it past the margin | Pending |

## Consequences

- **Positive**: pending.
- **Negative**: pending.
- **Neutral / follow-ups**: pending.

## References

- Issue #459, issue #602, PR #27, PR #548.
