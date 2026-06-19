<!-- markdownlint-disable MD013 MD060 -->
# ADR-0119: Closed-loop QP feedback — read the encoder's honored QP/bits back into the interop ABI

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: interop, abi, qsv, encoder, qp-map, roi, closed-loop, feedback

## Context

ADR-0114 (encoder steering) ships a one-way pipeline: `vf_pelorus_analyze` measures
a banding/delta-QP map on the GPU and the Tier-1/2 patches push it to the encoder
(NVENC `qpDeltaMap`, QSV `mfxExtMBQP`, Vulkan-Video quant-map). Nothing reads the
result back. ADR-0114 step 6 names the missing half: read the encoder's *actual*
per-block QP and bit decisions back so a later pass — or vmafx — can verify the
requested map was honored and refine it. Two ADR-0114 caveats make this necessary,
not optional: vendor AQ can silently override the requested map (NVENC's own AQ does
exactly this), and ROI steering only redistributes bits, so confirming *where* the
bits actually moved is the only honest way to attribute a BD-rate delta.

The readback must reach vmafx, which means it travels through the `PelorusSideData`
interop blob — a frozen, append-only wire ABI vmafx vendors verbatim (ADR-0103,
R1/R2). The concrete v1 surface available without a hardware encode run is Intel
oneVPL: `mfxEncodeBlkStats` carries a signed per-block QP (`mfxMBInfo.Qp` for AVC,
`mfxCTUInfo.QP` for HEVC), `mfxEncodeFrameStats` carries frame-mean QP plus PSNR and
intra/inter/skipped block counts, and `mfxExtEncodedUnitsInfo` carries per-unit bit
sizes.

## Decision

We will add one new append-only interop section, `PEL_SEC_QPREPORT` (bit `1u << 5`),
carrying the encoder's post-encode honored QP/bit readback, and bump
`PELORUS_ABI_MINOR` 1.0 → 1.1. The section holds frame-level scalars (mean QP, PSNR
Y/U/V, total bits, intra/inter/skipped block counts), two per-cell map offsets
(int8 actual-QP-per-cell, uint32 bits-per-cell) following the existing map-offset
convention, a `honored_fraction` verification scalar, and a `report_source` tag
(`enum pel_qp_report_source`: QSV today; NVENC/Vulkan reserved). libpelorus stays
SDK-free: the section is fed by a vendor-neutral reader stub
(`pel_qp_report_from_blocks`) that folds an abstract per-block QP grid onto the
cell grid, so vmafx vendors `interop.c` without ever linking oneVPL. This round
lands the ABI section + pack/parse + conformance test + the documented reader stub;
the libavcodec QSV stat-extraction wiring and the `honored_fraction` comparison
against the requested ROI map are the documented follow-up.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| New `PEL_SEC_QPREPORT` section (chosen) | Append-only, vmafx-readable, vendor-neutral via the reader stub | One more section bit + minor bump | — |
| Append fields to `PEL_SEC_BANDING` | No new bit | Conflates *requested* (pre-encode) with *honored* (post-encode) semantics on one bit; breaks the single-meaning-per-bit rule | Rejected: post-encode readback is a distinct producer/lifecycle |
| Side channel outside the blob | No ABI change | vmafx can only see the blob; a side channel doesn't cross the process boundary the blob is designed for | Rejected: defeats the interop contract |
| Tie the section to `mfx*` types directly | Less mapping code | Forces an oneVPL dependency into a library vmafx vendors verbatim | Rejected: violates the SDK-free invariant |

## Consequences

- **Positive**: the steering loop becomes closeable — a later pass or vmafx can read
  the honored QP/bits, attribute the BD-rate delta to the regions the bits moved to,
  and refine the requested map. The section is vendor-neutral, so NVENC/Vulkan
  readback slot in behind the same bit later.
- **Negative**: one more section to keep in lockstep across the two repos; the
  honored-vs-requested comparison still needs the requested map carried alongside.
- **Honest scope this round**: working ABI + pack/parse + conformance test + a
  documented reader stub (the block→cell QP fold). The QSV `mfxEncodeStats` /
  `mfxExtEncodedUnitsInfo` extraction in libavcodec and the populated
  `honored_fraction` are **not** wired yet — they are the follow-up. **No
  measured honored-fraction or BD-rate number is claimed here** (no HW encode run).

## References

- ADR-0114 step 6 (the closed-loop line item), [ADR-0103](0103-interop-sidedata-abi.md)
  (the append-only ABI), [ADR-0111](0111-benchmark-methodology.md) (how a claim is measured).
- oneVPL: `vpl/mfxencodestats.h` (`mfxEncodeBlkStats`, `mfxMBInfo.Qp`, `mfxCTUInfo.QP`,
  `mfxEncodeHighLevelStats`/`mfxEncodeFrameStats`), `vpl/mfxstructures.h`
  (`mfxExtEncodedUnitsInfo`, `mfxEncodedUnitInfo`, `mfxExtCodingOption3::EncodedUnitsInfo`).
- Surface doc: [docs/api/interop-abi.md](../api/interop-abi.md), [docs/metrics/qp-feedback.md](../metrics/qp-feedback.md).
- Source: `req` — ADR-0114 step 6: "Closed loop: read QSV mfxExtEncodeStats / AMF block-QP feedback back into the interop ABI to verify the map was honored and improve it."
