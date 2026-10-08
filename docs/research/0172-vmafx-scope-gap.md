<!-- markdownlint-disable MD013 MD024 MD033 MD060 -->

# Research 0172: vmafx scope against Pelorus

Evidence behind the vmafx-coupled items of [ADR-0172](../adr/0172-roadmap-milestone-map.md): everything vmafx covers (backends, codecs, encoders, platforms, Rust migration) set against what Pelorus covers today, as gap ids `G#` (written `VS G#` in roadmap issues).

**Sources.** vmafx `origin/master` fetched 2026-10-08: `docs/roadmap.md`, `docs/backends/index.md`, `docs/usage/vmaf-tune-codec-adapters.md`, `docs/state.md`, `docs/api/pelorus-interop.md`, ADR-0726, 0401, 1113, 1116, 1118, 1120, 1276, 1685, 1829, 1852, 1880, 2001, 2073, 2093, 2342, 2343, 2478 and research 2479. `gh issue list -R VMAFx/vmafx --state all`: 256 issues (224 open, 32 closed), 42 epics (the sub-issue API returned no children, so children are the `#` references in each epic body). Pelorus issues #80 to #87 were read for cross-links. `(U)` marks a claim not confirmed in a primary source.

**Milestone names.** The sections below use the working names 0.4, 0.5, 0.6 and 1.0 used at survey time. The adopted map is in [the roadmap](../roadmap.md); the tables there are authoritative where they differ.

## 0. Counts

| milestone (vmafx) | issues |
| --- | --- |
| 1.0.0 First release | 115 |
| 1.1 Integrations & live quality | 54 |
| 1.2 Encoder feedback, embedding & platforms | 19 |
| 1.3 New metrics | 12 |
| 1.4 Metric A/B, best mix & more data | 10 |
| 1.5 Next model generation | 2 |
| 2.0 Breaking changes | 7 |
| 2.1 / 2.2 / 2.3 Rust P1a/P1b/P2 | 1 / 1 / 1 |
| 2.4 Rust P3 | 2 |
| 2.5 Rust P4+P5 | 2 |
| 3.0 Host C/C++ removed | 2 |
| Ongoing: Code health & dedup | 13 |
| Ongoing: Models & benchmarks | 4 |
| none | 11 |

Top labels: type:feature 152, enhancement 52, area:core 47, epic 42, area:tools 35, rc4 33, area:cloud 28, area:docs 20, area:ai 19, area:gpu 16, release-blocker 16.
Release map (docs/roadmap.md, ADR-2001 2026-10-06 = last milestone-map change until 2.0; ADR-2342 amends): 1.0.0 = RC1..RC9 then final; v1.0.0-rc.1 published 2026-09-27.

## 2a. Compute / GPU / CPU backends

| backend | status | milestone | refs |
| --- | --- | --- | --- |
| CPU scalar C | shipped, reference | - | docs/backends/index.md |
| x86-64 AVX2 / AVX-512 | shipped | - | docs/backends/x86/avx512.md |
| x86-64 full ladder SSE2/SSSE3/SSE4.1/AVX/AVX2/AVX-512/AVX-512 ICL/AVX10 | planned, bit-exact, runtime dispatch | 1.0.0 RC7 | #1885, ADR-2001 |
| AArch64 NEON | shipped (scalar-bit contract for contracted features) | - | docs/backends/arm/overview.md |
| AArch64 dotprod/i8mm, SVE, SVE2 | planned | 1.0.0 RC7; SVE/SVE2 scalar-Rust fallback until stable core::arch (ADR-2478 Q-235) | #1885 |
| RISC-V RVV 1.0 | planned (qemu-user CI) | 1.0.0 RC7; scalar Rust fallback | #1885, ADR-2478 |
| POWER VSX | planned | 1.0.0 RC7 | #1885 |
| LoongArch LSX/LASX | planned | 1.0.0 RC7 | #1885 |
| WebAssembly SIMD128 (wasm32, Emscripten, npm) | planned; ADR-0401 still Proposed | 1.2 | #2248, #2336 browser demo |
| Arm servers exactness/throughput | planned | 1.2 | #2156 |
| CUDA (13.4.2) | shipped; 24/25 gate features exact, ciede libm bound 1e-9 | - | docs/backends/cuda |
| CUDA legacy sm_50-sm_72 via CUDA 12.x | planned | 1.0.0 RC6 | ADR-2001 |
| CUDA Linux aarch64 | planned | 1.2 | #2164 |
| SYCL / oneAPI DPC++ 2026.1 + Level Zero 1.34 (Intel Arc/Xe/Battlemage) | shipped; 24/25 exact; Windows doc exists | - | docs/backends/sycl |
| SYCL legacy Intel Gen9-Gen11 iGPU (legacy compute runtime) | planned | 1.0.0 RC6 | ADR-2001 |
| HIP / ROCm (19 extractors) | shipped; 24/25 exact | - | docs/backends/hip |
| HIP: every AMD target pinned ROCm still emits | planned | 1.0.0 RC6 | ADR-2001 |
| Metal (Apple Silicon, 17 kernels) | shipped, no exactness declared yet; SpEED twins missing | speed_chroma/speed_temporal twins 1.0.0 RC5 | #2160, #1239 closed, #1721 |
| iOS/iPadOS NEON + Metal, XCFramework, Swift | planned | 1.2 | #2246 |
| Vulkan compute | REJECTED/REMOVED (ADR-0726); RC5 small SPIR-V bit-exactness experiment; backend return decided 1.2 with Android | RC5 verdict, 1.2 decision | #2466 (Q-011), #2517, #2247, #2578 |
| Vulkan frame import (host-side, hwframes into FFmpeg/GStreamer filters) | planned | 1.0.0 RC4 | #2470 WP3, #2346 draft, roadmap RC4 |
| OpenGL texture import (OBS) | planned | 1.0.0 RC4 | #2238, #2470 |
| D3D11 import (SYCL path, OBS) | planned | 1.0.0 RC4 | #2067 table, #2238 |
| OpenCL | only as study candidate for Android | 1.2 | #2517 |
| D3D12 compute / DirectML | not planned as backend; DirectML only as ONNX Runtime EP | 1.1 | #2414 |
| TensorRT EP (ONNX) | planned | 1.1 | #2414 |
| WebGPU | none (CubeCL/wgpu only cited as Rust-Metal candidate, ADR-2478) | - | - |
| NPU | none (OpenVINO/TensorRT named only in AI-surface context) | - | #2414, ADR-0022 |
| Android GPU compute | study first | 1.2 | #2517 |
| Android CPU (NDK, AAR, Kotlin) | planned | 1.2 | #2247 |
| libgpudispatch (shared GPU dispatch layer, GPU pool arbiter) | planned; extract in C RC5, rewrite in Rust P4 behind same C ABI | 1.0.0 RC5; 2.5 | #1455 (names pelorus as third fork), #1253, #2577, ADR-2478 |
| GPU capability table (per-vendor, format envelope up to 16K, 8-16 bit) | planned | 1.0.0 RC6 | #1725, ADR-1880 |
| CPU capability table | planned | 1.0.0 RC7 | #1885 |
| Exact-twin contract | CPU bits == twin bits at --precision max, or measured libm bound in an ADR | RC3 | docs/backends/index.md, #1721 |

