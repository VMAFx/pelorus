<!-- markdownlint-disable MD013 -->
# ADR-0118: av1_nvenc film grain — carry the FGS estimate into NVENC hardware AV1 film-grain synthesis

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: grain, fgs, av1, nvenc, ffmpeg, encoder-steering, hardware

## Context

[ADR-0115](0115-grain-estimate.md) added `vf_pelorus_grain_estimate_vulkan`: a
GPU estimator that measures film grain and emits, per frame, AV1 (AOM)
film-grain-synthesis parameters as both a native
`AV_FRAME_DATA_FILM_GRAIN_PARAMS` (`AV_FILM_GRAIN_PARAMS_AV1`) and the
codec-neutral `PEL_SEC_FILMGRAIN` interop section. It deferred the "bitstream
writer" to follow-ups. The round-trip is asymmetric by encoder:

- **AV1 software encoders** (`libaom-av1`, `libsvtav1` via FFmpeg's plumbing)
  consume the native film-grain frame side data — the AV1 leg round-trips today.
- **HEVC** is handled by [ADR-0117](0117-grain-fgs-bsf.md): the `pelorus_fgs`
  bitstream filter inserts the H.274 FGC SEI.
- **`av1_nvenc`** does neither. Stock `nvenc.c` never enables NVENC's AV1
  film-grain synthesis path, so the estimate is dropped and the grain is coded
  as residual — exactly the bit tax FGS exists to remove. NVENC *can* synthesize
  AV1 film grain in hardware (`NV_ENC_CONFIG_AV1::enableFilmGrainParams` +
  `NV_ENC_FILM_GRAIN_PARAMS_AV1`, SDK 12.0+), but FFmpeg exposes no way to drive
  it from frame side data.

This ADR fills the hardware-AV1 gap: a libavcodec edit that pushes the AOM
parameter set into NVENC's AV1 film-grain config struct. The estimator and the
parameter contract are unchanged; this is purely the final emission leg for
`av1_nvenc`. It mirrors the encoder-steering strategy of
[ADR-0114](0114-encoder-steering.md) — consume a standard side-data channel the
encoder already understands — applied to film grain.

## Decision

Add a `pelorus_film_grain` AVOption (default off, registered on `av1_nvenc`
only) to the NVENC patch family, modelled structurally on the NVENC ROI
(ADR-0114) and external-ME-hint (ADR-0116) patches: a hand-maintained unified
diff in `ffmpeg-patches/files/`, compile-gated, default off, no `libpelorus`
link.

### Plumbing — config-enable at init, per-frame update

NVENC's AV1 film-grain params are an init-time config struct
(`NV_ENC_CONFIG_AV1::filmGrainParams`, gated by `enableFilmGrainParams`) with a
per-picture update path (`NV_ENC_PIC_PARAMS_AV1::filmGrainParamsUpdate`). The
grain estimate is per-frame, so:

1. **At init** (when the option is set), set `enableFilmGrainParams = 1` and
   point `filmGrainParams` at a persistent `NV_ENC_FILM_GRAIN_PARAMS_AV1` held
   in `NvencContext`. It is zero-initialised, so `applyGrain == 0` — a no-op
   until a frame carries an estimate.
2. **Per frame**, refill that struct from the frame's grain estimate and raise
   `filmGrainParamsUpdate` in the AV1 pic params. A frame with no estimate
   leaves the flag clear, so NVENC keeps the previously-signalled params (or,
   before the first estimate, the zero-init no-op). This is the time-varying
   model and the natural fit for a per-frame estimator.

### Input — native channel preferred, interop fallback

The setup reads the estimate from the **native
`AV_FRAME_DATA_FILM_GRAIN_PARAMS`** side data (`AV_FILM_GRAIN_PARAMS_AV1`,
`codec.aom`) first — the canonical, public, field-for-field channel, requiring
no `libpelorus` link in libavcodec for a default-off feature. Failing that, it
parses the `PEL_SEC_FILMGRAIN` interop section inline (bounds-checked,
allocation-free, against the frozen append-only ABI R1..R6), mirroring the
`PEL_SEC_MOTION` reader in the external-ME-hint patch. Either source yields an
`AVFilmGrainAOMParams`.

### Mapping — AVFilmGrainAOMParams → NV_ENC_FILM_GRAIN_PARAMS_AV1

The AOM set maps field-for-field onto the NVENC struct, shifted to NVENC's
"minus N" / "plus 128" bitfield conventions and clamped to the field widths:

| AOM (`AVFilmGrainAOMParams`) | NVENC (`NV_ENC_FILM_GRAIN_PARAMS_AV1`) |
|---|---|
| `num_y_points`, `y_points[i][{0,1}]` | `numYPoints`, `pointYValue[i]` / `pointYScaling[i]` |
| `num_uv_points[{0,1}]`, `uv_points` | `numCbPoints`/`numCrPoints`, `pointCb/CrValue`/`Scaling` |
| `chroma_scaling_from_luma` | `chromaScalingFromLuma` |
| `overlap_flag` | `overlapFlag` |
| `limit_output_range` | `clipToRestrictedRange` |
| `scaling_shift` (AV1 [8,11]) | `grainScalingMinus8` = `scaling_shift − 8` |
| `ar_coeff_lag` | `arCoeffLag` |
| `ar_coeff_shift` (AV1 [6,9]) | `arCoeffShiftMinus6` = `ar_coeff_shift − 6` |
| `grain_scale_shift` | `grainScaleShift` |
| `ar_coeffs_y[i]` | `arCoeffsYPlus128[i]` = `ar_coeffs_y[i] + 128` |
| `ar_coeffs_uv[{0,1}][i]` | `arCoeffsCb/CrPlus128[i]` = `+ 128` |
| `uv_mult[{0,1}]`, `uv_mult_luma[{0,1}]` | `cb/crMult`, `cb/crLumaMult` |
| `uv_offset[{0,1}]` (9-bit [-256,255]) | `cb/crOffset` |
| (synthesis recommended) | `applyGrain` |

AR-coefficient copy counts follow the AV1 model: luma `2·lag·(lag+1)`, chroma
that plus one when luma points are present.

### Scope — the hardware-AV1 NVENC leg

This is the `av1_nvenc` leg only. H.264/HEVC NVENC have no AV1 film grain, so
the option is registered on `av1_nvenc` alone. The QSV / AMF / VAAPI hardware
AV1 film-grain legs and the on-hardware BD-rate proof are documented follow-ups.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Config-enable + per-frame `filmGrainParamsUpdate` (this) | Time-varying, matches the per-frame estimator; uses NVENC's documented update path; graceful no-op | Slightly more plumbing than a static set | **Chosen** — correct fit for a per-frame estimate, minimal new code |
| Static one-shot film grain at init only | Simplest | Cannot track a time-varying estimate; init runs before any frame is seen, so the first frame's params are unavailable anyway | Rejected — the update path is the natural fit and barely more code |
| Patch only the AV1 OBU emission by hand | No SDK dependency | Re-implements the AV1 film_grain OBU + bit-packing NVENC already does in hardware | Rejected — NVENC synthesizes it; we only feed params |
| Link `libpelorus` into libavcodec and read the interop section only | One code path | Adds a pkg-config dependency to a default-off feature; the native channel is the public, canonical one | Rejected — prefer the native channel, parse interop inline as fallback |
| Register on all NVENC encoders | Uniform | H.264/HEVC NVENC have no AV1 film grain; the option would be a dead no-op there | Rejected — `av1_nvenc` only is the honest surface |

## Consequences

- **Positive**: completes the hardware-AV1 leg of the FGS round-trip ADR-0115
  started (AV1 software via native side data; HEVC via the ADR-0117 BSF; now
  `av1_nvenc` via this); pairs with `pelorus_denoise_vulkan` (remove grain →
  re-synthesize) to recover the noise-tax bits while preserving the look on
  NVIDIA hardware AV1; opt-in, no-op by default, compile- and capability-gated,
  no `libpelorus` link, no device/box assumptions.
- **Negative / honest envelope**: no grain-match or BD-rate number ships. The
  estimate is wired into NVENC's AV1 film-grain config and **verified** to
  compile against the ffnvcodec 13.0 headers (with the SDK-12.0 film-grain path
  active), register on `av1_nvenc` only (absent from `hevc_nvenc`/`h264_nvenc`),
  and replay onto pristine n8.1.1. The on-hardware visual / BD-rate proof is a
  follow-up under the [ADR-0111](0111-benchmark-methodology.md) methodology, and
  is gated on hardware with an SDK-12.0+ driver. The chroma path inherits the
  estimator's `chroma_scaling_from_luma` default (ADR-0115); explicit chroma
  grain is that ADR's follow-up.
- **Follow-ups (documented, not in this PR)**: (1) the QSV / AMF / VAAPI
  hardware-AV1 film-grain legs; (2) the on-HW BD-rate / grain-match proof; (3)
  explicit chroma-grain params once the estimator emits them.

## References

- `ffmpeg-patches/files/nvenc-pelorus-film-grain.patch`
  (→ `ffmpeg-patches/0011-nvenc-pelorus-film-grain.patch`);
  [docs/usage/ffmpeg.md](../usage/ffmpeg.md) (the `-pelorus_film_grain` recipe);
  [docs/rebase-notes.md](../rebase-notes.md) (patch 0011).
- [ADR-0115](0115-grain-estimate.md) (the grain estimator + the AV1 param
  contract this consumes),
  [ADR-0117](0117-grain-fgs-bsf.md) (the HEVC/H.274 leg, for scope contrast),
  [ADR-0114](0114-encoder-steering.md) (the "emit a standard side-data channel
  the encoder already reads" strategy this applies to film grain),
  [ADR-0116](0116-pelorus-mc.md) (the NVENC external-ME-hint patch this mirrors
  structurally — inline interop reader, compile-gated, default off, no libpelorus
  link),
  [ADR-0103](0103-interop-sidedata-abi.md) (`PEL_SEC_FILMGRAIN` +
  `PelorusFilmGrainSection`),
  [ADR-0111](0111-benchmark-methodology.md) (the deferred BD-rate proof).
- FFmpeg n8.1.1: `libavcodec/nvenc.c` / `nvenc.h` / `nvenc_av1.c`;
  `libavutil/film_grain_params.h` (`AVFilmGrainAOMParams`,
  `AV_FILM_GRAIN_PARAMS_AV1`).
- ffnvcodec `nvEncodeAPI.h`: `NV_ENC_FILM_GRAIN_PARAMS_AV1`,
  `NV_ENC_CONFIG_AV1::enableFilmGrainParams` / `filmGrainParams`,
  `NV_ENC_PIC_PARAMS_AV1::filmGrainParamsUpdate` (SDK 12.0+).
- Source: `req` — implement AV1 hardware film-grain via NVENC so `av1_nvenc`
  carries the grain estimate into the AV1 bitstream using
  `NV_ENC_FILM_GRAIN_PARAMS_AV1`, the HW-AV1 leg of the FGS round-trip.
