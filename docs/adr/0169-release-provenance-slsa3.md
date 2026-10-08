<!-- markdownlint-disable MD013 -->
# ADR-0169: Build, attest, and sign releases in a reusable workflow (SLSA Build Level 3)

- **Status**: Proposed
- **Date**: 2026-10-08
- **Deciders**: Lusoris
- **Tags**: release, ci, supply-chain, governance

## Context

The `native-gpu-systems` profile and the `security:high` facet declare SLSA
Build Level 3 provenance, keyless cosign signatures, and an SBOM for releases.
Until now Pelorus produced none of them: `release.yml` built, packaged, and
published `pelorus-ffmpeg-patches-<tag>.tar.gz` in one job with
`contents: write`. Since the Praetor pin of
[ADR-0168](0168-praetor-engine-492a00f.md), the audit measures these controls
from the workflow files (HISS-11) and failed:

```text
[FAIL] Supply chain (HISS-11): policy declares SLSA Build Level 3 but the workflows reach Level 0. ...
[FAIL] Supply chain (HISS-11): policy sets enforce_cosign but no workflow signs with cosign ...
[FAIL] Supply chain (HISS-11): policy sets require_sbom but no workflow generates an SBOM ...
```

The engine credits Level 3 only to a reusable workflow of this repository
(`on: workflow_call`) whose job runs a build step and then GitHub's
attestation action, with no artefact download (`actions/download-artifact`,
`gh run download`) and no cache restore over the attested files before the
attestation (`internal/forge/provenance_workflow.go` at Praetor `492a00f9`).
Its build steps are a fixed list (`go build`, `cargo build`, GoReleaser,
`docker build`, `npm run build` and similar) plus `make` running a target;
`meson` and `ninja` are not on it. GitHub documents Build Level 3 for its
attestation action only when the reusable workflow that builds the software
also generates the attestation. `enforce_cosign` needs a `cosign sign`,
`sign-blob`, `attest`, or `attest-blob` step; `require_sbom` needs an SBOM
generator such as `anchore/sbom-action` (`internal/forge/sbom_workflow.go`).

The user chose to implement the controls now rather than declare a dated gap.

## Decision

Split the release into three jobs and move every build step into a new
reusable workflow, `.github/workflows/release-build.yml`, whose only trigger is
`workflow_call`:

- `release.yml` keeps the `ci` job of the A14/A15 release gate. A `build` job
  (`needs: ci`) calls `release-build.yml` with `contents: read`,
  `id-token: write`, and `attestations: write`. A `publish` job
  (`needs: build`) runs only on a `v*` tag push, downloads the release
  build's artefact, and runs `gh release create`. It is the only job with
  `contents: write`, and it does not check out the repository.
- `release-build.yml` runs, in one job: checkout without persisted credentials,
  the build configuration, the toolchain, `meson setup --werror build`,
  `make build`, the fast suite, the tag==version check, the changelog check,
  the release notes, and the archive (same content as before). Then it runs
  `anchore/sbom-action` (SPDX JSON of the archive, no upload of its own),
  `sha256sum` over the archive and the SBOM into `SHA256SUMS`, and
  `actions/attest-build-provenance` with both files as subjects. Then
  `sigstore/cosign-installer` and `cosign sign-blob --yes --bundle
  SHA256SUMS.sigstore.json SHA256SUMS`, then `cosign verify-blob` against the
  workflow's own identity (`--certificate-identity-regexp` anchored to
  `https://github.com/VMAFx/pelorus/.github/workflows/release-build.yml@`,
  `--certificate-oidc-issuer https://token.actions.githubusercontent.com`).
  Last, `actions/upload-artifact` uploads the files with the provenance
  bundle that the attestation step wrote.
- The release attaches five files: the archive, the SBOM, the provenance
  bundle, `SHA256SUMS`, and `SHA256SUMS.sigstore.json`.
