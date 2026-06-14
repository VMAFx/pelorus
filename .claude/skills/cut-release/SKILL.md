---
name: cut-release
description: Use when cutting a Pelorus release — verify the gate, bump the version, render the changelog, then push a SemVer tag which triggers the release workflow (build + GitHub release + patch tarball).
---

# /cut-release

Releases are **tag-triggered**: pushing `vX.Y.Z` runs `.github/workflows/release.yml`,
which gates on build+tests, extracts notes from the `[Unreleased]` changelog
block, packages the FFmpeg patch stack, and publishes the GitHub release.

## Steps

1. **Gate green**: `meson test -C build --suite=fast` and `/lint-all`.
2. **Bump the version** to `X.Y.Z` in:
   - `libpelorus/include/pelorus/pelorus.h` (`PELORUS_VERSION_*` + `_STR`)
   - `meson.build` (`version:`)
   (If the interop ABI changed this cycle, confirm `PELORUS_ABI_MINOR` was
   bumped too — see `/bump-abi`.)
3. **Render the changelog**: `/render-changelog` (`--write`), and confirm the
   `[Unreleased]` block reads as the release notes you want.
4. **Commit** the version bump (`chore(release): vX.Y.Z`), open a PR, merge to
   `master`.
5. **Tag + push**:

   ```sh
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

6. Watch `gh run watch` for the release job; verify `gh release view vX.Y.Z`.

## Rules

- SemVer. The interop ABI is append-only — a release that changes it bumps the
  minor and ships the conformance-fixture update (ADR-0103, ADR-0109).
- Never tag a red `master`. Never force-push a tag.
