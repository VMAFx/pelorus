<!-- markdownlint-disable MD013 MD060 MD029 -->
# ADR-0237: HDR Tone-Map Stage Runs Before the Pelorus Filters

- **Status**: Accepted
- **Implementation**: this pull request
- **Date**: 2026-10-28
- **Deciders**: Lusoris
- **Tags**: filters, vulkan, hdr, tonemap, ffmpeg-patches, shared-series
- **Related**: [ADR-0185](0185-shared-ffmpeg-fix-series.md), [ADR-0143](0143-build-time-spirv-only.md)

## Context

Pelorus provides GPU-accelerated pre-encode video filtering through Vulkan compute filters in `ffmpeg-patches/files/vf_pelorus_*_vulkan.c` (deband, analyze, denoise, grain estimation, motion compensation, dehalo, aa, deblock, borderfix, scenecut) with zero-copy VRAM preservation. When processing high dynamic range (HDR) source material—specifically HDR10 and Dolby Vision profile 5 (DV5)—for downstream standard dynamic range (SDR) distribution and encoding, frames must undergo tone-mapping to BT.709 colorimetry.

The shared FFmpeg fix series `VMAFx/ffmpeg-patches`, pinned at `v0.5.0` (`build-config.env`, PR #447, [ADR-0185](0185-shared-ffmpeg-fix-series.md)), supplies native HDR tone-mapping capabilities introduced in `v0.4.0`: the `dovi_reshape` CPU filter (Dolby Vision profile 5 reshape, emitting planar floating-point `gbrpf32` PQ BT.2020 frames), and `tonemap_vulkan` (`tonemap=bt2408_hable`, BT.2408 Hable curve on IPT color space; `peak_detect=1` dynamically measuring signal peak on GPU and consuming Dolby Vision level 1 metadata when present; input P010 PQ BT.2020 limited range; output `format=` NV12 default or P010, BT.709 limited range), alongside CPU, OpenCL, and VAAPI tone-map paths.

All Pelorus Vulkan filters accept NV12 and P010 software layouts under `AV_PIX_FMT_VULKAN` (`ffmpeg-patches/files/pelorus_vulkan_input.h:57`), allowing frames output by `tonemap_vulkan` to feed directly into the Pelorus filter chain in VRAM with no format re-allocation or conversion. This record establishes the architectural placement of the HDR tone-mapping stage relative to the Pelorus Vulkan filters, defines the zero-copy boundary across HDR10 and Dolby Vision profile 5, and documents the measurement and parity contract.

## Decision

1. **Order: tone-map FIRST, before Pelorus filters.** The HDR tone-map stage executes immediately upon VRAM upload, preceding the Pelorus filter sequence: `tonemap_vulkan` -> `pelorus_analyze_vulkan` -> `pelorus_grain_estimate_vulkan` -> `pelorus_mc_vulkan=meta=1` -> `pelorus_denoise_vulkan=mc=1` -> `pelorus_deband_vulkan` -> encoder. Pelorus thresholds (deband threshold `thry`, denoise strength `sigma`, the grain estimate) act on the code values of the frame they read, and the encoder receives the tone-mapped SDR frame, so the filters must read that frame rather than PQ code values. Banding visible to downstream encoders occurs in the SDR output code space, so placing `pelorus_deband_vulkan` after `tonemap_vulkan` catches banding introduced across smooth gradients by the non-linear tone curve. Furthermore, `pelorus_grain_estimate_vulkan` reads the exact SDR signal delivered to the encoder rather than estimating pre-compression grain across a high dynamic range.

2. **Vulkan zero-copy scope and DV5 CPU reshape caveat.** After `hwupload`, tone-mapping and the Pelorus filters run in Vulkan memory with no `hwdownload` before the encoder. For HDR10, frames stay in GPU memory from initial upload (`format=p010,hwupload,tonemap_vulkan=...`) through encoding (`hevc_vulkan` or `hevc_nvenc` via derived CUDA device `hwupload=derive_device=cuda`). With Vulkan hardware-accelerated decode (`-hwaccel vulkan`), the initial upload is also eliminated (not measured here; documented as such). For Dolby Vision profile 5 (DV5), no GLSL reshape filter exists: decode is software, followed by `dovi_reshape` on CPU, `scale=out_color_matrix=bt2020:out_range=tv,format=p010`, and a single `hwupload` to Vulkan VRAM before `tonemap_vulkan`. There is one upload, zero downloads, and the reshape executes on CPU before entering the GPU pipeline.

3. **Measurement contract.** Parity targets follow `VMAFx/ffmpeg-patches` ADR-0002 (`docs/adr/0002-dovi-reshape-and-tone-mapping.md` in that repository): the standalone tone-map stage is evaluated against reference `vf_libplacebo` from the same build (`tonemapping=hable:gamut_mode=clip:dithering=none:deband=0`, peak detection matched, output BT.709 limited `yuv420p`). Parity target is mean CIEDE2000 <= 2.3 on DV5 and HDR10 clips, measuring every 30th frame with `scripts/tonemap_parity.py` in the series repository. The measurement run for this pull request on an RTX 4090 reports the following metrics (Pelorus chain numbers are reported for observability, not gated by the tone-map parity threshold):

| Clip | Path | Mean CIEDE2000 | p95 CIEDE2000 | Wall time (s) | VRAM above idle (MiB) | Ref wall time (s) | Ref VRAM above idle (MiB) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| HDR10 (`a510c0a7b563`) | `tonemap_vulkan` standalone | 0.523 | 1.133 | 8.589 | not reported (1) | 3.770 | 597 |
| HDR10 (`a510c0a7b563`) | `tonemap_vulkan` + Pelorus chain | 0.527 | 1.133 | 28.403 | 1576 | 3.679 | 596 |
| DV5 (`d7ef48d2a9f3`) | `dovi_reshape` + `tonemap_vulkan` standalone | 0.589 | 1.481 | 1.916 | 111 | 1.466 | 231 |
| DV5 (`d7ef48d2a9f3`) | `dovi_reshape` + `tonemap_vulkan` + Pelorus chain | 0.626 | 1.517 | 3.020 | 263 | 1.567 | 230 |

(1) Another process on the GPU grew from 386 MiB to 14566 MiB during this call, so its VRAM figure is not reported; the other three Vulkan rows ran with every other GPU process unchanged. Frames scored: HDR10 10, DV5 4 (every 30th frame). The largest single-frame CIEDE2000 values are 32.8 (HDR10) and 7.9 (DV5); 1.7 % and 0.6 % of pixels exceed 2.3.

4. **No Pelorus code change.** Pelorus Vulkan filters already accept NV12 and P010 software formats (`pelorus_vulkan_input.h:57`). Because `tonemap_vulkan` outputs NV12 by default or P010 when configured, no filter host code, shader code, or interop ABI change is required in Pelorus for issue #356.

5. **Documented recipes.** Official recipes for HDR10 and Dolby Vision profile 5 tone-mapping before the Pelorus filter chain are documented in `docs/usage/ffmpeg.md` with zero-copy validation against `hevc_vulkan` and `hevc_nvenc`.

## Alternatives considered

| Option | Verdict | Reason |
| :--- | :--- | :--- |
| Pelorus filters on PQ before tone-map | Rejected | Filter thresholds (`thry`, `sigma`, grain estimate) act on code values, so PQ input would need PQ-specific parameter variants; banding introduced by the non-linear tone curve escapes debanding; ADR-0002 parity only covers tone-mapped SDR output. |
| Write a Pelorus GLSL Dolby Vision reshape filter | Rejected | Duplicates existing series work; generic FFmpeg filter development belongs in `VMAFx/ffmpeg-patches` per `AGENTS.md` rule 5 rather than the Pelorus filter stack. |
| Use `vf_libplacebo` as the tone-mapping stage | Rejected | Adds an external libplacebo build dependency that the standalone replay and CI environments lack (`AGENTS.md` rule 9); additionally, `vf_libplacebo` serves as the independent reference oracle for parity testing and must remain decoupled from the tested pipeline. |

## Consequences

- Users can process HDR10 and Dolby Vision profile 5 inputs through the complete Pelorus pre-encode pipeline with no download after upload.
- No changes to `libpelorus/include/pelorus/interop.h`, `ffmpeg-patches/files/`, or shaders.
- Requires shared FFmpeg fix series `v0.4.0` or higher (`build-config.env` pins `v0.5.0`).
- DV5 decoding requires CPU time for `dovi_reshape` prior to Vulkan `hwupload`.
- Zero-copy is verified on the filter graph that FFmpeg writes with `-print_graphs_format json`: no `hwdownload`, and every link after the upload carries a hardware format. A planted download round trip is refused ([research digest](../research/0356-tonemap-stage-parity.md)).
- Parity and performance measurements are catalogued in `docs/research/0356-tonemap-stage-parity.md`.

## References

- Issue [#356](https://github.com/VMAFx/pelorus/issues/356): HDR tone-map stage before Pelorus filters.
- Shared series issue [VMAFx/ffmpeg-patches#63](https://github.com/VMAFx/ffmpeg-patches/issues/63).
- Shared series ADR-0002: [docs/adr/0002-dovi-reshape-and-tone-mapping.md](https://github.com/VMAFx/ffmpeg-patches/blob/master/docs/adr/0002-dovi-reshape-and-tone-mapping.md).
- Research digest: [docs/research/0356-tonemap-stage-parity.md](../research/0356-tonemap-stage-parity.md).
- Evidence: measurement JSON artifacts (`parity-hdr10.json`, `parity-dv5.json`, `pelorus-chain-*.json`, `zc-*.json`, `zc-*.check`) kept locally and recorded in [docs/research/0356-tonemap-stage-parity.md](../research/0356-tonemap-stage-parity.md).
