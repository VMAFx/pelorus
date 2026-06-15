<!-- markdownlint-disable MD013 -->
# Pelorus

[![CI](https://github.com/vmafx/pelorus/actions/workflows/ci.yml/badge.svg)](https://github.com/vmafx/pelorus/actions)
[![License: BSD-2-Clause-Patent](https://img.shields.io/badge/License-BSD--2--Clause--Patent-blue.svg)](LICENSE)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/vmafx/pelorus/badge)](https://securityscorecards.dev/viewer/?uri=github.com/vmafx/pelorus)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ff69b4.svg)](https://github.com/sponsors/lusoris)
[![ko-fi](https://img.shields.io/badge/ko--fi-support-FF5E5B.svg)](https://ko-fi.com/lusoris)

**Turn the GPU into an intelligent pre-processor so a hardware encoder rivals a
CPU encoder.** Pelorus runs Vulkan compute filters on frames *while they stay in
VRAM*, fixing the psychovisual flaws a fixed-function encoder (NVENC/AMF/QSV)
can't — dark-scene banding, noise-tax, missing film grain — before the encoder
ever sees the pixels. The result is most of the BD-rate win of a slow x265 /
SVT-AV1 encode at hardware speed and power.

**Codec-agnostic.** The deband, denoise, and motion-hint filters are pure
pre-processing — they improve **HEVC** (`hevc_nvenc` / `hevc_qsv` / `hevc_vaapi`
/ `hevc_amf`, rivaling x265) exactly as much as AV1 (`av1_nvenc`, …). Only
film-grain synthesis is codec-specific, and Pelorus covers both: AV1 (AOM) and
HEVC/H.265 + VVC/H.266 (H.274 / SEI FGC). See
[docs/principles.md](docs/principles.md) for the engineering contract.

Pelorus is the GPU sibling of [**vmafx**](https://github.com/VMAFx/vmafx) (the
VMAF fork), under the same `vmafx` org. vmafx is the **quality oracle**; Pelorus
is the **Vulkan home** vmafx is not. They're bidirectionally wired through a
shared side-data ABI and vmafx's VMAF-in-the-loop autotune — see
[docs/architecture/overview.md](docs/architecture/overview.md).

## Quick start

```bash
# build + install the shared core (the FFmpeg filters link it)
meson setup build && ninja -C build && ninja -C build install

# apply the FFmpeg patch stack onto a pristine n8.1.1 checkout
cd ffmpeg-patches && ./generate.sh && ./test/build-and-run.sh

# zero-copy: decode -> smart deband (VRAM) -> hardware encode
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_deband_vulkan=range=15:dither=bluenoise:dynamic=1" \
       -c:v hevc_nvenc -cq 28 out.mkv      # or: av1_nvenc / hevc_qsv / hevc_vaapi
```

## Principles

| # | Rule |
|---|---|
| 1 | NASA/JPL Power of 10 (C, original) — bounded loops, checked returns, page-size functions |
| 2 | SEI CERT C mandatory; MISRA C:2012 informative — cite rule IDs in review |
| 3 | K&R, 4-space, 100 columns (`.clang-format`, identical to vmafx) |
| 4 | C4 + ADRs for every non-trivial decision (`docs/adr/`) |
| 5 | Zero-copy Vulkan: frames never leave VRAM mid-pipeline; validation-clean |
| 6 | Append-only interop ABI; never reorder/remove a wire field |
| 7 | Per-surface docs + changelog fragment in the same PR as the code |

Every merged commit: **0 warnings · clang-tidy clean · `meson test --suite=fast`
green · the deband shader compiles.**

## Modules

### Core — `libpelorus/`

| Module | Purpose | Key dep |
|---|---|---|
| `include/pelorus/interop.h` + `src/interop.c` | The Pelorus⇄vmafx side-data ABI (pack/parse, append-only, vendored by vmafx) | — |
| `include/pelorus/deband.h` + `src/deband_params.c` | Smart-deband parameter contract (shared by filter + autotune) | — |
| `shaders/pelorus_deband.comp` | Standalone reference deband shader (f3kdb) | glslang |
| `test/interop_test.c` | Shared ABI conformance fixture | — |

### FFmpeg filters — `ffmpeg-patches/`

| Filter | Purpose | Status |
|---|---|---|
| `pelorus_deband_vulkan` | Smart deband (f3kdb): flatten banding + TPDF/blue-noise dither, detail-protected, zero-copy | **Working** |
| `pelorus_denoise_vulkan` | Edge-preserving spatio-temporal denoise (the biggest BD-rate lever) | Roadmap |
| `pelorus_analyze_vulkan` | Measured banding/variance/edge stats → interop side data (GPU reduction + readback) | **Working** |
| `pelorus_grain_estimate_vulkan` | Film-grain param estimation (GPU per-band HF-residual) → PEL_SEC_FILMGRAIN + native AV1 side data | **Estimator built** |
| `pelorus_mc_vulkan` | Block-matching motion estimator → per-block MV-hint side data for the encoder (speed, not quality) | **Working** |
| `pelorus_fgs` (BSF) | Inserts the H.274 FGC SEI into HEVC so a decoder re-synthesizes grain — the HEVC leg of the FGS round-trip (AV1 round-trips via native side data) | **Working (static model)** |

## Landed so far

- [x] Step 1 — Core: `libpelorus` interop ABI + deband param contract + tests.
- [x] Step 2 — Flagship: `vf_pelorus_deband_vulkan` smart deband (Vulkan), inline
      GLSL, side-data emission, patch stack against n8.1.1.
- [x] Step 3 — `vf_pelorus_analyze_vulkan`: measured banding/variance/edge stats
      (GPU reduction + readback) → interop side data.
- [x] Step 4 — Temporal denoise (`vf_pelorus_denoise_vulkan`).
- [x] Step 5 — FGS param estimation (`vf_pelorus_grain_estimate_vulkan`): GPU
      per-band HF-residual estimate → PEL_SEC_FILMGRAIN + native AV1 side data.
- [x] Step 6 — Motion-vector hints: `vf_pelorus_mc_vulkan` producer (0007 →
      `PEL_SEC_MOTION`) + the **NVENC ME-hint consumer** (`-pelorus_me_hints`,
      patch 0008): the MV field seeds NVENC's external motion search (encode
      SPEED; on-HW A/B pending). ADR-0116 / 0114 Tier 3.
- [x] Step 7 — FGS round-trip: `pelorus_fgs` BSF (patch 0010) inserts the H.274
      FGC SEI into HEVC; `av1_nvenc` carries the estimate into NVENC's hardware
      AV1 film-grain (`NV_ENC_FILM_GRAIN_PARAMS_AV1`, `-pelorus_film_grain`,
      patch 0011); AV1 software encoders round-trip via native side data. Static
      AVOption-driven HEVC model; per-frame + H.264/VVC legs are follow-ups
      (ADR-0117 / ADR-0118).
- Encoder steering (ADR-0114, opt-in `-pelorus_roi 1`): the `analyze roi=1`
  banding map drives dense per-block delta-QP on **NVENC** (`qpDeltaMap`, proven
  −41% banding), **QSV** (`mfxExtMBQP`, code-complete; on-Intel-HW proof pending),
  and the native **Vulkan-Video** encoders via `VK_KHR_video_encode_quantization_map`
  (Tier 2, compile-verified; on-HW proof blocked by the dev box's driver
  feedback-flag gap). The same map also steers **SVT-AV1** (`libsvtav1`) via its
  per-superblock ROI segment map (`SvtAv1RoiMapEvt`, ADR-0121, proven on hardware:
  CAMBI −1.5% at CRF 35, an honest modest gain on a mild synthetic source).
  Patches 0004 / 0005 / 0009 / 0012.
- Closed-loop QP feedback (ADR-0114 step 6 / ADR-0119): new append-only interop
  section `PEL_SEC_QPREPORT` (ABI 1.1) carries the encoder's *honored* per-block
  QP/bit decisions back into the side-data blob, plus a vendor-neutral reader stub
  (`pel_qp_report_from_blocks`). The ABI + pack/parse + conformance test + stub are
  working; the libavcodec QSV stat-extraction wiring is the documented follow-up.

## Tooling

```bash
meson setup build && ninja -C build      # build (libpelorus + tests + shaders)
meson test -C build --suite=fast         # pre-push gate
ninja -C build install                   # install libpelorus (for the patches)
cd ffmpeg-patches && ./generate.sh        # regenerate the FFmpeg patch stack
clang-format --dry-run -Werror libpelorus/**/*.{c,h}   # format check
```

## Status

Pre-alpha (`v0.1.0`). Public API and the interop ABI may evolve before
`v1.0.0`; the ABI is append-only from here.

## License

[BSD-2-Clause-Patent](LICENSE) for `libpelorus` (so the interop TU is
co-vendorable with vmafx); FFmpeg's LGPL-2.1 for filters that become part of
FFmpeg when applied.

## Support

If Pelorus is useful to you: [GitHub Sponsors](https://github.com/sponsors/lusoris)
· [Ko-fi](https://ko-fi.com/lusoris).
