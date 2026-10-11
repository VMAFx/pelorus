<!-- markdownlint-disable MD013 -->
# Research 0187: Praetor re-pin `9615f1b9` → `7cf9cbb2` measurements

Measurements behind
[ADR-0187](../adr/0187-praetor-engine-7cf9cbb-engine-launcher.md), taken on
2026-10-11 against `master` at `12985e1` in a linked worktree.

## Setup

| Item | Value |
| --- | --- |
| Engine | `7cf9cbb2d8cce4000bde5ba2cf2a5b770fc90712` (`praetorctl version 7cf9cbb2d8cc`) |
| Previous pin | `9615f1b907cc7e677a3ae71f6e864deb4450b9de` (14 squash commits earlier) |
| Lock source | local clone of `cordanaLLM/praetor`, detached at the new pin |
| Toolchain | Go 1.27.1, Node.js 26.11.1, Meson 1.12.1, Ninja 1.13.2, GCC 16.2.1, LLVM/clang-tidy 23.1.1, glslc 2026.4, reuse 6.2.0 |

## Procedure

```bash
git worktree add <wt> -b chore/praetor-repin-7cf9cbb origin/master && cd <wt>
praetorctl adopt --dry-run --path .                                    # plan, no --force
praetorctl adopt --dry-run --force --lock-source-root=<praetor> --path .
praetorctl adopt --force --lock-source-root=<praetor> --path .
git checkout -- .zed/settings.json       # keep the Pelorus file, as ADR-0154 and ADR-0168 did
# by hand: PRAETOR_REF and its mentions, Makefile, lefthook.yml, AGENTS.md project part
git rm scripts/praetor-engine.sh
make compile-context
```

No Git hook was installed. A listing of the shared hooks directory was the same
before and after the run.

## Unchanged tree, old and new engine

| Command | `9615f1b9` | `7cf9cbb2` |
| --- | --- | --- |
| `compile-context --verify` | exit 0 | exit 0, with `[WARN] AGENTS.md carries no cache band markers` |
| `baseline --verify` | exit 0 | exit 0: 42 active findings within the 51 recorded; 9 baseline entries match nothing in the tree |
| `audit` | exit 0 | exit 1: `Documentation gate asset tools/markdownlint/package.json holds an earlier Praetor text; run 'praetorctl adopt' to refresh it` |
| `audit`, HISS-18 | 10 findings in 5 of 8 workflows, warning | not reached |
| `editors verify --path .` | exit 1 | exit 1: `.vscode/settings.json is missing managed standards policy` |

The harness text on `master` said "6 open findings (5 in `ci.yml`, 1 in
`standards-gate.yml`)" for HISS-18. The old engine already counts 10: `ci.yml`
6, and one each in `devcontainer-image.yml`, `reuse.yml`, `standards-gate.yml`
and `tester-publish.yml`. The new text states 10.

## What `adopt --force` writes

The forced plan lists 8 files to create and 9 to replace, and prints 19
warnings.

| Path | Change |
| --- | --- |
| `.config/lefthook/engine.sh`, `.config/praetor/engine.mk` | new: launcher and make fragment |
| `Makefile` | `-include .config/praetor/engine.mk` inserted above the existing `PRAETORCTL ?=` line |
| `AGENTS.md` | harness block replaced: 117 → 102 lines, file 270 → 255 lines |
| `.devcontainer/` | bundle re-rendered from the new engine source |
| `.github/workflows/praetor-docs.yml` | `actions/setup-node` v7.0.0 → v7.1.0 |
| `tools/markdownlint/package.json`, `package-lock.json` | `js-yaml` 5.4.2 → 5.4.3 |
| `.config/agent/hooks/block_evasion.py` | refreshed |
| `caveman`, `social-text`, `adhd-format` skills (two trees) | `SKILL.md` refreshed; six new `LICENSE` files |
| `.zed/settings.json` | keys reordered, Go language block added; reverted |
| `lefthook.yml` | kept: `existing lefthook.yml differs from the scaffold adoption writes … kept, --force included, and not activated` |
| `.standards.lock`, `.standards-baseline.json`, `PRAETOR_REF` | unchanged |

`tools/figures/dist/` is reported as preserved; no file there is removed.

After the forced run and before any hand edit: `compile-context --verify`,
`baseline --verify`, `audit`, `python3 scripts/check-build-config.py` and
`reuse lint` (594 of 594 files) all exit 0. `editors verify --path .` still
exits 1 with the same message, while the adoption report says
`.vscode/settings.json: Existing vscode IDE configuration holds every managed
value`.

## Two launchers after a plain adoption

The inserted include makes `make` resolve the engine through the engine's
fragment, because the fragment's `PRAETORCTL ?=` comes first. The adoption
report says so:

```text
! Makefile line 4: "PRAETORCTL ?= sh scripts/praetor-engine.sh" defines PRAETORCTL with ?=; shadowed by .config/praetor/engine.mk (use := or = to override)
```

`lefthook.yml` is not rewritten, so its eight jobs kept calling
`scripts/praetor-engine.sh`. Both read the same `PRAETOR_REF`.

| Property | `scripts/praetor-engine.sh` (retired) | `.config/lefthook/engine.sh` |
| --- | --- | --- |
| Pin source | `PRAETOR_REF` in `standards-gate.yml` | `PRAETOR_REF` under `.github/workflows`; refuses when several values are declared |
| Cache | `.workingdir/bin/<pin>/`, per worktree | `${XDG_CACHE_HOME:-$HOME/.cache}/praetor/engine/<pin>/`, per user |
| First use | `go install` | `go install` into a scratch directory, files renamed into place; `PRAETOR_ENGINE_INSTALL_TIMEOUT` seconds, default 300 |
| Cached binary checked against the pin | yes (`version` output) | no; the directory name is the only link to the pin |
| `PRAETORCTL=<path>` | honoured, with a notice | not read; `make` honours it through `engine.mk`, without a notice |
| No pin declared | exit 1 | runs the engine on `PATH` with a notice |
| `--print-path` | yes; installs first when the cache is empty | the same |

On the final tree, `make praetor-engine-path` prints
`~/.cache/praetor/engine/7cf9cbb2d8cce4000bde5ba2cf2a5b770fc90712/standardsctl`,
and `make -n compile-context-verify` shows that path.

## The forced harness block

The block on `master` was hand-kept since ADR-0154. Its HISS table had a
column naming the check that decides each rule in this repository. The forced
block has cache band markers at lines 4, 63 and 81 and ends at line 102. All
21 rows read `not enforced` and `advisory`. The adoption report describes the
replacement as:

```text
! [REPL] AGENTS.md : Refreshed Praetor Agent Operating Harness; kept repository-specific instructions; replaced existing content (-66/+51 lines, …
```

The text below the block (the project contract) is kept byte for byte. The
per-rule facts and the engine-binary paragraph that sat inside the old block
are gone after the run. They were written again as the section "Enforcement
here" in the project part.

| State | `AGENTS.md` lines | Limit |
| --- | --- | --- |
| `master` | 270 | 300 |
| after `adopt --force` | 255 | 300 |
| with "Enforcement here" and the launcher edits | 285 | 300 |

`praetorctl caveman check` on the final `AGENTS.md` reports 1681 prose words
and 0 findings.

## Not changed by the adoption

`adopt` does not move `PRAETOR_REF`. The pin was edited in
`.github/workflows/standards-gate.yml`, and its mentions in `AGENTS.md`,
`CONTRIBUTING.md`, `docs/development/build.md` and `README.md`.
