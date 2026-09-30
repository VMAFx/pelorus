<!-- markdownlint-disable MD013 MD060 -->
# ADR-0150: Validate the stack on Intel Arc B580 and UHD 770 under Windows

- **Status**: Accepted
- **Date**: 2026-09-30
- **Deciders**: Lusoris
- **Tags**: vulkan, ffmpeg, intel, windows, validation, correctness

## Context

Pelorus's Vulkan evidence came from Linux drivers: NVIDIA RTX 4090, Intel Arc
A380 under ANV, and AMD RADV. The native Windows build (shw-1) was then run on
an office host with only two Intel GPUs: an Arc B580 (Xe2, driver 101.9033) and
the i9-12900K's UHD 770 (Xe-LP, driver 101.7092). Everything was run with the
Khronos validation layer. A positive control confirmed the layer was active in
`ffmpeg.exe`. The run covered:

- the ADR-0147 format matrix;
- every documented option of the nine Vulkan filters in 8- and 10-bit formats;
- the plane pass-through contracts;
- the encoder-steering side-data paths;
- the `pelorus_fgs` BSF.

The results fall into three groups. Evidence is in
[research digest 0150](../research/0150-intel-windows-vulkan-validation.md).

1. **Stock FFmpeg and driver behaviour that no filter can fix.** Intel's
   Windows drivers corrupt `VK_EXT_host_image_copy` round trips of
   multi-planar images. A standalone program with zero validation diagnostics
   reproduces it. FFmpeg uses host copy on both GPUs, so a default `hwupload`
   of any YUV format is already wrong before any filter runs. The UHD 770 also
   cannot allocate FFmpeg's single-plane optimal upload images. Two validation
   errors come from stock code on every run: a driver-zeroed
   `VkFormatProperties3` chain that FFmpeg reuses, and an invalid host layout
   transition.
2. **Three Pelorus defects the Linux devices did not expose.**
   - The pass-through analyzers mislabel forwarded frames when FFmpeg cannot
     reuse the input frames context.
   - The `tile`/`fast` shared-memory variants are not bit-identical at 10 and
     12 bit, because the driver fuses multiply-adds differently on each path.
   - Denoise needs 21 storage-image descriptors, but the UHD 770 exposes
     `maxPerStageDescriptorStorageImages = 16`.
3. **Documentation that overstated behaviour.**
   - Scene-cut keyframes need `-force_key_frames source` with the `ffmpeg`
     command line.
   - libaom and SVT-AV1 ignore `AV_FRAME_DATA_FILM_GRAIN_PARAMS`.
   - `pelorus_fgs components=0` is a pass-through at the NAL level, not at the
     byte level.
   - FFmpeg's HEVC decoder synthesizes only H.274 model 0.
   - Intel's Windows oneVPL runtimes reject dense HEVC MBQP, so QSV
     `-pelorus_roi` silently takes the rectangle path.

## Decision

1. **Validate Windows Intel in the device mode whose stock round trip is
   exact.** Use `disable_multiplane=1` on the Arc B580 and
   `linear_images=1,disable_multiplane=1` on the UHD 770. Pass it through the
   matrix's existing `VULKAN_DEVICE`. A row run in the default device mode on
   these drivers tests the driver corruption, not Pelorus, and does not count
   as evidence. Later quality or timing work on this host must use the same
   modes.
2. **Fix the three Pelorus defects at their canonical sources.**
   - `pelorus_analyze_vulkan`, `pelorus_grain_estimate_vulkan` and
     `pelorus_mc_vulkan` set their output link's frames context to the input
     link's.
   - Denoise, dehalo and aa make the storage-to-sample product `precise`
     (SPIR-V NoContraction). Denoise also makes its patch-SSD accumulator
     `precise`, and aa its squared Sobel magnitude.
   - Denoise binds its six read-only frames as `SAMPLED_IMAGE`, read with
     `texelFetch()`. Only its output stays a `STORAGE_IMAGE`.
3. **Guard each fix.**
   - A matrix row runs the pass-through filters on a linear input context.
   - A matrix row compares direct and tiled denoise at 10 bit.
   - A fast-gate budget check fails any filter whose storage-image bindings
     times 4 planes exceed 16.
   - The storage-domain checker treats `texelFetch()` as a load.
