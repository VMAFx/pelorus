<!-- markdownlint-disable MD013 -->
# ADR-0117: pelorus_fgs — H.274 / HEVC film-grain SEI bitstream filter

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: grain, fgs, h274, hevc, bsf, ffmpeg, sei

## Context

[ADR-0115](0115-grain-estimate.md) added `vf_pelorus_grain_estimate_vulkan`: a
GPU estimator that measures film grain and emits, per frame, the Pelorus
`PEL_SEC_FILMGRAIN` interop section (codec-neutral intent + AV1 params + H.274
mode scalars + a `grain_model` tag) **and** a native
`AV_FRAME_DATA_FILM_GRAIN_PARAMS` (`AV_FILM_GRAIN_PARAMS_AV1`). It explicitly
deferred the "OBU/SEI bitstream writer" to a follow-up.

That follow-up is asymmetric by codec:

- **AV1 already round-trips.** FFmpeg AV1 encoders consume the native
  `AV_FRAME_DATA_FILM_GRAIN_PARAMS` channel the estimator attaches, so the AV1
  leg works today with no extra tooling.
- **HEVC does not.** HEVC/H.265 (and VVC/H.266) carry grain as the ITU-T H.274
  Film Grain Characteristics (FGC) SEI, but stock FFmpeg HEVC encoders do **not**
  carry film-grain frame side data into the coded bitstream. There is no
  automatic path from the estimator's parameters to an HEVC elementary stream.

This ADR fills the HEVC gap with a libavcodec bitstream filter that inserts the
H.274 FGC SEI. The estimator + the parameter contract are unchanged; this is
purely the final bitstream-emission leg for HEVC.

## Decision

Add `pelorus_fgs`, an HEVC bitstream filter built on the coded-bitstream (CBS)
generic-BSF framework (`cbs_bsf.c`), modelled on `hevc_metadata` (the
`h265_metadata.c` BSF). Each access unit is parsed into a
`CodedBitstreamFragment`; the BSF adds one FGC SEI message via
`ff_cbs_sei_add_message(..., SEI_TYPE_FILM_GRAIN_CHARACTERISTICS, &fgc, NULL)`
as a prefix NAL; CBS reassembles the unit. The FGC payload is the standard
`H265RawFilmGrainCharacteristics` struct, so the existing CBS H.265 writer
serializes it (no hand-coded bit-packing).

### Input contract — a static model via AVOptions

The H.274 model is supplied through AVOptions (`model_id`, `blending_mode`,
`log2_scale`, `components`, `intensity_low`/`high`, `scale_y`, `scale_c`,
`persistence`, `skip_existing`) — the canonical FFmpeg metadata-BSF contract.
The producer's H.274 scalars (`h274_model_id` / `h274_blending_mode` /
`h274_log2_scale`) and per-band RMS scaling map directly onto these options; the
recipe is in [docs/usage/grain-fgs-bsf.md](../usage/grain-fgs-bsf.md).

The BSF is opt-in and a no-op by construction: with no component selected
(`components=0`) it passes the stream through unchanged; `skip_existing` avoids
double-stamping a stream a previous stage already marked. It does **not** link
`libpelorus` — it consumes the model via AVOptions, not the interop ABI — so it
adds no pkg-config dependency to the patch stack.

### Colour-description inference

The inserted FGC SEI omits its own colour description
(`separate_colour_description_present_flag = 0`), so the H.265 syntax infers six
fields (luma/chroma bit depth, full-range flag, primaries, transfer, matrix) from
the active SPS, and the CBS *writer* verifies the payload already holds the
inferred value. The BSF therefore caches those fields from the most recent SPS in
the stream and pre-fills them (mirroring the H.265 VUI inference defaults), and
passes an access unit through untouched until the first SPS establishes them.

### Scope — the static-model HEVC leg

A BSF operates on `AVPacket`s, and no stock encoder forwards the estimator's
**per-frame** grain frame side data onto the coded packet, so per-frame
estimate->SEI plumbing through an arbitrary HEVC encoder is not expressible in
n8.1.1. The static-model path is the honest, fully general one and matches every
metadata BSF in FFmpeg. v0.x emits a single intensity interval; the per-band ->
multi-interval mapping, a per-frame side-data channel, and the H.264 / VVC legs
are documented follow-ups.

### Patch-stack integration

