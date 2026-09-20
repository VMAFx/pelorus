<!-- markdownlint-disable MD013 MD060 -->
# QSV ROI steering

Pelorus patch 0005 adds `-pelorus_roi` to FFmpeg's `h264_qsv` and `hevc_qsv`
encoders. It consumes standard `AV_FRAME_DATA_REGIONS_OF_INTEREST` side data.
The option defaults to `0`; enabling it prefers a dense QSV delta-QP map only
for eligible HEVC sessions where the documented oneVPL contract is complete.
It is not registered on `av1_qsv`; H.264 and HEVC are the only QSV encoders in
this patch.

## Selection contract

| Input/build condition | Path used |
|---|---|
| Progressive HEVC, CQP, runtime API 1.28 or newer, MBQP headers available, final CodingOption3 has `EnableMBQP=ON` | Dense per-frame `mfxExtMBQP` with `MFX_MBQP_MODE_QP_DELTA` |
| H.264 | Stock `mfxExtEncoderROI` rectangles |
| HEVC on runtime API 1.27 or older | Stock `mfxExtEncoderROI` rectangles |
| HEVC with non-CQP rate control | Stock `mfxExtEncoderROI` rectangles |
| Interlaced HEVC session or frame | Stock `mfxExtEncoderROI` rectangles |
| Build headers older than API 1.13 | Stock `mfxExtEncoderROI` rectangles |
| Final CodingOption3 missing or `EnableMBQP` is not ON | Stock `mfxExtEncoderROI` rectangles |
| Frame has no ROI side data | No ROI ext-buffer is attached for that frame |

The dense and rectangle ext-buffers are mutually exclusive on a frame.
`mfxExtCodingOption3::EnableMBQP=ON` is a request made during initialization.
FFmpeg n9.0.2 attaches that coding-option buffer to HEVC only on runtime API
1.28 or newer. After successful init or reset, Pelorus caches whether the final
attached CodingOption3 has `EnableMBQP=ON`; an `AVQSVContext` replacement with
the field unset therefore uses stock rectangles. Frame submission consumes the
cached result because FFmpeg's later parameter-retrieval query uses a transient
ext-buffer list. oneVPL does not define a per-frame `mfxExtMBQP` query mode that
would make this a runtime capability probe. An actual hardware encode is still
required to establish that a particular implementation honors the request.

## Lifetime and layout

Each dense-path frame owns one zeroed allocation containing the `mfxExtMBQP`
header followed immediately by its signed-byte `DeltaQP` array. The allocation
is attached to that frame's `mfxEncodeCtrl`. FFmpeg n9.0.2 keeps the control
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

Use runtime API 1.28 or newer with progressive HEVC+CQP when dense steering is
wanted:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
  -i input.mkv \
  -vf "format=p010le,hwupload,pelorus_analyze_vulkan=roi=1,hwdownload,format=p010le" \
  -c:v hevc_qsv -q:v 30 -pelorus_roi 1 output.mkv
```

`-q:v 30` sets FFmpeg's QScale flag as well as the quality value, so
`select_rc_mode()` actually selects QSV CQP. `-global_quality 30` by itself
selects ICQ and therefore takes the stock rectangle fallback, not dense MBQP.

H.264, an older runtime, or another HEVC rate-control mode is valid, but
`-pelorus_roi 1` then selects the stock rectangle path and emits a diagnostic
explaining why. The option does not turn unsupported dense cases into a no-op.

## Deterministic validation

The hardware-independent regression reads the immutable FFmpeg n9.0.2 tag and
commit from root `build-config.env`, requires an explicit local checkout, and
verifies that its namespaced tag peels to the configured commit. It applies
patches 0001-0004 plus the source-of-truth QSV diff in a hook-neutralized,
run-scoped worktree, runs the dense-map test under ASan/UBSan, and compiles the
QSV translation units with MBQP both present and forced absent:

```bash
FFMPEG_REPO=/path/to/ffmpeg bash ffmpeg-patches/test/qsv-roi-regression.sh
```

It verifies two simultaneously live frame maps, runtime 1.27 stock fallback
versus 1.28 dense selection, final CodingOption3 replacement with
`EnableMBQP` unset versus enabled, aligned storage padding, overlap precedence,
malformed side data, invalid dimensions, interlaced selection, and both
compile-time branches. It does not replace an asynchronous on-hardware encode
test. The harness was also exercised with hostile checkout/applypatch hooks;
the hooks did not run and the owned worktree and scratch directory were removed.

See [ADR-0146](../adr/0146-qsv-roi-frame-ownership.md) and the focused
[research digest](../research/0146-qsv-roi-frame-ownership.md).
