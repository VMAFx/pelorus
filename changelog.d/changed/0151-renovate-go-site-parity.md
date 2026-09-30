- The build-config checker now also rejects `renovate.json` settings that would
  bump the actionlint Go pin in `ci.yml` without the checker literal, or the
  reverse. It flags `includePaths`/`ignorePaths` that drop either file,
  `enabledManagers` without both `github-actions` and `custom.regex`, a
  top-level `github-actions` or `regex` block that changes that manager, extra
  options on the Go regex manager, and `packageRules` entries that can reach
  `go` and tell the two sites apart. Before, it checked only the regex manager,
  and the docs claimed more than that. Presets named in `extends` are still not
  expanded
  ([ADR-0151](docs/adr/0151-renovate-mirrors-checker-toolchain-pins.md)).
