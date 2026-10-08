<!-- markdownlint-disable MD013 MD024 MD033 MD060 -->

# Research 0172: zero-copy hop audit

Evidence behind the zero-copy items of [ADR-0172](../adr/0172-roadmap-milestone-map.md). Requirement: decode, Pelorus filters, encoder and vmafx scoring keep frames in GPU memory and never touch system memory; side data rides the frame.

**Method.** Source read only; nothing was executed on hardware. Items marked UNVERIFIED are memory or inference, not read from source. Bases: Pelorus `420c19a`, FFmpeg n9.0.2 (`946fcce`), vmafx `origin/master` `18554dc9f` plus the draft pull requests vmafx#2370, #2375, #2346, #2470, #2138 and #1455. Short names: HWVK = `libavutil/hwcontext_vulkan.c`, HWCUDA = `libavutil/hwcontext_cuda.c`, PF = `ffmpeg-patches/files`. Break ids `G#` are written `ZC-G#` in roadmap issues.

## 0. Bottom line

- Pelorus filters themselves are VRAM-resident: input and output `AV_PIX_FMT_VULKAN` only, no pixel readback, no format conversion. Only small stat buffers (0.6 KB to 390 KB) cross to host, each with a per-frame CPU wait.
- Every documented NVENC / QSV / libaom / AMF recipe uses `hwupload ... hwdownload` (docs/usage/ffmpeg.md:21-23,70,93,98,152,199,288,338; docs/backends/qsv-roi.md:68; libaom-roi.md:15). Those are full-frame host round trips. Violates the requirement today.
- FFmpeg n9.0.2 has NO Vulkan->NVENC map: Vulkan<->CUDA is `av_hwframe_transfer_data` = device-to-device `cuMemcpy2D` (a VRAM copy, not a map). Vulkan<->VAAPI/DRM is a true map but only when the Vulkan pool uses DRM-modifier tiling; Pelorus output pools are OPTIMAL tiling, so the map fails. No Vulkan<->D3D11/D3D12/AMF/VideoToolbox path exists.
- Only fully native zero-copy path today: Vulkan decode -> Pelorus -> Vulkan encode (README.md:41). vmafx then imports Vulkan frames with one GPU copy (by design, ADR-2125 decision 9).
- Side data (SEI_UNREGISTERED) survives hwmap / hwupload / hwdownload (av_frame_copy_props) and is not written to the bitstream by default. vmafx reads it wrongly: first SEI entry only, from the decoded (distorted) frame which never carries it.

## 1. Pelorus filter audit

All filters: `FILTER_SINGLE_PIXFMT(AV_PIX_FMT_VULKAN)` + `FF_FILTER_FLAG_HWFRAME_AWARE`, `ff_vk_filter_init/config_input/config_output`. No hwupload/hwdownload inside any filter.

| filter | in/out | host readback (size, 1080p / 4K) | CPU stall | evidence |
|---|---|---|---|---|
| deband | VULKAN->VULKAN | none (side data built from options, no GPU data) | none | vf_pelorus_deband_vulkan.c:461-462, :190-211 |
| analyze | VULKAN->VULKAN (pass-through frame) | 5 x ntiles x 4 B, tile 32: 40.8 KB / 163 KB | yes: `ff_vk_exec_submit` + `ff_vk_exec_wait` per frame | analyze:61,647,654-657,749-750 |
| mc | VULKAN->VULKAN (pass-through) | mvx, mvy (+sad if meta=1, default 1), nblocks x 4 B each, bsize 16: 8160 blocks = 32.6 KB each / 32400 = 130 KB each; then `memcpy` to host scratch | yes: submit+wait per frame; next frame's predictors computed on CPU (`pel_mc_build_predictors`) and written back into a host-visible SSBO: GPU->CPU->GPU loop that serialises frame N+1 behind frame N | mc:489-500,623,660-663,749-751,804-826 |
| denoise | VULKAN->VULKAN (new pooled frame) | meta=0 (default): none, no wait. meta=1: PelorusDenoiseBuf about 640 B (assumes PEL_DENOISE_STATS=4) | meta=1 only | denoise:98-125,523-531,783-786,791-796,1060 |
| denoise MV input | consumes mc side data | CPU parses side data and fills two HOST_VISIBLE-only SSBOs (cells x 4 B each); GPU reads system memory over PCIe | none (async) | denoise:546-630 |
| grain_estimate | VULKAN->VULKAN (pass-through) | PelorusGrainBuf 2304 B | yes: `ff_vk_exec_wait` per frame | grain_estimate:120-126,540-558,628-634 |
| dehalo, aa, deblock, borderfix | VULKAN->VULKAN | none | none | no mapped_mem / exec_wait in files (grep) |
| scenecut | any format, metadata only, no GPU work | none | none | vf_pelorus_scenecut.c:92-107 |
| fgs BSF | packets (host) | n/a | n/a | h265_pelorus_fgs_bsf.c; inherent (bitstream) |

