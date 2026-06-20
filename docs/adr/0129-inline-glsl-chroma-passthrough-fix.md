<!-- markdownlint-disable MD013 MD060 -->
# ADR-0129: Runtime-only GPU defects — on-device validation as a release gate

- **Status**: Accepted
- **Date**: 2026-06-20
- **Deciders**: Lusoris

## Context

On-device execution of the merged filter family on a real Vulkan device
(RTX 4090) surfaced two **runtime-only defects** in shipped, merged code that
every static gate had passed. Neither is reachable by the patch-stack CI (it
compiles the filter translation units but never instantiates a Vulkan device),
by `glslangValidator` (it only checks the standalone `.comp` reference shaders),
or by the type-check parity build (compile against n8.1.1 headers, no execution).
A multi-agent audit of all nine inline-GLSL filters independently re-derived both
defects with high confidence and reported no other runtime-only defect class.

### Defect 1 — chroma-passthrough macro (5 filters)

`dehalo`, `aa`, `deblock`, `deband`, and `borderfix` emitted their per-plane
chroma-passthrough branch as a **two-line split `GLSLF`**:

```c
GLSLF(1, imageStore(output_images[%i], pos,            ,i);   // imageStore( never closed here
GLSLF(2,             imageLoad(input_images[%i], pos)); ,i);
```

The `imageStore(` parenthesis is never closed inside the first `GLSLF(...)`
macro call, so the C preprocessor keeps consuming tokens across the newline,
mis-splits the macro arguments, and emits the *expansion* of the second `GLSLF`
— literal `do { av_bprintf(&shd->src, ...); } while (0)` control-flow text —
**into the shader source**. At first-frame `init_filter()` shaderc aborts the
SPIR-V compile with `syntax error, unexpected COMMA`, and `filter_frame` fails
with `-22 (EINVAL)`. The `.o` compiles cleanly (valid C; garbage only at
`av_bprintf` runtime) and the chroma loop is C-emitted so no `.comp` contains it.

`dehalo`/`aa`/`deblock` default to luma-only, so their chroma planes always took
the broken branch — an **active** failure. `deband`/`borderfix` default to all
planes, so the branch was **latent**, armed by any non-default `planes` mask.
`denoise` already used the correct single balanced form and was never affected.

### Defect 2 — mc image-descriptor array undersized

`vf_pelorus_mc_vulkan` declared its `cur_image` and `ref_image` bindings with
`.elems = 1`, reasoning that the shader only reads plane 0 (luma). But the
host-side fill, `ff_vk_shader_update_img_array()` (libavutil/vulkan.c:2735),
unconditionally writes one descriptor per frame plane:

```c
const int nb_planes = av_pix_fmt_count_planes(hwfc->sw_format);
for (int i = 0; i < nb_planes; i++)
    ff_vk_shader_update_img(s, e, shd, set, binding, i, views[i], ...);
```

For any multi-plane input (yuv420p = 3 planes) it writes `dstArrayElement` 1 and
2 into a one-element descriptor array — an **out-of-bounds descriptor write** on
every frame. The shader's "reads plane 0 only" rationale governs GLSL *indexing*,
not the host write loop. The image views for the extra planes already exist
(`ff_vk_create_imageviews` fills all planes into an `AV_NUM_DATA_POINTERS`
array), so only the binding size was wrong. The defect is undefined behaviour:
it raises a validation error (VUID-VkWriteDescriptorSet-dstArrayElement-00321)
and can fault or corrupt adjacent descriptors on a strict driver; on this box's
NVIDIA driver with validation layers absent it happened to run, which is exactly
why a compile-and-smoke gate did not catch it.

## Decision

1. **Defect 1**: collapse the split emit to a single balanced `GLSLF` in all
   five filters — the idiom `denoise` already used and the one runtime-verified
   to compile and run:

   ```c
   GLSLF(1, imageStore(output_images[%i], pos, imageLoad(input_images[%i], pos)); ,i, i);
   ```

   Every parenthesis closes inside the macro call, so the inner commas are
   protected and only the two trailing `,i, i` reach the variadic list. Both
   active failures and both latent copies are fixed together so the family
   converges on one correct idiom. The `.comp` reference shaders are untouched,
   preserving shader/filter lockstep (AGENTS hard rule 4).

2. **Defect 2**: size the `mc` image bindings to the plane count
   (`.elems = av_pix_fmt_count_planes(vkctx->input_format)`) for both
   `cur_image` and `ref_image`, matching the `deband`/`denoise` precedent. The
   shader keeps indexing only plane 0; the descriptor array is now large enough
   for the per-plane writes the library performs.

3. **Process**: adopt **on-device execution of every filter as a release gate**,
   exercising both plane-mask regimes (luma-only *and* all-planes) so the
   chroma-passthrough branch is hit, and — where the toolchain provides them —
   with Vulkan validation layers enabled so descriptor-array and binding faults
   are caught deterministically. The patch-stack TU compile remains necessary
   but is not sufficient.

## Alternatives considered

- **Fix only the three active chroma failures.** Rejected: `deband`/`borderfix`
  carry the identical latent defect and fail the moment a user sets a non-default
  `planes` mask. Leaving a known latent crash to minimise diff is the wrong
  trade.
- **Ship the two defects as separate PRs.** Rejected: both are the same class
  (runtime-only GPU defects invisible to CI), both were surfaced by the same
  on-device validation sweep, and bundling them under one decision record avoids
  a second CI round-trip while keeping the prevention thesis in one place.
- **Keep `mc` at `.elems = 1` and trust "reads plane 0 only".** Rejected: the
  host write loop is library-controlled and writes `nb_planes` descriptors
  regardless of shader indexing. The binding must match the writer, not the
  reader.
- **Add a runtime smoke test but defer the code fixes.** Rejected: both defects
  are in merged, user-reachable filters and must be fixed now; the gate is
  prevention, not a substitute.

## Consequences

- `dehalo`, `aa`, `deblock` run on the GPU instead of failing with `-22`;
  `deband`/`borderfix` are correct under any `planes` mask; chroma planes are
  passed through bit-identically (verified: infinite PSNR on U and V vs a no-op
  round-trip).
- `mc` no longer issues out-of-bounds descriptor writes on multi-plane input.
- The patch stack is regenerated for the six affected patches (`0001`, `0007`,
  `0014`, `0015`, `0017`, `0018`); the `.comp` files and the other twelve
  patches are unchanged.
- The release process gains an on-device execution gate covering both plane-mask
  regimes (and validation layers where available), closing the class of blind
  spot that hid both defects.

## References

- Defect 1 symptom: `pelorus_dehalo:107: error: '' :  syntax error, unexpected COMMA`
  → `Task finished with error code: -22 (Invalid argument)` on `vk:0` (RTX 4090).
- Defect 1 reference idiom: `vf_pelorus_denoise_vulkan.c` chroma passthrough.
- Defect 2: `ff_vk_shader_update_img_array` (libavutil/vulkan.c:2735) writes one
  descriptor per plane; `vf_pelorus_mc_vulkan.c` bindings were `.elems = 1`.
- Chroma bit-exactness: lossless ffv1 comparison of each luma-only filter vs a
  no-op hwupload/hwdownload — `U:1.000000 (inf) V:1.000000 (inf)`.
- Independent audit: `pelorus-runtime-bug-audit` workflow — confirmed both
  defects, reported all other runtime-only classes clean across nine filters.
