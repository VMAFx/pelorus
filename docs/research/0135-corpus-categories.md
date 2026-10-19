<!-- markdownlint-disable MD013 MD060 -->
# Bench corpus categories: candidate sets and their licences

**Date:** 2026-10-19

**Decision:** [ADR-0228](../adr/0228-bench-corpus-categories.md), issue [#135](https://github.com/VMAFx/pelorus/issues/135).

**Scope:** public test-sequence collections at `media.xiph.org` (AOM CTC test set, Derf collection, SVT 2022 suite) and `download.blender.org`, checked on 2026-10-19 for a set per category in `scripts/bench/corpus.categories`.

## Verdict

Seven of eight categories have a pinned set with a licence that allows local measurement use. `noisy` has none: every permissive candidate either loses its noise in distribution coding or in the bench's scaling to 640x360.

## Method

1. For each candidate, the copyright file next to the media (`<name>_Copyright.txt` in the AOM CTC tree, the SVT 2022 suite PDF, the Blender sharing page) was read; the licence column below quotes it.
2. Each chosen file was downloaded, its sha256 recorded in `scripts/bench/corpus.lock`, and its stream parameters read with `ffprobe`.
3. A cold run of `scripts/bench/fetch-corpus.sh` into an empty directory fetched and extracted every entry of the lock; `--check-categories` reported the coverage.
4. For `sintel-trailer`, `scdet=threshold=10` over the whole trailer located the hard cuts; the window `-ss 26.5 -t 2` holds the cuts at 27.42 s and 28.00 s.

## Chosen sets

| Category | Set | Source file | Licence (quoted file) | Stream |
| --- | --- | --- | --- | --- |
| `real-banding` | `bluesky` | AOM CTC `a4_360p/BlueSky_360p25_v2.y4m` | "No restrictions", "No Copyright" (`BlueSky_360p25_Copyright.txt`) | 640x360 yuv420p 25 fps, 5.2 s |
| `retranscode` | `news-mixedcoding` | AOM CTC `e_nonpristine/MixedCoding_NewsIntroAnchor_1280x720_2997fps.y4m` | CC BY-SA 4.0, Enlacetelevision | 1280x720 yuv420p 29.97 fps, 4.3 s |
| `anime` | `glasshalf` | AOM CTC `b1_syn/GlassHalf_1920x1080p_24p_8bit_420.y4m` | CC BY 4.0, Blender Cloud, Ton Roosendaal | 1920x1080 yuv420p 24 fps, 5.4 s |
| `multishot` | `sintel-trailer` | `download.blender.org/durian/trailer/sintel_trailer-480p.mp4` | CC BY 3.0, Blender Foundation | 854x480 h264 24 fps, 52.2 s |
| `highmotion` | `svt-waterfall` | SVT 2022 proxy `svt_waterfall_3840x2160_50_10bit_420p.y4m-proxy.mp4` | CC BY 4.0, SVT (suite PDF) | 3840x2160 AV1 yuv420p10le 50 fps, 2.6 s |

Scene cuts found in the Sintel trailer: 16.5, 17.5, 23.79, 25.875, 27.417 and 28.0 s.

## Rejected candidates

| Candidate | Reason |
| --- | --- |
| Netflix Chimera / El Fuente (incl. BarScene) | CC BY-NC-ND 4.0 (`Netflix_BarScene_Copyright.txt`) |
| SVT 2006 (ParkJoy, CrowdRun, DucksTakeOff) | Terms restrict use to standards work |
| NTIA/ITS sets | Research use only |
| UVG | CC BY-NC |
| SVT 2022 `smoke_sauna` proxy (for `noisy`) | 1 MB AV1 preview of 2.6 s at 4K; the sensor noise is coded away, and scaling to 640x360 averages what remains |
| Caminandes (for `anime`) | Distributed as zip; `fetch-corpus.sh` reads media files only |

Backups if a chosen set disappears: Noise_Animation (CC BY-SA, paper texture) for `anime`; FourPeople (Vidyo, public domain) for `retranscode`.

## The `noisy` gap

A denoise proof needs noise that survives into the 640x360 workload. Two routes remain: a permissive source that is noisy at native 640x360 (or close to it), or a crop path in `fetch-corpus.sh` that takes a 640x360 window from a 4K source instead of scaling it. Until one exists, `--check-categories` exits 1 with `MISSING noisy` and the #198 proof has no pinned clip.

## Reproduce

```bash
CORPUS=/absolute/empty/dir bash scripts/bench/fetch-corpus.sh
bash scripts/bench/fetch-corpus.sh --check-categories
python3 -I scripts/test-fetch-corpus.py
```