- Every action is pinned by full commit SHA with a `# vX.Y.Z` comment. Each
  version is the newest release that was at least three days old on
  2026-10-08. `upload-artifact` v7.0.2 and `download-artifact` v8.0.2 were one
  day old, so the previous releases are pinned.

  | Action | Version | Commit | Released |
  | --- | --- | --- | --- |
  | `actions/attest-build-provenance` | v4.2.2 | `4d101475d8b20a2381f78447822ac1eab6504dd8` | 2026-08-06 |
  | `anchore/sbom-action` | v0.24.3 | `66cbf4bc1f1c0d2edc94016e65bc221b6bb0ad6c` | 2026-10-02 |
  | `sigstore/cosign-installer` (cosign v3.0.6 default) | v4.1.2 | `6f9f17788090df1f26f669e9d70d6ae9567deba6` | 2026-05-07 |
  | `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | 2026-04-10 |
  | `actions/download-artifact` | v8.0.1 | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | 2026-03-11 |

- `scripts/check-build-config.py` enforces the shape, and its self-test rejects
  a mutation of each rule.

The SBOM is generated before `SHA256SUMS` and the attestation, not after them,
so the checksums, the cosign signature, and the provenance all cover it.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Reusable build workflow that builds, attests, signs, and uploads; tag-only publish job | Level 3 by GitHub's and the engine's definition; one job holds `contents: write`; a dispatch rehearses everything but publishing | Two workflow files; the release files pass through a workflow artefact | **Chosen** |
| Attest in the current single `release` job | Smallest change | Level 2 only: the attestation is signed with the identity of the workflow that also publishes, and the engine credits Level 3 only to a reusable workflow | Fails HISS-11 |
| `slsa-framework/slsa-github-generator` `generator_generic_slsa3.yml` | Credited Level 3 by the engine; `slsa-verifier` checks it | Its repository says it is no longer actively maintained and points to GitHub artifact attestations; a remote reusable workflow outside Renovate's SHA pinning | Unmaintained upstream |
| Declare a dated HISS-11 exception | No workflow work | The controls stay absent; the exception needs renewal every 90 days | User decision: implement now |
| Build with `ninja` only, no `make build` | One fewer layer | The engine does not read `meson` or `ninja` as a build step, so the attestation would stay Level 2 | Fails HISS-11 |

## Consequences

- **Positive**: a release carries SLSA v1.0 provenance signed by the reusable
  build's identity, a keyless cosign bundle over `SHA256SUMS`, and an SPDX SBOM.
  `praetorctl audit` reports `[PASS] Supply chain (HISS-11): SLSA Build Level 3
  declared, Level 3 measured from release.yml; cosign signing in release.yml;
  SBOM generated in release-build.yml.` A manual dispatch exercises the whole
  chain without publishing.
- **Negative**: the audit reads only the workflow files. Whether the hosted run
  attests, signs, and verifies as written, and whether the signer identity is
  `release-build.yml@refs/tags/<tag>`, is proven only by a hosted run (a
  `workflow_dispatch` rehearsal, then the first tag). The SBOM of an archive of
  patches and C sources lists few or no packages. Five more pinned actions need
  updates.
- **Neutral / follow-ups**: `docs/development/build.md` documents the
  verification (`gh attestation verify`, `cosign verify-blob`,
  `sha256sum -c`). Branch protection does not require the release jobs.

## References

- [ADR-0168](0168-praetor-engine-492a00f.md) (engine pin that measures
  HISS-11), [ADR-0145](0145-praetor-governance-adoption.md).
- Praetor `492a00f9`: `docs/guides/releasing.md` ("How the audit measures the
  SLSA level", "Verifying a published release"),
  `internal/forge/provenance_workflow.go`, `internal/forge/sbom_workflow.go`.
- `actions/attest` v4.2.1 README (permissions `id-token: write`,
  `attestations: write`; `bundle-path` output), cosign v3.0.6
  `doc/cosign_sign-blob.md` and `doc/cosign_verify-blob.md`,
  `anchore/sbom-action` v0.24.3 `action.yml`.
- Source: user decision 2026-10-08 (decision B), paraphrased: implement SLSA
  Build Level 3, cosign, and the SBOM now instead of declaring an exception.
