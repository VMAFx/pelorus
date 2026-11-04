<!-- markdownlint-disable MD013 -->
# ADR-0245: Changelog render at landing

- **Status**: Proposed
- **Implementation**: pending (#518)
- **Date**: 2026-11-04
- **Deciders**: Lusoris
- **Tags**: release, docs, ci

## Context

Per-PR changelog rendering makes every pull request touching `changelog.d/` also touch `CHANGELOG.md`. Pull requests landing in sequence conflict on the rendered file (#517 went conflicting after #508 landed, on `CHANGELOG.md` alone). Each conflict costs a merge of the default branch, a re-render and a CI run.

## Decision

We will require fragments only per pull request and move changelog rendering to landing and release cut. A render-only pull request lands per landing batch and at release cut (`release-build.yml` keeps `--check`). A push to the default branch runs the staleness check (`scripts/release/check-changelog-staleness.py`) with a 24 h limit, warning when fragments remain unrendered within 24 h and failing when unrendered fragments exceed 24 h. A hand-edited `CHANGELOG.md` is red at any age.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep per-PR render (runner-up) | Every commit on the default branch has an up-to-date changelog | Every pull request touching `changelog.d/` touches `CHANGELOG.md`, causing merge conflicts on sequence landings | Conflicts cost unnecessary merges, re-renders and CI runs |
| Bot render commit on the default branch | Fully automated changelog updates without manual render PRs | Requires a repository push token and a bypass of the signed-commit rule | Violates the signed-commit policy and requires token credential management |
| No staleness check | Minimal tooling and zero gate overhead | Changelog render drifts indefinitely until release cut | Leaves the default branch's `CHANGELOG.md` stale for unbounded periods |

## Consequences

- **Positive**: pull requests no longer conflict with each other on `CHANGELOG.md`; contributors add only fragment files under `changelog.d/`; sequence landings do not trigger merge-and-rerender cycles.
- **Negative**: `CHANGELOG.md` on the default branch can lag fragment additions by up to 24 h until a render-only pull request is landed.
- **Neutral / follow-ups**: `scripts/release/check-changelog-staleness.py` enforces the 24 h staleness bound on the default branch and runs its self-test in pull request CI; documentation and templates updated to guide contributors.

## References

- VMAFx/vmafx ADR-2197 (fragments are inputs, render at landing) and ADR-3295 (render-only PR per batch, 24 h staleness window).
- Maintenance ledger entry `pelorus-changelog-render-policy` (the maintainer chose the vmafx shape).