Notes:

- Readback memory is DEVICE_LOCAL|HOST_VISIBLE|COHERENT with no fallback: `ff_vk_alloc_mem` returns EINVAL if no such type (libavutil/vulkan.c ff_vk_alloc_mem; mc:489-500). Hard failure on a device without such a heap type. UNVERIFIED which real GPUs lack it.
- Frame retention: mc keeps `prev = av_frame_clone(in)` (mc:757-764); denoise keeps a ring of up to PEL_DENOISE_MAX_PREV clones plus 1 lookahead frame (denoise:172-174,856-880). Clones pin decoder pool surfaces. No doc mentions `-extra_hw_frames` (git grep: none). Risk of decoder pool starvation / stall with fixed-size pools (NVDEC, VAAPI, QSV). UNVERIFIED at runtime.
- scenecut is documented "run after hwdownload" (scenecut.c:29, ADR-0126:51, README:138). That is not a requirement: the filter has no format callback, so default negotiation accepts every format incl. hw (formats.c ff_all_formats -> ff_formats_pixdesc_filter(0,0)), and the non-HWFRAME_AWARE rule copies the input hw_frames_ctx to the output (avfilter.c:428-439). It should work in the VULKAN domain, and `pict_type=I` is honoured on hw frames by NVENC (nvenc.c:3017), QSV (qsvenc.c:2485), AMF (amfenc.c:517), and hw_base_encode (hw_base_encode.c:464) covering VAAPI / Vulkan / D3D12. Source-verified only, no run. Docs steer users into a needless hwdownload: doc defect, not a functional break.
- Multiplane: NV12/P010 Vulkan images are multiplane by default. CUDA interop refuses them: "Cannot map a multiplane Vulkan image ... create the Vulkan device with disable_multiplane=1" (HWVK:3910-3916). Pelorus filters under `disable_multiplane=1` (NV12 = 2 images) are not tested anywhere (git grep disable_multiplane: no hit outside upstream). UNVERIFIED that filters work in that mode.
- Output pool: `ff_vk_filter_config_output` reuses the input frames ctx if usable, else allocates OPTIMAL tiling, usage SAMPLED|STORAGE|TRANSFER_SRC (libavfilter/vulkan_filter.c:99-140). `ENCODE_SRC` usage is added automatically at frames init when supported (HWVK:3034-3037), so Vulkan encode needs no copy.

## 2. Encoder steering patches (what frame types they accept)

All Pelorus encoder patches read only frame side data (`AV_FRAME_DATA_REGIONS_OF_INTEREST` or `SEI_UNREGISTERED`), never pixels. They are frame-format agnostic and work with hw frames. The hop that fails is getting the frame into the encoder, not the patch.

