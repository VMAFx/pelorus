<!-- markdownlint-disable MD013 -->
# vf_pelorus_deblock_vulkan

Re-encode **deblock / dering**, run as a zero-copy pre-encode pass in VRAM. A
single-pass Vulkan compute filter that smooths the prior codec's DCT block-edge
discontinuities ("blocking") so the new encoder does not waste bits coding them
as false residual. See [ADR-0127](../adr/0127-deblock.md) for the design.

The largest real-world pre-encode corpus is re-transcode of already-compressed
sources. Those carry a block-grid step at the boundaries of the prior codec's
transform; the new encoder spends real bits coding that step as residual in
every frame. This stage removes it before the encoder sees the pixels. **Luma
only; chroma passes through.**

## Algorithm

One Vulkan compute dispatch, bit-depth-agnostic (`FF_VK_REP_FLOAT` UNORM). At
the prior codec's block grid (`bsize`), for each pixel within `edge` of a block
boundary, apply a weak `[1 2 1]` low-pass **across** the boundary, **gated by the
cross-boundary step**:

1. **Boundary band** — the pixel's distance to the nearest block edge,
   `min(pos % bsize, bsize - pos % bsize)`, is computed per axis. When it is
   `<= edge`, the pixel is in the deblocked band around that boundary.
2. **Step gate** — sample the two pixels straddling the boundary. If their
   absolute difference is **below `thr`**, the step is a block-edge artefact and
   is smoothed; **above `thr`** it is real structure and is left untouched.
3. **Conditional `[1 2 1]` blend** — where the gate passes, the pixel is blended
   toward the `(a + 2·c + b) · 0.25` low-pass across the boundary by `str`. The
   horizontal and vertical passes are independent, so a grid corner is smoothed
   on both axes.

The standalone reference shader is `libpelorus/shaders/pelorus_deblock.comp`; the
filter's inline GLSL implements the same algorithm (kept in lockstep, AGENTS hard
rule 4). The only intended difference is the working domain: the `.comp` reads
`r16ui` and normalises by 65535, the inline form reads `FF_VK_REP_FLOAT` (UNORM)
already in `[0,1]`.

## Options

Thresholds are normalized in `[0,1]`, independent of bit depth.

| Option | Default | Range | Meaning |
|---|---|---|---|
| `bsize` | 8 | 2–64 | prior codec block size (DCT grid) — the boundary spacing |
| `edge` | 1 | 0–8 | half-width of the deblocked band around a boundary (px) |
| `thr` | 0.06 | 0–1 | cross-boundary step below which it is an artefact (smooth); above it, real structure (preserve) |
| `str` | 0.6 | 0–1 | deblock strength — blend toward the low-pass (0 = off, 1 = full) |
| `planes` | 0x1 | 0x0–0xF | planes to process (bitmask; default `0x1` = luma only) |

`bsize` matches the prior codec's transform grid (8 for legacy H.264/MPEG-style
8×8 DCT — the default). `thr` is the key knob: raise it to deblock more
aggressively (treats larger steps as artefacts), lower it to preserve more
structure. `str` scales how hard the smoothed pixels are pulled. The vmafx
`vmaf-tune` autotune ([ADR-0106](../adr/0106-autotune-control-plane.md)) can
sweep these against the encoded-quality oracle once the defaults are tuned on
content.

## Output

A filtered frame — pixels only, **no side data**. Deblock is a pure transform; it
does not link libpelorus and emits no interop section.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_deblock_vulkan=bsize=8:thr=0.06:str=0.6,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 out.mkv
```

## Pipeline placement

Deblock runs **early**, before any flattening stage — in particular **before
deband**. It cleans the prior codec's hard block edges first; deband then sees a
clean flat field and re-injects its dither without the residual block step
fooling its flat-test.

```
hwupload → pelorus_deblock → pelorus_deband → (hwdownload) → encoder
```

## Interactions and limits (honest scope)

- **Luma only** — chroma passes through unchanged (`planes` defaults to `0x1`).
  Blocking is dominated by the luma transform grid; chroma deblock is a deferred
  follow-up (ADR-0127).
- **Runs before deband** (see above), so the block step does not fool deband's
  flat-test.
- **Complements, does not duplicate, deband** — deblock targets the block-grid
  *boundaries*; deband flattens the slow contour stair-stepping *inside* flat
  regions. Different artefacts, different stages.
- **Honest caveat — defaults are not yet content-tuned.** The algorithm is
  build-verified and glslang-clean, but the perceptual tuning of the defaults
  against a real re-transcode corpus and the quality proof are the documented
  follow-up. **No SSIMULACRA2 or BD-rate number is claimed yet.** The proof must
  be measured under the [ADR-0111](../adr/0111-benchmark-methodology.md)
  methodology:
  - **SSIMULACRA2** on a re-transcode corpus (already-compressed source →
    new-codec encode) against the clean ground truth — overall perceptual
    fidelity of the deblocked re-encode;
  - **BD-rate** with and without the deblock stage — the bitrate saved by not
    coding the prior codec's block edges as false residual.
