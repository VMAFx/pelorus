<!-- markdownlint-disable MD013 -->
# ADR-0170: The build-config checker validates the shape of the setup-go and actionlint pins; a ci.yml regex manager tracks actionlint

- **Status**: Accepted
- **Date**: 2026-10-08
- **Deciders**: Lusoris
- **Tags**: ci, build, renovate, supply-chain
- **Amends**: the scope of [ADR-0151](0151-renovate-mirrors-checker-toolchain-pins.md) and [ADR-0152](0152-renovate-guard-enforcement-scope.md); their bodies stay as accepted

## Context

Two pins in `ci.yml`'s docs job break the rule that a Renovate bump must leave
the fast suite green and must be tracked at all:

- `scripts/check-build-config.py` held `SETUP_GO_COMMIT`, a copy of the
  `actions/setup-go` digest, and required `actions/setup-go@<that digest>`.
  Renovate's github-actions manager rewrites the digest in `ci.yml` and
  `standards-gate.yml` but cannot edit a Python constant, so its next setup-go
  PR would fail `build-config-sync`. [ADR-0151](0151-renovate-mirrors-checker-toolchain-pins.md)
  already removed the same class of copy for `msys2/setup-msys2` by checking
  the shape; setup-go was missed.
- `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12` is a shell line.
  No built-in Renovate manager reads it, so actionlint releases were never
  proposed. The checker also required the literal `@v1.7.12`, so a manual
  bump needed a checker edit.

## Decision

1. The checker validates the shape of both pins and holds no copy of either
   value:
   - the docs job has exactly one `uses: actions/setup-go@<40-hex> # vX.Y.Z`
     line (floating tag, missing release comment, short digest, second step:
     rejected);
   - the docs job has exactly one
     `run: go run github.com/rhysd/actionlint/cmd/actionlint@vX.Y.Z` line
     (`@latest`, branch refs, no version: rejected).
2. `renovate.json` gains one regex `customManager` with
   `managerFilePatterns` `/^\.github/workflows/ci\.yml$/`, one `matchStrings`
   entry capturing `currentValue` `v\d+\.\d+\.\d+` on that line,
   `depNameTemplate` `github.com/rhysd/actionlint` and `datasourceTemplate`
   `go`. Renovate's `go` datasource defaults to semver versioning and looks the
   module up through the Go proxy, so no versioning template is needed.
3. The checker requires exactly one regex manager to cover `ci.yml`, with those
   two templates, whose `matchStrings` entry matches `ci.yml` exactly once and
   captures the pinned tag. A manager that also covers the checker file is
   rejected by the existing exactly-one rule for the checker, so the
   [ADR-0151](0151-renovate-mirrors-checker-toolchain-pins.md) Go manager
   stays the only one that targets it.
4. `--self-test` carries a negative case for each rule above.

This extends the checked Renovate contract of
[ADR-0152](0152-renovate-guard-enforcement-scope.md) by one manager. Other
`renovate.json` settings stay unevaluated, and a split is still detected by a
red Renovate PR.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Check the shape in the checker; track actionlint with a ci.yml regex manager | Renovate bumps stay green; actionlint is tracked; no checker edit per bump | One more manager to validate | **Chosen** |
| Keep the digest and tag literals in the checker and add regex managers for the checker | Pins stay checker-visible | Two files change per bump; Renovate would need a digest-aware manager for the checker; the msys2 precedent already rejected this | More moving parts for no added safety |
| Leave actionlint untracked | No new manager | Silent staleness; manual bumps | Fails the tracking goal |
| `.github/actionlint-version` file read by the workflow | Standard `renovate: datasource` comment manager | Changes the workflow shape for a one-line pin | Larger change than the regex manager |

## Consequences

- **Positive**: a Renovate setup-go or actionlint PR changes `ci.yml` only and
  passes the fast suite. Actionlint releases are proposed automatically.
- **Negative**: the checker no longer rejects a stale digest or an older
  actionlint tag; only the shape is enforced. The Praetor-locked
  `praetor-docs.yml` and `standards-gate.yml` are not covered by this check.
- **Neutral**: the Go toolchain rules of ADR-0151 are unchanged.

## References

- [ADR-0151](0151-renovate-mirrors-checker-toolchain-pins.md),
  [ADR-0152](0152-renovate-guard-enforcement-scope.md),
  [research digest 0170](../research/0170-renovate-actionlint-manager.md).
- Source: task `ci_dependency_tracking` (2026-10-08): every bumpable CI pin
  yields a green Renovate PR and is tracked.
