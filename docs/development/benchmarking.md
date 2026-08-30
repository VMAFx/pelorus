<!-- markdownlint-disable MD013 -->
# Benchmarking — proving the BD-rate win

### The grain axis (`--grain N`)

Reductive pre-encode filtering is the project's central claim, and ADR-0142's measured law
says its gain scales with how much removable impairment exists. Testing that needs a
*controlled* grain axis. `run-bench.py --grain N` provides one: it overlays seeded noise on
a **real** source and scores both arms against the clean original, so a denoiser is not
penalised for removing the impairment under test.

```bash
run-bench.py --src .bench-corpus/netflix-bar.yuv --grain 12 ... # implies --clean-reference
```

Verified monotonic and reproducible on `netflix-bar`:

| `--grain` | `grain_sigma` | `grain_flat` |
|---|---|---|
| 0 | 0.0124 | 0.600 |
| 4 | 0.0125 | 0.593 |
| 8 | 0.0138 | 0.572 |
| 12 | 0.0151 | 0.535 |
| 20 | 0.0182 | 0.171 |

Same seed, same bytes.

**What `N` is worth physically** — and where to stop — is profiled against **752 real
titles across 2230 scenes** in [grain-ladder.md](grain-ladder.md). The short version: use
`netflix-bar` as the grain base and stay at or below `--grain 16`; `bbb` starts at only 33%
flat coverage and its axis goes non-monotonic past 16.

The single most useful line in that profile for choosing bench material: **1080p masters
are 2.2x grainier than 4K masters** (median 0.0072 vs 0.0032, like-for-like native crops).
4K releases are denoised hard enough to compress, so they carry little of the removable
impairment this project exists to exploit.

**Why not just use a grainy clip?** Because there isn't one to pin. Measured at the
640x360/48-frame corpus workload:

| source | `grain_sigma` | `grain_flat` |
|---|---|---|
| real Blu-ray scan, 1994 remux | 0.0047–0.0101 | 0.96–0.98 |
| real Blu-ray scan, 2000 | 0.0079 | 0.94 |
| real film scan, 1971 | 0.0079 | 0.73 |
| `netflix-bar` (Chimera, VP9 webm) | 0.0124 | 0.60 |
| `bbb` (clean animation) | 0.0131 | 0.33 |

Real film scans measure **lower** grain than the public clips, not higher — modern Blu-ray
masters are frequently denoised, and any downscale to 640x360 attenuates what remains
(grain is a per-pixel high-frequency signal; averaging 3–6 pixels into one removes it).
The public 4K sets are all distribution re-encodes with the grain compressed out. So the
honest grain axis is injection, not selection.

**Why not `--synth noise`?** It noises a flat grey plate. That is the degenerate case: no
content to preserve, and the grain estimator starves on it (no 3x3 neighbourhood stays
under `edge_thr`, so `grain_flat` collapses to ~0 and the sigma reading becomes
meaningless). `--grain` keeps the estimate in the supported regime — see
[grain_estimate.md](../metrics/grain_estimate.md).

### What each corpus entry is for

| entry | source | what it exercises |
|---|---|---|
| `bbb` | Big Buck Bunny (360p) | clean animation — the low-impairment end. Reductive filters are expected to show ~0 here (ADR-0142) |
| `netflix-bar` | Netflix Chimera *BarScene* via Xiph.Org | real camera content: **2.4x the texture, 3.1x the variance and 1.7x the banding** of `bbb` at the same workload |
| `synth-banding` | lavfi gradient | deband torture — a smooth dark gradient with nothing else in it |

