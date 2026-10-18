<!-- markdownlint-disable MD013 MD024 MD033 MD060 -->

# Research 0172: competitive audit (OSS, vendor SDKs, commercial, academic)

Evidence behind the competitive items of the roadmap in [ADR-0172](../adr/0172-roadmap-milestone-map.md): what OSS tools, vendor SDKs and commercial or academic products have or claim, which gaps Pelorus has to close (CLOSE), which claims it can beat (BEAT) and what nobody offers (WHITESPACE). Every URL was read on 2026-10-08. Quarterly refresh 2026-10-18 (#121): rows re-read on 2026-10-18 carry [F] for that date; changes listed in section Refresh log.

**Marks.** `[V]` verified against a primary source or a local binary. `(U)` unverified or secondary. `none-found` means the search ran and found nothing; absence is evidence only within the scope stated in its row. `CLAIMED` is a vendor or self-published figure. `MEASURED` is independent or peer-reviewed. `[F]` the primary page was fetched; items without it come from search summaries only.

**Ids used by roadmap issues.** `CO C#` and `CO BEAT #` are the OSS lists (Part A, sections 5 and 6). `CC` is the commercial claims ladder `L#`, the CLOSE list `C#` and the whitespace list `W#` (Part B). Competitor numbers are always labelled by who made the claim and are never Pelorus measurements.

Related: [zero-copy audit](0172-zero-copy-audit.md), [roadmap](../roadmap.md).

## Part A: OSS and vendor SDK/driver (CO)

### 1. Format note (vmafx method copied)

- vmafx has NO single competitor-audit artefact. `gh search issues -R VMAFx/vmafx` for "competitor", "claims", "prior art", "close the gap", "competitive", "state of the art", "market": 0 titles on competitor audit (hits unrelated: #2144, #1270, #1519, #2481). Issue bodies #2236-#2525: only #2242 matches "beats?" (model retrain, unrelated).
- Closest vmafx precedents, format copied from them:
  - `docs/research/0085-vendor-neutral-vvc-encode-landscape.md` (`git show origin/master:` read, no checkout): "Verification status - read before citing" table = Claim | Source URL | Verified by (WebFetch/gh-API/curl + date); `[verified]`/`[UNVERIFIED]` tags; explicit remainder table "what stays unverified and why".
  - `docs/research/0087-external-bench-competitor-survey-2026-05-08.md`: Open questions -> Findings per Q -> References; licence-first triage per competitor (GPL/NC => subprocess-only, never vendored).
  - Epic #2148 (+ #2144 plan update 2026-10-07) rule: "Every number in docs comes from a measurement with its device named; no unmeasured claims." Competitor numbers below are therefore labelled claim-by-whom, never Pelorus-measured.
- Applied here: verification table (sec 2a), [V]/(U) tags, licence column, claims carry author+metric+dataset, BEAT targets state what to measure and on which device.
- Milestone naming: `gh api repos/VMAFx/pelorus/milestones` showed only "0.1 First release" (#1) and "0.2 Telemetry, interchange and conformance" (#2) at survey time. The milestone labels 0.4/0.5/0.6/backends/codecs/1.0/post-1.0 below are working names; [the roadmap](../roadmap.md) is authoritative. Existing 0.2 issues map onto them: #80 ROI/delta-QP intent+conformance, #81 provenance record, #82 grain interchange (AV1<->H.274, grain-table I/O, inline bridge), #83 quality-window stream/zone emitter, #84 recon-frame handoff, #85 bit-exact filter mode, #86 normalised encoder telemetry, #87 LCEVC metadata.

### 2. Pelorus baseline (own measured numbers; paths)

Source: `{README.md,docs/architecture/overview.md,docs/principles.md,docs/development/bench-results.md,docs/adr/0114-encoder-steering.md,ffmpeg-patches/series.txt,libpelorus/include/pelorus/interop.h}`. Release v0.2.2, ABI 1.3, 18 patches on FFmpeg n9.0.2, licence BSD-2-Clause-Patent (libpelorus) / LGPL-2.1 (filters inside FFmpeg).

| claim (bench-results.md) | number | conditions | caveat |
|---|---|---|---|
| v0.2 CPU stand-in denoise, static | BD-rate -88.94% (BD-VMAF +8.19); high-motion -42.94% | hevc_nvenc p5, vs CLEAN ref, synthetic noise added | best case; self-labelled |
| v0.3 real GPU denoise | static -35.89% (BD-VMAF +2.18); BBB high-motion -33.95% (+1.96) | n8.1.1 stack, RTX 4090, hevc_nvenc p5, CQ {26,32,38,44} | scored against clean source of artificially noised input; NOT a noisy-source-referenced result |
| v0.5 auto-ROI banding steering | libx265: CAMBI 1.355->0.711 (-47%) iso-bitrate, VMAF 95.17->95.19; NVENC: CAMBI 1.533->0.910 (-41%), +0.10 VMAF, +3% bitrate | const-QP, 640x360 synthetic gradient | NVENC AQ must be off; content-dependent |
| v0.6/v0.7 AMD VAAPI ROI | -16% (v0.6, bitrate rose 684->744 kbps), -11% at true iso-bitrate 455 kbps, VMAF +0.46 | radeonsi | Intel QSV: crash fixed then driver wall, no number |
| v0.9 NVENC ext ME hints | no gain, ~2-3% slowdown at p7 | 4090 | honest negative |
| v0.10 SVT-AV1 ROI | CAMBI -1.5% (CRF35), -0.5% (CRF45), bitrate +5.6/+8.0% | synthetic | modest |
| v0.12 perceptual-AQ | SS2 BD-rate +13.05% / +19.89% (loss) | NVENC | negative |
| v0.13 per-shot CRF | VMAF BD-rate +8.81%, SS2 +7.35% (loss) | | negative |
| v0.14 lookahead denoise | +0.37 dB PSNR vs clean (35.60->35.98) | 24f cadence clip | modest |
| v0.17 GPU vs CPU gap | tuned hevc_nvenc (p7 hq, spatial+temporal AQ, lookahead 32, multipass fullres, b_ref middle) vs `x265 -preset slow`: CPU ahead ~1.8-2.5 SS2; av1_nvenc vs SVT-AV1 p4: ~2.5 SS2 | clean content, CQ-locked | primary metric moved VMAF-NEG -> SSIMULACRA2 |
| v0.18 MC-denoise | SSIM-vs-clean +0.0009 at cq20/26 over same-coord; NO BD-rate (metric inverts) | BBB 00:09:00, 720p, 64f, ~15 px/frame | modest |

Gap in Pelorus evidence (grep `uhq|tfLevel|temporal.?filter|ultra.?high` over docs/README/scripts, 2026-10-08): the bench baseline never enables NVENC UHQ tuning or `tfLevel`; the only `tf_level` hit is inside `ffmpeg-patches/files/nvenc-pelorus-film-grain.patch`. The v0.17 "tuned" config omits both. See BEAT-1.

#### 2a. Verification table (vmafx 0085 style)

| claim | source | verified by |
|---|---|---|
| FFmpeg release docs list deband, gradfun, hqdn3d, nlmeans, nlmeans_opencl, nlmeans_vulkan, bm3d, atadenoise, fftdnoiz, vaguedenoiser, owdenoise, dctdnoiz, scdet, mestimate, minterpolate, deblock, removegrain, libplacebo, vpp_amf, noise, fillborders | <https://ffmpeg.org/ffmpeg-filters.html> | curl + text grep [V] |
| FFmpeg libplacebo filter: `apply_filmgrain` ("Apply film grain (e.g. AV1 or H.274) if present in source frames"), `deband`, `deband_iterations/threshold/radius/grain` | same page, 11.148 libplacebo | curl [V] |
| nlmeans_vulkan: s default 1.0 (0-100), p 7, r 15, t 8; "supports more pixel formats than nlmeans or nlmeans_opencl, including alpha" | same page, 16.11 | curl [V] |
| FFmpeg master has `vf_scdet_vulkan.c` (Copyright 2025 Niklas Haas), `vf_fruc_vulkan.c` (Copyright 2026 Philip Langdale; VK_NV optical flow, options fps/perf/grid), `vf_vqe_amf.c` (AMF VQEnhancer, attenuation 0.02-0.4 default 0.1), `vf_frc_amf.c`, `vf_sr_amf.c`, `vf_vpp_amf.c`, `vf_vpp_qsv.c` (denoise 0-100, detail 0-100, procamp, tonemap, deinterlace), `vf_blackdetect_vulkan.c` | <https://github.com/FFmpeg/FFmpeg/tree/master/libavfilter> (api.github.com contents listing + raw files) | curl [V] |
| Installed ffmpeg n9.0.2 has avgblur/gblur/nlmeans/scdet/bwdif/scale/xfade _vulkan, denoise_vaapi, sharpness_vaapi, vpp_qsv, vpp_amf, sr_amf, libplacebo, bilateral_cuda | local `ffmpeg -hide_banner -filters` | local binary [V] |
| FFmpeg master encoders consuming `AV_FRAME_DATA_REGIONS_OF_INTEREST`: qsvenc.c, vaapi_encode.c, libx265.c (count>0). Zero hits: nvenc.c, libsvtav1.c, libaomenc.c, vulkan_encode.c, amfenc.c | raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavcodec/*.c grep | curl [V] (libx264.c not grepped for ROI; (U)) |
| FFmpeg master side-data enum has REGIONS_OF_INTEREST, VIDEO_ENC_PARAMS, FILM_GRAIN_PARAMS (types AV1 + H274), VIDEO_HINT (only libx264.c references it among checked encoders), SEI_UNREGISTERED, MOTION_VECTORS; no quality-map / saliency / grain-estimate-confidence / QP-report type | libavutil/frame.h, film_grain_params.h (raw, master) | curl [V] |
| NVENC API 13.1 (nv-codec-headers master): `qpDeltaMap` + `NV_ENC_QP_MAP_{EMPHASIS,DELTA}`, `NV_ENC_TEMPORAL_FILTER_LEVEL` (0,4) in H264/HEVC/AV1 configs (needs frameIntervalP>=5), `NV_ENC_TUNING_INFO_ULTRA_HIGH_QUALITY` ("only supported for HEVC and AV1 on Turing+"), `NV_ENC_LOOKAHEAD_LEVEL_0..3/AUTOSELECT`, `NV_ENC_FILM_GRAIN_PARAMS_AV1`, `NV_ENC_OUTPUT_STATS_{BLOCK,ROW}_LEVEL`, `NVENC_EXTERNAL_ME_HINT_*` | <https://raw.githubusercontent.com/FFmpeg/nv-codec-headers/master/include/ffnvcodec/nvEncodeAPI.h> | curl+grep [V] |
| NVENC guide 13.0: emphasis map "incompatible with spatial or temporal AQ"; "QP adjustment is performed after the rate control algorithm has run" (VBV violations possible); spatial AQ strength 1-15; lookahead depth <=32; per-row/per-block QP+bitcount stats row-level Turing/Ampere, block-level Ada+ | <https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/index.html> | WebFetch [V] |
| NVIDIA claim: temporal filtering "average coding gains of 4-5% for natural video content"; UHQ = lookahead level + temporal filtering; UHQ p4/p7 "beat x265 Slow in terms of bit-rate savings"; UHQ P1 "only 3% higher than x265 Slow"; UHQ P1 4x FPS of x265, P4 up to 3x FPS of x265 Slow; HEVC-only in 12.2 | <https://developer.nvidia.com/blog/improving-video-quality-with-nvidia-video-codec-sdk-12-2-for-hevc/> | WebFetch [V]; BD-BR table is a figure, not read; GPU/dataset not stated |
| Independent: NVENC Pascal->Blackwell; HQ vs UHQ PSNR-Y BD-rate Table 7: HEVC avg -10.7..-13.1%, AV1 (Blackwell) avg -17.0..-18.6%; Blackwell std modes 5.94% over Ada, up to 22.79% UHQ; UHQ latency >400%, board power up to +40%; datasets Netflix Chimera + Twitch gaming; VMAF "may perceptually regress in simpler, low-motion sequences" | <https://arxiv.org/abs/2605.01187> ; PDF text `pdftotext` | curl+pdftotext [V] |
| AMF v1.5.3 (2026-09-29): `HevcROIData`/`Av1ROIData` (64x64, importance 0-10), `ROIData` H.264 (16x16), `HevcBlockQpMap` (AMF_SURFACE_GRAY32 block QP values; HEVC only in grep), `PSNRFeedback`/`SSIMFeedback`/`StatisticsFeedback` per encoder, `HevcPreAnalysis`; PreAnalysis: PAQ (CAQ), TAQ modes 1/2, scene-change flag + `ACTIVITY_MAP` output surface, LTR, static-scene skip, lookahead depth; PreProcessing: "JND based edge-adaptive denoising filter", beta, NV12 only, DX11/OpenCL, RX 5000+ / Ryzen 2000 U/H+ | <https://github.com/GPUOpen-LibrariesAndSDKs/AMF> (amf/public/include/components/*.h, amf/doc/AMF_Video_PreAnalysis_API.md, AMF_Video_PreProcessing_API.md), gh releases | curl [V] |
| oneVPL/libvpl v2.17.0 (2026-06-22), `mfxstructures.h`: MCTF (`mfxExtVppMctf` FilterStrength 0-20), VPP_DENOISE2, VPP_DETAIL, ENCODER_ROI, MBQP, AV1_FILM_GRAIN_PARAM (decode-side report), `mfxExtQualityInfoMode/Output` (encoder per-frame MSE[3] report), experimental `mfxExtAIEncCtrl` (SaliencyEncoder, ML AdaptiveTargetUsage), experimental `mfxExtEncPreProcessing` (TFLevel 0-4, not with CQP), AI super-resolution (artifact-removal mode), AI frame interpolation | <https://github.com/intel/libvpl> api/vpl/mfxstructures.h, CHANGELOG.md | curl [V] |
| Vulkan `VK_KHR_video_encode_quantization_map` ratified, rev 2, last modified 2024-09-23; contributors AMD, NVIDIA, Intel, RasterGrid; quantization delta maps per block for all rate-control modes + emphasis maps; interacts with encode H.264/H.265/AV1 | <https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_quantization_map.html> | curl [V] |
| libplacebo `film_grain.h`: `pl_film_grain_type` = NONE/AV1/H274; shader-based grain synthesis; MIT-relicensed file inside LGPL-2.1+ project; GitHub haasn/libplacebo LGPL-2.1 | raw film_grain.h, gh api | curl [V] |
| SVT-AV1 v4.2.0 (local `SvtAv1EncApp --help` + Docs/Parameters.md): `--film-grain 0-50` (denoise level), `--film-grain-denoise`, `--fgs-table`, `--adaptive-film-grain`, `--enable-tf 0-2`, `--tf-strength 0-4`, `--enable-kf-tf`, `--roi-map-file`, `--aq-mode 0-2`, `--enable-variance-boost`, `--scd`, `--lookahead` | local binary + <https://raw.githubusercontent.com/AOMediaCodec/SVT-AV1/master/Docs/Parameters.md> | [V] |
| aomenc (local `--help`): `--denoise-noise-level 0-50`, `--denoise-block-size`, `--enable-dnl-denoising`, `--film-grain-table`, `--film-grain-test`, `--deltaq-mode` 0-6 (6 = Variance Boost), `--enable-tpl-model`, `--tune-content`, `--sharpness`; libaom example `noise_model.c` builds grain table from source+denoised pair | local binary; <https://aomedia.googlesource.com/aom/+/243f8ae84b/examples/noise_model.c> | [V] / search (U for exact flags of noise_model) |
| x265 4.3 (local): `--hist-scenecut`, `--hist-threshold` (default 0.03), `--scenecut-aware-qp`, `--aq-mode 0-4` (3 = dark-scene bias "to prevent color banding/blocking", 4 = edge info), `--nr-intra/--nr-inter` (DCT-domain deadzone, "no pixel-level filtering"), `--rc-grain`, `--tune grain`, `--nalu-file` (arbitrary SEI by POC), `--cutree`, `--zones`. NO `--temporal-filter`/MCTF and NO film-grain-characteristics option found in cli.rst nor `--fullhelp` | local binary + <https://raw.githubusercontent.com/videolan/x265/master/doc/reST/cli.rst> | [V] |
| rav1e (local): `--photon-noise`, `--film-grain-table`; no denoiser | local `rav1e --help` | [V] |
| GStreamer 1.28.7 local: videofiltersbad `scenechange`; nvcodec `nvh265enc` props spatial-aq/temporal-aq/aq-strength/rc-lookahead/i-adapt/b-adapt (no ROI/QP-map property); `vulkanh264enc`/`vulkanh264device2enc` (no ROI/QP-map property grep hit); no denoise/deband/grain-estimate element (only frei0r film-grain add). `va*enc`, `qsv*enc`, `amf*enc` not installed locally -> (U) for their ROI properties | local `gst-inspect-1.0` | [V]/(U) |
| DL Streamer (open-edge-platform/dlstreamer, MIT): elements gvadetect/gvaclassify/gvainference/gvatrack/gvamotiondetect/gvawatermark...; no denoise/ROI-encode/quality element listed | <https://raw.githubusercontent.com/open-edge-platform/dlstreamer/main/README.md> | WebFetch [V] (README only; element docs unchecked) |
| Netflix AV1 FGS production: 36% avg bitrate reduction at >=1080p, 10% below 1080p, ~300 titles, one frame 8274 vs 2804 kbps | mirror <https://noise.getoto.net/2025/07/02/av1-scale-film-grain-synthesis-the-awakening/> (original netflixtechblog.com returned HTTP 403) ; Norkin&Birkbeck DCC 2018 "up to 50%" <https://norkin.org/pdf/DCC_2018_AV1_film_grain.pdf> | WebSearch snippet (U) - re-verify on original before quoting |
| Apple `VTTemporalNoiseFilterConfiguration` (VTFrameProcessor, macOS 26+, `isSupported`); no public ROI/QP-map key on VTCompressionSession found | <https://developer.apple.com/documentation/videotoolbox/vttemporalnoisefilterconfiguration.md> | WebSearch snippet (U); ROI absence = not found in one search only |
| NVIDIA VPI TNR: backends CUDA and VIC (Jetson) in 3.1/4.0 samples; strength 0-1, scene-lighting preset | <https://docs.nvidia.com/vpi/3.1/sample_tnr.html> | WebSearch snippet (U) |
| Maxine VFX SDK: artifact reduction, super resolution (up to 4x), upscaler, video noise removal; TensorRT; NGC gated/EA | <https://github.com/nvidia/MAXINE-VFX-SDK> (gh api 404 for repo metadata), NGC catalog | WebSearch snippet (U); licence (U) |

Still unverified: Optical Flow SDK feature/perf numbers (not fetched); Intel MCTF/ROI quality claims (no vendor number found); VideoToolbox ROI absence; NVENC AV1 UHQ/tf in FFmpeg master option names; Maxine/VPI licence terms; libx264.c ROI consumption; Windows-only AMF PreProcessing behaviour on Linux; Netflix original numbers.

### 3. Per-competitor table

Columns: features | licence | GPU backends | claimed results (who/metric) | maturity. Repo facts: `gh api repos/<r>` 2026-10-08 (stars/pushed).

#### 3.1 FFmpeg built-ins and libplacebo

| competitor | features | licence | GPU | claims | maturity |
|---|---|---|---|---|---|
| FFmpeg `deband`, `gradfun` | CPU deband (1thr..4thr 0.02 default, range 16, direction, blur, coupling); gradfun | LGPL-2.1+ (FFmpeg) | none (CPU) | none | stable, since 2010s |
| FFmpeg `hqdn3d`, `atadenoise`, `fftdnoiz`, `vaguedenoiser`, `owdenoise`, `dctdnoiz`, `bm3d`, `nlmeans` | CPU spatial/temporal denoise | LGPL-2.1+ (GPL for some) (U per-filter) | none | none | stable |
| `nlmeans_vulkan` / `nlmeans_opencl` | NLM denoise, patch 7 / research 15 / parallelism t 8, more pixfmts, alpha | LGPL | Vulkan / OpenCL | none | in release docs; Pelorus models on it |
| `avgblur_vulkan`,`gblur_vulkan`,`bwdif_vulkan`,`scale_vulkan`,`xfade_vulkan` | building blocks | LGPL | Vulkan | none | stable |
| `scdet` / `scdet_vulkan` | scene-change score (MAFD-based, (U)); Vulkan twin in master + installed n9.0.2 | LGPL | CPU / Vulkan | none | present; `scdet_vulkan` absent from release docs page (grep) |
| `mestimate`, `minterpolate`, `fruc_vulkan` (master, 2026) | CPU ME; Vulkan NV optical-flow frame-rate conversion (perf slow/medium/fast, grid 1-8) | LGPL | Vulkan (VK_NV_optical_flow) | none | `fruc_vulkan` new in master; optical-flow engine not exposed as MV side data (U) |
| `libplacebo` filter | deband (iterations/threshold/radius/grain), dithering, `apply_filmgrain` (AV1 + H.274 synthesis on GPU), tone mapping, scaling, Dolby Vision | LGPL-2.1+ ; film_grain.h also MIT | Vulkan (via libplacebo; OpenGL/D3D11 in lib (U)) | none | very mature, mpv/VLC/FFmpeg consumer; display-time, not encoder steering |
| `vpp_qsv` | denoise 0-100, detail 0-100, procamp, scale, tonemap, deinterlace | LGPL | Intel QSV (VAAPI/oneVPL) | none | stable |
| `denoise_vaapi`,`sharpness_vaapi`,`procamp_vaapi` | VAAPI VPP | LGPL | VAAPI | none | stable |
| `vpp_amf`,`sr_amf`,`frc_amf`,`vqe_amf` | AMF scale/CSC, super-res, frame-rate conv., VQEnhancer (attenuation 0.02-0.4) | LGPL | AMF (DX11/DX12/Vulkan) | none | `vqe_amf` new in master |
| `scale_cuda`,`bilateral_cuda`,`yadif_cuda`,`thumbnail_cuda` | CUDA VPP; bilateral = spatial edge-preserving | LGPL | CUDA | none | stable |
| `scale_npp` | not in docs grep (U) | | | | |
| `fillborders`, `removegrain`, `deblock` | CPU border fill, RG modes, deblock | LGPL | none | none | stable |
| libplacebo (library) | see filter; <https://code.videolan.org/videolan/libplacebo> ; GitHub mirror haasn/libplacebo 798 stars, pushed 2026-10-18, latest release v7.360.1 [F] | LGPL-2.1 | Vulkan (+GL (U)) | none | very mature |

#### 3.2 VapourSynth / AviSynth ecosystem

| competitor | features | licence | GPU | claims | maturity |
|---|---|---|---|---|---|
| neo_f3kdb (HomeOfAviSynthPlusEvolution) | f3kdb deband: range 15, 7 sample modes (5-7 detail/gradient aware), dither algo ordered/Floyd-Steinberg, grain 0-4096 dynamic, 8-16 bit in/out, SSE4.1/AVX2/AVX-512 | GPL-3.0; 41 stars; pushed 2026-09-26, latest release r11 [F] | none | none | maintained; CPU only |
| vs-placebo `Deband` (Lypheo) | libplacebo deband in VS, 8/16-bit int + float, blue-noise dither default, grain | LGPL-2.1; 103 stars; pushed 2026-10-18, latest release 2.0.4 [F] | Vulkan via libplacebo (U in README) | none | maintained |
| vs-dfttest2 (AmusementClub) | DFTTest CUDA + x86 | GPL-3.0; 19 stars; pushed 2026-09-26, latest release v11 [F] | CUDA, CPU | none | maintained |
| VapourSynth-BM3DCUDA (WolframRhodium) | BM3D, V-BM3D (radius>0), CBM3D; CUDA cc>=5.0, CUDA-RTC, CPU AVX2; float32 input | GPL-2.0; 88 stars; pushed 2026-09-26, latest release R2.17 [F] (README states no licence) | CUDA (HIP not mentioned) | no benchmarks in README | maintained |
| KNLMeansCL | OpenCL NLM, AviSynth(+)/VapourSynth | GPL-3.0; 113 stars; last push 2023-01-19, latest release v1.1.1 [F] | OpenCL | none | stale 3y |
| vapoursynth-mvtools (MDegrain basis) | CPU motion analysis + MDegrain; pinterf/mvtools for AviSynth (191 stars, pushed 2026-04-24, release 2.7.47 [F]) | GPL-2.0; 221 stars; pushed 2026-06-21 [F] | CPU only | none | mature |
| havsfunc (SMDegrain, DeHalo_alpha, FineDehalo) | Pelorus' dehalo/aa ports target these | Unlicense; 120 stars; pushed 2025-11-27, latest release r34 [F] | CPU | none | mature |
| vs-jetpack (JET) | vsdenoise, vsdeband, vsdehalo, vsaa, vsmasktools; extras `bm3d`,`dfttest2`,`mvutensils`,`nlm-ispc`,`vs-placebo`; GPU extra = onnxruntime | MIT; 81 stars; pushed 2026-10-17, latest release v2.2.6 [F] | CUDA/ORT optional | none | active; vs-denoise standalone archived (merged) |
| vs-mlrt (AmusementClub) | ML runtimes (waifu2x, DPIR, RealESRGAN, Real-CUGAN, RIFE, SCUNet, ArtCNN) CPU/GPU | GPL-3.0; 486 stars; pushed 2026-09-25, latest release v15.16 [F] | TensorRT/ORT/OpenVINO (U) | none | active |
| EdgeFixer / bbmod | border repair (sekrit-twc/EdgeFixer WTFPL, 22 stars, pushed 2026-07-22, latest release r3 [F]) | WTFPL | CPU | none | stable |
| zimg | colour/scale/dither lib | WTFPL; 472 stars; pushed 2026-09-04, latest release release-3.0.6 [F] | CPU | none | mature |
| AviSynth+ | host | none-declared per API; 1211 stars; pushed 2026-10-15, latest release v3.7.5 [F] | CPU | | mature |

Properties common to the ecosystem (from READMEs above): frames pass through system RAM into a pipe to the encoder (vspipe -> ffmpeg/encoder); no encoder steering; no side-data standard. (vspipe present locally: R4.3 "VapourSynth Video Processing Library", `vspipe --version` 2026-10-08.)

#### 3.3 Encoder-internal pre-processing

| competitor | features | licence | GPU | claims | maturity |
|---|---|---|---|---|---|
| aomenc / libaom | `--denoise-noise-level`, `--denoise-block-size`, noise model -> film grain table, `--film-grain-table`, deltaq-mode 0-6 incl. 6 Variance Boost | BSD-2-Clause + AOM patent | none | none in help | reference encoder |
| SVT-AV1 v4.2.0 | `--film-grain` (denoise+synth), `--film-grain-denoise`, `--fgs-table`, MCTF `--enable-tf`/`--tf-strength 0-4`, `--roi-map-file`, `--enable-variance-boost`, `--scd` | BSD-3-Clause-Clear (GitHub mirror 82 stars, pushed 2026-08-09 [F]) | none | none in help | production (Netflix, others (U)) |
| svt-av1-psy (psy-ex) | SVT-AV1 + perceptual tunings; 396 stars; pushed 2026-02-12, latest release v3.0.2 [F] | BSD-3-Clause-Clear | none | (U) | active fork |
| rav1e | `--photon-noise`, `--film-grain-table`; no denoiser | BSD-2-Clause; 4161 stars; pushed 2026-10-17, latest release v0.8.1 [F] | none | none | active |
| x265 4.3 | AQ modes 0-4 (3 dark-scene/banding, 4 edge), `--nr-*` DCT deadzone, `--hist-scenecut`, `--scenecut-aware-qp`, `--rc-grain`, `--tune grain`, `--nalu-file` SEI injection; no MCTF, no FGC option | GPL-2.0; 814 stars (last GitHub push 2023-09-11 = mirror [F]) | none | none | mature |
| x264 | AQ, mbtree; `quortex/x264-pVMAF` (GPL-2.0, 26 stars, pushed 2026-02-06 [F]) predicts VMAF in-loop | GPL-2.0 | none | per vmafx 0087 (not re-verified) | mature |
| Av1an v0.5.2 (`rust-av/Av1an`) | chunked parallel encode, scene detect, Target Quality over VMAF/SSIMULACRA2/Butteraugli (`--target-quality`, `--probes` 4), `--photon-noise`, `--chroma-noise`; encoders aomenc/SVT-AV1/rav1e/vpx/x264/x265 | GPL-3.0; 2000 stars; pushed 2026-10-16, latest release v0.5.2 [F] | none (CPU) | "fully utilizing a 96-core CPU" (README image alt; author rust-av, metric throughput, dataset unstated) [F] | active |
| grav1synth (`rust-av/grav1synth`) | `inspect`, `diff` (source vs denoised -> grain table), `apply` (table or `--iso`), `remove`; AV1 only | MIT (GitHub API); 96 stars; pushed 2026-10-16 [F] | none | none | small, caveats in README (keyframe-filtering=2) |
| ab-av1 | VMAF/ crf-search wrapper over ffmpeg; MIT; 1019 stars; pushed 2026-09-07, latest release v0.11.7 [F] | MIT | uses ffmpeg encoders | none | active |
| libaom `noise_model` example | grain table from original+denoised | BSD-2 | none | | reference |

#### 3.4 Vendor SDKs / drivers

| competitor | features | licence | GPU/HW | claims | maturity |
|---|---|---|---|---|---|
| NVIDIA NVENC SDK 13.1 | qpDeltaMap (delta/emphasis), spatial AQ 1-15, temporal AQ, lookahead (depth<=32 / level 0-3), **tfLevel temporal filter** (H264/HEVC/AV1, needs frameIntervalP>=5), **UHQ tuning** (HEVC+AV1, Turing+), external ME hints struct, AV1 `NV_ENC_FILM_GRAIN_PARAMS_AV1`, output stats per row/block (QP+bits), recon output (Turing+), split-frame | proprietary SDK; ffnvcodec headers MIT/LGPL (SDK 13.1.15 verified [F]) | NVENC ASIC + CUDA assist | NVIDIA (CLAIMED, SDK 12.2 blog [F]): tf 4-5% avg coding gain (metric unstated, natural video dataset unstated); UHQ p4/p7 beat x265 Slow (metric bit-rate savings, dataset unstated). Independent (MEASURED, Bojović et al., arXiv:2605.01187 [F]): UHQ vs HQ -10.7..-13.1% HEVC, -17.0..-18.6% AV1 (PSNR-Y BD-rate, datasets Netflix Chimera + Twitch gaming) | production |
| NVIDIA Video Effects SDK (Maxine) | artifact reduction, SR up to 4x, upscaler, noise removal (webcam); TensorRT | proprietary, NGC (U) | NVIDIA GPU | none found | production but not pipeline-integrated |
| NVIDIA VPI | TNR (CUDA, VIC on Jetson), other CV algos | proprietary (U) | CUDA/VIC/PVA/OFA (U) | none | production |
| NVIDIA Optical Flow SDK / VK_NV_optical_flow | HW optical flow; used by FFmpeg `fruc_vulkan` | proprietary | Turing+ OFA | not fetched | production |
| Intel libvpl 2.17 | MCTF 0-20, VPP denoise2/detail/procamp, ROI, MBQP, AV1 FG param (decode), QualityInfo output (per-frame MSE), experimental SaliencyEncoder + ML adaptive TU, EncPreProcessing TF 0-4, AI SR + AI frame interp | MIT (libvpl; 371 stars, pushed 2026-07-13, release v2.17.0 [F]); media-driver (U) | Intel Xe | none found | MCTF/ROI mature; AI/TF experimental (`ONEVPL_EXPERIMENTAL`) |
| AMD AMF 1.5.3 | PreAnalysis (CAQ/PAQ, TAQ 1/2, scene change + ActivityMap output, static-scene skip, LTR, lookahead), PreProcessing (JND edge-adaptive denoise, beta), VQEnhancer, ROI importance 0-10 (64x64 HEVC/AV1, 16x16 H.264), HEVC block QP map, PSNR/SSIM/statistics feedback | MIT-style (SDK doc says MIT licence text; GitHub SPDX NOASSERTION; 751 stars, pushed 2026-10-16, release v1.5.3 [F]) | AMD VCN, DX11/DX12/Vulkan/OpenCL | none found | PreProcessing "beta" per its own doc |
| VAAPI VPP | denoise/sharpness/procamp; ROI via VAEncMiscParameterTypeROI where driver supports | MIT (libva) (U) | Intel/AMD | none | mature |
| Vulkan Video + `VK_KHR_video_encode_quantization_map` / `feedback2` | per-block quantization delta maps + emphasis maps (`quantization_map`, ratified 2024-09-23 [F]); **NEW**: `VK_KHR_video_encode_feedback2` (Ext 599, ratified 2026-02-04 [F]): whole-picture stats (avg/min/max QP, intra/inter/skip pixel counts) and per-partition status/bytes/offset | Khronos spec | NVIDIA/AMD/Intel drivers per support | none | ratified 2024-09-23 (qmap) / 2026-02-04 (feedback2) [F] |
| Apple VideoToolbox | `VTTemporalNoiseFilterConfiguration` (macOS 26+ frame processor); ROI key none-found | proprietary | Apple Silicon | none | new |
| Android MediaCodec / V4L2 / Media Foundation | QP offset map 16x16 (Android), ROI rectangle (MF) | n/a | n/a | n/a | (U) from search snippets |

#### 3.5 GPU pipelines / frameworks

| competitor | features | licence | GPU | claims | maturity |
|---|---|---|---|---|---|
| GStreamer 1.28.7 | cuda\* convert/scale/compositor, vulkan\* decode/enc/overlay/shaderspv, nvcodec enc props (AQ, lookahead), videofiltersbad `scenechange`; no denoise/deband/ROI property found locally | LGPL-2.1 | CUDA/Vulkan/D3D/AMF/VA | none | mature, no pre-encode quality filters |
| NVIDIA DeepStream (9.0 docs) | inference-centric; nvdspreprocess = ML tensor preprocessing, not pixel denoise | proprietary SDK | NVIDIA | not fetched | production; encode quality not its scope (U) |
| Intel DL Streamer | gva* analytics/inference on OpenVINO; no pre-encode quality element | MIT; 642 stars; pushed 2026-10-17, latest release v2026.2.0 [F] | Intel CPU/GPU/NPU | none | active |

### 4. Feature matrix

Legend: H = shipped/have (ref), C = vendor/author-documented capability (not measured by Pelorus), P = partial (note), N = none-found, U = unverified.
Columns: PEL = Pelorus v0.2.2 | FF = FFmpeg built-ins (+libplacebo filter) | VS = VapourSynth/AviSynth ecosystem | ENC = encoder-internal (SVT-AV1/aomenc/x265/rav1e) | NV = NVENC SDK 13.1 | AMD = AMF 1.5.3 | INT = Intel libvpl 2.17/QSV | VK = Vulkan Video | AV = Av1an/grav1synth/ab-av1 | GST = GStreamer 1.28.7 | DS = DeepStream/DL Streamer.

| capability | PEL | FF | VS | ENC | NV | AMD | INT | VK | AV | GST | DS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deband | H zero-copy Vulkan (README) ; v0.16 pre-encode gain does not survive encoder | H CPU `deband`,`gradfun`; H GPU `libplacebo deband` | H neo_f3kdb (CPU), placebo.Deband | N (x265 aq-mode 3 = bias, not filter) | N | N | N | N | N | N | N |
| temporal denoise | H `pelorus_denoise_vulkan` (−35.9%/−33.95% BD-rate vs clean-ref, v0.3) | H CPU hqdn3d/atadenoise; GPU nlmeans_vulkan (spatial NLM) | H MDegrain/BM3D(V)/DFTTest | P SVT `--enable-tf` (ARF MCTF), aomenc `--denoise-*` | C `tfLevel` 0/4 HW-assisted MCTF (4-5% avg, NVIDIA) | C PreProcessing JND denoise (beta, NV12, DX11/OpenCL) ; VQEnhancer | C MCTF 0-20, VPP denoise2 ; TF 0-4 experimental | N | N | N | N |
| MC (motion-compensated) denoise | H `denoise mc=1` warp (v0.18: +0.0009 SSIM) | N (mestimate CPU, no mc-denoise filter; `fruc_vulkan` is interpolation) | H MVTools MDegrain (CPU), BM3D-V | P SVT ARF MCTF | C tfLevel (ME on NVENC HW, filter CUDA per NVIDIA blog) | U | C MCTF | N | N | N | N |
| grain estimation | P `grain_estimate_vulkan` estimator built, no accuracy bench vs references in bench-results.md | N | N (external: grav1synth diff) | C aomenc noise model (denoise-noise-level), SVT `--film-grain` | N | N | N | N | C grav1synth `diff`/`inspect`; Av1an photon-noise (parametric, not estimate) | N | N |
| grain synthesis AV1 | H via native side data to SW encoders; NVENC AV1 HW grain wired (patch 0011) | H libplacebo `apply_filmgrain` (display-side) | N | H SVT/aomenc/rav1e | C `NV_ENC_FILM_GRAIN_PARAMS_AV1` | N | P decode-side param report only | N | H grav1synth apply | N | N |
| grain synthesis H.274 (HEVC/VVC) | P `pelorus_fgs` BSF static AVOption SEI (HEVC only; no inline bridge) | P libplacebo renders H274 in display path | N | P x265 `--nalu-file` raw SEI (no FGC option) (U for payload workflow) | N | N | N | N | N | N | N |
| film-grain interchange (AV1<->H.274, tables) | N today (#82 open) ; ADR-0161 H.274 mapping exists | P AVFilmGrainParams has AV1+H274 types (frame.h) | N | P grain-table file I/O (aomenc/SVT/rav1e share table format) | N | N | N | N | H grav1synth tables | N | N |
| scene cut | H `pelorus_scenecut` via mc `has_scene_cut` -> pict_type=I (BD-rate A/B pending) | H `scdet`, `scdet_vulkan` (score metadata) | H (external) | H x265 hist-scenecut, SVT `--scd` | H lookahead I-adapt | C PA scene-change flag + sensitivity | U | N | H Av1an scene detection | P `scenechange` element | N |
| ROI / saliency steering | H `analyze roi=1` banding map (not saliency) | P ROI side data consumed by qsv/vaapi/libx265 only | N | P SVT `--roi-map-file`; x265 quantOffsets API (U) | C emphasis/delta map | C ROI 0-10 | C ROI + experimental Saliency encoder | C quant-map ext | N | N | N |
| delta-QP maps (dense) | H NVENC qpDeltaMap (-41% CAMBI), QSV MBQP (HW proof pending), Vulkan qmap (positive offsets only on 4090), SVT per-SB seg map | N (ROI rectangles only) | N | P SVT segment map | C | C HEVC block QP map | C MBQP | C | N | N | N |
| external ME hints | H NVENC patch 0008 (v0.9: no speed gain, ~2-3% slowdown) | N (VIDEO_HINT = skip hints, libx264 only) | N | N | C struct exists | N | N | N | N | N | N |
| QP / encoder telemetry feedback | P `PEL_SEC_QPREPORT` ABI + x265 `--csv` reader (measured); QSV/NVENC per-block readers not done | N (VIDEO_ENC_PARAMS decode-side (U)) | N | P x265 csv, SVT stats | C output stats row/block QP+bits | C PSNR/SSIM/Statistics feedback | C QualityInfoOutput (MSE/frame) | C `feedback2` ext (picture QP/partition stats) | P Av1an reads metrics post-hoc | N | N |
| metric-in-the-loop | P via vmafx autotune (control plane ADR-0106; v0.12/0.13 loops were negative) | N | N | P x264-pVMAF (VMAF predictor inside encoder) | N | N | N | N | H Av1an Target Quality, ab-av1 (offline probes) | N | N |
| zero-copy HW frames | H Vulkan VRAM end-to-end; NVENC/QSV/VAAPI/AMF need explicit mapping (README) | H within a vendor domain | N (system RAM + pipe) | N | C CUDA/DX surface input | C | C | C | N | H per-vendor elements | H NVMM (U) |
| multi-vendor GPU | P Vulkan filters portable (v0.11: Arc +65.7% from NLM tiling, RADV neutral, 4090 -1..-4%); ROI proven on NVIDIA+AMD VAAPI | P Vulkan filters + per-vendor | N | N | N | N | N | H (spec) | N | P | N |
| bit-exactness (cross-device) | N today (#85 open; "exploratory") | U (Vulkan filters not claimed bit-exact) | P bm3dcpu "not guaranteed bitwise identical to CUDA" (README) | H CPU deterministic (x265 nr breaks frame-thread parity per doc) | N | N | N | N | N | N | N |
| side-data interop to a quality oracle | H `PelorusSideData` ABI 1.3, UUID-keyed AV_FRAME_DATA_SEI_UNREGISTERED, conformance fixture shared with vmafx | P generic side-data types only (no quality-oracle schema) | N | N | N | N | N | N | N | N | N |
| provenance | P #81 open (encode provenance record); vmafx has #2142 per-score provenance | N | N | N | N | N | N | N | N | N | N |

### 5. BEAT list (measure -> target)

Principle: beat only on a like-for-like baseline. Current Pelorus headline numbers are vs clean-reference + synthetic noise + `p5`, so they do not yet beat any vendor claim.

1. **BEAT NVENC tfLevel / UHQ (HEVC + AV1).** Claim to beat: NVIDIA 4-5% avg for temporal filter (blog 12.2); independent UHQ vs HQ -10.7..-13.1% HEVC, -17.0..-18.6% AV1 (PSNR-Y, Netflix Chimera + Twitch, arXiv 2605.01187 Table 7). Measure: noisy-source-referenced BD-rate (SSIMULACRA2 primary, VMAF + CAMBI secondary, per bench v0.17 method) for four arms on RTX 4090 and an Ada/Blackwell part: (a) tuned NVENC (v0.17 config), (b) +`tfLevel 4` (frameIntervalP>=5), (c) +UHQ, (d) Pelorus denoise + (a). Target: arm (d) <= arm (c) bitrate at iso-SS2 on grain/noise content, and (d)+(c) additive gain >= 3% over (c) alone. Also record fps and board power (UHQ costs >400% latency, +40% power per arXiv) - Pelorus wins on latency if (d) is within 10% of (a) fps.
2. **BEAT x265 `--nr-*` / SVT `--enable-tf` / aomenc `--denoise-noise-level` on source-referenced BD-rate.** Measure: same noisy corpus, CPU encoders with their own denoise vs NVENC+Pelorus. Target: close v0.17 residual (HEVC ~1.8-2.5 SS2, AV1 ~2.5 SS2 behind x265 slow / SVT p4) to <= 1.0 SS2 on noisy content, speed >= 3x x265 slow (NVIDIA UHQ P4 claims up to 3x FPS of x265 Slow; match that bar).
3. **BEAT libplacebo deband / neo_f3kdb as the encoder-aware deband.** Claim to beat: none published by them. Measure: CAMBI + SS2 BD-rate with the encoder in the loop on 10-bit banding corpus (the "corpus gap" in bench v0.16). Target: positive pre-encode gain that SURVIVES the encoder (v0.16 negative today) vs `libplacebo deband` default and `deband` (ffmpeg) at iso-bitrate; throughput vs CPU neo_f3kdb (state device).
4. **BEAT FFmpeg stock ROI path.** Master encoders honouring ROI: qsvenc, vaapi_encode, libx265 only; Pelorus already adds NVENC, Vulkan, libaom, SVT-AV1 (patch 0004/0009/0012/0013). Measure: conformance (does encoder honour intent; #80) and iso-bitrate banding/SS2 per encoder. Target: >= -11% CAMBI at iso-bitrate on every ROI-capable encoder (AMD v0.7 already -11%), and an SS2 non-regression (no repeat of v0.12 +13% loss).
5. **BEAT grav1synth / aomenc noise model on grain-parameter accuracy.** Measure: for a corpus with known synthetic AR grain (plus real film scans), parameter error and decoded-grain SSIM/PSNR vs source residual, grain estimate throughput. Target: GPU estimator within the same error as `noise_model`/`grav1synth diff` at >= 10x speed; zero-copy side data vs file tables. No accuracy bench exists in bench-results.md today.
6. **BEAT Netflix-class FGS economics on HEVC/NVENC.** Claim: Netflix 36% avg bitrate reduction >=1080p, 10% <1080p (~300 titles) with AV1 FGS (mirror; (U)). Measure: denoise + `av1_nvenc` HW grain (patch 0011) vs plain `av1_nvenc` on grainy 1080p film. Target: >= 20% iso-SS2 bitrate reduction on grainy 1080p+ content with grain params carried; report <1080p separately.
7. **BEAT Av1an / ab-av1 probe cost with in-pipeline metric.** Claim: Av1an Target Quality needs `--probes` 4 default full probe encodes per chunk. Measure: wall-clock + GPU-seconds to hit a VMAF/SS2 target using vmafx side-data feedback vs 4 probe encodes. Target: <= 1 probe-equivalent cost at same final error (only after #83 quality-window feedback exists).
8. **BEAT AMD PreProcessing / Intel MCTF on cross-vendor reach.** Measure: Pelorus Vulkan denoise on Arc A380 / RADV / 4090 (bench v0.11 devices) vs vendor MCTF (`vpp_qsv denoise`/MCTF on Arc) and AMF PreProcessing (Windows-only DX11/OpenCL per doc) at iso-bitrate SS2. Target: parity or better on Intel/AMD with identical filter settings across all three vendors (the only multi-vendor claim in the matrix).
9. **BEAT x265 `--hist-scenecut` / `scdet` on GOP placement.** Measure the pending BD-rate A/B (README step 9: "no number claimed") on multi-shot content, forced-IDR on `mc` cut vs periodic IDR vs NVENC lookahead I-adapt. Target: >= 1% BD-rate vs NVENC lookahead I-adapt on >=10 shot-rich clips; cut-detection F1 >= scdet at equal threshold (state fixture).

### 6. CLOSE list (gaps to match; roadmap milestone)

| # | gap | evidence | milestone (working names) | note |
|---|---|---|---|---|
| C1 | No NVENC `tfLevel`/UHQ baseline in any Pelorus bench; every "beats NVENC" claim is vs p5/tuned-without-TF | grep sec 2; NVENC 13.1 header | 0.4 schemas (define baseline matrix schema) -> run in 0.6 | blocks BEAT-1/2; do first |
| C2 | Denoise results scored vs synthetic-clean reference, not source-referenced | bench v0.3, v0.18 caveats | 0.4 | add noisy-source corpus + SS2/VMAF/CAMBI method |
| C3 | Film-grain interchange AV1<->H.274, grain-table I/O, inline per-frame bridge to `pelorus_fgs` | #82; README step 7; ADR-0117/0161 | 0.5 interchange & live integration | match grav1synth table I/O and libplacebo type coverage; x265 `--nalu-file` is the only HEVC SEI route found, document it |
| C4 | Grain estimator accuracy unproven | no grain accuracy table in bench-results.md | 0.5 | needed for BEAT-5 |
| C5 | Normalised encoder telemetry; per-block readers for NVENC (row/block stats Ada+), AMF (PSNR/SSIM/Statistics feedback), Intel (QualityInfoOutput MSE), x265/SVT | #86; QSV path HW-blocked | 0.6 conformance & encoder feedback | vendors already expose it (matrix row); Vulkan Video ratified `VK_KHR_video_encode_feedback2` (2026-02-04, Ext 599 [F]) exposing avg/min/max QP and partition bytes |
| C6 | ROI/delta-QP intent normalisation + honoured-vs-requested conformance | #80; README QP report | 0.6 | NVENC emphasis map is incompatible with AQ; delta applied after RC (NVENC guide 8.9) - encode as capability flags |
| C7 | AMD AMF steering missing: AMF has ROI importance (H264 16x16, HEVC/AV1 64x64, 0-10) and HEVC block QP map; ADR-0114 table says "none(*)" and "AMF headers absent" | AMF headers 2026-10-08 vs `docs/adr/0114-encoder-steering.md` lines 129-137 | backends | ADR-0114 text is stale on HEVC BlockQpMap - correct via new ADR (Accepted ADR bodies are frozen) |
| C8 | Intel QSV ROI HW proof missing (crash fixed; driver wall; no numbers) | bench v0.8, README ADR-0146 | backends | Intel also ships experimental SaliencyEncoder and TF level - track |
| C9 | Quality-window stream/zone emitter (metric feedback through supported controls) | #83 | 0.6 | needed for BEAT-7 |
| C10 | Bit-exact CPU reference + cross-device parity gate | #85 exploratory; competitor ecosystem has none either, but bm3dcpu README admits non-identical | 1.0 | differentiator once done |
| C11 | Reconstructed-frame handoff capability table | #84; NVENC recon output (Turing+) exists | 0.6 | |
| C12 | Encode provenance record | #81; vmafx #2142 | 0.5 | |
| C13 | Codec scope: H.264/VVC grain legs, x264 | README step 7 follow-ups | codecs | Note NVENC FGS only AV1; Intel AV1 FG param is decode-side only |
| C14 | `scdet_vulkan` / `fruc_vulkan` / `vqe_amf` landing in FFmpeg master overlap Pelorus scenecut/mc/denoise; rebase and deduplicate where upstream wins | FFmpeg master list sec 2a | 1.0 (upstream-or-own decision) | `fruc_vulkan` optical flow (VK_NV) could feed `mc` as an alternative ME on NVIDIA |
| C15 | No headline numbers against libplacebo deband / hardware denoise on same devices | none exists | 0.6 | prerequisite for BEAT-3/8 |
| C16 | Vulkan encode qmap: 4090 accepts no negative deltas; RADV disabled | README / ADR-0166 | backends | track driver updates |
| C17 | Pelorus filters not in a distribution upstream (patch stack on n9.0.2) | README | post-1.0 | upstreaming list: borderfix, deblock candidates |

### 7. WHITESPACE (nobody has it; evidence of absence)

Absence scope: searched 2026-10-08 in the sources named; "none-found", not proof.

1. **Standardised pre-encode quality-oracle side-data (banding/variance/denoise/grain/motion-confidence/complexity/QP-report) consumed by a metric engine in the same filtergraph.** FFmpeg master `AVFrameSideDataType` list has no such type (frame.h enumerated; closest: REGIONS_OF_INTEREST, VIDEO_ENC_PARAMS, VIDEO_HINT, FILM_GRAIN_PARAMS, SEI_UNREGISTERED). Pelorus uses SEI_UNREGISTERED + UUID. GStreamer 1.28.7 local, DL Streamer README, vs-jetpack README: none.
2. **Per-block honoured-QP feedback of the encoder back into the filter chain as a first-class section.** Vendors expose pieces (NVENC output stats, AMF Statistics/PSNR/SSIM feedback, Intel QualityInfoOutput; Vulkan Video `VK_KHR_video_encode_feedback2` adds picture-level QP min/max/avg and partition status) but no open framework joins them; FFmpeg has no consumer of those fields in the checked encoders (grep nvenc.c/amfenc.c/qsvenc.c for stats consumption not done - (U) for the claim "no consumer").
3. **One ROI/delta-QP producer steering NVENC + QSV + Vulkan Video + libaom + SVT-AV1 (+x265) with measured conformance.** FFmpeg master honours ROI in qsv/vaapi/x265 only (grep); Vulkan quantization-map extension ratified but no FFmpeg consumer found (vulkan_encode.c qpmap grep = 0).
4. **GPU film-grain *estimation* in the filtergraph feeding hardware-encoder grain (NVENC AV1) and an HEVC H.274 SEI BSF.** Estimation exists only offline (libaom `noise_model`, grav1synth `diff`, aomenc/SVT internal denoise); synthesis exists in libplacebo (display) and encoders. No tool found doing GPU estimate -> NVENC `NV_ENC_FILM_GRAIN_PARAMS_AV1`. H.274 FGC insertion for HEVC: no x265 option (cli.rst, `--fullhelp`), FFmpeg `hevc_metadata` BSF not checked (U).
5. **Zero-copy cross-vendor (Vulkan) denoise + deband + MC + grain + dehalo/aa/deblock/borderfix chain.** VS ecosystem is CPU/CUDA with system-RAM pipes; FFmpeg Vulkan set has blur/NLM/scdet only (no temporal denoise, deband, dehalo); libplacebo has deband+grain synthesis but no denoise/MC/estimation.
6. **Encoder-steering by external ME hints measured honestly.** NVENC external ME hints exist in API; no open measurement found; Pelorus v0.9 negative result is itself the only data point.
7. **Bit-exact filter conformance across vendors for pre-encode filters.** None of the competitors claim it; BM3DCUDA README says CPU/CUDA not bitwise identical. (Pelorus #85 is exploratory.)
8. **Provenance record binding filter-chain parameters + encoder settings + metric result.** Not found in FFmpeg, GStreamer, Av1an, ab-av1 docs; only vmafx #2142/#2144 (same org).
9. **LCEVC enhancement-layer facts in a normalised telemetry schema (#87).** FFmpeg has `AV_FRAME_DATA_LCEVC` side data (frame.h) but no telemetry schema found.

### 8. Reproduce

- Local tool versions: `ffmpeg n9.0.2`, `SvtAv1EncApp v4.2.0`, `x265 4.3`, `aomenc` (no `--version`), `rav1e`, `gst-inspect-1.0 1.28.7`, `vspipe R80/API R4.3`.

## Part B: commercial and academic (CC)

### 0. Pelorus own numbers (baseline for the ladder)

Source: `docs/development/bench-results.md` (read 2026-10-08), `README.md`, `docs/principles.md`.

| Capability | Pelorus result | Conditions | Caveat |
| --- | --- | --- | --- |
| Temporal/spatial denoise (GPU `pelorus_denoise_vulkan`) | BD-rate -35.89% (static), -33.95% (BBB high-motion), BD-VMAF +2.18 / +1.96 | hevc_nvenc, seeded synthetic noise, scored vs CLEAN source, VMAF | synthetic noise; clean-reference scoring; VMAF only; one encoder |
| Denoise CPU stand-in (atadenoise) | BD-rate -42.94% (high-motion), -88.94% (static) | same | stand-in, not shipped filter; author flags -89% as best case |
| ROI/banding steering NVENC | CAMBI -41% at +3% bitrate, VMAF +0.10 | hevc_nvenc | NOT iso-bitrate BD-rate; CAMBI not SS2 |
| ROI x265 | CAMBI -47% iso-bitrate and iso-VMAF | libx265 aq-mode 2, 2-pass | synthetic banding source |
| ROI SVT-AV1 | CAMBI -1.5% (CRF35), -0.5% (CRF45), +5.6/+8.0% bytes | libsvtav1 | "honest modest" per doc |
| ROI cross-vendor | NVENC CAMBI -35%, VAAPI AMD -16%, Intel blocked | v0.6 table | not BD-rate |
| ROI global reclaim variant | SSIMULACRA2 BD-rate +13.05% / +19.89% LOSS; rejected ADR-0135 | NVENC | fidelity loss |
| Per-shot QP redistribution | VMAF BD-rate +8.81%, SS2 +7.35% LOSS | CRF 26-38 | rejected |
| HW-vs-SW gap (tuned) | tuned hevc_nvenc vs x265 slow: CPU ahead ~1.8-2.5 SS2; av1_nvenc vs SVT-AV1 p4: ~2.5 SS2 | CQ-locked, clean content | gap in SS2 points, not BD-rate % |
| NVENC ME hints | no speed gain (-2..-3% slower) | RTX 4090 | |
| Grain (FGS) | NO BD-rate number published; estimator built, static-model BSF, per-frame HEVC leg a follow-up | README Step 7 | no measured benefit |
| Scenecut | no number claimed | README Step 9 | |
| QSV ROI | code-complete, no HW proof | README | |
| ABI | v1.3 append-only; QPREPORT section (honoured-QP) | README | HW reader (QSV) blocked; x265 csv reader works |

Key gap: Pelorus has no published headline BD-rate figure on a public corpus with real (non-synthetic) noise, multiple encoders and a perceptual metric vs a named baseline. Competitors' numbers are mostly equally weak (CLAIMED), so the bar is credibility, not magnitude.

### 1. Per-competitor table

Columns: offering | HW encoders | open/closed + price | claim (number, metric, conditions) | flag | independent verification | gaps/criticism | sources.

#### 1.1 Perceptual / AI pre-processing

| Vendor | Offering | HW enc | Open/price | Claim | Flag | Independent check | Gaps | Sources |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| iSIZE BitSave (acq. by Sony Interactive Entertainment, announced 2023-11-02 [F], terms undisclosed) | NN pixel-to-pixel perceptual pre-processor before any encoder; codec-agnostic (AVC/HEVC/VP9 at launch) | encoder-agnostic (software tested) | closed SaaS/SDK; pricing not public | Author: iSIZE; 2019: up to 70% vs non-AI codecs (U, metric unstated, dataset unstated); later 40-60% (website, quoted by Ozer [F], AVC/HEVC/AV1/VVC, metric unstated, dataset unstated), 50% "over state of the art" (U, metric unstated, dataset unstated); Intel doc: "up to 25% on top of any codec" (U, metric unstated, dataset unstated) | CLAIMED | Jan Ozer, Streaming Media 2020-03-27 [F]: 17 clips (movies, animation, sports, gaming), x264 2-pass, BitSave run at 60% rate; BitSave 2nd on PSNR/SSIM after baseline; VMAF "hackable"; did NOT deliver claimed 40% for H.264 vs author's own ffmpeg contrast/unsharp filter; trouble with gaming content. Peer-reviewed: Chadha et al. SMPTE MIJ 131(4) 2022, DOI 10.5594/JMI.2022.3160801 [F]: BD-rate 11-17% (metrics: VMAF, SSIM, AVQT; AVC/HEVC/VVC; dataset unstated in abstract), self-authored | claim baseline unclear (same recipe w/o pre-proc); VMAF gaming risk; no HW-encoder-specific data; no pricing; Sony ownership means roadmap tied to PlayStation (U) | <https://www.streamingmedia.com/Articles/Editorial/Featured-Articles/Review-iSize-BitSave-Video-Preprocessing-139985.aspx> [F]; <https://sonyinteractive.com/en/sony-interactive-entertainment-to-acquire-isize-a-uk-based-company-specializing-in-deep-learning-for-video-delivery/> [F]; <https://journal.smpte.org/periodicals/SMPTE%20Motion%20Imaging%20Journal/131/4/13/> [F]; <https://builders.intel.com/docs/networkbuilders/isize-bitsave-high-quality-video-at-lower-bitrates-via-intel-dl-boost.pdf> (PDF unreadable; "25%" via search summary (U)) |
| Chadha & Andreopoulos DPP (academic root of iSIZE) | rate-aware NN preprocessor, single pass | n/a | paper, no code | Author: Chadha & Andreopoulos (iSIZE / UCL, CVPR 2021) [F]; avg BD-rate -11% over AVC/AV1/VVC (metrics in supplemental: SSIM, VMAF NEG, VMAF; datasets: XIPH and CDVL) | peer-reviewed, self-reported | no independent replication found (U) | no HW encoder tests | <https://openaccess.thecvf.com/content/CVPR2021/supplemental/Chadha_Deep_Perceptual_Preprocessing_CVPR_2021_supplemental.pdf> [F]; <https://mlanthology.org/cvpr/2021/chadha2021cvpr-deep> [F] |
| Ma et al. RPP | rate-perception optimised pre-processing | AVC/HEVC/VVC | paper | Author: Ma et al. (Alibaba, arXiv:2301.10455) [F]; "16.27% bitrate saving" avg over AVC/HEVC/VVC (metric unstated in abstract, dataset unstated in abstract); subjective: 87% rated >= codec-only at ~12% avg bitrate saving | peer-reviewed-ish (arXiv preprint) | none | metric not stated in abstract | <https://arxiv.org/abs/2301.10455> [F] |
| Zhao et al. TDP (ByteDance & Peking University, ICASSP 2024; arXiv:2512.16101, 2025-12-18) | Tri-Dynamic Preprocessing for UGC: pre-analysis (frame-level SI/TI + fast pass x264 QP) + adaptive preprocessing-intensity factor (DPI), adaptive quantization level for codec simulator (DQL), and adaptive lambda RD trade-off (DlamT; only DPI applied at test time) | tested on vvenc (VVC), x265 (HEVC), x264 (AVC) preset medium, CBR [1000, 2500, 4000, 5000] kbps | paper; no open repo found | Author: Zhao et al. (Peking Univ. & ByteDance) [F]; BDBR on 1080p YouTube-UGC (266 videos, 13 categories): VVC (vvenc, Table 1 Avg): -7.14% VMAF_NEG, -12.03% VMAF, -7.78% SSIM, -4.07% MS-SSIM; H.265 (x265, Table 2 Ours): -8.11% VMAF_NEG, -12.35% VMAF, -8.09% SSIM, -3.98% MS-SSIM; H.264 (x264, Table 2 Ours): -7.84% VMAF_NEG, -12.84% VMAF, -8.46% SSIM, -3.08% MS-SSIM | peer-reviewed (ICASSP 2024 proceedings; arXiv 2025-12) [F] | none | requires x264 fast pass + SI/TI pre-analysis; trained on 4x A100 GPUs; no HW encoder evaluation | <https://arxiv.org/abs/2512.16101> [F]; <https://arxiv.org/html/2512.16101v1> [F] |
| Visionular (Aurora, WZ265; "Video Intelligent Encoding Engine": AI pre-processing + CAE + rate control; also HW-encoder optimisation engine for GPU/SoC) | encoder + AI enhance + pre-proc | claims tuning of GPU/SoC encoders | closed, commercial, "contact sales" [F] | Author: Visionular [F]; website: bandwidth/CDN cost -33%..-50% (-33% TV OTT, -40% short drama, -50% short video and surveillance; metric unstated, dataset unstated) [F]; Zoe Liu (AOMedia spotlight [F]): >20% bitrate saving on AV1 high-motion sports videos, ~50-75% bandwidth saving for Aurora RTC (metric: objective/subjective unstated, dataset: sports/RTC); LinkedIn 30-50%, Ant Media up to 40%, NAB 2026 up to 50% (U, metric unstated, dataset unstated) | CLAIMED | none found | no isolated pre-processing number; baselines unstated | <https://www.visionular.com/> [F]; <https://aomedia.org/member%20spotlight/aomedia-member-spotlight-with-visionular-co-founder-and-president-zoe-liu> [F]; <https://www.cdsaonline.org/2024/04/09/visionular-ezdrm-to-deliver-unmatched-video-streaming-efficiency-with-enhanced-security> (fetch failed; via search (U)) |
| Beamr (CABR; 53 patents claimed) | content-adaptive bitrate; closed-loop quality-threshold encode; AVC/HEVC/AV1 | NVIDIA GPU (NVENC) accelerated, 2023-03 NVIDIA partnership [F]; CPU too | closed, sales-gated [F] | Author: Beamr [F]; "up to 50% file size reduction" ("perceptually identical" claim, patented perceptual quality measure + large-scale subjective testing, dataset unstated); VMAF 97.2 4K HDR example (U, password-gated case study, dataset unstated); machine vision: <2% difference from uncompressed baseline object detection at mAP 0.96 [F] | CLAIMED | Ozer (Streaming Learning Center [F]), older product "Beamr Video" (not CABR): 50% avg High Quality mode, 35% avg Best Quality mode (metrics: PSNR, SSIM, VQM + subjective; dataset: test clips, U date) | page admits machine-vision output differs (<2% mAP delta) | <https://beamr.com/cabr> [F]; <https://streaminglearningcenter.com/blogs/beamr-technology-assessment.html> [F]; <https://finance.yahoo.com/news/beamr-teams-nvidia-accelerate-beamr-120000721.html> [F] |
| Harmonic EyeQ | in-loop AI content-aware encoding (CAE) + CBR/VBR; plain AVC/HEVC output, no syntax change | own encoders | closed, appliance/SaaS | Author: Harmonic (via Streaming Media [F]); "up to 50%" avg bitrate reduction vs CBR across typical channel lineup, 60-65% on cinema content (metric: prescribed video quality / HVS model unstated, dataset: OTT channel lineup / cinema) | CLAIMED | none | baseline is CBR (not modern per-title/VBR); 2016-2019 sources; primary PDF unreadable (U) | <https://www.streamingmediaglobal.com/Articles/News/Featured-News/Harmonic-Claims-50-OTT-Performance-Improvements-with-EyeQ--113366.aspx> [F]; <https://www.harmonicinc.com/hubfs/solution-brief/eyeq.pdf> (binary) |
| AWS Elemental MediaConvert "bandwidth reduction filter" (2023-06-21) | perceptual input pre-filter, removes temporal noise; AVC/HEVC | AWS software encoders | closed, per-minute AWS pricing (U) | Author: AWS [F] (no numeric % on MediaConvert page; metric unstated, dataset: SDR 4K/HD video); third-party blog (Shunya Shimizu, Classmethod 2023-06-30 [F]): up to ~8% (8.38% bitrate reduction, 7,999 -> 7,329 kbps at QVBR; metric: visual quality, dataset: single 1080p iPhone XS clip, Japanese) | CLAIMED (qualitative) | single blog [F] | no BD-rate; cannot combine with Noise reducer on AVC (U) | <https://aws.amazon.com/about-aws/whats-new/2023/06/aws-elemental-mediaconvert-bandwidth-reduction-filter-hevc-avc> [F]; <https://dev.classmethod.jp/articles/aws-elemental-mediaconvert-bandwidth-reduction-filter/> [F] |
| AWS MediaLive bandwidth reduction filter (2024-09-23) | same filter live | Enhanced AVC/HEVC | included "at no additional cost" [F] | Author: AWS [F]; "improves video encoding efficiency by an average of 7%" (metric unstated, dataset unstated) | CLAIMED, avg | none | metric/corpus unspecified | <https://aws.amazon.com/about-aws/whats-new/2024/09/aws-elemental-medialive-bandwidth-reduction-filter> [F] |
| AWS MediaConvert AV1 film grain synthesis | denoise + AV1 FGS; mutually exclusive with Noise reducer | AV1 software | closed | Author: AWS [F]; recommended Enabled to reduce bandwidth for QVBR quality level 5, 6, 7, or 8 outputs (metric: QVBR quality level, dataset unstated) | CLAIMED | none | | <https://docs.aws.amazon.com/sdk-for-kotlin/api/latest/mediaconvert/aws.sdk.kotlin.services.mediaconvert.model/-a-v1-settings/film-grain-synthesis.html> [F] |
| Synamedia Quality-Aware Video Compression | quality-aware encode | own | closed | "operational cost savings up to 40%" | CLAIMED | none | baseline/method unstated | <https://www.synamedia.com/wp-content/uploads/VN-Quality-Aware-Compression-AAG.pdf> (via search (U)) |
| Ateme TITAN / KYRION | encoders; content-aware | own | closed | no verifiable pre-processing number found; a third-party page cites "up to 50%" (U) | CLAIMED (U) | none | no AI pre-proc primary found; Netflix live deal 2026-03 | <https://www.ateme.com/press/netflix-goes-live-with-ateme/> ; <https://www.trendingaitools.com/ai-tools/ateme-ai/> (low authority) |
| MediaKind | nothing found | - | - | - | (U) | - | - | search returned nothing |
| Bitmovin | per-title, per-shot, ML-based ladder; no pre-filter product found | software | closed | per-title + 3-pass: TCO -30% (beta marketing); per-title+per-shot up to 20% vs fixed segment; Seven.One case: up to 60% bandwidth (H.264, VMAF 92) | CLAIMED | none | not a pre-filter | <https://bitmovin.com/blog/per-title-encoding-savings/> ; <https://patents.google.com/patent/US12108055> |
| Brightcove Context Aware Encoding (2017) | per-asset ladder | software | closed | Author: Brightcove (via Streaming Media [F]); up to 50% storage and delivery costs [F], avg 40% bandwidth (U) (metric unstated, dataset: early customer data) | CLAIMED | none | 2017 beta estimates | <https://www.streamingmedia.com/Articles/News/Online-Video-News/Brightcove-Announces-Context-Aware-Encoding-Up-to-50-Savings-118356.aspx> [F] |
| Mux Instant Per-Title | per-title ladder | software | closed | Author: Mux (via Streaming Media [F]); video file sizes -30%, video quality +15% (2018; metric unstated, dataset unstated) | CLAIMED | none | | <https://www.streamingmedia.com/PressRelease/Mux-Launches-Instant-Per-Title-Encoding-Improving-Video-Quality-For-Everyone_47002.aspx> [F] |
| WaveOne (Apple, closed Jan 2023; undisclosed) | learned codec/compression | n/a | acquired, product ended | none found | - | - | | <https://9to5mac.com/2023/03/27/apple-ai-acqusition-waveone/> |
| Deep Render (InterDigital, announced 2025-10-30 [F]) | learned codec; patents transferred | n/a | acquired | Author: Deep Render (via SaaS News [F]); up to $1.6B/yr CDN savings estimate for streaming services such as Netflix (2023, not a bitrate number; metric: CDN cost, dataset unstated) | CLAIMED | none | | <https://seekingalpha.com/pr/20285668-interdigital-acquires-ai-startup-deep-render> [F]; <https://www.thesaasnews.com/news/deep-render-raises-9-million-in-funding> [F] |
| NVIDIA Maxine VFX (Artifact Reduction, Super Resolution, Upscaler) | NN enhancement SDK, usable pre-encode in principle | NVIDIA GPU | proprietary SDK, free tier on NGC (U) | no bitrate claim for pre-encode use; 2020 video-call face codec "10x" is a different feature | CLAIMED, off-topic | none | not positioned as pre-encode; Artifact Reduction is post-decode | <https://developer.nvidia.com/blog/transforming-noisy-low-resolution-into-high-quality-videos-for-captivating-end-user-experiences/> ; <https://github.com/nvidia/MAXINE-VFX-SDK> |
| NVIDIA NVENC Video Codec SDK 13.0 (Blackwell) | in-encoder temporal filtering + lookahead (UHQ); AV1 UHQ w/ 7 B-frames [F] | NVIDIA | proprietary SDK | Author: NVIDIA; "comparable to software AV1 at ~3x throughput" (U, metric unstated, dataset unstated); FFmpeg temporal filter ~4-5% (Phoronix, U, metric unstated, dataset unstated); SDK 13.0 blog [F] gives NO BD-rate numbers in text (charts only for natural content), no film grain or emphasis-map mention [F] | CLAIMED | MSU 2025 HW report [F]: NVENC AV1 (RTX 5060 Ti) top GPU encoder in all speed classes (30/60/120/240 fps on 50 FullHD sequences, BSQ-rate over YUV-SSIM, Y-VMAF 0.6.1, Y-VMAF-NEG 0.6.1; gap numbers in charts) | UHQ narrows the very gap Pelorus targets; moves temporal denoise inside the encoder | <https://developer.nvidia.com/blog/nvidia-video-codec-sdk-13-0-powered-by-nvidia-blackwell/> [F]; <https://compression.ru/video/codec_comparison/2025/hardware_report.html> [F] |
| NVIDIA NVSaliENC (saliency model integrated with NVENC) | saliency -> ROI for NVENC | NVIDIA | proprietary, NGC catalog | no eval numbers on catalog page | (U) | none | closest commercial analogue to Pelorus ROI steering; vendor-locked | <https://catalog.ngc.nvidia.com/orgs/nvidia/multimedia/models/nvsalienc/-/file-browser> |
| Topaz Video AI | NN denoise/upscale, desktop; ffmpeg-ish pipeline | exports to NVENC/QSV/AMF/software | closed, paid licence | no bitrate claim | - | forum users advise lossless intermediate + software encode (anecdote) | not rate-aware; no steering | <https://www.topazlabs.com/tools/video-denoiser> ; <https://community.topazlabs.com/t/how-does-topaz-video-ai-work-particularly-in-relation-to-ffmpeg/45138> |
| Dolby Hybrik / Dolby.io Media Enhance | nothing relevant found | - | - | - | (U) | - | search returned nothing usable | - |
| YouTube / Google | no published pre-encode-denoise blog found | - | - | - | (U) | - | patent US12273533 (below) is the only trace | - |
| Meta | AV1 rollout; convex-hull ABR 7 res x 5 CRF = 35 encodes; HW AVC encode (own ASIC) for first pass only; no pre-encode denoise post found | own ASIC (MSVP) first pass | internal | Author: Meta [F]; AV1 vs AVC: bitrate -65% in one VOD example (-48% vs VP9), avg bitrate -12% on Facebook Reels iPhone, playback FB-MOS +0.6; newer-generation standard typically reduces bandwidth ~30-50% vs predecessor (metrics: FB-MOS / bitrate, dataset: Facebook & Instagram Reels iOS production) | CLAIMED (internal) | none | | <https://engineering.fb.com/2023/02/21/video-engineering/av1-codec-facebook-instagram-reels/> [F] |

#### 1.2 Grain: production and prior art

| Item | What | Number | Flag | Source |
| --- | --- | --- | --- | --- |
| Netflix AV1 FGS (blog 2025-07-02, rollout since March 2025) | denoise -> AR grain model + piecewise-linear scaling; 64x64 template, random 32x32 patches; encoder picks denoiser | Author: Netflix (via noise.getoto.net mirror [F]; primary netflixtechblog.com returned 403); ~300 Netflix catalog titles: avg bitrate -36% at >=1080p, ~-10% below 1080p (metric: avg bitrate reduction; NO grain quality metric exists, PSNR/VMAF penalise grain); example frame 8274 -> 2804 kbps (-66%); production A/B: start bitrate -24%, avg bitrate -31.6%, rebuffer -10%; device certification is biggest rollout challenge | CLAIMED (vendor, large production A/B) | <https://noise.getoto.net/2025/07/02/av1-scale-film-grain-synthesis-the-awakening/> [F] (mirror; netflixtechblog.com returned 403) |
| Grois et al. (Ateme) SMPTE MIJ 133(1) Jan 2024 | AV1-style grain via ITU-T T.35 SEI in HEVC | Author: Grois et al. (Ateme) [F]; "significant bitrate savings" on popular cinematic content (numbers/metric/dataset paywalled, (U)) | peer-reviewed, numbers (U) | <https://journal.smpte.org/periodicals/SMPTE%20Motion%20Imaging%20Journal/133/1/16/> [F] |
| ITU-T H.Sup21 (01/2025) | film grain synthesis technology, end-to-end steps | spec guidance | standard | <https://itu.int/dms_pubrec/itu-t/rec/h/T-REC-H.Sup21-202501-I!!SUM-HTM-E.htm> |
| FGA-NN, Ameur et al. (InterDigital), arXiv:2506.14350 (2025-06-17) | first learned grain ANALYSIS giving conventional (AV1-style) parameters | no numbers in abstract | peer-review pending | <https://arxiv.org/abs/2506.14350> [F] |
| Norkin, AV1 film grain (DCC 2018) | AV1 FGS design | Author: Norkin & Birkbeck (Netflix / Google) [F]; preliminary results: bitrate savings "up to 50%" on sequences with heavy film grain (metric unstated in abstract/intro, dataset: sequences with heavy film grain) | peer-reviewed | <https://norkin.org/pdf/DCC_2018_AV1_film_grain.pdf> [F] |
| SVT-AV1 `film-grain`, `film-grain-denoise` | built-in encoder-side estimation | Author: SVT-AV1 docs (`CommonQuestions.md` [F]): `film-grain` level ~8 for normal live-action grain, ~4 for standard 2-D animation, 10-15 for noisier video (metric unstated, dataset unstated); community blog (Author: Mason K, dev.to, (U) secondary): 4-8 | tool | <https://gitlab.com/AOMediaCodec/SVT-AV1/-/raw/master/Docs/CommonQuestions.md> [F]; <https://dev.to/masonwritescode/turn-on-av1-film-grain-synthesis-and-measure-what-it-saves-on-your-own-footage-37bb> ((U) secondary) |
| HW decoder FGS support | anecdotes: some HW decoders skip grain (FireTV 4K Max, Amlogic) | - | anecdote (U) | <https://forum.doom9.org/archive/index.php/t-182011.html> |

#### 1.3 Academic: pre-processing, denoise, ROI, encoder-in-loop

| Item | Result | Flag | Source |
| --- | --- | --- | --- |
| Encoding in the Dark challenge (Anantrasirichai et al.) arXiv:2005.03315 | Author: Anantrasirichai et al. (Univ. of Bristol, ICMEW 2020) [F]; VVC alone achieves high performance compared to simply denoising the video source prior to encoding (metric unstated in abstract, dataset: low-light video sequences) | peer-reviewed benchmark; counter-evidence to naive pre-denoise | <https://arxiv.org/abs/2005.03315> [F] |
| Pre+post filtering 10-30% | Author: Huawei Munich Tech Arena 2025 brief [F]; cites unspecified empirical evidence that pre-processing + post-processing filters give 10% to 30% compression performance gains on HEVC/H.265 (metric unstated, dataset: YUV 4:2:0 10-bit challenge set) | secondary (U) | <https://huawei.agorize.com/en/challenges/2025-munich-tech-arena/pages/topic-description-video-compression?lang=en> [F] |
| Saliency/ROI QP | only software HEVC/H.264 studies; no BD-rate for NVENC saliency pipeline found; patents note fixed QP offset can backfire on talking-head content | evidence of absence (searched, not exhaustive) | <https://patents.google.com/patent/US12341977> ; <https://doi.org/10.3390/app14093823> |
| Netflix Dynamic Optimizer | Author: Netflix / Katsavounidis et al.; VMAF-in-loop, per-shot; 4K blog (via noise.getoto.net mirror [F]): BD-rate 50% gains on average over fixed-bitrate ladder (metric: 4K VMAF BD-rate, dataset: Netflix 4K catalog); earlier talk transcript: 17% bitrate / +3.7 VMAF (U, dataset unstated) | CLAIMED/peer-reviewed (SPIE 10752, DOI 10.1117/12.2322118) | <https://medicalimaging.spiedigitallibrary.org/conference-proceedings-of-spie/10752/107520Q/Video-codec-comparison-using-the-dynamic-optimizer-framework/10.1117/12.2322118.full> ; <https://noise.getoto.net/2020/08/28/optimized-shot-based-encodes-for-4k-now-streaming/> (mirror [F]) |
| MSU Video Codecs Comparison 2025 (hardware), released 2026-05-25 | Author: MSU Graphics & Media Lab (Vatolin et al.) [F]; 41 HW encoders (NVENC, QSV, AMF, Netint, Tencent...), 4 speed usecases (30/60/120/240 fps); metrics: BSQ-rate (enhanced BD-rate) over YUV-SSIM, PSNR, Y-VMAF 0.6.1, Y-VMAF-NEG 0.6.1; dataset: 50 FullHD video sequences; software refs only x264/x265; gap numbers only in charts / enterprise tier; no pre-processing tested | MEASURED (independent; full data paywalled) | <https://compression.ru/video/codec_comparison/2025/hardware_report.html> [F] |
| Zhao et al. Machine Vision Preprocessing (ByteDance & Peking University, DCC 2024; arXiv:2512.15331, 2025-12-17) | Author: Zhao et al. (Peking Univ. & ByteDance) [F]; neural preprocessor + differentiable virtual codec (training) tested with standard x264 (H.264) and x265 (H.265) preset medium QP [30,35,40,45,50]: >15% bitrate saving vs standard codec anchor; video action recognition (Kinetics400, Table 1): Slow-only top-1 BD-Rate -17.6% (H.264) / -16.2% (H.265), VMAF BD-Rate -5.6% / -5.1%; video object tracking (GOT-10K, Table 2): up to 17.5% bitrate saving (ATOM AUC BD-Rate -17.2% H.264 / -16.6% H.265, VMAF BD-Rate -6.3% / -5.7%) | peer-reviewed (DCC 2024 proceedings; arXiv 2025-12) [F] | <https://arxiv.org/abs/2512.15331> [F]; <https://arxiv.org/html/2512.15331v1> [F] |

### 2. Claims ladder

Strongest published savings by capability, with what Pelorus must measure to match or beat. Pelorus must use SSIMULACRA2 primary + VMAF secondary + CAMBI for banding (per bench-results v0.17), CQ-locked iso-bitrate ladders, and real (non-synthetic) noisy content.

| # | Capability | Strongest number (flag) | Pelorus today | Must measure to match/beat |
| --- | --- | --- | --- | --- |
| L1 | Film grain synthesis (AV1) | Netflix: -36% avg bitrate >=1080p over ~300 titles; -66% single frame; -10% <1080p (CLAIMED, production A/B, no grain metric) | no number; estimator + static BSF | metric: SS2 + VMAF-vs-source + a grain-aware proxy (power spectrum / blind-observer subjective, since PSNR/VMAF penalise grain); corpus: >=30 grainy 1080p+/4K titles (public: film-grain clips, Xiph/AOM CTC grain set (U)); encoder: SVT-AV1 + libaom with FGS on/off, and av1_nvenc grain path; target: reproduce >=30% at >=1080p, quote <1080p separately |
| L2 | FGS in HEVC via SEI | Grois (Ateme) SMPTE 2024 "significant" (numbers paywalled, (U)) | `pelorus_fgs` BSF static only | x265 + H.274 FGC SEI and T.35 AV1-style; decoder conformance (which decoders apply it); BD-rate on grainy HEVC corpus; need per-frame estimator->BSF bridge first |
| L3 | NN perceptual pre-processing | iSIZE 40-70% CLAIMED; peer-reviewed 11-17% BD-rate (Chadha 2022); 11% (CVPR 2021); RPP 16.27% (arXiv 2023); Zhao et al. TDP (ICASSP 2024, arXiv:2512.16101 [F]) -7.14%..-8.11% VMAF_NEG, -12.03%..-12.84% VMAF on 1080p YouTube-UGC (266 videos, 13 categories) | not offered (classical denoise/deband only) | metric: BD-rate on VMAF + SS2 + AVQT-like + subjective, matching Chadha's protocol (AVC/HEVC/VVC, JVET-style sequences); must also report with VMAF-NEG to prove not gaming; target: >=11% on clean JVET CTC without synthetic noise |
| L4 | Temporal/spatial denoise before encode | Pelorus own -34..-36% (synthetic noise, vs clean ref); AWS 7% avg (MediaLive, CLAIMED); AWS up to 8% (U); Beamr/Harmonic 50% (CLAIMED, different mechanism) | -34/-36% synthetic | real noisy corpus (low-light, UGC, sensor-noise; Encoding-in-the-Dark set, arXiv:2005.03315); score vs the NOISY source with SS2/VMAF-NEG AND vs clean where available; baseline = tuned encoder w/o filter AND encoder's built-in temporal filter (NVENC UHQ, x265 `--nr`, SVT-AV1 TF). Target: beat AWS 7% on identical-class content and show if gain survives NVENC Blackwell temporal filtering |
| L5 | Closed-loop content-adaptive (CABR) | Beamr/Harmonic "up to 50%" CLAIMED; Netflix DO ~17% (talk) .. ~50% (4K ladder, secondary) ; Ozer 35-50% for Beamr Video (older) | per-shot redistribution LOST (+8.81% VMAF BD-rate) | VMAF-in-loop via vmafx (autotune) with CQ-locked iso-quality bisection; report bitrate at fixed SS2/VMAF vs fixed-CRF; corpus incl. Netflix Open Content; compare to per-title baseline |
| L6 | ROI / saliency steering on HW encoders | no published BD-rate for HW saliency steering (absence); Pelorus NVENC banding CAMBI -41% (own) | CAMBI wins, SS2 BD-rate losses (+13..+20%) in reclaim variant | ROI-region metric (SS2/VMAF on ROI mask) + global BD-rate; per-vendor iso-bitrate; honoured-fraction via QPREPORT; shows steering helps where saliency/banding exists without global loss |
| L7 | HW-vs-SW gap closure | NVIDIA: Blackwell AV1 UHQ "comparable to software AV1 at ~3x throughput" (CLAIMED); MSU 2025 ranks NVENC AV1 first among GPUs (MEASURED, charts) | CPU ahead ~1.8-2.5 SS2 (tuned) | report the gap as BD-rate % (SS2 and VMAF) for {tuned NVENC, tuned NVENC + Pelorus} vs {x265 slow, SVT-AV1 p4} on a shared public corpus; claim "closes N% of gap" with CI |
| L8 | Per-title / shot ladders | Bitmovin per-shot up to 20%; Brightcove 40% avg; Mux 30% (all CLAIMED) | out of scope | not Pelorus's lane; keep out of claims |
| L9 | Banding | no commercial claim found | CAMBI -35..-47% | SS2 + CAMBI + subjective; stay inside own lane |
| L10 | Machine vision video pre-processing | Zhao et al. (DCC 2024, arXiv:2512.15331 [F]) >15% bitrate saving (up to -17.6% H.264 / -16.2% H.265 action recognition top-1 BD-Rate on Kinetics400; up to 17.5% / -17.2% H.264 / -16.6% H.265 object tracking ATOM AUC BD-Rate on GOT-10K) vs standard x264/x265 anchor | not offered | metric: downstream task accuracy (top-1/AUC/mAP) vs bitrate; evaluate whether Pelorus denoise/deband degrades downstream machine vision models (Beamr admits <2% mAP delta) |

### 3. CLOSE list (capabilities competitors have, Pelorus lacks)

| # | Gap | Who | Roadmap placement |
| --- | --- | --- | --- |
| C1 | Published BD-rate on public corpus with real noise + multi-encoder + SS2/VMAF/CAMBI, reproducible script | all (Chadha, Netflix) | 0.4 schemas (result schema) then 0.6 conformance |
| C2 | Per-frame grain estimator -> encoder bridge (AV1 native side data exists; HEVC SEI is static) | Netflix, Ateme/Grois | 0.5 interchange & live integration |
| C3 | Grain quality evaluation without VMAF/PSNR (Netflix admits none exists) | Netflix | 0.6 conformance |
| C4 | NN rate-aware pre-processor (learned) | iSIZE, RPP, Visionular; Zhao et al. TDP (ICASSP 2024, arXiv:2512.16101 [F]) demonstrates -7.14%..-8.11% VMAF_NEG on 1080p YouTube-UGC | post-1.0 (or backends if ONNX/Vulkan-ML added); weights licence needs ADR |
| C5 | Closed-loop quality-threshold encode (CABR-style) beyond current autotune | Beamr, Harmonic, Netflix DO | 0.5 (live integration with vmafx autotune), 0.6 (encoder feedback) |
| C6 | Hardware-agnostic live/real-time deployment (appliance, MediaLive-style managed filter) | AWS, Harmonic, Beamr | 0.5 |
| C7 | Saliency detection feeding ROI (NVSaliENC analogue) | NVIDIA | 0.5; codecs/backends |
| C8 | Honoured-QP telemetry from HW encoders (QSV, NVENC per-block) | none ships it; Pelorus has schema only; Khronos ratified VK_KHR_video_encode_feedback2 (2026-02-04, Ext 599 [F]) providing picture-level min/max/avg QP and partition stats in Vulkan Video | 0.6 encoder feedback |
| C9 | Decoder-side FGS conformance matrix (which devices apply grain) | Netflix (internal) | 0.6 conformance |
| C10 | Temporal filtering parity with encoder-internal TF (NVENC Blackwell UHQ, x265, SVT-AV1) as baseline | NVIDIA | 0.4 (baseline definition) |
| C11 | H.274/VVC and H.264 grain legs | Ateme (HEVC), AWS (AV1) | codecs |
| C12 | AMF / VAAPI-AMD / Intel proof | MSU shows these encoders matter | backends |
| C13 | Pricing/support/SLAs/SaaS delivery | Beamr, iSIZE, Visionular | non-goal (open project); note in positioning |
| C14 | Low-light/UGC denoise robustness (AWS filter targets it) | AWS; Zhao et al. TDP (ICASSP 2024, arXiv:2512.16101 [F]) benchmarks 1080p YouTube-UGC (266 videos, 13 categories) and reduces UGC bad cases (BDBR > 0) | 0.6 |
| C15 | Machine vision downstream task evaluation (verify pre-filter does not degrade computer vision models) | Beamr (<2% mAP delta), Zhao et al. (DCC 2024, arXiv:2512.15331 [F]) | 1.0 |

### 4. WHITESPACE (no one offers; evidence of absence)

Evidence of absence = searches run 2026-10-08 (WebSearch, standard mode) found no product or paper; not exhaustive. Revisit before using in public claims.

| # | Whitespace | Evidence of absence |
| --- | --- | --- |
| W1 | Open-source, multi-vendor GPU pre-encode filter set inside FFmpeg (Vulkan) | competitors found are all closed (iSIZE, Visionular, Beamr, Harmonic, AWS); NVIDIA Maxine proprietary; no open Vulkan equivalent surfaced |
| W2 | Verifiable ROI honouring (requested vs honoured QP per block, standard section) | search "NVENC emphasis map honored accuracy study" returned no measurement study; only implementations and forum questions |
| W3 | Interchangeable grain models (AV1 AR/ H.274 FGC / T.35 from one estimator) | Ateme (Grois) shows AV1-style in HEVC SEI but closed; FGA-NN is analysis-only; no open cross-codec estimator found |
| W4 | Encoder-agnostic telemetry standard (per-frame QP/bits/honoured fraction) | none found; Meta uses internal ASIC stats (not standardised); Khronos ratified VK_KHR_video_encode_feedback2 (Ext 599, 2026-02-04 [F]) exposing picture min/max/avg QP and partition byte stats in Vulkan Video, but open filtergraph telemetry integration remains missing |
| W5 | Provenance of pre-filter chain (which filters, params, versions travel with the stream) | no vendor describes it; Netflix FGS carries grain params only |
| W6 | Bit-exact cross-vendor filters (deterministic hash-based PRNG; same output on NVIDIA/AMD/Intel) | none found; NN filters are not bit-exact across vendors by nature |
| W7 | Zero-copy path into an open quality oracle (Vulkan frame -> VMAF) | none found; vmafx/Pelorus interop unique |
| W8 | Published negative results (reclaim variant +13..+20% SS2 loss; per-shot redistribution loss) | vendors publish only gains; Ozer 2020 is the sole independent critique found |
| W9 | Independent, reproducible, metric-hack-aware methodology (SS2 + VMAF flag; VMAF-NEG) | Ozer warned about VMAF hackability in 2020; academic pre-processors (Chadha CVPR 2021 supplemental [F], Zhao et al. TDP ICASSP 2024 arXiv:2512.16101 [F]) report VMAF-NEG alongside VMAF, but no commercial vendor publishes a VMAF-NEG guard; Pelorus pairs SSIMULACRA2 primary + CAMBI + VMAF-NEG in an open Vulkan pipeline |
| W10 | HW-encoder gap measured in BD-rate with named corpus | MSU gap numbers are in paywalled charts; NVIDIA gives no BD-rate numbers in the Blackwell blog |
| W11 | Combined human-perceptual and machine-vision dual-guaranteed pre-processing | Zhao et al. (DCC 2024, arXiv:2512.15331 [F]) targets machine vision (action recognition on Kinetics400, object tracking on GOT-10K) with VMAF side-reporting; Beamr CABR reports machine vision (<2% mAP delta); no open Vulkan pipeline guarantees both |

### 5. Positioning risks

#### 5.1 Patents (found; not legal advice; claim scope unread beyond abstracts)

| Patent | Holder | Topic | Dates | Relevance | Source |
| --- | --- | --- | --- | --- | --- |
| US10986363B2 | Beamr Imaging | pre-processing before encode: align frames by shift, adaptive pre-filter by encoding complexity, or film-grain removal | priority 2016-09-14, granted 2021-04-20, est. expiry 2037-10-02 | broad: overlaps denoise/grain-removal-before-encode; claim text NOT read (U) | <https://patents.google.com/patent/US10986363B2/en> [F] |
| US12273533B2 | Google (Izadi, Adsumilli) | encode reference + candidate filtered copies, pick lowest cost (distortion+rate) | priority 2018-12-24, granted 2025-04-08, est. expiry 2039-11-04 | overlaps filter selection by trial encode (autotune/vmafx loop) if implemented that way | <https://patents.google.com/patent/US12273533B2/en> [F] |
| US12413800B2 | ATI (AMD) | pre-processing strength, resize, scene-change sensitivity set from target bitrate | priority 2022-06-21, granted 2025-09-09, est. expiry 2043-03-29 | overlaps bitrate-dependent pre-filter strength / scenecut sensitivity | <https://patents.google.com/patent/US12413800B2/en> [F] |
| US12413789B2 | InterDigital | estimate film grain scaling factor from grain blocks vs filtered blocks | priority 2021-07-01, granted 2025-09-09 | directly overlaps `pelorus_grain_estimate` scaling estimation approach | <https://patents.google.com/patent/US12413789B2/en> [F] |
| EP 3678097 | Dolby Intl | film grain simulation | revoked by EPO 2022-11-07 | revoked in EP; US family status unchecked (U); Dolby says it never accepted AOM Patent License 1.0 and sued Snap (Delaware) | <https://www.unifiedpatents.com/insights/2022/11/14/interdigitaldolby-european-av1-patent-revoked> [F]; <https://daejeonchronicles.com/2023/11/03/av1-film-grain-synthesis-aom-patent-license-1-0-dolby/> |
| Sisvel AV1 pool | 21 owners, >1,900 patents (Sisvel) | AV1 | - | licensing exposure for the AV1 film-grain/NVENC path rests with the adopter, but marketing "royalty-free AV1" is contested | <https://www.sisvel.com/insights/sisvel-has-licensed-half-the-av1-market-while-bringing-much-needed/> |
| iSIZE | "patent-pending" 2019-2020; no grant found; ownership likely Sony now (U) | NN perceptual pre-proc | - | portfolio unknown; check USPTO assignee "iSIZE Technologies" / Sony Interactive Entertainment | (search; none found) |
| Deep Render | transferred to InterDigital (2025-10-30) | AI codec | - | unrelated to pre-filter, but InterDigital now holds film-grain-estimation + AI-codec patents | <https://seekingalpha.com/pr/20285668-interdigital-acquires-ai-startup-deep-render> |

Counsel review required: this list reads abstracts and bibliographic data only, not claim text, and is not legal advice. A freedom-to-operate review by counsel of the Beamr US10986363 and InterDigital US12413789 claims against `pelorus_denoise` and `pelorus_grain_estimate` has to finish before any quality claim is published (roadmap item #88). Do not cite expiry dates as legal fact (Google Patents marks them an assumption).

#### 5.2 Unverifiable competitor marketing (do not benchmark against as if measured)

- iSIZE 40-70%: baseline = same recipe without pre-processing; Ozer found 40% not delivered (MEASURED, single test, 2020, VMAF-hackable).
- Beamr "perceptually identical" / "guarantee" (CABR page): not independently verified; page itself shows non-identical machine-vision output.
- Harmonic "up to 50%": baseline CBR; 2016-2019 sources.
- Visionular 30-50%: no baseline, no corpus.
- Brightcove 40-50% (2017 beta), Mux 30% (2018), Bitmovin 20-60% (beta/case): stale.
- Netflix -36%: large but vendor-run, no grain-specific metric (they say so), device-dependent; do not use as a Pelorus target for HW AV1 without own measurement.
- AWS 7% (MediaLive) "average" with no corpus.
- NVIDIA "comparable to software at 3x": no BD-rate number in blog text.

#### 5.3 Pelorus risks

- R1: Pelorus numbers are synthetic-noise, clean-reference, one-encoder; vendors will be held to same bar by Ozer-style reviewers. Publish methodology + negative results (W8) as the differentiator.
- R2: Encoder-internal temporal filtering (NVENC Blackwell UHQ, AV1/H.264 TF in FFmpeg ~4-5% (U)) can erase denoise gains; baseline must include it. The 2020 Encoding-in-the-Dark result (VVC beat denoise-then-encode) is a documented counter-case.
- R3: README states `av1_nvenc` carries grain via `NV_ENC_FILM_GRAIN_PARAMS_AV1`; The symbol could not be confirmed from public NVIDIA docs/blog (SDK 13 blog does not mention film grain [F]; search found nothing). Verify against installed `nvEncodeAPI.h` before external claims (U).
- R4: HW decoder grain support is patchy (anecdotes) - FGS wins are device-dependent.
- R5: Metric gaming - Pelorus already moved to SS2 primary; keep VMAF-NEG-style guard visible (Ozer 2020 showed contrast +25% raises VMAF ~13 points while SSIM falls).
- R6: Acquisitions (Sony/iSIZE, Apple/WaveOne, InterDigital/Deep Render) consolidate NN pre-proc and AI-codec IP; expect patent assertion risk for learned approaches; Pelorus's classical filters are lower risk but see 5.1.
- R7: Dolby's stance on AOM licence leaves AV1/FGS royalty-freedom contested; frame as "signals AV1 FGS", do not warrant royalty-free.

### 6. Source access notes

- netflixtechblog.com 403; used noise.getoto.net mirror (full text) - treat as mirror.
- Intel iSIZE PDF, Harmonic EyeQ PDF: binary, unreadable by tool; figures from search summaries (U).
- Visionular pages partly zh; cdsaonline fetch failed (ECONNREFUSED).
- Searches not yielding: MediaKind, Dolby Hybrik/Dolby.io, YouTube pre-encode denoise, Ateme AI pre-processing primary, Meta pre-encode denoise.
- No independent MEASURED BD-rate for any commercial pre-processor other than Ozer 2020 (iSIZE) and Streaming Learning Center (Beamr Video, older) located.
- 2026-10-18 refresh: `PKU-ICST-MCL/TDP`, `tarak/grav1synth`, and `nvidia/MAXINE-VFX-SDK` returned HTTP 404 on GitHub; canonical repositories verified for `rust-av/grav1synth` and `rust-av/Av1an`.

## Refresh log 2026-10-18

- Part A Sec 3.1: `libplacebo (library)`: updated stars (794 -> 798), pushed date (2026-10-05 -> 2026-10-18), added latest release v7.360.1 [F]; <https://github.com/haasn/libplacebo>.
- Part A Sec 3.2: `neo_f3kdb`: updated stars (42 -> 41), added latest release r11 [F]; <https://github.com/HomeOfAviSynthPlusEvolution/neo_f3kdb>.
- Part A Sec 3.2: `vs-placebo Deband`: updated pushed date (2026-10-08 -> 2026-10-18), added latest release 2.0.4 [F]; <https://github.com/Lypheo/vs-placebo>.
- Part A Sec 3.2: `vs-dfttest2`: added latest release v11 [F]; <https://github.com/AmusementClub/vs-dfttest2>.
- Part A Sec 3.2: `VapourSynth-BM3DCUDA`: added latest release R2.17 [F]; <https://github.com/WolframRhodium/VapourSynth-BM3DCUDA>.
- Part A Sec 3.2: `KNLMeansCL`: added latest release v1.1.1 [F]; <https://github.com/Khanattila/KNLMeansCL>.
- Part A Sec 3.2: `vapoursynth-mvtools`: updated stars (223 -> 221), added `pinterf/mvtools` stats (191 stars, pushed 2026-04-24, release 2.7.47) [F]; <https://github.com/dubhater/vapoursynth-mvtools> ; <https://github.com/pinterf/mvtools>.
- Part A Sec 3.2: `havsfunc`: added latest release r34 [F]; <https://github.com/HomeOfVapourSynthEvolution/havsfunc>.
- Part A Sec 3.2: `vs-jetpack`: updated pushed date (2026-10-05 -> 2026-10-17), added latest release v2.2.6 [F]; <https://github.com/Jaded-Encoding-Thaumaturgy/vs-jetpack>.
- Part A Sec 3.2: `vs-mlrt`: updated stars (483 -> 486), added latest release v15.16 [F]; <https://github.com/AmusementClub/vs-mlrt>.
- Part A Sec 3.2: `EdgeFixer`: added latest release r3 [F]; <https://github.com/sekrit-twc/EdgeFixer>.
- Part A Sec 3.2: `zimg`: added pushed date (2026-09-04) and latest release release-3.0.6 [F]; <https://github.com/sekrit-twc/zimg>.
- Part A Sec 3.2: `AviSynth+`: updated stars (1208 -> 1211), added pushed date (2026-10-15) and latest release v3.7.5 [F]; <https://github.com/AviSynth/AviSynthPlus>.
- Part A Sec 3.3: `SVT-AV1`: updated GitHub mirror stars (79 -> 82) and pushed date (2026-08-09) [F]; <https://github.com/AOMediaCodec/SVT-AV1>.
- Part A Sec 3.3: `svt-av1-psy`: added latest release v3.0.2 [F]; <https://github.com/psy-ex/svt-av1-psy>.
- Part A Sec 3.3: `rav1e`: updated stars (4159 -> 4161), pushed date (2026-10-08 -> 2026-10-17), added latest release v0.8.1 [F]; <https://github.com/xiph/rav1e>.
- Part A Sec 3.3: `x265`: updated GitHub mirror stars (812 -> 814) [F]; <https://github.com/videolan/x265>.
- Part A Sec 3.3: `x264`: verified `quortex/x264-pVMAF` (26 stars, pushed 2026-02-06) [F]; <https://github.com/quortex/x264-pVMAF>.
- Part A Sec 3.3: `Av1an`: updated to canonical repo `rust-av/Av1an`, stars (1996 -> 2000), pushed date (2026-09-28 -> 2026-10-16), latest release v0.5.2, structured claim cell [F]; <https://github.com/rust-av/Av1an>.
- Part A Sec 3.3: `grav1synth`: updated to canonical repo `rust-av/grav1synth`, stars (93 -> 96), pushed date (2026-04-30 -> 2026-10-16) [F]; <https://github.com/rust-av/grav1synth>.
- Part A Sec 3.3: `ab-av1`: updated stars (1013 -> 1019), added latest release v0.11.7 [F]; <https://github.com/alexheretic/ab-av1>.
- Part A Sec 3.4: `NVIDIA NVENC SDK 13.1`: verified SDK 13.1.15 header, structured claim cell with explicit author, metric, dataset [F]; <https://raw.githubusercontent.com/FFmpeg/nv-codec-headers/master/include/ffnvcodec/nvEncodeAPI.h> ; <https://developer.nvidia.com/blog/improving-video-quality-with-nvidia-video-codec-sdk-12-2-for-hevc/> ; <https://arxiv.org/pdf/2605.01187.pdf>.
- Part A Sec 3.4: `Intel libvpl 2.17`: verified 371 stars, pushed 2026-07-13, release v2.17.0 [F]; <https://github.com/intel/libvpl>.
- Part A Sec 3.4: `AMD AMF 1.5.3`: verified 751 stars, pushed 2026-10-16, release v1.5.3 [F]; <https://github.com/GPUOpen-LibrariesAndSDKs/AMF>.
- Part A Sec 3.4, 4, 6, 7: `Vulkan Video`: added newly ratified `VK_KHR_video_encode_feedback2` (Ext 599, 2026-02-04) [F] and updated Feature matrix, C5, and Whitespace item 2; <https://registry.khronos.org/vulkan/specs/latest/man/html/VK_KHR_video_encode_feedback2.html>.
- Part A Sec 3.5: `Intel DL Streamer`: updated stars (636 -> 642), pushed date (2026-10-08 -> 2026-10-17), added latest release v2026.2.0 [F]; <https://github.com/open-edge-platform/dlstreamer>.
- Part B Sec 1.1: `iSIZE BitSave`: structured claim cell with explicit author, metric, dataset; verified Ozer review, Sony acquisition, and SMPTE MIJ 131(4) 2022 [F]; <https://www.streamingmedia.com/Articles/Editorial/Featured-Articles/Review-iSize-BitSave-Video-Preprocessing-139985.aspx> ; <https://journal.smpte.org/periodicals/SMPTE%20Motion%20Imaging%20Journal/131/4/13/>.
- Part B Sec 1.1: `Chadha & Andreopoulos DPP`: structured claim cell with explicit author, metric (SSIM, VMAF NEG, VMAF), and dataset (XIPH and CDVL) from CVPR 2021 supplemental [F]; <https://openaccess.thecvf.com/content/CVPR2021/supplemental/Chadha_Deep_Perceptual_Preprocessing_CVPR_2021_supplemental.pdf> ; <https://mlanthology.org/cvpr/2021/chadha2021cvpr-deep>.
- Part B Sec 1.1: `Ma et al. RPP`: structured claim cell with explicit author, metric, dataset [F]; <https://arxiv.org/abs/2301.10455>.
- Part B Sec 1.1: `Zhao et al. TDP`: added row for Tri-Dynamic Preprocessing (ByteDance & Peking University, ICASSP 2024 proceedings; arXiv:2512.16101, 2025-12-18) [F]; <https://arxiv.org/abs/2512.16101> ; <https://arxiv.org/html/2512.16101v1>.
- Part B Sec 1.1: `Visionular`: structured claim cell with explicit author, metric, dataset [F]; <https://www.visionular.com/> ; <https://aomedia.org/member%20spotlight/aomedia-member-spotlight-with-visionular-co-founder-and-president-zoe-liu>.
- Part B Sec 1.1: `Beamr`: structured claim cell with explicit author, metric, dataset [F]; <https://beamr.com/cabr> ; <https://streaminglearningcenter.com/blogs/beamr-technology-assessment.html>.
- Part B Sec 1.1: `Harmonic EyeQ`: structured claim cell with explicit author, metric, dataset [F]; <https://www.streamingmediaglobal.com/Articles/News/Featured-News/Harmonic-Claims-50-OTT-Performance-Improvements-with-EyeQ--113366.aspx>.
- Part B Sec 1.1: `AWS Elemental MediaConvert bandwidth reduction filter`: structured claim cell with explicit author, metric, dataset [F]; <https://aws.amazon.com/about-aws/whats-new/2023/06/aws-elemental-mediaconvert-bandwidth-reduction-filter-hevc-avc> ; <https://dev.classmethod.jp/articles/aws-elemental-mediaconvert-bandwidth-reduction-filter/>.
- Part B Sec 1.1: `AWS MediaLive bandwidth reduction filter`: structured claim cell with explicit author, metric, dataset [F]; <https://aws.amazon.com/about-aws/whats-new/2024/09/aws-elemental-medialive-bandwidth-reduction-filter>.
- Part B Sec 1.1: `AWS MediaConvert AV1 film grain synthesis`: structured claim cell with explicit author, metric, dataset [F]; <https://docs.aws.amazon.com/sdk-for-kotlin/api/latest/mediaconvert/aws.sdk.kotlin.services.mediaconvert.model/-a-v1-settings/film-grain-synthesis.html>.
- Part B Sec 1.1: `Brightcove Context Aware Encoding`: structured claim cell with explicit author, metric, dataset [F]; <https://www.streamingmedia.com/Articles/News/Online-Video-News/Brightcove-Announces-Context-Aware-Encoding-Up-to-50-Savings-118356.aspx>.
- Part B Sec 1.1: `Mux Instant Per-Title`: structured claim cell with explicit author, metric, dataset [F]; <https://www.streamingmedia.com/PressRelease/Mux-Launches-Instant-Per-Title-Encoding-Improving-Video-Quality-For-Everyone_47002.aspx>.
- Part B Sec 1.1: `Deep Render`: structured claim cell with explicit author, metric, dataset [F]; <https://seekingalpha.com/pr/20285668-interdigital-acquires-ai-startup-deep-render> ; <https://www.thesaasnews.com/news/deep-render-raises-9-million-in-funding>.
- Part B Sec 1.1: `NVIDIA NVENC Video Codec SDK 13.0`: structured claim cell and independent check with explicit author, metric, dataset [F]; <https://developer.nvidia.com/blog/nvidia-video-codec-sdk-13-0-powered-by-nvidia-blackwell/> ; <https://compression.ru/video/codec_comparison/2025/hardware_report.html>.
- Part B Sec 1.1: `Meta`: structured claim cell with explicit author, metric, dataset [F]; <https://engineering.fb.com/2023/02/21/video-engineering/av1-codec-facebook-instagram-reels/>.
- Part B Sec 1.2: `Netflix AV1 FGS`: structured claim cell with explicit author, metric, dataset from `noise.getoto.net` mirror (kept marked as mirror, not primary) [F]; <https://noise.getoto.net/2025/07/02/av1-scale-film-grain-synthesis-the-awakening/>.
- Part B Sec 1.2: `Grois et al. (Ateme)`: structured claim cell with explicit author, metric, dataset [F]; <https://journal.smpte.org/periodicals/SMPTE%20Motion%20Imaging%20Journal/133/1/16/>.
- Part B Sec 1.2: `Norkin (AV1 film grain)`: structured claim cell with Norkin & Birkbeck (DCC 2018) "up to 50%" bitrate savings on heavy film grain sequences [F]; <https://norkin.org/pdf/DCC_2018_AV1_film_grain.pdf>.
- Part B Sec 1.2: `SVT-AV1 film-grain`: cited primary SVT-AV1 documentation (`Docs/CommonQuestions.md`: level ~8 normal live-action, ~4 2-D animation, 10-15 noisier video) [F] and marked `dev.to` blog as (U) secondary; <https://gitlab.com/AOMediaCodec/SVT-AV1/-/raw/master/Docs/CommonQuestions.md>.
- Part B Sec 1.3: `Encoding in the Dark`: structured claim cell with explicit author, metric, dataset [F]; <https://arxiv.org/abs/2005.03315>.
- Part B Sec 1.3: `Pre+post filtering 10-30%`: structured claim cell with explicit author, metric, dataset [F]; <https://huawei.agorize.com/en/challenges/2025-munich-tech-arena/pages/topic-description-video-compression?lang=en>.
- Part B Sec 1.3: `Netflix Dynamic Optimizer`: structured claim cell with explicit author, metric, dataset from `noise.getoto.net` mirror [F]; <https://noise.getoto.net/2020/08/28/optimized-shot-based-encodes-for-4k-now-streaming/>.
- Part B Sec 1.3: `MSU Video Codecs Comparison 2025`: structured claim cell with explicit author, metric, dataset (50 FullHD sequences, 41 HW encoders, BSQ-rate) [F]; <https://compression.ru/video/codec_comparison/2025/hardware_report.html>.
- Part B Sec 1.3: `Zhao et al. Machine Vision Preprocessing`: added row for video preprocessing for machine vision (ByteDance & Peking University, DCC 2024 proceedings; arXiv:2512.15331, 2025-12-17) [F]; <https://arxiv.org/abs/2512.15331> ; <https://arxiv.org/html/2512.15331v1>.
- Part B Sec 2, 3, 4: updated `L3`, `C4`, `C8`, `C14`, `W4`, `W9` and appended `L10`, `C15`, `W11` for `Zhao et al. TDP` (arXiv:2512.16101), `Zhao et al. Machine Vision Preprocessing` (arXiv:2512.15331), and `VK_KHR_video_encode_feedback2`.
