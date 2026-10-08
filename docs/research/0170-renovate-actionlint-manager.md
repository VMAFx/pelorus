<!-- markdownlint-disable MD013 MD060 -->
# Tracking `go run ...actionlint@vX.Y.Z` with a Renovate regex manager

**Date:** 2026-10-08

**Decision:** [ADR-0170](../adr/0170-renovate-shape-guards.md)

**Scope:** Renovate 44.147.0 (`npx --package renovate@44.147.0`); docs.renovatebot.com
`modules/manager/regex` and `modules/datasource/go`; `renovate.json`, `ci.yml`

## Finding

- The regex manager needs `managerFilePatterns`, `matchStrings` with a
  `currentValue` group, a dependency name (`depNameTemplate`) and a datasource
  (`datasourceTemplate`). Matching is per file, not per line.
- The `go` datasource looks modules up through the Go proxy (default
  `GOPROXY`), with `semver` as default versioning. The module path is
  `github.com/rhysd/actionlint`; `cmd/actionlint` is a package inside it, so
  the dependency name is the module path, not the command path.
- No built-in manager reads a `go run <module>@<tag>` shell line. The
  github-actions manager sees only `uses:` lines.

## Verification

`renovate --platform=local --dry-run=extract` on this tree extracts, from
`.github/workflows/ci.yml`, one dependency: `depName` and `packageName`
`github.com/rhysd/actionlint`, `datasource` `go`, `currentValue` `v1.7.12`,
`replaceString` the full `go run ...@v1.7.12` text. The new manager lists only
`ci.yml`, so the Go-version manager for the checker is unchanged.
`renovate-config-validator --strict renovate.json` exits 0. The lookup step
needs a GitHub token and was not run, so the proposal of a newer tag is
unverified here.

## Open questions

- `standards-gate.yml` carries a second `actions/setup-go` digest. The
  github-actions manager bumps it; the checker does not look at it.
