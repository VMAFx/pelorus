<!-- markdownlint-disable MD013 MD060 -->
# ADR-0219: ADR Allocator Discovers Worktrees and Local Branch Tips

- **Status**: Accepted
- **Implementation**: landed (#387)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: docs, adr, git, tooling, workflow
- **Related**: [ADR-0001](0001-project-genesis.md)

## Context

The repository uses `scripts/adr/next-free.sh` to atomically allocate the next free Architecture Decision Record (ADR) number by scanning `docs/adr/` for `[0-9]{4}-*.md` and `*.md.stub` files and returning `max + 1`.

Previously, `next-free.sh` only scanned the local `docs/adr/` directory of the single worktree where the script was executed. In multi-worktree and multi-branch development workflows, this caused number collisions:

1. When multiple git worktrees exist concurrently (e.g. parallel feature development or reviews), an untracked `.stub` file reserved in sibling worktree `dev/worktrees/pelorus-sei-fit` (ADR-0218) was invisible to the main checkout, which allocated the duplicate number 0218.
2. In-flight branches whose commits hold newly introduced ADRs (e.g. `0213` on a pushed branch or unmerged local feature branch) were not visible until merged into the current worktree's checked-out branch.

A sibling project (`vmafx`) implements a 400-line remote-aware allocator using `/tmp` lock directories, `git ls-remote`, and `.git/adr-claims` side files. That mechanism is overly complex, requires network connectivity, introduces stale locks, and performs unbounded retries.

## Decision

We update `scripts/adr/next-free.sh` to compute the next ADR number as the maximum over:

1. Every worktree of the repository: parses `git worktree list --porcelain`, finds existing `<path>/docs/adr/`, and globs `[0-9]{4}-*.md` and `*.md.stub` (prunable worktrees whose directory was deleted are safely skipped).
2. Every branch tip known locally: lists `refs/heads` and `refs/remotes` via `git for-each-ref --format='%(objectname)'`, dedupes commit SHAs, caps total refs at 2000 (failing loudly if exceeded), and scans `docs/adr/` in each commit tree using `git ls-tree --name-only <sha> -- docs/adr/`.
3. Offline execution with explicit local contract: the script makes zero network calls. Developers and agents run `git fetch origin` to update remote tracking refs before reserving a number.

In addition:

- Slug validation: `--claim <slug>` validates that `<slug>` matches `^[a-z0-9][a-z0-9-]*$`, exiting 2 on invalid slugs.
- Number validation: `--release <NNNN>` validates that `<NNNN>` matches `^[0-9]{4}$`, and exits 1 if no matching `.stub` file exists to release (replacing silent `rm -f`).
- Repository resolution: resolves the repository root via `git -C "$(dirname "$0")" rev-parse --show-toplevel`. Running outside a git repository exits 1 with an error message without fallback.
- Invariants: bash functions are limited to $\le 60$ lines (HISS-04), every loop has an explicit scalar upper bound (HISS-02), and recursion is prohibited (HISS-01).

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Port `vmafx` remote-aware allocator | Checks remote repository directly via `ls-remote` | ~400 lines of complex shell, creates lock directories in `/tmp`, requires network connectivity, unbounded retry loops | Overly heavy for Pelorus; network calls stall offline development and break airgapped test harnesses |
| `.git/` side files (e.g. `.git/adr-claims/`) | Persists claims in shared git common dir | Stale claim files persist after branch deletion; requires garbage collection mechanisms | Worktree scanning already inspects the `.stub` files directly on disk without secondary state |
| Network `git fetch` inside `next-free.sh` | Automatically brings in remote branch updates | Requires network access, slow, risks silent fallback or hangs in offline environments | Violates local-first tool design; explicit `git fetch origin` keeps allocator fast and deterministic |

## Consequences

- **Positive**: Prevents ADR number collisions across concurrent local worktrees and unmerged local/remote branches.
- **Negative**: Branch tips must be fetched locally (`git fetch origin`) to discover ADRs pushed by other machines.
- **Neutral / follow-ups**: Regression test suite added in `scripts/test-adr-next-free.py` and registered in `meson.build` fast suite under the Linux guard.

## References

- Issue [#387](https://github.com/VMAFx/pelorus/issues/387)
- User request: ADR allocator finding (worktrees and branches)
- [0000-template.md](0000-template.md)
