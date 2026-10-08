<!-- markdownlint-disable MD013 -->
# Research 0153: Praetor full adoption at `492a00f9`

Measurements behind [ADR-0153](../adr/0153-praetor-full-adoption.md), taken on
2026-10-08 on `master` `54d533f`; the gates were re-run after a rebase onto
`1d061e4`, which changes only Renovate, the build-config checker, and their
docs. Adoption and the hook proof ran in throwaway
clones (`git clone --no-local`) whose `origin` names `VMAFx/pelorus` with
pushing disabled, so no command installed hooks into a shared Git directory.

## Setup

| Item | Value |
| --- | --- |
| Engine | `492a00f930e1a2df557ffebb76564cfa65637b77`, built from a clone at that commit (`praetorctl version 492a00f930e1`) |
| Host | Linux, Go 1.27.1, Node.js 26.10, Lefthook 2.1.14, hadolint 2.14.0, actionlint 1.7.12 |
| Not measured | Container builds: the host had too little free disk; the `0af07a73` revision of this digest built both images with the same base digest and features |

## Procedure

```bash
# .standards.yaml: drop adoption.decline, add
#   overrides.branch_protection.review_mode: single_maintainer
mv lefthook.yml ../lefthook.pelorus.yml        # a kept file is not rendered
praetorctl adopt --dry-run --lock-source-root=<praetor at 492a00f9> --path .
praetorctl adopt --lock-source-root=<praetor at 492a00f9> --path .
```

A plain run is enough: no file the audit compares byte for byte had drifted,
and `AGENTS.md` and `.zed/settings.json` are kept without `--force`. With a
local-path `origin` the dry run could not resolve the repository identity and
skipped the checkpoint lifecycle, so `origin` must name the GitHub repository.
Adoption reported 16 files created, including `.git/hooks/pre-commit` in the
clone. The Pelorus jobs were then merged into `lefthook.yml` by hand.

## Rendered versus kept

| Asset | Result |
| --- | --- |
| `.devcontainer/` (`devcontainer.json`, `Dockerfile.praetor`, 6 source parts) | Rendered, committed unchanged; `common-utils` now carries `installZsh: false`, `upgradePackages: true` |
| `lefthook.yml` | Rendered; Pelorus adds `no_auto_install: true`, `pelorus-audit` (`make audit AUDIT_FLAGS=--offline`), `verify-all` |
| `.config/lefthook/`, `.config/agent/` | Rendered; checkpoint `base` set to `master` (Praetor issue 71, still open) |
| `.github/rulesets/main.json` | Rendered under `single_maintainer`: 0 approvals, no code-owner review, admin bypass for pull requests, 7 checks; committed, not applied |
| `.gitattributes` | Managed block gains `.devcontainer/Dockerfile.praetor text eol=lf` |
| `.devcontainer/base/Containerfile`, `.github/workflows/devcontainer-image.yml` | Pelorus-owned; hadolint and actionlint clean |

## Hooks

| Step | Result |
| --- | --- |
| `make hooks-install` | installs `pre-commit`, `post-commit`, `pre-push` |
| signed probe commit | `context-check`, `hiss-audit`, `pelorus-audit`, `state-sync`, `dedupe-cadence` pass; 0.5 s |
| planted drift in generated `CLAUDE.md` | commit refused: all three pre-commit jobs fail |
| `lefthook run pre-push`, forge token present | `flavor-audit`, `gate` pass; `audit`, `verify-all` fail on the live protection drift only |
| `lefthook run pre-push`, no forge token | `audit`, `flavor-audit`, `gate` (admitted, no receipt: Meson), `verify-all` (41 of 41 fast tests) pass; 42 s |
| edit `lefthook.yml`, `lefthook run pre-commit` | `no_auto_install: true`: `.git/info/lefthook.checksum` unchanged; `false`: hooks re-installed, checksum changed |

## Audit

| Probe | Result |
| --- | --- |
| HISS-18 | 6 findings before and after; the new workflow adds none. Without its draft step the audit adds `devcontainer-image.yml:33: pull_request job build runs on a draft pull request` |
| HISS-11 | PASS, SLSA Build Level 3 measured from `release.yml` |
| Ruleset hand edit (`required_approving_review_count` 1) | FAIL: ruleset differs from declared branch protection policy |
| Live protection, forge token present | FAIL until applied: dismiss stale reviews and signed commits not enforced; 4 of 7 checks required, missing the sanitizer, Windows, and Documentation Governance checks |
| Live protection, no token (as in CI) | SKIP; audit passes |
