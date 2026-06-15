<!-- markdownlint-disable MD013 -->
# ADR-0115: vf_pelorus_grain_estimate — film-grain-synthesis parameter estimator

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: grain, fgs, vulkan, interop, ffmpeg, av1, h274

## Context

Film grain is the dual of the denoise lever (ADR-0112). Grain is temporally
incoherent, so a block encoder cannot inter-predict it and re-codes it as
residual every frame — an enormous, structureless bit tax. The other half of
the win is to **remove the grain before encode and re-synthesize it at the
decoder** from a compact parameter set: AV1 carries an AOM film-grain model in
the bitstream (`film_grain_params` OBU), and HEVC/H.265 + VVC/H.266 carry the
ITU-T H.274 film-grain-characteristics SEI (FGC). The encoder spends a few
hundred bytes once instead of megabits of residual per second, and the viewer
still sees grain.

That re-synthesis needs *parameters*. This filter measures them. The interop
ABI already reserved the `PEL_SEC_FILMGRAIN` section and the
`PelorusFilmGrainSection` struct (ADR-0103, bit `1u << 3`) — AOM params
field-for-field plus the H.274 mode scalars and a `grain_model` tag. This ADR
fills that reserved slot with a real GPU estimator; **no ABI bump is required**
(the section, the struct, and the conformance fixture already exist).

Grain is the one Pelorus stage that is *codec-specific* (deband / denoise /
motion help any encoder). The estimator itself is codec-neutral — it measures a
physical property of the source — and the `grain_model` tag plus the dual
parameter payload let a downstream bitstream filter target either container.

## Decision

Add `vf_pelorus_grain_estimate_vulkan`: a **pass-through analyzer** (frame in →
same frame out, side data added) modelled on `vf_pelorus_analyze_vulkan` — the
lazy-SPIR-V-init, `FF_VK_REP_FLOAT`, GPU-reduction-into-a-HOST_VISIBLE-SSBO,
submit+wait readback, side-data-emit pattern.

### Estimator — per-luma-band high-frequency residual variance

Grain is high-frequency, near-zero-mean additive noise whose strength varies
with local intensity (the AV1 / H.274 piecewise *scaling function* exists
precisely to model that intensity dependence). The estimator measures it
directly, per intensity band:

1. **High-pass.** For each luma pixel, subtract a 3×3 box low-pass:
   `resid = luma − mean3x3`. The box mean is the cheapest separable-free
   low-pass that survives an inline single-pass kernel; it removes structural
   content (edges, gradients) while leaving the grain residual.
2. **Edge gate.** A pixel whose 3×3 neighbourhood has high range (a real edge
   or texture) is *excluded* — its residual is structure, not grain. The
   estimate is taken only over locally-flat neighbourhoods, where the residual
   is grain. This is the same flat-test intuition deband and analyze use.
3. **Bin by intensity.** Each surviving pixel's `resid²` is accumulated into one
   of `PEL_GRAIN_BANDS` (8) luma-intensity bins, with a per-bin pixel count.
   The per-bin RMS residual is the grain standard deviation at that intensity.
4. **AR proxy.** A single global lag-1 spatial-correlation accumulator
   (`Σ resid·resid_right` over flat pixels) yields a coarse autocorrelation
   coefficient — grain is not white; a positive lag-1 correlation widens the
   synthesized grain's spectrum. v0.x emits a conservative low-lag AR
   approximation from this scalar; the full per-lag AR fit is a follow-up.

The GPU reduction is sliced (`PEL_GRAIN_SLICES`, 16) to cut atomic contention,
exactly as analyze/denoise do, and summed on the host.

### Host mapping → FGS parameters

The host turns the per-band RMS residual into the AV1 AOM piecewise scaling
function `y_points[value, scaling]`: band centre → `value`, band RMS scaled to
`[0,255]` by a `strength` AVOption → `scaling`. Monotonic-value points only
(AV1 requires strictly increasing `value`); empty bands are interpolated /
dropped. `scaling_shift`, `ar_coeff_lag`, `ar_coeff_shift`, `grain_scale_shift`
take AV1-legal defaults; `ar_coeffs_y` is seeded from the lag-1 proxy. Chroma
uses `chroma_scaling_from_luma` by default (cheap, and chroma grain is usually
luma-correlated); explicit chroma estimation is a follow-up.

The filter emits **both** of:

- `PEL_SEC_FILMGRAIN` (the Pelorus interop section) — the codec-neutral intent,
  the AV1 params, and the H.274 mode scalars + `grain_model` tag, for vmafx and
  downstream Pelorus tooling.
