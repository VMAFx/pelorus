<!-- markdownlint-disable MD013 MD060 -->
# ADR-0187: Re-pin the Praetor engine to `7cf9cbb2`, run it through the engine's launcher, and take the engine's harness block

- **Status**: Accepted
- **Date**: 2026-10-11
- **Deciders**: Lusoris
- **Tags**: governance, ci, standards, build
- **Supersedes**: the engine pin of [ADR-0168](0168-praetor-engine-492a00f.md)
  and its decision to keep the Pelorus `AGENTS.md` harness; the rest of
  ADR-0168 stays Accepted

## Context

The pin stood at `9615f1b907cc7e677a3ae71f6e864deb4450b9de`. On 2026-10-11
upstream `main` was 14 squash commits further, at
`7cf9cbb2d8cce4000bde5ba2cf2a5b770fc90712`. Three things changed for Pelorus in
that range:

- **The engine ships its own launcher.** `praetorctl adopt` now writes
  `.config/lefthook/engine.sh` and `.config/praetor/engine.mk` beside a
  `lefthook.yml` it does not own (engine pull request 1229). Both resolve the
  commit that `PRAETOR_REF` pins, install it once per pin and never fall back
  to `PATH`. Pelorus had solved the same problem itself in
  `scripts/praetor-engine.sh` (issue 136, pull request 240). With both in the
  tree, `make` and the Git hooks reached the same pin through two launchers and
  two caches.
- **`AGENTS.md` wants cache band markers.** `compile-context --verify` warns
  that the file carries no `<!-- praetor:head -->`, `<!-- praetor:config -->`
  and `<!-- praetor:tail -->` markers. Only the engine's own rendering of the
  harness block has them.
- **The engine's harness block says nothing about Pelorus.** ADR-0154 and
  ADR-0168 kept a hand-written harness block whose HISS table named the check
  that decides each rule here. `adopt --force` replaces that block: all 21 rows
  then read "not enforced, advisory", because the engine credits only checks it
  generated itself and Pelorus keeps its own `Makefile` and `lefthook.yml`.

The measurements are in
[research digest 0187](../research/0187-praetor-repin-7cf9cbb.md).

## Decision

Pin Praetor `7cf9cbb2d8cce4000bde5ba2cf2a5b770fc90712` in
`.github/workflows/standards-gate.yml` and in every document that quotes the
pin, and take the output of
`adopt --force --lock-source-root=<praetor at the pin>` with these rules:

- **One launcher.** Delete `scripts/praetor-engine.sh`. The eight Lefthook
  jobs call `sh .config/lefthook/engine.sh <arguments>`, and the `Makefile`
  includes `.config/praetor/engine.mk`, which sets `PRAETORCTL` to the path the
  launcher resolves. The include is not optional (`include`, not `-include`):
  a tree without the fragment fails instead of running an unset command.
- **Engine block as rendered, Pelorus facts below it.** `AGENTS.md` keeps the
  engine's harness block unedited. The per-rule enforcement facts and the
  engine-binary notes move into the Pelorus part of the file, as the section
  "Enforcement here". The file is 285 lines, under the 300-line budget that
  `compile-context` enforces.
- **Direct engine calls go through the launcher.** The command table in
  `AGENTS.md` and the contributor docs name `make` targets or
  `sh .config/lefthook/engine.sh <arguments>`, not a binary on `PATH`.
- **Keep `.zed/settings.json`**, as ADR-0154 and ADR-0168 did. The engine's
  merge reorders keys and adds a Go block; Pelorus has no Go.
- **Keep the baseline.** `baseline --verify` passes at the new pin without a
  re-record: 42 active findings within the 51 recorded.
- **Accept the rest of the adoption output**: the refreshed dev container
  bundle, `actions/setup-node` v7.1.0 in `praetor-docs.yml`, `js-yaml` 5.4.3 in
  the Markdown gate (the audit requires it), `block_evasion.py`, and the three
  register skills with their licence files.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Engine launcher only, Pelorus launcher deleted | One implementation, maintained upstream; one cache per pin for every worktree and every adopted repository on the machine | Loses two Pelorus features (below) | **Chosen** |
| Keep `scripts/praetor-engine.sh` and ignore the engine's files | Keeps the `version` check and the `PRAETORCTL` override for hooks | `adopt` writes both engine files on every run and inserts the include, so `make` moves to the engine launcher anyway; two launchers for one behaviour | Splits `make` from the hooks |
| Keep both, `PRAETORCTL :=` in the `Makefile` to win over the fragment | No behaviour change | The engine fragment stays in the tree unused; every adoption run warns about the shadowing | Dead managed file |
| Keep the hand-written harness block (ADR-0168 practice) | Enforcement facts stay in the first table an agent reads | No cache band markers, so the warning stays; the block must be restored by hand after every `adopt --force`; the engine may stop recognising it | Fights the engine on every re-pin |
| Take the engine block and drop the Pelorus facts | Smallest file | An agent reads "not enforced" for rules that the audit, clang-tidy and CI do decide here | States less than is true |
| Stay on `9615f1b9` | No work now | No engine launcher fix, no cache bands; the distance to upstream grows (issue 209) | Debt compounds |

## Consequences

- **Positive**: one launcher and one engine cache
  (`${XDG_CACHE_HOME:-$HOME/.cache}/praetor/engine/<pin>/`), shared by every
  worktree. A new worktree no longer installs its own engine copy. A re-pin
  needs no `git checkout -- AGENTS.md` step. The harness block has cache band
  markers.
- **Negative**: the engine launcher does not compare the cached binary's
  `version` with the pin, which the Pelorus script did; a damaged or replaced
  cache file is found by the hosted `Standards` job only. The hooks no longer
  honour `PRAETORCTL=<path>`; only `make` does, and it prints no notice. The
  enforcement table now sits below the harness block and must be kept current
  by hand, as before.
- **Neutral / follow-ups**: HISS-18 reports 10 warnings in 5 of 8 workflows
  at the old and the new pin; the harness text said 6 and was out of date
  (issue 207). The audit at the new pin fails on an unadopted tree
  (`tools/markdownlint/package.json holds an earlier Praetor text`), so the
  Markdown gate refresh is required, not optional.
  `praetorctl editors verify --path .` exits 1 on
  `.vscode/settings.json` at the old and the new pin; no gate runs it (Praetor
  issue 1230). The engine credits no Pelorus check in its own table as long as
  `lefthook.yml` carries Pelorus jobs (Praetor issue 1104). `adopt` does not
  move `PRAETOR_REF`; the pin and its mentions remain a hand edit.

## References

- [ADR-0145](0145-praetor-governance-adoption.md),
  [ADR-0154](0154-praetor-engine-repin.md),
  [ADR-0168](0168-praetor-engine-492a00f.md), and
  [research digest 0187](../research/0187-praetor-repin-7cf9cbb.md).
- Praetor `9615f1b9..7cf9cbb2` on `github.com/cordanaLLM/praetor`; engine guide
  `docs/guides/context-cache-bands.md` at the pin; Praetor issues 1104, 1230
  and 1231.
- Source: fleet controller answer `pelorus-engine-launcher`, recorded
  2026-10-11, on which launcher the hooks and the `Makefile` use (paraphrased):
  retire the repository's own launcher, run both through the engine's launcher
  and make fragment, and move the repository's enforcement facts into its own
  part of `AGENTS.md`.
