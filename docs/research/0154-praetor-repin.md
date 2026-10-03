<!-- markdownlint-disable MD013 -->
# Research 0154: Praetor re-pin `25451d88` → `0af07a73` measurements

Measurements behind [ADR-0154](../adr/0154-praetor-engine-repin.md),
taken on 2026-10-03 against `master` at `f65b167`.

## Setup

| Item | Value |
| --- | --- |
| Engine | `0af07a733e6534269b435cea185da4d1df7aba0c`, Go module version `v0.0.0-20261003144447-0af07a733e65` |
| Previous pin | `25451d888c8710822dd578907625dc69a0975142` (53 commits earlier) |
| Go | 1.27.1 (same as the hosted job) |
| Node.js | 26.10 locally; the hosted documentation job uses 24 |

## Procedure

The upgrade runs in a throwaway clone, so no command touches the shared `.git`
directory of the main checkout or its worktrees:

```bash
git clone --no-local <pelorus> pel-bump && cd pel-bump
praetorctl adopt --force --dry-run --lock-source-root=<praetor at 0af07a73> --path .
praetorctl adopt --force --lock-source-root=<praetor at 0af07a73> --path .
git checkout -- AGENTS.md .zed/settings.json      # reconcile, see ADR-0154
praetorctl compile-context                        # updates the register block only
praetorctl baseline --record --allow-increase --reason '<ADR-0154 reason>'
praetorctl adopt --path .                         # refreshes the README governance block
```

The hosted job, reproduced with a `go install`ed engine:

```bash
go install github.com/cordanaLLM/praetor/cmd/standardsctl@0af07a733e6534269b435cea185da4d1df7aba0c
standardsctl version                               # praetorctl version 0af07a733e65
go version -m "$(command -v standardsctl)" | grep -F 0af07a733e65
standardsctl compile-context --verify              # 32 persona projections in sync
CI=true standardsctl audit --base origin/master --touched-debt-delta-reason '<reason>'   # exit 0, HISS-13 WARN
standardsctl baseline --verify                     # 99 within 99
```

## Baseline delta

| Rule | `25451d88` | `0af07a73` | Cause |
| --- | --- | --- | --- |
| HISS-01 | 31 | 31 | — |
| HISS-02 | 1 | 2 | `scripts/bench/fetch-corpus.sh`: `curl` with no deadline |
| HISS-04 | 28 | 28 | — |
| HISS-07 | 2 | 38 | shell scripts now scanned (`\|\| true`) |
| **Total** | **62** | **99** | |

The first forced adoption reported 105 active findings against the old
baseline. Re-recording under the new rule/file/function keys collapses that to
99. The 36 new HISS-07 sites:

| File | Sites | Pattern |
| --- | --- | --- |
| `ffmpeg-patches/generate.sh` | 19 | 18× `mv <glob> <name> 2>/dev/null \|\| true` patch rename, 1× `am --abort` cleanup |
| `ffmpeg-patches/test/build-and-run.sh` | 8 | 7× diagnostic `tail -80 … \|\| true`, 1× `am --abort` cleanup |
| `.codex/hooks/*.sh` | 6 | JSON payload parse and `clang-format` fallbacks in agent hooks |
| `ffmpeg-patches/test/qsv-roi-regression.sh` | 1 | `am --abort` cleanup |
| `ffmpeg-patches/test/vulkan-format-matrix.sh` | 1 | `grep -Eo … \| sort -u \|\| true` |
| `scripts/release/concat-changelog-fragments.sh` | 1 | diagnostic `diff -u … \|\| true` |

Two of the 37 are defects rather than style. `fetch-corpus.sh` downloads
without `--fail` or `--max-time`, so a dead URL writes an HTTP error page where
the corpus belongs. The `generate.sh` rename loop swallows a failed `mv`, so
renamed `format-patch` output can leave a stale patch in place.

## Documentation-gate dependencies

`npm audit --package-lock-only` on `tools/markdownlint/package-lock.json`:

| Lockfile | Advisories |
| --- | --- |
| `25451d88` | 8 (6 high: `braces`, `smol-toml` and dependents; 2 moderate: `js-yaml` 5.2.2, `markdown-it` 14.3.0) |
| `0af07a73` | 0 |