`netflix-bar` is **not** a grain source, despite being real camera footage: the pinned
`.webm` is a VP9 distribution copy and the encode removed most of the grain (measured
`grain_sigma` 0.0124 vs `bbb`'s 0.0131 — marginally *lower*). The ungraded `.y4m` retains
grain but is 29.6 GB. For a grain axis use `run-bench.py --synth noise`, or inject seeded
noise into a real clip; both give a clean monotonic grain response.

> **The pinned corpus URL is dead (verified 2026-08-30).** `download.blender.org`
> now 404s for `BigBuckBunny_640x360.m4v`, so `fetch-corpus.sh` cannot materialise
> the clip on a cold machine and the harness is only runnable with a warm
> `.bench-corpus/` cache. It has deliberately **not** been re-pinned to a different
> clip: the `sha256` in `corpus.lock` is what every number in
> [bench-results.md](bench-results.md) was measured against, so switching sources
> would invalidate the entire comparison history rather than repair it. Re-pinning
> resets the baseline and is an explicit decision, not a maintenance fix.

Pelorus's whole premise is a **claim** until measured: that pre-filtering in
VRAM makes a *hardware* encode better. The bench proves (or disproves) it
honestly, and is **pinned + repeatable**.

## What is compared

The only variable is the Pelorus pre-pass. Both arms use the **same hardware
encoder, same preset, same CQ**:

- **baseline** — a *normal* GPU encode: `ffmpeg -i src -c:v hevc_nvenc -cq N`
  (no filter). This is the thing Pelorus must beat.
- **pelorus** — the same encode with the pre-filter in the zero-copy Vulkan
  pipeline: `ffmpeg -i src -init_hw_device vulkan -vf
  "format,hwupload,pelorus_deband_vulkan=…,hwdownload,format" -c:v hevc_nvenc -cq N`.

For each CQ in a **CQ-locked** ladder (never `-b:v` VBR — the tuned-NVENC
multipass config overshoots a VBR target by ~50%, breaking iso-bitrate) we record
(bitrate, **SSIMULACRA2**) → two RD curves → **BD-rate** (`scripts/bench/bd_rate.py`,
Bjøntegaard): negative % = fewer bits for equal quality = a win.

**SSIMULACRA2 is the primary metric** (vmafx `--feature ssimulacra2`): it does not
saturate on clean content and is robust to sharpening-gaming, so it both *credits*
genuine AQ / bit-allocation gains *and* *catches* artifact-adding tricks. Regular
**VMAF** (v1.0.16, `-m path=…vmaf_v1.0.16_3d0h.json`) is a secondary cross-check —
a VMAF-up / SS2-down split is the gaming flag. **VMAF-NEG is retired** (it zeroes
the enhancement credit, blinding the metric to real AQ gains; see bench-results
v0.17). For deband also record **CAMBI** (lower = less banding). A CPU encode
(`x265 -preset slow` / `SVT-AV1 -preset 4`) is the gold-standard reference for the
GPU↔CPU gap — Pelorus' job is to close it **while staying faster than CPU**.

## Pinned corpus

`scripts/bench/corpus.lock` pins each input by URL + **sha256** + segment +
scale + format + frame count. `fetch-corpus.sh` downloads (cached), verifies the
hash, and extracts the pinned segment to raw YUV — byte-reproducible from the
source. Current corpus:

| name | source | why |
|---|---|---|
| `bbb` | Big Buck Bunny (Blender, CC-BY 3.0), pinned segment | real content — shows behavior on clean footage |
| `synth-banding` | deterministic lavfi dark gradient | controlled banding torture — where deband is *designed* to help |

Real clean content (e.g. the pinned BBB segment) often has little banding
(CAMBI ≈ 0), so deband is roughly neutral there — it is *supposed* to be: it
helps **banded** content. The synthetic gradient is the controlled case that
isolates the banding win; both are pinned and repeatable.

## Reproduce

```bash
# build a runnable ffmpeg+pelorus (needs shaderc + a Vulkan GPU); install libpelorus
meson setup build --prefix=/usr && sudo ninja -C build install
cd ffmpeg-patches && ./generate.sh && ./test/build-and-run.sh   # or build manually

# run the pinned bench
FFMPEG=/path/to/ffmpeg+pelorus VMAF=$(command -v vmaf) DEVICE=vk:0 \
  scripts/bench/bench.sh
# -> bench-out/REPORT.md  (BD-rate + CAMBI per clip × encoder)
```

Pinned knobs (in `bench.sh`): encoders `hevc_nvenc`, `av1_nvenc`; preset `p5`;
CQ ladder `28 34 40 46`; deband `range=15:thry=0.012:dither=bluenoise:dynamic=1:protect=1`.
GPU encoders are not bit-exact across drivers, so the *methodology* is pinned;
the report records the ffmpeg/vmaf/GPU versions used.

## Results

Generated by `bench.sh` into `bench-out/REPORT.md`; the latest committed numbers
live in [docs/development/bench-results.md](bench-results.md). Read BD-rate as
"% bitrate Pelorus saves at equal VMAF" and CAMBI as the banding reduction.
