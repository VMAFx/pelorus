<!-- markdownlint-disable MD013 -->
# vf_pelorus_borderfix_vulkan

Dirty-line / **border repair**, run as a zero-copy pre-encode pass in VRAM. A
single-pass Vulkan compute filter — the GPU equivalent of FFmpeg's
`fillborders=smear` — that replaces the garbage rows/columns at the frame edge
with the nearest clean interior pixel. See
[ADR-0128](../adr/0128-borderfix.md) for the design.

Cropped, telecined, and analog-captured sources carry a dirty band at the frame
edge: half-pixels from an off-grid crop, clamp/repeat lines from an upstream
resize, head-switching noise on a VHS capture. The encoder cannot tell that band
from real signal, so it spends bits coding it as residual every frame, and the
band reads as a dirty border. This stage smears the clean edge outward over the
band before the encoder sees the pixels. **All planes are processed by default;
the band widths are interpreted in each plane's own pixels.**

## Algorithm

One Vulkan compute dispatch, bit-depth-agnostic (`FF_VK_REP_FLOAT` UNORM). Given
the per-edge dirty-band widths `left`, `right`, `top`, `bottom` and a plane of
size `w × h`, the **clean interior rectangle** is
`[left, w − 1 − right] × [top, h − 1 − bottom]`. For each output pixel at
`(x, y)`:

1. **Clamp the read coordinate onto the clean rect** —
   `cx = clamp(x, left, w − 1 − right)`,
   `cy = clamp(y, top, h − 1 − bottom)`. A pixel already inside the clean rect
   maps to itself; a pixel inside a dirty band maps to the nearest pixel on the
   clean edge.
2. **Smear the clean edge outward** — store the input sample read from the
   clamped coordinate. Every pixel in the dirty band is thus replaced by the
   nearest clean interior pixel — the good edge is extended over the garbage,
   exactly as `fillborders=smear` does on the CPU.

The smear is **exact and deterministic** — no threshold, no blend, no
estimation. With all widths at their default of `0` the filter is byte-identical
pass-through. A plane not selected in `planes` is copied through unchanged.

The standalone reference shader is `libpelorus/shaders/pelorus_borderfix.comp`;
the filter's inline GLSL implements the same clamp-and-smear (kept in lockstep,
AGENTS hard rule 4). The only intended difference is the working domain: the
`.comp` reads `r16ui` and normalises by 65535, the inline form reads
`FF_VK_REP_FLOAT` (UNORM) already in `[0,1]`.

## Options

Band widths are integers in **each plane's own pixels** (not luma pixels).

| Option | Default | Range | Meaning |
|---|---|---|---|
| `left` | 0 | 0–4096 | dirty band width on the left edge (this plane's px) |
| `right` | 0 | 0–4096 | dirty band width on the right edge |
| `top` | 0 | 0–4096 | dirty band height on the top edge |
| `bottom` | 0 | 0–4096 | dirty band height on the bottom edge |
| `planes` | 0xF | 0x0–0xF | planes to process (bitmask; default all) |

The widths are the **only** knob — set each to the measured thickness of the
source's dirty band on that edge. Default `0` is a no-op: nothing happens until
you tell the filter how wide the garbage is. Because the widths are per-plane
pixels, on 4:2:0 chroma the band is **half the luma band** — a 4-luma-pixel dirty
edge is 2 chroma pixels, so the same option value cleans the right fraction of
each plane automatically (see "Pipeline placement").

## Output

A filtered frame — pixels only, **no side data**. Borderfix is a pure transform;
it does not link libpelorus and emits no interop section.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_borderfix_vulkan=left=4:right=4,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 out.mkv
```

Here a 4-pixel dirty band on the left and right luma edges is smeared away. On
the 4:2:0 chroma planes the same `left=4`/`right=4` covers a 2-chroma-pixel band
(the widths are per-plane pixels), which is the correct chroma fraction of a
4-luma-pixel edge.

## Pipeline placement

Borderfix runs **first** — before any other Pelorus stage. The dirty edge band
should be cleaned before anything measures, flattens, or steers on the frame, so
the garbage never pollutes a downstream statistic (analyze's variance/banding
map, deband's flat-test) or gets smeared by a later spatial pass.

```
hwupload → pelorus_borderfix → pelorus_analyze → pelorus_deband → … → (hwdownload) → encoder
```

## Interactions and limits (honest scope)

- **All planes by default** (`planes` = `0xF`) — the dirty band is in chroma as
  well as luma, so cleaning only luma would leave a coloured fringe. The widths
  are per-plane pixels, so chroma is cleaned at the right scale automatically.
- **Runs first** (see above), so the dirty band never pollutes a downstream
  measurement or gets dragged inward by a later spatial filter.
- **Deterministic, no tuning needed for correctness.** The smear is exact — there
  is no threshold or blend to tune. The only choice is the **band widths**, which
  must match the source's actual dirty edge: too narrow leaves part of the band,
  too wide smears away real picture content. The filter cannot detect the band
  width for you; the default `0` is a no-op precisely because there is no safe
  universal width.
- **A BD-rate note is a nice-to-have follow-up, not a correctness gate.** Because
  the transform is deterministic, no quality A/B is required to trust it. A
  BD-rate measurement on a **dirty-border corpus** (re-encode of a
  cropped/telecined/analog-captured source, with and without the borderfix stage)
  under the [ADR-0111](../adr/0111-benchmark-methodology.md) methodology would
  quantify the bits saved by not coding the garbage edge as residual — a
  documented follow-up.
