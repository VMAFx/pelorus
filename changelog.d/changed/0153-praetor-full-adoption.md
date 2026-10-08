- Pelorus now adopts Praetor's Git hooks, dev container, and branch ruleset
  instead of declining them. `make hooks-install` installs Lefthook hooks that
  run the context check and two offline audits on commit and the audit, gate,
  and `make verify-all` on push; `lefthook.yml` sets `no_auto_install`, so
  rerun it after the file changes. `.github/rulesets/main.json` requires
  signed commits, linear history, and the seven pull-request checks with zero
  approving reviews; it takes effect once the maintainer applies it with
  `praetorctl sync --remote`, and until then a local audit with a forge token
  reports the live protection drift. `.devcontainer/` holds the rendered dev
  container, and the new `Dev container image` workflow builds its toolchain
  base on pull requests and publishes it, with build provenance, as
  `ghcr.io/vmafx/pelorus-dev` from `master`
  ([ADR-0153](docs/adr/0153-praetor-full-adoption.md)).
