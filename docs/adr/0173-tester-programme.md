<!-- markdownlint-disable MD013 MD060 -->
# ADR-0173: Tester programme: artifacts are evidence for a master commit, licence rules bind before first publication

- **Status**: Proposed
- **Implementation**: pending (#108)
- **Date**: 2026-10-08
- **Deciders**: Lusoris
- **Tags**: tester, release, licensing, supply-chain, ci, docs

## Context

Pelorus needs outside testers with hardware the project does not own. The
compute path is Vulkan, so one binary covers every vendor, but the Vulkan
driver and the hardware encoder differ per vendor, and the project has no
tester package, report format, publishing environment or licence record today
([research 0172](../research/0172-tester-programme.md), part B). The sibling
vmafx runs such a programme. Its first release-candidate images were withdrawn
because licence handling came after publication (vmafx ADR-1578); this ADR
adopts the principles that came out of that and fixes them before anything is
published.

Two properties differ from vmafx. The product is a patched FFmpeg binary, so
redistributing it triggers FFmpeg's licence. And the value is partly BD-rate,
which needs the bench subset and a quality oracle, so the kit cannot defer
measurement the way the vmafx kit does.

This ADR is the principle and licence-rule ADR of epic
[#108](https://github.com/VMAFx/pelorus/issues/108) ([#224](https://github.com/VMAFx/pelorus/issues/224)).
It also fixes the report contract, which `tools/tester/` implements
([#226](https://github.com/VMAFx/pelorus/issues/226)), and the fixture rule
([#225](https://github.com/VMAFx/pelorus/issues/225)). Images, the publish
workflow and the stage runners are later children and carry their own ADRs only
where they decide something new.

## Decision

**1. Evidence, not release.** A tester artifact is evidence for one commit
reachable from `master`. It is never a product release. Its name is
`tester-<YYYYMMDD>-<sha8>` (image tags add a variant suffix, Windows zips use
`tester-windows-<YYYYMMDD>-<sha8>`). It is never attached to a `v*` release and
never tagged or marked `latest`. A release candidate (`vX.Y.Z-rc.N`) is a
different object with its own process
([#234](https://github.com/VMAFx/pelorus/issues/234)); it requires every
tester leg to be green on the exact commit and consumes tester artifacts as
evidence, but a tester artifact is not an rc.

**2. Package set.** Linux images for NVIDIA, Intel and AMD, an arm64 CPU image,
a Windows x64 zip, and a macOS spike. Phasing follows the roadmap: NVIDIA and
Intel images in 0.4; the AMD image is a spike (Vulkan video encode on RADV; no
AMF); arm64 and the Windows zip in 0.5; macOS (MoltenVK) is a spike that
produces a verdict, not a bundle. A CPU image with software Vulkan (lavapipe)
is the base the vendor images extend and lets hosted CI run the real shaders
([#228](https://github.com/VMAFx/pelorus/issues/228)). Images are published as
tags `tester-<date>-<sha8>[-nvidia|-intel|-amd]` of `ghcr.io/vmafx/pelorus`
(multi-arch index where more than one platform exists). The package is created
and made public once, by the maintainer, before the first tester tag is
announced, because a new GHCR package starts private and its visibility cannot
be changed through the API; the publish workflow checks an anonymous pull and
fails closed. `pelorus-dev` stays a dev-only image.

**3. Stages.** The kit runs eight stages in a fixed order: `probe`,
`libpelorus_suite`, `registration`, `format_matrix`, `steering_smoke`,
`sidedata_roundtrip`, `zero_copy_chain`, `bench`. Each ends as `pass`, `fail`,
`not_run` (with a reason), `no_device` (with the missing option named) or
`incomplete`. `bench` is opt-in and labelled non-gating performance data. A
backend that is requested but absent is reported, never replaced by another
path (no silent fallback). The stage runners are specified in
[#227](https://github.com/VMAFx/pelorus/issues/227); this ADR fixes only the
ids and their order.

**4. Report contract.** One run writes one JSON report, validated against
[`tools/tester/report.schema.json`](../../tools/tester/report.schema.json)
(schema version 1), plus `SHA256SUMS` and a manifest that hashes it (integrity
only, no authenticity claim). The program is `python3 -I` with the standard
library only, and takes no dependency on vmafx code. Exit codes: 0 pass, 1 fail,
2 incomplete, 100 unavailable. `no_device` is a non-failure with a reason, so a
CPU-only host exits 0; `--require-device` turns it into 100. The report is
redacted before it is written: host name, home and repository prefixes, user
name, Vulkan `deviceUUID`/`driverUUID`/LUID and PCI bus ids. The producer
re-scans its own output and writes nothing if an identifier survives; the
validator applies the same patterns. A report is rejected when a field is
missing, the stage list is truncated or out of order, the schema version
differs, the exit code does not follow from the stages, or an identifier is
present. The validator's rules each have a planted bad case in the program's
`--self-test`, and the self-test fails when any one rule is switched off.

**5. Licence rules, binding before the first publication.** Adapted from vmafx
ADR-1503 and ADR-1514:

- Ship only what the report needs. Vendor binaries, where unavoidable, are
  unmodified. No NVIDIA, AMD or Intel proprietary driver file is placed in any
  image: the host supplies the NVIDIA driver and Vulkan ICD through the
  container toolkit; Intel ANV and AMD RADV are Mesa user-mode drivers from the
  Debian packages.
- libpelorus and the tester tooling are EUPL-1.2; the FFmpeg-tree sources
  (`ffmpeg-patches/files/`, the patches) stay LGPL-2.1-or-later; vendored files
  keep their upstream licence ([ADR-0171](0171-eupl-relicense.md)).
- The FFmpeg in a tester image is built GPL-3.0-or-later (`--enable-gpl
  --enable-version3`) and is published under that licence. It is never built
  with `--enable-nonfree`, `--enable-cuda-nvcc`, `--enable-cuda-sdk`,
  libfdk-aac, decklink or libmpeghdec; the build fails if `ffmpeg -L` or
  `-version` names nonfree.
- Every file in an artifact has a recorded licence in a checked-in licence
  record, and a gate fails the image build on an unclaimed file, a missing
  copyright text, copyleft code without source, or a stale notices file
  ([#236](https://github.com/VMAFx/pelorus/issues/236)). Debian packages keep
  their `/usr/share/doc/*/copyright`.
- Every image has a `-source` companion image with the same lifetime: the
  patched FFmpeg tree as compiled, `series.txt`, the configure line, the
  libpelorus commit, and the Debian source packages at the installed versions.
- Each artifact carries `THIRD_PARTY_NOTICES` that names this repository and
  the exact commit, the BBB CC BY 3.0 attribution when the fixture ships, and
  the licence texts. Fixtures are pinned by SHA-256 in a lock file with a
  licence per file; the lock is verified at image build; a fixture whose licence
  is unconfirmed (netflix-bar today) is not shipped.
- Each image has an SPDX SBOM, keyless cosign signature and SLSA provenance,
  following [ADR-0169](0169-release-provenance-slsa3.md); the publish job
  verifies them before it finishes.
- The audit of the published bytes against the record runs before the first
  publication and per release candidate.

**6. Publishing environment.** Publication runs in a workflow with
`workflow_dispatch` as its only publishing trigger (input `ref` or `tag`,
which must be reachable from `master`), in the GitHub environment
`tester-publish`, restricted to `master` with the maintainer as required
reviewer. Pull requests and the nightly schedule build without pushing.
Creating the environment, its reviewer rule and the ruleset change is a forge
write by the maintainer (rulesets are applied with `praetorctl sync --remote`);
this ADR does not create it, and the first dispatched dry run must be shown to
wait for approval before any publishing step
([#232](https://github.com/VMAFx/pelorus/issues/232)).

**Out of scope.** Production images, a prebuilt FFmpeg for end users (1.0
roadmap item), hardware-report intake ([#233](https://github.com/VMAFx/pelorus/issues/233)),
CI cost control ([#235](https://github.com/VMAFx/pelorus/issues/235)), the
image Dockerfiles, and any vendor driver redistribution.

### Decision matrix

| Question | Decision | Rejected |
| --- | --- | --- |
| What is a tester artifact | Evidence for a master commit, `tester-<date>-<sha8>`, never `latest` | A product release; a draft release |
| FFmpeg licence of the image | GPL-3.0-or-later, `-source` image, never nonfree | LGPL-only build (cannot carry x264/x265 reference arms for the bench subset) |
| Vendor drivers | None in images; host supplies | Redistribute vendor user-mode drivers |
| Package name | Tags of a new public `ghcr.io/vmafx/pelorus` | Reuse `pelorus-dev` (mixes dev and tester); new `pelorus-tester` package (same visibility step, longer name) |
| Report tooling | Own stdlib-only program in `tools/tester/` | Import vmafx `rc1-tester` (cross-repo dependency) |
| `no_device` | Reasoned non-failure, exit 0; `--require-device` gives 100 | Treat as failure (CPU-only hosts and hosted CI could never pass) |
| Publish trigger | Dispatch only, behind `tester-publish` | Publish on push or on a release event |
| macOS | Spike with a verdict | Bundle first |

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Tester builds from source | No artifact to trust or license | Needs a toolchain and an 18-patch replay; most testers will not | The programme exists to remove that barrier |
| Publish first, add licence handling later | Faster to first tester | vmafx had to withdraw its rc images and delete a package (ADR-1578) | Licence work after publication costs more than the delay |
| LGPL-2.1-or-later FFmpeg image | Fewer obligations | No x264/x265 arms for the bench subset | Chosen direction is GPL-3.0-or-later; an LGPL variant can be added by a later ADR |
| Ship NVIDIA/Intel/AMD user-mode drivers in the images | Works without host setup | Redistribution terms, size, and the vmafx ROCm findings | Host supplies or Mesa from Debian |
| Hosted-runner GPU testing instead of testers | No outside party | No GPU runners; hosted CI can only cover lavapipe | Complementary: lavapipe covers shaders, testers cover hardware |

## Consequences

- **Positive**: licence and naming rules exist before the first image; a tester
  on a CPU-only host gets a valid report and exit 0; the report contract is
  testable in CI without a GPU; the first rc can reuse the same evidence.
- **Negative**: the GPL-3.0-or-later image makes every tester image carry
  source obligations (the `-source` image doubles the publishing work); the
  maintainer has one manual step for the package and one for the environment.
- **Neutral / follow-ups**: ADR flips to Accepted when #232 (workflow and
  environment) merges; `docs/development/tester-image.md` (maintainer runbook)
  lands with #232; the CI validator over tracked reports and the hardware
  report intake land with #233; an ADR for the licence record format lands with
  #236 if its design differs from this summary.

## References

- Issues: #108 (epic), #224, #225, #226, #227, #232, #234, #236.
- [Research 0172](../research/0172-tester-programme.md): vmafx programme survey and mapping.
- vmafx ADRs: 1342 (explicit backends, exit codes), 1492 and 1493 (evidence not release), 1503 and 1514 (licence rules, GPL-3.0-or-later FFmpeg), 1578 (withdrawal), 1505, 1509, 1511, 1515 (GPU image precedents).
- [ADR-0169](0169-release-provenance-slsa3.md), [ADR-0171](0171-eupl-relicense.md), [docs/licensing.md](../licensing.md).
- Source: per user direction, the maintainer decided on 2026-10-08 that tester FFmpeg builds are GPL-3.0-or-later without nonfree components, that macOS starts as a MoltenVK spike, and that release candidates cover every 0.x minor from 0.4.
