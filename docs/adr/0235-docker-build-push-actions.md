<!-- markdownlint-disable MD013 MD060 -->
# ADR-0235: Pinned Docker Actions for Dev Container Image Pipeline

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `ci`, `docker`, `devcontainer`, `buildx`, `supply-chain`
- **Related**: [ADR-0153](0153-praetor-full-adoption.md), [ADR-0169](0169-release-provenance-slsa3.md), [ADR-0179](0179-dev-image-licence-record.md)

## Context

The dev container toolchain image workflow (`.github/workflows/devcontainer-image.yml`) previously used hand-rolled Docker CLI commands (`docker login`, `docker build`, `docker push`). Extracting the image manifest digest in the publish job required a custom Python one-liner on `docker buildx imagetools inspect`, piping JSON output to extract the digest, verifying it against a regex, and exporting it to `$GITHUB_OUTPUT`.

Meanwhile, `VMAFx/vmafx` uses official, digest-pinned Docker GitHub Actions (`docker/setup-buildx-action`, `docker/login-action`, and `docker/build-push-action`) in `.github/workflows/dev-container-publish.yml`. Using different patterns across the two repositories means each repository maintains its own Docker publishing logic, and a fix to one never reaches the other.

## Decision

1. Replace the hand-rolled Docker login, build, and push shell commands in `.github/workflows/devcontainer-image.yml` with pinned Docker actions using the identical pins as `VMAFx/vmafx`:
   - `docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1`
   - `docker/login-action@dbcb813823bdd20940b903addbd779551569679f # v4.6.0`
   - `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0`
2. In the PR `build` job:
   - Preserve the initial `Stop on a draft pull request` step unchanged (HISS-18).
   - Keep checkout.
   - Run `docker/setup-buildx-action` and `docker/build-push-action` with `push: false`, `context: .`, `file: .devcontainer/base/Containerfile`, `build-args: PELORUS_COMMIT=${{ github.sha }}`, and tag `${{ env.IMAGE }}:pr-${{ github.sha }}`.
   - Ensure every step in the `build` job preserves `if: github.event.pull_request.draft != true`.
3. In the `publish` job:
   - Run checkout, `docker/setup-buildx-action`, and `docker/login-action` against `ghcr.io` with `${{ github.actor }}` and `${{ secrets.GITHUB_TOKEN }}`.
   - Run `docker/build-push-action` with `push: true`, tags `${{ env.IMAGE }}:${{ github.sha }}` and `${{ env.IMAGE }}:latest`, `build-args: PELORUS_COMMIT=${{ github.sha }}`, and `id: push`.
   - Explicitly disable action-generated provenance and SBOM (`provenance: false`, `sbom: false`) to prevent conflicts with the repository's authoritative `actions/attest-build-provenance` SLSA step.
   - Preserve the strict build policy of [ADR-0169](0169-release-provenance-slsa3.md): do not configure `cache-from` or `cache-to` before attestation.
4. Digest validation:
   - Validate `${{ steps.push.outputs.digest }}` against `^sha256:[0-9a-f]{64}$` in an explicit verification step.
   - Pass the digest via environment variable (`env: DIGEST: ${{ steps.push.outputs.digest }}`) to avoid expression injection in run scripts.
   - Emit the published digest and tags to `$GITHUB_STEP_SUMMARY`.
   - Preserve the downstream SLSA v1.0 attestation step unchanged (`subject-digest: ${{ steps.push.outputs.digest }}`).
5. Scope of adoption:
   - `tester-publish.yml` stays hand-rolled. That workflow builds each candidate tester kit locally and runs execution and licence gates (`tools/tester/licensing.py check`, `docker run` self-tests) *before* pushing to GHCR. Using `docker/build-push-action` there would require building with `load: true` followed by separate manual push or multi-phase invocation, yielding no workflow simplification.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Pinned Docker actions (this ADR) | Actively maintained upstream; Apache-2.0 licence; outputs clean image digest directly; identical pins to `VMAFx/vmafx`, so Renovate proposes the same version bumps in both repositories | Relies on third-party action ecosystem | **Chosen**; reduces shell scripting, standardizes workflow patterns across VMAFx repositories, and keeps both repositories on the same action versions |
| Keep hand-rolled Docker CLI | No additional action dependencies; already implemented | Requires brittle shell parsing (`docker buildx imagetools inspect` piped to Python JSON decoder); manual login handling; diverges from `VMAFx/vmafx` | Rejected; higher maintenance footprint and divergence across sibling repositories |
| Buildah / Podman | Daemonless rootless builds | GitHub-hosted Ubuntu runners natively run Docker; introduces container storage differences and extra setup steps | Rejected; standard Docker Buildx is already the environment standard and runner native |
| Red Hat push-to-registry (`redhat-actions/push-to-registry`) | Modular push action | Splits build and push into disparate action ecosystems; differs from `VMAFx/vmafx` conventions | Rejected; `docker/build-push-action` integrates Buildx building and pushing in one unified step |

## Consequences

- **Positive**: Eliminates custom Python JSON parsing for manifest digest retrieval; aligns `devcontainer-image.yml` with `VMAFx/vmafx` dev container publishing; Renovate's GitHub Actions manager proposes the same Docker action bumps in both repositories.
- **Negative**: Adds dependencies on three Docker GitHub actions (`setup-buildx-action`, `login-action`, `build-push-action`).
- **Neutral / follow-ups**:
  - `scripts/check-build-config.py` updated to check for action inputs and regression cases.
  - `tester-publish.yml` remains hand-rolled due to pre-push container gating requirements.

## References

- `.github/workflows/devcontainer-image.yml`
- `VMAFx/vmafx` `.github/workflows/dev-container-publish.yml`
- [ADR-0153: Praetor full adoption](0153-praetor-full-adoption.md)
- [ADR-0169: SLSA L3 build provenance](0169-release-provenance-slsa3.md)
- [ADR-0179: Dev container image licence record](0179-dev-image-licence-record.md)
