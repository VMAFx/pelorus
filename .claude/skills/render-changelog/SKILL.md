---
name: render-changelog
description: Use when adding a changelog entry or before a release — render CHANGELOG.md's [Unreleased] block from the changelog.d/ fragments. Never hand-edit the block.
---

# /render-changelog

`CHANGELOG.md`'s `[Unreleased]` block (between the `BEGIN/END UNRELEASED`
markers) is generated from `changelog.d/<section>/*.md`. To add an entry, drop a
fragment, then render:

```sh
# 1. add a fragment (one per PR): changelog.d/<section>/<num>-<topic>.md
#    sections = added | changed | deprecated | removed | fixed | security
# 2. render
bash scripts/release/concat-changelog-fragments.sh --write
# 3. CI enforces it's up to date:
bash scripts/release/concat-changelog-fragments.sh --check
```

## Rules

- One fragment file per PR (avoids the CHANGELOG merge conflict two PRs would hit).
- Number-prefix the filename to order entries within a section.
- Reference the governing ADR by number in the bullet.
- Performance items live in `changed/` with a `perf-` filename prefix.
- See [changelog.d/README.md](../../../changelog.d/README.md) and ADR-0108.
