<!-- markdownlint-disable MD013 -->
# Releases and release candidates (maintainer procedure)

Every minor from 0.4.0 goes through at least one release candidate. `v0.3.0`
stays a plain cut, and patch releases (`vX.Y.Z`, `Z > 0`) need none. The
decisions are in [ADR-0176](../adr/0176-rc-process-and-tester-publish.md); the
workflow shape is in [build](build.md#release).

## Tags

| Tag | Release kind | Latest |
| --- | --- | --- |
| `v0.4.0-rc.1` | prerelease | never |
| `v0.4.0` | release | yes, by GitHub's own ordering |
| `v0.4.0-rc.01`, `v0.4.0-rc`, `v0.4.0-rc.0`, `v0.4.0-beta.1` | refused | |

`scripts/release/verify-release.py tag` also refuses an rc whose predecessor is
missing (`rc.3` without `rc.2`), an rc number that is not above the existing
ones, an rc of a version that is already released, and a final `vX.Y.0` (from
0.4.0) without any candidate. The release build runs it on every tag push, with
the tag list read from the remote.

## Version strings

| Where | rc.1 of 0.4.0 | Final 0.4.0 |
| --- | --- | --- |
| tag | `v0.4.0-rc.1` | `v0.4.0` |
| `meson.build` `version:` | `0.4.0-rc.1` | `0.4.0` |
| `PELORUS_VERSION_STR` | `"0.4.0-rc.1"` | `"0.4.0"` |
| `PELORUS_VERSION_MAJOR`, `_MINOR`, `_PATCH` | `0`, `4`, `0` | `0`, `4`, `0` |
| `libpelorus.so.0.4.0`, `libpelorus.pc` `Version:` | `0.4.0` | `0.4.0` |
| `README.md` and `AGENTS.md` release line | `v0.4.0-rc.1` | `v0.4.0` |
| changelog section, release notes | `## [0.4.0-rc.1]` | `## [0.4.0]` |

Meson rejects a suffix in a shared-library version and pkg-config sorts
`0.4.0-rc.1` after `0.4.0`, so the shared object and the `.pc` file use the
numeric core (`pelorus_core_version` in `meson.build`). The tag step compares
the tag with the meson version with plain string equality, then checks that
`pelorus.h` agrees on the string and on the three numbers. There is no fallback
from an rc to its core.

## Candidate gate

`scripts/release/candidate-legs.json` lists the legs. For a `-rc.N` tag the
release build checks them on the exact tagged commit
(`check-candidate-legs.py`) and refuses the run when one is red or missing:

| Leg | Kind | Needs |
| --- | --- | --- |
| `ci` | needs | the full CI call in `release.yml`, which already gates the build |
| `tester-publish` | workflow_run | the newest `workflow_dispatch` run of `tester-publish.yml` for the tag commit concluded `success`: its `head_sha` is the tag commit, or it ran on master and its run name is `tester-publish workflow_dispatch <full sha>` ([ADR-0199](../adr/0199-patch-branch-tester-evidence.md)) |
| `hardware-coverage`, `bench-smoke` | manual | recorded as `manual`; the maintainer confirms them in the release pull request. Not machine-verified |

The result is `CANDIDATE_LEGS.json`: it is covered by the signed `SHA256SUMS`
and attached to the prerelease. A new leg goes into the JSON file; the
build-config checker fails when a leg names a job or workflow that does not
exist.

## Cut a candidate

1. In the release pull request, bump `meson.build`, `pelorus.h` (string and
   numbers) and the release line in `README.md` and `AGENTS.md` (then
   `standardsctl compile-context`) to `X.Y.Z-rc.N`. Rotate the fragments of
   `changelog.d/` into `## [X.Y.Z-rc.N] - YYYY-MM-DD`, write a leading
   `### Highlights` subsection by hand (3 to 6 user-facing bullets drawn from
   the section's own entries; `scripts/release/extract-release-notes.sh` refuses
   a section without `### Highlights` or outside 3 to 6 bullets, unwraps hard
   line breaks, rewrites relative documentation links to absolute blob URLs
   at the release tag, and places `### Maintenance` last), and run
   `bash scripts/release/concat-changelog-fragments.sh --write`. The section
   holds the changes since the previous candidate, or since the previous
   release for `rc.1`.
2. Merge. Dispatch `Tester publish` from the default branch with an empty `ref`
   (the commit must be the merge commit), wait for the environment approval and a
   green run ([runbook](tester-image.md)).
3. Tag that commit with the pre-tag guard and push the tag:
   `bash scripts/release/tag-release.sh vX.Y.Z-rc.N && git push origin vX.Y.Z-rc.N`.
4. Watch the run. The result is a prerelease with the same assets as a release
   plus `CANDIDATE_LEGS.json`. `gh release view vX.Y.Z-rc.N --json isPrerelease`
   must print `true`.

## Cut the final release

Same steps with `X.Y.Z`, without the suffix. Tag with
`bash scripts/release/tag-release.sh vX.Y.Z && git push origin vX.Y.Z`.
The `## [X.Y.Z]` section is the
whole release: merge the bullets of the candidate sections into it, write its
`### Highlights` subsection by hand (3 to 6 user-facing bullets for the whole
release), keep maintainer-internal items in the closing `### Maintenance`
subsection, and remove the candidate headings (the prereleases keep their own
notes). The release build refuses the tag when no candidate of that version
exists (from 0.4.0).

## Patch release from a patch branch

A patch of an older minor whose fixes master cannot carry alone (master already
holds the next minor) comes from a patch branch `lts-X.Y`
([ADR-0199](../adr/0199-patch-branch-tester-evidence.md)). The ruleset that
protects master covers `lts-*` too, so every change reaches it through a pull
request with signed commits and the required checks.

1. Branch `lts-X.Y` from the tag `vX.Y.Z` (Z = the last patch) and pick the
   fixes in pull requests against it (`git cherry-pick -x -S -s`).
2. Bump the version and changelog in a `chore(release): vX.Y.Z+1` pull request
   against `lts-X.Y`.
3. Dispatch `Tester publish` **from master** with `ref` set to the full 40-hex
   SHA of the patch-branch tip; a branch name or short SHA is refused, so the
   run name names the commit.
4. Tag with `bash scripts/release/tag-release.sh --sha <full sha> vX.Y.Z+1` and
   push the tag; `release.yml` runs CI on the tag and builds the release.

## Rehearse

A manual dispatch of `Release` runs `ci` and `build` without the tag steps and
never publishes. To rehearse the tag steps, push `vX.Y.Z-rc.N` in a fork, or
test the rules directly: `python3 -I scripts/release/verify-release.py --self-test`,
`python3 -I scripts/release/check-candidate-legs.py --self-test`,
`bash scripts/release/tag-release.sh --self-test`,
`bash scripts/release/check-publish-ref.sh --self-test` and
`bash scripts/release/extract-release-notes.sh --self-test`.
