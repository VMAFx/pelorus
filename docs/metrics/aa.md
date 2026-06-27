<!-- markdownlint-disable MD013 -->
# `pelorus_aa_vulkan` — anime warp anti-aliasing + line-darkening

A single-pass Vulkan compute filter that removes the stair-stepped aliasing
("jaggies") anime line-art accumulates through repeated scale/encode, and
optionally darkens real lines so they stay crisp. A zero-copy GPU port of
**awarpsharp2** (warp-based AA) + **FastLineDarken** (line-darkening). It
operates on `AV_PIX_FMT_VULKAN` frames and never leaves VRAM
(`FF_VK_REP_FLOAT` UNORM, bit-depth-agnostic). Decision:
[ADR-0124](../adr/0124-anime-aa.md).

## What it does

Anti-aliasing is **luma only**; chroma planes pass through unchanged. Per luma
pixel, in one pass:

1. **Blurred edge-strength map.** A Sobel magnitude per pixel is clamped to a
   ceiling (`thresh`) and averaged over a `(2·blur+1)²` box to produce a smooth
   edge-strength field.
2. **Warp.** The central-difference gradient of that field points toward the
   nearest line. The sampling position is displaced by `depth ×` that gradient
   and the source luma is **bilinearly resampled** at the displaced position —
   pulling stair-stepped samples onto the line (anti-aliasing + slight thinning)
   with no high-pass ringing.
3. **Optional line-darkening.** When `darkstr > 0`, a pixel whose Sobel
   magnitude exceeds `edge` is treated as a real line and the warped value is
   blended toward the local 3×3 minimum by `darkstr`, deepening the dark side of
   the edge. With `darkstr = 0` (default) this branch is skipped and the filter
   is pure warp-AA.

Output is clamped to `[0,1]` in the Vulkan float image domain. The standalone
reference shader is `libpelorus/shaders/pelorus_aa.comp`; the filter's inline
GLSL implements the same algorithm (kept in lockstep, AGENTS hard rule 4).

## Options

Thresholds are normalized in `[0,1]`, independent of bit depth. Defaults and
ranges below are the filter's actual `AVOption` table
(`ffmpeg-patches/files/vf_pelorus_aa_vulkan.c`).

| Option | Type | Default | Range | Meaning |
|---|---|---|---|---|
| `blur` | int | 2 | 0–8 | edge-map blur radius in pixels (box is `(2·blur+1)²`) |
| `depth` | float | 8.0 | 0–64 | warp displacement scale (pixels per unit edge-map gradient) |
| `thresh` | float | 0.5 | 0–1 | edge-map clamp ceiling (normalized) — caps any single edge's contribution to the blur |
| `darkstr` | float | 0.0 | 0–1 | line-darkening strength; `0` = off (pure warp-AA) |
| `edge` | float | 0.08 | 0–1 | Sobel magnitude above which a pixel counts as a line (gates line-darkening) |
| `planes` | int bitmask | 0x1 | 0–0xF | planes to process; default `0x1` = luma only |
| `fast` | bool | 0 | 0–1 | hoist the redundant sobel-mag into shared memory ([ADR-0140](../adr/0140-aa-sobel-mag-hoist.md)). aa is ALU-bound — `sobel_mag` is recomputed ~1156×/px across the overlapping edge-map windows; `fast=1` computes each cell's sobel once per workgroup and reduces from the cache. **Bit-identical**; a large speedup on **every** GPU (**12.6× on Arc A380, 2.6× on the 4090** at `darkstr=0`). Opt-in; default off |

`depth` is the main knob: raise it for stronger de-aliasing/thinning, lower it if
lines start to over-thin or wobble. `blur` widens the edge map (a larger,
smoother warp field). Enable line-darkening with `darkstr` and gate it with
`edge`.

## Example

```bash
ffmpeg -init_hw_device vulkan -hwaccel vulkan -hwaccel_output_format vulkan \
       -i input.mkv \
       -vf "hwupload,pelorus_aa_vulkan=blur=2:depth=8:darkstr=0.3:edge=0.08,hwdownload,format=yuv420p" \
       -c:v hevc_nvenc -cq 24 out.mkv      # codec-agnostic; or av1_nvenc / hevc_qsv
```

Output: a de-aliased (and optionally line-darkened) video stream. The filter
adds **no side data** — it is a pure pixel transform, so it does not link
`libpelorus` and emits nothing on the interop channel. It logs nothing on the
happy path; errors surface as a non-zero ffmpeg exit with a `[pelorus_aa_vulkan]`
message.

## Pipeline placement

aa is a stage of the anime `tune` chain. It runs **after** dehalo
([ADR-0123](../adr/0123-anime-dehalo.md)) — dehalo removes the ringing/halos
around edges first, then aa de-aliases and darkens the now-clean lines — and
typically after any deband/denoise stage. Keep it inside the VRAM segment of the
graph; pair `hwupload`/`hwdownload` only at the pipeline edges, never mid-graph
(it breaks zero-copy):

```
hwupload → pelorus_dehalo_vulkan → pelorus_aa_vulkan → (deband/denoise) → hwdownload → encoder
```

## Honest scope (tuning caveat)

The algorithm port is **compile-verified and glslang-clean**, kept in lockstep
with the reference shader. **No quality number is claimed.** The default option
values are carried over from the awarpsharp2 / FastLineDarken conventions and
have **not** been perceptually tuned on real anime content on the GPU. The
on-content tuning of the defaults and the proof against clean anime ground
truth — **SSIMULACRA2** and **edge-region VMAF-NEG** under the
[ADR-0111](../adr/0111-benchmark-methodology.md) methodology — are the documented
follow-up. Until then, treat the defaults as a starting point and tune `depth` /
`blur` / `darkstr` per title.
