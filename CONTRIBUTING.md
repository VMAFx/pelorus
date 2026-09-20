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
  Praetor engine is installed, also run `make verify-all`; the full gate
  currently reports the accepted `branch-ruleset` decline as an upstream audit
  failure tracked by [ADR-0145](docs/adr/0145-praetor-governance-adoption.md).
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

A clean clone needs Go 1.27 and the exact Praetor engine pin used by CI:

```bash
go install github.com/cordanaLLM/praetor/cmd/standardsctl@846da5908d15b3cf5581ca6b0205cc644b249599
export PATH="$(go env GOPATH)/bin:$PATH"
standardsctl version
```

```bash
make compile-context          # regenerate vendor projections
make compile-context-verify   # reject projection drift
make audit                    # apply the pinned HISS baseline ratchet
make verify-all               # context + audit + native Pelorus gate
```

Until [Praetor issue 408](https://github.com/CordanaLLM/praetor/issues/408) is
fixed, `make audit` and `make verify-all` are expected to stop on the explicitly
declined branch-ruleset artifact after the preceding checks pass. Do not add a
local bypass.

Do **not** run `make hooks-install` for normal work while issue 408 is open: its
pre-commit and pre-push gates reach the known audit failure and block every
commit/push. If already installed, run `lefthook uninstall`; linked worktrees
share that hooks directory. Adoption and CI do not install hooks, apply
repository rulesets, or run remote Praetor sync. See the development guide for
the full operational warning.

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