- a native `AV_FRAME_DATA_FILM_GRAIN_PARAMS` (`AV_FILM_GRAIN_PARAMS_AV1`) on the
  frame — so a downstream FFmpeg AV1 encoder / muxer that already honours that
  side data can act with **no Pelorus-specific BSF** (the analyze filter took
  the same "use a standard side-data channel the encoder already reads" path
  with ROI).

### FGS target: AV1 (AOM) primary, H.274 carried

AV1/AOM is the authoritative target for v0.x: FFmpeg has a complete public
`AVFilmGrainAOMParams` struct and a native `AV_FRAME_DATA_FILM_GRAIN_PARAMS`
channel that maps to it field-for-field, so the estimate is consumable today
with zero extra bitstream plumbing. The H.274 path (HEVC/VVC SEI FGC) needs the
larger component-model tables and an SEI writer; the section carries the H.274
mode scalars (`model_id`, `blending_mode`, `log2_scale`) and the `grain_model`
tag so the follow-up BSF can target it, but the **OBU/SEI bitstream writer is a
documented follow-up** (see Consequences). The estimator and the parameter
contract are complete and codec-neutral; only the final bitstream emission is
deferred.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Per-band HF-residual variance + edge gate (this) | Direct physical measurement; single inline pass; intensity-dependent → maps straight to the AV1 scaling function | Box low-pass is coarse; AR is a proxy | **Chosen** — best accuracy/risk for a first kernel; fills the reserved slot |
| Global single-σ noise estimate (one scalar) | Trivial | Throws away the intensity dependence the scaling function needs; bad on dark grain | Rejected — loses the whole point of the FGS scaling curve |
| Full per-lag AR (Yule-Walker) fit on GPU | Matches the AV1 AR model exactly | Multi-pass autocorrelation matrix solve; high correctness risk first time | Deferred — emit a lag-1 proxy now, fit later |
| Wavelet / DCT-domain HF isolation | Cleaner grain/structure split | Needs a transform pass + tuning; overkill for v0.x | Rejected for the first kernel |
| Write the AV1 OBU / H.274 SEI in this filter | One-stop | A filter is the wrong layer for bitstream surgery; needs a BSF; couples to one codec | Deferred to a dedicated BSF (follow-up) |
| H.274 as the primary target | Helps the HEVC/VVC majority today | No public field-for-field FFmpeg side-data path as clean as AOM; larger tables | AV1 first; H.274 scalars carried for the follow-up BSF |

## Consequences

- **Positive**: fills the reserved `PEL_SEC_FILMGRAIN` slot with a measured
  estimate; pairs with denoise (remove grain → re-synthesize) to recover the
  noise-tax bits while preserving the look; the native
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS` emit makes the AV1 path usable with no BSF;
  zero-copy, codec-neutral measurement.
- **Negative / honest envelope**: the box low-pass and lag-1 AR proxy are
  approximations — the synthesized grain will match the source's *intensity-
  dependent strength* well but its *spectral shape* only coarsely until the
  full AR fit lands. No accuracy proof is shipped in this PR (estimator +
  contract + build green only); the BD-rate / visual-match proof is a follow-up
  under the ADR-0111 methodology.
- **Follow-ups (documented, not in this PR)**: (1) the AV1 OBU / H.274 SEI
  **bitstream filter** that writes the params into the stream for encoders that
  do not consume the side data; (2) the full per-lag AR coefficient fit; (3)
  explicit chroma-grain estimation; (4) H.274 component-model table emission.

## References

- `ffmpeg-patches/files/vf_pelorus_grain_estimate_vulkan.c`,
  `libpelorus/shaders/pelorus_grain_estimate.comp`;
  [docs/metrics/grain_estimate.md](../metrics/grain_estimate.md).
- [ADR-0103](0103-interop-sidedata-abi.md) (the reserved `PEL_SEC_FILMGRAIN`
  slot + `PelorusFilmGrainSection` + `pel_grain_model`),
  [ADR-0109](0109-analyze-filter.md) (the GPU-reduction/readback pattern reused),
  [ADR-0112](0112-temporal-denoise.md) (the denoise lever this pairs with),
  [ADR-0114](0114-encoder-steering.md) (the "emit a standard side-data channel
  the encoder already reads" strategy, mirrored here with
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS`).
- `libavutil/film_grain_params.h` (`AVFilmGrainAOMParams`,
  `AVFilmGrainH274Params`, `AV_FILM_GRAIN_PARAMS_AV1`).
- Source: `req` — implement a film-grain-synthesis parameter estimator that
  measures grain on the GPU and emits FGS params as Pelorus side data so a
  downstream encoder/BSF can reproduce grain synthetically instead of spending
  bitrate coding noise.
