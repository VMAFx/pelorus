<!-- markdownlint-disable MD013 MD060 -->
# ADR-0163: Dehalo harm-fix: ring gate in edge-step units and the DeHalo_alpha MaskedMerge + Repair pull

- **Status**: Accepted
- **Date**: 2026-10-03
- **Deciders**: Lusoris
- **Tags**: `vulkan`, `ffmpeg`, `dehalo`, `quality`

## Context

[ADR-0123](0123-anime-dehalo.md) ports HAvsFunc `DeHalo_alpha` + `FineDehalo`
to one Vulkan dispatch: pull the halo toward a blur, gated to the ring next to
lines so line-art and flats stay untouched. BUG-011 reported the ring gate as
inverted and its ring scan as one-sided. Checked against HAvsFunc r33
(`havsfunc.py`, `DeHalo_alpha` lines 389–425, `FineDehalo` lines 488–598), the
shipped shader differed in four places:

1. **Edge threshold scale.** `on_line` compared the raw 3×3 Sobel magnitude,
   which reads 4× an ideal step, with `edge = 0.08`, so any step above 0.02
   counted as line-art. `FineDehalo`'s line threshold (`thmi..thma` = 80..128 on
   `AvsPrewitt`, which reads 3× the step) sits at a step of 0.105..0.167.
2. **One-sided ring scan.** `near_line` tested `|max(cross at d) − c| > edge`,
   which sees a brighter neighbour but not a darker one, so bright halos next to
   dark lines were missed.
3. **Inverted `so` mix.** `DeHalo_alpha` takes
   `lets = MaskedMerge(halos, clp, so)`: the source where `so` is high, the blur
   where it is low. The shader took `mix(c, h, so)`, the opposite.
4. **No `Repair`.** `DeHalo_alpha` clamps the source into the 3×3 range of
   `lets` (`Repair` mode 1), which only removes an excursion beyond the blurred
   envelope. The shader moved the pixel toward the box mean directly, which can
   overshoot past the fill level.

The gate logic `near_line && !on_line` matches `FineDehalo`'s `large − strong`,
so the boolean was not inverted. Its effect was. Measured with a numpy model
that is bit-exact to the shader on the Arc A380 and RADV, on a synthetic set
(anti-aliased lines on flat fills, ringed by unsharp masking, a 2× Lanczos round
trip, or both):

- `on_line` covered 97–100% of the halo-band pixels, so the gate admitted
  0.1–0.2% of the band and the halo RMS did not change.
- A symmetric stroke core reads a zero Sobel, so `on_line` was false there while
  `near_line` was true. The filter rewrote line-art: on the clean image it
  changed 100 line pixels, 77 of them by 64 codes or more, at most 129 codes
  (PSNR vs the clean image 41.5 dB).
- `near_line` hit 19–54% of the bright halo pixels against 58–86% of the dark
  ones.

Fixing only the gate made things worse. With the gate open and the `so` mix
still inverted, the pull blurs the detail the opened gate now admits: 32% of
Big Buck Bunny pixels changed (PSNR vs source 45.7 → 32.8 dB).

## Scope

This change is a harm-fix. It stops `pelorus_dehalo_vulkan` from corrupting
line-art and keeps clean sources intact. It does not remove halos: at the
defaults the measured halo RMS drops by at most 1.3% on the synthetic set (usm
0.0706 → 0.0706, Lanczos 0.0571 → 0.0564, Lanczos + usm 0.0695 → 0.0692), and
the halos of real sources stay. The reason is structural (steep halo flanks
still count as line-art, see Consequences). Real halo removal is a separate
follow-up bug: a feathered `FineDehalo`-style mask. Nothing here should be read
as a halo-removal claim.

## Decision

We will correct both the gate and the pull, keeping the single dispatch and the
option set:

- **Edge mask in step units.** `edge_step = |Sobel| / 4`, so `edge` compares
  against a `[0,1]` step height. The default stays 0.08.
- **Ring gate.** A pixel whose own step exceeds `edge` is line-art and is left
  alone. A ring pixel has a line pixel within `ring` px along the cross, tested
  on the edge mask, so both halo polarities qualify. A pixel with a line on both
  sides of one axis is `FineDehalo`'s close-edge exclusion zone (a thin line's
  core, a gap between parallel lines) and is left alone.
- **Pull.** For each pixel of the 3×3 window, `so` comes from that pixel's
  `are`/`ugly` and `lets = mix(blur, source, so)`. Then
  `remove = clamp(source, min lets, max lets)`, and the pixel moves toward
  `remove` with `darkstr`/`brightstr`.
- **Single pass.** One sliding sum per row of the union window produces the 5×5
  grid of box means that the 3×3 window needs. Its loop bounds are constant, so
  the grid stays in registers. The deepest read is `MAX_R + 2`, so `PEL_HALO`
  goes from 9 to 10. The arithmetic is `precise` (NoContraction): without it
  the `tile=0` and `tile=1` specializations fused multiply-adds differently and
  disagreed on 3 of BBB's samples in 48 frames.

