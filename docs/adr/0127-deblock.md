<!-- markdownlint-disable MD013 MD060 -->
# ADR-0127: vf_pelorus_deblock_vulkan — re-encode deblock/dering

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

The largest real-world pre-encode corpus is not pristine masters — it is
**re-transcode of already-compressed sources**: existing H.264/HEVC/VP9 files
being re-encoded to a newer codec. Those sources carry a signature artefact of
block-transform coding: **DCT block-edge discontinuities** ("blocking"). At the
boundaries of the prior codec's transform grid, quantisation leaves a small step
between adjacent blocks that has no analogue in the original signal. The new
encoder cannot tell that step apart from real image structure, so it spends real
bits coding the false residual at every block edge in every frame. Removing the
blocking before encode both restores the intended look and frees those bits.

This is distinct from what the existing **deband** stage
([ADR-0102](0102-flagship-smart-deband.md)) handles. Deband flattens the slow
contour stair-stepping inside *flat regions* and re-injects dither; it operates
on smooth gradients, not on the hard block-grid step. Deblocking targets the
*boundaries* of the prior transform grid — the high-frequency edge artefact that
deband neither sees nor removes. The two are complementary stages of the
re-transcode chain: deblock cleans the block edges, deband then cleans the flats.

Pelorus is a zero-copy GPU pre-encode pipeline ([ADR-0001](0001-project-genesis.md)):
once a frame drops to system RAM, PCIe bandwidth destroys throughput. The
existing deband, denoise, and analyze stages all run as Vulkan compute passes in
VRAM. A deblock stage that stayed on the GPU slots into that pipeline with no
copy, and covers the single most common pre-encode input — the re-transcode.

## Decision

Add `vf_pelorus_deblock_vulkan`: a **single-pass** Vulkan compute filter
(zero-copy, `FF_VK_REP_FLOAT` UNORM sampling, bit-depth-agnostic, modelled on
the existing `vf_pelorus_*_vulkan` filters). **Luma only; chroma passes
through.**

The algorithm, in one dispatch: at the prior codec's block grid (`bsize`), for
each pixel whose distance to the nearest horizontal or vertical block boundary
is within `edge`, apply a weak **conditional `[1 2 1]` low-pass across that
boundary**, **gated by the cross-boundary step**:

1. **Locate the boundary band** — the pixel's distance to the nearest block edge
   is `min(pos % bsize, bsize - pos % bsize)` on each axis. When that distance is
   `<= edge`, the pixel sits in the deblocked band around a boundary.
2. **Gate on the cross-boundary step** — sample the two pixels straddling the
   boundary. If their absolute difference is **below `thr`**, the step is a
   block-edge artefact and is smoothed; if it is **above `thr`**, it is real
   structure (a genuine edge that happens to fall on the grid) and is preserved
   untouched.
3. **Conditional `[1 2 1]` blend** — where the gate passes, compute the
   `(a + 2·c + b) · 0.25` low-pass across the boundary and blend the pixel toward
   it by `str`. The horizontal and vertical passes are independent, so a pixel on
   a grid corner is smoothed on both axes.

The gate is what makes this safe to run unconditionally on a re-transcode: a
large cross-boundary step is, by construction, never touched, so real edges
aligned to the grid survive while only the small artefactual steps are softened.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| **Run FFmpeg's CPU `pp=deblock` / `spp` via `hwdownload`/`hwupload`** | Rejected — round-tripping every frame to system RAM and back breaks the zero-copy invariant the whole pipeline exists to preserve; the PCIe cost dwarfs the compute. The CPU postprocessing filters also do not stay in VRAM where the rest of the chain runs. |
| **A learned / neural deblocker** | Rejected — out of scope for this stage: far higher implementation and runtime cost and a model-weights dependency, with no clear quality win over the well-understood gated low-pass for a first deblock. Revisit only if the classical filter proves insufficient after on-content tuning. |
| **Rely on the new encoder's in-loop deblocking filter** | Rejected — the new encoder's in-loop deblock operates on *its own* reconstruction grid, after it has already decided to spend bits coding the prior codec's baked-in block edges as residual. It cannot remove block edges that are already present in the *source* pixels; by the time the in-loop filter runs, the bits are already spent. The artefact must be removed from the source before the new encoder sees it. |
| **Process chroma as well** | Deferred — blocking is dominated by the luma transform grid, and chroma deblocking risks colour smearing across legitimate edges for little gain. `planes` defaults to luma-only (`0x1`); the hook exists if chroma deblock is later justified. |

## Consequences

- **Positive**: removes the dominant re-transcode artefact in VRAM with zero
  copy on the single most common pre-encode input; the cross-boundary-step gate
  preserves real structure by construction (a large step is never smoothed); no
  new interop ABI surface (a pure transform — it does not link libpelorus);
  complements the existing deband stage (block edges vs. flats) rather than
  duplicating it.
- **Negative / honest envelope**: the algorithm is **build-verified and
  glslang-clean**, but the perceptual on-content tuning of the defaults
  (`bsize`, `edge`, `thr`, `str`) against a real re-transcode corpus and the
  quality proof are the documented follow-up. **No SSIMULACRA2 or BD-rate number
  is claimed in this PR.** The proof must be measured under the
  [ADR-0111](0111-benchmark-methodology.md) methodology: SSIMULACRA2 and BD-rate
  on a re-transcode corpus (already-compressed source → new-codec encode, with
  and without the deblock stage), against the clean ground truth.
- **Follow-ups (documented, not in this PR)**: (1) on-content tuning of the
  defaults against a representative re-transcode corpus, including the right
  `bsize` per prior codec; (2) the SSIMULACRA2 / BD-rate proof on that corpus;
  (3) consider per-codec `bsize` presets once the corpus tuning is in.

## References

- `ffmpeg-patches/files/vf_pelorus_deblock_vulkan.c`,
  `libpelorus/shaders/pelorus_deblock.comp` (the standalone reference shader,
  kept in lockstep with the filter's inline GLSL);
  [docs/metrics/deblock.md](../metrics/deblock.md).
- [ADR-0001](0001-project-genesis.md) (the zero-copy GPU pre-encode invariant),
  [ADR-0100](0100-doc-substance-rule.md) (the per-surface doc bar this filter
  ships against), [ADR-0102](0102-flagship-smart-deband.md) (the
  `vf_pelorus_*_vulkan` filter idiom reused, and the deband stage this
  complements), [ADR-0111](0111-benchmark-methodology.md) (the clean-referenced
  proof methodology the follow-up must use).
- Prior art: FFmpeg `pp=deblock` / `spp` (libpostproc), H.264/HEVC in-loop
  deblocking filters (the conceptual model for a boundary-strength-gated
  low-pass).
- Source: `req` — add a re-encode deblock/dering Vulkan filter that smooths the
  prior codec's DCT block-edge discontinuities, gated by cross-boundary step, so
  the new encoder does not waste bits coding them as false residual.
