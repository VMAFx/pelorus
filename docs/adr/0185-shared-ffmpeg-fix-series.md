<!-- markdownlint-disable MD013 MD060 -->
# ADR-0185: Take generic FFmpeg fixes from the shared fix series, as a pinned and verified release

- **Status**: Proposed
- **Implementation**: pending (#129)
- **Date**: 2026-10-10
- **Deciders**: Lusoris
- **Tags**: ffmpeg, patch-stack, supply-chain, nvenc, vulkan

## Context

The Pelorus patch stack carried fixes that are not Pelorus features. Patch
0021 fixed a queue family transfer in `libavutil/vulkan.c`, and most of patch
0022 kept `hevc_nvenc` user data SEI inside NVENC's 1024-byte limit for any
payload, not only Pelorus side data. The sibling project vmafx carries its own
FFmpeg stack with a generic fix of its own (the GCC diagnostics, series patch
0002 below), and both projects need the same NVENC truncation workaround. A
fix that lives in two stacks drifts, and each project would send it upstream
on its own.

Generic FFmpeg changes belong in one place (maintainer routing rule, also
[ADR-0184](0184-vulkan-drm-modifier-output-pools.md) decision 5). That place
now exists: [VMAFx/ffmpeg-patches](https://github.com/VMAFx/ffmpeg-patches), a
`git format-patch` series on one pinned FFmpeg release that every consumer
applies before its own patches. Its release `v0.1.0-rc.1` targets `n9.0.2`
(`946fcce07b6d`), the base in `build-config.env`, and holds four patches:

| Series patch | Change | Pelorus before |
| --- | --- | --- |
| 0001 | `ff_vk_frame_barrier()` keeps the owning queue family (upstream PR 24970) | patch 0021, the same diff |
| 0002 | GCC 14 and 16 diagnostics in about 50 files | none; no file of the Pelorus stack is touched |
| 0003 | `hevc_nvenc` user data SEI budget of 768 bytes per picture | the generic part of patch 0022 |
| 0004 | drop user data SEI that NVENC would write truncated | none; patch 0022 only kept Pelorus blobs clear of it |

A series outside this repository is a new dependency of every build, so hard
rule 9 asks for the alternatives and for the reason this one wins.

## Decision

1. **Pin.** `build-config.env` names the series by repository, release tag,
   tagged commit and tarball sha256 (`FFMPEG_SERIES_REPO`, `_TAG`, `_COMMIT`,
   `_SHA256`). `scripts/check-build-config.py` validates the four values and
   their place after the FFmpeg block. A bump moves all four together, by
   hand, from a release that was verified first; Renovate does not track it,
   because it cannot derive the sha256 or the commit.
2. **One fetch and verify script.** `scripts/fetch-ffmpeg-series.sh OUT_DIR`
   downloads the release files, runs five checks and unpacks the tarball. Any
   failed check removes the output and exits 1:
   1. the tarball's sha256 equals the pin;
   2. `cosign verify-blob` of `SHA256SUMS` with the certificate identity of the
      series repository's `release-build.yml` at the pinned tag;
   3. `sha256sum --check --strict SHA256SUMS` over every listed file, read
      only once its signature holds;
   4. `gh attestation verify` of the tarball: SLSA provenance from that
      workflow, a GitHub-hosted runner, the pinned tag and the pinned commit;
   5. the unpacked `base.env` names the FFmpeg remote, tag and commit of
      `build-config.env`, and every patch in its `series.txt` exists.

   `--self-test` plants a flipped byte, a moved pin, a tampered `SHA256SUMS`
   and a wrong commit and requires each to fail at its own check; the
   `ffmpeg-stack` CI job runs it. `--unpack TARBALL OUT_DIR` checks the sha256
   pin only and is for the tester image build, whose runner ran the full check
   on the same file.
3. **Apply order.** The shared series goes first, in its `series.txt` order,
   then `ffmpeg-patches/series.txt`. `generate.sh`, `test/build-and-run.sh`,
   `test/qsv-roi-regression.sh` and `tools/tester/Containerfile` do that, and
   the checker refuses a version of each that skips the series or applies it
   late. `generate.sh` writes the Pelorus patches as the range above the
   series tip, so no Pelorus patch contains a series change.
4. **Pelorus stack: 22 patches.** Patch 0021 is dropped and its number
   retired. Patches 0022 and 0023 keep their numbers, because documents,
   ADR-0181, ADR-0183, the tester and the `v0.4.0-rc.2` release cite them;
   `generate.sh` maps its output to the shipped names by position. The
   `[PATCH n/22]` subject of a patch counts its position, so 0022 reads
   `21/22`.
5. **Patch 0022 becomes a hook.** It adds one call between the series'
   `av_memdup()` of a side data entry and `nvenc_hevc_sei_fits()`: a Pelorus
   blob is turned into its zero-free carrier ([ADR-0183](0183-sidedata-zero-free-carrier.md)),
   and on `hevc_nvenc` a blob whose carrier does not fit loses its per-cell
   maps first ([ADR-0181](0181-hevc-nvenc-sei-header-budget.md)). The budget,
   the drop of a payload that does not fit and their log lines are the
   series' code. Patches 0004, 0008 and 0011 change only in context.
6. **Base equality is checked, not assumed.** Check 5 fails when the series
   was made for another FFmpeg release. An FFmpeg bump therefore needs a
   series release on the new base first.
7. **Artefacts say so.** The NOTICE of the released patch archive names the
   series tag, commit and sha256 and states that the stack does not apply
   without it (`scripts/release/write-patch-notice.sh`); the `-source` tester
   image carries the series tarball next to the tree as compiled.

## Alternatives considered

| Option | Verdict | Reason |
| --- | --- | --- |
| Keep own copies in the Pelorus stack (status quo) | Rejected | The same fix in two stacks: patch 0021 and series 0001 are one diff, vmafx needs series 0004 as well, and every correction would have to land twice and go upstream twice. It also contradicts the routing rule. |
| Git submodule of VMAFx/ffmpeg-patches | Rejected | It pins a commit but verifies nothing: no signature, no provenance, and it can name a commit that was never released. Every replay runs in a throwaway FFmpeg worktree and every CI job would need a recursive checkout; the released patch archive would have to carry or re-fetch the submodule; the series' own governance files and tooling would sit inside this tree and its REUSE record. |
| Copy of the series' patches in this tree | Rejected | The status quo under another directory: a copy that drifts from the series, 167 KB of foreign diff in this repository's reviews (`wc -c patches/*.patch` of `v0.1.0-rc.1`), per-file licence records here for files maintained elsewhere, and the Pelorus release would redistribute them. Its one advantage, replay without network, does not outweigh that. |
| Release tarball pinned by sha256 only | Rejected | The sha256 proves the bytes, not their origin: a pull request that moves the tag and the sha256 to another tarball would pass. The signed checksums and the build provenance tie the bytes to the series repository's release workflow, tag and commit. |
| Release tarball pin plus sigstore verification | Chosen | Four pinned values, nothing vendored, byte identity and origin both checked, and the same release model Pelorus uses for its own archive ([ADR-0169](0169-release-provenance-slsa3.md)). |

## Consequences

- One copy of each generic fix. Patch 0022's `nvenc.c` change shrinks from 145
  added lines to 72, and the Vulkan queue family fix leaves the stack.
- Regenerating or replaying the stack needs network access, `cosign` and an
  authenticated `gh`. CI jobs that do it install cosign through the installer
  pin `release-build.yml` already uses and pass `GH_TOKEN`. A GitHub or
  sigstore outage blocks those jobs; that is the fail-closed choice.
- Encoder behaviour that changes with series 0004: on `h264_nvenc` and
  `hevc_nvenc`, a user data unregistered SEI from another producer that NVENC
  would write truncated is no longer written, with a warning. Before, it
  reached the stream and the decoder discarded it.
- Log text: a dropped SEI is reported by the series (`Not writing a N-byte
  user data unregistered SEI at pts ...`), a stripped Pelorus blob by patch
  0022 as before. `ffmpeg-patches/test/nvenc-udu-sei-smoke.sh` and
  `docs/usage/ffmpeg.md` follow.
- Patch 0022 once wrote the carrier straight from the side data into one
  allocation. Behind the series' `av_memdup()` it now strips the copy in
  place and replaces it by the carrier: one more short-lived allocation per
  Pelorus blob, and no per-encoder scratch buffer. That is the price of a
  one-call hook that does not edit series lines.
- The Pelorus-only skip of empty side data entries on `hevc_nvenc` is gone
  with the generic code; such an entry takes the stock path.
- `scripts/gen-h274-grain-calibration.py` reads `libavcodec/h274.c` at the
  FFmpeg base. The series does not touch that file; a later series patch that
  does needs the generator to read the patched tree.
- The pin is a release candidate. `v0.1.0` of the series is expected once both
  consumers have adopted it; that is a four-value bump and a regeneration.
- Rebase-sensitive: patch 0022 depends on the series' `sei_budget` variable
  and on the position of its hook; `ffmpeg-patches/AGENTS.md` invariant 10
  records both.

## References

- Shared series release [v0.1.0-rc.1](https://github.com/VMAFx/ffmpeg-patches/releases/tag/v0.1.0-rc.1) (tag commit `ea78aa5daf7ffaa22ac1ab75d0c160cf5f457ea3`, tarball sha256 `75193707608546171e00dc24c28eda8370491e28528727bd33f933529d6c948b`) and its README (apply order, release files, verification commands).
- Maintainer direction, paraphrased (req): Pelorus consumes the shared series instead of its own copies, applies it first, verifies sha256, cosign signature and build attestation, and keeps patches 0009 and 0023 in its own stack.
- Research [0185](../research/0185-shared-ffmpeg-fix-series.md): replay, regeneration, verifier negative cases and GPU results; evidence `.workingdir/evidence/shared-series-switch/` (local).
- [ADR-0104](0104-ffmpeg-patch-stack.md) (patch stack model), [ADR-0144](0144-ffmpeg-pin-and-ci-runner-policy.md) (`build-config.env`), [ADR-0169](0169-release-provenance-slsa3.md) (release provenance), [ADR-0181](0181-hevc-nvenc-sei-header-budget.md), [ADR-0183](0183-sidedata-zero-free-carrier.md), [ADR-0184](0184-vulkan-drm-modifier-output-pools.md).
- `cosign verify-blob` and `gh attestation verify` as installed (cosign v2.6.3, gh 2.102.0): flags read from their `--help`.
