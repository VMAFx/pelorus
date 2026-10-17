<!-- markdownlint-disable MD013 MD060 -->
# ADR-0201: Pelorus Vulkan filters accept a measured list of input layouts and refuse the rest by name

- **Status**: Accepted
- **Implementation**: landed (#188)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: vulkan, ffmpeg, pixel-formats, interlacing
- **Related**: [ADR-0147](0147-vulkan-sample-domain-and-components.md)

## Context

The nine Pelorus Vulkan filters accepted any software layout behind `AV_PIX_FMT_VULKAN` and any frame flags. A run on the v0.4.0 stack (FFmpeg n9.0.2, RTX 4090) measured each filter on 28 layouts against its own yuv420p result (normalized luma within MAE 0.010 and maximum 0.055; frame metadata within 5 % for analyze and grain_estimate):

- All 24 planar and semi-planar YUV layouts at 4:2:0, 4:2:2 and 4:4:4 with 8, 10, 12 and 16 bits matched, apart from a driver-specific right-edge defect on nv12 and nv24 (issue #358, below).
- `yuyv422` timed out after 30 s in every filter.
- `gbrp` and `gray` gave different output. The sample-domain helper of [ADR-0147](0147-vulkan-sample-domain-and-components.md) maps a layout it does not know to scale 1.0, so such a layout is processed in a wrong domain without an error.
- `yuva420p` luma matched, and the alpha plane passed through bit-exact in every filter whose planes mask leaves it out (BUG-007 and BUG-008 fixed the plane loops for it).
- An interlaced frame (`AV_FRAME_FLAG_INTERLACED`) ran through every filter with exit 0. The six spatial filters (deband, denoise, mc, dehalo, aa, deblock) read neighbouring rows, which in an interlaced frame belong to the other field, so their output mixes two instants.

The Vulkan hwcontext of FFmpeg n9.0.2 has no 9-bit or 14-bit YUV layout (only `gray14` and `gbrp14`), so no 9- or 14-bit YUV frame can reach the filters.

## Decision

1. **Allow list**: `libavfilter/pelorus_vulkan_input.h` (`ffmpeg-patches/files/pelorus_vulkan_input.h`) defines the measured set: the 24 YUV layouts plus `yuva420p`. Every Pelorus Vulkan filter checks the input frames context in its input `config_props` and refuses any other software layout with `AVERROR(EINVAL)` and the message `<filter>: input format <name> is not supported; supported: <list>`.
2. **RGBA**: aa, dehalo, deblock and denoise also accept 8-bit `rgba`, which the existing lane-preservation rows prove (G, B and A unchanged). deband, mc, analyze, grain_estimate and borderfix refuse it.
3. **Interlacing**: the six spatial filters refuse an interlaced frame with `AVERROR(EINVAL)` and the message `<filter>: interlaced input (AV_FRAME_FLAG_INTERLACED) is not supported; deinterlace first, or mark progressive frames with setfield=prog`. analyze, grain_estimate, borderfix and scenecut accept it and keep the flag (maintainer decision on #188, 2026-10-17).
4. **Proof**: `ffmpeg-patches/test/vulkan-format-matrix.sh` runs every filter on every accepted layout against yuv420p, the yuva420p alpha pass-through, the refusal of `yuyv422`, `gbrp`, `yuva444p`, `gray` (and `rgba` where refused) by name, and the interlace rows. Its fixture is 128 pixels wide because of issue #358.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Measured allow list, refusal by name (chosen) | A layout runs only when a test shows it correct; the error names the layout and the supported set | A new layout needs a matrix row before it is accepted | Chosen |
| Deny list of known-bad layouts | Fewer lines | Any layout nobody tested still runs silently into the wrong domain | Fails open |
| Convert unsupported layouts inside the filter | Accepts everything | A hidden copy and conversion per frame on a zero-copy path; the conversion needs its own proof | Cost and scope; FFmpeg's `scale_vulkan` already converts in the graph |
| Process interlaced frames per field | Interlaced input works | Every spatial shader needs a field-aware row stride; no measured need | Larger change; deinterlace or `setfield=prog` covers it |
| Refuse nv12 and nv24 in aa and dehalo because of #358 | The defect cannot appear | nv12 is the main hardware layout; the defect is NVIDIA-only and limited to the last 4 columns | Tracked in #358 instead |

## Consequences

- **Positive**: a layout or field structure the filters cannot process stops the graph with a message naming it, instead of giving wrong output or hanging.
- **Negative**: graphs that fed `yuyv422`, `gbrp`, `gray`, `yuva422p`, `yuva444p` or other unlisted layouts, or interlaced frames into a spatial filter, now fail at configuration or at the first interlaced frame. They need a `scale_vulkan` or `format` step, a deinterlacer, or `setfield=prog`.
- **Neutral**: the list is a header shared by all filters; adding a layout means one entry and one matrix row.

## References

- [Issue #188](https://github.com/VMAFx/pelorus/issues/188): the format matrix request; the interlace choice was the maintainer's decision on 2026-10-17 (paraphrased: refuse interlaced frames in the spatial filters, keep them in the measuring filters).
- [Issue #358](https://github.com/VMAFx/pelorus/issues/358): NVIDIA nv12/nv24 right edge in aa and dehalo.
- FFmpeg n9.0.2: `libavutil/hwcontext_vulkan.c` (supported software layouts), `libavfilter/vulkan_filter.c` (`ff_vk_filter_config_input`).
