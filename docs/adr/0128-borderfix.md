<!-- markdownlint-disable MD013 MD060 -->
# ADR-0128: vf_pelorus_borderfix_vulkan — dirty-line / border repair

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

Real-world pre-encode sources are rarely clean to the edge. Cropped, telecined,
and analog-captured material carries a band of **garbage rows and columns at the
frame edge**: half-pixels left by an off-grid crop, clamp/repeat lines from an
upstream resize, head-switching noise on the bottom of a VHS capture, the black
or mismatched strip on a letterbox seam. None of it is real signal, but the
encoder cannot tell — it spends real bits coding the random edge band as
residual in every frame, and the band reads as a dirty border to a viewer.
Replacing the dirty band with the nearest clean interior pixel before the encode
both frees those bits and cleans the visible border.

FFmpeg already has a CPU answer to this — `fillborders=smear`, which clamps each
border pixel onto the nearest clean edge. But Pelorus is a **zero-copy GPU
pre-encode pipeline** ([ADR-0001](0001-project-genesis.md)): frames stay in VRAM
from decode, through the Vulkan compute filters, to the hardware encoder. Routing
every frame out to `fillborders` would force an `hwdownload`/`hwupload` round
trip to system RAM and back across PCIe — the exact copy the whole pipeline
exists to avoid. A border-repair stage that stayed on the GPU slots into that
pipeline with no copy. It is also the natural **first** stage of any pre-encode
chain: the edges should be cleaned before any later stage measures variance,
flattens flats, or steers bits, so the dirty band never pollutes a downstream
statistic.

## Decision

Add `vf_pelorus_borderfix_vulkan`: a **single-pass** Vulkan compute filter
(zero-copy, `FF_VK_REP_FLOAT` UNORM sampling, bit-depth-agnostic, modelled on the
existing `vf_pelorus_*_vulkan` filters). It is the GPU equivalent of
`fillborders=smear`. **All planes are processed by default**; the band widths are
interpreted in **each plane's own pixels**.

The algorithm, in one dispatch, is a **clamp onto the clean interior rectangle**.
Given the per-edge dirty-band widths `left`, `right`, `top`, `bottom` and the
plane size `w × h`, the clean interior is the rectangle
`[left, w − 1 − right] × [top, h − 1 − bottom]`. For each output pixel at
`(x, y)`:

1. **Clamp the read coordinate onto the clean rect** —
   `cx = clamp(x, left, w − 1 − right)` and
   `cy = clamp(y, top, h − 1 − bottom)`. A pixel already inside the clean rect
   maps to itself; a pixel inside a dirty band maps to the nearest pixel on the
   clean edge.
2. **Smear the clean edge outward** — store the input sample read from the
   clamped coordinate. Each pixel in the dirty band is thus replaced by the
   nearest clean interior pixel: the good edge is extended outward over the
   garbage band, exactly as `fillborders=smear` does on the CPU.

The smear is **exact and deterministic** — there is no threshold, no blend, and
no estimation. The only choice the user makes is the band widths, set to match
the source's actual dirty edge. With all widths at their default of `0`, the
filter is byte-identical pass-through. A plane not selected in the `planes`
bitmask is copied through unchanged.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| **Run FFmpeg's CPU `fillborders=smear` via `hwdownload`/`hwupload`** | Rejected — round-tripping every frame to system RAM and back breaks the zero-copy invariant, which is the entire reason to have a GPU border-repair stage at all. In a Vulkan pipeline `fillborders` is the one stage that would force the copy; the PCIe cost dwarfs the trivial compute. |
| **Mirror / reflect or fixed-colour fill modes** | Deferred — smear (clamp onto the clean edge) is the safe default: it never introduces a new value and never folds the dirty band's own garbage back into the frame, the way a mirror would. The `left`/`right`/`top`/`bottom` AVOption surface already accommodates a future `mode` knob; mirror and fixed-colour fill are follow-ups, not a reason to widen the first version. |
| **Crop the dirty band, then pad back** | Rejected — cropping changes the frame dimensions mid-pipeline, which ripples into the encoder's resolution and every downstream stage's coordinate math; padding then has to re-invent the smear anyway. Repairing in place keeps the geometry fixed and is strictly simpler. |
| **Process luma only** | Rejected — the dirty band is present in chroma as well as luma (an off-grid crop leaves half-pixels in every plane), so cleaning only luma would leave a coloured fringe. `planes` defaults to `0xF` (all planes); the per-plane-pixel width interpretation makes the chroma band the correct fraction of the luma band automatically. |

## Consequences

- **Positive**: removes the dirty edge band in VRAM with zero copy, sparing the
  Vulkan pipeline the `hwdownload`/`hwupload` a CPU `fillborders` would force;
  the smear is exact and deterministic, so there is no tuning risk and no quality
  A/B needed for correctness; all planes are handled with per-plane-pixel widths,
  so chroma is cleaned at the right scale automatically; no new interop ABI
  surface (a pure transform — it does not link libpelorus); a natural first stage
  that cleans the edges before any later stage measures or processes them.
- **Negative / honest envelope**: the transform itself is exact, so the only
  "tuning" is **choosing the band widths to match the source's dirty edge** — the
  filter cannot detect the dirty band's width for the user. Mis-set widths either
  leave part of the dirty band (too narrow) or smear away real picture content
  (too wide). The defaults are `0` (no-op) precisely because there is no safe
  universal width. A **BD-rate note on a dirty-border corpus** (re-encode of a
  cropped/telecined/analog-captured source, with and without the borderfix stage)
  is a nice-to-have follow-up under the [ADR-0111](0111-benchmark-methodology.md)
  methodology — but the correctness of the smear does not depend on it.
- **Follow-ups (documented, not in this PR)**: (1) optional `mode` knob for
  mirror / fixed-colour fill once a use case justifies it; (2) the BD-rate note
  on a dirty-border corpus; (3) optional automatic dirty-band-width detection
  (out of scope for the first version — the deterministic smear is the contract).

## References

- `ffmpeg-patches/files/vf_pelorus_borderfix_vulkan.c`,
  `libpelorus/shaders/pelorus_borderfix.comp` (the standalone reference shader,
  kept in lockstep with the filter's inline GLSL);
  [docs/metrics/borderfix.md](../metrics/borderfix.md).
- [ADR-0001](0001-project-genesis.md) (the zero-copy GPU pre-encode invariant
  this filter exists to preserve), [ADR-0100](0100-doc-substance-rule.md) (the
  per-surface doc bar this filter ships against),
  [ADR-0111](0111-benchmark-methodology.md) (the clean-referenced proof
  methodology the BD-rate follow-up would use).
- Prior art: FFmpeg `vf_fillborders` (the `smear` mode is the conceptual model —
  clamp each border pixel onto the nearest clean edge), and the standard crop /
  telecine / analog-capture edge artefacts this repairs.
- Source: `req` — add a GPU dirty-line / border-repair Vulkan filter, the
  zero-copy equivalent of `fillborders=smear`, so cropped/telecined/analog
  sources do not cost the encoder bits on the garbage edge band and do not read
  as a dirty border.
