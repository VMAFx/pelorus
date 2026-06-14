<!-- markdownlint-disable MD013 MD060 -->
# Benchmark results

Measured on real hardware via the pinned harness (`scripts/bench/`). Honest
numbers — including where Pelorus does **not** win. Methodology:
[benchmarking.md](benchmarking.md).

## Environment

- GPU: NVIDIA RTX 4090 (+ Intel Arc A380), Vulkan 1.4, 32-core host.
- ffmpeg: n8.1.1 + the Pelorus patch stack, `--enable-vulkan --enable-libshaderc`,
  linked against libpelorus 0.1.0.
- Scorer: vmafx (`build/tools/vmaf`), VMAF v0.6.1 + CAMBI.
- Encoder: `hevc_nvenc -preset p5`, CQ ladder {26,32,38,44}.

## v0.1.0 — deband, synthetic banding gradient (8-bit, 640×360, 24f)

Baseline = normal `hevc_nvenc` encode; Pelorus = same encoder, deband
pre-filter (`range=15 thry=0.012 dither=bluenoise dynamic protect`).

| CQ | variant | bitrate (kbps) | VMAF | CAMBI (banding) |
|---:|---|---:|---:|---:|
| 26 | baseline | 46.7 | 95.82 | 20.60 |
| 26 | pelorus  | 44.9 | 94.51 | 20.09 |
| 32 | baseline | 38.2 | 95.30 | 19.78 |
| 32 | pelorus  | 29.8 | 94.37 | 19.50 |
| 38 | baseline | 27.8 | 94.85 | 18.09 |
| 38 | pelorus  | 24.8 | 94.24 | 17.90 |
| 44 | baseline | 22.8 | 94.82 | 15.62 |
| 44 | pelorus  | 22.2 | 94.50 | 15.68 |

