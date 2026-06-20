<!-- markdownlint-disable MD013 -->
# ADR-0136: analyze emits per-frame stats as FFmpeg frame metadata

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

`vf_pelorus_analyze_vulkan` measures per-frame complexity / banding / variance /
edge / motion and packs them into the `PelorusSideData` interop blob
(`AV_FRAME_DATA_SEI_UNREGISTERED`) for a downstream vmafx consumer. But that blob
is **not muxed into the output file** and has no host-readable surface — there
was no way to inspect analyze's signals per frame from the shell, nor to drive a
host-side orchestration (per-shot CRF, the autotune loop) off them.

This blocked the next BD-rate lever (per-shot CRF steering, ADR-0132): allocating
bits across shots needs the per-frame complexity + scene-cut signal *extracted to
the host*, and the only producer (analyze) kept them in an opaque blob.

## Decision

Emit the per-frame scalars **also** as FFmpeg frame metadata via
`av_dict_set(&frame->metadata, ...)` — the established `vf_scdet` / `signalstats`
idiom — under the `lavfi.pelorus.*` namespace:

| key | meaning |
|---|---|
| `lavfi.pelorus.complexity` | EMA-smoothed per-frame complexity [0,1] (ADR-0132) |
| `lavfi.pelorus.texture` | normalized texture/edge energy [0,1] |
| `lavfi.pelorus.motion` | motion component [0,1] (0 with no upstream `pelorus_mc`) |
| `lavfi.pelorus.variance` | mean per-tile luma variance |
| `lavfi.pelorus.edge` | mean per-tile edge density [0,1] |
| `lavfi.pelorus.banding` | flat-area fraction / banding risk [0,1] |
| `lavfi.pelorus.scene_cut` | `1` on a Pelorus scene cut, else `0` |

Readable with `ffprobe -show_frames -show_entries frame_tags` or the
`metadata=mode=print` filter. The interop blob is unchanged (this is additive,
host-facing). **No interop ABI change, no shader change** — pure host-side, the
values mirror what the blob already carries.

## Alternatives considered

- **A custom `dump=<file>` CSV option.** Rejected — re-implements what FFmpeg's
  metadata + `ffprobe`/`metadata=print` already do; non-idiomatic; another file
  handle to own.
- **A standalone tool reading the blob from the file.** Impossible — the
  `SEI_UNREGISTERED` side data is not written to the container; it lives only on
  in-graph `AVFrame`s.
- **Leave it blob-only.** Rejected — it strands the analyze signals from every
  host-side consumer (orchestration, debugging, validation).

## Consequences

- Unblocks host-side per-shot CRF / autotune orchestration (read complexity +
  `scene_cut` per frame, aggregate per shot) and makes the whole analyze path
  debuggable from the shell.
- Metadata is emitted unconditionally (like `vf_scdet`); it is near-free and
  ignored unless a downstream filter/`ffprobe` reads it.
- New user-discoverable surface → documented in `docs/metrics/analyze.md`.

## References

- ADR-0109 (the analyze filter), ADR-0132 (per-shot CRF — the consumer this
  unblocks), ADR-0114 (the ROI steering channel).
- `vf_scdet` (`lavfi.scd.*`) / `signalstats` — the frame-metadata idiom mirrored.
