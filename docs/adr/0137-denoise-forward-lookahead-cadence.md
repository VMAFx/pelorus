<!-- markdownlint-disable MD013 -->
# ADR-0137: Forward-lookahead (bidirectional) temporal denoise — cadence-aware

- **Status**: Accepted (built + validated; opt-in `lookahead=1`, default 0)
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

`vf_pelorus_denoise_vulkan`'s temporal term is **causal**: it averages the
current frame against a ring of *previous* frames, gated by `tcut` (a tap whose
delta exceeds `tcut` breaks the walk, so motion / scene-cuts / drawing-changes
cannot ghost). This is correct but asymmetric, and animation exposes the gap.

Limited animation holds each drawing for 2–3 video frames (2s/3s cadence). With
independent per-frame noise (grain, capture, prior compression) on a held
drawing `{A0, A1}`, averaging the duplicates recovers the clean drawing — but
the causal walk only helps the **trailing** frame: denoising `A1` finds `A0`
(prev, same drawing, low delta → averaged), while the **leading** frame `A0`
sees only the *previous, different* drawing (`tcut` breaks) → it gets no temporal
help. Half the frames of every held run are under-denoised.

This is the inverse of the encoder-steering negatives: it is **preprocessing**
(cleaning the source), which is where Pelorus wins.

## Premise validation (measured, 2026-06-20)

Synthetic 2s-cadence clip: 12 distinct BBB drawings, each held ×2 (24 frames),
with independent per-frame noise (`noise=alls=20:allf=t+u`). Denoised with the
current causal filter (`sigmat=0.08 strength=0.9 prev=4 blend=0.85`), PSNR vs the
clean held cadence:

| | PSNR vs clean |
|---|---|
| noisy (baseline) | 32.89 dB |
| causal denoise | 34.42 dB (**+1.53 dB**) |

Fully averaging the two held duplicates would give ≈ +3 dB (noise ÷ √2). The
causal filter captures only ~half of that — exactly the leading-frame gap. The
headroom (≈ +1.5 dB more, ~doubling the recovery) is real, unlike the encoder-RC
bets. (This is the premise check the fp16/subgroup/per-shot-CRF negatives taught
us to run *before* building.)

## Decision (proposed)

Add a **forward lookahead**: a small ring of *next* frames so the temporal walk
is bidirectional. `A0` then finds `A1` (next, same drawing) and both held frames
recover ≈ +3 dB. Cadence-awareness falls out of the existing `tcut` gate (held
duplicates are low-delta in *both* directions; a drawing change breaks the walk
either way) — no explicit cadence classifier needed for v1.

Scope for v1 (minimal, same-coordinate forward taps; forward-MC is a follow-up
since the cadence case is static-held, no motion):

1. **Filter lifecycle**: convert from the immediate `filter_frame` model to a
   1-frame-delay buffered model (`activate()`): hold the incoming frame, emit the
   previous one processed with the new frame as its forward tap; flush the last
   held frame at EOF. (The filter is currently causal — "no 1-frame latency, no
   EOF flush" — so this is the load-bearing change.) `lookahead` AVOption,
   **default 0** (causal, no latency — see Built result), range 0..1 for v1
   (0 = today's causal behaviour, bit-identical; 1 = one forward tap).
2. **Shader**: one `next0_images[]` binding (mirror `prev0_images`); a forward
   same-coord temporal tap in the temporal loop, `tcut`-gated like the prev taps.
3. **Lockstep** `pelorus_denoise.comp` (AGENTS rule 4).

## Built result (2026-06-20)

Implemented (a 6th `next0_images` storage-image binding, an `activate()`
1-frame-delay + EOF-flush lifecycle, a `tcut`-gated forward tap, push-const
`actual_next`, `.comp` lockstep) and validated against the cadence oracle on the
2s-cadence noisy clip (24 frames, `sigmat=0.08 strength=0.9 prev=4 blend=0.85`):

| | PSNR vs clean | frames |
|---|---|---|
| lookahead=0 (causal) | 35.60 dB | 24 |
| lookahead=1 (forward) | **35.98 dB (+0.37 dB)** | 24 |

The gain is **real but modest** (≈+0.37 dB average, concentrated on the leading
frame of each held run), well short of the +1.5 dB upper-bound estimate — the
spatial NLM term already partly covers the leading frames, so the forward tap
only recovers the remainder. `lookahead=0` is bit-identical to the causal filter;
frame count is preserved across the delay/flush. An independent review confirmed
the `activate()` frame-ownership, descriptor wiring (binding 9), push-constant
std430 layout, and shader/filter lockstep are correct.

**Decision: ship `lookahead=1` opt-in, default 0.** The benefit is niche
(animation cadence) and modest, while the forward tap adds a 6th image binding +
a frame of latency for every denoise; defaulting off keeps the common
(live-action) path untouched and matches the `tile` opt-in pattern. Cadence /
animation pipelines (`tune=anime`) enable it explicitly.

## Validation plan

- **Cadence oracle** (above): the forward-lookahead recovery must approach +3 dB
  (vs the causal +1.5 dB) on the synthetic 2s-cadence clip.
- **Correctness**: output frame count == input frame count (no drop/dup at EOF);
  `lookahead=0` is bit-identical to the current causal filter (SSIM 1.0).
- **Live-action**: SSIM-vs-clean on a noisy real clip (bidirectional same-coord
  taps also help low-motion live-action); confirm no regression vs causal.
- **Kill-criterion**: if the cadence recovery does not improve materially over
  causal, or `lookahead=0` is not bit-identical, do not ship.

## Alternatives considered

- **Causal-only, boost spatial strength on the leading frame.** Rejected —
  spatial denoising blurs detail; it cannot recover the held-duplicate noise the
  way lossless temporal averaging of the same drawing does.
- **Explicit cadence classifier (detect the 2s/3s pattern).** Deferred — the
  `tcut` gate already discriminates same-drawing from drawing-change in both
  directions; an explicit classifier is only needed for cadence-driven *frame
  decimation* (a separate, encoder-facing feature), not for the denoise.
- **Forward motion-compensated taps.** Deferred to a follow-up — needs forward
  MV from `pelorus_mc`; the cadence win is the static-held case (no motion).

## Consequences

- Adds a 1-frame latency + an EOF flush (acceptable for offline pre-encode; the
  header invariant "no latency / no flush" is updated). `lookahead=0` preserves
  today's behaviour exactly.
- A genuine preprocessing improvement (Pelorus's proven lane), validated in
  premise before the build — the first positive direction after the v0.11–v0.13
  encoder-RC negatives.

## References

- ADR-0112 (the denoise filter), ADR-0131 (the MC→denoise warp — the causal
  motion path this complements), ADR-0125 (the anime tune pipeline this serves).
- Premise-check data: `docs/development/bench-results.md` (v0.14, to accompany
  the build).
