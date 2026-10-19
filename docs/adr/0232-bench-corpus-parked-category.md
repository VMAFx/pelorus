<!-- markdownlint-disable MD013 MD060 -->
# ADR-0232: A bench corpus category can be parked on an issue

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `bench`, `corpus`, `quality`
- **Related**: [ADR-0228](0228-bench-corpus-categories.md), [ADR-0223](0223-noisy-source-bench-method.md)

## Context

[ADR-0228](0228-bench-corpus-categories.md) left `noisy` without a pinned set, so `fetch-corpus.sh --check-categories` exits 1 on master. Issue [#413](https://github.com/VMAFx/pelorus/issues/413) searched for a permissive real-noise source: fourteen clips from the AOM CTC, SVT 2006 and SVT 2022 sets, measured with and without a temporal denoiser. None carries noise that survives into the bench workload ([research 0413](../research/0413-noisy-candidates.md)). With a gap that stays open by decision, a check that always fails says nothing: a new gap in another category would go unseen.

## Decision

1. A row of `scripts/bench/corpus.categories` may read `parked #<issue>` in its clips column. `--check-categories` prints `PARKED <category>: no pinned set, #<issue> (used by <x>)` for it and does not fail; a full fetch prints the same line on stderr.
2. Only the exact form `parked #<digits>` parks a row. `parked` without an issue number, or with anything after the number, is read as a clip name and fails as `UNKNOWN CLIP`. An empty clips column still fails as `MISSING`.
3. `noisy` is parked on #413. The #198 denoise proof on real noise keeps no pinned clip, and [ADR-0223](0223-noisy-source-bench-method.md) stays partially implemented.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Keep `MISSING noisy` and the exit 1 | No format change | The check fails on master forever, so a new gap is not seen | Hides regressions |
| Remove the `noisy` row | Check passes | The gap disappears from the coverage record | Silent drop |
| Pin an SVT 2006 clip | Film-scan content | Terms limit use to standards work, and no clip passes the 30 % drop | Licence and measurement |
| Generate synthetic noise | Permissive, reproducible | Not real capture; the reason [ADR-0223](0223-noisy-source-bench-method.md) exists | Method |

## Consequences

- **Positive**: `--check-categories` exits 0 on master again and fails on any new gap. A parked row names the issue that holds the search.
- **Negative**: a parked category can be forgotten; the issue must stay open while the row is parked.
- **Neutral / follow-ups**: a source that passes the 30 % drop with a permissive licence replaces `parked #413` with its lock name. Candidates not measured: Netflix Open Content masters (CC BY 4.0, hundreds of gigabytes each).

## References

- Issue: VMAFx/pelorus#413; earlier search: VMAFx/pelorus#135.
- Research: [docs/research/0413-noisy-candidates.md](../research/0413-noisy-candidates.md).
- Tests: `scripts/test-fetch-corpus.py` (parked row passes; `parked`, `parked #`, `parked #413x` fail).
