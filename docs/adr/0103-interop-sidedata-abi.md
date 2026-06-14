<!-- markdownlint-disable MD013 -->
# ADR-0103: Pelorus⇄vmafx interop via a UUID-keyed AVFrame side-data ABI

- **Status**: Accepted
- **Date**: 2026-06-14
- **Deciders**: Lusoris
- **Tags**: interop, abi, ffmpeg, vmafx

## Context

The bidirectional Pelorus⇄vmafx requirement has a data plane: Pelorus filters
produce per-frame analysis (banding/flatness, variance/edge, denoise residual,
film-grain params, motion hints) that an encoder and a downstream vmafx
`vf_libvmaf*` should consume — ideally zero-copy, in one filtergraph. FFmpeg's
`AVFrameSideDataType` enum is fixed upstream; an out-of-tree project cannot add
a value. `av_frame_copy_props` (which every well-behaved filter calls)
propagates side data through the graph for free.

## Decision

We will carry a single, versioned, self-describing blob as
`AV_FRAME_DATA_SEI_UNREGISTERED`, prefixed by a fixed project UUID
(`e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64`) and an `"PELOR1\0\0"` magic. Its layout
is `PelorusSideData` (header + a section directory + flat POD sections),
defined in `libpelorus/include/pelorus/interop.h` and packed/parsed by
`libpelorus/src/interop.c`, which **both repos link or vendor verbatim**. The
ABI is **append-only** (R1/R2): new fields go at the end of a section, new
sections take a new bit, `PELORUS_ABI_MINOR` bumps; nothing is ever reordered,
resized, or removed, so a breaking major bump should never happen. Pelorus is
the **sole writer**; vmafx is a read-only consumer. Forward/back-compat is
explicit: a consumer reads `min(producer_size, its_known_size)` per section
(R4) and ignores unknown section bits (R3). A shared conformance fixture
(`libpelorus/test/interop_test.c`) gates both repos.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| `SEI_UNREGISTERED` + project UUID | Standard escape hatch; round-trips the graph; coexists with other SEIs; no FFmpeg patch | Consumer must iterate side-data and UUID-match (only first is returned by helper) | **Chosen** |
| New `AVFrameSideDataType` enum value | Cleaner typing | Requires patching libavutil; creates an ABI fork; not portable to a stock FFmpeg | Avoided |
| `AVFrame.metadata` (AVDictionary) | Survives the graph | String-keyed, stringified, not zero-copy for binary per-cell maps | Wrong for binary map data |
| Hijack `REGIONS_OF_INTEREST` / `VIDEO_HINT` | Existing types | Fixed upstream-defined payloads; would break real consumers | Avoided |

## Consequences

- **Positive**: zero-copy data sharing in one filtergraph; the two repos evolve
  independently behind an append-only contract; one shared TU, no drift.
- **Negative**: single-writer discipline must be honored (vmafx never writes our
  UUID); LE-host assumption for v1 (byte-swap is a future additive minor).
- **Neutral / follow-ups**: per-cell map payloads (banding/variance/MV grids)
  are referenced by blob-relative offsets; `vf_pelorus_analyze` will populate
  them. vmafx adds the read-side perceptual-weighting hook.

## References

- `libavutil/frame.h` (`AV_FRAME_DATA_SEI_UNREGISTERED`), `film_grain_params.h`
  (`AVFilmGrainAOMParams`, mirrored by section (d)).
- vmafx `ffmpeg-patches/0002-add-vmaf_pre-filter.patch` (`av_frame_copy_props`)
  — confirms the round-trip.
- [docs/api/interop-abi.md](../api/interop-abi.md).
- Source: `Q1.4` — "Shared ABI/side-data + autotune RPC".
