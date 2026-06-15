<!-- markdownlint-disable MD013 MD060 -->
# QP feedback — closing the encoder-steering loop

The `PEL_SEC_QPREPORT` interop section (bit `1u << 5`, ABI 1.1) carries the
encoder's **actual** post-encode per-block QP and bit decisions back into the
Pelorus side-data blob, so a later pass — or vmafx — can verify the requested
ROI / delta-QP map (ADR-0114 Tier 0–2) was honored and refine it. Decision:
[ADR-0119](../adr/0119-qp-feedback.md); ABI: [interop-abi.md](../api/interop-abi.md).

This is the **read-back** half of encoder steering. Sections (a)–(e) carry
*pre-encode* GPU measurements that Pelorus pushes *to* the encoder;
`PEL_SEC_QPREPORT` carries what the encoder *did* — the only honest way to
attribute a BD-rate delta, because ROI steering merely redistributes bits and
vendor AQ can silently override the requested map (an ADR-0114 caveat).

## What the section carries

| Field | From (QSV) | Meaning |
|---|---|---|
| `avg_qp` | `mfxEncodeFrameStats.Qp` | frame mean QP (fractional under MBQP) |
| `psnr_y/u/v` | `mfxEncodeFrameStats.PSNR*` | encoder-reported PSNR (dB), 0 if absent |
| `total_bits` | sum of `mfxEncodedUnitInfo.Size` | encoded frame size in bits |
| `num_intra/inter/skipped_blocks` | `mfxEncodeFrameStats.Num*Block` | per-frame block-mode tally |
| `qp_cell_offset` / `qp_cell_size` | folded `mfxMBInfo.Qp` / `mfxCTUInfo.QP` | int8 actual QP per cell map |
| `bits_cell_offset` / `bits_cell_size` | (follow-up) | uint32 encoded bits per cell map |
| `honored_fraction` | computed | 0..1 cells whose QP moved as the ROI map requested |
| `report_source` | — | `enum pel_qp_report_source` (QSV today) |
| `block_size_log2` | encoder | log2 block edge (4 ⇒ 16×16) |
| `qp_valid` | — | 1 ⇒ `qp_cell` map populated; 0 ⇒ frame stats only |

The per-cell QP map uses the encoder's raw QP scale (0..51 for AVC/HEVC), folded
from the encoder's block grid onto the shared `(grid_cols × grid_rows)` cell grid
the other map sections use. A producer that does not populate a given per-cell
map leaves both its `*_offset` and `*_size` zero; the `bits_cell` map is a
follow-up, so v1 producers leave `bits_cell_offset` / `bits_cell_size` at 0.

## The vendor-neutral reader stub

libpelorus must stay free of any encoder SDK (vmafx vendors `interop.c`
verbatim and never links oneVPL/NVENC). So the section is fed through an
abstract input, `PelorusQpReportInput`, and a fold helper:

```c
/* The encoder-side reader (e.g. a libavcodec QSV consumer) extracts the raw
 * per-block QP into a row-major int8 grid + the frame summary, then folds: */
PelorusQpReportInput in = {
    .block_qp = blk_qp,            /* blk_cols*blk_rows, from mfxEncodeBlkStats   */
    .blk_cols = w / 16, .blk_rows = h / 16,
    .block_size_log2 = 4,          /* 16x16                                       */
    .report_source = PEL_QPSRC_QSV,
    .avg_qp = frame_stats.Qp,      /* mfxEncodeFrameStats                         */
    .total_bits = bits,
    .num_inter_blocks = frame_stats.NumInterBlock,
};
PelorusQpReportSection qp;
int8_t cellmap[GRID_COLS * GRID_ROWS];
pel_qp_report_from_blocks(&in, GRID_COLS, GRID_ROWS, &qp, cellmap, sizeof cellmap);
/* then pel_blob_pack(&meta, {{PEL_SEC_QPREPORT, &qp, sizeof qp}}, ...) and
 * append cellmap after the blob, setting qp.qp_cell_offset to its offset. */
```

