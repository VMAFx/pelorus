<!-- markdownlint-disable MD013 MD060 -->
# ADR-0209: Zero-copy chain vendor legs and encoder frames assertion

- **Status**: Accepted
- **Implementation**: landed (#105)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: tester, zero-copy, vulkan, nvenc, vaapi, qsv, gpu
- **Related**: [ADR-0173](0173-tester-programme.md), [ADR-0180](0180-tester-vendor-images.md), [research 0172](../research/0172-zero-copy-audit.md)

## Context

Tester stage `zero_copy_chain` ([ADR-0173](0173-tester-programme.md), `tools/tester/pelorus_tester_stages.py`) originally verified device residency for only two paths: a pure native Vulkan pipeline (`h264_vulkan`) and a software-encoder fallback (`libsvtav1`/`libaom-av1`). Real-world zero-copy workflows feed hardware encoders from Vulkan filter outputs across vendor-specific device boundaries (NVIDIA NVENC via CUDA, Intel/AMD VAAPI via DRM PRIME/hwmap, and Intel QSV via VAAPI mapping).

Without vendor-specific execution legs in `zero_copy_chain`:

1. Inter-device hops (such as `hwupload=derive_device=cuda` for Vulkan-to-CUDA transfer on NVIDIA) would be flagged by the graph transfer classifier as illegal host uploads, because `hwupload` is counted without inspecting surface formats.
2. Inadvertent fallback to host system memory was undetectable from FFmpeg's exit code alone: a pipeline with an inserted `hwdownload` followed by software encoding or host-based re-upload still exited 0.
3. Missing hardware devices or absent encoder components would not be distinguished from passes or genuine failures in automated reporting.
4. The `vmafx` scoring leg on hardware frames is deferred and blocked on #160 (coexistence of the Pelorus patch stack with the vmafx series, needed for one FFmpeg build that carries both).

## Decision

1. **Surface Format Classification for Hardware Hops**:
   `transfer_problems()` inspects `[hwupload @ ADDR] Surface format is <fmt>.` log lines. Hardware formats (`vulkan`, `cuda`, `vaapi`, `qsv`, `drm_prime`) designate legitimate device-to-device hops and credit allowed `hwupload` instances, one per distinct filter address (`allowed = int(sw_source) + hops`). Unclassified `hwupload` nodes without a matching hardware surface line default to host uploads (fail closed).
2. **Hardware Encoder Frames Assertion (`zc_encoder_frames`)**:
   `encoder_frames_problems()` requires the log line `Using input frames context (format <hw_format>) with <encoder> encoder.`. If the line is missing or specifies a non-hardware format, the leg fails with `"encoder <enc> did not receive <fmt> frames (host frames)"` under rule `zc_encoder_frames`. This is enforced on all vendor legs and on the native leg.
3. **Structured Bounded Vendor Leg Table**:
   The stage declares four vendor legs in a bounded table:

   - `h264_nvenc`: NVIDIA (vendor `0x10de`), CUDA hardware format, `vulkan=vk:<idx>,disable_multiplane=1`, tail `hwupload=derive_device=cuda`.
   - `h264_vaapi-intel`: Intel (vendor `0x8086`), VAAPI hardware format, `LIBVA_DRIVER_NAME=iHD`, DRM render node, `vulkan=vk@va`, tail `pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=vaapi,format=vaapi`.
   - `h264_qsv-intel`: Intel (vendor `0x8086`), QSV hardware format, `LIBVA_DRIVER_NAME=iHD`, VAAPI tail plus `hwmap=derive_device=qsv,format=qsv`, `-q:v 30`.
   - `h264_vaapi-amd`: AMD (vendor `0x1002`), VAAPI hardware format, `LIBVA_DRIVER_NAME=radeonsi`, render node, same VAAPI mapping tail.

   Each encoder identifier adheres to schema regex `^[a-z0-9_-]{1,64}$` and is unique per stage.
4. **Stated Skip Prefixes**:
   A leg is marked `not_run` only with an explicit, exact prefix:

   - `no_device:` when no compatible render node or Vulkan device exists for the vendor ID.
   - `encoder_absent:` when the required encoder is absent from FFmpeg's configured encoders.

   Silent passes are strictly forbidden.
5. **Stage Verdict Rule**:
   The stage fails if any executed leg fails. The stage passes if the native leg or at least one vendor leg passes (proving device residency). If only the software-encoder leg passes or all legs are skipped, the stage verdict is `not_run`.
6. **Fixture Normalization**:
   Vendor legs convert the synthetic YUV420P fixture to raw NV12 in the stage directory prior to chain execution. This eliminates FFmpeg's automatic insertion of `auto_scale_0` during format negotiation, avoiding false zero-copy scale rejections.
7. **vmafx Leg Scope**:
   The `vmafx` evaluation leg (evaluating VMAF directly on hardware frames) is not included in this change and remains blocked on #160.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Surface format inspection (chosen) | Distinguishes device hops from host uploads; detects planted re-uploads | Requires debug log parsing | Chosen |
| Count `hwupload` blindly without format | Simple node count | Flags valid Vulkan->CUDA and Vulkan->VAAPI device transfers as illegal host copies | Rejected: rejects correct zero-copy recipes |
| Rely on FFmpeg exit code 0 | Zero log parsing | Passes pipelines with planted host downloads and re-uploads | Rejected: exit code does not prove device residency |
| Mutate `ctx["env"]` globally for `LIBVA_DRIVER_NAME` | No helper signature change | Cross-contaminates driver environment across vendor legs | Rejected: per-leg env override in `ffrun()` isolates drivers |
| Insert `scale_vulkan=format=nv12` in chain | Keeps YUV420P input fixture | Returns `ENOTSUP` on current Vulkan filter drivers | Rejected: breaks pipeline execution |

## Consequences

- **Positive**: Zero-copy execution is rigorously verified per hardware vendor; planted host downloads or re-uploads fail deterministically; missing drivers or encoders produce explicit `not_run` records.
- **Negative**: Adds vendor leg execution time to `zero_copy_chain` when hardware devices are available.
- **Neutral**: When no hardware device is present, `zero_copy_chain` reports `no_device` with exit code 0, preserving existing non-failure behavior on CPU-only test runners.

## References

- Issue [#105](https://github.com/VMAFx/pelorus/issues/105): tester: zero-copy chain assertion per vendor.
- Issue [#160](https://github.com/VMAFx/pelorus/issues/160): coexistence of the Pelorus patch stack with the vmafx series.
- [Research 0172](../research/0172-zero-copy-audit.md): zero-copy audit and hop matrix.
- [ADR-0173](0173-tester-programme.md): tester programme architecture and stage contract.
- [ADR-0180](0180-tester-vendor-images.md): tester vendor images and driver isolation.
