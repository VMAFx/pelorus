- Corrected the documented scope of the build-config checker's Renovate guard.
  It validates the regex manager that mirrors the actionlint Go pin and the
  `ACTIONLINT_GO_VERSION` literal. Other `renovate.json` settings that would
  split the Go bump are not evaluated; such a split shows up as a red
  `build-config-sync` on the Renovate PR
  ([ADR-0152](docs/adr/0152-renovate-guard-enforcement-scope.md)).