| encoder | patch | accepted input (n9.0.2) | steering input | evidence |
|---|---|---|---|---|
| NVENC | 0004 ROI, 0008 ME hints, 0011 film grain | CUDA, D3D11, sw (not Vulkan) | ROI rects / PEL_SEC_MOTION / FILM_GRAIN side data -> host buffers (qpDeltaMap, ME hints) | nvenc.c:78-80,715-740; nvenc-pelorus-roi.patch:122-152; nvenc-pelorus-me-hints.patch (setup_me_hints reads SEI_UNREGISTERED) |
| QSV | 0005 | QSV (hw), sw; hw_frames_ctx QSV | ROI -> mfxExtEncoderROI / MBQP | qsvenc_hevc.c:400; qsvenc.c:2763-2765; qsv-pelorus-roi.patch:208 |
| Vulkan encode | 0009 qpmap | VULKAN only (native) | ROI rects -> on-GPU raster into quantization-map image on the encode command buffer | vulkan_encode.c:27; vulkan-pelorus-qpmap.patch:209-215,774 |
| libaom | 0012 | sw only | ROI side data | libaom-pelorus-roi.patch:200 |
| SVT-AV1 | 0013 | sw only | ROI side data | svtav1-pelorus-roi.patch:247 |
| VAAPI enc | none (upstream reads ROI natively, vaapi_encode.c) | VAAPI | ROI only; no MV hints, no grain | grep REGIONS_OF_INTEREST count: vaapi_encode 1, libx264 1, libx265 1, qsvenc 1, d3d12va_encode 1 |
| AMF | none (ENC-04 open) | sw, D3D11, DXVA2, AMF_SURFACE (no Vulkan, no VAAPI) | none | amfenc.c:56-72,406-420; hwcontext_amf.c:35 includes hwcontext_vulkan.h but no Vulkan use found |
| x264/x265 | none needed | sw | ROI native | inherent download |

Bitstream leak check: `SEI_UNREGISTERED` is written into the stream only when `udu_sei=1` (nvenc.c:2752, libx264.c:574,1570, libx265.c:816,1051; default 0). Pelorus blobs do not enter the bitstream by default. libaom / SVT / QSV / AMF / Vulkan encoders ignore the type.

### 2a. FFmpeg n9.0.2 hardware interop (Vulkan as the hub)

| pair | mechanism in n9.0.2 | copy? | evidence |
|---|---|---|---|
| VAAPI -> Vulkan | `av_hwframe_map`, DRM PRIME import with modifiers, sync_file semaphore import | no (map) | HWVK:3699-3721,4069-4090 (needs FF_VK_EXT_DRM_MODIFIER_FLAGS) |
| DRM PRIME -> Vulkan | map | no | HWVK:3664-3695 |
| Vulkan -> DRM PRIME / VAAPI | map, but ONLY if the Vulkan frames ctx has `tiling == DRM_FORMAT_MODIFIER`; OPTIMAL images have no modifier, `GetImageDrmFormatModifierProperties` fails. Waits on semaphores via CPU `vkWaitSemaphores` unless DMA_BUF_IOCTL_EXPORT_SYNC_FILE exists | no, but fails for Pelorus pools | HWVK:2861-2870 (export flag only for modifier tiling), :4199-4262, :4363-4406, vulkan_filter.c:137 |
| CUDA -> Vulkan | `transfer_data_to` = `cuMemcpy2DAsync` device linear -> imported CUarray, external memory + external timeline semaphore (OPAQUE_FD / OPAQUE_WIN32) | YES: VRAM->VRAM copy | HWVK:3734-4060 (copy at :4020-4040), :4836-4860 |
| Vulkan -> CUDA | `transfer_data_from` = `cuMemcpy2DAsync` array -> linear CUdeviceptr | YES: VRAM->VRAM copy | HWVK:4862-4935; `vulkan_map_to`/`map_from` have no CUDA case (:4069-4090,:4391-4415) |
| CUDA device derived from Vulkan | by device UUID | n/a | HWCUDA:476-520; HWVK:2176-2190 |
| Vulkan <-> D3D11VA / D3D12VA / DXVA2 | absent (no case in map_to/map_from/transfer) | no path | HWVK grep D3D: none; matches pelorus #69 "no Vulkan<->D3D11 mapping on Windows" |
| Vulkan <-> AMF | absent | no path | hwcontext_amf.c, amfenc.c |
| Vulkan <-> VideoToolbox | absent; MoltenVK only appears as a dlopen name | no path | HWVK:664 |
| Vulkan <-> QSV | absent directly; chain Vulkan->VAAPI->QSV possible on Linux (`qsv_map_to` from VAAPI) | maps, if first hop works | hwcontext_qsv.c:2145-2330 |
| VAAPI/D3D11 -> QSV | map via `qsv_fixed_pool_map_to` / `qsv_dynamic_pool_map_to` | no | hwcontext_qsv.c:2145-2329 |
| NVDEC output | AV_PIX_FMT_CUDA | n/a | nvdec.c:730 |
| Vulkan decode / encode | native, same frames ctx reused by filters | no | vulkan_decode.c; vulkan_encode.c:27 |

