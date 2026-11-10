<!-- markdownlint-disable MD013 -->
# ADR-0253: The render workflow's settings step fails only when a render is due

- **Status**: Accepted
- **Date**: 2026-11-10
- **Deciders**: Lusoris
- **Tags**: `release`, `docs`, `ci`
- **Related**: [ADR-0252](0252-scheduled-changelog-render-pull-request.md) (amended), [ADR-0251](0251-changelog-render-owed-on-pull-requests.md), [ADR-0245](0245-changelog-render-at-landing.md)

## Context

The settings step introduced by [ADR-0252](0252-scheduled-changelog-render-pull-request.md) runs when either `open=true` or `current=true`. Run [42856098380](https://github.com/VMAFx/pelorus/actions/runs/42856098380), at 2026-11-10T16:35:46Z on baseline `1f434b4`, failed with `repository variable RELEASE_BOT_CLIENT_ID is not set (ADR-0252)` even though the base rendered current. The checker emits `current=true` for that base, so every hourly run could fail despite having no render debt. ADR-0252's Consequences specify that every scheduled run needing a token fails by name until settings exist; this amendment narrows that failure to a due render.

The maintainer added the settings at 2026-11-10T18:16Z. The subsequent workflow_dispatch run [42873719398](https://github.com/VMAFx/pelorus/actions/runs/42873719398), at 18:17Z, passed and opened [PR #595](https://github.com/VMAFx/pelorus/pull/595). The change therefore governs any later absence; the absent-settings path is covered by self-tests rather than a live removal of working settings.

## Decision

Amend ADR-0252: the settings step fails only when `open=true` and a release-bot setting is missing. Preserve the five-step workflow, the 6 h opening threshold, the 20 h pull-request check in ADR-0251 and the 24 h push check in ADR-0245 as the named barriers.

1. Keep the settings step named `Release-bot settings are present`, with its existing `open`-or-`current` condition. Give it `id: settings`, pass `RENDER_DUE` from `steps.decide.outputs.open`, and run `python3 -I scripts/release/open-render-pr.py --check-settings --due "$RENDER_DUE" >> "$GITHUB_OUTPUT"`. Use a plain redirect so a pipe cannot mask failure. `--check-settings` requires `--due true|false`; omission exits 2 with `error: --check-settings requires --due (true or false)`.
2. With both settings present, emit exactly `present=true` and exit 0. With missing settings and a due render, emit one existing ADR-0252 `::error::` line per missing setting to stderr, no stdout, and exit 1. Collect both missing settings rather than stopping at the first.
3. With missing settings and a current base, emit `present=false` and exit 0. Emit one stderr `::notice::` naming `repository variable RELEASE_BOT_CLIENT_ID` and `repository secret RELEASE_BOT_PRIVATE_KEY`, each as `set` or `not set`, and stating that the base renders current, so the token mint and the open-or-close step are skipped (ADR-0253).
4. Gate both mint and opener on `steps.settings.outputs.present == 'true'`. With both settings present, mint and open-or-close retain their existing behaviour. When settings vanish while current, an existing App render pull request stays open: the App cannot close it without settings. If render debt later develops, the settings step fails when due, and the unchanged 20 h/24 h checks fail by name at their thresholds.
5. Cover the settings and CLI contract with self-tests s1-s9 in `scripts/release/open-render-pr.py`. Extend the existing workflow guard with planted cases R26-R33 for the decide condition, due input, output redirect, settings id, downstream conditions and a piped command; retain R1-R25, R6b and R9b, including accepted action pin updates.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep ADR-0252 as shipped | No implementation change; missing settings always visible | Makes master red hourly on a current base; recurring repository-health finding masks other failures | Failure does not indicate work due |
| Fail only when due; notice and skip when current (b) | Keeps the 6 h early failure and named 20 h/24 h barriers; current bases stay green | A stale App pull request stays open until settings return | Chosen |
| Always warn when settings are absent | Every scheduled run stays green | A due render becomes warning-only, losing the 6 h early red and reading as a silent fallback | Hides failed automation when action is due |

## Consequences

- **Positive**: a current base with absent settings passes with a notice naming both settings and the skipped work; due renders retain explicit failure for every absent setting.
- **Negative**: a current base cannot close a stale App pull request while settings are absent. Manual closure or restoring settings remains necessary.
- **Neutral / follow-ups**: s1-s9 and R26-R33 make the absent-settings behaviour replayable. ADR-0252's accepted body remains immutable; its status links this amendment. The 20 h pull-request and 24 h push checks retain their existing behaviour.

## References

- [ADR-0252](0252-scheduled-changelog-render-pull-request.md), [ADR-0251](0251-changelog-render-owed-on-pull-requests.md), [ADR-0245](0245-changelog-render-at-landing.md).
- Workflow runs [42856098380](https://github.com/VMAFx/pelorus/actions/runs/42856098380) and [42873719398](https://github.com/VMAFx/pelorus/actions/runs/42873719398); [PR #595](https://github.com/VMAFx/pelorus/pull/595).
- Maintainer decision `pelorus-render-pr-settings-step-red`, 2026-11-10, settings step scope (paraphrased): fail only when a render is due; on a current base, report absent settings in a notice and skip token mint and open-or-close.
- [`open-render-pr.py`](../../scripts/release/open-render-pr.py) settings tests s1-s9; [`check-build-config.py`](../../scripts/check-build-config.py) workflow cases R26-R33.
