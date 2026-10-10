<!-- markdownlint-disable MD013 -->
# vf_pelorus_dehalo_vulkan

Anime / 2D **dehalo + dering**, run as a zero-copy pre-encode pass in VRAM. A
single-pass Vulkan compute port of HAvsFunc's `DeHalo_alpha` + `FineDehalo`. It
removes the bright/dark **ringing band** ("halos") that upstream compression and
sharpening leave in the flat field next to hard line-art — anime's signature
artefact, which also costs the encoder bits coding the per-edge overshoot every
frame. See [ADR-0123](../adr/0123-anime-dehalo.md) for the design.

It is the first stage of the planned `tune=anime` pipeline. It processes luma by
default; chroma passes through under that default, but is supported when selected
with `planes`. A selected semi-planar chroma plane processes both U and V.

## Algorithm

One Vulkan compute dispatch. Samples are converted from the Vulkan storage
domain to logical `[0,1]` before arithmetic:

1. **Edge-ring gate** — a `FineDehalo` Sobel edge mask in step units (Sobel
   magnitude / 4, so an ideal step of height *s* reads *s*). A pixel whose own
   step exceeds `edge` is line-art and is left alone. The halo band is every
   other pixel with a line pixel within `ring` px along the horizontal or
   vertical axis; the scan tests the edge mask itself, so bright halos (next to
   dark lines) and dark halos (next to bright lines) qualify alike. A pixel with
   a line on both sides of an axis sits between two close edges (a thin line's
   core, the gap between parallel lines): that is `FineDehalo`'s exclusion zone
   and is left alone too. Open flats and gradients have no line nearby and pass
   through.
2. **Halo-free target** — a box blur of the selected component (`blur`
   radius). The blurred field is what the line-adjacent band should look like
   with the ring gone.
