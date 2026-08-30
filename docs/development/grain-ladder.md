<!-- markdownlint-disable MD013 -->
# Grain ladder — what `--grain N` is worth in real content

`run-bench.py --grain N` gives a controlled grain axis (see
[benchmarking.md](benchmarking.md)), but `N` is an ffmpeg `noise=alls=` value and means
nothing physically. This profiles a real library so a value can be read as *"about the
grain of X"*, and records where the measurement stops being trustworthy.

## Method

**752 titles / 2230 scenes.** 418 films and 334 TV episodes (drawn from all 183 series in
the library), each sampled at **three separate scenes** — films at 20/40/60 min, episodes
at 5/15/25 min — 12 frames per scene. Per-title value is the **median across its three
scenes**.

Frames are taken as a native centre `crop=640:360`, not a downscale, so per-pixel
statistics survive; this is the *true* grain of the master. Measured with
`pelorus_grain_estimate_vulkan`. Only aggregate statistics are recorded here; no content
is redistributed.

### Why three scenes, not one

A single scene is not a measurement of a title. Across the 750 titles with multiple
scenes, the within-title spread (max−min, relative to that title's own median) is:

| | spread |
|---|---|
| median | 0.71x |
| p75 | 1.18x |
| p90 | 2.02x |

So a one-scene probe typically misestimates a title by around **36%**, and for a tenth of
titles by **100% or more** — a dark interior and a bright exterior in the same film differ
more than two different films do. Any grain figure quoted from a single scene should be
treated as noise.

## What real content measures (per-title medians, n=752)

| percentile | `grain_sigma` |
|---|---|
| min | 0.00079 |
| p10 | 0.00201 |
| p25 | 0.00290 |
| **median** | **0.00484** |
| p75 | 0.00754 |
| p90 | 0.01017 |
| max | 0.02095 |

By decade:

| decade | film (n) | median | TV (n) | median |
|---|---|---|---|---|
| 1960s | 8 | 0.00486 | — | — |
| 1970s | 25 | **0.00720** | — | — |
| 1980s | 48 | **0.00714** | 7 | 0.00782 |
| 1990s | 64 | 0.00673 | 7 | **0.00885** |
| 2000s | 86 | 0.00517 | 42 | 0.00761 |
| 2010s | 113 | 0.00455 | 143 | 0.00462 |
| 2020s | 73 | **0.00328** | 136 | **0.00297** |
| **all** | 417 | 0.00535 | 335 | 0.00436 |

## The biggest single split is not decade — it is resolution tier

| tier | n | median | p25 | p75 |
|---|---|---|---|---|
| 4K | 402 | **0.00323** | 0.00221 | 0.00484 |
| 1080p / standard | 350 | **0.00724** | 0.00519 | 0.00914 |

**The 1080p tier is 2.2x grainier than the 4K tier.** Both were sampled as native crops,
so this is a like-for-like per-pixel comparison, not a scaling artifact — 4K masters are
simply denoised far more aggressively, which is what makes them compress at all.

For a project whose thesis is that reductive filtering wins in proportion to removable
impairment, that is the most actionable line in this document: **target 1080p sources.**
The 4K tier has already had the impairment removed by someone else.

Grain also declines steadily with decade — 1970s/80s at ~0.0072 down to 2020s at
~0.0030–0.0033, with median `grain_flat` reaching **0.99** in the 2020s (the estimator
finds essentially the entire frame flat). Digital acquisition plus routine DNR.

## The ladder

`--grain N` on `netflix-bar`, with the `grain_flat` coverage that says whether to believe
the reading:

| `--grain` | `grain_sigma` | Δ vs clean | `grain_flat` | trustworthy? |
|---|---|---|---|---|
| 0 | 0.01243 | — | 0.600 | yes |
| 4 | 0.01253 | +0.00010 | 0.593 | yes |
| 8 | 0.01380 | +0.00137 | 0.572 | yes |
| 12 | 0.01510 | +0.00267 | 0.535 | yes |
| 16 | 0.01745 | +0.00502 | 0.438 | marginal |
| 20 | 0.01824 | +0.00581 | 0.171 | **no** |
| 24+ | — | — | ≤0.057 | **no** |

**Use `netflix-bar` as the grain base and stay at or below `--grain 16`.** Past that,
`grain_flat` collapses and the sigma comes from too few qualifying pixels to mean anything
— the starvation described in [grain_estimate.md](../metrics/grain_estimate.md).

**Do not use `bbb` as the grain base.** It starts at `grain_flat` 0.33 — already
low-coverage — and saturates early: sigma peaks at `--grain 16` (0.0244) then *falls*
(0.0230 at 20, 0.0181 at 24). A non-monotonic axis is not an axis.

### Reading a value

Real content runs p10 0.0020 to p90 0.0102, a span of about 0.008. Against that:

- `--grain 8` (+0.0014) — roughly a sixth of the real range
- `--grain 12` (+0.0027) — about a third; comparable to the gap between a 2020s master and
  a 1980s one
- `--grain 16` (+0.0050) — about 60%; near the full span from modern-clean to the
  grainiest decade medians

## Caveat: `grain_sigma` is not purely grain

Both corpus clips read **higher** than almost all real content — `bbb` 0.0131 and
`netflix-bar` 0.0124, against a real-content median of 0.0048 — despite being the *clean*
sources. Both are heavily-compressed low-resolution distribution encodes, and compression
artifacts (ringing, mosquito noise) put high-frequency residual into flat regions exactly
where the estimator looks.

Read `grain_sigma` as **high-frequency residual in flat areas**, of which film grain is one
contributor and codec noise is another. That is fine for its purpose — deciding whether
removable high-frequency impairment exists — but it is not a film-grain measurement, and
absolute values are not comparable across differently-encoded sources.

## Revision history — the first version of this table was wrong

This document originally reported an 86-title, **single-scene** probe. Re-running at
752 titles with three scenes each changed individual decade buckets by up to **78%**:

| bucket | n=86, 1 scene | n=752, 3 scenes | error |
|---|---|---|---|
| 2010s TV | 0.00822 | 0.00462 | **+78%** |
| 1960s film | 0.00823 | 0.00486 | **+69%** |
| 2010s film | 0.00649 | 0.00455 | +43% |
| 2020s film | 0.00215 | 0.00328 | −34% |
| 2000s TV | 0.00997 | 0.00761 | +31% |
| overall median | 0.00666 | 0.00484 | +38% |

Two conclusions from the small probe were simply false: that the 1960s were the grainiest
decade (they are mid-pack; the 1970s–80s are the peak), and that TV stays grainier than
film into the 2010s (they converge). The resolution-tier split, which turns out to be the
largest effect in the data, was invisible at n=86 entirely.

The lesson is the one the within-title spread already states numerically: with a
scene-to-scene spread near 0.7x the title's own median, small single-scene samples produce
confident nonsense.
