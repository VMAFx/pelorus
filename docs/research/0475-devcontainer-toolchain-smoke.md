<!-- markdownlint-disable MD013 -->
# Dev container image toolchain smoke

**Date:** 2026-11-09

**Decision:** [ADR-0250](../adr/0250-devcontainer-toolchain-smoke.md).

**Scope:** the published dev container image `ghcr.io/vmafx/pelorus-dev@sha256:92599aa475c9…` (built from `8e6a41e`), an image built locally from this change's `.devcontainer/base/Containerfile`, `master` at `e3a5f5c`, Docker 29.9.3 with buildx 0.38.2 and BuildKit v0.34.2 (`docker-container` driver), a loopback `registry:3` for the tag promotion probe, and the GitHub-hosted runner sizes on docs.github.com, read on 2026-11-09. Each container ran with `--network none` as the image's default user `vscode` (uid 1000).

## Verdict

The published image passes every command and `pkg-config` check, but cannot run `make test` (its tester kit step exits 77) or `make python-lint` (no `ensurepip`). The image built with `locales`, a generated `de_DE.UTF-8` locale and `python3-venv` runs `scripts/check-devcontainer-image.py`, and through it `make verify-offline`, to exit 0. The published image fails the same smoke at the venv check. One buildx builder gives the same digest for `--load` and for an OCI export or an untagged push, so the publish job can gate on the loaded image, push it by digest, require the pushed digest to match, and tag it only then. Tagging must copy the manifest (`--prefer-index=false`); the default wraps it in a new index with a different digest.

## Measurements

| Probe | Result |
| --- | --- |
| Published image, commands and `pkg-config` modules | all present; `vulkan` 1.4.341, `vpl` 2.16, `aom` 3.13.1, `SvtAv1Enc` 2.3.0, `ffnvcodec` 12.1.14.0 |
| Published image, copy of `e3a5f5c`, fast suite | exit 0: 163 OK, 8 expected fail, 14 skipped, all listed in `.config/test-skips.json` |
| Same, tester kit step (`scripts/check-tester-kit-install.py --run-kit`) | exit 77: the `enc-telemetry-adapters-locale` skip stops the `set -eu` runner; `locale -a` lists `C`, `C.utf8`, `POSIX` |
| Same, `make python-lint` | fails: `check-python-lint: --install: python3 -m venv failed for '.cache/python-lint'` |
| Published image, checkout mounted read-only and used in place | `series-coexistence-self-test` and `series-coexistence-selftest-isolation` fail: both write under `$ROOT/build` |
| One builder, `--load` and OCI export of the same Containerfile and context | identical digest `sha256:d3387abda530…` |
| Branch image `sha256:cdbda7e7aa7b…`, smoke over a full clone with this change's files | exit 0: `toolchain ok`; fast suite 164 OK, 8 expected fail, 13 skipped, 0 fail; `make verify-offline` exit 0 |
| Published image, same smoke (planted negative) | exit 1: `missing venv: python3 -m venv failed: The virtual environment was not created successfully because ensurepip is not available` |
| Same smoke, branch image, no CPU limit on 32 host threads | 1 min 43 s |
| Same smoke, branch image, `--cpus 4 --memory 16g` (the public-repository `ubuntu-26.04` runner size) | exit 0 in 100 s; same counts |
| One builder, loopback `registry:3`, `FROM scratch` probe: `--load` and `push-by-digest=true` | identical digest `sha256:0306743713…`; the registry holds no tag before promotion (`NAME_UNKNOWN`) |
| `docker buildx imagetools create --prefer-index=false` from that digest, two tags | both tags resolve to `sha256:0306743713…` (OCI image manifest) |
| Same, default `--prefer-index=true` | the tag resolves to a new index `sha256:64513e60c0…`, not the gated digest; `--compare-digests` exits 1 on it |
| Same registry, two probes pushed by digest, both tags promoted to the first, then `latest` re-pointed at the second | the job's per-tag check exits 1 on `latest` only; after `imagetools create --prefer-index=false` points `latest` back at the first digest, it exits 0 on both tags |
| `docker buildx imagetools inspect --format '{{.Manifest.Digest}}'` on the published image | prints `sha256:92599aa475c9…` |
| Promotion mutants, each copied to `scripts/` and run with `--self-test` | all seven fail: `check-build-config.py` without the publish job rule, its order check, its tagged push check, the Makefile rule, or its recipe check; `--compare-digests` without the mismatch check or the format check. The workflow at `20337a7` (both tags pushed before the comparison) is refused with eight errors, three of them naming a missing publish step; the workflow on `master` at `e3a5f5c` with ten |
| Order check with the earlier text search restored, run with `--self-test` | exit 1: it accepts Attest moved after Promote tags with its name only in a comment, the same with its name only in a `run:` block, and a renamed Attest step |
| Mutants, each copied to `scripts/` and run with `--self-test` | all six fail: smoke without the command check (plant b), the module check (plant c), the venv check (plant d), or with a copy that skips tracked files (plant h); `check-build-config.py` accepting one commit argument for all build steps (two planted cases), or a shallow checkout |

## Limits

- On the runner, the checkout belongs to the runner user (uid 1001), not to `vscode`. The smoke copies a world-readable tree; locally that was measured on a tree made world-readable with `chmod -R a+rX`. The first CI run measures the runner's case.
- The runner's Docker image store and GHCR's handling of an untagged push were not measured; the pull request job does not push. The publish job's digest comparison fails closed if `--load` and push give different digests there, and then no tag moves. The pull request run used buildx v0.38.1, which has `--prefer-index`.
- A smoke from a linked worktree fails 15 tests: its `.git` file points at a git directory the container cannot see. Reproduce from a full clone, as the CI checkout (`fetch-depth: 0`) is.
- The tester kit runner still treats an allowed skip as a failure; the new locale hides that in the image but does not fix it.