A BSF is not an AVFilter. The source lands in `libavcodec/bsf/pelorus_fgs.c`
(canonical `ffmpeg-patches/files/h265_pelorus_fgs_bsf.c`), and the registration
edits target the libavcodec surfaces — `bitstream_filters.c` (extern),
`bsf/Makefile` (OBJS), `configure` (`pelorus_fgs_bsf_select="cbs_h265"`). It is
committed last in `generate.sh` so it lands as patch **0008**; 0001-0007 keep
their numbers. `generate.sh` expresses it with the same drop-file +
inject-registration python block as the filter loop, just against the libavcodec
BSF surfaces instead of the libavfilter ones.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| CBS generic BSF + `ff_cbs_sei_add_message` (this) | Reuses the tested H.265 SEI writer; mirrors `hevc_metadata`; no hand-coded bit-packing | Static model only (AVOption-driven) | **Chosen** — correct layer, minimal new code, fully general |
| Hand-code the FGC SEI NAL bytes | No CBS dependency | Re-implements SEI RBSP + emulation-prevention + the H.274 syntax; high correctness risk; duplicates CBS | Rejected — CBS already does this correctly |
| Patch an HEVC encoder to read `AV_FRAME_DATA_FILM_GRAIN_PARAMS` | Per-frame model possible | Couples to one encoder; libx265/nvenc/qsv each differ; no generic path | Rejected — a BSF is codec-stream-level and encoder-agnostic |
| Carry the estimate as packet side data, read it per-frame in the BSF | Time-varying model | No stock encoder forwards frame->packet film-grain side data in n8.1.1; needs an upstream channel | Deferred — documented follow-up |
| Do AV1 only, declare HEVC out of scope | Simplest | Leaves the HEVC/VVC majority with no grain round-trip | Rejected — closing the HEVC gap is the point of this ADR |

## Consequences

- **Positive**: completes the HEVC leg of the FGS round-trip ADR-0115 started;
  pairs with `pelorus_denoise_vulkan` (remove grain -> re-synthesize) to recover
  the noise-tax bits while preserving the look on HEVC, not just AV1; reuses the
  CBS H.265 SEI writer (no new bitstream surgery); opt-in, no-op by default, no
  device/box assumptions, no `libpelorus` dependency.
- **Negative / honest envelope**: a **static** model only (one FGC model inserted
  on every AU), driven by AVOptions, with a single intensity interval; the
  per-frame estimate is not plumbed through the encoder. No BD-rate / visual-match
  proof is shipped — only build-green plus an end-to-end SEI insert/parse-back
  smoke test (libx265 stream -> `pelorus_fgs` -> `trace_headers` shows the FGC SEI
  with the configured values; `components=0` is a byte-identical pass-through;
  `skip_existing` does not double-stamp). The quality proof is a follow-up under
  ADR-0111.
- **Follow-ups (documented, not in this PR)**: (1) per-band -> multi-interval FGC
  mapping; (2) a side-data channel carrying the per-frame estimate onto the coded
  packet for a time-varying model; (3) the H.264 and VVC/H.266 legs; (4) the full
  H.274 component-model tables.

## References

- `ffmpeg-patches/files/h265_pelorus_fgs_bsf.c` (-> `libavcodec/bsf/pelorus_fgs.c`),
  `ffmpeg-patches/0008-add-pelorus_fgs_bsf.patch`;
  [docs/usage/grain-fgs-bsf.md](../usage/grain-fgs-bsf.md).
- [ADR-0115](0115-grain-estimate.md) (the grain estimator + the deferred
  OBU/SEI writer this ADR delivers for HEVC),
  [ADR-0103](0103-interop-sidedata-abi.md) (`PEL_SEC_FILMGRAIN` + the H.274 mode
  scalars + `pel_grain_model`),
  [ADR-0104](0104-ffmpeg-patch-stack.md) (the patch-stack model the BSF extends),
  [ADR-0114](0114-encoder-steering.md) (the AV1 "emit a standard side-data channel
  the encoder already reads" path this complements for HEVC),
  [ADR-0111](0111-benchmark-methodology.md) (the deferred BD-rate proof).
- FFmpeg n8.1.1: `libavcodec/bsf/h265_metadata.c` (the CBS-BSF model),
  `libavcodec/cbs_sei.h` (`ff_cbs_sei_add_message`, `SEIRawMessage`),
  `libavcodec/cbs_h265.h` (`H265RawFilmGrainCharacteristics`),
  `libavcodec/sei.h` (`SEI_TYPE_FILM_GRAIN_CHARACTERISTICS = 19`),
  `libavcodec/cbs_h265_syntax_template.c` (the FGC write semantics).
- ITU-T H.274 / ISO/IEC 23002-7 (film-grain-characteristics SEI semantics).
- Source: `req` — implement the film-grain bitstream filter that completes the
  FGS round-trip the grain estimator started, inserting the H.274 FGC SEI into
  HEVC so a decoder re-synthesizes grain (the AV1 leg already round-trips via
  native side data).
