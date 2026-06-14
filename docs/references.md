<!-- markdownlint-disable MD013 -->
# References

Curated, machine-resolvable links "for AI agents" (principles.md §8 convention)
— the authoritative sources and awesome-lists for the topics Pelorus touches.
Prefer these (and the in-tree reference implementations) over recall.

## FFmpeg / libavfilter

- FFmpeg source (Vulkan filter model): `libavfilter/vulkan_filter.h`,
  `vf_gblur_vulkan.c`, `vf_nlmeans_vulkan.c`, `vf_scdet_vulkan.c` (readback),
  `vf_deband.c` (the deband algorithm). GLSL macros: `libavutil/vulkan.h`.
- Filtergraph + hwcontext: <https://ffmpeg.org/ffmpeg-filters.html>,
  <https://trac.ffmpeg.org/wiki/Hardware/VAAPI>, Vulkan: <https://trac.ffmpeg.org/wiki/Hardware/vulkan>
- awesome-ffmpeg: <https://github.com/transitive-bullshit/awesome-ffmpeg>

## Vulkan compute

- Vulkan spec: <https://registry.khronos.org/vulkan/specs/latest/html/vkspec.html>
- GLSL compute / extensions: <https://registry.khronos.org/OpenGL/specs/gl/GLSLangSpec.4.60.html>
- Vulkan Memory Allocator (VMA): <https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator>
- awesome-vulkan: <https://github.com/vinjn/awesome-vulkan>

## Debanding (the flagship)

- flash3kyuu_deband (f3kdb) — the canonical debanding algorithm and its
  grain/dither/detail-mask design (see `docs/research/0101-smart-deband.md`).
- Dither theory (TPDF, why dither defeats contouring): Lipshitz/Wannamaker/Vanderkooy,
  "Quantization and Dither: A Theoretical Survey".

## Film grain (FGS) — AV1 + HEVC/VVC

- AV1 film grain (AOM): <https://aomedia.org/av1/specification/> (§ film grain),
  FFmpeg `libavutil/film_grain_params.h` (`AVFilmGrainAOMParams`).
- H.274 film grain (HEVC/H.265 + VVC/H.266 SEI FGC): ITU-T H.274 /
  ISO/IEC 23002-7; FFmpeg `AVFilmGrainH274Params`.

## Quality assessment (the oracle)

- VMAF: <https://github.com/Netflix/vmaf> and the vmafx fork (`VMAFx/vmafx`).
- BD-rate (Bjøntegaard delta): G. Bjøntegaard, "Calculation of average PSNR
  differences between RD-curves", VCEG-M33.

## Hardware encoders

- NVENC (Video Codec SDK): <https://developer.nvidia.com/nvidia-video-codec-sdk>
- AMD AMF: <https://github.com/GPUOpen-LibrariesAndSDKs/AMF>
- Intel QSV / oneVPL: <https://github.com/intel/libvpl>
- Optical Flow (motion hints, future `vf_pelorus_mc`): NVIDIA Optical Flow SDK
  <https://developer.nvidia.com/opticalflow-sdk>; Vulkan `VK_NV_optical_flow`.

## Engineering process

- NASA/JPL Power of 10: <https://spinroot.com/gerard/pdf/P10.pdf>
- SEI CERT C: <https://wiki.sei.cmu.edu/confluence/display/c/>
- obra/superpowers (vendored meta-skills, `.claude/skills/superpowers/`):
  <https://github.com/obra/superpowers>
- Keep a Changelog: <https://keepachangelog.com/>; SemVer: <https://semver.org/>;
  Conventional Commits: <https://www.conventionalcommits.org/>
- awesome-c: <https://github.com/oz123/awesome-c>; awesome-cmake/meson, awesome-video:
  <https://github.com/krzemienski/awesome-video>
