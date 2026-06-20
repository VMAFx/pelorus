<!-- markdownlint-disable MD013 -->
# `pelorus_scenecut` — scene-cut → forced IDR (encoder steering)

A tiny **metadata-only** consumer that turns the scene-cut flag
`vf_pelorus_mc_vulkan` already measures into a **forced keyframe at the cut**. It
is **not** a Vulkan filter — no shader, no GPU work, no pixels touched
(`AVFILTER_FLAG_METADATA_ONLY`). On a frame `vf_pelorus_mc` flagged as a cut, it
sets `frame->pict_type = AV_PICTURE_TYPE_I` (+ the key flag) so the downstream
encoder opens a fresh GOP exactly at the cut instead of predicting across the
unrelated previous picture and re-keying at the next periodic IDR. Decision:
[ADR-0126](../adr/0126-scenecut-idr.md).

## What it does

`vf_pelorus_mc_vulkan` (ADR-0116) sets `has_scene_cut` in its `PEL_SEC_MOTION`
interop section when the mean residual SAD is high — no coherent match anywhere,
the signature of a cut. That flag rides the frame as UUID-keyed side data
(`AV_FRAME_DATA_SEI_UNREGISTERED`) and survives the graph via
`av_frame_copy_props`.

This filter reads that flag (via `pel_blob_find_section`, `PEL_SEC_MOTION`) and,
on a cut frame, marks the frame as a forced keyframe:

- `frame->pict_type = AV_PICTURE_TYPE_I`
- `frame->flags |= AV_FRAME_FLAG_KEY`

It is **codec-agnostic**: a forced `pict_type == I` is honoured as a keyframe by
every target encoder — x264, x265, NVENC, QSV, SVT-AV1 — with **no per-encoder
patch**, because `pict_type` is the standard libavcodec keyframe-request
mechanism. The filter links `libpelorus` (to parse the side data) but is **not**
gated on Vulkan.

## Options

| Option | Type | Default | Range | Meaning |
|---|---|---|---|---|
| `force_idr` | bool | 1 (on) | 0–1 | set `pict_type = I` on a Pelorus scene-cut frame; `0` = transparent pass-through |

Defaults match the filter's actual `AVOption` table
(`ffmpeg-patches/files/vf_pelorus_scenecut.c`). With `force_idr=0` the filter
does nothing — the frame, including its side data, passes through untouched.

## Output

A frame with **no new side data** — only `pict_type`/`flags` are set on cut
frames. The filter emits nothing on the interop channel; it is a pure consumer.
Non-cut frames pass through unchanged.

## Usage

Place it **after `hwdownload`**, just before the encoder. `vf_pelorus_mc_vulkan`
runs upstream in VRAM with `meta=1` to emit `PEL_SEC_MOTION`; the side data is
metadata and rides the frame through `hwdownload` via `av_frame_copy_props`, so
the consumer sees the flag while working on a plain system-memory frame (it needs
no Vulkan device of its own):

```bash
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "pelorus_mc_vulkan=meta=1,hwdownload,format=yuv420p,pelorus_scenecut" \
       -c:v hevc_nvenc -cq 28 out.mkv      # or x264 / x265 / hevc_qsv / libsvtav1
```

The encoder opens a fresh GOP on every frame the cut detector flagged.

## Interactions and limits (honest scope)

- **Needs `vf_pelorus_mc_vulkan` upstream with `meta=1`.** The scene-cut flag is
  produced only by the motion estimator. Without an upstream `mc` emitting
  `PEL_SEC_MOTION`, there is no side data to read and the filter is a no-op (it
  never forces a keyframe).
- **Run it after `hwdownload`.** The motion side data is metadata, so it survives
  `hwdownload`; place the consumer just before the encoder, on plain frames. It
  does no GPU work and needs no Vulkan device.
- **Codec-agnostic, no patch.** `pict_type == I` is the standard keyframe-request
  path; x264/x265/NVENC/QSV/SVT-AV1 all honour it. No per-encoder fork patch is
  shipped or needed (unlike the ROI/delta-QP tiers — see
  [ADR-0114](../adr/0114-encoder-steering.md)).
- **Detector fidelity inherits from `mc`.** The cut flag is the mean-residual-SAD
  heuristic in `vf_pelorus_mc` ([docs/metrics/mc.md](mc.md)); false positives /
  negatives there propagate here as spurious / missed IDRs. Tuning that threshold
  is a `mc`-side follow-up.
- **Honest caveat — no BD-rate number is claimed.** The producer
  (`mc.has_scene_cut`), this consumer, and the `pict_type == I` mechanism are
  **build-verified** — the flag is read and the picture type is set. The
  **end-to-end BD-rate proof — a measured A/B of scene-cut-aligned GOPs vs
  periodic IDR on real multi-shot content — is the documented follow-up**, to be
  measured under the [ADR-0111](../adr/0111-benchmark-methodology.md) methodology
  (BD-rate at iso-quality over an RD-ladder on multi-shot footage). No quality or
  rate number ships with this filter.
