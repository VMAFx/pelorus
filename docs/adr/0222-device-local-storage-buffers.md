<!-- markdownlint-disable MD013 MD060 -->
# ADR-0222: Shader-read storage buffers are device-local, held by a source check

- **Status**: Accepted
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: vulkan, zero-copy, performance

## Context

Issue [#109](https://github.com/VMAFx/pelorus/issues/109) asks for GPU-resident stats and motion vectors. The zero-copy audit ([research 0172](../research/0172-zero-copy-audit.md)) found that `vf_pelorus_denoise_vulkan` with `mc=1` uploads the motion grid and its confidence grid into two storage buffers allocated `HOST_VISIBLE | HOST_COHERENT` without `DEVICE_LOCAL`. On a discrete GPU that memory is system RAM, so every shader read of the grid crosses PCIe while the dispatch runs. The Vulkan-Video QP-map patch (`ffmpeg-patches/files/vulkan-pelorus-qpmap.patch`) allocates its ROI list the same way. Every other Pelorus storage buffer already asks for `DEVICE_LOCAL | HOST_VISIBLE | HOST_COHERENT`.

No compiler, test or review step caught the difference: both memory types work, only one is slow.

## Decision

1. The three host-only storage buffers request `DEVICE_LOCAL | HOST_VISIBLE | HOST_COHERENT`, like the other pools. The host still writes them through the mapping before submit, so the data and the shader are unchanged and the output is bit-identical.
2. `scripts/check-vulkan-buffer-domain.py` (fast suite test `vulkan-buffer-domain`) reads every `ff_vk_get_pooled_buffer()` and `ff_vk_create_buf()` call in `ffmpeg-patches/files/` (C files, headers and the added side of hand-maintained patches) and refuses a storage-buffer usage whose memory flags lack `DEVICE_LOCAL`. A transfer-only staging buffer may stay host-only. A local-name flag argument resolves to its nearest assignment before the call plus later `|=`; a name it cannot resolve fails closed. The shared fix series, patch context lines and flag-forwarding wrappers are out of scope. `--self-test` plants a host-only storage buffer in each source shape and requires a refusal.

This is the first unit of #109. Keeping the motion vectors on the GPU between `mc` and `denoise` (no host wait, no CPU repack) is a later unit with its own decision.

## Alternatives considered

| Option | Why not |
| --- | --- |
| Pure `DEVICE_LOCAL` buffers filled by a staging copy | Adds a copy command and a staging pool per frame for a grid of a few kilobytes; the mapped device-local type already exists on every device that runs the other pools |
| Fall back to host-only memory when no mapped device-local type exists | A second path nobody measures; `ff_vk_alloc_mem` already fails on such a device for the other pools (research 0172), so the fallback would not make those devices work |
| Review only | The defect is invisible in output and in a compile; a review missed it twice |

## Consequences

- **Positive**: The denoise motion grid and the QP-map ROI list are read from device memory; a new host-only storage buffer fails the fast suite.
- **Negative**: A device with no `DEVICE_LOCAL | HOST_VISIBLE` memory type now also fails `denoise` with `mc=1` and the QP-map path, as it already failed every other Pelorus filter (research 0172, readback memory note).
- **Neutral / follow-ups**: The GPU-side hand-off from `mc` to `denoise` and removal of the per-frame host waits stay open in #109.

## References

- [Research 0172](../research/0172-zero-copy-audit.md), row "denoise MV input".
- Issue [#109](https://github.com/VMAFx/pelorus/issues/109).
