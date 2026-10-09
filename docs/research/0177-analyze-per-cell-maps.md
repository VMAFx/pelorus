<!-- markdownlint-disable MD013 MD060 -->
# Per-cell banding, variance and edge maps from vf_pelorus_analyze

**Date:** 2026-10-09

**Decision:** [ADR-0177](../adr/0177-analyze-per-cell-maps.md), issue
[#219](https://github.com/VMAFx/pelorus/issues/219)

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`) with the 21-patch stack, libpelorus
0.3.0 plus this change, NVIDIA RTX 4090 (proprietary driver), Intel Arc A380
(Mesa ANV, Xe KMD) and AMD Raphael iGPU (Mesa RADV), Vulkan validation layer
on for every GPU row. The workstation ran other jobs (load average 17 to 48),
so no timing is reported.

## Verdict

The filter now writes the three maps the ABI 1.0 sections point at, and the
VMAFx reader rules accept them. A banded ramp scores a banding-map mean of
0.952 against 0.000 for flat grey with heavy noise, the same on all three
devices. The ROI output and the `lavfi.pelorus.*` scalars are byte-identical
to the previous filter at the default `cell=32`.

## Before: the maps were absent

The previous filter (`origin/master` stack) on the matrix ramp, decoded by
`ffmpeg-patches/test/analyze-maps-check.py`:

```text
$ ffmpeg-master ... -vf "format=yuv420p,hwupload,pelorus_analyze_vulkan,hwdownload,format=yuv420p,showinfo" ...
$ analyze-maps-check.py maps maps-ramp.stderr --grid 8x4 --band-min 0.8
FAIL: frame 0: no per-cell maps (all map offsets and sizes are zero)
```

The header already carried an 8x4 grid, so VMAFx's reader computed 32 cells
and then took its frame-scalar fallback for both maps (offset 0).

## After: the matrix rows

`vulkan-format-matrix.sh` with `PELORUS_VALIDATE=1`, one device at a time, on
the 21-patch stack (patch 0021 removed the 09059/09064 allowances; no row needs
them).
Every row passed on devices 0 (RTX 4090), 1 (Arc A380) and 2 (RADV), and the
decoded values were identical on all three:

| Row | Input | Grid | Banding mean | Variance mean | Edge mean |
| --- | --- | --- | --- | --- | --- |
| `maps-ramp` | 256x128 ramp `16 + 64x/W`, `yuv420p` | 8x4 | 0.952 | 0.000074 | 0.000 |
| `maps-noise` | 256x128 grey, `noise=alls=40:allf=u` | 8x4 | 0.000 | 0.002134 | 0.105 |
| `maps-ramp-p010le` | the ramp as `p010le` | 8x4 | 0.952 | 0.000075 | 0.000 |
| `maps-ramp-cell8` | the ramp, `cell=8` | 32x16 | 0.085 | 0.000000 | 0.000 |
| `maps-ramp-cell64` | the ramp, `cell=64` (2x2 span per invocation) | 4x2 | 0.788 | 0.000320 | 0.000 |
| `maps-one-cell` | 16x16 ramp, smaller than one cell | 1x1 | 0.788 | 0.000320 | 0.004 |
| `maps-partial` | 100x70 ramp, partial last column and row | 4x3 | 0.731 | 0.000095 | 0.000 |
| `maps-max-grid` | 8192x8192 grey, `cell=8` | 1024x1024 | 0.000 | 0.000000 | 0.000 |
| `maps-off` | the ramp, `maps=0` | 8x4 | no maps, all six fields 0 | | |
| `maps-oversized-grid` | 8200x8192 grey, `cell=8` | refused | `8200x8192 at cell=8 exceeds the grid limit of 1048576 cells and 65535 per side; use a larger cell` | | |

For every blob with maps the checker applied the `pel_blob_map()` rules (size
= cells x element, 8-aligned, past the directory, inside `total_size`,
`total_size` = blob length) and checked that the variance-map mean equals
`global_variance` and the edge-map mean equals `edge_density` within rounding.
The only validation message on analyze rows is
`VUID-VkImageMemoryBarrier2-srcAccessMask-07454`, which the previous filter
emits too (checked on the master build) and which the allow-list carries
under #214.

The rest of the matrix (format equivalence, U/V and RGBA preservation, plane
masks, denoise, dehalo) passed unchanged on all three devices.

## Unchanged output at the default cell

Both builds on the RTX 4090, `roi=1`, `metadata=mode=print,showinfo`, all
`lavfi.pelorus.*` keys and every ROI rectangle compared with `diff`:

| Input | Lines compared | Result |
| --- | --- | --- |
| `testsrc2` 1280x720, 4 frames, `noise=alls=6` | 156 | identical |
| ramp 640x360, 2 frames | 78 (64 ROI rectangles) | identical |

The shader now accumulates each invocation's pixels locally and adds the
partial sums once. The per-pixel terms are truncated to `uint` exactly as
before, so the integer totals cannot differ.

## Light grain and the cell size

The per-cell score is the existing detector's (ADR-0133). Two properties show
up in the maps:

- Flat grey with uniform noise, 256x128, 8-bit: `alls=12` gives a banding-map
  mean of 0.881, `alls=20` 0.652, `alls=30` 0.194, `alls=40` 0.000. Light grain
  falls in the variance window of a shallow ramp.
- The detector constants are per cell. The ramp steps one code value every 4
  pixels: an 8-pixel cell holds two steps (variance near the constant floor,
  inter-cell step of 2 codes), so `cell=8` scores 0.085 where `cell=32` scores
  0.952.

## Compatibility

- No wire change: the six offset/size fields exist since ABI 1.0, and
  `PELORUS_ABI_MINOR` stays 4. The VMAFx reader
  (`core/src/feature/perceptual_weight.c`, `origin/master`) requires
  `offset != 0`, `size >= cells` (`size >= 4 x cells` for the float map) and
  the map inside the image; the matrix blobs meet all three.
- The filter compiles against the ABI 1.3 headers of libpelorus v0.3.0
  (`vf_pelorus_analyze_vulkan.c` with `-I` pointing at the `v0.3.0` include
  tree) and needs no function newer than `pel_blob_pack()` and
  `pel_blob_find_section()`, so the configure floor `libpelorus >= 0.2.0`
  holds.
- Side data per frame: 168 bytes of header, directory and sections, plus 6
  bytes per cell and at most 14 bytes of alignment; 12 KiB at 1080p and 48 KiB at 2160p with `cell=32`.
  The readback buffer is unchanged (20 bytes per cell).

## Reproduce

```sh
FFMPEG_BIN=/path/to/patched/ffmpeg VULKAN_DEVICE=0 PELORUS_VALIDATE=1 \
  ffmpeg-patches/test/vulkan-format-matrix.sh
python3 -I ffmpeg-patches/test/analyze-maps-check.py --self-test
meson test -C build --suite=fast analyze-maps interop-abi
```
