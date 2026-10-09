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
| `tester-publish` | workflow_run | the newest `workflow_dispatch` run of `tester-publish.yml` with `head_sha` equal to the tag commit concluded `success` |
| `hardware-coverage`, `bench-smoke` | manual | recorded as `manual`; the maintainer confirms them in the release pull request. Not machine-verified |

The result is `CANDIDATE_LEGS.json`: it is covered by the signed `SHA256SUMS`
and attached to the prerelease. A new leg goes into the JSON file; the
build-config checker fails when a leg names a job or workflow that does not
exist.

## Cut a candidate

1. In the release pull request, bump `meson.build`, `pelorus.h` (string and
   numbers) and the release line in `README.md` and `AGENTS.md` (then
   `standardsctl compile-context`) to `X.Y.Z-rc.N`. Rotate the fragments of
   `changelog.d/` into `## [X.Y.Z-rc.N] - YYYY-MM-DD` and run
   `bash scripts/release/concat-changelog-fragments.sh --write`. The section
   holds the changes since the previous candidate, or since the previous
   release for `rc.1`.
2. Merge. Dispatch `Tester publish` from the default branch with an empty `ref`
   (the commit must be the merge commit), wait for the environment approval and a
   green run ([runbook](tester-image.md)).
3. Tag that commit and push the tag:
   `git tag -s vX.Y.Z-rc.N && git push origin vX.Y.Z-rc.N`.
4. Watch the run. The result is a prerelease with the same assets as a release
   plus `CANDIDATE_LEGS.json`. `gh release view vX.Y.Z-rc.N --json isPrerelease`
   must print `true`.

## Cut the final release

Same steps with `X.Y.Z`, without the suffix. The `## [X.Y.Z]` section is the
whole release: merge the bullets of the candidate sections into it and remove the
candidate headings (the prereleases keep their own notes). The release build
refuses the tag when no candidate of that version exists (from 0.4.0).

## Rehearse

A manual dispatch of `Release` runs `ci` and `build` without the tag steps and
never publishes. To rehearse the tag steps, push `vX.Y.Z-rc.N` in a fork, or
test the rules directly: `python3 -I scripts/release/verify-release.py --self-test`
and `python3 -I scripts/release/check-candidate-legs.py --self-test`.
