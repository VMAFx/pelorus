<!-- markdownlint-disable MD013 -->
# Research 0169: release provenance, SBOM, and signature measurements

Measurements behind [ADR-0169](../adr/0169-release-provenance-slsa3.md), taken
on 2026-10-08 with Praetor `492a00f9` on the branch of
[ADR-0168](../adr/0168-praetor-engine-492a00f.md).

## What the engine credits

Read from `internal/forge/provenance_workflow.go` and
`internal/forge/sbom_workflow.go` at Praetor `492a00f9`:

| Rule | Source | Consequence for Pelorus |
| --- | --- | --- |
| Level 3 = a job calls `./.github/workflows/<file>` (`on: workflow_call`) whose own job runs a build step, then `actions/attest-build-provenance` | `calledJob`, `jobProvenance.attest` | The attestation must live in a reusable workflow, not in `release.yml` |
| Build steps: `go build`, `cargo build`, GoReleaser, `docker build`, `docker buildx build`, `npm`/`pnpm`/`yarn` build, or `make` running a target | `buildCommands`, `runsMakeTarget` | `meson` and `ninja` do not count; the release build runs `make build BUILD_DIR=build` |
| `actions/download-artifact`, `gh run download`, or a cache restore over the attested paths before the attestation demotes it to Level 2 | `readAction`, `importCommands`, `cacheImports` | The release build downloads nothing and uses no cache |
| A reusable workflow called from a reusable workflow is not followed | `calledJob` | `release.yml` calls `release-build.yml` directly |
| Cosign: a `cosign sign`, `sign-blob`, `attest`, or `attest-blob` step; installing or verifying signs nothing | `invokesCosignSigning` | `cosign sign-blob` in the release build |
| SBOM: `anchore/sbom-action`, `actions/attest-sbom`, or a known SBOM command | `sbomGeneratorActions` | `anchore/sbom-action` |

HISS-10 reads `meson setup` on the command line, and treats `meson test` as a
lane only after a `run:` step configured the tree; it does not read what a
`make` target compiles (`docs/guides/build-warnings.md`). The release build
therefore runs `meson setup --werror build` as its own step before `make build`
and the fast suite.

## Audit before and after

Before (`release.yml` building and publishing in one job):

```text
[FAIL] Supply chain (HISS-11): policy declares SLSA Build Level 3 but the workflows reach Level 0. ...
[FAIL] Supply chain (HISS-11): policy sets enforce_cosign but no workflow signs with cosign ...
[FAIL] Supply chain (HISS-11): policy sets require_sbom but no workflow generates an SBOM ...
[FAIL] Supply chain (HISS-11): no exceptions entry declares the gap above; ...
```

After:

```text
[PASS] Supply chain (HISS-11): SLSA Build Level 3 declared, Level 3 measured from release.yml; cosign signing in release.yml; SBOM generated in release-build.yml. Measured from the workflow files under .github/workflows; published attestations and signatures were not checked.
[PASS] Build warnings (HISS-10): 5 build lanes fail on a warning (Meson 5).
```

HISS-18 reports the same six warnings as before (`ci.yml` and
`standards-gate.yml` jobs that run on drafts) and none for `release.yml` or
`release-build.yml`, which have no `pull_request` trigger.

## Action pins

Each tag was resolved with `git ls-remote` to the commit it peels to, and each
release date read from `gh api repos/<owner>/<repo>/releases`. The rule is the
newest release at least three days old on 2026-10-08.

| Action | Pinned | Commit | Released | Newer release skipped |
| --- | --- | --- | --- | --- |
| `actions/attest-build-provenance` | v4.2.2 | `4d101475d8b20a2381f78447822ac1eab6504dd8` | 2026-08-06 | none |
| `anchore/sbom-action` | v0.24.3 | `66cbf4bc1f1c0d2edc94016e65bc221b6bb0ad6c` (annotated tag `fffec3d8` peeled) | 2026-10-02 | none |
| `sigstore/cosign-installer` | v4.1.2 | `6f9f17788090df1f26f669e9d70d6ae9567deba6` | 2026-05-07 | none |
| `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | 2026-04-10 | v7.0.2, 2026-10-07 |
| `actions/download-artifact` | v8.0.1 | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | 2026-03-11 | v8.0.2, 2026-10-07 |

At these versions: `attest-build-provenance` v4 wraps `actions/attest`
v4.2.1, which needs `id-token: write` and `attestations: write`
(`artifact-metadata: write` only for a registry storage record, unused here)
and writes its bundle to the path in the `bundle-path` output.
`cosign-installer` v4.1.2 installs cosign v3.0.6 by default. cosign v3.0.6
`sign-blob` takes `--bundle` and `--yes` and writes the new bundle format by
default; `verify-blob` takes `--bundle`, `--certificate-identity-regexp`, and
`--certificate-oidc-issuer`. `anchore/sbom-action` v0.24.3 defaults
`upload-artifact` and `upload-release-assets` to `true`; the release build sets
both to `false`, because only the `publish` job may write the release.

## Checker proofs

`scripts/check-build-config.py --self-test` adds eight mutations of
`release-build.yml` and eight of `release.yml`; each must be rejected with its
own error, and the current files must pass. To prove the cases are live, four
rules were disabled one at a time in a throwaway copy of the checker; the
self-test then failed each time:

| Rule disabled | Self-test result |
| --- | --- |
| publish job must run only on a v* tag push | exit 1: `workflow regression: publish on every run was accepted` |
| release build must not use (download/cache) | exit 1: `workflow regression: artefact downloaded before the attestation was accepted` |
| release-build.yml must be triggered by workflow_call only | exit 1: `workflow regression: release build also runs on push was accepted` |
| release build steps are out of order | exit 1: `workflow regression: signature before the attestation was accepted` |

Run against the single-job `release.yml` of `master` (pull request 76), the checker exits 1,
starting with `job build must call ./.github/workflows/release-build.yml`.
Without `release-build.yml` it reports
`.github/workflows/release-build.yml: missing`.

## What only a hosted run proves

The audit, the checker, and actionlint read files. These need a
`workflow_dispatch` rehearsal and then the first tag:

- the attestation is issued and `gh attestation verify` accepts it with
  `--signer-workflow VMAFx/pelorus/.github/workflows/release-build.yml`;
- the Fulcio certificate's identity is
  `https://github.com/VMAFx/pelorus/.github/workflows/release-build.yml@<ref>`,
  with `<ref>` the caller's ref, which the in-job `cosign verify-blob` regexp
  and the documented `--certificate-identity` assume;
- `anchore/sbom-action` with `file:` writes an SPDX JSON for the `.tar.gz`
  (Syft is not installed on the workstation);
- the reusable workflow's output reaches the `publish` job, and
  `gh release create` attaches the five files.