Twin-exactness data (docs/backends/index.md): 25 gate features; 90 twins exact, 3 libm-bound, 7 not declared (all Metal).

## 2b. Codecs / bitstreams / colour

Scoring is pixel-domain after FFmpeg decode: codec-agnostic. Codec-specific scope:

| item | status | milestone | refs |
| --- | --- | --- | --- |
| H.264 | bitstream facts (type/size/QP) first-class; vmaf-tune adapters x264 + NVENC/QSV/AMF/VideoToolbox | 1.1 | #2271, adapters doc |
| HEVC | same; plus in-loop VMAF predictor from x265 stream features (x265-pVMAF) | 1.1 facts; 1.2 predictor | #2271, #2516, #2416 |
| AV1 | facts "where FFmpeg exposes"; adapters libaom, svtav1 (+svt-av1-hdr variant), NVENC/QSV/AMF/VideoToolbox(placeholder) | 1.1 | #2271 |
| VP9 | facts where FFmpeg exposes; libvpx adapter | 1.1 | #2271 |
| VVC | libvvenc adapter (qp 17..50); XPSNR requested because of VVenC perceptual QP adaptation; LCEVC base codec | XPSNR 1.0.0 | #2158, #2251 |
| LCEVC (MPEG-5 Part 2) | layer-aware scoring; BLOCKED on VMAFx/pelorus#87 (enhancement-layer metadata) + #2147 + #2142; optional build, off in published images (royalty review) | 1.1 wave 3 | #2251, pelorus#87 |
| AV2/AVM | none | - | - |
| EVC, APV, JPEG XS, JPEG XL (codec), ProRes (scoring) | none in issues. ProRes only as encoder adapter prores_videotoolbox. JPEG XL appears only as a butteraugli-family context | - | #2165 |
| Neural/generative codecs | research watch only | ongoing | #2168 |
| Film grain (AV1 AOM / H.274) | NOT in vmafx scope: 0 issues, 0 ADRs (docs mention only svt-av1-hdr variant and the interop doc). Pelorus owns grain; vmafx would score grain-synthesised output as ordinary pixels | - | git grep |
| HDR PQ/HLG | vmaf-tune HDR-aware flags (ADR-0300, ffprobe detect), PQ-native HDR (ADR-1685), per-input colorimetry + conversion_target (ADR-2093), HDR-tag warning (RC8), dE-ITP/PU21/HDR-SSIM/HDR-MS-SSIM, HDR conversion checks | 1.0.0 | #2157, #2145, #2161, #1247, ADR-1110/1111 |
| Dolby Vision | passthrough only; never reads/applies RPU; "not in scope" | out | #2067 |
| HDR10+ | none | - | - |
| Bit depth | 8/10/12/16 now; 9-15 odd depths and RGB-with-matrix in RC4 WP13; 8K/16K overflow audit with 16-bit samples | 1.0.0 RC3/RC4/RC6 | #2476, ADR-1880 |
| Chroma formats | 4:0:0/4:2:0 core; 4:2:2/4:4:4 import and twins | 1.0.0 | #2470 |
| Interlaced | field-aware scoring + guard | 1.0.0 RC5 | #2361 |
| Container input (MXF, IMF, MPEG-TS, MP4) | via optional FFmpeg-library input | 1.0.0 RC5 | #2363 |
| Bitstream-facts track | owns stream-metadata library; field names to match pelorus#86; consumers #2280 #2417 #2416 #2270 | 1.1 | #2271, #2510 (ADR), #2311 |
| Session/QoE models | P.1203 native (patent review first), P.1204.1/.2 monitor | 1.1 | #2280, #2417, #2509, #2270 |