The fold averages the encoder blocks that fall in each cell (proportional index
mapping; guarantees ≥1 sampled block per cell). With no block grid it preserves
the frame scalars and sets `qp_valid = 0`.

## The runnable reader — x265 `--csv` (ADR-0122)

The QSV per-block path above is the eventual hardware source, but the dev box's
Arc-A QSV is low-power-bugged, so it cannot be validated on HW here. To get one
surface that runs **end-to-end** and populates `PEL_SEC_QPREPORT` with real,
non-synthetic numbers, libpelorus also reads the per-frame statistics the HEVC
software reference encoder x265 emits with `--csv --csv-log-level 2`. This stays
SDK-free (pure stdio CSV parsing — no libx265 link), so vmafx still vendors
`interop.c` verbatim.

```c
PelorusX265Frame frames[4096];
size_t n = 0;
pel_x265_csv_parse("x265.csv", frames, 4096, &n);   /* locates cols by header name */

float requested_qp[4096];                            /* the QP a pass requested per frame */
/* ... fill requested_qp[0..n) ... */

PelorusQpReportSection qp;
pel_qp_report_from_x265_frames(frames, n, requested_qp, &qp);
/* qp.avg_qp = bit-weighted honored QP; qp.total_bits = sum; qp.psnr_* bit-weighted;
   qp.honored_fraction = sign-agreement of requested vs honored per-frame delta-QP;
   qp.qp_valid = 0 (x265 CSV is frame-granular — no per-CTU QP, so no per-cell map). */
```

`honored_fraction` is the frame-granular analogue of the per-cell ROI
honored-fraction: the fraction of frames whose honored QP moved in the same
direction (relative to the GOP mean) as the requested QP did. Under fixed-QP
(`--qp N --aq-mode 0`) x265 still spreads the one requested QP across slice types
(I lower, B higher), which is a genuine requested-vs-honored signal. The reader
admits only coded-frame rows by a positive `Type` test, dropping x265's trailing
blank + `Summary` block.

The `tools/pelorus_qp_report` demonstrator runs the full loop — parse → fold →
`pel_blob_pack` → `pel_blob_find_section` → print:

```sh
x265 --input clip.y4m --y4m --qp 32 --aq-mode 0 --psnr \
     --csv x265.csv --csv-log-level 2 --output /dev/null
pelorus_qp_report x265.csv --requested-qp 32
```

A 17-frame `testsrc2` run produced `avg_qp ≈ 32.21`, `total_bits = 104744`,
`psnr_y/u/v ≈ 38.6/37.6/37.0 dB`, `honored_fraction = 0.1765` (measured — a flat
request scores low because x265 spread the QP by slice type).

## Status — working ABI + runnable reader vs deferred HW path

- **Working now**: the `PEL_SEC_QPREPORT` ABI section (pack/parse, byte-frozen,
  `_Static_assert(sizeof == 64)`), the conformance fixture round-trip + fold +
  forward-compat + **CSV-reader** tests, the `pel_qp_report_from_blocks` fold
  stub, and the **runnable x265 CSV reader** (`pel_x265_csv_parse` +
  `pel_qp_report_from_x265_frames`, ADR-0122) — proven end-to-end above with
  measured values, `honored_fraction` included.
- **Deferred (HW-blocked, code follow-up)**: the libavcodec QSV consumer that
  probes `mfxExtCodingOption3::EncodedUnitsInfo` / the `mfxEncodeStats`
  container, extracts `mfxEncodeBlkStats` / `mfxEncodeFrameStats` /
  `mfxExtEncodedUnitsInfo`, fills `PelorusQpReportInput`, and packs the section
  per frame; the per-cell `qp_cell` / `bits_cell` maps (x265 CSV is
  frame-granular). **No BD-rate number is claimed** — that needs a full RD-ladder
  run (ADR-0111 methodology); the QSV per-block fidelity needs working Intel HW.
