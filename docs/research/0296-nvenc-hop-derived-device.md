<!-- markdownlint-disable MD013 MD060 -->
# NVDEC → Vulkan → NVENC with a Vulkan device derived from CUDA

**Date:** 2026-10-14

**Decision:** [ADR-0191](../adr/0191-nvenc-hop-derived-vulkan-device.md), issue
[#296](https://github.com/VMAFx/pelorus/issues/296).

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`) with the shared fix series `v0.1.0` (`ea78aa5d`) and the
Pelorus stack of `master` at `a17dfa4` (`ffmpeg -version`: `n9.0.2-26-g21d59ed`). Between `a17dfa4` and
the branch base `baae3a4` the stack changed only comments in `vf_pelorus_denoise_vulkan.c` and
`vf_pelorus_scenecut.c` and the series tag name (same commit). RTX 4090, driver 615.78.08. Input: a
1280x720 `yuv420p` HEVC clip of 120 frames (sha256 `10d6eefb`) whose only source keyframe is frame 0
(`ffprobe` picture types). The host ran other jobs, so no timing is reported.

## Verdict

With the Vulkan device created from the decoder's CUDA device
(`-init_hw_device cuda=cu:0 -init_hw_device vulkan=vk@cu -hwaccel_device cu`), every measured chain
behind NVDEC runs all 120 frames into NVENC without a CUDA error. With a separate Vulkan device
(`-init_hw_device vulkan=vk:0,disable_multiplane=1`) the same chains fail with exit code 187 and
`CUDA_ERROR_INVALID_VALUE` from `cuWaitExternalSemaphoresAsync`. The cause is in stock FFmpeg: the
second hop derives a second CUDA context, and `vulkan_export_to_cuda()` reuses the frame's CUDA import
made for the first one. With the derived device, `hwupload=derive_device=cuda` returns the decoder's
own device, so only one context exists.

## Method

Each run is one `ffmpeg -v verbose` process with `timeout 120`, `-f null -` output unless noted,
NVENC `-cq 28`. A run passes when the exit code is 0, the last progress line reports 120 frames and
the log holds no `CUDA_ERROR` line. No recipe contains `hwdownload`.

## Chains that failed on the separate device

| Chain between `hwupload` and `hwupload=derive_device=cuda` | Separate device | Derived device |
| --- | --- | --- |
| `null` | exit 187 at frame 0 | 120 frames |
| `pelorus_analyze_vulkan` | exit 187 at frame 0 | 120 frames |
| `pelorus_mc_vulkan=meta=1` | exit 187 at frame 0 | 120 frames |
| `pelorus_scenecut` | exit 187 at frame 0 | 120 frames |
| `pelorus_denoise_vulkan=prev=3` | exit 187 at frame 33 | 120 frames |
| `pelorus_mc_vulkan=meta=1,pelorus_denoise_vulkan=prev=3:mc=1` | exit 187 at frame 33 | 120 frames |
| `pelorus_borderfix_vulkan` twice (luma 4, chroma 2) | exit 187 at frame 32 | 120 frames |
| `pelorus_deband_vulkan` twice | exit 187 at frame 32 | 120 frames |
| `pelorus_grain_estimate_vulkan=strength=2.0,pelorus_denoise_vulkan=strength=0.4` | exit 187 at frame 33 | 120 frames |

Single writing filters, measured both ways:

| Filter | Separate device | Derived device |
| --- | --- | --- |
| `pelorus_aa_vulkan=blur=2:depth=8:darkstr=0.3:edge=0.08` | 120 frames | 120 frames |
| `pelorus_deblock_vulkan=bsize=8:thr=0.06:str=0.6` | 120 frames | 120 frames |
| `pelorus_dehalo_vulkan=blur=2:darkstr=1.0:brightstr=1.0:edge=0.08:ring=2` | 120 frames | 120 frames |
| `pelorus_denoise_vulkan` (defaults, `prev=3`) | exit 187 at frame 33 | 120 frames |

## Documented recipes on the derived device

Run exactly as `docs/` prints them, with `-f null -` in place of the output file:

| Recipe | Encoder options | Result |
| --- | --- | --- |
| `docs/usage/anime.md` chain: `analyze roi=1`, dehalo, aa, deband | `hevc_nvenc -pelorus_roi 1 -preset p5 -rc constqp -qp 22` | 120 frames |
| `docs/usage/ffmpeg.md` deband example | `hevc_nvenc -cq 28` | 120 frames |
| `docs/usage/ffmpeg.md` QP map, `analyze roi=1` | `hevc_nvenc -rc constqp -qp 30 -pelorus_roi 1` | 120 frames |
| `docs/usage/ffmpeg.md` film grain, `grain_estimate` | `av1_nvenc -cq 32 -pelorus_film_grain 1` | 120 frames |
| `docs/usage/ffmpeg.md` motion hints, `mc bsize=16:search=24`, `-extra_hw_frames 1` | `hevc_nvenc -preset p5 -cq 28 -pelorus_me_hints 1` | 120 frames |
| `docs/metrics/denoise.md`, defaults, `-extra_hw_frames 3` | `hevc_nvenc -cq 28` | 120 frames |
| `docs/metrics/mc.md`, `mc` + denoise `prev=3:mc=1`, `-extra_hw_frames 4` | `hevc_nvenc -cq 28` | 120 frames |
| `docs/metrics/scenecut.md`, `mc meta=1`, deband, `scenecut`, `-extra_hw_frames 1` | `hevc_nvenc -cq 28 -force_key_frames source`, written to a file | 120 frames; `I` pictures 0 and 60 (source: 0 only) |

## Smoke gate

`ffmpeg-patches/test/nvenc-cuda-hop-smoke.sh` on the same binary: the nine chains at 100 frames and
deband twice at 31, 32 and 33 frames all pass (exit 0). `--self-test` runs `null` and deband twice on
the separate device; both fail with `CUDA_ERROR_INVALID_VALUE` as required, exit 0. With `libcuda.so.1`
hidden (an empty stub on `LD_LIBRARY_PATH`) the smoke prints
`SKIP: hevc_nvenc cannot encode on this host: [hevc_nvenc @ ...] Cannot load libcuda.so.1` and exits
77, the reason the `ffmpeg-stack` CI lane names in `NVENC_HOP_SMOKE_SKIP`. With a stub binary whose
encoder list has no `hevc_nvenc` it prints `SKIP: hevc_nvenc is not built into ...`, the reason of the
`lavapipe` lane. `named-skip.sh` accepts each log with its own lane's reason and refuses it with the
other reason or with none.

## Upstream

The stock-FFmpeg defect reproduces without Pelorus filters on `n9.0.2` and on FFmpeg master
`ebfc9497f7`. It is filed on code.ffmpeg.org as FFmpeg issue 25052, with a fix proposed as FFmpeg
pull request 25053: the frame is imported again when the CUDA context changes.

## Limits of this evidence

- One GPU and driver; one 8-bit 4:2:0 clip. 10-bit input and software decode on the derived device
  were not run.
- `-extra_hw_frames` is set where `scripts/check-doc-recipes.py` requires it. Decoder-pool starvation
  without it was not reproduced (see `docs/usage/ffmpeg.md`, "Frames the filters keep").
- The CUDA-context defect itself stays in FFmpeg until the upstream fix lands; recipes that build a
  separate Vulkan device behind NVDEC still fail.
