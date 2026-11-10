<!-- markdownlint-disable MD013 -->
# changelog.d — Keep-a-Changelog fragments

`CHANGELOG.md`'s Unreleased block is **rendered, not edited** (mirrors vmafx's
ADR-0221). One fragment file per PR avoids the section-header merge conflict two
in-flight PRs would hit editing `CHANGELOG.md` directly.

- **Directories = changelog sections** (the only valid ones): `added/`,
  `changed/`, `deprecated/`, `removed/`, `fixed/`, `security/`, and
  `maintenance/` (rendered last as `### Maintenance`). Anything else is skipped
  by the renderer. Performance items live in `changed/` with a `perf-` filename
  prefix; maintainer-internal items (HISS baseline updates, CI and tool pins,
  docs-gate and gate internals, governance re-pins) live in `maintenance/`.
- **Highlights**: `### Highlights` (3 to 6 user-facing bullets) is written by
  hand at the top of `## [X.Y.Z]` in the release commit, not via `changelog.d/`.
- **One file per PR**: `changelog.d/<section>/<num>-<topic>.md`, e.g.
  `added/0102-vf-pelorus-deband-vulkan.md`. The number prefix orders entries
  within a section.
- **Content** = the Markdown bullet(s) you'd paste into `CHANGELOG.md`, each
  referencing the governing ADR by number.
- **Render**: PRs add fragments only; a render-only pull request lands per landing batch and at release cut (`scripts/release/concat-changelog-fragments.sh --write`). The hourly workflow `.github/workflows/changelog-render-pr.yml` opens the render PR as the release-bot App after 6 h owed using a signed API commit, arms squash auto-merge, and closes its own PR when the base renders current ([ADR-0252](../docs/adr/0252-scheduled-changelog-render-pull-request.md)). When the workflow cannot open or land it, render by hand with the same command and land that pull request before the pull request check below fails every other pull request: when a render is due and a release-bot setting is missing, the workflow fails at its settings step by name; on a current base it prints a notice and passes ([ADR-0253](../docs/adr/0253-render-settings-step-fails-only-when-due.md)). A review thread on the bot pull request holds auto-merge. A plain render pull request carries `CHANGELOG.md` only; a release pull request carries `CHANGELOG.md`, optional fragment deletions, and the release files only (`VERSION_SITES` in `scripts/prepare-release.py`, compile-context projections, and `RELEASE_DOCS` in `scripts/release/check-changelog-staleness.py`; other paths fail with a remedy). Pull request CI fails when the base branch owes a changelog render for more than 20 h judged on the first parent of the merge commit (`refs/pull/<n>/merge`), with remedy "land the render PR first" ([ADR-0251](../docs/adr/0251-changelog-render-owed-on-pull-requests.md)). Two stale base states fail that check and the push check below at once, without waiting for their limit: the last `CHANGELOG.md` commit does not render current on its own tree (a hand edit), or no fragment commit landed since that commit (a render script change). The workflow opens the render pull request for both on its next hourly run; render by hand at once when it cannot open or land that pull request, or when a pull request cannot wait for the run. A render pull request whose merge tree renders current passes even while the base owes more than 20 h (the remedy itself is never blocked). Push to the default branch runs the 24 h staleness check (`scripts/release/check-changelog-staleness.py`, [ADR-0245](../docs/adr/0245-changelog-render-at-landing.md)).
