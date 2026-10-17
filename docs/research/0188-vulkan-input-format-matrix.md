<!-- markdownlint-disable MD013 MD060 -->
# Vulkan input format and interlace matrix of the Pelorus filters

**Date:** 2026-10-17

**Decision:** [ADR-0201](../adr/0201-vulkan-input-format-allow-list.md), issue
[#188](https://github.com/VMAFx/pelorus/issues/188); follow-up defect
[#358](https://github.com/VMAFx/pelorus/issues/358).

**Scope:** the v0.4.0 stack (FFmpeg `n9.0.2` plus shared series `v0.2.0` plus the 22 Pelorus patches), NVIDIA GeForce RTX 4090 with driver 615.78.08. The nv12/nv24 right-edge rows were also run on the Intel Arc and RADV devices of the same host. Input: `testsrc2` through `format=<layout>,hwupload`, then `hwdownload` and conversion to `gray` (luma) for comparison.

Raw logs (local, not committed): `.workingdir/evidence/188-format-matrix/` (`formats-baseline.txt`, `interlace-baseline.txt`, `nv12-edge.txt`, `failing-first-base*.log`, `new-replay.log`, `matrix-new-nvidia.log`).

## Verdict

Every filter gives its yuv420p result on all 24 planar and semi-planar YUV layouts at 4:2:0, 4:2:2 and 4:4:4 with 8, 10, 12 and 16 bits, and on `yuva420p`. `yuyv422` hangs, `gbrp` and `gray` run in a wrong sample domain without an error, and interlaced frames pass every filter. Before this change no filter refused any of them.

## Method

1. For each of the nine Vulkan filters and each of 28 layouts, run the filter with default options and compare normalized luma against the same filter's `yuv420p` output. Pass: MAE at most 0.010 and maximum difference at most 0.055. analyze and grain_estimate compare their frame metadata within 5 %.
2. Set `AV_FRAME_FLAG_INTERLACED` with `setfield=tff` and record exit status and the output flag (`showinfo`, `i:T`).
3. For the nv12/nv24 outliers, sweep widths 32 to 160 per device and record the columns whose luma differs from nv16.

## Results

Base stack, 28 layouts per filter (`formats-baseline.txt`, 252 rows):

| Outcome | Layouts | Filters |
| --- | --- | --- |
| match | 24 YUV layouts, `yuva420p` | all nine (aa: 23, see below) |
| timeout after 30 s | `yuyv422` | all nine |
| mismatch | `gbrp` (deband MAE 0.148, max 0.451), `gray` (deband MAE 0.035, max 0.071) | all nine |
| mismatch | `nv12`, `nv24` (MAE 0.0018, max 0.337) | aa only |

- The worst passing pixel row (10-16 bit) has MAE 0.0015 and maximum 0.0052 (nv12 and nv24 reach maximum 0.0078 at MAE 0.00001), which is quantization of the 8-bit source, not a domain error. analyze and grain_estimate metadata stay within 5 % on every passing layout.
- Interlace (`interlace-baseline.txt`): all ten filters exit 0 and keep `i:T`. The six spatial filters therefore mix rows of two fields.
- nv12/nv24 right edge (`nv12-edge.txt`): on NVIDIA, aa differs in the last 3-4 columns at widths 32, 96, 98, 100 and 160, and dehalo at width 96; widths 64 and 128 are clean, nv16 is clean at every width. Intel and RADV show no difference at any width. Tracked as #358; the matrix fixture is 128 pixels wide.

With the change (`matrix-new-nvidia.log`): every accepted layout matches in every filter, the alpha plane of `yuva420p` passes through bit-exact, `yuyv422`, `gbrp`, `yuva444p`, `gray` (and `rgba` in deband, mc, analyze, grain_estimate, borderfix) are refused at configuration with a message naming the layout, and interlaced frames are refused by the six spatial filters and kept by the four measuring filters. The same script on the base stack fails at the first interlace row (`failing-first-base.log`) and accepts `gbrp` (`failing-first-base-gbrp.log`).

## Limits

- The Vulkan hwcontext of FFmpeg `n9.0.2` offers no 9-bit or 14-bit YUV layout, so 8 and 16 bit are the measured depth boundaries.
- The format matrix ran on NVIDIA only; Intel and RADV were measured for the edge rows only.
- Reproduce with `ffmpeg-patches/test/vulkan-format-matrix.sh` (`FFMPEG_BIN`, `VK_DRIVER_FILES`, `OUTPUT_ROOT`).
