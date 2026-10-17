<!-- markdownlint-disable MD013 MD060 -->
# ADR-0206: Per-frame film-grain bridge in the pelorus_fgs bitstream filter

- **Status**: Accepted
- **Implementation**: landed (#82)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: grain, fgs, h274, hevc, bsf, ffmpeg, sei, interop
- **Related**: [ADR-0117](0117-grain-fgs-bsf.md) (one clause superseded), [ADR-0155](0155-fgs-bsf-rdd5-profile.md), [ADR-0183](0183-sidedata-zero-free-carrier.md), [ADR-0205](0205-aom-h274-grain-conversion.md)

## Context

`pelorus_fgs` ([ADR-0117](0117-grain-fgs-bsf.md)) inserts one H.274 film grain characteristics (FGC) SEI into every HEVC access unit, built from a static model given as AVOptions. It deferred the per-frame leg: the grain estimator (`pelorus_grain_estimate_vulkan`) writes a new `PEL_SEC_FILMGRAIN` section on every frame, but nothing carried those parameters into an HEVC stream frame by frame.

Two pieces now exist that close that gap:

- Encoders with `-udu_sei 1` already write the Pelorus blob into the stream as a user data unregistered SEI, either as the blob or as the zero-free carrier ([ADR-0183](0183-sidedata-zero-free-carrier.md)). The parameters therefore reach the bitstream filter inside each access unit.
- `pel_grain_aom_to_h274()` ([ADR-0205](0205-aom-h274-grain-conversion.md)) converts the section's AV1 parameters to an H.274 frequency-filtering model and reports the fidelity loss.

## Decision

1. **Mode option.** `pelorus_fgs` gains `source` with two values. `options` (default) keeps the static model unchanged byte for byte. `sidedata` reads the model from the stream.
2. **Per access unit.** In `sidedata` mode the filter scans the access unit's user data unregistered SEI messages. It accepts a payload whose UUID is the blob UUID or the carrier UUID, unwraps it with `pel_blob_unwrap()`, and takes `PEL_SEC_FILMGRAIN`. When the section has `apply` set and `grain_model` is `aom` or `h274` (the estimator fills the AV1 fields under either tag), the filter converts them and inserts an FGC SEI with three model values per interval (scale and both cutoffs, as the static mode writes them). The Pelorus SEI stays in the stream for VMAFx.
3. **No grain, no SEI.** An access unit without a usable section gets no FGC. If the previous FGC was persistent, the filter writes one FGC with `film_grain_characteristics_cancel_flag` set, so a decoder stops applying stale grain.
4. **Fail closed.** A conversion error, or a stream whose luma or chroma bit depth is not 8, stops the filter with an error. The converter works in the 8-bit domain and FFmpeg's H.274 synthesis supports only 8-bit 4:2:0, so a higher bit depth would need a scaling rule nobody has verified yet.
5. **Link libpelorus.** The filter now requires libpelorus through the same `require_pkg_config` probe as the Pelorus filters, checked against `pelorus/grain_convert.h` and `pel_grain_aom_to_h274`. This supersedes the [ADR-0117](0117-grain-fgs-bsf.md) sentence that the filter does not link libpelorus. Every other filter in the stack already requires it, and copying the conversion (its fit plus a 169-row statistics table) into libavcodec would make two implementations of one behaviour.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Inline blob parser and conversion in the filter (no link) | Duplicates `interop.c` unwrap and the whole ADR-0205 fit plus table in FFmpeg code; two copies drift. |
| Optional link (`_suggest`), per-frame mode only when libpelorus is found | Two build variants of one filter, and a silent loss of the mode when the probe fails; the stack already needs libpelorus for every filter. |
| Encoder-side FGC (patch libx265 or NVENC wrappers) | One patch per encoder; the bitstream filter works for every HEVC encoder that writes the user data SEI. |
| Scale `comp_model_value` by `2^(bitdepth-8)` for 10-bit | Unverified against a reference synthesiser; FFmpeg cannot synthesise H.274 above 8-bit to check it. |

## Consequences

- A per-frame HEVC grain path exists: estimator, encoder with `-udu_sei 1`, then `pelorus_fgs=source=sidedata`.
- Building `pelorus_fgs` now needs libpelorus with the ADR-0205 API.
- 10-bit and 4:2:2 or 4:4:4 streams stay on the static mode until a verified scaling rule exists.
- The H.264 and VVC legs remain on the roadmap (`ffmpeg-patches/series.txt`).
- Every stack replay runs `ffmpeg-patches/test/fgs-sidedata-smoke.sh`: grain estimator, HEVC encoder with `-udu_sei 1` (`hevc_nvenc`, else libx265 when a probe shows it keeps the user data SEI; libx265 4.3 dropped it when measured), then the filter. A host without either encoder skips only when `FGS_SMOKE_SKIP` names the reason ([ADR-0189](0189-named-nvenc-smoke-skip.md)); the LGPL CI replay has no HEVC encoder and names that.

## References

- [#82](https://github.com/VMAFx/pelorus/issues/82), item 3 (inline per-frame bridge).
- FFmpeg `libavcodec/h274.c` `ff_h274_apply_film_grain()`: YUV420P 8-bit only; first matching intensity interval wins.
- Rec. ITU-T H.274, film grain characteristics SEI semantics (cancel and persistence flags).
- Usage: [docs/usage/grain-fgs-bsf.md](../usage/grain-fgs-bsf.md).
