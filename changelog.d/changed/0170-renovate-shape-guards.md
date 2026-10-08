- The build-config checker no longer holds a copy of the `actions/setup-go`
  digest or the actionlint tag. It checks the shape of both pins in `ci.yml`
  (full digest with a `# vX.Y.Z` comment; exact `@vX.Y.Z` tag), so Renovate
  bumps of either stay green. A new `renovate.json` regex manager for `ci.yml`
  makes Renovate track the actionlint release through the `go` datasource, and
  the checker validates that manager. See ADR-0170.
