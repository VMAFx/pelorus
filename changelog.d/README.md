<!-- markdownlint-disable MD013 -->
# changelog.d — Keep-a-Changelog fragments

`CHANGELOG.md`'s Unreleased block is **rendered, not edited** (mirrors vmafx's
ADR-0221). One fragment file per PR avoids the section-header merge conflict two
in-flight PRs would hit editing `CHANGELOG.md` directly.

- **Directories = Keep-a-Changelog sections** (the only valid ones): `added/`,
  `changed/`, `deprecated/`, `removed/`, `fixed/`, `security/`. Anything else is
  skipped by the renderer. Performance items live in `changed/` with a `perf-`
  filename prefix.
- **One file per PR**: `changelog.d/<section>/<num>-<topic>.md`, e.g.
  `added/0102-vf-pelorus-deband-vulkan.md`. The number prefix orders entries
  within a section.
- **Content** = the Markdown bullet(s) you'd paste into `CHANGELOG.md`, each
  referencing the governing ADR by number.
- **Render**: `scripts/release/concat-changelog-fragments.sh --write` (CI runs
  `--check`).
