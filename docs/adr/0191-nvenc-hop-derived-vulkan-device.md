<!-- markdownlint-disable MD013 MD060 -->
# ADR-0191: Derive Vulkan device from decoder CUDA device for the NVENC hop

- **Status**: Accepted
- **Date**: 2026-10-13
- **Deciders**: Lusoris
- **Tags**: nvidia, vulkan, cuda, nvenc, ffmpeg

## Context

The NVDEC → Vulkan → NVENC pipeline hops between CUDA and Vulkan via `hwupload`
without copying frames through host memory. In FFmpeg, importing and exporting
Vulkan frames across CUDA boundaries is handled by `libavutil/hwcontext_vulkan.c`.

When configuring separate hardware devices (`-init_hw_device vulkan=vk:0,disable_multiplane=1`),
two independent CUDA contexts exist: the decoder's primary CUDA context and a second
CUDA context created when `hwupload=derive_device=cuda` derives a CUDA device from
the separate Vulkan device.

In upstream FFmpeg (`vulkan_export_to_cuda()`), a Vulkan frame's memory and external
semaphores are imported into CUDA once (`if (!dst_int->cuda_fc_ref)`) and cached on
the frame context. When the frame is later exported to the second CUDA context, the
previously imported foreign semaphores are reused. Calling
`cuWaitExternalSemaphoresAsync` on semaphores imported into a different CUDA context
fails with `CUDA_ERROR_INVALID_VALUE` (exit code 187). This defect reproduces with
stock filters only (`hwupload_cuda` or NVDEC, `hwupload`, `hwupload=derive_device=cuda`)
on unpatched FFmpeg `n9.0.2` and FFmpeg master `ebfc9497f7` (#296).

Until now the docs restricted NVDEC graphs to end in exactly one Pelorus writing
filter (deband, aa, `mc` + deband passed 3000 frames) and sent every other graph to a
software decoder. The rule was measured, not derived: a single stock
`gblur_vulkan` fails at frame 33 on the same setup, and temporal denoise, motion
estimation, pass-through filters (`null`, `analyze`, `scenecut`), borderfix twice
and deband twice all failed on the NVDEC CUDA hop.

## Decision

Derive the Vulkan hardware device from the decoder's CUDA device:

```bash
ffmpeg -init_hw_device cuda=cu:0 -init_hw_device vulkan=vk@cu -filter_hw_device vk \
       -hwaccel cuda -hwaccel_device cu -hwaccel_output_format cuda -i in.mkv \
       -vf "hwupload,<filters>,hwupload=derive_device=cuda" \
       -c:v hevc_nvenc -cq 28 out.mkv
```

1. **Single CUDA Context**: With the Vulkan device derived from `cu`,
   `hwupload=derive_device=cuda` returns `cu` itself, because
   `av_hwdevice_ctx_create_derived_opts()` (`libavutil/hwcontext.c`) walks the
   `source_device` chain before it creates a new device. Both hops use the
   decoder's CUDA context, so the import cached by `vulkan_export_to_cuda()` is
   always valid.
2. **Implicit Multiplane Handling**: In `libavutil/hwcontext_vulkan.c`, the CUDA
   derive path internally passes `disable_multiplane=1`. The explicit
   `disable_multiplane=1` option is therefore unnecessary and omitted.
3. **Verification**: Measured on an RTX 4090 with driver 615.78.08, all 9 previously
   failing or limited filter chains pass 120 frames with exit code 0:
   - `null` (previously failed at frame 0)
   - `pelorus_analyze_vulkan` (previously failed at frame 0)
   - `pelorus_mc_vulkan=meta=1` (previously failed at frame 0)
   - `pelorus_scenecut` (previously failed at frame 0)
   - `pelorus_denoise_vulkan=prev=3` (previously failed at frame 33)
   - `pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=3:mc=1` (previously failed at frame 33)
   - `pelorus_borderfix_vulkan=left=4:right=4:planes=0x1,pelorus_borderfix_vulkan=left=2:right=2:planes=0xE` (previously failed at frame 32)
   - `pelorus_deband_vulkan,pelorus_deband_vulkan` (previously failed at frame 32)
   - `pelorus_grain_estimate_vulkan=strength=2.0,pelorus_denoise_vulkan=strength=0.4` (previously failed at frame 33)
   Deband-twice is additionally verified around the failure boundary at 31, 32, and 33 frames.
   Every documented NVDEC recipe was re-run on the derived device. Runs and
   numbers: [research 0296](../research/0296-nvenc-hop-derived-device.md).
4. **Smoke Gate**: Added `ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh`: the nine
   chains at 100 frames plus deband twice at 31, 32 and 33 frames on the derived
   device. Its `--self-test` runs `null` and deband twice on the old separate
   setup and passes only when both fail with `CUDA_ERROR_INVALID_VALUE`, so the
   gate is seen failing (run on the RTX 4090, recorded in research 0296). `ffmpeg-patches/test/build-and-run.sh` runs it after the
   SEI smoke; a skip (exit 77) passes only when `NVENC_HOP_SMOKE_SKIP` names the
   printed reason (ADR-0189). The CI replay lanes name `Cannot load libcuda.so.1`
   (hosted runner, no driver) and `hevc_nvenc is not built into` (lavapipe lane,
   no ffnvcodec).

## Alternatives considered

- **Keep single-writer rule + software decode.** Rejected: forces users into host
  round trips and CPU decoding for common chains like denoise and motion estimation,
  leaving zero-copy GPU acceleration broken for multi-stage pipelines.
- **Fix `libavutil/hwcontext_vulkan.c`.** Proposed upstream, not a
  replacement: FFmpeg issue 25052 on code.ffmpeg.org reports the defect, and
  FFmpeg pull request 25053 releases the cached import and imports the frame
  again when the CUDA context changes.
  It makes the separate-device setup work, but a frame that alternates between
  two contexts is imported again on every switch, and Pelorus builds on `n9.0.2`
  would carry it in the shared fix series until a release contains it. The derived
  device needs neither.
- **Derived device (`vulkan=vk@cu`).** Chosen: requires no code changes to FFmpeg
  or Pelorus, works on unpatched FFmpeg `n9.0.2` and master `ebfc9497f7`, and
  passes every measured chain behind NVDEC (list above).

## Consequences

- Documentation and user recipes for NVDEC → Vulkan → NVENC are updated to the
  derived form (`cuda=cu:0`, `vulkan=vk@cu`).
- Software decode recipes remain documented on the separate device setup as
  measured; derived device with software decode and 10-bit inputs under derived
  setup were not measured and are not claimed.
- `build-and-run.sh` gains an automated smoke gate running all 9 chains and boundary
  frames on hardware.

## References

- Issue #296: NVENC hop (Vulkan->CUDA) fails CUDA_ERROR_INVALID_VALUE.
- [Research 0296](../research/0296-nvenc-hop-derived-device.md): measured chains and recipes.
- FFmpeg issue 25052 and pull request 25053 (code.ffmpeg.org): the stock defect and its proposed fix.
- Upstream source: `libavutil/hwcontext_vulkan.c` (`vulkan_export_to_cuda`).
- Pinned FFmpeg commit: `n9.0.2` (`946fcce07b6dcd0331c8cc609192aeff5e1924f8`).
