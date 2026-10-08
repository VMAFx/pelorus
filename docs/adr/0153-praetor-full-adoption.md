<!-- markdownlint-disable MD013 -->
# ADR-0153: Adopt Praetor's dev container, Git hooks, and branch ruleset

- **Status**: Proposed
- **Date**: 2026-10-03, re-rendered 2026-10-08
- **Deciders**: Lusoris
- **Tags**: governance, standards, hooks, devcontainer, ci

This ADR amends [ADR-0145](0145-praetor-governance-adoption.md). It replaces
that ADR's decision to decline the `branch-ruleset`, `dev-container`, and
`git-hooks` adoption steps. Every other ADR-0145 decision stands, as does the
engine pin of [ADR-0168](0168-praetor-engine-492a00f.md) (`492a00f9`).

## Context

ADR-0145 kept three `adoption.decline` entries in `.standards.yaml`. An agent
added them in the 2026-09-20 onboarding commit; no maintainer decision chose
them. On 2026-09-30 the maintainer decided on full adoption: every decline goes,
and the repository takes the assets the pinned engine renders for those steps.
Pull request 67 first rendered them at engine `25451d88`, then at `0af07a73`
on top of pull request 76. Both have since merged into `master` with the
re-pin to `492a00f9` (ADR-0168), so this revision renders all three steps
again at `492a00f9` on `master`
([research digest 0153](../research/0153-praetor-full-adoption.md)).

What the engine renders at `492a00f9`:

- **Dev container.** `.devcontainer/` holds `devcontainer.json`,
  `Dockerfile.praetor`, and six `praetor-source.*.b64` parts that build the
  pinned `praetorctl` into the image. The audit compares the bundle with its
  own rendering byte for byte. The image is Praetor's reviewed
  `mcr.microsoft.com/devcontainers/base` digest with the `native-gpu-systems`
  catalog features `common-utils`, `nix`, and `rust`. Measured on the
  `0af07a73` rendering, which used the same base digest and features, the
  container builds and passes `compile-context` and the audit, then fails its
  `postCreateCommand` (`make verify-all`) at the first `meson` call: it has no
  Meson, Ninja, clang, glslc, Node.js, or Lefthook.
- **Git hooks.** `lefthook.yml` carries the engine's governance jobs only
  (Pelorus has no `go.mod` or `Cargo.toml`), plus the checkpoint scripts, a
  checkpoint policy, and the agent interceptor. Adoption runs
  `lefthook install` when `lefthook.yml` is an unedited rendering, and keeps
  any other file without installing it. The checkpoint policy still names
  `main` as its base branch (Praetor issue 71). Lefthook's `lefthook run`
  re-installs the hooks whenever the configuration differs from the installed
  one, and linked worktrees share one hooks directory.
- **Branch ruleset.** `.github/rulesets/main.json`, `praetor-main-protection`,
  protects `master` and `lts-*`. The engine derives its required checks from
  the workflows: a job is required when every pull request reports it. No
  Pelorus workflow is path-filtered, so the merge-gate aggregate that Praetor
  pull request 859 proves is not needed, and the rendering requires the seven
  leaf checks. Live `master` protection is a classic branch rule: linear
  history, no approving reviews, no signatures, and four of those checks.
- **Live protection audit.** Once `branch-ruleset` is no longer declined, the
  audit compares the protection GitHub enforces with the declared policy
  whenever it finds a forge token (Praetor pull requests 795 and 807). The
  hosted Standards job has no token and reports that check as not made.

## Decision

We will remove `adoption.decline` from `.standards.yaml` and commit the
engine's rendering of all three steps at `492a00f9`, produced in a throwaway
clone because adoption runs `lefthook install`. We depart from the rendering
and add to it in these places:

- **Git hooks.** Keep every generated job in `lefthook.yml` and add two
  Pelorus jobs: pre-commit `pelorus-audit`
  (`make audit AUDIT_FLAGS=--offline`, the hosted debt-delta mode) and
  pre-push `verify-all` (`make verify-all`). The pre-commit audit stays
  offline like the generated `hiss-audit`, so no commit fails on a forge
  setting; the `Makefile` `audit` target takes `AUDIT_FLAGS` for it. Set
  `no_auto_install: true`, so `lefthook run` never rewrites the shared hooks
  directory; hooks are installed per clone with `make hooks-install`, never
  by CI or by an agent. Set the checkpoint policy `"base"` to `"master"`.
