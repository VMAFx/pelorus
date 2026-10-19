<!-- markdownlint-disable MD013 MD060 -->
# ADR-0228: Bench corpus categories with pinned, licence-checked sets

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `bench`, `corpus`, `quality`, `licence`
- **Related**: [ADR-0111](0111-benchmark-methodology.md), [ADR-0223](0223-proven-filters-milestone.md)

## Context

Issue [#135](https://github.com/VMAFx/pelorus/issues/135): the proof items of the "Ongoing — Proven filters" milestone each need a content class (banded sky, anime line art, re-transcoded footage, hard cuts, high motion, sensor noise), but `scripts/bench/corpus.lock` held only `bbb`, `netflix-bar` and `synth-banding`. Nothing recorded which class a proof needs or which pinned set covers it, so a missing class was found only when a proof was attempted. `fetch-corpus.sh` also reported failures without the entry name, and a failed extraction left a partial `.yuv` behind that the next run took as ready.

Each new set must be downloadable by anyone who runs the bench, pinned by sha256, and carry a licence that allows local use for measurement. The extracted YUV is never redistributed.

## Decision

1. `scripts/bench/corpus.categories` maps each category to the pinned clips that cover it and to the proof items that use it (`category | clips | used by`).
2. `fetch-corpus.sh --check-categories` checks that map against `corpus.lock` without downloading: it exits 1 when a category has no set or names a clip the lock does not hold. A full fetch prints the same `MISSING` lines on stderr and keeps its exit status.
3. Five sets are pinned, each with its licence quoted from the copyright file next to the download (read 2026-10-19): `bluesky` (real banding, no restrictions), `news-mixedcoding` (re-transcode, CC BY-SA 4.0), `glasshalf` (anime, CC BY 4.0), `sintel-trailer` (multi-shot, CC BY 3.0), `svt-waterfall` (high motion, CC BY 4.0). Every set uses the existing 640x360 yuv420p 48-frame workload shape.
4. `noisy` stays without a set and is reported `MISSING` until a permissive source keeps its noise through the bench's scaling.
5. `netflix-bar` (CC BY-NC-ND 4.0) stays in the lock as before but belongs to no category; whether to keep it is a separate decision.
6. Every fetch failure names the entry (`<KIND>: <name> (<detail>)`), and a failed extraction removes its partial output.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Netflix Chimera/El Fuente sets for real camera and noise | Native 4K camera content with grain | CC BY-NC-ND 4.0 | Non-commercial, no-derivatives terms; not added as category sets |
| SVT 2006 sets (ParkJoy, CrowdRun) | Classic high-motion and texture content | Terms limit use to standards work | Licence |
| NTIA/ITS and UVG sets | Wide content range | Research-only, or CC BY-NC | Licence |
| SVT 2022 `smoke_sauna` proxy for `noisy` | CC BY 4.0 | 1 MB AV1 preview of 2.6 s; the noise is gone, and scaling to 640x360 averages the rest | Does not carry noise |
| Caminandes for anime | CC BY | Distributed as zip; `fetch-corpus.sh` reads media files only | Format |
| Per-filter ad hoc clips | No shared file | Same gap found again per proof | No coverage check |

## Consequences

- **Positive**: seven of eight categories have a pinned set; a proof that needs a class can name it, and `--check-categories` reports a gap before any download. A cold fetch of the whole lock completes with every sha256 verified.
- **Negative**: a cold full fetch downloads about 1 GB (`glasshalf` y4m alone is 404 MB). Category sets can still disappear upstream; the sha256 pin makes that a named `DOWNLOAD FAILED` or `SHA MISMATCH`, not silent drift.
- **Neutral / follow-ups**: a `noisy` set needs either a permissive native-resolution noisy source or a crop path in `fetch-corpus.sh`; until then the #198 denoise proof on real noise has no pinned clip and ADR-0223 stays partially implemented. Backups found during the search: Noise_Animation (CC BY-SA, paper texture) for `anime`, FourPeople (public domain) for `retranscode`.

## Update 2026-10-19

Decision 5 is settled: `netflix-bar` stays pinned by URL and sha256 only. Its copyright file (`media.xiph.org/video/derf/Chimera/Netflix_BarScene_Copyright.txt`, read 2026-10-19) states CC BY-NC-ND 4.0, so it is measured locally and nothing derived from it (extracted frames, crops, encodes) is committed or published. The same rule holds for every set, CC BY-SA ones included.

`corpus.lock` gains a ninth column, `licence`: an SPDX identifier, `no-copyright` or `generated`. `fetch-corpus.sh` reads it from an allow-list (`CC-BY-3.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `CC-BY-NC-ND-4.0`, `no-copyright`, `generated`) and stops with `NO LICENCE PINNED` or `UNKNOWN LICENCE` before any download, so a new set cannot enter the lock without a recorded licence. `scripts/test-fetch-corpus.py` covers a missing, an empty and an unknown licence, and requires an allowed licence on every line of the shipped lock.

## References

- Issue: VMAFx/pelorus#135.
- Research: [docs/research/0135-corpus-categories.md](../research/0135-corpus-categories.md).
- Tests: `scripts/test-fetch-corpus.py` (named failures, extraction cleanup, category check).
