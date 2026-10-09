<!-- markdownlint-disable MD013 MD060 -->
# ADR-0179: The pelorus-dev image starts from plain Ubuntu, records every file it holds, and fails its build on an unrecorded one

- **Status**: Proposed
- **Implementation**: pending (#256)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: licensing, supply-chain, devcontainer, ci

## Context

[ADR-0153](0153-praetor-full-adoption.md) builds the dev container
toolchain base `ghcr.io/vmafx/pelorus-dev` from `.devcontainer/base/Containerfile`
and publishes it from `master`. The package is public. The audit in
[research 0236](../research/0236-tester-artifact-licence-audit.md) and issue
[#256](https://github.com/VMAFx/pelorus/issues/256) found, in digest `sha256:33c934d3...`:

- `git` 2.55.0 (GPL-2.0-only) built from source in `/usr/local/bin`, with its
  `libexec` and `share` trees, redistributed with neither source nor a written
  offer. It comes from the base image `mcr.microsoft.com/devcontainers/base:ubuntu26.04`.
- `actionlint`, `lefthook` and `node` installed outside the package system with
  no licence text, which the MIT licence and Node's licence require beside a
  binary.
- A label, `org.opencontainers.image.licenses=EUPL-1.2`, that describes none of
  the contents.
- No record and no gate, unlike the tester image ([ADR-0178](0178-tester-artifact-licence-record.md)).

Measured on the pinned MCR digest, the base adds to Ubuntu 26.04: the `vscode`
user (uid 1000) and passwordless `sudo`; `oh-my-zsh` in `/home/vscode` and
`/root`; `code`, `devcontainer-info` and a `systemctl` shim in `/usr/local/bin`;
`/usr/local/etc/vscode-dev-containers`; and the source-built git (17 files in
`/usr/local/bin`, a `libexec/git-core` tree, `share/git-core`, `gitk`, `gitweb`).
`devcontainer.json` already applies the `common-utils` feature, which creates or
reuses the user `vscode`, installs `sudo` and the usual shell tools, and does
not depend on the MCR base having done so.

## Decision

**1. Base: plain `ubuntu:26.04`, pinned by digest.** The Containerfile starts
from `ubuntu:26.04@sha256:...` and adds what it needs itself: `sudo`, the user
`vscode` (the image's uid 1000 user `ubuntu`, renamed), and a one-line
`NOPASSWD` sudoers rule. Git is Ubuntu's package, `/usr/bin/git`, which ships its
copyright file and whose source is in the Ubuntu archive; the image holds no GPL
software built outside a package, so no source offer beyond the archive pointer
in the notices is needed.

**2. Licence texts beside every non-package tool.** Node's `LICENSE` (previously
excluded from the extraction), actionlint's `LICENSE.txt` (from the release
archive) and Lefthook's `LICENSE` (the release archive holds only the binary, so
it is fetched from the release tag and checked against a pinned SHA-256) go to
`/usr/local/share/licenses/<tool>/`.

**3. A record and a gate.** `.devcontainer/base/licensing.json` records the
image like the tester record does: components with SPDX, paths, source and, new
in this ADR, `licence_files` (texts that must exist beside the component);
Ubuntu files by dpkg ownership plus their copyright file; ignore rules that
name a reason and an expiry. `tools/tester/licensing.py` is the one
implementation (HISS-19). It gains an optional `notices` object in the record
(title and intro lines, so the notices header need not name FFmpeg), optional
`--ffmpeg-*` arguments, `licence_files`, and `ARG KEY=value` pins for
`version_from` (the record's versions must equal the Containerfile's `ARG`s).
A `licence` stage runs `self-test`, `notices` and `check` on the finished tree;
the final stage copies the notices from it, so the image cannot be built
without the gate passing. Ubuntu's `main` and `universe` are permitted; the
record names no `restricted` or `multiverse` package. The record also covers
`pebble`, a Canonical binary in the Ubuntu base image that the first full check
found outside the package system.

**4. No licence label.** `org.opencontainers.image.licenses` is removed: one SPDX
expression for the Ubuntu layer plus MIT tools plus EUPL-1.2 files is either
wrong or unreadable. The label `dev.vmafx.pelorus.notices` names
`/usr/share/licenses/pelorus-dev/THIRD_PARTY_NOTICES.txt`, which the build
generates from the record. `check-build-config.py` requires that the label
names the record's notices path, that the Containerfile installs each
component's licence directory, and that no licence label returns.

**5. Build context.** The image builds from the repository root, with a
`Containerfile.dockerignore` that lets only the gate, the record and the EUPL
text through, because the gate lives in `tools/tester/`. The workflow passes
`PELORUS_COMMIT` and also triggers on `tools/tester/licensing.py` and
`LICENSES/EUPL-1.2.txt`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep the MCR base, delete `/usr/local/bin/git` and its `libexec` and `share` trees | Smallest Containerfile change | The record would then have to cover oh-my-zsh, Microsoft's scripts and `/usr/local/etc/...` that the image does not need, and any later MCR bump can reintroduce another source-built tool; the gate would need an ignore for each | The image stays smaller and fully accounted for when it starts from Ubuntu; the dev container features already supply the user tooling |
| Keep the MCR base and ship git's source plus a written offer | Keeps the base | Permanent duty to carry GPL source for a tool nobody needs from `/usr/local` | Ubuntu's package already ships a working git |
| Keep a single SPDX label | Machine-readable | Cannot be true for the whole image | The notices file and record are the authoritative statement |
| A second copy of `licensing.py` for the dev image | No build-context change | Two implementations of one behaviour | HISS-19: one implementation, two records |

## Consequences

- **Positive**: the published image holds Ubuntu's git; every non-package file has a record and a licence text; a tool added to `/usr/local` without a record fails the build and the pull request build; the image is about 0.5 GB smaller (1.67 GB against 2.22 GB, locally measured).
- **Negative**: every change to the image contents edits `licensing.json`; ignore rules expire (first expiry 2027-04-09); the dev container no longer has oh-my-zsh, and tools that the MCR base preinstalled and the `common-utils` feature installs at container creation (for example `ssh`, `jq`, `vim-tiny`) now arrive at that step, not in the pulled image.
- **Follow-up**: the digest in `.devcontainer/devcontainer.json` and `Dockerfile.praetor` still names the old image. After `publish` pushes the new image, re-pin it with the Praetor re-render in [the build guide](../development/build.md#dev-container); until then the bundle builds on the old, non-compliant digest. Embedded Go dependencies of actionlint and Lefthook are not enumerated; their upstream licence files cover the tool only.

## References

- Issue [#256](https://github.com/VMAFx/pelorus/issues/256); [research 0236](../research/0236-tester-artifact-licence-audit.md) `pelorus-dev` section; [ADR-0178](0178-tester-artifact-licence-record.md).
- Source: per user direction in the licensing-fix task brief (paraphrased).
