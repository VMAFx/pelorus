<!-- markdownlint-disable MD013 MD060 -->
# ADR-0202: Pelorus Vulkan shaders take the frame size from specialization constants, not imageSize()

- **Status**: Accepted
- **Implementation**: landed (#358)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: vulkan, ffmpeg, pixel-formats, nvidia
- **Related**: [ADR-0143](0143-ffmpeg-9-migration.md), [ADR-0201](0201-vulkan-input-format-allow-list.md)

## Context

Seven Pelorus shaders (aa, dehalo, deband, denoise, deblock, borderfix, analyze) took each plane's size from `imageSize()` of its image view. They used it for the bounds check and for clamping neighbour reads to the last row and column.

On NVIDIA (RTX 4090, driver 615.78.08), luma differs from the yuv420p result in the last columns of nv12 and nv24 frames whose width is not a multiple of 64. A shader probe showed `imageSize()` of the luma plane view returning 1920 for a 1918-wide nv12 frame, so the edge clamp read columns past the frame. On Intel ANV and AMD RADV the same rows give the yuv420p result (issue #358 width sweep). At width 1918 the old stack measured:

- aa: up to 7 codes in columns 1914-1917;
- dehalo: up to 6 codes;
- deband: up to 2 codes from column 1904 on;
- denoise: 1 code;
- borderfix `right=3`: 11 codes;
- analyze: complexity 0.086281 instead of 0.083958.

Width 1920 was exact. nv16, deblock and mc had no difference. The measurements are in [the research digest](../research/0358-vulkan-plane-size-nvidia.md).

The [ADR-0201](0201-vulkan-input-format-allow-list.md) format matrix compares layouts within a tolerance (maximum 0.055, about 14 codes at 8 bits) on a 128-wide fixture. It could not see this defect.

## Decision

1. **Frame size from the host**: every one of the seven shaders declares specialization constants 16 `pel_frame_w`, 17 `pel_frame_h`, 18 `pel_chroma_shift_w` and 19 `pel_chroma_shift_h`. `pel_plane_size(plane)` derives each plane's size from them, with chroma rounded up as `AV_CEIL_RSHIFT` does. Every former `imageSize()` call now uses it.
2. **One host helper**: `pel_vk_spec_add_frame_size()` in `ffmpeg-patches/files/pelorus_vulkan_input.h` adds the four constants from `FFVulkanContext.output_width`, `output_height` and the chroma shifts of `output_format`. Each host reserves `PEL_VK_SPEC_FRAME_SIZE_COUNT` extra entries. The push-constant layouts are unchanged.
3. **IDs**: 16-19 are the same in every Pelorus shader. 0-15 stay per-filter, and 253-255 stay reserved for the workgroup size.
4. **Proof**: `ffmpeg-patches/test/vulkan-format-matrix.sh` runs every spatial filter and analyze at widths 1918 and 1920 on yuv420p, nv12, nv16 and nv24. It requires byte-exact luma and identical analyze metadata across the four layouts.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Specialization constants (chosen) | No per-dispatch cost; the push layouts that C mirrors byte for byte stay as they are; the size is folded at pipeline creation | The pipeline is built for one frame size; a size change needs a new filter graph, which FFmpeg already rebuilds | Chosen |
| Push constants (as mc does) | Size can change per dispatch | Seven push structs and their C mirrors change; layout drift is a known defect class here | More surface for the same result |
| Sampler with clamp-to-edge addressing | The hardware does the clamp | Every `imageLoad` becomes a sampled read with a different descriptor type; the bounds check still needs the size | Larger change; does not remove the size need |
| Keep imageSize, clamp against `min(imageSize, size)` | Small diff | Still needs the true size from the host | Same host work as the chosen option, plus dead code |
| Refuse nv12/nv24 on NVIDIA | No shader change | nv12 is the main hardware layout | Rejected in ADR-0201 already |

## Consequences

- **Positive**: on NVIDIA and lavapipe (the drivers measured with the fix), the 8-bit semi-planar layouts now give the yuv420p luma byte for byte at widths that are not a multiple of 64. analyze metadata no longer depends on the layout.
- **Negative**: a new Pelorus shader that reads its own plane size must use `pel_plane_size()` and call the host helper. A shader that uses `imageSize()` brings the defect back on NVIDIA. The exact-equality matrix rows catch this for the filters they cover.
- **Neutral**: no ABI, option or push-layout change.

## References

- [Issue #358](https://github.com/VMAFx/pelorus/issues/358): the measurement and the probe.
- [Research digest 0358](../research/0358-vulkan-plane-size-nvidia.md).
- FFmpeg n9.0.2: `libavfilter/vulkan_filter.c` (`ff_vk_filter_config_output` sets `output_width`, `output_height`, `output_format`), `libavutil/vulkan.h` (`SPEC_LIST_CREATE`, `SPEC_LIST_ADD`).
