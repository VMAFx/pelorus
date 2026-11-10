<!-- markdownlint-disable MD013 -->
# ADR-0251: Pull requests fail while the default branch owes a changelog render for more than 20 h

- **Status**: Accepted
- **Date**: 2026-11-10
- **Deciders**: Lusoris
- **Tags**: `release`, `docs`, `ci`
- **Related**: [ADR-0245](0245-changelog-render-at-landing.md) (extended)

## Context

[ADR-0245](0245-changelog-render-at-landing.md) moved the changelog render from every pull request to a render-only pull request per landing batch. Only the default branch sees the debt: a push to `master` runs `scripts/release/check-changelog-staleness.py` with a 24 h limit, and the scheduled `changelog-staleness.yml` run (every 6 h) prints a `warning: render owed` line and stays green below 24 h. Pull request CI runs only the checker's self-test.

That left the owed render invisible until `master` was already red, twice:

- 2026-11-05 (UTC): pull request #540 passed every check and turned `master` red on its push run 42128311372 (render owed 26.1 h since `e9027a406c38`).
- 2026-11-09: `master` was red on two push runs in a row, 42611731897 (27.1 h) and 42628208691 (28.9 h), both owing the render since `f9779b431356`. The scheduled runs 42485108277, 42528716959 and 42574014153 had stayed green with a `warning: render owed` line; the last of them printed 21.7 h.

The render pull request that cleared it, #586 (`fc498c8`), also rewrote one line of the fragment `changelog.d/fixed/soak-showinfo-nostats.md`. ADR-0245 expects a render pull request to carry the render only; no check enforced that.

