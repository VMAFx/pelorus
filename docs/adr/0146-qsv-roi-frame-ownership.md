<!-- markdownlint-disable MD013 MD060 -->
# ADR-0146: QSV dense ROI maps are per-frame and contract-gated

- **Status**: Accepted (2026-09-20)
- **Date**: 2026-09-20
- **Deciders**: Lusoris
- **Tags**: ffmpeg, qsv, roi, lifetime, onevpl, safety

## Context

Patch 0005 translates `AV_FRAME_DATA_REGIONS_OF_INTEREST` into a dense
`mfxExtMBQP` delta-QP map.  The shipped implementation stores that map once in
`QSVEncContext` and points every frame's `mfxExtMBQP` at the same allocation.
This is unsafe with FFmpeg's normal `async_depth > 1`: the defect was first
reproduced on n9.0.1, whose QSV lifecycle keeps each submitted frame's
`mfxEncodeCtrl` alive on its `QSVFrame` until the associated surface unlocks,
while the context-wide map can be cleared and repainted for a later frame
immediately after `MFXVideoENCODE_EncodeFrameAsync()` returns. Frame N can
therefore be encoded using frame N+1's ROI map.

The existing portability contract also overstates what oneVPL exposes.  The
installed oneVPL 2.17 API documents `mfxExtMBQP` as a per-frame
`mfxEncodeCtrl` buffer and `EnableMBQP` as an initialization option restricted
to CQP.  It defines no `MFXVideoENCODE_Query` mode or capability bit that
round-trips a per-frame `mfxExtMBQP`, so setting `EnableMBQP` is a request, not
a runtime capability probe.  The delta-map layout is useful for HEVC, but AVC
must remain on FFmpeg's stock `mfxExtEncoderROI` path.  The API documents a
field-split layout only for the absolute-QP member, not for `DeltaQP`, so using
the progressive raster for interlaced content would be a guess.

The map also crosses several size domains: FFmpeg's signed frame dimensions,
oneVPL's aligned `mfxFrameInfo` dimensions, `size_t` allocation sizes, and
oneVPL's 32-bit `Pitch` and `NumQPAlloc` fields.  Every conversion and product
must be checked before allocation or narrowing.

## Decision

`pelorus_roi=1` will mean "prefer the dense Pelorus QSV map when its documented
contract is satisfied", not "disable all ROI steering otherwise".

The dense path will be used only when all six conditions hold: the build
headers expose the MBQP API, the QSV runtime reports API 1.28 or newer, the
codec is HEVC, rate control is CQP, the session is progressive, and the final
attached CodingOption3 buffer has `EnableMBQP=ON`. FFmpeg n9.0.2 does not attach
`mfxExtCodingOption3` to older HEVC runtimes, while an `AVQSVContext`
CodingOption3 buffer can replace Pelorus's internal buffer during initialization.
AVC, older runtimes, non-CQP HEVC, interlaced HEVC, MBQP-absent builds, and a
final replacement without `EnableMBQP=ON` retain the stock per-region
`mfxExtEncoderROI` path. No documentation or log will call the `EnableMBQP`
request a runtime capability probe.

The final-list result is cached immediately after successful encoder init and
reset. Frame submission must not rescan `q->param.ExtParam` because FFmpeg's
parameter-retrieval helper later uses a transient stack-local query list.

Each ROI-bearing dense-path frame will own one zeroed allocation containing the
`mfxExtMBQP` header followed immediately by that frame's signed-byte delta map.
The header is attached to the frame's existing `mfxEncodeCtrl`; FFmpeg's existing
`free_encoder_ctrl()` then releases the complete allocation only after the QSV
surface unlocks.  Frames never share mutable map storage.

The raster dimensions will come from the aligned storage dimensions in
`q->param.mfx.FrameInfo.Width` and `Height`.  ROI coordinates remain relative to
the visible `AVFrame`; they are clipped there, and cells covering right/bottom
storage padding remain zero.  `Pitch` is the checked cell width in bytes and
`NumQPAlloc` is the checked cell count.  Width/height validation, cell-count
multiplication, allocation-byte addition, and all oneVPL field narrowings must
reject overflow before allocating or writing.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Per-frame contiguous header + map allocation | Matches `mfxEncodeCtrl` ownership; existing cleanup frees everything; no shared mutation | One allocation per ROI-bearing frame | **Chosen**: the lifetime follows the exact n9.0.2 surface lifecycle with the smallest change |
| Keep one context-wide scratch map | Lowest allocation churn | Data race in the logical async pipeline; older frames observe later maps | Rejected: this is the defect |
| Pool `async_depth` maps in the encoder context | Amortizes allocations | Must map submissions to surface unlocks and handle dynamic queue depth/reset/error paths | Rejected: duplicates lifecycle state FFmpeg already owns on `QSVFrame` |
| Allocate the MBQP header and map separately | Simple map sizing | `free_encoder_ctrl()` frees only the ext-buffer pointer, so the map needs a new destructor/owner and is easy to leak | Rejected: weaker ownership than one contiguous block |
| Use dense MBQP for AVC and interlaced HEVC too | Broader apparent coverage | AVC does not provide the required HEVC block-size/delta contract; oneVPL does not define interlaced `DeltaQP` layout | Rejected: would rely on behavior the API does not promise |
| Disable dense QSV ROI entirely | Removes the lifetime risk | Loses the per-cell HEVC steering fidelity ADR-0114 selected | Rejected: a bounded, testable safe path exists |

## Consequences

- **Positive**: async depth no longer changes a submitted frame's ROI map;
  unsupported dense cases keep stock ROI steering instead of silently losing
  it; allocation/layout arithmetic is explicit and checked.
- **Negative**: dense QSV ROI requires runtime API 1.28 or newer plus progressive
  HEVC+CQP, and allocates one map per ROI-bearing in-flight frame.
- **Neutral / follow-ups**: deterministic coverage will exercise two live maps,
  padded raster layout, ROI precedence, invalid dimensions/sizes, and both
  compile-time MBQP branches against the configured FFmpeg n9.0.2 commit and
  current oneVPL headers.
  Hardware async execution remains a separately reported validation step; no
  hardware result will be inferred from compile or harness evidence.

## References

- [ADR-0104](0104-ffmpeg-patch-stack.md) — cumulative FFmpeg patch-stack delivery model.
- [ADR-0114](0114-encoder-steering.md) — QSV dense-map steering decision this ADR narrows and repairs.
- FFmpeg n9.0.2 (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`), `libavcodec/qsvenc.c`: current configured pin used for the focused and cumulative gates.
- FFmpeg n9.0.1 (`bf1b838f2ab88b4f8fd83443325c782ea0e0f7fa`): original defect-reproduction baseline with the same `QSVFrame::enc_ctrl`, `clear_unused_frames()`, `free_encoder_ctrl()`, and `MFXVideoENCODE_EncodeFrameAsync()` lifetime.
- Intel oneVPL 2.17, `mfxstructures.h`: `mfxExtCodingOption3::EnableMBQP`, `mfxExtMBQP`, `Pitch`, `BlockSize`, `NumQPAlloc`, and `DeltaQP`.
- Intel oneVPL Encode Structures documentation: `mfxEncodeCtrl` and `mfxExtMBQP`.
- Source: `req` — "per-frame mfxExtMBQP/DeltaQP ownership safe for async depth >1; HEVC+CQP uses dense delta map; AVC and non-CQP HEVC correctly fall back to stock mfxExtEncoderROI ... checked width/height/cell-count/byte-size arithmetic ... define and validate interlaced/layout behavior".