## 2c. Encoders / decoders

vmaf-tune registered adapters (19, docs/usage/vmaf-tune-codec-adapters.md, Python tree to be replaced by Go pkg/codecadapter in RC5):
libx264, libx265, libaom-av1, libsvtav1, libvpx-vp9, libvvenc, h264_nvenc, hevc_nvenc, av1_nvenc, h264_qsv, hevc_qsv, av1_qsv, h264_amf, hevc_amf, av1_amf, h264_videotoolbox, hevc_videotoolbox, prores_videotoolbox, av1_videotoolbox (placeholder: validate() raises until FFmpeg exposes it).

| encoder / path | vmafx status | milestone | refs |
| --- | --- | --- | --- |
| x264, x265, libaom, SVT-AV1, libvpx-vp9, VVenC | shipped adapters (CRF/preset/two-pass) | - | adapters doc |
| NVENC, QSV (oneVPL), AMF, VideoToolbox | shipped adapters | - | ADR-0290 0281 0282 0283 |
| VAAPI, Vulkan video, MediaCodec, D3D12 video, V4L2 M2M encode | no adapter, no issue (V4L2 only as virtual camera capture) | - | git grep 0 hits |
| rav1e, uvg266, SVT-HEVC, Kvazaar | none (rav1e only referenced via Av1an in #2326) | - | - |
| Non-FFmpeg encoder adapter interface (commercial SDKs) | planned, Go pkg/codecadapter; depends on #2162 external-tool contract | 1.1 | #2147 |
| HandBrakeCLI driver + carried HandBrake patch set (HandBrake builds FFmpeg 9.0.2, 22 patches, filters on CUDA/QSV/VideoToolbox/AMF frames) | planned, XL | 1.0.0 RC5 | #2407 |
| SVT Encore VMAF-profile options on libvmaf.so.3 | planned conformance | 1.0.0 RC4 | #2364 |
| Encoder-feedback API + reconstructed-frame input (NVENC SDK 12.1+ exposes recon NV12/10-bit; first target one SDK, then others) | planned, L | 1.2 | #2514 (epic #2067) |
| Encoder-side predictors VQM4HAS, in-loop HEVC predictor | planned from papers | 1.2 | #2416, #2516 |
| ROI / saliency steering in vmaf-tune | 5 encoders only: libx264, libaom-av1, libx265, libsvtav1, libvvenc; all others "no ROI support in either tree" | - | #1249 |
| vmaf-tune prefilter (Pelorus deband + CRF joint Optuna search, emits -vf pelorus_deband_vulkan=...) | shipped (Python); Go port pkg/prefilter exists | - | ADR-1116, docs/usage/vmaf-tune-prefilter.md |
| Decoders | FFmpeg decode; QSV/VA + DMA-BUF import into SYCL (luma only today, chroma RC4); VideoToolbox IOSurface into Metal; NVDEC->CUDA; LCEVC external decoder optional | RC4 | ADR-1121, ADR-0423, ADR-1679, ADR-1829 |

## 2d. Platforms, packaging, integrations

| item | status | milestone | refs |
| --- | --- | --- | --- |
| Linux x86-64 | shipped; older-glibc floor (rc.2 needs glibc >= 2.41) | 1.0.0 RC4 | #2437 |
| Linux arm64 | assets planned | 1.0.0 RC4 | #2437 |
| Windows (MSVC/MinGW UCRT64, SYCL on Windows, CUDA installer) | shipped; MinGW leg migrated to UCRT64 (closed) | - | #1609 |
| macOS arm64 | shipped (Metal); bundle attached since rc.3 | - | #2437 |
| iOS/iPadOS | planned | 1.2 | #2246 |
| Android | planned | 1.2 | #2247, #2517 |
| WASM/browser | planned | 1.2 | #2248, #2336 |
| Containers: cpu, cuda, sycl, rocm (rc1/rc2 ROCm withdrawn, ADR-1578), go-server, node; tester image | shipped (rc) | - | tools/rc1-tester |
| Helm chart, kind+kuttl, operator (VmafxJob CRD), controller/node split, GPU pool arbiter | planned | 1.0.0 RC4/RC5 | #1252 #1253 #2431 #2478-#2493 |
| Cloud-native platform: Postgres(CNPG)+River, Valkey/Dragonfly, S3/OCI artifacts, CloudEvents NATS/Kafka, KEDA, DRA templates, NFD labels, mTLS | planned | 1.0.0 RC4 | #2431, WP17.x |
| Observability: OTel/Prometheus, Grafana dashboards | planned | 1.0.0 (pkg), 1.1 (metrics export) | #2430, #2327 |
| Distribution manifest; Homebrew tap, conda-forge, winget, deb/rpm repos | planned | 1.0.0 | #2314, #2319 |
| Python wheels (pip), Rust crates on crates.io, Go module path, npm (wasm) | planned | 1.0.0 (Python, crates), 1.2 (npm) | #2318, #2320, #2434, #2248 |
| Bindings generated from VMAFx API: Rust, Go, Python (RC4); Swift (1.2), Kotlin (1.2), JS/TS (1.2) | planned | RC4 / 1.2 | #2434, #2246, #2247, #2248 |
| Client libraries from OpenAPI: Python, TypeScript, Go | planned | 1.1 | #2321 |
| FFmpeg: vmafx / vmafx_tune / vmafx_pre filters; series renumbered from 0001; no vmaf-named filter left | planned (breaking for filter users) | 1.0.0 RC4 WP9/WP10/WP15 | #2475, #2435, #2477 |
| GStreamer native vmafx element + CI conformance of upstream vmaf element | planned | 1.0.0 RC4 | #2236, #2237 |
| OBS-ready API (RC4) -> OBS Studio plugin | planned | 1.0.0 / 1.1 | #2238, #2239 |
| VapourSynth plugin (Av1an/ab-av1 consumers) | planned | 1.1 | #2326 |
| libVLC sample (1.1), VLC plugin (1.2) | planned | 1.1 / 1.2 | #2357, #2358 |
| WebRTC analyzer, SRT/RTMP/NDI/virtual-camera recipes | planned | 1.1 | #2355, #2356 |
| HandBrake | see 2c | RC5 | #2407 |
| MCP server (Go; Python MCP deleted), REST/gRPC scoring API contract, server mode | planned/partial | 1.0.0 | #2155 #1251 #1240 |
| CI: GitHub Action + GitLab/Jenkins templates around score gate | planned | 1.1 | #2322, #2274 |
| ComfyUI node / Diffusers callback, AIGC scorer package | planned | 1.1 | #2418 #2524 #2525 |
| Out-of-tree plugin build/signing plan (OBS, VapourSynth, VLC, WebRTC) | planned | 1.1 | #2512 |
| Supply chain: SLSA L3 reusable build+provenance workflow, SBOM, CRA mapping, signed score provenance (Sigstore/C2PA) | planned | 1.0.0 / 1.1 | #2465, #2146, #2159, ADR-2073 |
| Licence | EUPL-1.2 + BSD-2-Clause-Patent; no commercial licence | decided 2026-10-05 | #2067 |

## 2e. Rust migration (ADR-2478, epic #2567, Research-2479)

| phase | milestone | issue | content |
| --- | --- | --- | --- |
| P0a | 1.0.0 (RC4) | #2568 | pin stable toolchain (1.98.1 on 2026-10-08), cargo in dev container |
| P0b | 1.2 | #2569 | Rust emitter in API generator (core/api/vmafx.toml) |
| P0c | 1.2 | #2570 | sanitizer/fuzz/coverage lanes for Rust crates |
| P0d | 1.2 | #2571 | register Rust-only extractors; from 1.3 new host code is Rust-first |
| P0e | 1.2 | #2572 | hashed per-frame oracle fixtures from C path |
| P1a | 2.1 | #2573 | adm, motion, cambi, speed_chroma Rust-default with SIMD |
| P1b | 2.2 | #2574 | remaining CPU extractors + SIMD (std::arch AVX2/AVX-512/NEON); new-ISA kernels Rust only |
| P2 | 2.3 | #2575 | engine, model loading, predict, pooling, generated ABI shells; C shim removed |
| P3 | 2.4 | #2576, #2581 | CLI, tools, MCP server, ONNX host in Rust; host glue in Rust where host allows (GStreamer yes; FFmpeg filters, VLC, OBS only if a supported route exists) |
| P4 | 2.5 | #2577 | GPU host runtimes + libgpudispatch rewritten in Rust behind same C ABI |
| P5 | 2.5 | #2578 | per-backend GPU kernel verdict (input: #2466); exception list |
| P6 | 3.0 | #2579 | delete host C/C++ outside named exception list (file, rule, reason, trigger, expiry) |
| 2.0 | 2.0 | #2535, #2536 | libvmaf.h compatibility library removed (breaking-only release) |

C ABI (vmafx/*.h, libvmafx.so.1) stays; C is differential oracle until deleted. GPU kernels (.cu/.hip/.metal/SYCL device source) stay native on exception list (nvptx Tier 2 nightly, amdgcn Tier 3, no SYCL route, Metal none).

Implication for the Pelorus interop mirror (exactly 10 paths, scripts/ci/pelorus-mirror-paths.txt): headers core/include/libvmaf/pelorus/{deband,denoise,interop,pelorus}.h; sources core/src/interop/pelorus_{deband_params,denoise_params,interop,qp_report_csv,version}.c; fixture core/test/test_pelorus_interop.c. Facts:

- They are host-side C under core/ -> inside the "no host C by 3.0" rule unless exception-listed. Neither ADR-2478 nor Research-2479 mentions Pelorus or the mirror (git grep: only a file-count row "Compat, interop, arch glue core/src/{compat,interop,x86,arm}/ 21 files 2323 lines").
- Mirror must stay byte-identical (ADR-1113, ADR-1276, tidy/format/cppcheck exclusions); vmafx cannot port it to Rust in place without breaking the drift guard. Options for vmafx: (1) exception-list entry with expiry, (2) Pelorus ships a Rust crate / generated Rust bindings and vmafx drops the C mirror, (3) vmafx Rust reader + shared conformance vectors. Undecided in vmafx = OPEN QUESTION for Pelorus (affects ABI minor-bump workflow and fixture sharing).
- ADR-2478 Q-233: new host components from 1.3 on are Rust-first; Rust bindings generated from VMAFx API (RC4 WP7) do not cover the Pelorus ABI.
- Mirror pin on origin/master: scripts/sync-pelorus-interop.sh PELORUS_VENDOR_SHA=11e183ec0aedf6b3e6447fda64acbb6072a1ae60 (docs/api/pelorus-interop.md agrees). #1640 (closed) asked for 174938e; ADR-1276 text still says 93bef12 (v0.2.2). ABI stays 1.3.
- vmafx ADR-1113 rejects meson subproject/submodule because Pelorus pulls Vulkan/shader deps: any Pelorus-side Rust crate must stay dependency-free like libpelorus core.

## 2f. Every explicit Pelorus mention (vmafx origin/master)

Issues (bodies):

| ref | ask / fact |
| --- | --- |
| #1455 body:4,21,34,40,56 | libgpudispatch: "pelorus is the third copy" of the GPU dispatch layer (vf_pelorus_*_vulkan.c vs template); VMAFx/pelorus has exactly one open issue (dep dashboard) -> invisible; libgpudispatch to be consumed by vmafx, pelorus, template-native-gpu; migrate template and pelorus after the layer exists. ASK: Pelorus is a planned consumer. |
| #1640 (closed) | re-vendor mirror to pelorus 174938e (UTF-8 paths, owner-only fixture, ABI 1.3). Done. |
| #2271 body:7,19,22,31 | stream-metadata library: field names MUST match VMAFx/pelorus#86 schema where both define a field; acceptance test loads both. ASK: Pelorus#86 schema is normative for field names. |
| #2510 body:4 | same: stream-metadata library shares field names with pelorus#86. |
| #2251 body:7,20 | LCEVC: "Blocked by: #2147, #2142, VMAFx/pelorus#87". ASK: Pelorus delivers LCEVC enhancement-layer metadata. |
| #2138 body:9 | real-time GPU encode scoring: "pre-processing before the encoder (tone mapping, denoising) stays possible" (zero-copy chain must tolerate Pelorus filters ahead of encoder). |
| #2586 body:23 | T-PREFILTER-LIVE-ENCODE-UNTESTED: needs a pelorus-enabled ffmpeg (dev-mcp container/CI lane) to confirm deband+CRF recommendation. ASK: published Pelorus-enabled FFmpeg build. |
| #2067 body | VMAFx/pelorus#86 feeds items 5 and 6 (encoder feedback) per pelorus#86 text; score provenance #2142 and calibration #2143. |

Pelorus-side issues that reference vmafx (read): #86 (telemetry schema; "spec before vmafx 1.0.0 RC4 so the mirror carries it, adapters in 1.2"), #87 (LCEVC), #84 (recon handoff), #83 (quality-window zone emitter), #82 (grain interchange), #81 (encode provenance record), #80 (ROI/delta-QP), #85 (bit-exact filter mode).

Docs/ADRs/code:

| ref | content |
| --- | --- |
| docs/adr/1113 | vendored read-only append-only mirror; every ABI minor bump needs manual re-sync (bump PELORUS_VENDOR_SHA); Pelorus pulls Vulkan so no subproject. |
| docs/adr/1116 + pkg/prefilter/knobs.go + tools/vmaf-tune/.../filter_adapters/pelorus_deband.py | vmaf-tune prefilter hard-codes the 10 frozen knobs of Pelorus ADR-0110 for ONLY pelorus_deband_vulkan (FilterName const). Conformance test fails on knob drift. D3 (MCP autotune_prefilter) and D4 (/v1/autotune-prefilter) planned on it. ASK: knob contract frozen; FilterAdapter protocol ready for further filters (denoise, grain, dehalo, aa, deblock, borderfix not yet adapted). |
| docs/adr/1118 + docs/api/perceptual-weight.md + ffmpeg-patches/0017-libvmaf-read-pelorus-sidedata.patch | opt-in perceptual pooling from PEL_SEC banding/variance maps; vf_pelorus_deband emits placeholder (grid_cols==0); real per-cell maps await vf_pelorus_analyze ("plan C"). ASK: analyze filter must emit per-cell maps. |
| docs/adr/1120 | consumes PEL_SEC_COMPLEXITY (1.3); PEL_SEC_QPREPORT, PEL_SEC_MOTION_CONF vendored but only complexity consumed in scoring. |
| docs/adr/1276, research/2072 | released-tag pinning rule: parser safety fixes are re-pin triggers; misaligned-base UB fix. |
| docs/adr/2073 (provenance record) :21,93,139 | VMAFx/pelorus#81 encode record digest is bound into the score provenance once defined. ASK: #81 canonical record. |
| docs/state.md:1848, T-PELORUS-MIRROR-SOURCE-DRIFT-2026-09-22 | HISS-21 splits (pel_blob_pack, pel_blob_find_section, pel_qp_report_from_blocks, pel_x265_csv_parse) "still want landing upstream" in Pelorus. ASK (complexity splits) - Pelorus HISS-04 compliance. |
| docs/state.md:875 | "upstream-owned Pelorus work" is an explicitly deferred class. |
| docs/rebase-notes.md (93 hits), docs/development/tidy-lanes.md:244,249, pre-commit-hooks.md:285-309,403 | mirror exempt from tidy/format/cppcheck/SPDX via exception entries (10 files) pending SPDX line upstream: "until the line exists in Pelorus and the mirror is re-vendored". ASK: SPDX header upstream. |
| docs/development/vmafx-node.md:85 | node reads pelorus sidedata (0017). |
| core/src/feature/perceptual_weight.c, core/test/test_perceptual_weight.c | consumer implementation. |
| scripts/sync-pelorus-interop.sh, scripts/ci/pelorus_mirror.py, scripts/ci/pelorus-mirror-paths.txt, scripts/ci/tests/test-sync-pelorus-interop.sh | drift guard; reads local pelorus checkout only when explicitly run. |
| CHANGELOG.md:431 | provenance digest of VMAFx/pelorus#81 encode record (ABI 0.1.5 of vmafx provenance). |
| ADR-0215 / docs/ai/models/fastdvdnet_pre.md | vmafx has its OWN ONNX FastDVDnet temporal pre-filter (feature extractor, not a Pelorus competitor but overlaps denoise concept). |
| ffmpeg-patches/ (22 patches, 0001-0022) | vmafx FFmpeg series; WP10 renumbers from 0001 and removes vmaf-named filters; Pelorus has its own 18-patch stack (also from 0001, pin n9.0.2). No vmafx text addresses coexistence/ordering of the two stacks in one FFmpeg tree, nor whether `vmafx` keeps reading PelorusSideData after rename. OPEN. |

## 3. Gap analysis (Pelorus today: Vulkan compute only; H.264/HEVC/AV1; NVENC/QSV/Vulkan/libaom/SVT-AV1 patches + stock ROI x264/x265; interop ABI 1.3)

Class: REQ = required-for-vmafx (cited); PAR = parity-nice-to-have; OOS = out-of-scope-for-Pelorus.
Milestone key: 0.4 telemetry schemas / 0.5 interchange & live integration (vmafx 1.1) / 0.6 conformance & encoder feedback (vmafx 1.2) / 1.0 stable contract / post-1.0 theme.

| # | area | item | vmafx status+milestone+refs | Pelorus today | Pelorus need | class | suggested Pelorus milestone | size |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | telemetry | Normalised encoder telemetry schema (per-frame/per-block) | vmafx #2271 must share field names with pelorus#86; 1.1; consumers #2280 #2417 #2416 #2270 | pelorus#86 open, PEL_SEC_QPREPORT + x265 CSV only | spec the schema (ABI 1.4) BEFORE vmafx RC4 so mirror carries it; adapters later | REQ (#2271, #2510, #2067) | 0.4 (spec) / 0.6 (adapters) | L |
| G2 | telemetry | LCEVC enhancement-layer metadata | vmafx #2251 blocked by pelorus#87; 1.1 wave 3 | pelorus#87 open | reader for FFmpeg side data / elementary stream -> schema records | REQ (#2251) | 0.5 | L |
| G3 | provenance | Encode provenance record (canonical hashable) | vmafx ADR-2073 binds pelorus#81 digest into score provenance; #2142 RC4, #2159 signed 1.1 | pelorus#81 open | canonical record + digest definition | REQ (ADR-2073) | 0.4 | M |
| G4 | interop | Interop mirror under Rust migration | ADR-2478: no host C by 3.0 (P6), mirror byte-identical; undecided | libpelorus C only, no crate | decide with vmafx: Rust crate / bindings / exception; keep dependency-free; extend shared conformance vectors | REQ-adjacent (ADR-1113 + ADR-2478 conflict) | 0.5 decision, 1.0 delivery | M |
| G5 | interop | ABI stability signal for mirror consumers | vmafx re-pins on each minor / released parser fixes (ADR-1276) | ABI 1.3 append-only, releases v0.2.x | stable-contract release policy (tag-only pins, changelog of ABI), 1.0 freeze | REQ (ADR-1113/1276) | 1.0 | S |
| G6 | FFmpeg | Coexistence of Pelorus patch stack with vmafx series (renumbered from 0001, vmafx/vmafx_tune/vmafx_pre, HandBrake carries vmafx set) | #2435 RC4, #2407 RC5, #2477 audit | 18-patch stack n9.0.2 from 0001 | documented apply order / non-overlap test with vmafx series; confirm `vmafx` filter keeps reading PelorusSideData (0017 successor) | REQ (ADR-1118 chain) | 0.5 | M |
| G7 | autotune | Prefilter adapters beyond deband (denoise, grain, dehalo, aa, deblock, borderfix, mc, scenecut) | vmafx FilterAdapter protocol ready (ADR-1116); only pelorus_deband hard-coded; Go pkg/prefilter | 10 filters, ADR-0110 control-plane contract for deband only | publish control-plane knob contracts per filter (frozen JSON) so vmafx adapters can be generated, not hand-coded | REQ-ish (ADR-1116 mode-2) / PAR | 0.5 | M |
| G8 | scoring | vf_pelorus_analyze real per-cell maps | ADR-1118 awaits "plan C" | analyze exists (10 filters); deband placeholder | confirm analyze emits grid maps in sidedata consumed by 0017 | REQ (ADR-1118) | 0.4 | S |
| G9 | encoder feedback | Quality-window zone emitter / metric feedback via encoder controls | vmafx #2514 encoder feedback API 1.2, #2138 windows RC4 | pelorus#83 open | emit zones/QP offsets from vmafx window scores via supported controls | REQ (#2514, #2067 item 6) | 0.6 | L |
| G10 | encoder feedback | Reconstructed-frame handoff + capability table | #2514 first target NVENC SDK 12.1+ recon NV12/10-bit; 1.2 | pelorus#84 open | per-encoder recon capability table, descriptor, recon-vs-decode conformance | REQ (#2514) | 0.6 | L |
| G11 | encoder steering | ROI/delta-QP intent normalisation + conformance harness | vmaf-tune ROI exists for libx264, libaom, libx265, libsvtav1, libvvenc only (#1249) | pelorus#80 open; patches NVENC/QSV/Vulkan/libaom/SVT-AV1 | normalised ROI intent so vmaf-tune recommend-saliency can reach hardware encoders | PAR -> REQ if vmafx wants ROI on HW encoders (#1249 notes none) | 0.6 | L |
| G12 | conformance | Bit-exact CPU reference + cross-device parity gate for filters | vmafx exact-twin culture (RC3, ADR-2343); libgpudispatch RC5 | pelorus#85 exploratory | CPU reference + parity gate per backend, once more than Vulkan exists | PAR (needed before any 2nd backend) | 0.6 | L |
| G13 | grain | Film grain interchange AV1/H.274 | vmafx: no scope (0 issues) | pelorus#82 open | none for vmafx; keep Pelorus-internal | OOS (vmafx) | 0.5 (Pelorus own) | M |
| G14 | backend | CUDA compute path for filters | vmafx CUDA shipped, 13.4.2, exact twins; libgpudispatch RC5 | Vulkan only | CUDA kernels or libgpudispatch consumption; Pelorus is a named planned consumer (#1455) | PAR (REQ only via #1455 dedup intent) | post-1.0 "multi-backend compute" | XL |
| G15 | backend | SYCL/oneAPI compute for filters | shipped; legacy Gen9-11 RC6 | none | same via libgpudispatch | PAR | post-1.0 multi-backend | XL |
| G16 | backend | HIP/ROCm compute | shipped | none | same | PAR | post-1.0 multi-backend | XL |
| G17 | backend | Metal compute (macOS/iOS) | shipped (17 kernels), iOS 1.2 | none (Vulkan via MoltenVK is (U)) | Metal path or MoltenVK evaluation | PAR | post-1.0 multi-backend | XL |
| G18 | backend | libgpudispatch adoption | #1455 RC5 extract in C, Rust P4 | Vulkan host code forked ("third copy") | adopt after vmafx extracts; avoid Vulkan-specific lock-in; note vmafx dropped Vulkan compute (ADR-0726) and only runs SPIR-V experiment #2466 | REQ-intent (#1455) | 1.0 evaluate / post-1.0 adopt | L |
| G19 | backend | Vulkan strategy alignment | vmafx: Vulkan backend removed; Vulkan frame import RC4; return decided 1.2 (#2517/#2466) | Vulkan is Pelorus's ONLY backend and Vulkan encode steering exists | none required; track #2466 verdict (SPIR-V bit-exactness across drivers) as evidence for pelorus#85 | PAR | 0.6 | S |
| G20 | backend | D3D12 / DirectML / OpenCL / WebGPU / NPU compute | vmafx: none (DirectML only ONNX EP #2414) | none | none | OOS | - | - |
| G21 | codec | VVC steering (VVenC ROI/QP; H.274 grain on VVC) | vmafx vvenc adapter + ROI; XPSNR #2158 for VVenC perceptual QP | Pelorus grain H.274 (HEVC/VVC) but no VVC encoder patch | VVenC ROI/telemetry adapter (VVenC log in pelorus#86 list) | PAR | 0.6 | M |
| G22 | codec | VP9 steering | vmafx libvpx adapter + facts #2271 | none | telemetry adapter (libvpx stats via QUALITY_STATS) | PAR | 0.6 | S |
| G23 | codec | AV2/AVM, EVC, APV, JPEG XS/XL | vmafx: none | none | none | OOS | post-1.0 watch | - |
| G24 | codec | LCEVC steering | vmafx #2251 consumer only | none | metadata only (G2) | REQ (metadata) / OOS (encode) | 0.5 | see G2 |
| G25 | encoder | AMF steering (H.264/HEVC/AV1) | vmafx adapter shipped; AMF BlockQpFeedback/PSNR/SSIM named in pelorus#86 | none (hardware-blocked per ADR-0119/0122) | AMF telemetry + ROI adapter | PAR | 0.6 | L |
| G26 | encoder | VAAPI, VideoToolbox, MediaCodec, D3D12 video, V4L2 steering | VideoToolbox adapter shipped in vmafx; others no adapter | none | VideoToolbox telemetry/ROI is the only one with a vmafx counterpart | PAR (VideoToolbox) / OOS (rest) | post-1.0 "hardware encoder breadth" | L |
| G27 | encoder | rav1e / uvg266 / other open encoders | vmafx: none | none | none | OOS | - | - |
| G28 | encoder | Non-FFmpeg encoder adapters (commercial SDK) | vmafx #2147 1.1 Go pkg/codecadapter | FFmpeg patches only | input struct for telemetry must not assume FFmpeg (already planned in pelorus#86) | PAR | 0.4 | S |
| G29 | HDR | PQ/HLG-correct filters (deband/denoise on PQ), dE-ITP/PU21 awareness; HDR-tag warning | vmafx PQ-native HDR, #2157, #2145, #2161 | filters 10-bit capable (U); no HDR semantic check in this survey | verify filters preserve colorimetry tags + side data; document transfer assumptions | PAR | 0.6 | M |
| G30 | HDR | Dolby Vision / HDR10+ | vmafx: DV passthrough only, HDR10+ none | none | passthrough guarantee only | OOS | - | S |
| G31 | formats | 4:2:2/4:4:4, 9-16 bit, interlaced, RGB-with-matrix | vmafx RC4 WP13, #2361 | 4:2:0 10-bit (U) | matrix of supported formats per filter in docs + refusal by name | PAR | 0.6 | M |
| G32 | integration | GStreamer element/plugin for Pelorus filters | vmafx native element RC4 (#2236) | FFmpeg only | optional GStreamer wrapper | PAR | post-1.0 "integration breadth" | L |
| G33 | integration | OBS / VLC / VapourSynth / HandBrake | vmafx plugins 1.1-1.2; HandBrake RC5 | none; HandBrake builds FFmpeg 9.0.2 with filter allowlist (could carry Pelorus patches) | HandBrake patch-set recipe is the only plausible one (shared FFmpeg 9 base) | PAR | post-1.0 | M |
| G34 | bindings | Python wheel / Rust crate / Go for libpelorus | vmafx generates Rust/Go/Python bindings RC4, publishes crates (#2320) | C library; qp-report demonstrator | thin bindings for interop ABI (parse/pack) so tune tooling needs no C mirror | PAR (REQ if G4 chooses crate) | 1.0 | M |
| G35 | platform | Windows | vmafx shipped | CI has MSYS2 UCRT64 job; UTF-8 paths fixed | keep parity; Vulkan on Windows replay | PAR | 0.5 | S |
| G36 | platform | macOS | vmafx shipped (Metal) | Vulkan via MoltenVK (U) | decide supported/unsupported explicitly | PAR | 0.6 | M |
| G37 | platform | Linux arm64 | vmafx RC4 assets | (U) | CI lane + patch replay on arm64 | PAR | 0.6 | M |
| G38 | platform | Android / iOS / WASM | vmafx 1.2 | none | none | OOS (pre-encode runs on server/workstation) | - | - |
| G39 | packaging | Package channels (Homebrew, conda-forge, winget, deb/rpm, PyPI, crates) | vmafx #2319/#2318/#2320 1.0.0 | source + release tarball of patches | libpelorus pkg-config/distro packaging only if Pelorus has standalone consumers | PAR | 1.0 | M |
| G40 | packaging | Containers / Helm / K8s / GPU pool | vmafx RC4-RC5 | dev container only | reference container with Pelorus-enabled FFmpeg so vmaf-tune prefilter live loop (#2586) can run | REQ (#2586) | 0.5 | M |
| G41 | supply chain | SLSA L3 build provenance, SBOM, signing | vmafx #2465, #2146 | Praetor engine attest (commit 54d533f mentions SLSA Build L3 attest releases) | align attestation format with vmafx provenance (ADR-2073) | PAR | 1.0 | S |
| G42 | governance | HISS-04 splits upstream (pel_blob_pack etc.) + SPDX headers | docs/state.md T-PELORUS-MIRROR-SOURCE-DRIFT; pre-commit-hooks.md:308 | complexity violations per vmafx report (U current state) | land splits and SPDX lines upstream so vmafx exceptions expire | REQ (vmafx exception expiry) | 0.4 | S |
| G43 | observability | OTel/Prometheus metrics, run-result document, error catalogue | vmafx #2311 #2312 #2327 | qp-report CSV | emit filter telemetry in a schema vmafx run-result document can ingest (#2311 facts) | PAR | 0.5 | M |
| G44 | live | Real-time GPU encode+score chain compat | vmafx #2138 RC4 (Pelorus-style pre-processing before encoder must remain zero-copy) | Vulkan frames zero-copy to Vulkan/NVENC | compatibility test: Pelorus filter -> encoder -> vmafx filter on hw frames | REQ-ish (#2138) | 0.5 | M |

## 4. Explicit vmafx asks to Pelorus (REQ list, ordered by gate date)

1. pelorus#86 schema spec lands before vmafx RC4 (so the mirror carries it); field names normative for vmafx #2271/#2510 (cross-test loads both).
2. pelorus#87 LCEVC enhancement-layer metadata (blocks vmafx #2251).
3. pelorus#81 encode provenance record (digest bound into vmafx score provenance, ADR-2073).
4. Real per-cell maps from vf_pelorus_analyze into PelorusSideData (ADR-1118 "plan C").
5. Pelorus-enabled FFmpeg build/container for vmaf-tune prefilter live test (#2586).
6. Upstream HISS-04/21 splits and SPDX line so the ten mirror exceptions can expire (docs/state.md, pre-commit-hooks.md:308).
7. Frozen control-plane knob contracts per additional filter if vmaf-tune prefilter is to grow beyond deband (ADR-1116).
8. #1455: Pelorus as planned libgpudispatch consumer (after vmafx extracts it).
9. #2514 (1.2): recon-frame/zone feedback needs pelorus#83/#84.
10. Joint decision on the mirror under ADR-2478 (no host C by 3.0).
