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
- **Render**: PRs add fragments only; a render-only pull request lands per landing batch and at release cut (`scripts/release/concat-changelog-fragments.sh --write`). A render pull request carries `CHANGELOG.md` only (plus fragment deletions at a release cut; other paths fail with a remedy). Pull request CI fails when the base branch owes a changelog render for more than 20 h judged on the first parent of the merge commit (`refs/pull/<n>/merge`), with remedy "land the render PR first" ([ADR-0251](../docs/adr/0251-changelog-render-owed-on-pull-requests.md)). Push to the default branch runs the 24 h staleness check (`scripts/release/check-changelog-staleness.py`, [ADR-0245](../docs/adr/0245-changelog-render-at-landing.md)).
