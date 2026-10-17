<!-- markdownlint-disable MD013 MD060 -->
# ADR-0208: Build the generic tester image natively on arm64

- **Status**: Accepted
- **Implementation**: landed (#170)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: tester, arm64, aarch64, ci, container
- **Related**: [ADR-0173](0173-tester-programme.md), [ADR-0176](0176-rc-process-and-tester-publish.md), [ADR-0178](0178-tester-artifact-licence-record.md), [ADR-0180](0180-tester-vendor-images.md), [ADR-0203](0203-arm64-ci-lane.md)

## Context

Issue #170 asks for an arm64 tester image. [ADR-0203](0203-arm64-ci-lane.md) added a native arm64 CI lane that builds libpelorus, replays the patch stack and runs the lavapipe checks on `ubuntu-26.04-arm`, but the tester image itself (`tools/tester/Containerfile`) was only ever built on x86-64 runners.

Nothing in the image is tied to x86-64:

- The pinned Debian base digest is an OCI index with a `linux/arm64` manifest.
- Debian trixie ships every package the build and runtime stages install for arm64.
- The tester report records `platform.machine()` as `host.arch` (`tools/tester/pelorus_tester_report.py`), so a run inside the image says which architecture executed it.

The vendor kits are different: the `nvidia` kit depends on the NVIDIA host driver stack and the `intel` kit on Intel's media driver, and neither has arm64 tester hardware in this project.

## Decision

1. `tester-publish.yml` gets a job `build-arm64` that builds the `generic` kit on `ubuntu-26.04-arm` on every pull request that touches the image inputs and on the nightly schedule, under the same `if:` as the `build` job. It pushes nothing.
2. Two receipts make the leg prove what it claims: the built image must report `linux/arm64` in `docker image inspect`, and the report the image writes without a device must carry `host.arch` `aarch64`. Either mismatch fails the job.
3. The licence self-tests stay in the `build` job only; they test Python and shell logic that does not depend on the architecture.
4. `scripts/check-build-config.py` holds the shape: the job runs exactly on `ubuntu-26.04-arm`, keeps the repository guard and draft stop, fetches the shared series before the build, and keeps both receipts. Its self-test plants each removal and the wrong runner and must refuse them.
5. Publishing stays amd64 only for now. A multi-arch index for the generic kit (per-architecture digests, one signed and attested index) is the follow-up under #170.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Separate `build-arm64` job on the native runner (chosen) | Native build; the matrix and check names of `build` stay unchanged | One more job definition | Chosen |
| Add an architecture axis to the `build` matrix | One job definition | Renames every existing `build` check and needs matrix excludes for the two vendor kits | Rejected: more churn for the same coverage |
| `docker buildx --platform linux/arm64` with QEMU on x86-64 | No arm64 runner needed | Emulated FFmpeg build is slow, and the report would run under emulation, so `host.arch` would not prove a native run | Rejected: weaker evidence |
| Build and publish the multi-arch index in one step | Closes #170 in one change | Changes the signed, attested publish path at the same time as the first arm64 build | Rejected: build evidence first, publish next |

## Consequences

- **Positive**: Every image-input change is built and smoke-run on arm64, so an arm64 break shows up before a publish depends on it.
- **Negative**: One more arm64 runner job of up to 90 minutes per qualifying pull request and per night.
- **Neutral**: The tester build jobs are not required checks; the ruleset is unchanged.

## References

- Issue [#170](https://github.com/VMAFx/pelorus/issues/170): tester: arm64 image.
- [ADR-0203](0203-arm64-ci-lane.md): native arm64 CI lane.
- [ADR-0180](0180-tester-vendor-images.md): one image per kit.
