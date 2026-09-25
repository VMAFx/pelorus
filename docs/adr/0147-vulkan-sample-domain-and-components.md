<!-- markdownlint-disable MD013 MD060 -->
# ADR-0147: Normalize Vulkan arithmetic to the sample domain and preserve plane components

- **Status**: Accepted
- **Date**: 2026-09-20
- **Deciders**: Lusoris
- **Tags**: vulkan, ffmpeg, bit-depth, pixel-format, correctness

## Context

Pelorus's Vulkan filters bind FFmpeg hardware frames through
`FF_VK_REP_FLOAT`. That name describes the shader representation, not the
meaning of its numeric range. FFmpeg maps a 10- or 12-bit planar sample to an
`R16_UNORM` view while the corresponding software pixel format stores the
sample LSB-aligned (`shift == 0`). An `imageLoad()` therefore returns
`code / 65535`, not `code / ((1 << depth) - 1)`. The same picture is 64 times
darker to shader arithmetic at 10 bit and 16 times darker at 12 bit. The
existing claim that UNORM alone made the filters bit-depth agnostic is false.

The defect affects every value derived from a sample: analyze/grain statistics,
motion SAD and confidence, thresholds, temporal weights, and pixel transforms.
Correcting a threshold on the host is insufficient because the algorithm still
runs in the wrong domain. Correcting an integer SAD after readback is also too
late: small per-pixel differences have already rounded to zero in the shader.

A second defect shares the same boundary. FFmpeg's `planes` option selects
physical image planes, but a physical plane is not necessarily scalar. NV12,
P010, and P012 store U and V in the two components of plane 1. Several scalar
shaders computed `.x` and stored a constructed `vec4`, which replaced V with a
constant. On the pre-fix n9.0.2 build, red NV12/P010/P012 frames changed V from
240/960/3840 to zero under `pelorus_denoise_vulkan=planes=3`; aa, dehalo, and
deblock turned an RGBA texel `fd 00 00 ff` into `fd fd fd fd`.

## Decision

Pelorus will treat storage normalization and algorithm normalization as
different domains.

1. A shared private FFmpeg helper will derive `sample_scale` and the logical
   integer `code_max` from the `AVPixFmtDescriptor` for supported planar,
   semi-planar, and single-component integer UNORM formats:

   ```text
   sample_scale = storage_max / (((1 << depth) - 1) << shift)
   ```

   where `storage_max` is 255 for an 8-bit component container and 65535 for a
   16-bit component container, and `code_max = (1 << depth) - 1`. Invalid,
   packed, float, or otherwise unsupported descriptor shapes conservatively
   return scale 1.0 with explicit quantization disabled rather than guessing.
2. Every arithmetic shader converts loads to the true sample domain before any
   threshold, difference, interpolation, accumulation, or fixed-point
   quantization. Pixel-transform shaders clamp and round to `code_max` in that
   domain before dividing by `sample_scale` at the storage boundary. The
   explicit logical-code rounding is equivalent to native UNORM writeback for
   unshifted formats and keeps P010/P012 low padding bits zero. Read-only
   filters do not perform the inverse conversion.
   `code_max` is a specialization constant because denoise already uses the
   Vulkan baseline guarantee of 128 push-constant bytes; a compile-time guard
   prevents that block from growing past the portable limit.
3. `mc` applies the scale before SAD quantization. Host-side rescaling of
   `sad_out` is forbidden because it cannot recover discarded precision.
4. The `planes` bitmask continues to address FFmpeg physical planes. An
   unselected plane is copied byte-for-byte. When a selected semi-planar chroma
   plane carries U and V, scalar filters process both components with the
   chroma parameters. Scalar filters use read-modify-write and preserve any
   component they do not explicitly process, including extra components in a
   packed view. The vector-valued deband kernel continues to process the whole
   selected texel.
5. `borderfix` is exempt from sample scaling because it only copies a texel
   from one coordinate to another; changing domains would add rounding without
   changing its decision. The quant-map shader is also outside this contract
   because it does not consume picture samples.
6. Format-equivalence and component-survival tests are release evidence, not
   optional diagnostics. The matrix covers 8/10/12-bit planar inputs,
   NV12/P010/P012 with distinct U and V, scalar packed-RGBA preservation,
   direct and tiled denoise, temporal/lookahead paths, analyzer telemetry, and
   the MC-to-denoise runtime path.

This is an internal FFmpeg-filter correction. It does not change the public
libpelorus ABI, AVOption names/ranges, or the Pelorus release version.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|---|---|---|---|
| Scale only thresholds on the host | Small C/GLSL diff | Filters still interpolate, accumulate, and emit telemetry in storage units | Fixes one comparison, not the arithmetic contract |
| Rescale analyzer and MC results after readback | Leaves shaders unchanged | Nonlinear operations and integer quantization have already lost information | Observed MC SAD precision cannot be recovered after `round()` |
| Add independent scale helpers to each filter | Easy incremental patches | Formula and supported-format policy can drift across nine filters | A cross-filter representation boundary needs one implementation |
| Restrict the filters to 8-bit input | Avoids normalization work | Drops the 10/12-bit hardware-encode path Pelorus exists to serve | Not an acceptable product boundary |
| Convert every shader to integer storage images | Makes code values explicit | Reworks all algorithms, format declarations, and interpolation paths | Far larger change than a domain conversion at the boundary |
| Treat every component of every packed plane as independently filterable | Uniform loop | Alpha and non-YUV channels have different semantics | Preserve unspecified packed components; explicitly support semi-planar U/V |

## Consequences

- **Positive**: equivalent 8/10/12-bit pictures drive equivalent thresholds,
  statistics, SAD/confidence, and filter strength.
- **Positive**: selecting a semi-planar chroma plane no longer erases V, and a
  scalar YUV operator no longer broadcasts its first component into RGBA.
- **Positive**: one helper and one regression checker make the format boundary
  reviewable across the filter family.
- **Negative**: arithmetic shaders gain a scale push constant, transforms gain
  a code-max specialization constant, and scalar chroma transforms may run
  twice on a selected semi-planar plane.
- **Negative**: exact GPU output still requires on-device tests; SPIR-V compile
  and C translation-unit builds cannot prove numeric or component behavior.
- **Neutral**: historical measurements made on planar 10/12-bit Vulkan inputs
  must be treated as invalid unless they predate the shader or independently
  corrected this scale.

## References

- Source: `req` — continue the active Pelorus/VMAFx update rather than stopping
  at version bookkeeping.
- [Research digest 0147](../research/0147-vulkan-storage-domain.md).
- [ADR-0129](0129-inline-glsl-chroma-passthrough-fix.md) and
  [ADR-0143](0143-ffmpeg-9-migration.md).
- FFmpeg n9.0.1/n9.0.2 `libavutil/vulkan.c` (`map_fmt_to_rep`,
  `ff_vk_create_imageviews`) and `libavutil/pixdesc.c`.
