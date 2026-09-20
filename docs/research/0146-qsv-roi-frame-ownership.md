<!-- markdownlint-disable MD013 MD060 -->
# QSV ROI frame ownership and MBQP contract

**Date:** 2026-09-20

**Decision:** [ADR-0146](../adr/0146-qsv-roi-frame-ownership.md)

**Scope:** FFmpeg n9.0.1 patch 0005 and oneVPL `mfxExtMBQP`

## Finding

The old QSV dense-ROI patch had the wrong owner for the delta-QP bytes. It kept
one mutable map on `QSVEncContext`, while every submitted frame received a
separate `mfxExtMBQP` header pointing to that same map. FFmpeg's QSV encoder is
asynchronous: `EncodeFrameAsync()` can return with several frames queued, and
n9.0.1 retains each frame's `mfxEncodeCtrl` on its `QSVFrame` until
`clear_unused_frames()` sees the surface unlocked. Repainting the context map
for frame N+1 could therefore change the bytes still referenced by frame N.

This is a lifetime defect even in a single-threaded caller. The conflicting
operations do not need to execute simultaneously; they only need to occur while
the earlier asynchronous submission still retains the pointer.

## Authoritative API facts

The [oneVPL Encode Structures reference](https://intel.github.io/libvpl/latest/API_ref/VPL_structs_encode.html)
defines `mfxEncodeCtrl` as per-frame control and permits extension buffers on its
`ExtParam` chain. It defines `mfxExtMBQP` as runtime control for the current
frame after `mfxExtCodingOption3::EnableMBQP` was requested at initialization.
For the map itself:

- `Pitch` is the byte distance between rows;
- `NumQPAlloc` is the application-allocated element count;
- `DeltaQP` is a raster-ordered signed delta array;
- the map is aligned to 16x16 blocks; and
- `BlockSize` is valid for HEVC only.

The same reference describes an interlaced top-field/bottom-field split for the
absolute `QP` member, but does not define that split for `DeltaQP`. Pelorus does
not infer a delta-field layout from the absolute-QP text; dense maps are limited
to progressive HEVC.

The installed oneVPL 2.17 header and the
[upstream oneVPL structure definition](https://github.com/oneapi-src/oneVPL/blob/674d015bcb294bc39fa276e99a652ea045423e82/api/vpl/mfxstructures.h)
confirm the field widths: `Pitch` and `NumQPAlloc` are 32-bit, `BlockSize` is
16-bit, and each `DeltaQP` entry is one signed byte. The MBQP documentation does
not define a `MFXVideoENCODE_Query` mode that round-trips the per-frame buffer.
Consequently, setting `EnableMBQP=ON` is a request, not evidence that a runtime
implementation accepts or honors the dense map.

The pinned [FFmpeg n9.0.1 QSV encoder](https://github.com/FFmpeg/FFmpeg/blob/n9.0.1/libavcodec/qsvenc.c)
provides the ownership mechanism the patch needs: a `QSVFrame` owns its
`mfxEncodeCtrl`, `free_encoder_ctrl()` frees every attached ext-buffer, and
`clear_unused_frames()` invokes that cleanup only after `surface.Data.Locked`
becomes zero.

## Implemented ownership and selection

For every ROI-bearing dense-path frame, patch 0005 now allocates one zeroed
block containing:

```text
+----------------------+-------------------------------+
| mfxExtMBQP header     | signed DeltaQP raster bytes   |
+----------------------+-------------------------------+
^ ExtParam pointer      ^ header + 1
```

The `mfxEncodeCtrl` ext-buffer pointer owns the start of that allocation.
Existing FFmpeg cleanup therefore frees the header and map together at the
correct surface-lifetime boundary; no new pool or destructor is needed.

Dense selection requires all of these conditions:

1. the build exposes MBQP API 1.13 or newer;
2. the codec is HEVC;
3. rate control is CQP; and
4. the session and current frame are progressive.

H.264, non-CQP HEVC, interlaced input, and MBQP-absent builds preserve standard
ROI steering by selecting FFmpeg's existing `mfxExtEncoderROI` rectangle path.
The dense and rectangle buffers are mutually exclusive on each frame.

## Layout and arithmetic

The allocation grid uses aligned QSV storage dimensions from
`mfxFrameInfo.Width` and `Height`. Visible ROI coordinates are clipped to the
`AVFrame` width and height before conversion to 16x16 cells; aligned right and
bottom padding therefore stays at the zero-delta default.

The rasterizer rejects non-positive or inconsistent visible/storage dimensions.
It uses `size_t` for block counts and allocation bytes, `av_size_mult()` for the
cell-count product, an explicit `SIZE_MAX` guard for header-plus-map addition,
and explicit `UINT32_MAX` guards before assigning `Pitch` or `NumQPAlloc`. ROI
side data must contain at least one complete `AVRegionOfInterest`, the declared
stride must be large enough and divide the payload exactly, every entry must use
the same stride, and every `qoffset` denominator must be nonzero.

## Reproduction and validation

The first direct harness encoded the failing layout without QSV hardware:
visible 33x33 pixels with aligned storage 48x64. The old implementation sized
from visible dimensions and produced nine cells; the required storage raster is
three by four, or twelve cells. That check failed before the implementation was
changed.

The committed regression then passed under Clang ASan+UBSan. It keeps two
frame-control allocations live simultaneously and verifies that writing the
second map does not change the first. It also covers the 3x4 padded raster,
zero padding, first-region overlap precedence, malformed ROI payloads, invalid
dimensions, and interlaced dense-path rejection. A second configuration forces
`QSV_HAVE_MBQP=0` and compiles `qsvenc.c`, `qsvenc_h264.c`, and `qsvenc_hevc.c`;
this checks source-level exclusion with current headers, not binary
compatibility against every historic SDK.

Run the focused gate with:

```bash
FFMPEG_REPO=/path/to/ffmpeg \
BASE_TAG=n9.0.1 \
bash ffmpeg-patches/test/qsv-roi-regression.sh
```

The cumulative patch-stack replay and full FFmpeg link are separate gates from
this focused harness. No Intel hardware is required for the direct test, so it
proves ownership, layout, validation, and compilation but does not prove driver
acceptance, asynchronous device execution, bitrate, or quality. Those claims
require a new on-hardware run of the corrected patch.