The shipped shader is `ffmpeg-patches/files/vulkan/pelorus_dehalo.comp.glsl`.
The standalone reference `libpelorus/shaders/pelorus_dehalo.comp` mirrors it.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Fix the gate only (step units + symmetric scan), keep the shipped pull | Smallest diff, matches the bug report's scope | The inverted `so` then blurs the detail the opened gate admits: BBB PSNR vs source 32.8 dB, 32% of pixels changed; halo RMS unchanged | Fails the do-no-harm bar |
| Fix polarity only (scan max and min of the cross) | One-line change | `on_line` still covers 97–100% of halo pixels, so nothing changes | No effect |
| Cross-only `Repair` (centre `so`, reuse the five box means) | No 5×5 grid | Rewrites fill pixels in concave corners between lines: clean synthetic PSNR 55 dB against 76 dB for the 3×3 form | Visible damage near line junctions |
| Raise the `edge` default to the `FineDehalo` equivalent (≈0.13) | Releases more of the steep halo flank | Only 2–4% more halo RMS reduction on the synthetic set; starts moving strong edges on real content | Tuning question, left to the ADR-0111 content proof |
| Feathered `FineDehalo` mask (3×3 mean ×2 of the ring mask) | Reaches the flank pixel next to the line, where most residual halo energy sits | Needs the gate at nine neighbours | Follow-up |

## Consequences

- **Positive**:
  - Line-art is no longer rewritten (the fix). The old code rewrote up to 129
    codes on stroke pixels. On the clean synthetic image, PSNR goes from 41.5
    to 76.0 dB and the RMS change on line pixels from 0.042 to 0.000.
  - Inside the admitted band the pull does not damage the structure: halo RMS
    on admitted pixels drops 50% on the Lanczos case and 25% on Lanczos +
    unsharp, but the admitted band holds only 1–3% of the halo energy, so this
    is not a halo-removal result.
  - On real sources the filter changes less than before: Big Buck Bunny 45.7 →
    55.1 dB and Netflix BarScene 47.2 → 52.8 dB PSNR vs source (48 frames at
    640×360). Strong edges are unchanged in both versions.
  - Faster on the Arc A380 at 1080p (wall time including upload and download):
    the default goes from 1.11 to 0.75 s for 24 frames, and `blur=8:ring=8` from
    2.14 to 1.14 s.
  - `tile=0` and `tile=1` stay byte-identical. Arc A380, RADV and RTX 4090
    outputs match at the default radii.
- **Negative**:
  - **Halos are not removed.** Overall halo RMS at the defaults drops only
    0–1.3% (synthetic table above). Steep halo flanks
    (step above `edge`) still count as line-art and carry 93–97% of the
    residual halo energy. On unsharp-masked copies of the corpus clips the
    band RMS goes 0.0325 → 0.0323 (BBB) and 0.0446 → 0.0440 (BarScene).
  - `edge` now means a step height. In raw-Sobel terms the same 0.08 is 4×
    stricter than before, so scripts that tuned `edge` against the old scale
    need re-tuning.
  - At the default radii `tile=1` is now about 8% slower than `tile=0` on the
    Arc A380, because only ring pixels read the box-mean window. ADR-0139's
    −38% was measured on the old algorithm; at `blur=8:ring=8` the tile still
    saves about 25%.
- **Neutral / follow-ups**:
  - `ffmpeg-patches/test/vulkan-format-matrix.sh` now asserts the gate on a
    ringed line and a 1-px stroke, plus direct/tiled identity. The pre-fix
    shader fails it.
  - Cross-vendor output can still differ by ±1 code on a handful of samples at
    `blur=8:ring=8` (2 of 16.6M on BarScene). The pre-fix shader does the same
    (5 samples): Vulkan's division and square-root precision.
  - Follow-up bug (halo removal proper): a feathered `FineDehalo`-style mask
    that reaches the flank pixel next to the line. Other follow-ups: content tuning of `edge`, and the ADR-0111
    proof that ADR-0123 already defers.

## References

- HAvsFunc r33 `havsfunc.py` (sha256 `4da2839544b1ce9382db670b069dc358228251d147dad91f740a860840e04924`): `DeHalo_alpha`, `FineDehalo`, `AvsPrewitt`.
- [ADR-0123](0123-anime-dehalo.md) (the port), [ADR-0139](0139-dehalo-shared-mem-tile.md) (tile path), [ADR-0147](0147-vulkan-sample-domain-and-components.md) (sample domain), [ADR-0111](0111-benchmark-methodology.md) (proof methodology).
- Source: BUG-011 (bug hunt B13, with B27 for the one-sided scan), triaged 2026-10-03.
