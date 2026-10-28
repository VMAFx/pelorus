<!-- markdownlint-disable MD013 MD060 -->
# HDR tone-map stage parity and performance measurements

**Date:** 2026-10-28

**Decision:** [ADR-0237](../adr/0237-tonemap-stage-placement.md), issue [#356](https://github.com/VMAFx/pelorus/issues/356).

**Scope:** Evaluation of HDR tone-mapping stages placed upstream of the Pelorus Vulkan filter pipeline for HDR10 and Dolby Vision profile 5 (DV5) sources. Measurements evaluate visual parity against `vf_libplacebo` under the contract defined in `VMAFx/ffmpeg-patches` ADR-0002, measure wall-clock run time and peak VRAM consumption above idle, and verify zero-copy VRAM preservation into hardware encoders (`hevc_vulkan` and `hevc_nvenc`).

## Verdict

Pass. Standalone `tonemap_vulkan` stays under the ADR-0002 bar of mean CIEDE2000 2.3 against `vf_libplacebo` on both clips (HDR10 0.523, DV5 0.589), and adding the Pelorus chain after it moves the mean by at most 0.04. All four recipes keep frames in GPU memory from the upload to the encoder, and the check refuses a planted download round trip.

## Method & setup

### Testbed environment

- **GPU:** NVIDIA GeForce RTX 4090 (24 GiB VRAM).
- **Host build:** Local measurement build uniting FFmpeg `n9.0.2` (base commit `946fcce0`), the shared fix series `v0.5.0` (`7641804b`), the Pelorus 23-patch stack, `libplacebo`, and `libx265`. This build contains GPL components and is used exclusively for internal measurement; it is not shipped in production distributions.

### Test fixtures

In conformance with `VMAFx/ffmpeg-patches` ADR-0002, no third-party clip is committed; a clip that is not committed is named by its SHA-256 digest only:

- **Dolby Vision profile 5 (DV5):** `tests/fixtures/dv5/dv5.mp4` from `VMAFx/ffmpeg-patches`, procedurally generated via `dv5gen.c` (sha256 `d7ef48d2a9f37fe4c6f7fe0191676919dba9017724fb38da9fd68ade0ac7d22e`).
- **HDR10:** 2160p BT.2020 PQ HEVC sequence (libx265), 300 frames, not committed, sha256 `a510c0a7b56353f58eeb2d70197bb50057a54c413b5f9f058c78b926db544808`, referenced strictly by SHA-256 digest.

### Parity oracle & evaluation contract

- **Reference filter:** `vf_libplacebo` from the identical measurement build, configured with:
  `tonemapping=hable:gamut_mode=clip:dithering=none:deband=0`
  Peak detection is enabled to match `tonemap_vulkan=peak_detect=1`, outputting BT.709 limited range `yuv420p`.
- **Parity metric:** Mean CIEDE2000 <= 2.3 across evaluated clips, computed on every 30th frame using `scripts/tonemap_parity.py` from `VMAFx/ffmpeg-patches`.
- **Reported fields:** Mean CIEDE2000, 95th percentile (p95) CIEDE2000, wall-clock processing time (seconds), and peak VRAM consumption above idle (MiB), alongside reference wall time and reference VRAM.
- **Filter chain scope:** Pelorus chain runs `tonemap_vulkan` followed by `pelorus_analyze_vulkan`, `pelorus_grain_estimate_vulkan`, `pelorus_mc_vulkan=meta=1`, `pelorus_denoise_vulkan=mc=1`, and `pelorus_deband_vulkan`. Metrics for the combined Pelorus chain are reported for system characterization; only the standalone tone-mapping stage is subject to the <= 2.3 CIEDE2000 parity gate.

## Parity and performance results

Measured on an RTX 4090 (driver 615.78.08) with the build above, FFmpeg `n9.0.2-62-g579d904fa8a`:

| Clip | Pipeline path | Mean CIEDE2000 | p95 CIEDE2000 | Wall time (s) | VRAM above idle (MiB) | Ref wall time (s) | Ref VRAM above idle (MiB) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| HDR10 (`a510c0a7b563`) | `tonemap_vulkan` standalone | 0.523 | 1.133 | 8.589 | not reported (1) | 3.770 | 597 |
| HDR10 (`a510c0a7b563`) | `tonemap_vulkan` + Pelorus chain | 0.527 | 1.133 | 28.403 | 1576 | 3.679 | 596 |
| DV5 (`d7ef48d2a9f3`) | `dovi_reshape` + `tonemap_vulkan` standalone | 0.589 | 1.481 | 1.916 | 111 | 1.466 | 231 |
| DV5 (`d7ef48d2a9f3`) | `dovi_reshape` + `tonemap_vulkan` + Pelorus chain | 0.626 | 1.517 | 3.020 | 263 | 1.567 | 230 |

(1) Another process on the GPU grew from 386 MiB to 14566 MiB during this call, so its VRAM figure is not reported; the other three Vulkan rows ran with every other GPU process unchanged. Frames scored: HDR10 10, DV5 4 (every 30th frame). The largest single-frame CIEDE2000 values are 32.8 (HDR10) and 7.9 (DV5); 1.7 % and 0.6 % of pixels exceed 2.3.

## Zero-copy verification & planted negative

### Zero-copy check

Each recipe encodes 60 frames and writes the filter graph with `-print_graphs_file <file> -print_graphs_format json`. A checker walks the chain from the graph input to the sink and fails when:

1. a `hwdownload` filter is present, or
2. any link after the first hardware-format link (`vulkan` or `cuda`) carries a software format, or
3. no link carries a hardware format.

For `hevc_nvenc`, the hop is `hwupload=derive_device=cuda` on a Vulkan device derived from CUDA (`vulkan=vk@cu`, [ADR-0191](../adr/0191-nvenc-hop-derived-vulkan-device.md)).

A `-v verbose` log grep is not used: it cannot tell a CPU-side `auto_scale` before `hwupload` (FFmpeg inserts one for the HDR10 decoder output, `yuv420p10le` to `p010le`) from a GPU-memory round trip, and the log does not name `hwdownload` at all.

Result: all four recipes pass. Every link from `hwupload` to the sink is `vulkan | p010le` or `vulkan | nv12`, and for `hevc_nvenc` the last hop is `cuda | nv12`. Example (DV5 into `hevc_nvenc`):

```text
buffer[yuv420p10le] -> dovi_reshape[gbrpf32le] -> scale[p010le] -> format[p010le]
  -> hwupload[vulkan | p010le] -> tonemap_vulkan[vulkan | nv12] -> pelorus_analyze_vulkan[vulkan | nv12]
  -> pelorus_grain_estimate_vulkan[vulkan | nv12] -> pelorus_mc_vulkan[vulkan | nv12]
  -> pelorus_denoise_vulkan[vulkan | nv12] -> pelorus_deband_vulkan[vulkan | nv12]
  -> hwupload[cuda | nv12] -> format[cuda | nv12]
```

### Planted negative case

To prove the check can fail, a planted chain inserts a host-memory round trip after `tonemap_vulkan`:

```text
hwdownload,format=nv12,hwupload
```

The checker must refuse that graph. Result: refused (exit 1), naming the `hwdownload` filter and the two software-format links after it; FFmpeg itself completed the encode (exit 0), so the refusal comes from the check, not from a failed run.

## Scope limits & caveats

- **Dolby Vision reshape execution:** Because no GLSL shader implementation exists for Dolby Vision profile 5 reshape, reshaping is performed by the CPU filter `dovi_reshape` before pixel upload via `hwupload`.
- **Hardware-accelerated decode:** HDR10 inputs can execute fully in VRAM when paired with Vulkan decode (`-hwaccel vulkan`), eliminating the upload stage. This configuration was not included in the benchmark matrix and remains unmeasured here.
- **Raw evidence logs:** Raw JSON and log artifacts (`parity-hdr10.json`, `parity-dv5.json`, `pelorus-chain-*.json`, `zc-*.json`, `zc-*.check`, `vram-guard.txt`) are retained locally in `.workingdir/evidence/`.
