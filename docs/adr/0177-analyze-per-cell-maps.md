<!-- markdownlint-disable MD013 MD060 -->
# ADR-0177: vf_pelorus_analyze fills the ABI 1.0 banding, variance and edge map fields on a power-of-two cell grid, packed FFmpeg-side

- **Status**: Proposed
- **Implementation**: pending (#219)
- **Date**: 2026-10-09
- **Deciders**: Lusoris
- **Tags**: analyze, interop, vulkan, ffmpeg, vmafx

## Context

VMAFx pools VMAF with per-frame weights derived from Pelorus side data
(VMAFx ADR-1118, `core/src/feature/perceptual_weight.c`). Its reader takes the
grid from the blob header (`grid_cols * grid_rows`), averages the `uint8`
banding map at `PelorusBandingSection.cell_data_offset` and the `float` variance
map at `PelorusVarianceSection.var_cell_offset`, and falls back to the frame
scalars when an offset or size is 0. Issue #219 found that
`vf_pelorus_analyze_vulkan` filled the header grid (32-pixel tiles) but left
every map offset and size at 0, so the reader always took the fallback.

The map fields exist since ABI 1.0 (`interop.h`): `cell_data_*` ("uint8 risk
per cell"), `var_cell_*` ("float variance per cell") and `edge_cell_*` ("uint8
edge per cell"). [ADR-0109](0109-analyze-filter.md) deferred the maps and
expected a `pel_blob_pack` extension for trailing payloads. Since then two
precedents appeared: `vf_pelorus_mc` appends its grids after `pel_blob_pack`
in the filter, and ABI 1.4's `pel_blob_map()` defines the reader checks for any
map (exact size, 8-aligned offset, past the directory, inside `total_size`).

Constraints:

- The FFmpeg stack builds against libpelorus 0.2.0 or newer (the configure
  probe and `scripts/check-build-config.py` hold that floor), so a filter
  cannot depend on a new libpelorus function.
- The per-frame path must not allocate for the maps (HISS-03) and the grid must
  be bounded (HISS-02).
- The tester's blob check requires `total_size` to equal the blob length.
- The ROI detector and the frame scalars must not change at the default
  settings.

## Decision

1. **No ABI change.** The filter fills the ABI 1.0 fields; `PELORUS_ABI_MINOR`
   stays 4. Each map starts at the next 8-aligned blob-relative offset after
   the packed sections, in the order banding, variance, edge, and `total_size`
   ends at the last map byte, so `pel_blob_map()` accepts every map.
2. **Units.** Banding: `uint8` = round(255 x score), where score in [0, 1] is
   the per-cell banding score the ROI detector already uses (the larger of the
   fine per-cell and the coarse inter-cell score, [ADR-0133](0133-analyze-coarse-banding-cambi.md)).
   Variance: `float` = per-cell luma variance in the [0, 1] sample domain (at
   most 0.25); its mean over the grid is `global_variance`. Edge: `uint8` =
   round(255 x per-cell edge density).
3. **Geometry.** A new `cell` option sets the cell edge, a power of two in
   8..64 (default 32, the previous tile). The grid is ceil(W / cell) x
   ceil(H / cell), row-major, with partial last cells; a frame smaller than one
   cell is a 1x1 grid. The shader runs one workgroup per cell, min(cell, 32)
   wide, each invocation covering a cell / workgroup span (specialization
   constant 1). Because the cell is a power of two, a consumer recovers it from
   the frame size and the grid without a new field.
4. **Bound.** At most 2^20 cells (the ABI 1.4 telemetry map bound); a larger
   grid is refused at link configuration with `ERANGE` and a message naming
   the frame size and the cell.
5. **Writer in the FFmpeg tree.** `ffmpeg-patches/files/pelorus_analyze_maps.h`
   (plain C, tested by the fast suite) holds the grid, the per-cell scores and
   the packer, built on `pel_blob_pack()` and `pel_blob_find_section()`. The
   blob goes into a buffer from a pool sized once per grid. `maps=0` packs the
   scalar-only blob, byte-identical to before.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Public libpelorus writer (`pel_analysis_pack()` in a new `analysis.c`), runner-up | One tested packer in the library; reusable by a future deband map | The analyze configure probe would need a libpelorus newer than 0.3.0, against the 0.2.0 floor the checker enforces; no producer outside FFmpeg needs it | Rejected: floor change without a second user |
| ABI 1.5: a cell-size field in the banding or variance section | Consumers read the pitch instead of inferring it | A minor bump and a VMAFx mirror update for a value a power-of-two cell already implies | Rejected: inferable |
| Maps opt-in (`maps=0` default) | Smaller blobs for SEI carriers (`udu_sei=1`) | VMAFx stays on the fallback unless every operator adds an option | Rejected: carriers can pass `maps=0` |
| Float banding map | No quantisation | The ABI 1.0 field is documented as `uint8` | Rejected: R1 forbids a type change |
| Per-frame `realloc` of the blob (the `vf_pelorus_mc` pattern) | Smallest diff | One allocation per frame for the maps (HISS-03) and unaligned map offsets | Rejected |

## Consequences

- **Positive**: VMAFx's reader gets real maps without a code change: the
  fields, units and reader checks are the ones it already uses. The ROI output
  and the `lavfi.pelorus.*` scalars are byte-identical to the previous filter
  at `cell=32`.
- **Negative**: each blob grows by 6 bytes per cell plus up to 14 bytes of
  alignment: 12 KiB at 1080p and 48 KiB at 2160p with `cell=32`, which an
  `udu_sei=1` encoder writes into the bitstream (`maps=0` avoids it). The
  detector thresholds are per cell and tuned at 32; at `cell=8` a shallow ramp
  scores far lower (0.085 against 0.952 on the matrix ramp).
- **Neutral / follow-ups**: the fine detector scores light grain on a flat
  field as banding-prone (FFmpeg `noise=alls=12` gives a banding-map mean of
  0.88; `alls=40` gives 0); separating grain from ramps is detector work, not
  part of this change. VMAFx reads only the first `SEI_UNREGISTERED` entry
  (research [0172 zero-copy audit](../research/0172-zero-copy-audit.md) G6),
  so `pelorus_analyze_vulkan` must come before other Pelorus producers in a
  graph that VMAFx scores.

## References

- Issue [#219](https://github.com/VMAFx/pelorus/issues/219) and its acceptance list (req).
- VMAFx ADR-1118, `core/src/feature/perceptual_weight.c` and
  `ffmpeg-patches/0017-libvmaf-read-pelorus-sidedata.patch` (`origin/master`, read 2026-10-09).
- [ADR-0103](0103-interop-sidedata-abi.md), [ADR-0109](0109-analyze-filter.md),
  [ADR-0133](0133-analyze-coarse-banding-cambi.md), [ADR-0174](0174-encoder-telemetry-abi-1-4.md).
- Evidence: [research 0177](../research/0177-analyze-per-cell-maps.md).
