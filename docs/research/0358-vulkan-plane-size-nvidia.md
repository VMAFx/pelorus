<!-- markdownlint-disable MD013 MD060 -->
# NVIDIA plane-view size and the right edge of nv12/nv24 frames

**Date:** 2026-10-17

**Decision:** [ADR-0202](../adr/0202-vulkan-plane-size-spec-constants.md), issue [#358](https://github.com/VMAFx/pelorus/issues/358).

**Scope:** FFmpeg `n9.0.2` plus shared series `v0.2.0` plus the Pelorus stack after #188 (ADR-0201). NVIDIA GeForce RTX 4090, driver 615.78.08. lavapipe (Mesa, LLVM 21.1.8) for the fixed build. Input: `testsrc2=size=Wx64:rate=25,noise=alls=12:allf=t+u:all_seed=123`, one frame, `format=<fmt>,hwupload,<filter>,hwdownload,format=<fmt>`. Luma is compared byte for byte with the yuv420p run of the same filter.

Raw logs (local, not committed): `.workingdir/evidence/358-edge/` (`probe-old.txt`, `probe-fixed.txt`, `matrix-nvidia-old.log`, `matrix-nvidia-fixed.log`, `matrix-lavapipe-fixed.log`, `probe.py`).

## Verdict

On NVIDIA, `imageSize()` of a plane view of a semi-planar image reports a width larger than the frame when the frame width is not a multiple of 64. Every Pelorus shader that clamped neighbour reads to `imageSize() - 1` read past the frame there. With the frame size passed as specialization constants, all seven affected filters give the yuv420p luma byte for byte at width 1918.

## Method

1. A temporary probe line in the aa shader changed its output unless `imageSize()` of plane 0 equalled a given width. On NVIDIA it showed 1920 for a 1918-wide nv12 or nv24 frame, and the frame width for yuv420p and nv16. FFmpeg creates the image with the frame's extent (`libavutil/hwcontext_vulkan.c`, no width alignment).
2. `probe.py` ran each Vulkan filter on yuv420p, nv12, nv16 and nv24 at widths 1918 and 1920 on NVIDIA and listed the luma columns that differ from yuv420p. analyze metadata was compared the same way.
3. The same probe and `ffmpeg-patches/test/vulkan-format-matrix.sh` (with Vulkan validation) ran on the old stack and on the fixed build.

## Results

Old stack, width 1918, nv12 and nv24 (identical rows):

| Filter | Maximum difference (codes) | Columns |
| --- | --- | --- |
| aa | 7 | 1914-1917 |
| dehalo | 6 | 1915-1917 |
| deband | 2 | 1904-1917 (11 columns) |
| denoise | 1 | 1916-1917 |
| borderfix `right=3:left=2` | 11 | 1915-1917 |
| deblock, mc | 0 | |

- analyze at 1918: nv12 and nv24 complexity 0.086281, variance 0.010074; yuv420p 0.083958, 0.009756. That is about 3 %, inside the 5 % tolerance of the ADR-0201 metadata rows.
- nv16 differs nowhere, and width 1920 differs nowhere.

Fixed build: every filter, layout and width above gives 0 differing columns, and analyze metadata is identical across layouts (`probe-fixed.txt`). The format matrix with the new exact rows fails on the old stack at `edge 1918 deband nv12` (`matrix-nvidia-old.log`). It passes on the fixed build on NVIDIA and lavapipe, with validation and no new VUID (`matrix-nvidia-fixed.log`, `matrix-lavapipe-fixed.log`).

## Limits

- Why NVIDIA reports the padded width for nv12/nv24 plane views but not for nv16 or the three-plane layouts was not measured. The stock FFmpeg `gblur_vulkan` and `avgblur_vulkan` filters also call `imageSize()`, but showed no difference in the same probe, so no stock reproduction was found.
- Intel ANV and AMD RADV were measured on the old stack only (the #358 width sweep, no difference). The fixed build was measured on NVIDIA and lavapipe.
- Reproduce with `ffmpeg-patches/test/vulkan-format-matrix.sh` (`FFMPEG_BIN`, `VK_DRIVER_FILES`, `OUTPUT_ROOT`).
