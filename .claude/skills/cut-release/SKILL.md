---
name: cut-release
description: Use when cutting a Pelorus release — verify the gate, bump the version, render the changelog, then push a SemVer tag which triggers the release workflow (build + GitHub release + patch tarball).
---

# /cut-release

Releases are **tag-triggered**: pushing `vX.Y.Z` (or a candidate `vX.Y.Z-rc.N`,
see below) runs `.github/workflows/release.yml`,
which first runs the full CI workflow, then calls `.github/workflows/release-build.yml`.
That reusable workflow asserts the tag equals the `meson.build` version, gates on
build+tests, extracts notes from the `## [X.Y.Z]` changelog section (fails if missing or empty), packages the
FFmpeg patch stack, writes an SPDX SBOM and `SHA256SUMS`, attests SLSA build
provenance, and signs `SHA256SUMS` with cosign (ADR-0169). The `publish` job then
creates the GitHub release with five assets: `pelorus-ffmpeg-patches-vX.Y.Z.tar.gz`,
`.spdx.json`, `.provenance.sigstore.json`, `SHA256SUMS`, `SHA256SUMS.sigstore.json`.

## Steps

1. **Gate green**: `meson test -C build --suite=fast` and `/lint-all`.
2. **Bump the version** to `X.Y.Z` in:
   - `libpelorus/include/pelorus/pelorus.h` (`PELORUS_VERSION_*` + `_STR`)
   - `meson.build` (`version:`)
   (If the interop ABI changed this cycle, confirm `PELORUS_ABI_MINOR` was
   bumped too — see `/bump-abi`.)
3. **Render and rotate the changelog**: `/render-changelog` (`--write`), then
   rotate `[Unreleased]` into a `## [X.Y.Z] - YYYY-MM-DD` section, delete the
   consumed `changelog.d/` fragments, and re-run `--write` so `[Unreleased]` is
   empty and `--check` passes. The release notes are that section, as in
   `93bef12`; the tag build fails if it is missing or empty.
4. **Commit** the version bump (`chore(release): vX.Y.Z`), open a PR, merge to
   `master`.
5. **Tag + push**:

   ```sh
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

6. Watch `gh run watch` for the release run; check `gh release view vX.Y.Z` lists
   the five assets, then verify them as `docs/development/build.md`
   ("Verifying a release") shows.

## Release candidates

From 0.4.0 every minor goes through `vX.Y.Z-rc.N` first; the full procedure is
`docs/development/release.md` (ADR-0176). Differences from a final cut:

- Version `X.Y.Z-rc.N` in `meson.build` and `PELORUS_VERSION_STR`; the
  `PELORUS_VERSION_MAJOR/MINOR/PATCH` numbers stay `X`, `Y`, `Z`. The
  `README.md` and `AGENTS.md` release line names `vX.Y.Z-rc.N`.
- Changelog section `## [X.Y.Z-rc.N]`; the final `## [X.Y.Z]` lists the whole release.
- Before tagging, dispatch `Tester publish` from the default branch at the
  release commit and wait for it to go green (the candidate gate reads that run).
- The tag is refused when its shape is wrong, when `rc.(N-1)` does not exist, or
  when a leg of `scripts/release/candidate-legs.json` is red. The release is a
  prerelease and never `latest`; check `gh release view vX.Y.Z-rc.N --json isPrerelease`.

## Rules

- SemVer. The interop ABI is append-only — a release that changes it bumps the
  minor and ships the conformance-fixture update (ADR-0103, ADR-0109).
- Never tag a red `master`. Never force-push a tag.