- **Branch ruleset.** The maintainer decided on 2026-10-03 that the ruleset
  requires zero approving reviews and no code-owner review, and keeps the
  required status checks, signed commits, and linear history.
  `.standards.yaml` declares this with
  `overrides.branch_protection.review_mode: single_maintainer`, the mode
  Praetor documents for a sole maintainer (`docs/guides/review-policy.md`).
  The rendering then requires 0 approvals, no code-owner review, and the
  seven checks, and carries the repository admin role as a bypass actor for
  pull requests only. The file stays unedited by hand, so `adopt` and the
  audit verify it. This change does **not** apply the ruleset. The maintainer
  applies it with `praetorctl sync --remote` from an up-to-date `master` after
  this change merges. The repository stays squash-only; GitHub signs a squash
  commit made in its web interface, which the signed-commit rule requires.
- **Dev container.** Commit the rendered bundle unchanged. Add
  `.devcontainer/base/Containerfile`, a toolchain base image on the same
  reviewed base digest with the packages the CI jobs install, Node.js 24,
  actionlint 1.7.12, and Lefthook 2.1.14, each download checked against its
  published SHA-256.
- **Base image workflow.** `.github/workflows/devcontainer-image.yml` builds
  the Containerfile without pushing on a pull request that touches it; that
  job starts with Praetor's draft step, so it adds no HISS-18 finding. On a
  push to `master` that touches it, and on manual dispatch, it pushes
  `ghcr.io/vmafx/pelorus-dev` tagged with the commit SHA and `latest`, reads
  the digest back from the registry, and attests SLSA build provenance for
  that digest with `actions/attest-build-provenance` v4.2.2 (`subject-name`,
  `subject-digest`, `push-to-registry`). Only that job holds `packages: write`,
  with `id-token`, `attestations`, and `artifact-metadata`. Re-rendering the
  bundle on the published digest is a follow-up change.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep the three declines (ADR-0145) | No new surface | No reproducible environment; the declared protection policy has no committed artifact | The maintainer chose full adoption |
| Commit the rendered `lefthook.yml` unchanged | Adoption verifies and activates it | Drops `make audit` and `make verify-all`, the native gate Pelorus ran before every push | Weaker than the hooks Pelorus already tracked |
| Keep pre-commit `make audit` online (the `0af07a73` revision) | One audit command everywhere | Every commit fails on the live protection drift until the ruleset is applied; Praetor keeps pre-commit offline by design | A commit should not depend on a forge setting |
| Leave Lefthook's auto-install on | Matches the default rendering | A commit from any worktree with a different `lefthook.yml` rewrites the hooks every worktree shares | Contradicts explicit hook installation |
| Require an `always()` merge-gate aggregate (Praetor pull request 859) | One required check | Only needed for path-filtered CI; adds a job whose shape the proof must keep accepting | The engine renders the seven leaf checks for unfiltered CI |
| Hand-edit the ruleset's review counts | Same JSON | The audit compares the file with its rendering; the next `adopt` reverts it | The manifest override is the supported source |
| Hand-edit `devcontainer.json` (features, `postCreateCommand`) | No base image to publish | The audit rejects drift from the rendering | Cannot pass the audit |
| Publish the image without a pull-request build | Fewer runs | The Containerfile first builds on `master`, after the merge | A build-only job proves it before the merge |
| Build-only pull-request job without the draft step | Runs on drafts | Adds a HISS-18 finding | The draft step costs one failed check on a draft |
| `docker/login-action`, `docker/build-push-action` | Layer caching, a digest output | Three more pinned actions | The runner's docker CLI and `buildx imagetools` are enough for one image |
| `actions/attest` instead of `actions/attest-build-provenance` | The action upstream recommends for new work | A second action identity beside `release-build.yml` | One pinned attestation action for releases and images |
| Run `sync --remote` in this change | Live protection matches the file at once | Forge mutation from a branch; requires checks `master` does not run yet | The maintainer applies it after the merge |

