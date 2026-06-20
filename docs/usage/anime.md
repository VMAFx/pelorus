<!-- markdownlint-disable MD013 -->
# The anime tune — Pelorus's GPU pre-encode pipeline for animation

Animation is where a fixed-function hardware encoder (NVENC / QSV / VAAPI / AMF)
loses the most to a slow CPU encoder, and it loses for a structural reason: an
anime frame is two extremes at once — large nearly-flat color regions and hard,
high-contrast line-art — and the encoder mishandles **both**. The flats **band**
(its cheap variance AQ starves exactly the smooth gradients that contour); the
lines **ring** (bright/dark "halos" around the line-art) and **alias** (stair-step
"jaggies" along it).

The anime tune fixes each artefact with a dedicated Pelorus GPU filter, all in
one zero-copy Vulkan filtergraph, then steers the encoder's bits onto the flats.
This is a **documented composition** of the existing filters, not a single magic
flag — read the chain, understand each stage, and retune per source
([ADR-0125](../adr/0125-anime-tune.md)).

## The recommended chain

```bash
ffmpeg -init_hw_device vulkan -i in.mkv -vf "
  format=yuv420p10le,hwupload,
  pelorus_analyze_vulkan=roi=1,
  pelorus_dehalo_vulkan=blur=2:darkstr=1:brightstr=1,
  pelorus_aa_vulkan=depth=8:blur=2:darkstr=0.3,
  pelorus_deband_vulkan=range=15:thry=0.02:dither=bluenoise:dynamic=1:protect=1,
  hwdownload,format=p010le
" -c:v hevc_nvenc -pelorus_roi 1 -preset p5 -rc constqp -qp 22 out.mkv
```

Codec-agnostic: swap `hevc_nvenc` for `av1_nvenc`, `hevc_qsv` / `av1_qsv`, or the
native `hevc_vulkan` (the `-pelorus_roi 1` AVOption is registered on all of them;
see [ffmpeg.md](ffmpeg.md)). `hwupload`/`hwdownload` belong only at the edges —
every Pelorus stage runs in VRAM.

## Stage by stage

| # | Stage | Anime artefact it fixes | Key params |
|--:|---|---|---|
| 1 | `pelorus_analyze_vulkan=roi=1` | the encoder **starves the flats** — auto-detects banding-prone flat tiles and emits `AV_FRAME_DATA_REGIONS_OF_INTEREST` so the encoder spends bits there | `roi=1`, `roi_strength`, `flat`, `grad_lo` |
| 2 | `pelorus_dehalo_vulkan` | **ringing / "halos"** around the line-art | `blur` (de-ring radius), `darkstr` / `brightstr` (dark/bright halo strength) |
| 3 | `pelorus_aa_vulkan` | **jaggies / aliasing** on lines (warp AA + line-darkening) | `depth` (warp strength), `blur`, `darkstr` (line darkening) |
| 4 | `pelorus_deband_vulkan` | **banding** on the flats (smart f3kdb + dither) | `range`, `thry`, `dither=bluenoise`, `dynamic=1`, `protect=1` |
| — | `-pelorus_roi 1` (encoder) | the encoder **honors** the stage-1 ROI map (dense per-block delta-QP) | requires constant-QP, AQ off |

Per-filter docs: [analyze](../metrics/analyze.md) · [dehalo](../metrics/dehalo.md)
· [aa](../metrics/aa.md) · [deband](../metrics/deband.md). Encoder ROI steering:
[ffmpeg.md "Encoder ROI steering"](ffmpeg.md#encoder-roi-steering-nvenc--qsv) and
[ADR-0114](../adr/0114-encoder-steering.md).

## Why this order

The ordering is principled, but **content-tunable** — reorder or drop a stage to
suit your source:

1. **`analyze` first.** It is a pass-through producer: it measures the *source*
   before any stage alters it, and the ROI blob then rides every downstream frame
   to the encoder. Measure first, then process.
2. **`dehalo` before `aa`.** Anti-aliasing *warps* the line-art; warp lines that
   have already been de-ringed, or the warp smears the halo along the line. Clean
   first, then warp.
3. **`deband` last.** It injects sub-visible dither into the flats; running the
   aa warp *after* deband would disturb that dither. Deband is the final pixel
   operation before download.

## The 10-bit rule (do not skip)

The chain runs at **10-bit end to end** (`format=yuv420p10le` in, `format=p010le`
out). This is not optional for deband: at 8-bit and low bitrate the encoder
**quantizes the dither away and re-bands**, so the banding reduction never
reaches the decoded output — the documented v0.1 "8-bit wash" lesson
([bench-results.md v0.1](../development/bench-results.md), [research 0101](../research/0101-smart-deband.md)).
10-bit gives the dithered gradient the headroom to survive quantization. Prefer
10-bit output even for 8-bit delivery.

## The encoder-steering rule

`-pelorus_roi 1` makes the encoder honor the stage-1 ROI map, but only under the
right rate control:

- **Use constant-QP** (`-rc constqp -qp N` on NVENC; `-global_quality N` on QSV).
  In VBR/CBR the rate-control redistribution erodes the perceptual win.
- **Turn the encoder's own spatial/temporal AQ off.** The encoder's variance-AQ
  *overrides* the delta-QP map (a one-shot warning fires otherwise).

This is the proven leg: auto-detected ROI gives **−47% banding at iso-bitrate and
iso-VMAF on libx265**, and **−41% banding** through NVENC's `qpDeltaMap`
([bench-results.md v0.5](../development/bench-results.md), [ADR-0114](../adr/0114-encoder-steering.md)).

## Tune it to your source, then prove it

The parameters above are a sensible starting point, not a universal preset. Anime
varies widely (TV line-art vs film vs CG), so:

- **Retune per source.** Raise `thry` for heavier banding; adjust `dehalo`
  `darkstr`/`brightstr` to the halo intensity; lower `aa` `depth` if the warp
  over-softens fine detail.
- **Prove with the right metrics.** Deband/dehalo/aa trade a little
  frame-vs-source VMAF for lower banding/ringing/aliasing, so **VMAF BD-rate is
  the wrong metric** for this chain. Score the encoded output against a *clean*
  ground truth with **SSIMULACRA2 + CAMBI** (banding), per the benchmark
  methodology ([ADR-0111](../adr/0111-benchmark-methodology.md)). The vmafx
  autotune loop ([ADR-0106](../adr/0106-autotune-control-plane.md)) can sweep the
  knobs for you.

> **Honest status.** Each leg is independently build-verified and the
> analyze-ROI / NVENC-ROI / x265-ROI wins are measured. The **composed**
> end-to-end anime BD-rate / SSIMULACRA2 / CAMBI proof against a clean anime
> ground truth is the documented follow-up — **no composed number is claimed
> yet** ([ADR-0125](../adr/0125-anime-tune.md)). `pelorus_dehalo_vulkan` and
> `pelorus_aa_vulkan` ship in PRs #17 and #18; on a tree without those merged,
> drop those two stages and the analyze + deband + ROI legs still apply.

## See also

- [ADR-0125](../adr/0125-anime-tune.md) — why a documented chain over a meta-filter.
- [usage/ffmpeg.md](ffmpeg.md) — the zero-copy pipeline and encoder ROI steering.
- Per-filter metrics: [analyze](../metrics/analyze.md), [dehalo](../metrics/dehalo.md),
  [aa](../metrics/aa.md), [deband](../metrics/deband.md).
