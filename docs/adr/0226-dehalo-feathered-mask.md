<!-- markdownlint-disable MD013 MD060 -->
# ADR-0226: Dehalo feathered mask for the steep halo flank, with line-art guards

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `vulkan`, `ffmpeg`, `dehalo`, `quality`
- **Related**: [ADR-0111](0111-benchmark-methodology.md), [ADR-0123](0123-anime-dehalo.md), [ADR-0139](0139-dehalo-shared-mem-tile.md), [ADR-0163](0163-dehalo-gate-and-pull.md)

## Context

Issue [#201](https://github.com/VMAFx/pelorus/issues/201): after the ADR-0163 harm-fix, `pelorus_dehalo_vulkan` keeps line-art intact but removes almost no halo. The pixel right next to a line, the steep halo flank, has its own edge step above `edge`, so the ring gate counts it as line-art. ADR-0163 measured that these flank pixels carry 93–97 % of the halo energy that remains, and named a feathered `FineDehalo`-style mask as the follow-up.

`FineDehalo` (HAvsFunc r33) does not apply its ring mask as a hard gate: it expands and blurs the mask, so pixels next to the ring take part of the correction. [Research 0201](../research/0201-dehalo-feathered-mask.md) measured that form on the ADR-0163 synthetic set and on two real clips. The plain feather improves the synthetic set but harms real content: on Big Buck Bunny it flattens isolated texture dots (one dot 145 → 109) and PSNR against the source drops from 55.09 to 51.65 dB; on BarScene it lifts a dark one-pixel line. Two guards remove most of that harm.

## Decision

1. A pixel outside the halo band (gate class 0) is feathered: with `n` of its eight neighbours in the band, it moves towards its target with weight `m = min(1, 2 n / 9)`, the 3×3 mean of the band mask times two. The target is the source clamped into the range of the neighbours' ADR-0163 pulls, so only an excursion beyond the dehaloed surroundings moves; a clean step edge lies inside that range and stays.
2. A pixel in the close-edge exclusion zone (line on both sides of an axis, a thin line's core) is never feathered.
3. A pixel above or below all eight neighbours (a strict local extremum: an isolated dot, the core of a one-pixel line seen across it) keeps the source. A flank along an edge shares its value with the pixels along that edge and is not an extremum.
4. The tile halo grows by one pixel (`PEL_HALO` 10 → 11 in the shipped shader, `PEL_TILE` 52 → 54), because the gate and the pull are evaluated at the neighbours. Direct and tiled output stay bit-identical.
5. No option, default or side-data change. The shader source lives once in `ffmpeg-patches/files/vulkan/pelorus_dehalo.comp.glsl`; the reference `libpelorus/shaders/pelorus_dehalo.comp` gets the same logic.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Plain feather (decision 1 only) | Largest removal on the synthetic set (Lanczos 38.3 %) | Real content: 42 of 49 texture dots moved by up to 42 codes on the dot fixture; BBB PSNR vs source 55.09 → 51.65 dB | Damages line-art the ADR-0163 fix protects |
| Feather with the exclusion skip only | Thin line cores safe | BBB: 362 strong-edge pixels moved by 8 codes or more, PSNR 52.91 dB (model) | Isolated dots still flattened |
| Raise `edge` to 0.10 with the guarded feather | usm 6.1 %, Lanczos 50.2 % removed (model) | Changes a default; ADR-0163 measured 0.12 moving strong edges on real content | Default change needs the ADR-0111 content proof |
| `FineDehalo` mask expansion with a larger blur | Closer to the AviSynth form | Needs a deeper halo than the 32×32 workgroup tile affords; reaches further into line-art | Read depth and harm |

## Consequences

- **Positive**: on the ADR-0163 synthetic set, overshoot beyond the clean 7×7 envelope removed goes from 0.2 % to 2.5 % (usm), 24.3 % to 35.0 % (Lanczos) and 3.6 % to 4.9 % (Lanczos + usm). The clean image keeps max change 2 codes and 71.9 dB. On BarScene, overshoot beyond the envelope of an area-filter downscale (halo-free reference) removed goes from 1.87 % to 3.92 %.
- **Negative**: halo removal is still small on unsharp-mask halos, which the ADR-0163 pull limits (`usm` 2.5 %). BBB PSNR against the source moves from 55.09 to 54.89 dB and BarScene from 52.65 to 52.26 dB; changed pixels rise from 2.85 % to 3.14 % (BBB) and 3.48 % to 4.12 % (BarScene). The feather spreads an existing ADR-0163 misclassification along a dark line in BarScene (the ring gate already lifts one pixel there; see research 0201). Cost: the dispatch evaluates the gate at eight neighbours and the pull at up to eight; at 1080p (300 frames, `tile=0`, upload and download included) the filter's share of the run rises from about 0.41 to 1.31 ms per frame on an NVIDIA GeForce RTX 4090.
- **Neutral / follow-ups**: the ring gate classing a real dark one-pixel line as halo (BarScene frame 43) is an ADR-0163 behaviour, now recorded as a known limit. A change of `edge` waits for the ADR-0111 content proof.

## References

- Issue: VMAFx/pelorus#201; prior decision ADR-0163.
- Research: [docs/research/0201-dehalo-feathered-mask.md](../research/0201-dehalo-feathered-mask.md).
- HAvsFunc r33 `FineDehalo` (`havsfunc.py` lines 488–598), as cited in ADR-0163.
- Tests: `scripts/test-dehalo-line-art.py`, `ffmpeg-patches/test/vulkan-format-matrix.sh` (steep-flank and texture-dot assertions).
