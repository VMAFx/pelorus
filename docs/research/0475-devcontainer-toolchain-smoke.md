<!-- markdownlint-disable MD013 -->
# Dev container image toolchain smoke

**Date:** 2026-11-09

**Decision:** [ADR-0250](../adr/0250-devcontainer-toolchain-smoke.md).

**Scope:** the published dev container image `ghcr.io/vmafx/pelorus-dev@sha256:92599aa475c9…` (built from `8e6a41e`), an image built locally from this change's `.devcontainer/base/Containerfile`, `master` at `e3a5f5c`, Docker 29.9.3 with buildx 0.38.2 and BuildKit v0.34.2 (`docker-container` driver), and the GitHub-hosted runner sizes on docs.github.com, read on 2026-11-09. Each container ran with `--network none` as the image's default user `vscode` (uid 1000).

## Verdict

The published image passes every command and `pkg-config` check, but cannot run `make test` (its tester kit step exits 77) or `make python-lint` (no `ensurepip`). The image built with `locales`, a generated `de_DE.UTF-8` locale and `python3-venv` runs `scripts/check-devcontainer-image.py`, and through it `make verify-offline`, to exit 0. The published image fails the same smoke at the venv check. One buildx builder gives the same digest for `--load` and for an OCI export, so the publish job can gate on the loaded image and then require the pushed digest to match it.

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
| Mutants, each copied to `scripts/` and run with `--self-test` | all six fail: smoke without the command check (plant b), the module check (plant c), the venv check (plant d), or with a copy that skips tracked files (plant h); `check-build-config.py` accepting one commit argument for all build steps (two planted cases), or a shallow checkout |

## Limits

- On the runner, the checkout belongs to the runner user (uid 1001), not to `vscode`. The smoke copies a world-readable tree; locally that was measured on a tree made world-readable with `chmod -R a+rX`. The first CI run measures the runner's case.
- The runner's Docker image store was not measured. The publish job's digest comparison fails closed if `--load` and push give different digests there.
- A smoke from a linked worktree fails 15 tests: its `.git` file points at a git directory the container cannot see. Reproduce from a full clone, as the CI checkout (`fetch-depth: 0`) is.
- The tester kit runner still treats an allowed skip as a failure; the new locale hides that in the image but does not fix it.
