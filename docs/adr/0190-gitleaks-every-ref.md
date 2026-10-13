<!-- markdownlint-disable MD013 MD060 -->
# ADR-0190: CI scans every branch and tag for credentials with a pinned gitleaks

- **Status**: Accepted
- **Date**: 2026-10-13
- **Deciders**: Lusoris
- **Tags**: security, ci, governance

## Context

HISS-12 (zero credentials in Git history) was decided by review only: no
workflow ran a secret scanner. The Praetor engine at `3bbde073` adds the
declared-linters gate. Its audit fails when a linter that the policy declares
runs in no workflow step and no dated exception names it. The policy declares
`gitleaks` and `trivy` (facet `security:high`), `semgrep` (profile
`native-gpu-systems`, facet `agent:sandboxed`) and `cppcheck` (profile
`native-gpu-systems`). Re-adoption wrote one dated exception for each of the
four.

A scan of the checked-out branch alone misses a credential committed to a side
branch, which is just as public as one on `master`. The repository already
carries a Praetor-adopted `.gitleaks.toml` that extends the gitleaks default
rules.

## Decision

The `docs` CI job, a required check, runs `scripts/check-gitleaks.sh` on every
push and pull request. Its checkout fetches full history (`fetch-depth: 0`), so
every branch is present as a remote-tracking ref and every tag is fetched.

- `--install DIR` downloads gitleaks 8.30.1 (linux x64) and checks its sha256
  against the pin in the script. A mismatch fails the job.
- A scan passes `--log-opts=--all`, which covers local branches,
  remote-tracking branches and tags. It uses the repository's `.gitleaks.toml`
  explicitly, refuses inline `gitleaks:allow` comments and runs under a
  600-second deadline. Exit 0 means no findings, 1 means findings, and any
  other gitleaks status fails.
- The only exception is a fingerprint line (`commit:file:rule:line`) in
  `.gitleaksignore`. `.gitleaks.toml` is checked against an allow-list: it may
  only keep `[extend] useDefault = true` and add rules. An allowlist at top
  level or inside a rule is refused.
- `--self-test` runs before the scan. It must:
  - accept a clean fixture;
  - refuse a secret that exists only on a remote-tracking side branch;
  - refuse a secret marked `gitleaks:allow`;
  - refuse a configuration without the default rules, or with an allowlist at
    top level or inside a rule;
  - refuse a binary of another version;
  - accept the side-branch secret once its fingerprint is listed.
- `.gitleaksignore` lists the two commits that add an FFmpeg `metadata` filter
  line in `docs/metrics/grain_estimate.md`. The `generic-api-key` rule reads the
  filter's `key` option and its frame-metadata value as a credential.

The dated exceptions for `cppcheck`, `semgrep` and `trivy` (expiry 2027-01-11)
stay, each with its reason in `.standards.yaml`:

- **cppcheck**: clang-tidy already covers every Meson-built unit (ADR-0168). A
  cppcheck lane first needs a recorded baseline of its current findings.
- **semgrep**: needs a pinned rule set kept in this repository. Registry
  configurations download their rules at run time.
- **trivy**: needs a pinned vulnerability database. trivy downloads its
  database at run time, so two runs of one commit can disagree.

## Alternatives considered

- **`gitleaks/gitleaks-action`.** Rejected: repositories of an organisation
  (VMAFx is one) need a licence key from gitleaks.io. Since v2.0.0 the action
  is no longer MIT-licensed. It also fetches the gitleaks binary itself, while
  the script keeps version and checksum in one reviewed place.
- **Scan only the checked-out branch.** Rejected: a credential on a side branch
  is published all the same, and the self-test's side-branch case would pass
  unnoticed.
- **A separate `secrets` job.** Rejected for now: a new job is not a required
  check until the forge ruleset is re-applied. Inside the `docs` job the scan
  blocks a merge at once.
- **Silence the documentation finding with an allowlist in `.gitleaks.toml`.**
  Rejected: a pattern allowlist exempts every future match. A fingerprint
  exempts one line of one commit.

## Consequences

- A credential pushed to any branch or tag fails the `docs` job on the next CI
  run, on every branch, until the credential is removed from history and
  revoked.
- Renovate bumps `GITLEAKS_VERSION` in the script through a regex manager. The
  bump fails the `--install` step until `GITLEAKS_SHA256` is updated from that
  release's checksums file.
- A new false positive fails the scan until its fingerprint is added to
  `.gitleaksignore`. A change to the flagged line produces a new fingerprint.
- The `docs` job fetches full history. The repository is small, so the
  checkout costs seconds.

## References

- Praetor declared-linters gate, engine `3bbde07342bdece12c46f74b0ee2c1f51129478f`;
  fleet order `fleet-engine-pin-sweep-20261013`.
- gitleaks 8.30.1 release asset `gitleaks_8.30.1_linux_x64.tar.gz`, sha256
  `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`, matching
  the release's `gitleaks_8.30.1_checksums.txt`.
- Planted failure: `scripts/check-gitleaks.sh --self-test` refuses a secret
  that exists only on `refs/remotes/origin/side` of a fixture clone.