## 3. Side data

- Carrier: `AV_FRAME_DATA_SEI_UNREGISTERED`, one blob appended per producer (`av_frame_new_side_data_from_buf`, never merges): deband:211, analyze:601, grain_estimate:449, denoise:465, mc:453. Standard types also used: REGIONS_OF_INTEREST (analyze:459-461), FILM_GRAIN_PARAMS (grain_estimate:386). Scan helper: pelorus_sidedata.h:53-73 (scans newest first).
- Survives: `av_frame_copy_props` copies all side data by ref (libavutil/frame.c:248-265). Used by hwupload (vf_hwupload.c:207), hwdownload (vf_hwdownload.c:150), hwmap (vf_hwmap.c:350), and every Pelorus out-of-place filter (deband:241, denoise:844). Pass-through filters (analyze, mc, grain_estimate) keep the same AVFrame. Lost after the encoder: decoded frames carry no Pelorus data (default `udu_sei=0`).
- vmafx reader: `av_frame_get_side_data(dist, AV_FRAME_DATA_SEI_UNREGISTERED)` returns the FIRST SEI entry only: vmafx ffmpeg-patches/0017-libvmaf-read-pelorus-sidedata.patch (hunk in do_vmaf); successor in draft #2370 `perceptual_sidedata()` (vf_vmafx.c, same call). With two producers (e.g. deband then analyze) the first blob may lack the section vmafx needs; Pelorus's own consumers scan all entries because of exactly this. Also read from `dist` (the decoded encode), not from the Pelorus-side frame, so in the canonical split -> encode -> loopback-decode graph (#2138) it is never present. Both are vmafx defects (and a contract gap: which tap carries the weights).

## 4. vmafx import capability (what Pelorus output can reach, per backend / OS)

Source: ADR-1829, #2370 body + ffmpeg-patches/src/vf_vmafx.c backend table (`{CUDA,AV_PIX_FMT_CUDA}`, `{SYCL,DRM_PRIME}`, `{HIP,DRM_PRIME}`, `{METAL,VIDEOTOOLBOX}`), #2375, #2470, docs/state.md rows. Draft PRs are not on master; origin/master still has the older libvmaf_cuda / libvmaf_sycl (QSV frames only, luma+chroma state per #2075) / libvmaf_metal filters.

