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
- **Local gate before pushing.** `meson test -C build --suite=fast` plus
  `clang-format --dry-run` + `clang-tidy` on touched files. CI re-runs the
  full gate; catch it locally first.
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
   the same PR, verified by a full series replay (`ffmpeg-patches/test/build-and-run.sh`).

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