3. **Sensitivity mask `so`** — `DeHalo_alpha`'s ratio of the local contrast the
   blur removes to the source contrast, shaped by the `lowsens` floor and the
   `highsens` gain. Where `so` is high the blur would erase detail the source
   has, so the source is kept; where it is low the blur keeps the structure and
   becomes the target (`DeHalo_alpha`'s `MaskedMerge(halos, clp, so)`).
4. **Remove-only asymmetric clamp** — the pixel is clamped into the range that
   target spans over its 3×3 window (`DeHalo_alpha`'s `Repair` mode 1), then
   moved toward the clamped value with separate `darkstr` (dark halos) and
   `brightstr` (bright halos) strengths. A pixel inside the blurred envelope,
   which includes every line transition, does not move: the filter can only
   reduce the ring.

[ADR-0163](../adr/0163-dehalo-gate-and-pull.md) records why the gate and the
pull take this form and the measurements behind it. That change is a harm-fix:
it stops the filter rewriting line-art (the earlier gate changed stroke pixels
by up to 129 codes) and leaves clean sources intact. It does **not** remove
halos: halo RMS at the defaults drops by at most 1.3% on the ADR-0163 synthetic
set. Real halo removal (a feathered `FineDehalo`-style mask) is a tracked
follow-up.

The shipped shader is
`ffmpeg-patches/files/vulkan/pelorus_dehalo.comp.glsl`; the similarly named
`libpelorus/shaders/*.comp` file is a compile-checked standalone reference, not
a second shipped implementation.

## Options

All thresholds are normalized in `[0,1]`, independent of bit depth.

| Option | Default | Range | Meaning |
| --- | --- | --- | --- |
| `blur` | 2 | 1–8 | halo-blur radius in pixels (the halo-free target) |
| `darkstr` | 1.0 | 0–1 | pull strength for **dark** halos |
| `brightstr` | 1.0 | 0–1 | pull strength for **bright** halos |
| `lowsens` | 0.0625 | 0–1 | sensitivity floor — below this removed-contrast, leave the pixel alone |
| `highsens` | 0.5 | 0–4 | sensitivity gain on the removed-contrast mask |
| `edge` | 0.08 | 0–1 | edge step (Sobel magnitude / 4) above which a pixel is line-art (drives the ring gate) |
| `ring` | 2.0 | 1–8 | edge-mask dilation — the halo-band half-width in pixels |
| `planes` | 0x1 | 0x0–0xF | physical planes to process (default `0x1` = luma); selecting a semi-planar chroma plane processes both U and V components |
| `tile` | 0 | 0–1 | cache the filter's read window in shared memory ([ADR-0139](../adr/0139-dehalo-shared-mem-tile.md)). Output is **bit-identical**. Since [ADR-0163](../adr/0163-dehalo-gate-and-pull.md) only ring pixels read the box-mean window, so the win depends on the radii: on an Arc A380 at 1080p (wall time including upload/download), `tile=1` is about 8% slower at the default radii and about 25% faster at `blur=8:ring=8`. Default off — enable on weak / integrated / mobile GPUs with large radii |
| `tiling` | optimal | optimal, drm | output pool: `optimal` is FFmpeg's pool; `drm` is a DRM-format-modifier pool that `hwmap=derive_device=vaapi` maps to VAAPI and QSV without `hwdownload` ([Vulkan output pools](../backends/vulkan-drm-modifiers.md)) |
| `drm_modifiers` | empty | up to 64, `\|`-separated | with `tiling=drm`: DRM format modifiers the consumer imports, 0x hex; empty = any usable modifier, `0x0` = LINEAR; ignored (with a warning) without `tiling=drm` |

`darkstr`/`brightstr` are the main intensity knobs; `edge` and `ring` shape
*where* the pull is allowed (raise `edge` to gate to only the hardest lines;
raise `ring` if the halo band is wide). The vmafx `vmaf-tune` autotune
([ADR-0106](../adr/0106-autotune-control-plane.md)) can sweep them against the
encoded-quality oracle once the defaults are tuned on content.

## Output

A filtered frame — pixels only, **no side data**. Dehalo is a pure transform; it
does not link libpelorus and emits no interop section.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_dehalo_vulkan=blur=2:darkstr=1.0:brightstr=1.0:edge=0.08:ring=2,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 out.mkv
```

## Pipeline placement

Dehalo runs on the **source** ring before any flattening stage, so it runs
*before* deband: it removes the line-adjacent overshoot first, then deband sees a
clean flat field and re-injects its dither.

```text
hwupload → pelorus_dehalo → pelorus_deband → (hwdownload) → encoder
```

## Interactions and limits (honest scope)

- **Luma by default** — `planes=0x1` targets the dominant anime luma-edge
  artefact. Chroma processing is available by selecting its physical plane; on
  NV12/P010/P012 that selection processes both U and V.
- **Runs before deband** in the anime chain (see above), so deband's flat-test
  is not fooled by the residual ring.
- **Steep halo flanks count as line-art.** The halo pixel right next to a line
  usually has its own step above `edge`, so the gate protects it with the line.
  At the defaults the filter clears the outer ring band and leaves that flank;
  on the ADR-0163 synthetic set the flank pixels carry 93–97% of the remaining
  halo energy, so overall halo RMS drops by at most a few percent. Raising
  `edge` releases more flank pixels; the measurements are in
  [ADR-0163](../adr/0163-dehalo-gate-and-pull.md).
- **On-device check** — `ffmpeg-patches/test/vulkan-format-matrix.sh` asserts
  that the gate clears a ringed line's outer overshoot, leaves line cores and a
  1-px stroke within one code, and that `tile=0` and `tile=1` match byte for
  byte.
- **Honest caveat — defaults are not yet content-tuned.** The algorithm port is
  compile-verified and glslang-clean, but the perceptual tuning of the defaults
  against real anime and the quality proof against a clean ground truth are the
  documented follow-up. **No BD-rate or quality number is claimed yet.** The
  proof must be measured under the [ADR-0111](../adr/0111-benchmark-methodology.md)
  methodology:
  - **SSIMULACRA2** against a clean anime reference — overall perceptual fidelity
    of the dehaloed frame;
  - **edge-region VMAF-NEG** — confirm the ring is removed without softening the
    line-art it hugs;
  - **CAMBI** on the flat fields — confirm the pull does not introduce banding
    where it flattened the halo.
