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
- **Render**: `scripts/release/concat-changelog-fragments.sh --write` (CI runs
  `--check`).
