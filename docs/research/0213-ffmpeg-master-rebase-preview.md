<!-- markdownlint-disable MD013 MD060 -->
# FFmpeg master rebase preview and upstream filter overlap

**Date:** 2026-10-19

**Issue:** [#213](https://github.com/VMAFx/pelorus/issues/213).

**Scope:** shared FFmpeg fix series VMAFx/ffmpeg-patches `v0.3.0` plus the 23-patch Pelorus stack, applied with `git am --3way` to FFmpeg `master` at `ec447c56e35463ce758c4c659c4c0bd9940f34ed` (committed 2026-10-19T00:51:53Z, 2382 commits after the `n9.0.2` pin `946fcce07b6dcd0331c8cc609192aeff5e1924f8`). No build; apply only.

Raw log and script (local, not committed): `.workingdir/evidence/213-master-preview/` (`trial.sh`, `trial.log`).

## Verdict

No FFmpeg release follows `n9.0.2`: the tag list on code.ffmpeg.org on 2026-10-19 ends at `n9.0.2` and `n9.1-dev`, so the release-gated rebase in #213 stays blocked. A trial on `master` shows what that rebase will meet:

- 6 of the 34 shared-series patches no longer apply. They belong to VMAFx/ffmpeg-patches, which rebases them before Pelorus can.
- 1 of the 23 Pelorus patches no longer applies: `0009b-vulkan-pelorus-qpmap.patch`. `0009a`, the libavutil enabler split out in [ADR-0231](../adr/0231-split-vulkan-qpmap-enabler.md), still applies.
- No upstream filter on `master` supersedes a Pelorus filter, so no filter is removed.

## Method

`trial.sh` fetches and verifies the shared series with `scripts/fetch-ffmpeg-series.sh`, adds a scratch FFmpeg worktree at `origin/master`, and applies each patch in `series.txt` order. A patch that fails is reported by name with its conflicted paths and then skipped (`git am --abort`), so later patches see the tree without it; a failure after the first one in a file can be a consequence of an earlier skip. The scratch worktree is removed at exit.

## Results

| Series | Patch | Conflicted paths |
| --- | --- | --- |
| shared | `0001` avutil/vulkan queue-family release | `libavutil/vulkan.c` |
| shared | `0002` GCC 14 and 16 diagnostics | `libswresample/tests/swresample.c`, `tools/seek_print.c` |
| shared | `0008` queue de-phase, video pool pinning | `libavcodec/ffv1enc_vulkan.c`, `libavutil/vulkan.c` |
| shared | `0019` tonemap_vulkan bt2408_hable | `doc/filters.texi` |
| shared | `0021` tonemap_vulkan dynamic peak | `doc/filters.texi`, `libavfilter/Makefile`, `libavfilter/vf_tonemap_vulkan.c`, `libavfilter/vulkan/Makefile` |
| shared | `0034` tonemap_vaapi bt2408_hable peak | `libavfilter/Makefile` |
| Pelorus | `0009b` Vulkan encode QP map | `configure`, `libavcodec/vulkan/Makefile`, `libavcodec/vulkan_encode.c` |

All other patches apply: 28 shared, 22 Pelorus.

`0009b` meets drift in the Vulkan encode path. Twelve upstream commits since the pin touch `libavcodec/vulkan_encode.c` or `libavcodec/vulkan/Makefile`, among them DPB images allocated outside the frame machinery, buffer pools on `AVRefStruct`, a timeline-semaphore busy state and a Vulkan APV encoder. Neither the pin nor `master` mentions `quantization_map` in `hwcontext_vulkan.c`, `vulkan_encode.c`, `vulkan_encode.h` or `vulkan_video.c`, so `0009a` and `0009b` remain needed.

### Upstream filter overlap

| Upstream filter | In `n9.0.2` | Pelorus filter | Decision |
| --- | --- | --- | --- |
| `scdet_vulkan` | yes | `pelorus_scenecut` | keep: Pelorus already uses it as the readback model ([ADR-0109](../adr/0109-analyze-filter.md), [ADR-0143](../adr/0143-ffmpeg-9-migration.md)); no change on `master` |
| `fruc_vulkan` | no | `pelorus_mc_vulkan` | keep: frame-rate conversion on `VK_NV_optical_flow` (options `fps`, `perf`, `grid`); it writes no motion side data, so it cannot feed `mc` or encoder motion hints. Revisit if it starts exporting flow vectors |
| `vqe_amf` | no | `pelorus_denoise_vulkan` | keep: AMF VQEnhancer only (AMD driver, one `attenuation` knob from 0.02 to 0.4); no portable Vulkan path |

## Open

- The release-gated acceptance in #213 (full replay on the new base, rebase notes with the base commit) waits for the first FFmpeg release after `n9.0.2`.
- `0009b` needs a rebase onto the new Vulkan encode code once that release exists; the shared-series failures come first.
