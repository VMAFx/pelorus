<!-- markdownlint-disable MD013 MD060 -->
# ADR-0123: vf_pelorus_dehalo_vulkan — anime/2D dehalo + dering

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

Hand-drawn / 2D animation has a signature artefact that block encoders pay
dearly for: **halos**. Hard line-art sits next to large flat colour fields, and
the production chain (upstream compression, broadcast re-encodes, and the
sharpening filters studios and fansub groups apply) leaves a bright/dark
**ringing band** hugging every line. That ring is high-contrast structure that
is *not* part of the original art: it is temporally noisy, it has no analogue in
the clean source, and the encoder spends real bits coding the overshoot residual
around every edge in every frame. Removing the ring before encode both restores
the intended look and frees those bits.

This is a well-understood problem with mature CPU prior art in the VapourSynth /
Avisynth ecosystem: HAvsFunc's `DeHalo_alpha` (a remove-only asymmetric pull of
the halo toward a blurred halo-free target, gated by a local-contrast
sensitivity mask) and `FineDehalo` (a Sobel edge mask that confines the pull to
the *ring* around lines so the line-art itself and open gradients are protected).
Those run on the CPU and require the frame to leave VRAM.

Pelorus is a zero-copy GPU pre-encode pipeline (ADR-0001): once a frame drops to
system RAM, PCIe bandwidth destroys throughput. The existing deband, denoise,
and analyze stages all run as Vulkan compute passes in VRAM. A dehalo stage that
matched the CPU prior art but stayed on the GPU would slot into that pipeline
with no copy, and is the missing first stage of a planned anime-specific tune.

## Decision

Add `vf_pelorus_dehalo_vulkan`: a **single-pass** Vulkan compute filter
(zero-copy, `FF_VK_REP_FLOAT` UNORM sampling, bit-depth-agnostic, modelled on
the existing `vf_pelorus_*_vulkan` filters) that ports `DeHalo_alpha` +
`FineDehalo` to the GPU. **Luma only; chroma passes through.**

The algorithm, in one dispatch:

1. **Halo-free target** — a strong box blur of luma. The blurred field is what
   the line-art-adjacent band should look like once the ring is gone.
2. **Sensitivity mask `so`** — derived from the *local contrast the blur
   removed*. `DeHalo_alpha`'s `lowsens` floor and `highsens` gain shape how much
   of the difference is treated as a halo to pull versus protected detail.
3. **Remove-only asymmetric pull** — the pixel is pulled toward the blur, never
   away from it, with separate `darkstr` (dark halos) and `brightstr` (bright
   halos) strengths. Remove-only means the filter can only *reduce* the ring, not
   add new overshoot.
4. **Edge-ring gate** — a `FineDehalo` Sobel edge mask, dilated by `ring`,
   confines the pull to the halo band *next to* lines. Line-art (on the edge
   itself) and open gradients (far from any edge) are excluded, so the filter
   removes the ring without softening the drawing or banding smooth skies.

This is the **foundation of the planned `tune=anime` pipeline**. Later ADRs add
warp-based anti-aliasing, cadence-aware denoise, and a `tune=anime` preset that
wires dehalo together with the existing deband / analyze / ROI stack into one
anime-specific pre-encode chain. Dehalo lands first because the ring is the
dominant anime artefact and because it is a pure pixel transform with no new
ABI surface — the lowest-risk first stage.

The `FineDehalo` edge mask is folded **inline** into the single dispatch rather
than shipped as a separate filter: the mask is cheap, it is only ever consumed
by this pull, and a separate filter would force the intermediate mask through a
second image and break the single-pass design.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| **Run the CPU VapourSynth `DeHalo_alpha` + `FineDehalo` chain via `hwdownload`/`hwupload`** | Rejected — round-tripping every frame to system RAM and back breaks the zero-copy invariant that the whole pipeline exists to preserve; the PCIe cost dwarfs the compute. |
| **Ship the `FineDehalo` Sobel edge mask as a separate `vf_pelorus_edgemask` filter and compose** | Rejected — folded inline instead. The mask is only consumed by this filter's pull; a separate stage adds an intermediate image and a second dispatch for no reuse benefit and breaks single-pass. |
| **A learned / neural dehalo network** | Out of scope — far higher implementation and runtime cost, a model-weights dependency, and no clear quality win over the well-understood `DeHalo_alpha`/`FineDehalo` formulation for a first stage. Revisit only if the classical port proves insufficient after on-content tuning. |
| **Process chroma as well** | Deferred — anime halos are overwhelmingly a luma-edge phenomenon; chroma dehalo risks colour bleeding around lines for little gain. `planes` defaults to luma-only; the hook exists if chroma dehalo is later justified. |

## Consequences

- **Positive**: removes the dominant anime artefact in VRAM with zero copy;
  matches mature CPU prior art (`DeHalo_alpha` + `FineDehalo`) so the behaviour
  is predictable; remove-only + the edge-ring gate keep line-art and gradients
  protected by construction; no new interop ABI surface (a pure transform — it
  does not link libpelorus); establishes the first stage of the `tune=anime`
  pipeline.
- **Negative / honest envelope**: the algorithm port is **compile-verified and
  glslang-clean**, but the perceptual on-content tuning of the defaults
  (`blur`, `darkstr`/`brightstr`, `lowsens`/`highsens`, `edge`, `ring`) against
  real anime and the quality proof against a clean ground truth are the
  documented follow-up. **No BD-rate or quality number is claimed in this PR.**
  The proof must be measured under the ADR-0111 methodology: SSIMULACRA2 and
  edge-region VMAF-NEG against a clean anime reference, plus CAMBI on the flats
  to confirm the pull does not introduce banding.
- **Follow-ups (documented, not in this PR)**: (1) on-content tuning of the
  defaults; (2) the SSIMULACRA2 / edge-region VMAF-NEG / CAMBI proof; (3) the
  remaining `tune=anime` stages (warp-AA, cadence denoise) and the preset that
  wires the chain.

## References

- `ffmpeg-patches/files/vf_pelorus_dehalo_vulkan.c`,
  `libpelorus/shaders/pelorus_dehalo.comp` (the standalone reference shader,
  kept in lockstep with the filter's inline GLSL);
  [docs/metrics/dehalo.md](../metrics/dehalo.md).
- [ADR-0001](0001-project-genesis.md) (the zero-copy GPU pre-encode invariant),
  [ADR-0100](0100-doc-substance-rule.md) (the per-surface doc bar this filter
  ships against), [ADR-0102](0102-flagship-smart-deband.md) (the
  `vf_pelorus_*_vulkan` filter idiom reused), [ADR-0111](0111-benchmark-methodology.md)
  (the clean-referenced proof methodology the follow-up must use).
- Prior art: HAvsFunc `DeHalo_alpha` and `FineDehalo` (VapourSynth/Avisynth).
- Source: `req` — port the HAvsFunc `DeHalo_alpha` + `FineDehalo` anime
  dehalo/dering to a single-pass zero-copy Vulkan compute filter as the
  foundation of a future `tune=anime` pre-encode pipeline.