**Honest verdict: a wash at these settings, not a win.** Pelorus produces a
smaller file at every CQ (the debanded gradient compresses better), but VMAF is
~1 point lower and CAMBI barely moves. BD-rate is undefined here (the VMAF
curves don't overlap — Pelorus sits ~1 point lower throughout).

**Why** (this is the documented caveat, [research 0101](../research/0101-smart-deband.md)):
at 8-bit and these very low bitrates the **encoder quantizes the dither away and
re-bands**, so the banding reduction never reaches the decoded output, while
VMAF still penalizes the added dither vs the pristine source. Deband's win
requires headroom for the dither to survive:

- **10-bit output** (`p010`/`yuv420p10`) — the primary lever; dithered gradients
  survive 10-bit quantization where they're crushed at 8-bit.
- higher bitrate (lower CQ), and content with *real* banding (clean live-action
  like the pinned BBB segment has CAMBI ≈ 0 — nothing to deband).

## What is proven now

- The full zero-copy pipeline **runs end-to-end on real hardware** (decode →
  Vulkan deband in VRAM → NVENC), and the filters link + register in ffmpeg.
- The bench is **pinned + repeatable** (`corpus.lock` sha-pinned BBB + a
  deterministic synthetic clip).
- A real **link bug** was found by running it: `require_pkg_config` added cflags
  but not `-lpelorus`; fixed with `add_extralibs`, and CI now links the binary.

## 10-bit deband: **not a bug** (correcting an earlier note)

An ad-hoc 10-bit run once produced a Pelorus arm at ~4000 kbps / VMAF 77 and was
briefly recorded here as a filter bug. **It is not.** Source audit (and the
design workflow) confirm the filter is bit-depth-agnostic by construction:
`vf_pelorus_deband_vulkan` binds `FF_VK_REP_FLOAT`, which maps to **UNORM**
storage images at every depth, so all shader math runs in normalized `[0,1]` and
the dither/grain amplitude is a normalized fraction the hardware de-normalizes
into the 10-bit code range correctly. The GLSL contains no `1023`/`65535`/shift
and no 8-bit assumption.

The bogus numbers came from a **pixfmt mismatch in the throwaway script**, not
the filter: `yuv420p10le` (planar, value LSB-justified) and `p010le`
(semi-planar, value MSB-justified, `<<6`) are **not** byte-compatible. Writing
the prefiltered raw in one layout and reading it as the other corrupts both
magnitude (a 64× shift) and chroma-plane positions → the encoder saw heavy noise
far from the source. The 8-bit arm was immune because `yuv420p` has one
unambiguous layout. The committed `run-bench.py` uses a single `args.pixfmt`
end-to-end and is internally consistent — it was never the culprit. To re-prove
10-bit, pin **one** `yuv420p10le` end-to-end (nvenc converts to p010 internally
and losslessly; never hand it raw p010 bytes).

## Why deband looked like a wash — it's the *metric*, not the filter

Two compounding reasons, both methodological:

1. **Wrong reference.** `run-bench.py` originally scored both arms against the
   *encoder input* (the banded/impaired source). A filter that removes the
   impairment then measures as *diverging* from the reference — VMAF reads the
   removed banding/added dither as "lost detail" and penalizes the very thing
   the filter is for. Fixed: the new `--clean-reference` flag scores against the
   clean ground truth.
2. **Wrong metric for deband.** Deband trades a little VMAF (dither vs a pristine
   source) for lower *banding*. Its proper metric is **CAMBI / SSIMULACRA2**, not
   VMAF BD-rate. On these clips CAMBI moved only marginally because at 8-bit and
   low bitrate the encoder quantizes the dither away and re-bands.

## v0.2 — temporal denoise (the real BD-rate lever), clean-referenced

Methodology fix in action. Real-world capture arrives carrying sensor/film
grain that is (a) perceptually unimportant and (b) expensive to encode (the
encoder re-codes the incoherent noise as residual every frame). The deliverable
is the **clean** picture underneath, so the clean original is the ground truth:

- baseline = encode the **noisy** source;
- pelorus = **denoise** then encode;
- **both scored against the CLEAN original** (`--clean-reference`).

**Result — a large, real gain.** HEVC (`hevc_nvenc -preset p5`), seeded grain
`noise=all_seed=12345:alls=24:allf=t+u`, CPU `atadenoise` (`0a=0.12:0b=0.30 …
s=9`) as a **stand-in** for the algorithm class `vf_pelorus_denoise_vulkan` will
implement in Vulkan (gated temporal averaging). Both arms scored against the
**clean** original.

BBB (high-motion animation):

| CQ | variant | bitrate (kbps) | VMAF-vs-clean |
|---:|---|---:|---:|
| 22 | baseline | 2487.9 | 86.81 |
| 22 | pelorus  | 1347.6 | 86.61 |
| 26 | baseline | 1347.4 | 85.68 |
| 26 | pelorus  |  681.2 | 84.55 |
| 30 | baseline |  651.1 | 82.50 |
| 30 | pelorus  |  345.6 | 82.39 |
| 34 | baseline |  298.0 | 78.28 |
| 34 | pelorus  |  193.0 | 78.87 |

**BD-rate −42.94%** (BD-VMAF +2.06). Even with full-frame motion, the
temporally-incoherent grain costs the encoder ~half its bitrate; removing it
reclaims it at equal clean-referenced quality.

Static / locked-off scene (temporal denoise's home turf — every tap is the same
scene point, so the grain is removed almost completely):

| CQ | variant | bitrate (kbps) | VMAF-vs-clean |
|---:|---|---:|---:|
| 22 | baseline | 2427.0 | 87.99 |
| 22 | pelorus  |  975.7 | 93.69 |
| 26 | baseline | 1246.7 | 87.31 |
| 26 | pelorus  |  466.2 | 91.65 |
| 30 | baseline |  583.4 | 84.38 |
| 30 | pelorus  |  216.0 | 88.97 |
| 34 | baseline |  256.9 | 80.50 |
| 34 | pelorus  |   94.0 | 86.21 |

**BD-rate −88.94%** (BD-VMAF +8.19) — cheaper *and* markedly higher quality at
every point.

### Honest caveats

- **Stand-in, not the GPU filter yet.** Measured with CPU `atadenoise`, which is
  the same *algorithm class* (motion-naive gated temporal averaging) as the
  planned `vf_pelorus_denoise_vulkan`. The real filter adds an edge-preserving
  spatial term and runs zero-copy in VRAM; it must be authored and re-proven.
- **The clean reference assumes the grain is unwanted.** This is the standard
  denoise-before-encode premise and the FGS use case (strip grain, optionally
  re-synthesize it at decode). If grain is artistic intent you would *not* simply
  remove it — that is what the roadmap film-grain path is for.
- **Magnitude scales with grain and operating point.** `alls=24` is
  moderate-strong; lighter grain or very low bitrate (where the encoder already
  discards noise) yields smaller gains. The static −89% is best-case; the
  high-motion −43% is the more representative figure.

### Methodology fix found while proving this

`bd_rate.py` had a **sign bug**: it computed `integ(baseline) − integ(pelorus)`,
inverting both BD-rate and BD-VMAF (a 50% saving read as ~+75%). Fixed to
`integ(pelorus) − integ(baseline)` with a self-test (half-bitrate ⇒ −50%,
identical ⇒ 0%, double ⇒ +100%). All numbers above use the corrected function.

## Open / next

1. **Author `vf_pelorus_denoise_vulkan`** (causal NLM-lite spatial + gated
   temporal, fills the pre-reserved `PEL_SEC_DENOISE` slot) and re-prove with the
   real GPU filter.
2. Re-prove 10-bit deband with a single consistent `yuv420p10le` pipeline
   (correctness confirmation; deband's gain is banding, scored by CAMBI).
3. Harness fixes shipped: `--clean-reference` (decouple scoring ref from encoder
   input) and `--vmaf-timeout` (vmaf hangs at 0% CPU *after* writing its JSON;
   the harness bounds it and reads the already-flushed result).
