<!-- markdownlint-disable MD013 -->
# Scheduled changelog render pull request

**Date:** 2026-11-10

**Decision:** [ADR-0252](../adr/0252-scheduled-changelog-render-pull-request.md).

**Scope:** scheduled changelog render pull request workflow `.github/workflows/changelog-render-pr.yml`, `scripts/release/open-render-pr.py`, `scripts/release/check-changelog-staleness.py`, and `scripts/check-build-config.py`, measured on `c219f15`, issue #591.

## Verdict

The scheduled changelog render workflow (`.github/workflows/changelog-render-pr.yml`) runs hourly and on `workflow_dispatch` to resolve changelog render debt without manual intervention. It invokes `check-changelog-staleness.py --open-after-hours 6` to determine whether a render is owed. When due, `actions/create-github-app-token` mints an installation token for `vmafx-release-bot`, and `open-render-pr.py` constructs a render-only commit via the GitHub REST Git Database API (without author, committer, or signature keys) so GitHub cryptographically signs and verifies it. The script arms squash auto-merge on the pull request and verifies DCO trailers via `scripts/check-dco.py`. When the base branch renders current, the workflow automatically closes any open bot render pull request and deletes its branch. The workflow and its contract are guarded by 16 planted mutants in `scripts/check-build-config.py`.

## Measurements

| Probe | Result |
| --- | --- |
| Render entry point | `bash scripts/release/concat-changelog-fragments.sh --write`; rewrites only lines between `BEGIN/END UNRELEASED` markers in `CHANGELOG.md`; no Makefile render target |
| Baseline staleness checker | `scripts/release/check-changelog-staleness.py` (1709 lines on `c219f15`); `is_tree_current` :219, `check_changelog_base` :230, `judge_owed_render` :257, `SUBPROCESS_TIMEOUT_S = 120` |
| DCO trailer contract | `scripts/check-dco.py`; `DCO_TRAILER` regex `^Signed-off-by: [^<>]+ <[^<>\s]+@[^<>\s]+>$`, `has_dco_trailer(body)` :105 |
| App token action pin | `actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3.3.0`; inputs `client-id`, `private-key`, `permission-<name>` |
| Checkout action pin | `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1` |
| Bot identity | Slug `vmafx-release-bot`, login `vmafx-release-bot[bot]`; user ID dynamically queried via `gh api users/vmafx-release-bot%5Bbot%5D --jq .id`; noreply `<id>+vmafx-release-bot[bot]@users.noreply.github.com` |
| REST commit verification | GitHub verifies App API commits when author, committer, and signature keys are omitted; response payload checked for `verification.verified is True` |
| Self-test d1: owed 7 h | exit 0; stdout `current=false open=true owed_hours=7.0` |
| Self-test d2: owed exactly 6 h (commit at now - 6*3600) | exit 0; stdout `current=false open=true owed_hours=6.0` |
| Self-test d3: owed 5.9 h | exit 0; stdout `current=false open=false owed_hours=5.9` |
| Self-test d4: base renders current | exit 0; stdout `current=true open=false empty owed_*` |
| Self-test d5: hand-edited UNRELEASED block | exit 0; stdout `current=false open=true owed_hours=0.0` |
| Self-test d6: `--open-after-hours 0` | exit 2 (argparse validation rejects `< 1`) |
| Self-test s1: release-bot settings present | exit 0; `CLIENT_ID` set, `HAS_KEY=true` |
| Self-test s2: release-bot missing client id | exit 1; reports missing `RELEASE_BOT_CLIENT_ID` |
| Self-test s3: release-bot key flag false | exit 1; reports missing `RELEASE_BOT_PRIVATE_KEY` |
| Self-test o1: fragments owed, no branch, no PR | exit 0; creates blob, tree, verified API commit (`message`, `tree`, `parents` only), refs POST, pulls POST, arms squash auto-merge with DCO trailer |
| Self-test o2: base current (`--due false`), own PR open | exit 0; calls `pr close <n> --comment ... --delete-branch`, zero forge write calls |
| Self-test o3: base current, no PR | exit 0; stdout `no render pull request to close`, zero forge write calls |
| Self-test o4: branch head current (parent==tip, tree==new tree) | exit 0; skips commit and ref (`render branch already current`), re-arms auto-merge |
| Self-test o5: unverified API commit (`verification.verified: false`) | exit 1; ref and pull request creation skipped |
| Self-test o6: render touches stray path | exit 1 naming stray path; zero forge writes |
| Self-test o7: due but render changes nothing | exit 1 (`render owed but the render changes nothing`); zero forge writes |
| Self-test o8: foreign PR on render branch | exit 1 naming author and PR number; zero forge writes |
| Self-test o9: branch validation | exit 1 for default branch match; exit 2 for non-`chore/` branch prefix |
| Self-test o10: blob sha mismatch vs `git hash-object` | exit 1; tree creation skipped |
| Self-test o11: existing branch with stale parent | exit 0; creates commit and updates ref with `PATCH git/refs/heads/<branch>` with `force: true` |
| Build config regression R1: schedule block removed | rejected with `must define a schedule trigger` |
| Build config regression R2: daily cron `'41 3 * * *'` | rejected with `schedule cron must be hourly` |
| Build config regression R3: workflow_dispatch removed | rejected with `must include workflow_dispatch trigger` |
| Build config regression R4: decide step removed | rejected with `missing decide step` |
| Build config regression R5: `--open-after-hours 24` | rejected with `exact decide command` |
| Build config regression R6: `\|\| true` appended to decide | rejected with `decide step must not contain pipe` |
| Build config regression R6b: decide piped through `\| tee -a` | rejected with `decide step must not contain pipe` |
| Build config regression R7: `permission-administration: write` added | rejected with `mint step with keys must be exactly` |
| Build config regression R8: `app-id:` in place of `client-id:` | rejected with `mint step must not set app-id` |
| Build config regression R9: opener step removed | rejected with `missing opener step` |
| Build config regression R10: `continue-on-error: true` on opener | rejected with `step must not set continue-on-error` |
| Build config regression R11: job `if:` dropped | rejected with `render job must set if: github.repository == 'VMAFx/pelorus'` |
| Build config regression R12: `permissions: write-all` | rejected with `top-level permissions must be exactly contents: read` |
| Build config regression R13: mint step `if:` changed to `always()` | rejected with `mint step must carry the exact if: condition` |
| Build config regression R14: opener step moved before mint | rejected with `steps must be in order checkout < decide < settings < mint < opener` |
| Build config regression R15: workflow file missing | rejected with `missing` |

## Sources

- [ADR-0252](../adr/0252-scheduled-changelog-render-pull-request.md) (A scheduled workflow opens the changelog render pull request as a signed bot commit).
- [ADR-0251](../adr/0251-changelog-render-owed-on-pull-requests.md) (Pull requests fail while the default branch owes a changelog render for more than 20 h).
- [ADR-0245](../adr/0245-changelog-render-at-landing.md) (Changelog render moves to landing and release cut).
- Issue #591.
