<!-- markdownlint-disable MD013 -->
# ADR-0252: A scheduled workflow opens the changelog render pull request as a signed bot commit

- **Status**: Proposed
- **Implementation**: pending (#591)
- **Date**: 2026-11-10
- **Deciders**: Lusoris
- **Tags**: `release`, `docs`, `ci`
- **Related**: [ADR-0245](0245-changelog-render-at-landing.md) (extended), [ADR-0251](0251-changelog-render-owed-on-pull-requests.md) (extended)

## Context

[ADR-0245](0245-changelog-render-at-landing.md) renders `CHANGELOG.md` in a render-only pull request per landing batch. A push to `master` fails when the render is owed for more than 24 h. [ADR-0251](0251-changelog-render-owed-on-pull-requests.md) fails every other pull request when the render is owed for more than 20 h. Both checks only fail; someone still has to open and land the render pull request by hand. Twice in one week nobody did in time (`master` red on 2026-11-05 and 2026-11-09, runs listed in ADR-0251).

VMAFx/vmafx closed the same gap with its ADR-3373: an hourly workflow opens the render pull request as the `vmafx-release-bot` GitHub App and arms auto-merge. Its bot pull requests merge regularly there. That shape cannot be copied unchanged here, for these reasons (measured 2026-11-10):

- The `praetor-main-protection` ruleset on `master` requires signed commits. The vmafx ruleset does not, so vmafx pushes an unsigned commit with `git push`. GitHub documents that, with required signatures, unsigned commits on the head branch can block a squash merge even though GitHub signs the squash commit itself.
- GitHub verifies a bot's commit signature only when the commit is created through the API, authenticated as the App, with no custom author, committer or signature fields.
- The repository has no `render` target in `Makefile`; the render entry point is `bash scripts/release/concat-changelog-fragments.sh --write`. It rewrites only the generated `[Unreleased]` block of `CHANGELOG.md`.
- `actions/create-github-app-token` v3.3.0 (the release pinned by vmafx) deprecates the `app-id` input in favour of `client-id`.
- The repository has 0 Actions secrets and 0 Actions variables, and `allow_auto_merge` is off. The `vmafx-release-bot` installation (id 170254504) covers selected repositories with `contents: write`, `pull_requests: write` and `metadata: read`; whether it covers this repository cannot be read without an App token.
- `scripts/check-dco.py` accepts any commit whose message carries a valid `Signed-off-by:` trailer.
- A pull request opened with the workflow `GITHUB_TOKEN` starts no workflow runs, so it would never get its required checks. A pull request opened with a GitHub App installation token starts them.

## Decision

We will add `.github/workflows/changelog-render-pr.yml`. It runs hourly and on `workflow_dispatch`, only in `VMAFx/pelorus`, and opens a render-only pull request whose single commit the `vmafx-release-bot` App creates through the GitHub REST API, so GitHub signs and verifies it. The forge lands the pull request by squash through auto-merge. The workflow's only permission is `contents: read`, and its one job has exactly five steps: checkout, decide, settings check, token mint and opener.

1. **Decide.** The job checks out `master` with full history and `persist-credentials: false`, then runs `python3 -I scripts/release/check-changelog-staleness.py --open-after-hours 6 >> "$GITHUB_OUTPUT"`. This new mode reuses the checker's git helpers and measures the owed age the same way as the push check. It prints `current=`, `open=`, `owed_since=` and `owed_hours=` lines and exits 0. A render owed for at least 6 h, or a hand-edited generated block, gives `open=true`. A base that renders current gives `current=true`. 6 h leaves 14 h before the 20 h pull request check and 18 h before the 24 h push check. The output goes through a plain redirect, not `| tee -a` as in vmafx: a step without a `shell:` key runs under `set -e` without `pipefail` (GitHub workflow syntax, exit codes), so a pipe would hide a failing checker.
2. **Check settings.** Only when `open` or `current` is `true`, `python3 -I scripts/release/open-render-pr.py --check-settings` fails the run with an `::error::` line naming `RELEASE_BOT_CLIENT_ID` or `RELEASE_BOT_PRIVATE_KEY` when either is empty. The key reaches this step only as the boolean `secrets.RELEASE_BOT_PRIVATE_KEY != ''`. The mint and open steps then skip, because their `if:` names no status function and GitHub adds `success()`. A run that finds nothing due mints nothing and passes.
3. **Mint.** Only when `open` or `current` is `true`, `actions/create-github-app-token` (pinned by digest) mints an installation token with `client-id: ${{ vars.RELEASE_BOT_CLIENT_ID }}`, `private-key: ${{ secrets.RELEASE_BOT_PRIVATE_KEY }}` and exactly `permission-contents: write` and `permission-pull-requests: write`. The client ID is a public identifier, so it lives in a repository variable, not a secret.
4. **Open or close.** `python3 -I scripts/release/open-render-pr.py` acts on the decision:
   - `current=true`: close the App's own open render pull request, if any, with a comment, and delete its branch.
   - `open=true`: render with `concat-changelog-fragments.sh --write` and refuse any changed path other than `CHANGELOG.md`. A render that changes nothing while one is owed fails. The script then creates a blob, a tree on the `master` tree and a commit whose only parent is the checked-out `master` tip, with no author or committer fields, and a message that ends with the App's `Signed-off-by:` line. It reads the commit back and fails before moving any ref unless `verification.verified` is `true`. Only then does it point `chore/changelog-render-auto` at the commit, with `force`. When the branch head already has that parent and that tree, it skips the commit and only re-arms auto-merge. It finds or opens the pull request authored by the App on that branch; another author's pull request on that branch fails by name. It arms `gh pr merge --auto --squash` with the subject `docs(changelog): render unreleased fragments` and a body carrying the same `Signed-off-by:` line.

The bot's name and noreply address (`<user id>+<slug>[bot]@users.noreply.github.com`) are read from the forge at run time, not written into the script. The pull request passes the ADR-0251 scope rule because it carries `CHANGELOG.md` only and its merge tree renders current.

`scripts/check-build-config.py` refuses the workflow when it loses the schedule, the decision call, its 6 h limit or its plain redirect, the settings check, the pinned token step or its permission set, or the opener call, and when a step is added or dropped. Each of those mutations is a planted case in that checker's self-test. The staleness checker's self-test plants decision cases in a scratch repository: owed 7 h opens, owed exactly 6 h opens, owed 5 h does not, a current base reports `current=true`, a hand-edited block opens. The opener's self-test runs against a stub forge that records each call: fragments owed open a pull request with a verified commit and armed auto-merge; no fragments owed opens nothing and closes the App's own pull request; an unverified commit moves no ref; a stray path fails; an empty render fails; an unchanged branch only re-arms auto-merge; a foreign pull request on the branch fails.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Signed bot commit created through the REST Git Database API, landed by squash through auto-merge (chosen) | GitHub signs and verifies the commit, so `required_signatures` holds on the head branch too; one API family covers blob, tree, commit and ref; no key to store besides the App key | Builds the commit through API calls instead of `git push`; strict up-to-date checks re-render each time `master` moves | — |
| Squash-only landing of an unsigned `git push` commit (vmafx ADR-3373 shape; runner-up) | Same code as vmafx; the squash commit GitHub writes is signed | Unsigned head commits can block the squash merge under required signatures (GitHub docs) | The `praetor-main-protection` ruleset requires signatures; vmafx's ruleset does not |
| GraphQL `createCommitOnBranch` | Also signed by GitHub | Needs an existing branch and `expectedHeadOid`; resetting the branch onto a new `master` tip still needs a REST ref call | Two API families for one commit |
| Bot GPG or SSH signing key in a secret, then `git push` | Plain git flow | One more long-lived key to store and rotate; the forge already signs App API commits | Extra credential without a gain |
| Open the pull request with `GITHUB_TOKEN` | No App and no operator act | GitHub starts no workflow runs for it, so required checks never report | The pull request could never merge |
| Push the render commit straight to `master` | No pull request | Needs a bypass of the ruleset and a push token; ADR-0245 refused it | Breaks the landing contract |
| `app-id` input with an `RELEASE_BOT_APP_ID` secret (as vmafx) | Same settings as vmafx | `app-id` is deprecated at v3.3.0 and warns on every run (HISS-10); an App ID is not secret | Deprecated input |

## Consequences

- **Positive**: the render pull request appears 6 h after the first unrendered fragment and lands without a hand step while its checks pass. The 20 h and 24 h checks stay as the fallback.
- **Negative**: every render pull request runs the full required check set, and once more each time `master` moves before it lands (strict up-to-date checks). Copilot review on push can leave a thread on the bot's pull request. The ruleset requires thread resolution, so such a thread blocks auto-merge until a maintainer resolves it; the 20 h check then names the owed render. Until the operator acts (App access for this repository, `RELEASE_BOT_CLIENT_ID` variable, `RELEASE_BOT_PRIVATE_KEY` secret, `allow_auto_merge`), every scheduled run that needs a token fails by name.
- **Neutral / follow-ups**: vmafx still passes the deprecated `app-id` input (logged as a finding for that repository). This ADR changes no threshold of ADR-0245 or ADR-0251.

## References

- [ADR-0245](0245-changelog-render-at-landing.md), [ADR-0251](0251-changelog-render-owed-on-pull-requests.md); pull requests #518, #586, #588, #590.
- VMAFx/vmafx ADR-3373 (`docs/adr/3373-scheduled-render-pull-request.md`, read at `563964b29` on 2026-11-10), its workflow `.github/workflows/docs-render-pr.yml` and `scripts/ci/open-render-pr.sh`.
- GitHub docs: rulesets, "Require signed commits" (squash merges with unsigned head commits); commit signature verification for bots (REST API commits made as a GitHub App); workflow syntax, `jobs.<job_id>.steps[*].shell`, "Exit codes and error action preference" (read 2026-11-10: `set -e` by default, `-o pipefail` only with `shell: bash`).
- `actions/create-github-app-token` `action.yml` at `64ab729a6294000f5aa4a4bc0b73d1f32bc45510` (v3.3.0): `app-id` carries `deprecationMessage: "Use 'client-id' instead."`.
- Maintainer answer `pelorus-changelog-guard-shape` (2026-11-09, changelog render guard shape; paraphrased): ship the pull request check first, then port the scheduled bot render pull request.
- Maintainer order `pelorus-scheduled-render-pr-20261110` (2026-11-10, scheduled render pull request port; paraphrased): port the vmafx workflow, decide here between a signed bot commit and squash-only landing, plant the open and no-open cases and a mutant that drops the schedule.