| backend | OS | zero-copy import today (master) | after RC4 drafts | Vulkan frames |
|---|---|---|---|---|
| CUDA | Linux, Windows | `libvmaf_cuda`: CUDA decoder frame copied D2D into libvmaf pool (T-CUDA-IMPORT-DEVICE-TO-DEVICE-COPY-2026-10-05) | CUDA frames imported in place with event fences (ADR-2023, #2277) | opaque-fd memory imported as CUDA arrays (#2375); filter still copies once on GPU into its own exportable pool (ADR-2125 d9) |
| SYCL | Linux (Intel) | `libvmaf_sycl`: QSV frames -> VA surface -> Level Zero dma-buf (ADR-1121) | DRM PRIME from VAAPI via hwmap; QSV frames refused by name (#2370 doc line "libvmaf_sycl (QSV frames) ... refuses these frames by name today") | LINEAR / DRM-modifier dma-buf only, OPTIMAL refused on SYCL (#2375); filter copy path covers it |
| HIP | Linux (AMD) | no import path (T-HIP-NO-IMPORT-PATH-2026-10-05) | DRM PRIME linear dma-buf only; AMD VAAPI surfaces are tiled -> refused (T-HIP-VAAPI-TILED-SURFACES-2026-10-07, open; e2e amd refused) | same as SYCL |
| Metal | macOS | IOSurface import is lock + CPU memcpy (T-GAP-METAL-IOSURFACE-NOT-TRUE-ZERO-COPY); filter failed first frame (T-METAL-FFMPEG-FILTER-BIPLANAR-IMPORT) | true MTLTexture binding planned in RC4, needs Apple device | none (Vulkan not offered on macOS in FFmpeg) |
| Vulkan compute | all | dropped (ADR-0726); reserved enum slots only | not coming back | n/a |
| D3D11 / D3D12 | Windows | not imported | "D3D11 on the GPU" is RC4 scope per ADR-1829; #2370 refuses D3D11/D3D12 frames by name or `import=host`; Windows shared textures refused by name (Q-044, per #2470) | Windows Vulkan handles declared and refused (#2375) |
| sw frames | any | uploaded by the context | same | n/a |

Fence model (needed for correct zero-copy): ADR-1829 item 2 / ADR-1929: acquire fence from producer, release fence to producer, no release without fence. CUDA: events on library stream (ADR-2023). SYCL/HIP: sync_file acquire + host release fence; Vulkan timeline semaphores importable on CUDA only; SYCL/HIP refuse (#2375). `vmafx_fence_wait()` refuses VULKAN_SEMAPHORE.

## 5. HOP MATRIX

Legend: Z = zero-copy, V = VRAM-to-VRAM copy (no system memory), H = host round trip (system memory), X = unsupported/refused, I = inherent (software encoder/decoder).
Today = origin/master + n9.0.2 + current Pelorus patches. RC4 = vmafx drafts listed in section 4.

| # | path | decode->Pelorus | Pelorus filters | Pelorus->encoder | encoder->vmafx (dist) | ref/side-data to vmafx | verdict today | verdict after fixes G1-G6 |
|---|---|---|---|---|---|---|---|---|
| A | Linux NVIDIA: NVDEC -> Pelorus -> NVENC -> vmafx CUDA | V (hwupload CUDA->Vulkan, disable_multiplane needed; HWVK:3990-4060, :3910) | Z + per-frame host stat wait | V (hwupload_cuda Vulkan->CUDA; HWVK:4862-4935) | NVENC out -> loopback NVDEC CUDA -> Z after RC4, V today (#2370; state row) | side data lost on dist; first-entry read | no host copy of frames, but 2 V copies at the Pelorus hop (+1 V at vmafx today). Documented recipe uses H. | Z via LINEAR exportable pool + CUDA map (G1) or CUDA kernels (G9) |
| B | Linux Intel: VAAPI dec -> Pelorus -> QSV enc -> vmafx SYCL | Z (hwmap VAAPI->Vulkan; HWVK:3699) | Z | X: Vulkan->VAAPI map needs DRM-modifier pool; Pelorus pool is OPTIMAL (HWVK:2861-2870; vulkan_filter.c:137). Falls to H (docs qsv-roi.md:68). Then VAAPI->QSV map ok (hwcontext_qsv.c:2321) | QSV/VAAPI loopback decode -> DRM PRIME -> SYCL Z (master: QSV frames, `libvmaf_sycl`; RC4: DRM PRIME, QSV refused) | same defects as A | H today | Z after G2 (+ vmafx keeps SYCL DRM path) |
| C | Linux Intel/AMD: VAAPI dec -> Pelorus -> VAAPI enc (no steering patch beyond native ROI) | Z | Z | X as in B (modifier pool) | AMD: X (HIP refuses tiled radeonsi surfaces); Intel: Z (SYCL de-tiles) | same | H (Intel), X (AMD->HIP) | Intel Z after G2; AMD needs vmafx de-tile (G11) |
| D | Linux AMD: Pelorus -> AMF | n/a | n/a | X: AMF takes sw / D3D11 / DXVA2 / AMF_SURFACE only (amfenc.c:56-72) | n/a | n/a | H (host surfaces) | needs new AMF input patch (G4); Linux AMF availability UNVERIFIED |
| E | Vulkan native: Vulkan dec -> Pelorus -> Vulkan enc -> (loopback Vulkan dec) -> vmafx (CUDA / SYCL / HIP by GPU vendor) | Z (same frames ctx reused, vulkan_filter.c:99-130) | Z + stat waits | Z (vulkan_encode.c:27; ROI via on-GPU qpmap, 0009) | V (vmafx copies Vulkan frames once into exportable images, #2370; CUDA imports opaque fd, SYCL/HIP need linear/modifier dma-buf) | ref-side frames carry the data, dist does not | best path: only the vmafx copy is V. Requires Vulkan video encode queue (Linux ANV/RADV/NVIDIA; absent on Intel Windows drivers per #69) | Z after G10 |
| F | Windows NVIDIA: NVDEC -> Pelorus -> NVENC -> vmafx CUDA | V (CUDA->Vulkan via OPAQUE_WIN32; HWVK:3746-3760) | Z | V | as A | as A | same as A | same as A |
| G | Windows Intel/AMD: D3D11VA dec -> Pelorus -> QSV/AMF | X (no D3D11->Vulkan map) -> H | Z | X (no Vulkan->D3D11) -> H | vmafx refuses D3D11 frames by name / import=host | n/a | H both ends | needs FFmpeg upstream D3D11/D3D12 interop (G3) |
| H | macOS: VideoToolbox dec -> Pelorus (MoltenVK) -> VT enc -> vmafx Metal | X (no VT<->Vulkan map) -> H | Z (Pelorus Vulkan filters on MoltenVK: untested, PLT-03 "macOS/MoltenVK not addressed") | X -> H | Metal import = CPU memcpy today | n/a | H | needs Metal compute filters (G9) or upstream VT<->Vulkan interop (G5) |
| I | Software encoders (libx264/x265/aom/SVT) | Z or V | Z | I: hwdownload unavoidable | I: sw frames -> context upload | n/a | inherent | inherent; keep scenecut/ROI as side-data consumers |
| J | Any path with mc + denoise MC taps | n/a | GPU->CPU->GPU MV loop (small, 33-130 KB/frame) | n/a | n/a | n/a | not a frame copy, but a host sync that serialises the pipeline | G7 |

## 6. Gap list (ordered by impact)

| id | break | fix | owner | size | placement |
|---|---|---|---|---|---|
| G1 | Vulkan<->CUDA is a VRAM copy plus mandatory `disable_multiplane=1` (NV12/P010); NVENC is the dominant encoder, so path A/F always pay 2 copies and a device-creation constraint no recipe documents | (a) document `disable_multiplane=1` + `hwupload_cuda` recipe now (S); (b) Pelorus option to allocate a LINEAR exportable output pool and a patch adding Vulkan->CUDA `map_from` via `cuExternalMemoryGetMappedBuffer` (true alias, no copy) (L, FFmpeg upstream or Pelorus patch); (c) CUDA filter kernels remove the hop (G9) | Pelorus + FFmpeg upstream | S docs / L map | docs 0.5; map 0.6; kernels backends milestone |
| G2 | Pelorus output pools are OPTIMAL tiling, so Vulkan->VAAPI/DRM/QSV maps fail (Intel/AMD Linux) and every recipe falls to hwdownload | Pelorus patch to `vulkan_filter.c` (or per-filter pool) allocating DRM-modifier tiling pools with storage usage; modifier chosen from the intersection of storage-image support and the consumer's read modifiers; LINEAR fallback. Risk: tiled/compressed modifiers may not support storage images (UNVERIFIED) | Pelorus | M | 0.5 |
| G3 | Windows non-NVIDIA: no Vulkan<->D3D11/D3D12 (also blocks QSV D3D11 child and AMF D3D11 input) | FFmpeg upstream: `VK_KHR_external_memory_win32` D3D11/D3D12 shared NT handle map + fence interop in hwcontext_vulkan.c and d3d hwcontexts | FFmpeg upstream (Pelorus carries as patch meanwhile) | XL | backends milestone |
| G4 | AMF has no Vulkan input (ENC-04 also has no steering patch) | amfenc patch wrapping `AV_PIX_FMT_VULKAN` into an AMF surface (AMF Vulkan interop API: UNVERIFIED, check AMF SDK) plus ROI/QP patch | Pelorus + FFmpeg upstream | L | backends milestone / 1.0 decision D8 |
| G5 | macOS has no Vulkan<->VideoToolbox/Metal map | FFmpeg interop through MoltenVK `VK_EXT_metal_objects` (UNVERIFIED) or Metal compute filters; vmafx Metal true IOSurface/MTLTexture binding | FFmpeg upstream + vmafx (RC4, needs Apple device) | XL | backends milestone |
| G6 | vmafx side-data read: first SEI entry only, and from the decoded frame (never carries it) | vmafx scans all SEI_UNREGISTERED entries newest first (same rule as pelorus_sidedata.h:53-73) and reads from the pre-encode tap (reference branch after Pelorus), or Pelorus ships a metadata-forward filter; also define the contract in ADR-0103/1118 | vmafx (+ Pelorus contract) | S | 0.5 interchange and live integration |
| G7 | Per-frame CPU waits: analyze, mc, grain_estimate, denoise meta=1 (`ff_vk_exec_wait`); mc round-trips MVs through the CPU | keep MV/stat fields device-resident for GPU consumers (denoise, qpmap bind the SSBO), copy to host only for host consumers (NVENC hints), use timeline-semaphore deferred readback; compute predictors on GPU | Pelorus | M-L | 0.6 |
| G8 | Frame pinning (mc prev, denoise ring + lookahead) can starve fixed decoder pools; undocumented | document `-extra_hw_frames N`, assert pool size at config, test with NVDEC / VAAPI / Vulkan | Pelorus | S | 0.5 |
| G9 | Multi-backend compute would delete interop hops: CUDA kernels remove Vulkan<->CUDA (A/F), SYCL/HIP kernels remove DRM export (B/C), Metal kernels remove H. Shares dispatch with vmafx#1455 libgpudispatch | backends milestone: per-backend filter kernels behind one filter surface; consume vmafx device-frame import (fences) | Pelorus + vmafx (#1455) | XL | backends milestone |
| G10 | vmafx always copies Vulkan frames once (cannot tell if FFmpeg pool exports) | Pelorus pools are exportable (OPAQUE_FD export flag set at pool creation, HWVK:2860-2865); expose that to vmafx (AVOption or documented frames-ctx contract) so vmafx can import in place | vmafx + Pelorus | M | 0.5 |
| G11 | vmafx import gaps: HIP none + tiled AMD VAAPI, SYCL QSV refused by the new filter, CUDA D2D copy on master, Metal memcpy, D3D11 absent | vmafx RC4 WP3/WP9 (#2470, #2370, #2586) | vmafx | XL (theirs) | tracked there |
| G12 | Pelorus filters under `disable_multiplane=1` and with DRM-modifier inputs untested | add replay/smoke tests in ffmpeg-patches/test for both modes | Pelorus | M | 0.5 |
| G13 | scenecut docs force hwdownload; QSV ROI `EnableMBQP` cleared on some Windows runtimes (#69) | fix docs (README:138, ADR-0126 pipeline placement, scenecut.c:29), add a Vulkan-frame scenecut test; warn when MBQP is cleared | Pelorus | S | 0.5 |
| G14 | Documented recipes teach the round trip | rewrite the NVENC / QSV / libaom examples per path; mark sw-encoder `hwdownload` as inherent | Pelorus | S | 0.5 |

## 7. Synchronisation requirements (to keep zero-copy correct)

- FFmpeg-internal: every Vulkan hop uses per-image timeline semaphores in `AVVkFrame` (`sem[]`, `sem_value[]`); Pelorus filters use `ff_vk_exec_add_dep_frame` / `ff_vk_exec_submit` correctly (mc, denoise share clones read-only). Keep that discipline for any new owned pool.
- Vulkan->CUDA / CUDA->Vulkan: external timeline semaphores waited and signalled on the CUDA stream around `cuMemcpy2DAsync` (HWVK:4005-4040, 4880-4935); any future alias map must keep the same wait/signal.
- Vulkan->DRM/VAAPI: acquire via `DMA_BUF_IOCTL_EXPORT_SYNC_FILE` sync_file; without it the map does a CPU `vkWaitSemaphores` (HWVK:4222-4247), a stall; the reverse (VAAPI->Vulkan) imports a sync_file semaphore.
- Vulkan->vmafx: CUDA imports the acquire timeline semaphore; SYCL/HIP use sync_file acquire + host release fence; no release before the fence (ADR-1829 item 2, #2375). A producer frame goes back to its pool only after the consumer's release fence (T-VMAFX-FILTER-DMABUF-RETURNED-BEFORE-READ-2026-10-07).
- Host readbacks (G7) need `HOST_BIT` buffer barriers (done: mc:576, grain_estimate:540) and are the only places Pelorus blocks the CPU.
- Held references (mc/denoise) must not be recycled by the decoder while a Pelorus dispatch still reads them: guaranteed by AVBuffer refcount, but the pool must be large enough (G8).

## 8. Items needing FFmpeg upstream work

1. Vulkan->CUDA true map (G1b); until then V copy.
2. Vulkan<->D3D11/D3D12 interop (G3).
3. Vulkan -> AMF input (G4), Vulkan <-> VideoToolbox/Metal (G5).
4. Optionally: a frames-ctx capability bit "exportable + tiling + semaphores exported" so consumers (vmafx) can import without a defensive copy (G10); `vulkan_filter.c` hook for pool tiling (G2).

## 9. What vmafx must provide

1. Scan all `SEI_UNREGISTERED` entries (newest first) and define which tap carries Pelorus data (G6).
2. In-place import of Pelorus-owned Vulkan frames when the pool is known exportable (G10); accept OPTIMAL on CUDA, LINEAR/DRM-modifier on SYCL/HIP.
3. RC4 items: CUDA no-copy import, HIP import + tiled AMD de-tile, SYCL DRM PRIME with chroma, Metal true binding, D3D11 on the GPU, fences both directions (ADR-1829).
4. Loopback decoder on hw frames (patch 0024) for every vendor so dist stays on device.
5. A read-only query of which backend/OS cells are zero-copy, so Pelorus docs can cite instead of restate.

## 10. Unverified (flagged)

AMF Vulkan interop API and Linux AMF availability; MoltenVK `VK_EXT_metal_objects` suitability; storage-image support on tiled/compressed DRM modifiers; runtime behaviour of scenecut on Vulkan frames; pool starvation with mc/denoise holding frames; Pelorus filters with `disable_multiplane=1`; which GPUs lack a DEVICE_LOCAL|HOST_VISIBLE|COHERENT memory type; vmafx draft PR behaviour (read from PR bodies and diffs, not run).
