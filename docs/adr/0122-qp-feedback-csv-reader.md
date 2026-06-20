<!-- markdownlint-disable MD013 MD060 -->
# ADR-0122: A runnable QP-feedback reader — fold x265 `--csv` per-frame stats into PEL_SEC_QPREPORT

- **Status**: Accepted
- **Date**: 2026-06-15
- **Deciders**: Lusoris
- **Tags**: interop, qp-map, roi, closed-loop, feedback, x265, encoder, verification

## Context

ADR-0119 landed the `PEL_SEC_QPREPORT` interop section (ABI 1.1) plus a
vendor-neutral fold stub (`pel_qp_report_from_blocks`), but explicitly deferred the
actual encoder-stat *reader*: nothing populates the section from a real encoder.
The named v1 source is Intel oneVPL (`mfxEncodeBlkStats` per-block QP), but the dev
box's Arc-A QSV is low-power-bugged (documented in the bench notes and ADR-0114), so
that path cannot be validated on hardware here — coding it without ever running it
would leave the loop unproven.

ADR-0114 step 6 ("close the loop") needs at least one surface that runs end-to-end so
the readback path is demonstrably wired, not just declared. The constraint is that
libpelorus stays SDK-free: vmafx vendors `interop.c` verbatim and links no encoder
SDK, so the reader cannot pull in oneVPL/NVENC types.

The HEVC software reference encoder x265 exposes a real, parseable post-encode
statistics surface with no SDK: `--csv --csv-log-level 2` writes one row per coded
frame with the encoder's *honored* `Type`, `POC`, `QP`, `Bits`, and (with `--psnr`)
`Y/U/V PSNR`. Under fixed-QP (`--qp N --aq-mode 0`) the encoder still spreads the one
requested QP across slice types (I lower, B higher), so the CSV is a genuine
requested-vs-honored signal — exactly what the loop must verify.

## Decision

Add a runnable, SDK-free CSV reader to libpelorus that parses an x265
`--csv-log-level 2` file into per-frame rows (`pel_x265_csv_parse`) and folds them
into a single `PEL_SEC_QPREPORT` section (`pel_qp_report_from_x265_frames`). The
reader:

- locates the `Type/POC/QP/Bits/PSNR` columns **by header name** (robust to x265's
  column set changing with build flags) and admits only coded-frame rows (positive
  `Type` test), dropping x265's trailing blank + `Summary` block;
- aggregates the GOP into frame-stats-only fields: bit-weighted mean honored QP,
  summed total bits, bit-weighted mean PSNR; leaves `qp_valid = 0` (x265 CSV is
  frame-granular — no per-CTU QP — so no per-cell map);
- computes `honored_fraction` when a requested per-frame QP array is supplied, as the
  sign-agreement between the requested per-frame delta-QP (vs the requested GOP mean)
  and the achieved per-frame delta-QP (vs the achieved GOP mean) — the frame-granular
  analogue of the per-cell ROI honored-fraction the QSV path will compute;
- tags `report_source = PEL_QPSRC_NONE` (x265 is a software reference, not a HW vendor
  enum).

This is **append-only consumer code**: no `PelorusSideData` wire field/section is
added, reordered, or resized, so `PELORUS_ABI_MAJOR/MINOR` stay at 1.1. A standalone
demonstrator (`tools/pelorus_qp_report`) runs the full path — parse CSV → fold →
`pel_blob_pack` → `pel_blob_find_section` → print — proving the section survives the
wire round-trip with non-synthetic values.

The QSV per-block reader (ADR-0119) remains the eventual HW source; it stays
code-deferred until working Intel HW is available. NVENC/Vulkan readers slot behind
the same section bit later.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| x265 `--csv` frame-stats reader (chosen) | Runs end-to-end on this box, SDK-free, real honored-QP/bits/PSNR | Frame-granular only (no per-cell QP map) | — |
| Code the QSV per-block reader, defer all validation | Hits the named v1 source + per-cell map | Unrunnable on the Arc-A here; loop stays unproven; pulls oneVPL into a vmafx-vendored TU unless carefully firewalled | Deferred (kept as the HW follow-up) |
| Parse x265 per-CTU QP for a real per-cell map | Would populate `qp_cell` | x265 CSV does not expose per-CTU QP; would need a libx265 analysis-info hook (SDK link) | Rejected: defeats the SDK-free invariant |
| ffprobe/bitstream QP parse | Decoder-side, vendor-neutral | Re-derives QP from the bitstream (fragile, codec-specific entropy decode) vs reading the encoder's own report | Rejected: more code, less faithful than the encoder's own CSV |

## Consequences

- **Positive**: the encoder-steering loop has one demonstrably-runnable readback
  surface; `PEL_SEC_QPREPORT` is populated with measured x265 numbers (verified
  end-to-end, see below); the reader is SDK-free so vmafx still vendors `interop.c`
  verbatim; the requested-vs-honored `honored_fraction` is computed from a path that
  actually ran.
- **Negative**: x265's surface is frame-granular, so `qp_valid` stays 0 and the
  per-cell `qp_cell` / `bits_cell` maps are not populated by this reader; that
  per-block fidelity waits on the QSV/NVENC HW path.
- **Verified this round (runnable)**: a 17-frame `testsrc2` clip encoded with
  `x265 --qp 32 --aq-mode 0 --psnr --csv … --csv-log-level 2`, fed through
  `tools/pelorus_qp_report --requested-qp 32`, yields `avg_qp ≈ 32.21` (bit-weighted;
  x265 honored the flat request as 29/32/33/34 by slice type), `total_bits = 104744`,
  `psnr_y/u/v ≈ 38.6/37.6/37.0 dB`, `honored_fraction = 0.1765` (a flat request scores
  low because the encoder spread the QP). **These are measured, not fabricated.**
- **Deferred**: the QSV `mfxEncodeBlkStats`/`mfxExtEncodedUnitsInfo` per-block reader
  (Arc-A HW-blocked) and the per-cell `qp_cell`/`bits_cell` maps. No BD-rate claim is
  made (ADR-0111 methodology — that needs a full RD-ladder run).

## References

- ADR-0114 step 6 (the closed-loop line item), [ADR-0119](0119-qp-feedback.md) (the
  `PEL_SEC_QPREPORT` section + the QSV-deferred reader), [ADR-0103](0103-interop-sidedata-abi.md)
  (the append-only ABI), [ADR-0111](0111-benchmark-methodology.md) (how a claim is measured).
- x265: `--csv` / `--csv-log-level 2` per-frame statistics (`Type`, `POC`, `QP`,
  `Bits`, `Y/U/V PSNR` columns); `--qp` + `--aq-mode 0` fixed-QP mode.
- Surface docs: [docs/metrics/qp-feedback.md](../metrics/qp-feedback.md),
  [docs/api/interop-abi.md](../api/interop-abi.md).
- Source: `req` — the closed-loop task: "wire a real encoder-stat reader so a
  downstream pass (or vmafx) can verify the delta-QP map was honored and refine it …
  populate `honored_fraction` from a path you CAN run (e.g. compare requested vs
  achieved frame-average QP via x265 `--csv`)."
