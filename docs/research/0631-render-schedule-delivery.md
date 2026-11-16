<!-- markdownlint-disable MD013 -->
# Scheduled changelog render delivery

**Date:** 2026-11-16

**Decision:** [ADR-0259](../adr/0259-render-schedule-best-effort.md).

**Scope:** scheduled workflow delivery across `.github/workflows/changelog-render-pr.yml` (`41 * * * *`), `.github/workflows/changelog-staleness.yml` (`17 */6 * * *`), and `.github/workflows/tester-publish.yml` (`17 2 * * *`), replay against `decide_render_pr` in `scripts/release/check-changelog-staleness.py` over `2026-11-10T08:15:00Z`..`2026-11-16T09:00:00Z` (`origin/master` at `350bbd2`), bot pull requests #595, #605, #613, #625, #627, and #629, and issue #631.

## Verdict

`.github/workflows/changelog-render-pr.yml` has no schedule or decision defect: over `2026-11-10T08:15:00Z`..`2026-11-16T09:00:00Z` the workflow remained active, `0` scheduled runs across all three repository schedules concluded `cancelled`, and replaying `decide_render_pr(repo, full_sha, 6, now=created_at)` matched the `mint` step execution on `23` of `24` runs (the single mismatch, run `42856098380`, failed `--check-settings` before repository `RELEASE_BOT_*` settings existed). Sparse execution (`22` of `145` hourly slots started, `15.2%`, inter-run gaps `4.10`–`8.92` h, median `6.94` h) is GitHub Actions best-effort schedule delivery, corroborated by `changelog-staleness.yml` (`13` of `24` six-hourly slots started, `54.2%`, max gap `13.18` h) and `tester-publish.yml` (`6` runs delayed `407.4`–`461.9` min past `02:17` UTC). Because `decide_render_pr` judges owed render age from git history rather than counting cron windows, dropped slots delay pull request creation to `6 h + gap` to the next run that passes the settings check and opens the pull request (`14.92` h worst case in this sample at the `8.92` h max gap, leaving `5.08` h margin to ADR-0251's `20` h pull request check and `9.08` h to ADR-0245's `24` h push check; `15.29` h with margins of `4.71` h and `8.71` h once the `21.97` min maximum runner queue wait is added; GitHub sets no upper limit on schedule delay, so a longer gap can exceed these figures). Every episode that exceeded `20` h (#605 at `27.0` h, #613 at `25.0` h) opened at `<= 14.0` h owed and was held in merge wait after `plurano-merge-train` disabled auto-merge (`manually_disabled`); once auto-merge stayed untouched (#625, #627, #629), `vmafx-release-bot` merged each pull request within `0.41`–`0.57` h (`6.9`–`9.0` h owed).

## Measurements

### Table A: Render workflow runs (`2026-11-10`..`2026-11-16`)

Lateness is a lower bound (`created_at - preceding :41 slot`), because a delayed run may originate from an earlier dropped slot. `Match` tests `(replay open || replay current) == (mint == "success")`.

| Run ID | Event | Created (UTC) | Preceding `:41` slot | Lateness >= (min) | Gap (h) | Runner wait (min) | Head SHA | Replay (`cur`/`open`/`owed_since`/`owed_h`) | Mint step | Match |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `42856098380` | `schedule` | `2026-11-10T16:35:46Z` | `2026-11-10T15:41:00Z` | 54.8 | — | 0.05 | `1f434b47c4d6` | `false` / `true` / `38861a659e50` / `11.9` | `skipped` (job `failure`) | `MISMATCH` |
| `42873719398` | `workflow_dispatch` | `2026-11-10T18:17:40Z` | `—` | — | — | 0.07 | `1f434b47c4d6` | `false` / `true` / `38861a659e50` / `13.6` | `success` | `true` |
| `42909789699` | `schedule` | `2026-11-10T21:56:09Z` | `2026-11-10T21:41:00Z` | 15.2 | 5.34 | 2.48 | `312ee3e806ae` | `true` / `false` / `—` / `—` | `success` | `true` |
| `42944469191` | `schedule` | `2026-11-11T02:13:04Z` | `2026-11-11T01:41:00Z` | 32.1 | 4.28 | 0.03 | `312ee3e806ae` | `true` / `false` / `—` / `—` | `success` | `true` |
| `42998322616` | `schedule` | `2026-11-11T09:48:53Z` | `2026-11-11T09:41:00Z` | 7.9 | 7.60 | 0.03 | `312ee3e806ae` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43073833894` | `schedule` | `2026-11-11T18:01:57Z` | `2026-11-11T17:41:00Z` | 20.9 | 8.22 | 0.17 | `86c592f34a77` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43131170364` | `schedule` | `2026-11-12T00:58:19Z` | `2026-11-12T00:41:00Z` | 17.3 | 6.94 | 0.03 | `86c592f34a77` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43185965423` | `schedule` | `2026-11-12T08:48:23Z` | `2026-11-12T08:41:00Z` | 7.4 | 7.83 | 0.03 | `5f2addedaa44` | `false` / `false` / `832b020a4a35` / `5.1` | `skipped` | `true` |
| `43272459556` | `schedule` | `2026-11-12T17:43:38Z` | `2026-11-12T17:41:00Z` | 2.6 | 8.92 | 0.03 | `6aa58b2a6ce5` | `false` / `true` / `832b020a4a35` / `14.0` | `success` | `true` |
| `43332274952` | `schedule` | `2026-11-12T23:51:02Z` | `2026-11-12T23:41:00Z` | 10.0 | 6.12 | 0.05 | `72f669ab5868` | `false` / `true` / `832b020a4a35` / `20.2` | `success` | `true` |
| `43374734216` | `workflow_dispatch` | `2026-11-13T05:51:24Z` | `—` | — | — | 0.05 | `7b7a7955cb4b` | `false` / `true` / `832b020a4a35` / `26.2` | `success` | `true` |
| `43384233642` | `schedule` | `2026-11-13T07:13:48Z` | `2026-11-13T06:41:00Z` | 32.8 | 7.38 | 0.05 | `2f31ab4c2a5c` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43454983985` | `schedule` | `2026-11-13T15:40:35Z` | `2026-11-13T14:41:00Z` | 59.6 | 8.45 | 1.12 | `6375f1a5cc5d` | `false` / `true` / `fbb94fc97747` / `7.7` | `success` | `true` |
| `43512346646` | `schedule` | `2026-11-13T21:37:30Z` | `2026-11-13T20:41:00Z` | 56.5 | 5.95 | 0.03 | `556b45f601de` | `false` / `true` / `fbb94fc97747` / `13.6` | `success` | `true` |
| `43542298866` | `schedule` | `2026-11-14T01:43:36Z` | `2026-11-14T01:41:00Z` | 2.6 | 4.10 | 0.03 | `4e98ae9709ce` | `false` / `true` / `fbb94fc97747` / `17.7` | `success` | `true` |
| `43580273296` | `schedule` | `2026-11-14T09:22:46Z` | `2026-11-14T08:41:00Z` | 41.8 | 7.65 | 0.03 | `7b3c1684d90c` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43613564039` | `schedule` | `2026-11-14T16:04:12Z` | `2026-11-14T15:41:00Z` | 23.2 | 6.69 | 0.03 | `7b3c1684d90c` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43639952461` | `schedule` | `2026-11-14T20:54:26Z` | `2026-11-14T20:41:00Z` | 13.4 | 4.84 | 0.07 | `881ec9b33331` | `false` / `false` / `362d1ef37680` / `4.2` | `skipped` | `true` |
| `43662311434` | `schedule` | `2026-11-15T01:16:32Z` | `2026-11-15T00:41:00Z` | 35.5 | 4.37 | 0.03 | `89d7a287f36b` | `false` / `true` / `362d1ef37680` / `8.6` | `success` | `true` |
| `43694390810` | `schedule` | `2026-11-15T08:24:56Z` | `2026-11-15T07:41:00Z` | 43.9 | 7.14 | 0.03 | `0a2d5de51737` | `true` / `false` / `—` / `—` | `success` | `true` |
| `43732159410` | `schedule` | `2026-11-15T15:51:01Z` | `2026-11-15T15:41:00Z` | 10.0 | 7.43 | 0.05 | `e202811270a4` | `false` / `true` / `e202811270a4` / `6.4` | `success` | `true` |
| `43762437368` | `schedule` | `2026-11-15T21:12:57Z` | `2026-11-15T20:41:00Z` | 31.9 | 5.37 | 1.83 | `452474219539` | `false` / `false` / `3c7da78c7ecb` / `3.9` | `skipped` | `true` |
| `43785808315` | `schedule` | `2026-11-16T01:21:37Z` | `2026-11-16T00:41:00Z` | 40.6 | 4.14 | 21.97 | `452474219539` | `false` / `true` / `3c7da78c7ecb` / `8.0` | `success` | `true` |
| `43832116457` | `schedule` | `2026-11-16T08:32:57Z` | `2026-11-16T07:41:00Z` | 52.0 | 7.19 | 2.87 | `350bbd285e9b` | `true` / `false` / `—` / `—` | `success` | `true` |

### Table B: Owed episodes and merge-wait attribution (`2026-11-10T08:41Z`..`2026-11-16T08:41Z`)

Counterfactual hourly `:41` tips use `git rev-list -1 --first-parent --before=<slot ISO> origin/master` (approximating branch state by first-parent git committer timestamp). `Schedule delay` runs from the first `:41` slot with `open=true` to the start of the first delivered `open=true` run; it covers dropped and late slots alike, because a run does not record which slot it came from.

| `owed_since` | First fragment (`%ct` UTC) | Due (+6 h) | First `:41` `open=true` slot | First delivered `open=true` run | Bot PR (open / owed h -> merged / owed h) | Schedule delay (h) | Auto-merge timeline & merge actor |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `38861a659e50` | `2026-11-10T04:44:20Z` | `2026-11-10T10:44:20Z` | `2026-11-10T11:41:00Z` | `42856098380` (`16:35:46Z`, `11.9` h; failed settings check) | #595 via `42873719398` (`18:17:59Z` `13.6` h -> `18:50:59Z` `14.1` h) | 4.91 | 1 arm; disabled `+11.1` min by `plurano-merge-train` (`manually_disabled`); merged by `lusoris` |
| `832b020a4a35` | `2026-11-12T03:41:52Z` | `2026-11-12T09:41:52Z` | `2026-11-12T10:41:00Z` | `43272459556` (`17:43:38Z`, `14.0` h) | #605 (`17:43:53Z` `14.0` h -> `2026-11-13T06:41:40Z` `27.0` h) | 7.04 | 3 arms; disabled `+3.2`, `+43.3`, `+30.9` min by `plurano-merge-train` (`manually_disabled`); merged by `lusoris` (`12.96` h merge wait; `ci.yml` push run `43371294722` failed step `Changelog render owed at most 24 h` at `25.6` h owed) |
| `fbb94fc97747` | `2026-11-13T07:58:51Z` | `2026-11-13T13:58:51Z` | `2026-11-13T14:41:00Z` | `43454983985` (`15:40:35Z`, `7.7` h) | #613 (`15:41:54Z` `7.7` h -> `2026-11-14T09:00:49Z` `25.0` h) | 0.99 | 3 arms; disabled `+4.9`, `+9.0`, `+2.5` min by `plurano-merge-train` (`manually_disabled`); merged by `lusoris` (`17.32` h merge wait) |
| `362d1ef37680` | `2026-11-14T16:41:24Z` | `2026-11-14T22:41:24Z` | `2026-11-14T23:41:00Z` | `43662311434` (`01:16:32Z`, `8.6` h) | #625 (`01:16:46Z` `8.6` h -> `01:41:26Z` `9.0` h) | 1.59 | 1 arm; 0 disables; merged by `vmafx-release-bot` (`0.41` h) |
| `e202811270a4` | `2026-11-15T09:29:36Z` | `2026-11-15T15:29:36Z` | `2026-11-15T15:41:00Z` | `43732159410` (`15:51:01Z`, `6.4` h) | #627 (`15:51:24Z` `6.4` h -> `16:21:33Z` `6.9` h) | 0.17 | 1 arm; 0 disables; merged by `vmafx-release-bot` (`0.50` h) |
| `3c7da78c7ecb` | `2026-11-15T17:19:58Z` | `2026-11-15T23:19:58Z` | `2026-11-15T23:41:00Z` | `43785808315` (`01:21:37Z`, `8.0` h; `21.97` min runner wait) | #629 (`01:43:49Z` `8.4` h -> `02:18:09Z` `9.0` h) | 1.68 | 1 arm; 0 disables; merged by `vmafx-release-bot` (`0.57` h) |

### Table C: Scheduled workflows (`2026-11-10T08:15:00Z`..`2026-11-16T09:00:00Z`)

| Workflow | Cron (UTC) | Slots in window | Schedule runs started | Started / slots | Lateness >= min / median / max (min) | Max inter-run gap (h) |
| --- | --- | --- | --- | --- | --- | --- |
| `changelog-render-pr.yml` | `41 * * * *` | 145 | 22 | 22/145 (15.2%) | 2.6 / 27.6 / 59.6 | 8.92 |
| `changelog-staleness.yml` | `17 */6 * * *` | 24 | 13 | 13/24 (54.2%) | 10.2 / 57.5 / 349.0 | 13.18 |
| `tester-publish.yml` | `17 2 * * *` | 6 | 6 | 6/6 (100.0%) | 407.4 / 441.4 / 461.9 | 24.51 |

`Started / slots` counts schedule runs created inside the window against cron slots inside the window. For `tester-publish.yml` the two counts match only by offset: the `6` runs inside `2026-11-10T08:15:00Z`..`2026-11-16T09:00:00Z` include `42795224086` (`2026-11-10T09:58:55Z`, `461.9` min after `2026-11-10T02:17:00Z`), while the `2026-11-16T02:17:00Z` slot started after `09:00:00Z` as `43843803978` (`2026-11-16T09:53:14Z`, `456.2` min late); per slot, each of the seven daily slots from `2026-11-10` to `2026-11-16` started exactly once. Initial wait from `changelog-render-pr.yml` landing (`2026-11-10T08:15:12Z`) to first scheduled run (`42856098380`) was `8.34` h; the sample's worst-case scheduled render PR open time (`6 h + 8.92 h`) is `14.92` h (`5.08` h below `20` h, `9.08` h below `24` h) before runner queue wait, and `15.29` h (`4.71` h and `8.71` h) with the `21.97` min maximum wait.

Run IDs outside Table A: `changelog-staleness.yml` scheduled runs `42888449334`, `42972524342`, `43089005259`, `43173794103`, `43295265244`, `43381352600`, `43495001075`, `43566450248`, `43624684313`, `43648145732`, `43686253314`, `43749262833`, `43817625099`; `tester-publish.yml` scheduled runs `42795224086`, `42996170354`, `43195793776`, `43403411161`, `43578785273`, `43699969502`, `43843803978`; `ci.yml` push run `43371294722` (step `Changelog render owed at most 24 h`, failure).

## Reproduce

1. Query workflow runs, render job step conclusions, bot pull requests, and auto-merge timeline events:

   ```bash
   gh api --paginate 'repos/VMAFx/pelorus/actions/workflows/changelog-render-pr.yml/runs?per_page=100'
   gh api --paginate 'repos/VMAFx/pelorus/actions/workflows/changelog-staleness.yml/runs?per_page=100'
   gh api --paginate 'repos/VMAFx/pelorus/actions/workflows/tester-publish.yml/runs?per_page=100'
   gh api 'repos/VMAFx/pelorus/actions/runs/<run_id>/jobs'
   gh pr list --repo VMAFx/pelorus --search 'head:chore/changelog-render-auto' --state all \
     --json number,author,createdAt,mergedAt,closedAt,state,headRefName
   gh api graphql -F owner=VMAFx -F name=pelorus -F pr=<pr_number> -f query='
     query($owner:String!, $name:String!, $pr:Int!) {
       repository(owner:$owner, name:$name) {
         pullRequest(number:$pr) {
           timelineItems(first:100, itemTypes:[AUTO_SQUASH_ENABLED_EVENT, AUTO_MERGE_DISABLED_EVENT, HEAD_REF_FORCE_PUSHED_EVENT, MERGED_EVENT]) {
             nodes { __typename ... on AutoSquashEnabledEvent { createdAt actor { login } } ... on AutoMergeDisabledEvent { createdAt actor { login } reasonCode } ... on HeadRefForcePushedEvent { createdAt actor { login } } ... on MergedEvent { createdAt actor { login } } }
           }
         }
       }
     }'
   ```

2. Resolve each run's `head_sha` with `git rev-parse --verify <sha>^{commit}` and replay the decision function from `scripts/release/check-changelog-staleness.py` (`decide_render_pr(repo, full_sha, 6, now=run_created_epoch)`). For counterfactual hourly slots (`:41` UTC), resolve `origin/master` at each slot with `git rev-list -1 --first-parent --before=<slot_iso> origin/master` and evaluate `decide_render_pr` with `now=slot_epoch`.

3. Steps 1 and 2 need an authenticated `gh` session and a fetched clone. The replay was a one-off analysis: its script and output are kept as review evidence outside the repository, and Tables A and B are its committed output.

## Sources

- [ADR-0259](../adr/0259-render-schedule-best-effort.md) (Keep the hourly changelog render schedule under best-effort platform delivery).
- [ADR-0252](../adr/0252-scheduled-changelog-render-pull-request.md), [ADR-0251](../adr/0251-changelog-render-owed-on-pull-requests.md), [ADR-0245](../adr/0245-changelog-render-at-landing.md).
- [GitHub Docs: Events that trigger workflows — `schedule`](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule) (read 2026-11-16).
- Issue #631; pull requests #595, #605, #613, #625, #627, #629.
