<!-- markdownlint-disable MD013 MD060 -->
# QSV ROI steering

Pelorus patch 0005 adds `-pelorus_roi` to FFmpeg's `h264_qsv` and `hevc_qsv`
encoders. It consumes standard `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data.
The option defaults to `0`; enabling it prefers a dense QSV delta-QP map only
where the documented oneVPL contract is complete.

## Selection contract

| Input/build condition | Path used |
|---|---|
| Progressive HEVC, CQP, MBQP headers available | Dense per-frame `mfxExtMBQP` with `MFX_MBQP_MODE_QP_DELTA` |
| H.264 | Stock `mfxExtEncoderROI` rectangles |
| HEVC with non-CQP rate control | Stock `mfxExtEncoderROI` rectangles |
| Interlaced HEVC session or frame | Stock `mfxExtEncoderROI` rectangles |
| Build headers older than API 1.13 | Stock `mfxExtEncoderROI` rectangles |
| Frame has no ROI side data | No ROI ext-buffer is attached for that frame |

The dense and rectangle ext-buffers are mutually exclusive on a frame.
`mfxExtCodingOption3::EnableMBQP=ON` is a request made during initialization;
oneVPL does not define a per-frame `mfxExtMBQP` query mode that would make this a
runtime capability probe. An actual hardware encode is therefore required to
establish that a particular implementation honors the request.

## Lifetime and layout

Each dense-path frame owns one zeroed allocation containing the `mfxExtMBQP`
header followed immediately by its signed-byte `DeltaQP` array. The allocation
is attached to that frame's `mfxEncodeCtrl`. FFmpeg n9.0.1 keeps the control
object on `QSVFrame` and frees its ext-buffers only after the corresponding QSV
surface unlocks, so asynchronously queued frames never share mutable map data.

The raster uses 16x16 cells. Its width and height come from the aligned storage
domain in `mfxFrameInfo.Width` and `Height`, not only the visible dimensions.
ROI rectangles are clipped to the visible `AVFrame`; cells that cover aligned
right or bottom padding remain zero. The implementation checks frame/storage
dimensions, cell-count multiplication, allocation-size addition, and narrowing
to oneVPL's 32-bit `Pitch` and `NumQPAlloc` fields before allocating or writing.
The first ROI in the side-data list keeps FFmpeg's established precedence when
regions overlap.

## Usage

Use progressive HEVC with CQP when dense steering is wanted:

```bash
ffmpeg -i input.mkv \
  -vf "...,pelorus_analyze_vulkan=roi=1,..." \
  -c:v hevc_qsv -global_quality 30 -pelorus_roi 1 output.mkv
```

H.264 or another HEVC rate-control mode is valid, but `-pelorus_roi 1` then
selects the stock rectangle path and emits a diagnostic explaining why. The
option does not turn unsupported dense cases into a no-op.

## Deterministic validation

The hardware-independent regression applies patches 0001-0004 plus the
source-of-truth QSV diff to a pristine FFmpeg n9.0.1 tree, runs the dense-map
test under ASan/UBSan, and compiles the QSV translation units with MBQP both
present and forced absent:

```bash
FFMPEG_REPO=/path/to/ffmpeg \
BASE_TAG=n9.0.1 \
bash ffmpeg-patches/test/qsv-roi-regression.sh
```

It verifies two simultaneously live frame maps, aligned storage padding,
overlap precedence, malformed side data, invalid dimensions, interlaced
selection, and both compile-time branches. It does not replace an asynchronous
on-hardware encode test.

See [ADR-0146](../adr/0146-qsv-roi-frame-ownership.md) and the focused
[research digest](../research/0146-qsv-roi-frame-ownership.md).