A pull request run checks out GitHub's merge commit `refs/pull/<n>/merge`, whose first parent is the base branch tip and whose committer time is the time GitHub built it. `git log --first-parent -- 'changelog.d/*/*.md'` lists that merge commit when it adds a fragment (git 2.56, measured in a scratch repository on 2026-11-10). So running the push-time check unchanged on the merge commit would date the pull request's own new fragment to the build time of the merge commit, and a rerun of an old run would report a fresh fragment as days old.

## Decision

We will run `scripts/release/check-changelog-staleness.py --pull-request --max-age-hours 20` in the `docs` job of `.github/workflows/ci.yml` on every pull request, on the checked-out merge commit. The `docs` job is a required check, so a failing step blocks the merge. In `--pull-request` mode the checker:

1. Fails when the checked-out ref is not a merge commit with exactly two parents, and names the ref. It does not fall back to the push-time check.
2. Checks the render scope. When `CHANGELOG.md` differs between the first parent and the merge commit, the pull request is a render pull request. A release cut is a render pull request that modifies `meson.build` (`("M", "meson.build")`). A release cut may only modify release files (the version sites `meson.build`, `libpelorus/include/pelorus/pelorus.h`, `README.md`, `AGENTS.md` declared in `scripts/prepare-release.py`, the compile-context projections, `docs/roadmap.md`, `docs/rebase-notes.md`, and `CHANGELOG.md`) and delete fragments (fragment deletions are optional). A fragment is `changelog.d/<section>/<name>.md` with `<section>` one of the `SECTIONS` that `scripts/release/concat-changelog-fragments.sh` renders; the checker reads that list from the renderer and fails closed when it cannot read exactly one list. Any other deletion, such as the `changelog.d/` README, a file in a non-section directory, a nested path or a non-Markdown file, fails by name. A plain render pull request carries `CHANGELOG.md` only; any other path, including fragment deletions under `changelog.d/`, fails by name with the remedy: land fragment and code changes in their own pull request first.
3. A render pull request passes when its merge tree renders current (`scripts/release/concat-changelog-fragments.sh --check` exits 0), else fails with the remedy: merge the default branch and render again.
4. A non-render pull request is judged on the first parent whatever its merge tree, so deleting the owed fragments does not pass it. A base that renders current passes: `changelog render current` when the merge tree renders current too (a code-only pull request), else a warning that only this pull request's fragments are owed; exit 0 either way. A base that owes a render for at most 20 h: warning, exit 0. A base that owes a render for more than 20 h: exit 1 with the remedy "land the render PR first". A hand-edited `CHANGELOG.md` on the base fails as on a push.

The 20 h limit leaves 4 h between the first red pull request and the 24 h push limit of ADR-0245, which stays unchanged. `scripts/check-build-config.py` refuses `ci.yml` when the `docs` job loses that step, matched by the step's own `name:` key: when the step is removed, its `if:` stops being exactly `github.event_name == 'pull_request'`, or its `run:` key is not exactly that one command (drops `--pull-request`, changes the 20 h limit, or wraps it with `exit 0`, a heredoc, or `if false`). The checker also reads three key allow-lists, so no override can make the step pass without running: the step sets only `name`, `if` and `run` (no `continue-on-error:`, `shell:`, `working-directory:` or `env:`), the `docs` job sets only `name`, `runs-on`, `timeout-minutes` and `steps`, and `ci.yml` sets only the top-level keys `name`, `on`, `permissions`, `concurrency` and `jobs` (no `defaults:` or `env:`). Repeated keys are refused in all three allow-lists, and quoted keys and `<<:` merge keys count as keys outside the list. Every workflow's job keys must be plain `<name>:` lines, each set once, because the job block reader keeps only the last block per name and does not start a block at any other key shape. The job key reader keeps scanning past column-0 comments and bounds the last job at the end of the jobs map. Each of those mutations is a planted case in that checker's self-test. The staleness checker's self-test plants the pull request cases in a scratch repository with merge commits: base owing 21 h fails, 19 h warns and passes, a render pull request passes while its base owes 21 h, a rerun of a 48 h old merge commit over a current base passes, a non-render pull request that deletes the owed fragments fails at 21 h and warns at 19 h, a code-only pull request over a current base passes, a fragment edit, a fragment deletion or an extra file inside a plain render pull request fails, a release cut passes with or without fragment deletions, a release cut that adds or modifies a code file or deletes a release file fails, a release cut deleting anything but a fragment fails (the `changelog.d/` README, a non-section directory, a nested path, a non-Markdown file, a section-named directory outside `changelog.d/`), a deletion in the last section passes, the section list loader fails closed, a root commit or a one-parent ref fails, and the release-file lists match the files the repository tracks.

This ADR also records that ADR-0245 is implemented: its pull request #518 merged on 2026-11-04, so its status changes to Accepted.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Pull request check on the merge commit, judged on its first parent (chosen) | Debt shows on every pull request 4 h before `master` turns red; the render pull request itself passes; reruns of old merge commits judge the base, not the build time of the merge commit | Every non-render pull request is blocked while the base owes more than 20 h | — |
| Scheduled render pull request opened by a bot (VMAFx/vmafx ADR-3373 shape: hourly workflow, release-bot App, auto-merge) | Renders without anyone landing a pull request by hand | Needs operator acts first: App access for this repository, `RELEASE_BOT_*` secrets (the repository has 0 Actions secrets today), `allow_auto_merge`; a pull request opened with `GITHUB_TOKEN` gets no checks | Follows as its own unit after this one lands; the two do not conflict |
| Run the push-time check on `origin/master` in pull request CI | No new checker mode | The render pull request itself fails while the base owes the render, so the debt cannot be paid | Blocks the remedy |
| Run the push-time check unchanged on the merge commit | No new checker mode | Dates the pull request's own fragment to the build time of the merge commit, so a rerun of an old run fails a fresh fragment (git behaviour measured above) | False failures on reruns |
| Warn-only pull request step | Never blocks | The scheduled job already warned for days and nobody read it | Invisible, as measured on 2026-11-09 |
| Lower the push limit on `master` (say 20 h) | One number to change | `master` still turns red first; pull request authors still see nothing | Moves the red, does not prevent it |
| Separate script for the render scope rule | One concern per script | Duplicates the git helpers of the staleness checker (HISS-19) | Same checker, same step, one implementation of the git calls |

## Consequences

- **Positive**: an owed render older than 20 h fails every pull request with a remedy before `master` turns red at 24 h. A render pull request that edits a fragment or carries other changes fails by name. ADR-0245's status matches the code.
- **Negative**: while the base owes a render for more than 20 h, no pull request other than the render pull request can merge, Renovate pull requests included. Each pull request run of the `docs` job extracts up to two trees more (merge commit and first parent).
- **Neutral / follow-ups**: the scheduled bot render pull request (row `pelorus-changelog-render-pr-port` in the maintainer ledger) follows as its own unit with its own ADR. `scripts/adr/check-status.py` passed ADR-0245 as Proposed for five days after #518 merged; it reads no forge state, so a merged implementation pull request is not detected (named in the pull request of this ADR as a known gap).

## References

- [ADR-0245](0245-changelog-render-at-landing.md); pull requests #518, #540, #586, #588.
- CI runs 42128311372, 42611731897, 42628208691 (red on `master`); scheduled runs 42485108277, 42528716959, 42574014153 (green while the render was owed).
- VMAFx/vmafx ADR-3373 (scheduled render pull request).
- Maintainer answer `pelorus-changelog-guard-shape` (2026-11-09, changelog render guard shape; paraphrased): ship the pull request check on the merge commit with a 20 h limit, the remedy text "land the render PR first", planted 21 h, 19 h and render pull request cases and a guarded CI step now; port the scheduled render pull request afterwards.
- Review finding on #586 (2026-11-09, render pull request scope; paraphrased): a render pull request rewrote a fragment.
