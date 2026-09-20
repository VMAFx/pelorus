<!-- markdownlint-disable MD013 -->
# Research digest 0147 — Vulkan sample-domain and component correctness

Evidence for
[ADR-0147](../adr/0147-vulkan-sample-domain-and-components.md), collected on
2026-09-20 against FFmpeg n9.0.2 and the pre-fix Pelorus patch stack. The
commands ran on an NVIDIA RTX 4090; source-level findings are vendor-neutral.

## Representation boundary

FFmpeg's `ff_vk_create_imageviews(..., FF_VK_REP_FLOAT)` calls
`map_fmt_to_rep()`. For an underlying `VK_FORMAT_R16_UNORM` plane, the float
representation remains `VK_FORMAT_R16_UNORM`; GLSL therefore receives a float
normalized by 65535. FFmpeg maps `AV_PIX_FMT_YUV420P10` and
`AV_PIX_FMT_YUV420P12` planes to `VK_FORMAT_R16_UNORM`.

The pixel descriptors establish the data layout:

| Format | depth | shift | Meaning of a full-scale code in R16_UNORM |
|---|---:|---:|---:|
| yuv420p10le | 10 | 0 | `1023 / 65535` |
| yuv420p12le | 12 | 0 | `4095 / 65535` |
| p010le | 10 | 6 | `(1023 << 6) / 65535` |
| p012le | 12 | 4 | `(4095 << 4) / 65535` |

UNORM guarantees storage-container normalization. It does not reinterpret an
LSB-aligned 10-bit code as a full-range 10-bit value. The earlier statement in
`docs/development/bench-results.md` conflated those domains and must be
corrected.

## Analyzer reproduction

The same `testsrc2` picture was converted to each format, uploaded once, passed
through the pre-fix analyzer, and printed with the metadata filter:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
  -f lavfi -i 'testsrc2=size=64x64:rate=1' \
  -vf 'format=FORMAT,hwupload,pelorus_analyze_vulkan,hwdownload,\
format=FORMAT,metadata=print:file=-' -frames:v 1 -f null -
```

First reported frame:

| Format | variance | edge | complexity |
|---|---:|---:|---:|
| yuv420p | 0.036938 | 0.072623 | 0.283980 |
| yuv420p10le | 0.000002 | 0.001131 | 0.000410 |
| yuv420p12le | 0.000137 | 0.004525 | 0.002539 |
| p010le | 0.036654 | 0.072343 | 0.281902 |
| p012le | 0.036654 | 0.072343 | 0.281902 |

The planar 10/12-bit collapse follows directly from 16-bit container
normalization; variance shrinks quadratically. The small 8-bit versus P010/P012
difference is conversion quantization plus the fact that an MSB-aligned maximum
is slightly below 65535. The descriptor formula accounts for both shift cases.

## Semi-planar component reproduction

A constant red source gives distinct chroma values. The pre-fix denoise shader
read only component 0 of plane 1 and stored a constructed vector. With
`planes=3`, V was erased in every tested semi-planar layout:

| Format | Input U/V | Output U/V |
|---|---:|---:|
| NV12 | 90 / 240 | 90 / 0 |
| P010 | 360 / 960 | 360 / 0 |
| P012 | 1440 / 3840 | 1440 / 0 |

Reproducer shape:

```bash
ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
  -f lavfi -i 'color=c=red:size=64x64:rate=1:duration=1' \
  -vf 'format=FORMAT,hwupload,pelorus_denoise_vulkan=planes=3,\
hwdownload,format=FORMAT,signalstats,metadata=print:file=-' \
  -frames:v 1 -f null -
```

`AVPixFmtDescriptor` reports U and V on the same physical plane for these
formats, and FFmpeg exposes that plane as `R8G8` or `R16G16`. A plane-bitmask
selection must therefore run the scalar kernel for both components.

## Packed-component reproduction

The same constructed-vector store corrupts components that are not part of the
scalar algorithm. A constant RGBA red texel was `fd 00 00 ff` after a no-op
Vulkan round trip. The pre-fix aa, dehalo, and deblock filters each produced
`fd fd fd fd`. These filters have no defined RGB color transform; their safe
behavior is to replace the computed component and preserve the rest of the
loaded texel.

## Why MC must scale before quantization

The MC shader rounds each normalized per-pixel absolute difference after
multiplying by `PEL_MC_SAD_SCALE`, then reduces integer values. On an
LSB-aligned 10-bit plane, differences are about 64 times smaller before that
rounding. Multiplying the final host mean by 64 fixes its unit but cannot
recover every contribution already rounded to zero, and can change winning
motion vectors. The only correct boundary is before SAD and candidate
selection in the shader.

## Scope matrix

| Filter | Reads samples arithmetically | Writes samples | Component action |
|---|---|---|---|
| analyze | yes | no | luma only; scale before statistics |
| grain_estimate | yes | no | luma only; scale before residual/statistics |
| mc | yes | no | luma only; scale before SAD fixed point |
| deband | yes | yes | vector kernel; scale load and inverse-scale store |
| denoise | yes | yes | process U and V on selected semi-planar chroma |
| aa / dehalo / deblock | yes | yes | scalar kernel; semi-planar U/V loop, preserve other packed components |
| borderfix | no (coordinate selection only) | yes | raw whole-texel copy; no scale |
| qpmap | no picture sample input | map only | outside the contract |

Static compilation proves the C/GLSL layouts. The final evidence must also run
the matrix on a Vulkan device because neither glslang nor translation-unit
builds exercise image representation or stored components.
