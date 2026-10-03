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
  Praetor engine is installed, also run `make -k verify-all`. Outside CI the
  audit fails only its final `.git/hooks/pre-commit` check
  ([Praetor issue 175](https://github.com/cordanaLLM/praetor/issues/175)), and
  `-k` lets the native and documentation gates run after it;
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

A clean clone needs Go 1.27, Node.js 22 or newer for the documentation gate,
and the exact Praetor engine pin used by CI:

```bash
go install github.com/cordanaLLM/praetor/cmd/standardsctl@bf815ba551fe326447f3161f97ba69ae5b7fe919
export PATH="$(go env GOPATH)/bin:$PATH"
go version -m "$(command -v standardsctl)" | grep -F bf815ba551fe
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

Outside CI, `make audit` fails its final check because no
`.git/hooks/pre-commit` exists while the manifest declines `git-hooks`; the
auditor ignores that decline
([Praetor issue 175](https://github.com/cordanaLLM/praetor/issues/175)). Every
earlier check must pass. `make verify-all` runs the audit before
`verify-native` and the documentation gate, so plain `make` stops there; use
`make -k verify-all` to run the remaining gates anyway. The hosted Standards job sets `CI=true`, which makes
the auditor skip only that hook check. Do not create a placeholder hook file,
weaken the manifest, or run remote sync to get past it.

The hosted job also compares your branch with its target. The recorded
baseline total may not grow unless the increase carries a recorded reason, and
the baseline must record every current finding at its current line. The exact
commands, their local equivalents, and what each one allows are in the
development guide.

`make hooks-install` stays opt-in: it installs the tracked Lefthook commands
into the Git hooks directory that every linked worktree shares. Adoption and CI
do not install Git hooks, apply repository rulesets, or run remote Praetor
sync. Adoption does register the agent pre-tool hook above in the tracked
client settings. See the development guide for the full operational notes.

## ABI changes

The `PelorusSideData` interop ABI is **append-only** (interop.h R1/R2). Adding
a field or section bumps `PELORUS_ABI_MINOR` and adds a case to the shared
conformance fixture (`libpelorus/test/interop_test.c`). A breaking change is
forbidden — mint a new section bit instead. See
[docs/adr/0103-interop-sidedata-abi.md](docs/adr/0103-interop-sidedata-abi.md).

## License

By contributing you agree your work is licensed under BSD-2-Clause-Patent
(libpelorus) / LGPL-2.1 (files that become part of FFmpeg). New wholly-new
files carry the `Copyright 2026 Lusoris` header for their tree.
