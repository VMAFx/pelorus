<!-- markdownlint-disable MD013 -->
# Contributing to Pelorus

Pelorus is the GPU pre-encode sibling of [vmafx](https://github.com/VMAFx/vmafx)
and follows the same engineering contract. Read [AGENTS.md](AGENTS.md) and
[docs/principles.md](docs/principles.md) before opening a PR.

## The short version

- **Branch + PR.** Never commit to `master`; never force-push it. Squash or
  fast-forward merge only.
- **Conventional Commits.** `type(scope): subject`. Types: `feat, fix, perf,
  refactor, docs, test, build, ci, chore, revert`. `!` / `BREAKING CHANGE:`
  for breaks. Branches: `type/<slug>`.
- **Local gate before pushing.** Run `make verify-native`. If the pinned
  Praetor engine is installed, also run `make verify-all`;
  see [Governance and agent contexts](#governance-and-agent-contexts) below.
- **Lint-clean touched files.** Every file your PR touches leaves the tree
  warning-clean to `-Wall -Wextra -Werror` and clang-tidy. A `// NOLINT` needs
  an inline citation (ADR / research digest / load-bearing invariant).

## Mandatory per-PR deliverables

These mirror vmafx's rules; reviewers verify them.

1. **ADR** for any non-trivial architectural / policy / scope decision —
   `docs/adr/NNNN-kebab.md`, Nygard format, landed **before** the implementing
   commit. Run `scripts/adr/next-free.sh --claim <slug>` to reserve the number.
   Cite the user request (`req`) or popup answer (`Q<r>.<q>`) in `## References`.
2. **Per-surface docs** — any user-discoverable surface (a filter, an AVOption,
   a public `libpelorus` API, an interop section, a `meson_options.txt` flag)
   ships human-readable docs under `docs/` in the **same PR**. No docs =
   unmergeable. See [docs/adr/0100-doc-substance-rule.md](docs/adr/0100-doc-substance-rule.md).
3. **Six deep-dive deliverables** for fork-local PRs — research digest,
   decision matrix (the ADR's alternatives table), `AGENTS.md` invariant note,
   reproducer command in the PR body, `changelog.d/<section>/<topic>.md`
   fragment, and a `docs/rebase-notes.md` entry when the change affects the
   FFmpeg patch stack. See [docs/adr/0108-deep-dive-deliverables-rule.md](docs/adr/0108-deep-dive-deliverables-rule.md).
4. **Patch-stack sync** — a change to any `libpelorus` surface the FFmpeg
   patches consume updates `ffmpeg-patches/files/` + the regenerated patch in
   the same PR, verified by a full series replay
   (`FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh`).

## Governance and agent contexts

`AGENTS.md` is the canonical cross-tool repository guide. Reviewer personas
under `.agents/agents/` are canonical too. Generated cross-tool contexts include
root `CLAUDE.md`, editor/agent projections, and persona copies; do not edit them
directly. Paperclip's separately generated harness/rules pair needs explicit
consumer reconciliation. The complete ownership map, target composition,
baseline semantics, and failure triage are in
[docs/development/build.md](docs/development/build.md#repository-verification-entry-points).

A clean clone needs Go 1.27, Node.js 22.12 or newer for the documentation gate,
and the exact Praetor engine pin used by CI:

```bash
go install github.com/cordanaLLM/praetor/cmd/standardsctl@492a00f930e1a2df557ffebb76564cfa65637b77
export PATH="$(go env GOPATH)/bin:$PATH"
go version -m "$(command -v standardsctl)" | grep -F 492a00f930e1
```

`standardsctl version` names the build commit, and the module
pseudo-version that `go version -m` prints names it too; CI checks both. The pin
installs a binary named `standardsctl`. Praetor's own text, including the
generated README block, calls the same program `praetorctl`; the `Makefile`
uses whichever of the two it finds first on `PATH`.

The agent hooks call it by the name `praetorctl` only. If you run Claude Code,
Codex, or Gemini CLI in this repository, put a copy under that name next to
the installed binary:

```bash
cp "$(go env GOPATH)/bin/standardsctl" "$(go env GOPATH)/bin/praetorctl"   # Windows: standardsctl.exe to praetorctl.exe
```

`.claude/settings.json`, `.codex/hooks.json`, and `.gemini/settings.json` run
`praetorctl hook <client> pre-tool` before every agent shell command. It blocks
commands that would skip or remove Git hooks (such as `--no-verify`) and
allows the rest. Without `praetorctl` on `PATH` the hook fails with a
command-not-found error that Claude Code and Gemini CLI report without blocking,
so agent commands then run without that policy. The development guide has the
rule list, a smoke test, and the Windows notes.

```bash
make compile-context          # regenerate vendor projections
make compile-context-verify   # reject projection drift
make audit                    # apply the pinned HISS baseline ratchet
make docs-lint docs-figures   # locked Markdown and figure checks
make verify-all               # context + audit + native Pelorus gate + docs gate
```

The manifest declines no adoption step
([ADR-0153](docs/adr/0153-praetor-full-adoption.md)), so a local audit
requires the Lefthook pre-commit hook (`make hooks-install`) and, when it finds
a forge token, compares the live `master` protection with the committed
ruleset. That comparison fails until the maintainer applies the ruleset;
`make audit AUDIT_FLAGS=--offline` skips it, and CI has no token. Every other
audit check must pass. Do not create a placeholder hook file, weaken the
manifest, or run remote sync to get past a failing check.

The audit also reads two lists that a code change can break. Every hosted
`meson setup` passes `--werror`, because the audit does not read Meson's
`default_options`. Every C file the Meson build compiles is listed in
`.config/clang-tidy/lane-files.txt`, which `make tidy` and the `core` job lint;
a new unit that is not listed fails the audit
([ADR-0168](docs/adr/0168-praetor-engine-492a00f.md)).

The hosted job also compares your branch with its target. The recorded
baseline total may not grow unless the increase carries a recorded reason, and
the baseline must record every current finding at its current line. The exact
commands, their local equivalents, and what each one allows are in the
development guide.

`make hooks-install` stays opt-in: it installs the tracked Lefthook hooks
into the Git hooks directory that every linked worktree shares. A commit then
runs the context check and two offline audits; a push runs the audit, the
flavor audit, the Praetor gate, and `make verify-all`. `lefthook.yml` sets
`no_auto_install`, so rerun `make hooks-install` after it changes. CI does not
install Git hooks, apply the ruleset, or run remote Praetor sync; the
maintainer applies `.github/rulesets/main.json` with `praetorctl sync --remote`.
Adoption does register the agent pre-tool hook above in the tracked client
settings. The development guide covers the hooks, the ruleset, and the dev
container (`.devcontainer/`).

## ABI changes

The `PelorusSideData` interop ABI is **append-only** (interop.h R1/R2). Adding
a field or section bumps `PELORUS_ABI_MINOR` and adds a case to the shared
conformance fixture (`libpelorus/test/interop_test.c`). A breaking change is
forbidden — mint a new section bit instead. See
[docs/adr/0103-interop-sidedata-abi.md](docs/adr/0103-interop-sidedata-abi.md).

## License

A contribution falls under the licence of the file it changes; a file says
which in its `SPDX-License-Identifier` line or, without one, in
[`REUSE.toml`](REUSE.toml) ([ADR-0171](docs/adr/0171-eupl-relicense.md),
[docs/licensing.md](docs/licensing.md)):

- **A new file, or a change to a Pelorus file**: [EUPL-1.2](LICENSES/EUPL-1.2.txt),
  a reciprocal licence. By contributing you license your contribution under
  the EUPL-1.2. A new C, shell or Python file starts with the copyright line
  and the SPDX line, in the comment syntax of its language:

  <!-- REUSE-IgnoreStart -->
  ```c
  /*
   * Copyright 2026 Lusoris
   *
   * SPDX-License-Identifier: EUPL-1.2
   */
  ```
  <!-- REUSE-IgnoreEnd -->

  Name yourself in the copyright line of a file you create; files without a
  header of their own are covered by `REUSE.toml`.
- **A file the patch stack adds to FFmpeg** (`ffmpeg-patches/files/`, and a
  test built inside the FFmpeg tree): LGPL-2.1-or-later, with FFmpeg's LGPL
  header, so the file can join FFmpeg.
- **Vendored files** (`.claude/skills/superpowers/`,
  `tools/figures/third_party/`): their upstream licence. Change them only by
  re-vendoring.

A new file that needs other terms gets a table in `REUSE.toml`, after the
whole-tree default. `reuse lint` (the `reuse-lint` pre-commit job and the
`REUSE lint` CI check) must pass.

### Developer Certificate of Origin

Every commit carries a sign-off: `git commit -s` appends
`Signed-off-by: Your Name <you@example.org>`. It states, under the
[Developer Certificate of Origin 1.1](https://developercertificate.org/), that
you wrote the change or have the right to submit it under the licence of the
files it touches. There is no contributor licence agreement. Bot commits
(Renovate) are exempt. The `commit-msg` Lefthook job (installed with `make hooks-install`) checks the trailer locally, and the `Standards` workflow checks every pull request commit in its "Verify DCO sign-off" step, so an unsigned commit fails the pull request.
