<!-- markdownlint-disable MD013 MD060 -->
# Dehalo feathered mask: the steep flank, and what the feather must not touch

**Date:** 2026-10-19

**Decision:** [ADR-0226](../adr/0226-dehalo-feathered-mask.md), issue [#201](https://github.com/VMAFx/pelorus/issues/201).

**Scope:** FFmpeg `n9.0.2` plus shared series `v0.3.0` plus the Pelorus stack, `pelorus_dehalo_vulkan` at its defaults (`blur=2:darkstr=1:brightstr=1:edge=0.08:ring=2`) on an NVIDIA GeForce RTX 4090, chosen by FFmpeg's default Vulkan device selection. Builds: the ADR-0163 shader ("old"), the plain feather ("b") and the guarded feather (shipped, "new"), each from a full replay of `ffmpeg-patches/test/build-and-run.sh` (34 shared and 22 Pelorus patches).

Raw logs (local, not committed): `.workingdir/evidence/201/` (`replay-r1.log`, `replay-r2.log`, `matrix-r2.log`, `speed-r2.txt`, `RESULTS.tsv`, `SUMMARY.txt`, the measurement scripts and fixtures).

## Verdict

A feather of the ring mask reaches the steep flank that ADR-0163 leaves, but the plain feather also moves real line-art: isolated texture dots and the core of a one-pixel line, which are not in the exclusion zone and have their own step above `edge`. Skipping the exclusion zone and every strict local extremum keeps the gain on the synthetic set (Lanczos overshoot removed 24.3 % → 35.0 %) and brings the real-clip cost close to the old shader (BBB PSNR vs source 55.09 → 54.89 dB). Removal of unsharp-mask halos stays small, because the ADR-0163 pull, not the mask, limits it there.

## Method

1. Fixtures: the ADR-0163 synthetic set (640×360 anti-aliased lines on flat fills; `clean`, ringed by unsharp masking `usm`, a 2× Lanczos round trip `lanczos`, or both `lzusm`).
2. Metric: overshoot beyond the clean image's 7×7 min/max envelope; removed % = 1 − Σ overshoot² out / Σ overshoot² in. An earlier band-RMS metric counted line pixels as halo and was dropped (see Negatives).
3. Real clips: `bbb` (640×360, 48 frames, from `scripts/bench/corpus.lock`) and a 48-frame BarScene segment downscaled bicubic from the 4K source (the bench corpus extraction). An area-filter downscale of the same segment serves as a halo-free reference: its 3×3 envelope bounds what the bicubic clip should hold. Both clips also went through `unsharp=5:5:1.5:5:5:0` to add a known halo.
4. A numpy model of the gate, pull and feather picked the variants; every number in Results comes from the GPU builds. The model of the plain feather mismatched the GPU on 0.03 % to 0.14 % of pixels.

## Results

Synthetic set, GPU, overshoot removed and clean-image change:

| Build | usm | lanczos | lzusm | clean max / PSNR / changed px |
| --- | --- | --- | --- | --- |
| old (ADR-0163) | 0.2 % | 24.3 % | 3.6 % | 2 / 71.9 dB / 302 |
| plain feather | 2.5 % | 38.3 % | 5.1 % | 2 / 71.9 dB / 309 |
| guarded feather | 2.5 % | 35.0 % | 4.9 % | 2 / 71.9 dB / 307 |

`tile=0` and `tile=1` output are byte-identical for every fixture on every build.

Real clips, GPU, all 48 frames:

| Build | BBB PSNR vs source / max / changed | BBB usm removed | BarScene PSNR vs source / max / changed | BarScene usm removed | BarScene vs area reference removed |
| --- | --- | --- | --- | --- | --- |
| old | 55.09 dB / 19 / 2.85 % | 0.2 % | 52.65 dB / 21 / 3.48 % | 0.9 % | 1.87 % |
| plain feather | 51.65 dB / 36 / 5.72 % | 1.9 % | 51.85 dB / 21 / 4.74 % | 1.5 % | 6.16 % |
| guarded feather | 54.89 dB / 19 / 3.14 % | 0.2 % | 52.26 dB / 21 / 4.12 % | 1.3 % | 3.92 % |

Texture-dot fixture (64×64 texture of codes 88..104, 49 one-pixel dots of 140 and 50): old moves no dot; the plain feather moves 42 of 49 by up to 42 codes; the guarded feather moves none. The 96×64 steep-flank fixture of the format matrix: the old build fails the flank assertion on all 56 checked rows, both feather builds pass it with line-art max change 0.

Speed, 1080p `testsrc2`, 300 frames, `hwupload` → filter → `hwdownload` → null, median `rtime` of three runs on the GeForce RTX 4090: upload and download alone 0.255 s; old 0.379 s (`tile=1` 0.367 s); guarded feather 0.650 s (`tile=1` 0.553 s). The filter's share grows from about 0.41 to 1.31 ms per frame at `tile=0`.

## Negatives

- Plain feather on BBB: isolated bright dots of the texture are pulled into the fill (one 145 → 109); on BarScene a dark one-pixel row is lifted. Hence the guards.
- Exclusion skip alone (model): BBB 362 strong-edge pixels moved by 8 codes or more, PSNR 52.91 dB. Not enough.
- Unsharp-mask halos stay mostly in place on all variants: their overshoot sits inside the range of the neighbours' pulls, so the Repair clamp leaves it.
- `edge=0.10` with the guarded feather (model) removes 6.1 % usm and 50.2 % Lanczos overshoot; ADR-0163 measured `edge=0.12` moving strong edges on real content. Not a default change without the ADR-0111 content proof.
- BarScene frame 43, row 183, columns 441..447: the area reference holds the dark row too (41..56), so it is a real line. The ADR-0163 ring gate already lifts one of its pixels (44 → 65); the guarded feather spreads that along the row (46, 50, 53 → 63, 60, 61), because those pixels have their own step just above `edge` and are not strict extrema. Recorded as a known limit of the ring gate.
- An external research draft scored variants with a band-RMS metric that counted line pixels as halo; its conclusion (no variant helps) did not survive the envelope metric.

## Consequence

The guarded feather ships (ADR-0226). `scripts/test-dehalo-line-art.py` checks the feather structure and the steep flank; `ffmpeg-patches/test/vulkan-format-matrix.sh` checks the flank on the device and adds the texture-dot fixture, which the plain feather fails.
