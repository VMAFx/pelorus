<!-- markdownlint-disable MD013 -->
# ADR-0152: The build-config checker validates the mirroring Renovate manager, not every `renovate.json` setting

- **Status**: Accepted
- **Date**: 2026-09-30
- **Deciders**: Lusoris
- **Tags**: ci, build, renovate

## Context

[ADR-0151](0151-renovate-mirrors-checker-toolchain-pins.md) added a regex
`customManager` so that Renovate bumps `ACTIONLINT_GO_VERSION` in
`scripts/check-build-config.py` in the same update as `ci.yml`'s
`actions/setup-go` `go-version`. The checker validates that manager.

ADR-0151 and `docs/development/build.md` went further and said that any
Renovate config that would split or drop the Go bump fails the fast suite. A
post-merge review of PR #59 showed that this is not true. Other `renovate.json`
settings can still send the two edits to different PRs, or drop one of them,
and the checker passes. Examples: `ignorePaths` covering `scripts/**`,
`enabledManagers` without `custom.regex`, a `packageRules` entry that disables
`go` for one manager only, a `groupName` scoped to github-actions, and presets
pulled in through `extends`.

PR #64 closed that gap by reproducing Renovate's path, manager and
`packageRules` matching inside the checker (+555 lines, read from Renovate's
source at one commit). The maintainer declined it on 2026-09-30.

## Decision

The checker's Renovate contract is exactly what it checks today:

- exactly one regex manager covers `scripts/check-build-config.py`, with
  `managerFilePatterns` evaluated the way Renovate evaluates them;
- that manager's templates copy Renovate's `actions/setup-go` identity
  (`depName` `go`, `github-releases` of `actions/go-versions`, `npm`
  versioning, the actions-versions `extractVersion`);
- its `matchStrings` entry captures the `ACTIONLINT_GO_VERSION` literal
  exactly once;
- `ci.yml`'s docs job sets exactly one `go-version`, equal to that literal.

The checker does not evaluate other `renovate.json` settings (`ignorePaths`,
`includePaths`, `enabledManagers`, top-level manager blocks, `packageRules`,
`extends` presets). If one of them splits or drops the update, the Renovate PR
that touches only one site fails `build-config-sync`. That red PR is the
accepted detection point. This ADR supersedes the ADR-0151 sentences that claim
more: the Decision paragraph on what `--self-test` confirms, the "a config
drift fails the fast suite" pro in its Alternatives table, and the first
Positive consequence ("A Renovate config that would split the update fails
locally").

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Validate the mirroring manager only; a split shows up as a red Renovate PR | Small checker; no copy of Renovate internals to keep current | A bad `renovate.json` edit is caught on the next bump, not when it is made | **Chosen** |
| Reproduce Renovate's path, manager and `packageRules` matching in the checker (PR #64) | Catches the config drift when it is made | +555 lines tracking Renovate's source; still cannot expand `extends` presets; the failure it prevents is already loud | Upkeep outweighs a loud, rare failure |
| Reject any `renovate.json` key outside an allow-list | Simple to state | Blocks legitimate Renovate tuning unrelated to Go; brittle | Too coarse |
| Run Renovate itself in CI in dry-run mode | Uses the real matcher | Lookup needs a GitHub token; slow; network-dependent | Not hermetic enough for the fast suite |

## Consequences

- **Positive**: the checker stays small, and its documentation now matches what
  it enforces.
- **Negative**: a `renovate.json` change that splits the Go bump is not caught
  when the change is made. The next Go bump opens a red Renovate PR, and a
  maintainer fixes `renovate.json` or adds the missing edit.
- **Neutral**: ADR-0151 stays Accepted for the mirroring rule itself; this ADR
  narrows only its enforcement claim.

## References

- [ADR-0151](0151-renovate-mirrors-checker-toolchain-pins.md) and
  [research digest 0151](../research/0151-renovate-setup-go-mirroring.md).
- PR #59 (the Go 1.27 bump) and PR #64 (the closed matcher reimplementation).
- Source: popup answer 2026-09-30, "Close it, fix docs only".
