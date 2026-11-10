---
name: render-changelog
description: Use when landing a changelog render batch or before a release — render CHANGELOG.md's [Unreleased] block from changelog.d/ fragments in a render-only PR. Feature PRs carry fragments only.
---

# /render-changelog

`CHANGELOG.md`'s `[Unreleased]` block (between the `BEGIN/END UNRELEASED`
markers) is generated from `changelog.d/<section>/*.md`. Feature PRs add fragments
only without rendering. Rendering runs in a dedicated render-only PR per landing
batch and at release cut (ADR-0245):

```sh
# 1. Feature PRs add a fragment only: changelog.d/<section>/<num>-<topic>.md
#    sections = added | changed | deprecated | removed | fixed | security | maintenance
# 2. Render-only PR lands per landing batch and at release cut:
bash scripts/release/concat-changelog-fragments.sh --write
# 3. Commit the rendered CHANGELOG.md, then check staleness (the script validates HEAD):
python3 -I scripts/release/check-changelog-staleness.py
```

## Rules

- Feature PRs add fragments only; render in a render-only PR, not inside feature PRs (ADR-0245).
- Plain render PRs carry `CHANGELOG.md` only; release PRs carry `CHANGELOG.md`, optional fragment deletions, and the release files only (`VERSION_SITES` in `scripts/prepare-release.py`, compile-context projections, and `RELEASE_DOCS` in `scripts/release/check-changelog-staleness.py`); editing or adding other files fails the pull-request staleness check (ADR-0251).
- Pull request CI fails when the base branch owes a changelog render for more than 20 h judged on the merge commit's first parent, requiring authors to land a render PR first (ADR-0251).
- One fragment file per PR (avoids the CHANGELOG merge conflict two PRs would hit).
- Number-prefix the filename to order entries within a section.
- Reference the governing ADR by number in the bullet.
- Performance items live in `changed/` with a `perf-` filename prefix.
- See [changelog.d/README.md](../../../changelog.d/README.md), ADR-0108, ADR-0245, and ADR-0251.
