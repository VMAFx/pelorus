<!-- markdownlint-disable MD013 MD060 -->
# ADR-0124: vf_pelorus_aa — anime warp anti-aliasing + line-darkening

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris
- **Tags**: anime, aa, warpsharp, line-darken, vulkan, ffmpeg, tune

## Context

Anime line-art is the worst-case content for a block encoder's aliasing
behaviour. Hand-drawn or cel-shaded lines are high-contrast, near-binary edges
on flat fields; every scale, re-encode, and chroma-subsample step in a typical
distribution chain leaves stair-stepped "jaggies" along those lines. The
aliasing is structured, high-contrast, and exactly where the eye is drawn, so it
both looks bad and costs bits — a hardware encoder spends residual coding the
jagged staircase that a clean line would not have produced.

The AviSynth/VapourSynth anime-restoration community solved this years ago with
**awarpsharp2** (warp-based anti-aliasing) and **FastLineDarken** (edge
line-darkening). awarpsharp does not sharpen in the conventional sense: it builds
a blurred edge-strength map (a blurred Sobel-magnitude field) and *warps* each
pixel toward the nearest edge by the gradient of that map, resampling the source
at the displaced position. This pulls the stair-stepped samples onto the line —
anti-aliasing and slight thinning — without the ringing a high-pass sharpen
would add. FastLineDarken then deepens the dark side of real edges so the
de-aliased lines stay crisp rather than washing out.

Pelorus is a **GPU pre-encode** pipeline: frames stay in VRAM from decode to
encode (ADR-0104, principles §5). The prior art runs on the CPU and would force
an `hwdownload`/`hwupload` round-trip mid-graph, which destroys the zero-copy
contract. Porting the algorithm to a Vulkan compute pass keeps it in VRAM and
lets it run as one stage of the anime `tune` chain. This filter is the **second
stage** of that chain, after `vf_pelorus_dehalo_vulkan` (ADR-0123): dehalo
removes the ringing/halo artifacts around edges first, then aa de-aliases and
darkens the now-clean lines. Like dehalo, it is a **pure transform** — it changes
pixels and emits no side data, so it does **not** link `libpelorus` (deps-only
registration; see the patch note below).

## Decision

Add `vf_pelorus_aa_vulkan`: a **single-pass** Vulkan compute filter (zero-copy,
`FF_VK_REP_FLOAT` UNORM, bit-depth-agnostic) that implements awarpsharp2-style
warp anti-aliasing plus optional FastLineDarken line-darkening. It is **luma
only**; chroma planes pass through unchanged.

### Warp anti-aliasing

Per luma pixel, in one pass:

1. **Edge-strength map.** Compute a Sobel magnitude per pixel, clamped to a
   ceiling `thresh` (so a single very strong edge cannot dominate the blur), and
   average it over a `(2·blur+1)²` box to get a blurred edge-strength field
   `emask`. The box blur is the cheapest separable-free low-pass that survives an
   inline single-pass kernel.
2. **Gradient.** Take the central-difference gradient of `emask` at the pixel —
   `(gx, gy)` point toward increasing edge strength, i.e. toward the nearest
   line.
3. **Warp + resample.** Displace the sampling position by `depth · (gx, gy)` and
   **bilinearly resample** the source luma at the displaced position. Stair-step
   samples on the outside of a line are pulled onto the line; the result is
   anti-aliased and slightly thinned, with no high-pass ringing.

### Optional line-darkening

When `darkstr > 0`, a pixel whose Sobel magnitude exceeds `edge` is treated as a
real line: the warped value is blended toward the local 3×3 minimum by
`darkstr`, deepening the dark side of the edge so the de-aliased line stays
crisp. The Sobel gate keeps the darkening off flat fields and texture. With
`darkstr = 0` (the default) the line-darkening branch is skipped entirely and the
filter is pure warp-AA.

The standalone reference shader is `libpelorus/shaders/pelorus_aa.comp`; the
filter's inline GLSL implements the same algorithm and is kept in lockstep
(AGENTS hard rule 4). The only intended difference is the working domain: the
`.comp` reads `r16ui` and normalizes by 65535, the inline form reads
`FF_VK_REP_FLOAT` UNORM images already in `[0,1]`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Warp-AA via blurred-edge-map gradient + bilinear resample (this) | Faithful awarpsharp2 port; anti-aliases + thins lines with no ringing; single inline pass; cheap | Box blur is coarse; warp can over-thin at high `depth` | **Chosen** — matches the established anime restoration practice at GPU speed, zero-copy |
| nnedi3 / EEDI-style re-interpolation AA | Highest-quality directional AA | Far heavier; needs a trained neural model and weights shipped; multi-pass; large VRAM footprint for a first kernel | Rejected — disproportionate cost and complexity for the warp-AA niche; revisit only if warp proves insufficient |
| Sharpen-based AA (unsharp / high-pass) | Trivial to implement | A high-pass sharpen adds ringing along exactly the high-contrast edges anime is made of — the opposite of the goal | Rejected — introduces the artifact the filter is meant to remove |
| CPU awarpsharp2 via `hwdownload`/`hwupload` | Reuses the reference implementation verbatim | Breaks the zero-copy contract (a mid-graph PCIe round-trip per frame); defeats the reason Pelorus exists | Rejected — the GPU port is the entire point |

## Consequences

- **Positive**: gives the anime `tune` chain a real de-aliasing + line-darkening
  stage that runs in VRAM, zero-copy, alongside dehalo (ADR-0123); a faithful
  port of the community-standard awarpsharp2 + FastLineDarken; luma-only so
  chroma is untouched; no side data, no `libpelorus` link, no ABI surface.
- **Negative / honest envelope**: the algorithm port is **compile-verified and
  glslang-clean only**. The default option values (`blur=2`, `depth=8`,
  `thresh=0.5`, `darkstr=0`, `edge=0.08`) are carried over from the
  awarpsharp2/FastLineDarken conventions and have **not** been perceptually tuned
  on real anime content on the GPU. **No quality number is claimed.** The
  on-content tuning of the defaults and the proof against clean anime ground
  truth — SSIMULACRA2 and edge-region VMAF-NEG under the ADR-0111 methodology —
  are the documented follow-up.
- **Follow-ups (documented, not in this PR)**: (1) perceptual tuning of the
  default option values on representative anime sources; (2) the SSIMULACRA2 /
  edge-region VMAF-NEG quality proof against clean ground truth (ADR-0111);
  (3) integration of aa into the `tune=anime` preset alongside dehalo (ADR-0123).

## References

- `ffmpeg-patches/files/vf_pelorus_aa_vulkan.c`,
  `libpelorus/shaders/pelorus_aa.comp`; [docs/metrics/aa.md](../metrics/aa.md).
- [ADR-0123](0123-anime-dehalo.md) (dehalo — the prior stage in the anime `tune`
  chain this filter follows),
  [ADR-0104](0104-ffmpeg-patch-stack.md) (the patch-stack delivery model and the
  zero-copy contract),
  [ADR-0111](0111-benchmark-methodology.md) (the score-against-clean-ground-truth
  methodology the deferred quality proof uses).
- Prior art: awarpsharp2 (AviSynth/VapourSynth warp-based anti-aliasing) and
  FastLineDarken (edge line-darkening) from the anime-restoration community.
- Source: `req` — add an anime warp anti-aliasing + line-darkening filter that
  ports awarpsharp2 + FastLineDarken to a single zero-copy Vulkan compute pass,
  as the second stage of the anime `tune` pipeline after dehalo.
