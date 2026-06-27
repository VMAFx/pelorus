<!-- markdownlint-disable MD013 -->
# vf_pelorus_dehalo_vulkan

Anime / 2D **dehalo + dering**, run as a zero-copy pre-encode pass in VRAM. A
single-pass Vulkan compute port of HAvsFunc's `DeHalo_alpha` + `FineDehalo`. It
removes the bright/dark **ringing band** ("halos") that upstream compression and
sharpening leave in the flat field next to hard line-art — anime's signature
artefact, which also costs the encoder bits coding the per-edge overshoot every
frame. See [ADR-0123](../adr/0123-anime-dehalo.md) for the design.

It is the first stage of the planned `tune=anime` pipeline. **Luma only; chroma
passes through.**

## Algorithm

One Vulkan compute dispatch, bit-depth-agnostic (`FF_VK_REP_FLOAT` UNORM):

1. **Halo-free target** — a strong box blur of luma (`blur` radius). The blurred
   field is what the line-adjacent band should look like with the ring gone.
2. **Sensitivity mask `so`** — from the local contrast the blur removed; the
   `DeHalo_alpha` `lowsens` floor and `highsens` gain shape how much of the
   removed difference is treated as halo to pull versus protected detail.
3. **Remove-only asymmetric pull** — the pixel is pulled *toward* the blur, never
   away from it, with separate `darkstr` (dark halos) and `brightstr` (bright
   halos) strengths. Remove-only: the filter can only reduce the ring.
4. **Edge-ring gate** — a `FineDehalo` Sobel edge mask, dilated by `ring`,
   confines the pull to the halo band next to lines. Line-art (on the edge) and
   open gradients (far from any edge) are excluded — the drawing and smooth skies
   are protected by construction.

The standalone reference shader is `libpelorus/shaders/pelorus_dehalo.comp`; the
filter's inline GLSL implements the same algorithm (kept in lockstep, AGENTS hard
rule 4).

## Options

All thresholds are normalized in `[0,1]`, independent of bit depth.

| Option | Default | Range | Meaning |
|---|---|---|---|
| `blur` | 2 | 1–8 | halo-blur radius in pixels (the halo-free target) |
| `darkstr` | 1.0 | 0–1 | pull strength for **dark** halos |
| `brightstr` | 1.0 | 0–1 | pull strength for **bright** halos |
| `lowsens` | 0.0625 | 0–1 | sensitivity floor — below this removed-contrast, leave the pixel alone |
| `highsens` | 0.5 | 0–4 | sensitivity gain on the removed-contrast mask |
| `edge` | 0.08 | 0–1 | Sobel magnitude above which a pixel is line-art (drives the ring gate) |
| `ring` | 2.0 | 1–8 | edge-mask dilation — the halo-band half-width in pixels |
| `planes` | 0x1 | 0x0–0xF | planes to process (bitmask; default `0x1` = luma only) |
| `tile` | 0 | 0–1 | cache the box-blur window in shared memory ([ADR-0139](../adr/0139-dehalo-shared-mem-tile.md)). Output is **bit-identical**; box_blur re-reads an overlapping 17×17 window ~5× per pixel, so `tile=1` is a throughput win on bandwidth-limited GPUs (**−38%, 1.6×** on an Arc A380), ~neutral on cache-rich GPUs (a 4090's L2 already caches it). Default off — enable on weak / integrated / mobile GPUs (and `tune=anime`) |

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

```
hwupload → pelorus_dehalo → pelorus_deband → (hwdownload) → encoder
```

## Interactions and limits (honest scope)

- **Luma only** — chroma passes through unchanged (`planes` defaults to `0x1`).
  Anime halos are a luma-edge phenomenon; chroma dehalo is a deferred follow-up
  (ADR-0123).
- **Runs before deband** in the anime chain (see above), so deband's flat-test
  is not fooled by the residual ring.
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
