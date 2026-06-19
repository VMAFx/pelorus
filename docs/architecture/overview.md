<!-- markdownlint-disable MD013 -->
# Architecture overview

Pelorus is a **zero-copy GPU pre-encode pipeline**. Frames stay in VRAM from
decode, through one or more Vulkan compute filters, to a hardware encoder — the
moment a frame drops to system RAM, PCIe bandwidth destroys throughput, so the
whole design is built to avoid it.

It is **codec-agnostic**: deband, denoise, and motion-hint stages pre-process
pixels and help any hardware encoder — HEVC (`hevc_nvenc`/`hevc_qsv`/
`hevc_vaapi`/`hevc_amf`, rivaling x265) and AV1 (`av1_nvenc`, …) alike. Only
film-grain synthesis is codec-specific, and Pelorus covers both AV1 (AOM) and
HEVC/VVC (H.274 / SEI FGC).

## Context (C4 level 1)

```
            ┌────────────────────────────────────────────────────────┐
   source → │  FFmpeg filtergraph (frames in AV_PIX_FMT_VULKAN, VRAM)  │ → HW encoder → mux
            │   vf_pelorus_analyze → _deband → _denoise → _grain → _mc  │   (NVENC/AMF/QSV)
            └───────────────┬───────────────────────────┬─────────────┘
                            │ writes PelorusSideData      │ reads side data
                            ▼ (UUID-keyed AVFrame blob)    ▼
                    ┌───────────────┐            ┌──────────────────┐
                    │   libpelorus  │◄───────────│  vmafx (oracle)  │
                    │  interop ABI  │  vendored  │  vf_libvmaf*,     │
                    │  + contracts  │  by both   │  server, MCP,    │
                    └───────────────┘            │  vmaf-tune       │
                                                 └──────────────────┘
```

## Components

- **libpelorus** — the shared core: the side-data interop ABI
  ([interop.h](../api/interop-abi.md)) plus filter parameter contracts
  ([deband.h](../metrics/deband.md)). Both Pelorus and vmafx link/vendor it.
- **vf_pelorus_\* filters** (`ffmpeg-patches/`) — Vulkan compute stages. Each is
  frame-in/frame-out, runs a compute shader, and (optionally) annotates the
  frame with a Pelorus side-data section.
- **vmafx** — the quality oracle (separate repo). Reads Pelorus side data for
  perceptually-weighted scoring; drives VMAF-in-the-loop autotune.

## The two seams (bidirectional interop)

1. **Data plane** ([ADR-0103](../adr/0103-interop-sidedata-abi.md)): a versioned,
   UUID-keyed `AVFrame` side-data blob both `vf_pelorus_*` and vmafx's
   `vf_libvmaf*` read/write in one filtergraph, zero-copy. Pelorus is the sole
   writer; the ABI is append-only.
2. **Control plane** ([ADR-0106](../adr/0106-autotune-control-plane.md)): Pelorus
   exposes filter strengths as `AVOption`s and the banding/variance maps as
   perceptual-weighting input; vmafx's `libvmaf_tune` / `vmafx-server` / MCP run
   the search and hand back recommended strengths + CRF.

## Pipeline stages (roadmap)

| Stage | Filter | Effect | Status |
|---|---|---|---|
| analyze | `pelorus_analyze_vulkan` | measure banding/variance/edge stats → side data | **working** |
| deband | `pelorus_deband_vulkan` | flatten contours + dither | **working** |
| denoise | `pelorus_denoise_vulkan` | edge-preserving spatio-temporal denoise (biggest BD-rate lever) | **working** |
| grain | `pelorus_grain_estimate_vulkan` | estimate film-grain params → PEL_SEC_FILMGRAIN + native AV1 side data; HEVC via `pelorus_fgs` BSF (ADR-0117); `av1_nvenc` via `-pelorus_film_grain` (ADR-0118); static H.274 model, per-frame/H.264/VVC legs deferred | estimator working; HEVC BSF working (static); av1_nvenc HW grain wired |
| mc | `pelorus_mc_vulkan` | block-matching motion estimator → per-block MV-hint side data (`PEL_SEC_MOTION`); encode-speed; NVENC ME-hint consumer shipped (`-pelorus_me_hints`, patch 0008); measured on 4090: no speed gain (~2–3% slowdown at p7 — see bench-results.md v0.9) | **working** |

See [docs/backends/vulkan.md](../backends/vulkan.md) for the compute-filter
authoring model and [docs/usage/ffmpeg.md](../usage/ffmpeg.md) for the end-to-end
command form.
