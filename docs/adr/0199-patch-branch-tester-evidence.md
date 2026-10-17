<!-- markdownlint-disable MD013 -->
# ADR-0199: Tester images may publish a commit of a maintained `lts-*` patch branch

- **Status**: Accepted
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: release, tester, ci, supply-chain

## Context

Pelorus (pronounced *puh-LOR-uhs*, from the nautical pelorus bearing instrument that keeps a vessel on course) cuts every release from master so far. A patch release of an older minor (for example `v0.4.1` while master already carries 0.5 features) needs a commit that master does not contain: the previous release tag plus the picked fixes.

[ADR-0176](0176-rc-process-and-tester-publish.md) makes the `tester-publish` candidate leg mandatory for every tag: `scripts/release/tag-release.sh` refuses a tag unless the newest `workflow_dispatch` run of `.github/workflows/tester-publish.yml` for the exact commit concluded `success`. Two rules stop that leg for a commit off master:

1. The `validate` job of `tester-publish.yml` refused any commit not reachable from `origin/master`.
2. `scripts/release/check-candidate-legs.py` matched runs by `head_sha`, the commit whose workflow file ran. A dispatch from master that publishes another commit has master's tip as `head_sha`, so the leg stayed `missing`.

Ruleset `praetor-main-protection` already covers `refs/heads/lts-*` with the same rules as master (pull request, signed commits, linear history, the nine status checks, no deletion, no force push). A branch named `release/*` is not covered.

## Decision

We will accept a commit of a maintained patch branch `lts-<major>.<minor>` as tester-image evidence, with three bindings:

1. **Runner stays on master.** `scripts/release/check-publish-ref.sh` (called by `validate`) requires the dispatching commit to be reachable from master, so the publish job, its gates and its permissions always come from master's workflow file.
2. **Published commit on master or `lts-*`.** The commit to publish must be reachable from `origin/master` or from an `origin/lts-*` branch. A commit reachable only from a patch branch must be given as its full 40-hex SHA.
3. **The run name names the commit.** `run-name` is `tester-publish <event> <inputs.ref or github.sha>`. `check-candidate-legs.py` accepts a run for a commit when its `head_sha` is that commit, or when it ran on master and its run name is `tester-publish workflow_dispatch <full sha>`. The full-SHA rule in 2 makes that name exact.

`check-publish-ref.sh --self-test` runs first in `validate` and refuses a planted side-branch commit, an lts commit by branch name or short SHA, a runner off master, a malformed ref and an unknown ref. The leg checker's self-test refuses a matching run name on a non-master branch, a run name for another commit and a newer red named run.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Skip the `tester-publish` leg for patch finals | A patch release would ship without tester images for its commit; the guard gets weaker exactly where the tree differs from master |
| Cut the patch from master (`v0.5.0-rc.1` or a master-based `0.4.x`) | Consumers pinned to 0.4 would take a prerelease or unrelated 0.5 changes; a master-based `0.4.x` breaks Semantic Versioning |
| Dispatch from the patch branch itself | The workflow file on an older branch carries the older guard; every patch branch would need a workflow backport, and its run would carry the older gates |
| Name the branch `release/<major>.<minor>` | Not covered by the existing ruleset; protecting it needs a ruleset change |
| A raw signed tag without `tag-release.sh` | Bypasses the declared release guard |

## Consequences

- Positive: patch releases keep every gate (CI on the tag, tester images, licence gates, signatures, attestations) without a workflow backport on the patch branch.
- Positive: the patch branch gets master's protections through the existing ruleset; no ruleset edit.
- Negative: the image-publish gate admits a second branch family. It stays behind the `tester-publish` environment approval and the ruleset.
- Neutral: the run name of a dispatch with an input ref now shows the input instead of the runner commit.

## References

- [ADR-0176](0176-rc-process-and-tester-publish.md): candidate legs and tester publish.
- [docs/development/release.md](../development/release.md#patch-release-from-a-patch-branch): maintainer procedure.
- `actions/checkout` v7.0.1 README: `fetch-depth: 0` fetches all history for all branches and tags, so `origin/lts-*` exists in `validate`.
