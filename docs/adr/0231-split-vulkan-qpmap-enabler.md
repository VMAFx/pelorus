<!-- markdownlint-disable MD013 -->
# ADR-0231: Split patch 0009 into a libavutil enabler (0009a) and the Pelorus encode path (0009b)

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `ffmpeg`, `patch-stack`, `vulkan`, `upstream`

## Context

Patch 0009 makes `h264_vulkan`, `hevc_vulkan` and `av1_vulkan` honor ROI side data through `VK_KHR_video_encode_quantization_map` ([ADR-0114](0114-encoder-steering.md) Tier 2, [ADR-0166](0166-vulkan-qpmap-activation.md)). Three of its nine files are libavutil: `hwcontext_vulkan.c` chains, copies and enables the `videoEncodeQuantizationMap` feature and lists the extension as optional, `vulkan_functions.h` adds the `FF_VK_EXT_VIDEO_ENCODE_QUANTIZATION_MAP` flag, and `vulkan_loader.h` maps the extension name to it. Neither `n9.0.2` nor FFmpeg master mentions quantization maps, so this part is new code, not a backport. It names nothing of Pelorus except two comments, and it is the first commit an upstream ROI-to-quantization-map pull request needs.

[ADR-0185](0185-shared-ffmpeg-fix-series.md) sends generic FFmpeg fixes to the shared fix series (VMAFx/ffmpeg-patches). The enabler is generic code but not a fix: stock FFmpeg has no user of the feature, and the series holds fixes only.

## Decision

1. **Split.** `files/vulkan-qpmap-enabler.patch` holds the three libavutil diffs and is applied as its own commit, patch `0009a-avutil-vulkan-enable-qpmap.patch`, with an FFmpeg-style subject (`avutil/hwcontext_vulkan: enable VK_KHR_video_encode_quantization_map`) and no Pelorus name. `files/vulkan-pelorus-qpmap.patch` keeps the six libavcodec and build files as patch `0009b-vulkan-pelorus-qpmap.patch`. The stack grows from 22 to 23 patches; every other shipped name keeps its number.
2. **Stays in this stack.** 0009a is not moved to the shared series, as an exception to ADR-0185's routing rule: the series holds FFmpeg fixes, and an enabler whose only user is 0009b is Pelorus scope. It goes upstream as the first commit of the ROI-to-quantization-map pull request, where it has a user.
3. **Applied tree unchanged in code.** Against the old 0009, the split changes two comments in `hwcontext_vulkan.c`: the feature comment drops the Pelorus prefix and keeps its VUID reason, and the Pelorus comment over the extension table entry is removed. No code line changes.
4. **Checked.** `scripts/check-vulkan-qpmap-contract.py` reads both patches and refuses a split where 0009a touches a file outside libavutil or names Pelorus, or 0009b touches libavutil; three planted mutations prove each refusal (fast test `vulkan-qpmap-contract`).
5. **Generator.** `generate.sh` writes `git format-patch` output into a staging directory and moves each file to its `series.txt` name by position. With the split, generated positions after 0009 map to lower shipped numbers, so the old last-to-first rename inside one directory no longer avoids collisions.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Move 0009a to the shared fix series | One copy for every consumer | The series holds fixes only; no consumer but Pelorus uses the feature | Out of the series' scope |
| Keep 0009 whole | No renumbering, no generator change | The upstream commit has to be cut out by hand each time; libavutil and libavcodec review mix | The split is cheap now and the upstream route needs it |
| Renumber the whole stack (0010 to 0024) | Plain numbers, no letter suffix | Every shipped name changes; downstream notes and docs point at the old numbers | Breaks every reference for no gain |

## Consequences

- **Positive**: 0009a is upstream-ready as is; the contract check keeps the libavutil and libavcodec parts apart.
- **Negative**: the stack carries a letter suffix (0009a/0009b), and the count check in `ffmpeg-patches/test/build-and-run.sh` moves to 23.
- **Neutral / follow-ups**: drop 0009a once FFmpeg merges the enabler, and drop 0009b's matching parts once the encode path lands upstream.

## References

- Issue [#308](https://github.com/VMAFx/pelorus/issues/308).
- Maintainer decision of 2026-10-10 on the upstream route for the enabler (paraphrased): keep it in the Pelorus stack as its own patch and send it upstream with its first user, since the shared series takes fixes only.
- `ffmpeg-patches/files/vulkan-qpmap-enabler.patch`, `ffmpeg-patches/files/vulkan-pelorus-qpmap.patch`, `ffmpeg-patches/generate.sh`, `scripts/check-vulkan-qpmap-contract.py`.
