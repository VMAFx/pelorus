<!-- markdownlint-disable MD013 -->
# ADR-0153: Adopt Praetor's dev container, Git hooks, and branch ruleset

- **Status**: Proposed
- **Date**: 2026-09-30
- **Deciders**: lusoris
- **Tags**: governance, standards, hooks, devcontainer, ci

This ADR amends [ADR-0145](0145-praetor-governance-adoption.md). It replaces
that ADR's decision to decline the `branch-ruleset`, `dev-container`, and
`git-hooks` adoption steps. Every other ADR-0145 decision stands.

## Context

ADR-0145 pinned Praetor `25451d888c8710822dd578907625dc69a0975142` and kept
three `adoption.decline` entries in `.standards.yaml`. An agent added them in
the 2026-09-20 onboarding commit `11d1bdc` (pull request 56, squashed into
`e5b9d70`); no maintainer decision chose them. On 2026-09-30 the maintainer
decided on full adoption: every decline goes, and the repository takes the
assets the pinned engine renders for those steps.

The engine at the pin renders these assets for Pelorus
([research digest 0153](../research/0153-praetor-full-adoption.md)):

- **Dev container.** A bootstrap bundle in `.devcontainer/`: `devcontainer.json`,
  `Dockerfile.praetor`, and five `praetor-source.*.b64` parts that build the
  pinned `praetorctl` into the image. The audit compares `devcontainer.json`
  with its own rendering byte for byte and refuses keys outside its schema, such
  as `runArgs`. The features come from the locked `native-gpu-systems` catalog
  (`rust`, `nix`, `common-utils`). The default base image is Microsoft's plain
  Ubuntu 26.04 dev container image. Nothing in that bundle installs Meson, a C
  compiler, clang-format, clang-tidy, glslc, or the Vulkan headers, yet
  `postCreateCommand` runs `make verify-all`
  ([Praetor issue 75](https://github.com/cordanaLLM/praetor/issues/75)). The
  engine supplies native tools only through a digest-pinned `--base-image`.
- **Git hooks.** A `lefthook.yml` with governance jobs only (Pelorus has no
  `go.mod` or `Cargo.lock`), the checkpoint evaluator scripts, a checkpoint
  policy, and a standalone anti-evasion interceptor. Adoption runs
  `lefthook install` when the file is its current rendering. Outside CI the
  audit then requires `.git/hooks/pre-commit`. The rendered pre-push `gate` job
  runs `praetorctl gate run`, which refuses a receipt when no Go or Cargo stage
  ran. It rejects every push from this Meson repository
  ([Praetor issue 648](https://github.com/cordanaLLM/praetor/issues/648)). The
  checkpoint policy names `main` as its base branch
  ([Praetor issue 71](https://github.com/cordanaLLM/praetor/issues/71)).
- **Branch ruleset.** `.github/rulesets/main.json`, named
  `praetor-main-protection`, protects `master` and `lts-*`. It requires signed
  commits, linear history, two approving reviews including a code owner's, and
  seven status checks. The live `master` protection is a classic branch
  protection rule with four required checks, linear history, no signatures, and
  no required reviews. The audit checks the file against the declared policy;
  only `praetorctl sync --remote` writes it to GitHub.

## Decision

We will remove `adoption.decline` from `.standards.yaml` and commit the
engine's rendering of all three steps. We depart from the rendering only where
it cannot work for Pelorus, and only in ways Praetor documents:

- **Dev container.** Commit the rendered bundle unchanged except for its base
  image. `praetorctl devcontainer generate --base-image` selects
  `ghcr.io/vmafx/pelorus-dev@sha256:23ac69281ea469dfede2842fff58e5180cad9e39e730171a76078d8e03939883`,
  built from `.devcontainer/base/Containerfile`. That file starts from
  Praetor's reviewed default base and adds the packages the CI jobs install
  (clang 21, Meson, glslc, the FFmpeg-stack headers). It also adds
  checksum-pinned Node.js 24, actionlint 1.7.12, and Lefthook 2.1.14.
  `.gitattributes` pins `.devcontainer/**` to LF
  ([Praetor issue 313](https://github.com/cordanaLLM/praetor/issues/313)).
- **Git hooks.** Commit `lefthook.yml` as the rendering, with the pre-push
  `gate` job replaced by `make verify-native` and
  `make docs-lint docs-figures`. Praetor's Git-hook guide allows merging the
  generated jobs by hand. Commit the checkpoint scripts and the interceptor as
  rendered. Commit the checkpoint policy with `"base": "master"`. Hooks are
  installed per clone with `make hooks-install`, never by CI.
- **Branch ruleset.** Commit the rendered `.github/rulesets/main.json`
  unchanged. This change runs no `sync --remote`. Applying the ruleset stays a
  separate maintainer step, and it has two prerequisites: commit signing, and a
  decision on the review requirement. With one code owner and no bypass
  actors, the rendered review rule blocks every pull request the owner opens.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep the three declines (ADR-0145) | No new surface; nothing to publish or apply | Local audit keeps failing on the missing pre-commit hook; no reproducible environment; the declared protection policy has no committed artifact; the declines never had a maintainer decision | The maintainer chose full adoption |
| Commit the rendered `lefthook.yml` byte for byte | Adoption verifies and activates it | Its `gate` job fails on every push in a Meson repository (issue 648); the only way past a failing hook is skipping it, which the agent-hook policy denies | A hook that can never pass is not a gate |
| Keep the default dev container base image | Zero Pelorus-owned container code | `make verify-all` fails at the first Meson call; issue 75 | The container would not build Pelorus |
| Hand-edit `devcontainer.json` (features, `runArgs`, `postCreateCommand`) | No base image to publish | The audit rejects keys outside the schema and any drift from the rendering | Cannot pass the audit |
| A dev container outside `.devcontainer/` beside the rendered one | Free choice of tooling | Two environments that drift; the governed one still cannot build Pelorus | Duplicates the governed surface |
| Set `review_mode: single_maintainer` now | The ruleset would be mergeable by the owner once applied | Lowers the declared policy in a governance change without a maintainer decision | Recorded as a prerequisite of `sync --remote` instead |
| Run `sync --remote` in this change | Live protection matches the committed file at once | Forge mutation; enables signed-commit and two-review rules before commit signing and a review decision exist | Applied later by the maintainer |

## Consequences

- **Positive**: every surface the declared profile renders is committed and
  verified by the audit. The local audit passes once `make hooks-install` has
  run, and Praetor issue 175 no longer affects Pelorus. One command builds a
  container that runs every local gate: native build, fast suite,
  clang-format, clang-tidy, the build-config checker, actionlint, and the
  documentation gate. Commits and pushes run the governance and native gates.
- **Negative**: the dev container depends on a published image. Until
  `ghcr.io/vmafx/pelorus-dev` holds the recorded digest, opening the container
  fails at the base-image pull. A toolchain change means rebuilding and
  publishing the base image, then re-rendering the bundle with `--force`.
  Anyone who installs the hooks needs `praetorctl`, `lefthook`, `make`,
  Python 3, and Node.js on `PATH`, and pre-push runs the full native gate. The
  committed ruleset states a stricter policy than the one GitHub enforces
  until it is applied. A new workflow or job name changes the required checks,
  and the ruleset must be refreshed with `adopt`. Hooks install into the Git
  directory that all linked worktrees share.
- **Neutral / follow-ups**: publish the base image at the recorded digest.
  Decide the review mode, set up commit signing, then run `sync --remote` and
  read the live ruleset back. Consider a CI job that builds the dev container
  from `.devcontainer/base/Containerfile`. Drop the `lefthook.yml` deviation
  once issue 648 is fixed, and the checkpoint base edit once issue 71 is fixed.
  The GPU limits of the container are in `docs/development/build.md`.

## References

- [ADR-0145](0145-praetor-governance-adoption.md) — the adoption this ADR
  amends.
- [Research digest 0153](../research/0153-praetor-full-adoption.md) —
  rendered assets, dev container build and gate transcript, hook proof, and the
  ruleset's `sync --remote` effect.
- [Development guide](../development/build.md#dev-container) — dev container,
  hooks, and ruleset procedures.
- Praetor at `25451d888c8710822dd578907625dc69a0975142`:
  `docs/guides/devcontainer-bootstrap.md`, `docs/guides/git-hooks.md`
  ("The lefthook.yml adoption writes"), `docs/guides/review-policy.md`,
  `docs/adoption.md` ("Dry-run ruleset preview").
- Praetor issues [75](https://github.com/cordanaLLM/praetor/issues/75),
  [71](https://github.com/cordanaLLM/praetor/issues/71),
  [175](https://github.com/cordanaLLM/praetor/issues/175),
  [313](https://github.com/cordanaLLM/praetor/issues/313),
  [584](https://github.com/cordanaLLM/praetor/issues/584), and
  [648](https://github.com/cordanaLLM/praetor/issues/648).
- Source: `req`, 2026-09-30: user decision on full adoption; remove every
  `adoption.decline` entry and adopt the rendered assets.