4. **Allowlist exactly the two stock VUIDs, by entry point.**
   `VUID-VkFormatProperties2-pNext-pNext` and
   `VUID-VkHostImageLayoutTransitionInfo-oldLayout-09230` are raised inside
   `vkGetPhysicalDeviceFormatProperties2` and `vkTransitionImageLayoutEXT`.
   Pelorus calls neither, and a bare `hwupload,hwdownload` emits both.
5. **Correct the docs; do not change the encoder or BSF code.** The encoder
   and BSF behaviour matches their ADRs once described precisely. The silent
   QSV fallback and the start-code normalization are recorded as follow-ups.

## Alternatives considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Run the gate in FFmpeg's default device mode | No per-device options | Every YUV row reads frames the driver has already corrupted, so it cannot pass or fail on Pelorus's merits | Rejected |
| Patch FFmpeg's host-copy path in the stack | Default mode would work | Pelorus does not carry libavutil fixes. The corruption is in the driver, so FFmpeg would have to avoid host copy for multi-plane images, which is an upstream decision | Rejected; reported upstream |
| Allowlist every VUID seen on Windows | Simple | Hides regressions. Attribution by entry point makes exactly two safe | Rejected |
| Document that `tile`/`fast` are only bit-identical at 8 bit | No shader change | Drops a guarantee three ADRs and the matrix rely on, even though a local NoContraction restores it on both devices for denoise and dehalo | Rejected |
| Make all of denoise's arithmetic `precise` | Most robust | Adds ALU in the hot NLM loop for no measured gain; the product and the SSD accumulator were enough | Rejected |
| Bind only the temporal frames each configuration uses | Fewer descriptors for small `prev` | Still 18 at `prev=4` on 3 planes. The shader uses all bindings statically | Rejected |
| Sampled images for denoise's read-only frames | Needs one storage array; sampled limit is 200 on the UHD 770 and far higher elsewhere; same UNORM values | Adds `GL_EXT_samplerless_texture_functions`; frames need `SAMPLED` usage, which FFmpeg's reuse check already requires | Chosen |
| Rewrite the pass-through output with a shared helper in patch 0001 | One definition | Couples three filter patches to 0001 for a 10-line function | Rejected; per-filter function |

## Consequences

- The ADR-0147 matrix now passes on both Intel GPUs with both layer builds.
  The only diagnostics left are the two stock VUIDs. The option sweep (482 runs
  per GPU, core and synchronization validation) and a GPU-assisted-validation
  subset (198 runs per GPU) are clean.
- At 10 and 12 bit, default-path output of aa, dehalo and denoise can change
  in rare samples. The shared-memory variants now match it. See the digest for
  the numbers.
- On the UHD 770 in the linear single-plane mode, aa `fast=1` still differs
  from `fast=0` by at most 1 code value on a few top-row samples in planar
  formats. It is deterministic, is not caused by float contraction, and does
  not occur in multi-plane modes or on the B580. The UHD 770's `fast=0` output
  is the outlier: its `fast=1` output matches the B580 byte for byte. It is
  open; aa's bit-identity claim carries that caveat.
- Linux ANV/RADV/NVIDIA are expected to be unaffected by the fixes' behaviour
  beyond the rounding note above. The rows still need re-running there.
- This host cannot exercise NVENC or Vulkan video encode. The Windows Intel
  drivers expose no `VK_KHR_video_encode_queue`. Those rows belong to another
  machine.

## References

- req: continue the Intel office-host hardware validation after the host crash
  ("go on"), stream shw stage 2 (correctness with validation layers).
- [Research digest 0150](../research/0150-intel-windows-vulkan-validation.md).
- [ADR-0147](0147-vulkan-sample-domain-and-components.md) (format matrix and
  storage domain); [ADR-0134](0134-denoise-shared-mem-tile.md),
  [ADR-0139](0139-dehalo-shared-mem-tile.md) and
  [ADR-0140](0140-aa-sobel-mag-hoist.md) (bit-identical variants);
  [ADR-0146](0146-qsv-roi-frame-ownership.md) (QSV dense MBQP);
  [ADR-0126](0126-scenecut-idr.md), [ADR-0115](0115-grain-estimate.md) and
  [ADR-0117](0117-grain-fgs-bsf.md) (the corrected docs).
