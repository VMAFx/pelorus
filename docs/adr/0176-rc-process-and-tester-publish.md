<!-- markdownlint-disable MD013 MD060 -->
# ADR-0176: Release candidates are `vX.Y.Z-rc.N` prereleases gated on candidate legs; tester images publish only from a dispatched, approved workflow

- **Status**: Proposed
- **Implementation**: pending (#234)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: release, ci, supply-chain, licensing, tester

## Context

[ADR-0173](0173-tester-programme.md) fixes the tester programme and defers two
mechanisms: the release-candidate process ([#234](https://github.com/VMAFx/pelorus/issues/234))
and the publish workflow ([#232](https://github.com/VMAFx/pelorus/issues/232)).
Today a pre-release tag would not be flagged as a prerelease and could become
`latest`, and nothing in CI builds, signs or attests an image. Meson 1.x
rejects a suffix in a shared-library version (`library(version: '0.4.0-rc.1')`
fails configuration; verified on the pinned toolchain), and pkg-config orders
`0.4.0-rc.1` after `0.4.0`, so how an rc version is represented needs a rule.
Candidate numbering is manual because Pelorus has no release-please.

## Decision

**1. Tag shape.** A release tag is `vX.Y.Z` or `vX.Y.Z-rc.N`: no leading zeros,
`N >= 1`, no other suffix. `scripts/release/verify-release.py tag` enforces it
together with the numbering: `rc.N` needs `rc.(N-1)`, no later rc and no final
tag of the same version. From 0.4.0 on a final `vX.Y.0` needs at least one
candidate; `v0.3.0` stays a plain cut and patch releases need none.

**2. Version representation.** For a candidate, `meson.build` `version:` and
`PELORUS_VERSION_STR` carry the full `X.Y.Z-rc.N`, and `PELORUS_VERSION_MAJOR`,
`_MINOR`, `_PATCH` carry the numeric core. The tag, `meson.build` and `pelorus.h`
must agree exactly (`verify-release.py version`; the existing equality check
stays and there is no fallback to the core). The shared object and the `.pc`
file carry only the numeric core (`pelorus_core_version` in `meson.build`), so
`libpelorus.so.0.4.0` and `Version: 0.4.0` ship in the candidate and in the
final release. The release notes section is `## [X.Y.Z-rc.N]`; the final
`## [X.Y.Z]` section lists the whole release, not only the delta after the last
candidate.

**3. Publication.** The release build outputs `prerelease` (`true` for an rc).
The publish job passes `--prerelease --latest=false` for a candidate and fails
on an unclassified tag; after creation it reads `isPrerelease` back and fails on
a mismatch with the tag.

**4. Candidate gate.** For an rc tag the release build runs
`scripts/release/check-candidate-legs.py` on the exact commit
(`scripts/release/candidate-legs.json`): `needs` legs are release jobs that gate
the build (full CI), `workflow_run` legs need the newest dispatched run of a
workflow for that commit to be `success` (the tester publish workflow), and
`manual` legs are recorded as `manual` and never as green. The result is signed
into `SHA256SUMS` as `CANDIDATE_LEGS.json` and attached to the prerelease.

**5. Tester publish workflow.** `.github/workflows/tester-publish.yml` has
`workflow_dispatch` as its only publishing trigger; pull requests build without
pushing and stop on drafts. `validate` requires the commit and the workflow's
own commit to be reachable from `master`. `publish` runs in the environment
`tester-publish`, so the build starts after approval. It builds
`tools/tester/Containerfile`, whose licence gate (`tools/tester/check-ffmpeg-licence.sh`)
fails the build unless `ffmpeg -version` shows `--enable-gpl --enable-version3`,
no banned flag (`--enable-nonfree`, `cuda-nvcc`, `cuda-sdk`, libfdk-aac,
decklink, libmpeghdec) and `-L` reports GPL version 3 or later without the word
nonfree; a self-test plants each flag first. It runs the documented command
without a GPU and validates the report, pushes
`ghcr.io/vmafx/pelorus:tester-<date>-<sha8>` and a `-source` companion, and
attaches an SPDX SBOM, SLSA provenance and a keyless cosign signature, then
verifies each. Its last step pulls both digests anonymously and fails closed: a
first push creates the package private, so the first run fails there until the
maintainer makes the package public. `scripts/check-build-config.py` pins the shape of both
workflows, the Containerfile and the legs file, and its self-test plants a
defect per rule.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Meson version without suffix; the suffix only in the tag | No change to the shared-library version rule | Library, header and `pelorus_version_string()` cannot tell a candidate from the final; the tag==version check would need a fallback | A silent fallback is what the check exists to prevent |
| Suffix everywhere, including the `.so` and `.pc` | One string | Meson refuses it; pkg-config `>= 0.4.0` would reject the candidate | Does not build |
| Run the candidate check as a separate job that `build` needs | Visible as its own job | `needs` on a skipped job skips the build for every final tag | A step guarded by the tag shape keeps one build path |
| Build the image in a job without approval and push after approval | The approval gate comes after the long build | Artifact hand-off between jobs; the attested job is not the building job | Approval first; a rejected dispatch costs nothing |

## Consequences

- **Positive**: a candidate cannot become `latest` or skip a number; every
  candidate records which legs were green; the licence rule fails closed before
  the first image exists; every new rule has a planted-defect proof.
- **Negative**: a candidate needs a tester publish run on the exact commit, so a
  dispatch from `master` at the candidate commit precedes the tag. The first
  dispatch of the skeleton image is expected to need the vendor work in #229 and
  #230 before it passes the report step. The SBOM attestation verify line uses
  predicate type `https://spdx.dev/Document/v2.3`, which no run has confirmed yet.
- **Neutral / follow-ups**: the maintainer makes the GHCR package public after
  the first dispatch creates it, and creates the `tester-publish` environment (restricted to `master`, required reviewer);
  `docs/development/release.md` and `docs/development/tester-image.md` hold the
  procedures; ADR-0173 flips to Accepted when the first dispatch is shown to
  wait for approval.

## References

- Issues: #108 (epic), #232, #234. [ADR-0173](0173-tester-programme.md), [ADR-0169](0169-release-provenance-slsa3.md), [ADR-0170](0170-renovate-shape-guards.md), [docs/licensing.md](../licensing.md).
- [Research 0172](../research/0172-tester-programme.md): T9 and T14; vmafx ADR-1201 and ADR-1348 (rc rules), ADR-2198 (candidate legs), ADR-1578 (withdrawn images).
- Source: per user direction (issue #234 and #232 acceptance), release candidates cover every 0.x minor from 0.4, tester FFmpeg is GPL-3.0-or-later and never nonfree.
