<!-- markdownlint-disable MD013 -->
# vf_pelorus_denoise_vulkan

Edge-preserving spatio-temporal denoiser, run as a zero-copy pre-encode pass in
VRAM. Removing temporally-incoherent grain before the encoder sees it is the
biggest BD-rate lever in the pipeline — the encoder would otherwise re-code the
noise as residual every frame. See [ADR-0112](../adr/0112-temporal-denoise.md)
for the design and [ADR-0111](../adr/0111-benchmark-methodology.md) for the
clean-referenced proof.

## Algorithm

`PEL_DENOISER_BILATERAL_TEMPORAL` — a single Vulkan compute pass over a **causal**
window (the current frame + *N* previous frames held in VRAM). Temporal taps are
same-coordinate by default; the optional `mc=1` mode (ADR-0131) instead warps
each tap by an upstream `pelorus_mc` motion vector, gated by per-block
confidence, to follow motion instead of ghosting it:

- **Spatial** (current frame): an NLM-lite joint bilateral whose *range* weight is
  a 3×3-patch SSD. A real edge has high patch SSD across it, so the weight
  collapses and the filter refuses to average across it — text and facial lines
  survive; flat-region noise is averaged away.
- **Temporal** (previous frames): the same-coordinate sample of each frame, gated
  by a per-pixel similarity threshold (a delta above `tcut` breaks the walk so
  motion / scene-cuts cannot ghost) and decayed by `tdecay` per frame.
- **Combine**: `num = (1−blend)·numS + blend·numT`, `den` likewise,
  `out = mix(in, num/den, strength)`.

The standalone reference shader is `libpelorus/shaders/pelorus_denoise.comp`; the
filter's inline GLSL implements the byte-identical algorithm (kept in lockstep).

## Options

All thresholds are normalized in `[0,1]`, independent of bit depth. Per-plane
options follow the `{Y, Cb, Cr}` split.

| Option | Default | Range | Meaning |
|---|---|---|---|
| `sigma` | 0.03 | 0–0.5 | luma spatial range sigma (edge sensitivity) |
| `sigmac` | 0.04 | 0–0.5 | chroma spatial range sigma |
| `sigmat` | 0.05 | 0–0.5 | temporal gate bandwidth |
| `strength` | 0.30 | 0–1 | luma dry/wet mix `out = mix(in, filtered, strength)` |
| `strengthc` | 0.20 | 0–1 | chroma dry/wet mix |
| `blend` | 0.6 | 0–1 | spatial↔temporal blend (0 = spatial-only, 1 = temporal-only) |
| `tdecay` | 0.8 | 0–1 | per-frame temporal trust falloff |
| `tcut` | 0.10 | 0–0.5 | per-pixel scene-cut/fast-motion clamp |
| `patch` | 1 | 0–3 | spatial window radius (0 = temporal-only) |
| `prev` | 3 | 0–4 | temporal depth (previous frames in VRAM) |
| `protect` | on | bool | damp strength on textured regions |
| `planes` | 0xF | bitmask | planes to process |
| `meta` | off | bool | attach the `PEL_SEC_DENOISE` interop section (adds one GPU→host readback) |
| `mc` | off | bool | motion-compensated temporal taps: warp the temporal fetch by an upstream `pelorus_mc` quarter-pel MV field, gated by per-block confidence + `tcut` ([ADR-0131](../adr/0131-mc-denoise-warp.md)). Requires `pelorus_mc_vulkan=meta=1` **before** denoise; with no upstream MV field denoise falls back to same-coordinate taps |
| `tile` | off | bool | cache the current-frame spatial search window in shared memory before the NLM scan ([ADR-0134](../adr/0134-denoise-shared-mem-tile.md)). Output is **bit-identical**; a large throughput win on bandwidth-limited GPUs (~2.9× on an Arc A380), ~neutral on cache-rich GPUs (a 4090's L2 already absorbs the redundant fetches). Default off (flagship-first) — enable on weak / integrated / mobile GPUs |

Defaults are the conservative pre-encode preset — a safe floor the vmafx
`vmaf-tune` autotune ([ADR-0106](../adr/0106-autotune-control-plane.md)) sweeps
`sigma`/`strength` upward from against the encoded-VMAF-at-bitrate oracle.

## Pipeline placement

Denoise runs **before** deband so deband's flat-test sees a clean low-variance
field (not noise mistaken for texture) and re-injects its dither *after*:

```
hwupload → pelorus_analyze → pelorus_denoise → pelorus_deband → (hwdownload) → encoder
```

## Interop (`meta=1`)

Emits the pre-reserved 28-byte `PEL_SEC_DENOISE` section (append-only ABI, no
version bump): per-plane `residual_energy` (mean `|in−out|`), `applied_strength`,
`noise_sigma_estimate` (residual stddev), `psnr_vs_input`, and
`denoiser_id = PEL_DENOISER_BILATERAL_TEMPORAL`. These are telemetry for vmafx and
the autotune loop; they free-ride the denoise dispatch (one small readback).

## Gain envelope (honest)

The gain is concentrated where temporal averaging is valid — static / slow /
locked-off content with grain. Measured against the clean ground truth with a
stand-in temporal denoiser (ADR-0111): **−42.94% BD-rate** on high-motion BBB +
grain, **−88.94%** on a locked-off scene + grain. Fast full-frame motion sees
little benefit (no motion compensation in this version); the clean-reference
framing assumes the grain is unwanted (otherwise re-synthesize it via the
film-grain path). Re-prove with this filter once built.

## Usage

```bash
ffmpeg -init_hw_device vulkan=vk:0 -i in.mkv \
  -vf "hwupload,pelorus_denoise_vulkan=sigma=0.03:sigmat=0.05:strength=0.30:prev=3:tcut=0.10:blend=0.6,hwdownload,format=yuv420p" \
  -c:v hevc_nvenc -preset p5 -cq 28 out.mkv
```
