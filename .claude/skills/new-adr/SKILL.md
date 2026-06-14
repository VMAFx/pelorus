---
name: new-adr
description: Use when a non-trivial architectural, policy, or scope decision is being made — atomically reserve an ADR number and create the file from the template before the implementing commit lands.
---

# /new-adr

Non-trivial decisions (another engineer could reasonably have chosen
differently) get an ADR in `docs/adr/` **before** the commit that implements
them. Bug fixes and implementation details do not.

## Steps

```sh
# 1. atomically reserve the next number (creates docs/adr/NNNN-<slug>.md.stub)
N=$(scripts/adr/next-free.sh --claim <kebab-slug>)
# 2. author from the template
cp docs/adr/0000-template.md "docs/adr/$N-<kebab-slug>.md"   # then rename the stub away
mv "docs/adr/$N-<kebab-slug>.md.stub" /dev/null 2>/dev/null || rm -f "docs/adr/$N-<kebab-slug>.md.stub"
# 3. add the index row to docs/adr/README.md
# 4. if you abandon it: scripts/adr/next-free.sh --release $N
```

## Rules

- Status starts `Proposed`, flips to `Accepted` when the implementing PR merges;
  Accepted ADRs are immutable — supersede with a new ADR, don't edit the body.
- `## Alternatives considered` must list at least the runner-up (an empty table
  means it wasn't a real decision).
- Cite the user request (`req`) or popup answer (`Q<r>.<q>`) in `## References`.
- CI's `docs` job fails if an ADR lacks an index row in README.md.
