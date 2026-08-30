<!-- markdownlint-disable MD013 -->
# Grain ladder — what `--grain N` is worth in real content

`run-bench.py --grain N` gives a controlled grain axis (see
[benchmarking.md](benchmarking.md)), but `N` is an ffmpeg `noise=alls=` value and means
nothing physically. This profiles real content so a value can be read as *"about the grain
of X"*, and records where the measurement stops being trustworthy.

## Method

86 titles sampled from a private 418-film / 183-series library, stratified by decade:
56 films and 30 TV series (one episode each). From each, 24 frames at a fixed 15-minute
seek, taken two ways:

- **native crop** — a centre `crop=640:360` of the source frame. Preserves per-pixel
  statistics, so this is the *true* grain of the master.
- **scaled** — `scale=640:360`, i.e. exactly what the benchmark corpus feeds the filters.

Measured with `pelorus_grain_estimate_vulkan` (`grain_sigma`, `grain_flat`). Only
aggregate statistics are recorded here; no content is redistributed.

## What real content actually measures (native crop, n=84)

| percentile | `grain_sigma` |
|---|---|
| min | 0.00113 |
| p10 | 0.00173 |
| p25 | 0.00381 |
| **median** | **0.00666** |
| p75 | 0.01020 |
| p90 | 0.01352 |
| max | 0.02652 |

By decade — median `grain_sigma`, native crop:

| decade | film | TV |
|---|---|---|
| 1960s | 0.00823 | — |
| 1970s | 0.00770 | — |
| 1980s | 0.00788 | 0.00700 |
| 1990s | 0.00644 | **0.01045** |
| 2000s | 0.00408 | 0.00997 |
| 2010s | 0.00649 | 0.00822 |
| 2020s | **0.00215** | 0.00275 |

Three things fall out of this:

1. **Grain collapses in the 2020s** — film 0.0022, TV 0.0028, against 0.008 for the
   1960s–80s. Digital acquisition plus routine DNR. Median `grain_flat` for 2020s film is
   **1.000**: the estimator finds the frame essentially entirely flat.
2. **TV is grainier than film from the 1990s on** — 1990s TV 0.0105 vs 1990s film 0.0064.
   Film-sourced series retain grain that feature Blu-rays often have remastered out. If
   you want grain, TV is the better hunting ground.
3. **Nothing here is very grainy.** The p90 of the whole sample is 0.0135. Heavy-grain
   content is rare in a modern library, which is why the grain axis has to be synthetic.

## Scaling compresses the range — benchmark at 640x360 and you hide grain differences

Same titles, measured both ways:

| | median | p25 | p75 |
|---|---|---|---|
| native crop | 0.00666 | 0.00381 | 0.01020 |
| scaled to 640x360 | 0.00698 | 0.00568 | 0.00945 |

The medians barely move, but the **interquartile range halves** (0.0064 → 0.0038).
Downscaling lifts the floor (resampling manufactures high-frequency residual) and lowers
the ceiling (averaging destroys real grain), squeezing everything toward the middle. So a
benchmark run at the corpus's 640x360 **under-detects** content differences in grain.
Read that as a caveat on any BD-rate delta attributed to grain at this resolution.

## The ladder

`--grain N` on `netflix-bar`, and the `grain_flat` coverage that says whether to believe it:

| `--grain` | `grain_sigma` | Δ vs clean | `grain_flat` | trustworthy? |
|---|---|---|---|---|
| 0 | 0.01243 | — | 0.600 | yes |
| 4 | 0.01253 | +0.00010 | 0.593 | yes |
| 6 | 0.01312 | +0.00069 | 0.585 | yes |
| 8 | 0.01380 | +0.00137 | 0.572 | yes |
| 10 | 0.01450 | +0.00207 | 0.556 | yes |
| 12 | 0.01510 | +0.00267 | 0.535 | yes |
| 16 | 0.01745 | +0.00502 | 0.438 | marginal |
| 20 | 0.01824 | +0.00581 | 0.171 | **no** |
| 24+ | — | — | ≤0.057 | **no** |

**Use `netflix-bar` as the grain base, and stay at or below `--grain 16.`** Past that,
`grain_flat` collapses and the sigma is drawn from too few qualifying pixels to mean
anything — the starvation described in
[grain_estimate.md](../metrics/grain_estimate.md).

**Do not use `bbb` as the grain base.** It starts at `grain_flat` 0.33 — already
low-coverage — and saturates much earlier: sigma peaks at `--grain 16` (0.0244) and then
*falls* (0.0230 at 20, 0.0181 at 24). A non-monotonic axis is not an axis.

### Reading a value

Real content spans roughly 0.0022 (2020s digital) to 0.0105 (1990s TV) at the median, a
range of about 0.008. Against that, on `netflix-bar`:

- `--grain 8` (+0.0014) — a small nudge, roughly a sixth of the real-world range
- `--grain 12` (+0.0027) — about a third: comparable to the gap between a 2000s film
  master and a 1990s TV transfer
- `--grain 16` (+0.0050) — about 60%: near the full span from modern-digital-clean to
  the grainiest decade medians

## Caveat: `grain_sigma` is not purely grain

Both corpus clips read **higher** than almost all real content — `bbb` 0.0131 and
`netflix-bar` 0.0124, against a real-content median of 0.0067 — despite being the *clean*
sources. They are also both heavily-compressed low-resolution distribution encodes, and
compression artifacts (ringing, mosquito noise) put high-frequency residual into flat
regions exactly where the estimator looks.

So `grain_sigma` should be read as **high-frequency residual in flat areas**, of which
film grain is one contributor and codec noise is another. That is fine for the purpose it
serves — deciding whether there is removable high-frequency impairment — but it is not a
film-grain measurement, and a cross-source comparison of absolute values is not safe
unless the sources are encoded comparably.

## Why this matters

ADR-0142's law is that reductive pre-encode gain scales with removable impairment. This
profile says a modern library gives you very little of it: the 2020s median is 0.0022 with
essentially the whole frame flat. That is consistent with the repeated honest negatives on
clean content, and it is the reason the grain axis is injected rather than selected.
