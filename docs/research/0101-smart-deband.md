<!-- markdownlint-disable MD013 -->
# Research digest 0101 — smart deband for pre-encode

Background for [ADR-0102](../adr/0102-flagship-smart-deband.md) and the
`vf_pelorus_deband_vulkan` filter. Why debanding closes a real gap on hardware
encodes, how the algorithm works, and why it is net-VMAF-positive only with
discipline.

## The problem

Block-transform encoders quantize flat, low-gradient regions (skies, dark walls,
fades) into visible stair-steps — false contours / banding. This is true of
**both HEVC and AV1** hardware encoders: their adaptive quantization is
rudimentary versus a CPU encoder (x265 / SVT-AV1), so they spend too few bits
keeping a smooth gradient smooth. Banding is one of the most VMAF-penalized
hardware-encode artifacts (the DLM term punishes contours absent from the
source). Debanding is pure pre-processing, so the fix is codec-agnostic — it
helps `hevc_nvenc`/`hevc_qsv`/… exactly as much as `av1_nvenc`.

## The algorithm

Modeled on **flash3kyuu_deband (f3kdb)**, with the core flat-test taken verbatim
from FFmpeg's `vf_deband.c` (Haas/Mahol):

1. **Reference sampling.** For each pixel, sample reference taps at a
   pseudo-random angle and distance within `range` pixels. Default (square mode)
   uses 4 reflected taps; the offset is a sin-fract hash of the coordinate
   (FFmpeg-exact for the static path; an integer-mix hash with `frame_seed` for
   the dynamic path).
2. **Flat test.** `avg` = mean of taps. *Average mode*: flat iff
   `|center - avg| < thr`. *All-refs mode*: flat iff every tap is within `thr`.
   Strict `<`, per-plane threshold.
3. **Decision.** Flat → output `avg` (debanded); else keep the original. Pelorus
   adds a **soft blend** (smoothstep over a transition band) to avoid a visible
   on/off seam — set `softness=0` to recover the hard `vf_deband` switch.
4. **Detail-protection mask.** Compute local variance from the taps; smoothstep
   it into a `protect` weight and suppress debanding (and grain) where activity
   is high. Banding lives in low-variance regions by definition, so this removes
   nearly all false-positive smoothing of real texture/edges — the single
   biggest lever for keeping the filter VMAF-positive.
5. **Grain / dither injection (the f3kdb stage `vf_deband` lacks).** Add
   zero-mean **TPDF** (triangular-PDF) blue-noise so the flattened gradient
   carries enough entropy that the encoder's quantizer cannot re-snap it into
   bands. TPDF decorrelates quantization error from the signal — the textbook
   condition that defeats contouring. Mostly applied where we debanded (scaled
   by the blend weight).
6. **Dither-down (16-bit internal).** Work in a 16-bit domain; on write to a
   lower output depth, add a sub-LSB dither before rounding so the bit-depth
   reduction itself doesn't re-band.

## Why it is net-VMAF-positive (with discipline)

- **Naive frame-vs-source VMAF can drop**: dither adds "error" the source didn't
  have, so VIF/DLM on the debanded-vs-source frame rises. The net win shows on
  the **encode round-trip**: without debanding the encoder *creates* banding
  (contours absent from source) that VMAF penalizes hard; the dithered input
  prevents that, so decoded-vs-source VMAF rises net.
- **Amplitude discipline**: keep luma grain ≈ ±1–2 output LSB; keep chroma grain
  low or zero (chroma noise is more visible and more expensive). Above ~±3 LSB
  the dither costs more than the banding it prevents.
- **Spectrum**: TPDF + blue-noise puts dither energy at high spatial frequency
  where the HVS contrast-sensitivity is lowest — minimal visibility per unit of
  contour suppression.
- **Dynamic grain**: a static dither pattern is learned and stripped by the
  encoder across a scene; re-seeding per frame forces it to keep the gradient.
  Cap amplitude ~0.75× when dynamic to control flicker/bitrate.
- **Encode at 10-bit even for 8-bit delivery** when the path allows — the extra
  quantizer headroom is where most of the win over "debanding alone" comes from.

## Recommended preset (dark-scene pre-encode)

`range=15, thr≈0.008–0.015 (normalized), sample=square, blur=average +
softness>0, grainy≈1–1.5 LSB, grainc=0, dither=bluenoise, dynamic=1,
protect=1, detail≈0.06`, output 10-bit. See [docs/metrics/deband.md](../metrics/deband.md)
for the full option reference and a VMAF-measured tuning loop.

## Sources

- `libavfilter/vf_deband.c` — the flat-test, sin-hash, threshold scaling, and
  average-vs-all-refs semantics are taken directly from it.
- flash3kyuu_deband (f3kdb) — the grain/dither, 16-bit-internal dither-down,
  dynamic seed, and detail-mask extensions.
- Gemini design discussion captured in `.workingdir/*.md`.
