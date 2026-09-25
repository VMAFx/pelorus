<!-- markdownlint-disable MD013 -->
# ADR-0144: Pin FFmpeg by release and commit; run CI on Ubuntu 26.04 native Vulkan packages

- **Status**: Accepted
- **Date**: 2026-09-20
- **Deciders**: Lusoris
- **Tags**: ffmpeg, build, ci, supply-chain, vulkan

## Context

Pelorus's generated FFmpeg patch stack names n9.0.1 independently in shell
scripts, workflows, and documentation. The scripts also default to a developer
checkout and fixed `/tmp` paths, so a replay can silently consume the wrong
upstream tree or remove another run's worktree. A release tag alone is readable
but does not make the upstream object immutable if that tag is moved.

The FFmpeg lane also depends on LunarG's Ubuntu apt repository for Vulkan
headers and shader compilers. LunarG stopped updating those Ubuntu packages
after May 2025. Ubuntu 26.04 (Resolute) carries `libvulkan-dev`, `glslc`, and
`glslang-tools` natively and GitHub exposes an `ubuntu-26.04` runner. GitHub
currently classifies that image as beta: it updates weekly and has no Actions
SLA until general availability. Local testing therefore cannot establish the
hosted-runner part of the migration.

## Decision

Pelorus will keep the FFmpeg remote, human-readable release tag, and peeled
commit in one `build-config.env` contract. Generation, replay, and CI will
source that contract, verify that the tag resolves to the pinned commit, and
operate on the commit. Renovate will update the tag and digest together.

Generation and replay will require an explicit FFmpeg checkout and use only
new, run-scoped worktree paths with cleanup traps. Replay will build and install
the current libpelorus tree into a private prefix before linking FFmpeg, so an
ambient installation cannot satisfy the gate.

All GitHub-hosted jobs will use `ubuntu-26.04` and Ubuntu's native Vulkan and
SPIR-V packages. The release workflow will gain a non-publishing manual path.
The change is locally complete only after syntax, build, sanitizer, generation,
and replay gates pass; it is accepted for hosted use only after every job and
the manual release gate pass on GitHub's Ubuntu 26.04 image.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Pin n9.0.2 by tag only | Small, readable change | A moved tag changes the tested source; Renovate can leave copied consumers stale | Does not provide reproducible upstream identity |
| Keep Ubuntu 24.04 and use its native packages | GA runner with Actions SLA | Retains the older toolchain and does not exercise the current target environment | Useful fallback, but not the requested forward baseline |
| Keep LunarG's apt repository | Minimal workflow diff | Repository updates ended after May 2025; adds a retired third-party package source | Not a durable CI dependency |
| Vendor Vulkan SDK binaries | Fully controllable versions | Adds large artifacts and a separate update/provenance burden | Ubuntu packages already provide the required build-time tools |

## Consequences

- **Positive**: one update changes all operational FFmpeg consumers; a replay
  identifies the exact upstream commit and current libpelorus implementation.
- **Positive**: worktree setup is concurrency-safe and cannot force-remove an
  unknown caller path.
- **Positive**: CI no longer depends on LunarG's retired apt feed.
- **Negative**: Ubuntu 26.04 is a beta GitHub image until GitHub declares it GA;
  its jobs have no Actions SLA and must be validated on the hosted service.
- **Negative**: replay becomes slower because it builds and installs
  libpelorus before FFmpeg. That cost is the proof that the linked binary did
  not use an ambient library.
- **Neutral**: this changes build/release policy, not Pelorus's ABI or project
  version. A fallback to Ubuntu 24.04 requires a superseding decision if the
  hosted beta proves unreliable.

## References

- Source: `req` — “vmafx moved a lot”, “there must be a lot of version bumps”.
- [Research digest 0144](../research/0144-ffmpeg-9.0.2-ci-refresh.md).
- [ADR-0104](0104-ffmpeg-patch-stack.md),
  [ADR-0108](0108-deep-dive-deliverables-rule.md), and
  [ADR-0143](0143-ffmpeg-9-migration.md).
- FFmpeg n9.0.2 tag: `ce8f11b9fac4cd7ccfb41ca7c01bc17f5cda8cc3`;
  peeled commit: `946fcce07b6dcd0331c8cc609192aeff5e1924f8`.