## Consequences

- **Positive**: every surface the declared profile renders is committed and
  verified by the audit. Measured in a throwaway clone: after
  `make hooks-install` a commit runs `context-check`, `hiss-audit`, and
  `pelorus-audit`, then `state-sync` and `dedupe-cadence` (0.5 s); a planted
  context drift is refused; `lefthook run pre-push` without a forge token
  passes `audit`, `flavor-audit`, `gate`, and `verify-all` (42 s, 41 of 41
  fast tests). With `no_auto_install` a changed `lefthook.yml` leaves the
  installed hooks alone; without it Lefthook re-installs them.
- **Negative**: until the ruleset is applied, an audit that finds a forge
  token fails on the live protection drift, and so do the pre-push `audit`
  and `verify-all` jobs and a local `make verify-all`; the hosted jobs are
  unaffected. The rendered dev container cannot run the native gates until the
  bundle is re-rendered on the base image, and neither image passes a GPU
  through. The image workflow has never run: its first pull-request run fails
  on a draft by design, and its first publish needs the `VMAFx` organization
  to let the repository write the `pelorus-dev` package. Installed hooks need
  `praetorctl`, `lefthook`, `make`, Python 3, and, for a push, the native and
  documentation toolchains; `lefthook.yml` changes need a new
  `make hooks-install`. Renovate moves the Containerfile's base digest but not
  its actionlint, Lefthook, and Node.js versions.
- **Neutral / follow-ups**: apply the ruleset with `praetorctl sync --remote`
  from `master` after this change merges, read it back with
  `praetorctl plan --remote`, and decide whether to remove the classic
  `master` rule. After the first successful publish, re-render the bundle with
  `praetorctl devcontainer --source-root <Praetor at the pin> --force
  --base-image ghcr.io/vmafx/pelorus-dev@sha256:<digest>` in a separate change.
  Drop the checkpoint `base` edit when Praetor issue 71 is fixed. Track the
  Containerfile tool versions in Renovate.

Regenerate the rendered assets in a throwaway clone whose `origin` names
`VMAFx/pelorus`, then copy them back and re-add the Pelorus jobs:

```bash
praetorctl adopt --lock-source-root=<Praetor checkout at 492a00f9> --path .
```

## References

- [ADR-0145](0145-praetor-governance-adoption.md) and
  [ADR-0168](0168-praetor-engine-492a00f.md): the adoption this ADR amends and
  the engine pin it uses.
- [Research digest 0153](../research/0153-praetor-full-adoption.md): rendered
  assets, hook proof, and audit results.
- [Development guide](../development/build.md#git-hooks-opt-in-per-clone):
  hook, ruleset, and dev container procedures.
- Praetor at `492a00f930e1a2df557ffebb76564cfa65637b77`:
  `docs/adoption.md` (which jobs the ruleset requires),
  `docs/guides/git-hooks.md`, `docs/guides/devcontainer-bootstrap.md`,
  `docs/guides/review-policy.md`, `docs/guides/workflow-triggers.md`.
- Praetor issue [71](https://github.com/cordanaLLM/praetor/issues/71) and pull
  requests [795](https://github.com/cordanaLLM/praetor/pull/795),
  [807](https://github.com/cordanaLLM/praetor/pull/807), and
  [859](https://github.com/cordanaLLM/praetor/pull/859).
- `actions/attest-build-provenance` v4.2.2
  (`4d101475d8b20a2381f78447822ac1eab6504dd8`), `action.yml` inputs
  `subject-name`, `subject-digest`, `push-to-registry`.
- Source: `req`, 2026-09-30: user decision on full adoption; remove every
  `adoption.decline` entry and adopt the rendered assets.
- Source: `.workingdir/QUESTIONS.md` Q-002 (decided: "0 reviews, keep checks + signed commits + linear history"), Q-003 (decided: "after #76 is merged by rebase; then squash-only + signed commits"; restated 2026-10-08: apply only after this change merges), Q-004 (decided: "CI job publishes ghcr.io/vmafx/pelorus-dev; bundle re-render with digest in a follow-up PR").
