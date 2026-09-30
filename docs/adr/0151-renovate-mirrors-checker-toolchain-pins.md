<!-- markdownlint-disable MD013 -->
# ADR-0151: CI toolchain pins that the build-config checker copies get a checker-validated Renovate regex manager

- **Status**: Accepted; enforcement claim narrowed by [ADR-0152](0152-renovate-guard-enforcement-scope.md)
- **Date**: 2026-09-30
- **Deciders**: Lusoris
- **Tags**: ci, build, supply-chain, renovate

## Context

`scripts/check-build-config.py` (the fast-suite test `build-config-sync`)
checks the CI workflows against values it holds itself. One of those values is
the Go toolchain that runs actionlint in `ci.yml`'s docs job: the
`actions/setup-go` step's `go-version`. Renovate's built-in github-actions
handling bumps that input, and nothing else. PR #59, Renovate's
`go 1.26.x -> 1.27.x` update, therefore changed `ci.yml` alone. The checker
still expected `'1.26.x'`, so every automatic Go bump would open with a red
fast suite.

[ADR-0144](0144-ffmpeg-pin-and-ci-runner-policy.md) solved the same kind of
problem for the FFmpeg pin. There the pinned value lives in `build-config.env`
and a regex manager updates it. ADR-0144 does not cover a CI toolchain value
that the checker copies, and it does not say how a second update site is kept
in the same Renovate update.

## Decision

The checker keeps each copied CI toolchain pin in a single literal
(`ACTIONLINT_GO_VERSION` for Go). `renovate.json` carries one regex
`customManager` per literal, and that manager copies the dependency identity
Renovate's built-in handling gives the workflow value. For `actions/setup-go`
the identity is: `depName` `go`, `github-releases` of `actions/go-versions`,
`npm` versioning, and the actions-versions `extractVersion`. Renovate then sees
one dependency at two sites and puts both edits in the same
`renovate/go-<major>.x` branch.

The checker also validates that manager:

- exactly one regex manager covers the checker, with `managerFilePatterns`
  evaluated the way Renovate evaluates them;
- the templates are exactly the ones above;
- its `matchStrings` entry captures the literal exactly once.

Its `--self-test` confirms that a config that would split or drop the update is
rejected.

A future pin copied into the checker follows the same rule. The alternative is
for the checker to read that value from its source instead of copying it.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Mirroring regex manager, validated by the checker | One green Renovate PR per bump; a config drift fails the fast suite, not the next bump | A second place names the dependency; the checker has to model Renovate's file matching and the setup-go identity | **Chosen** |
| Checker reads `go-version` from `ci.yml` instead of copying it | No second update site, no Renovate change | The checker would accept any value, including a hand edit Renovate would never propose; the pin loses its independent check | Weakens the drift check the checker exists for |
| `packageRules` grouping only | Config-only | Grouping merges updates Renovate already found. The checker literal is not a dependency until a manager extracts it | Does not solve the failure |
| `postUpgradeTasks` rewriting the checker | Exact edit | Needs a self-hosted Renovate with `allowedCommands`; the hosted app does not run it | Not available to this repo |
| Drop the Go version from the checker | Simplest | Loses the only check that the docs job's toolchain is intentional | Rejected |

## Consequences

- **Positive**: an automatic Go bump changes `ci.yml` and the checker together
  and should pass `build-config-sync`. A Renovate config that would split the
  update fails locally in `meson test --suite=fast`.
- **Positive**: the checker's `managerFilePatterns` evaluation now follows
  Renovate's `matchRegexOrGlob`: `/re/` and `/re/i` with optional `!`, the `*`
  catch-all, and minimatch globs with `dot` and `nocase`. Glob syntax outside
  the modelled subset is rejected rather than approximated.
- **Negative**: `GO_RENOVATE_TEMPLATES` duplicates Renovate's known-action
  config for `actions/setup-go`. If Renovate changes that config (a new
  datasource or package), the two sites split again. The checker cannot detect
  that upstream change; the next Renovate PR shows it as a red
  `build-config-sync`.
- **Negative**: a maintainer commit on a Renovate branch stops Renovate from
  rebasing that PR automatically.
- **Neutral / follow-ups**: PR #59 is the first run. A config dry run proves
  that both sites are extracted, but it does not prove that both are updated
  in one PR. That is only observable on the next Go minor, 1.28.

## References

- [Research digest 0151](../research/0151-renovate-setup-go-mirroring.md).
- [ADR-0144](0144-ffmpeg-pin-and-ci-runner-policy.md) (Renovate owns the
  FFmpeg pin) and [ADR-0108](0108-deep-dive-deliverables-rule.md).
- PR #59 (`chore(deps): update dependency go to 1.27.x`).
- Source: the red `build-config-sync` on PR #59 and a review finding that
  ADR-0144 did not cover this rule. No direct user quote.
