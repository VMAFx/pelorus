<!-- markdownlint-disable MD013 -->
# ADR-0250: Run the offline native gate inside the dev container image before it is pushed

- **Status**: Proposed
- **Implementation**: pending (#475)
- **Date**: 2026-11-09
- **Deciders**: Lusoris
- **Tags**: `ci`, `docker`, `devcontainer`, `test`
- **Related**: [ADR-0153](0153-praetor-full-adoption.md), [ADR-0179](0179-dev-image-licence-record.md), [ADR-0235](0235-docker-build-push-actions.md)

## Context

`.github/workflows/devcontainer-image.yml` builds the dev container toolchain base image `ghcr.io/vmafx/pelorus-dev` from `.devcontainer/base/Containerfile` (ADR-0153, ADR-0235). On a pull request it builds and discards the image; on a push to `master` it builds, pushes and attests it. No job ever starts the image, so a toolchain that cannot run the repository's own gate ships as `latest` (issue #475).

Measured on 2026-11-09 against the published image `ghcr.io/vmafx/pelorus-dev@sha256:92599aa475c9…` (built from `8e6a41e`), started with `--network none` as its default user `vscode` (uid 1000) over a copy of a `master` `e3a5f5c` checkout (evidence: `.workingdir/evidence/c9-11/`):

- Every tool the Containerfile installs is on `PATH`, and `pkg-config` reports `vulkan` 1.4.341, `vpl` 2.16, `aom` 3.13.1, `SvtAv1Enc` 2.3.0 and `ffnvcodec` 12.1.14.0. A check that only looks for commands and versions passes.
- `meson setup --werror`, `ninja`, the fast suite (163 OK, 8 expected fail, 14 listed skips), `make format-check`, `make tidy` and `make docs-check` all exit 0.
- `make test` still fails, with exit 77: the image has no comma-decimal locale, so `pelorus:enc-telemetry-adapters-locale` skips (a skip `.config/test-skips.json` allows), and the installed tester kit runner (`set -eu`) stops on that skip. The `core` job in `.github/workflows/ci.yml:39-43` installs `locales` and runs `locale-gen de_DE.UTF-8` on the runner; the tester image does the same with `localedef` (`tools/tester/Containerfile:154-155`); the dev image does neither.
- `make python-lint` fails: the image has no `python3-venv`, so `python3 -m venv` cannot import `ensurepip`. The CI jobs get a venv-capable Python from `actions/setup-python`.

So `make verify-native` cannot pass inside the dev container, although every check of the commands themselves passes. Two of the probe's failures came from mounting the checkout read-only: the series coexistence self-tests write under `$ROOT/build`. They pass once the checkout is copied into a directory the container user owns.

## Decision

We will start the image the workflow just built and run the repository's offline native gate in it before anything is pushed. A new Makefile target `verify-offline` holds the native gate steps that need no network (`build test format-check tidy docs-check`); `verify-native` becomes `verify-offline` plus `python-lint`, so the step list stays in one place. A stdlib-only script, `scripts/check-devcontainer-image.py`, runs inside the container with `--network none` as the image's default user. It checks the commands and `pkg-config` modules the gate does not exercise (the FFmpeg stack build's assemblers and encoder headers, the hook and workflow linters), checks that `python3 -m venv` can create an environment with `pip` (what `scripts/check-python-lint.sh --install` needs from the image), copies the read-only checkout into a private directory, and runs `make verify-offline` there. Every child process has a deadline. The pull request job builds with `load: true` and runs the script. The publish job builds with `load: true`, runs the script, and only then logs in to `ghcr.io`, builds again with `push: true` from the same builder cache, and fails unless the pushed digest equals the loaded one. The Containerfile gains `locales` with a generated `de_DE.UTF-8` locale and `python3-venv`, with the generated locale files recorded in `.devcontainer/base/licensing.json` (ADR-0179).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Run the offline gate in the image before push (chosen) | Catches what a toolchain can and cannot do, measured: both defects above; reuses the Makefile steps; nothing broken reaches `latest` | Image job takes the gate's run time on top of the build; the gate result depends on the checkout as well as the image | — |
| Check commands and versions only (`--version`, `pkg-config --modversion`) | Fast; independent of the checkout | Passes on `92599aa475c9` (measured), which cannot run `make test` or `make python-lint` | Misses the two defects that motivated the issue |
| Run all of `make verify-native` with network access | One target, no new Makefile target | `python-lint` installs Ruff and zizmor from PyPI inside the container, an egress path in the job that holds `packages: write`; the `core` job already runs it on the runner | The venv check covers what the image owes `python-lint`, without network |
| Push first, then pull and test the pushed image | Tests the exact registry bytes | A failing smoke has already moved `latest`; needs a rollback | The digest equality check gives the same assurance before the push |
| `devcontainers/ci` action or `@devcontainers/cli` | Exercises `devcontainer.json` features too | New dependency (rule 9) with Node and Docker-in-Docker setup; `devcontainer.json` is a Praetor projection that pins a published digest, not the image this run built | The base image, not the projection, is what this workflow produces |

## Consequences

- **Positive**: a dev container image that cannot run the offline native gate fails its pull request and is never pushed. The dev container gains the comma-decimal locale check and a working `make python-lint`.
- **Negative**: the image jobs run the gate (measured locally: about two minutes on six cores); the job timeouts grow to cover it. A pull request whose code breaks the gate also fails the image job, which reports the same failure as the `core` job.
- **Neutral / follow-ups**: `.devcontainer/devcontainer.json` keeps naming the digest it was rendered with until the next Praetor render picks up a newly published digest. The kit runner treating an allowed skip as a failure, and the packages the `ffmpeg-stack` and `sanitizers` jobs install that the image lacks (`libdav1d-dev`, `libclang-rt-dev`), are separate defects tracked outside this decision.

## References

- Issue #475: start the dev container base image for a toolchain smoke.
- [ADR-0153](0153-praetor-full-adoption.md) (dev container), [ADR-0179](0179-dev-image-licence-record.md) (licence record), [ADR-0235](0235-docker-build-push-actions.md) (pinned Docker actions).
- Source: `req` (paraphrased; controller order `pelorus-c9-11-order-1505`, 2026-11-09, frontier rank): start the dev container base image in CI for a toolchain smoke, on its own branch, through the local gate, landed when the pull request is clean.
