<!-- markdownlint-disable MD013 MD060 -->
# QE-02: edge-gated detail boost before NVENC does not win at iso-quality

**Date:** 2026-10-19

**Decision:** [ADR-0234](../adr/0234-qe02-gated-hf-rejected.md), follow-up to [ADR-0142](../adr/0142-tune-auto-content-router.md). Inventory row QE-02 in [0172-roadmap-inventory](0172-roadmap-inventory.md).

**Scope:** stock FFmpeg `n9.0.2` (`/usr/bin/ffmpeg`) for the prefilter and the encodes, `hevc_nvenc` on an NVIDIA GeForce RTX 4090 (driver 615.78.08), vmafx `vmaf` v3.1.0-2519-g19c8a4be1 with its built-in default model plus `--feature ssimulacra2 --feature cambi` (the build the [#131 run](0131-noisy-source-reference-run.md) used). Method: `scripts/bench/run-bench.py --four-arm` ([ADR-0223](../adr/0223-noisy-source-bench-method.md)), 48 frames, CQ 22, 26, 30, 34 (standard ladder) and CQ 30, 34, 38, 42 (low-rate ladder).

Raw logs and records (local, not committed): `.workingdir/evidence/qe02/` (`<clip>-<arm>/result.json`, `<clip>-<ladder>.log`). Source frames stay local.

## Verdict

No net win. The edge-gated boost costs bits at matched SSIMULACRA2 against `nvenc-tuned` on five of six valid ladders (+5.4 % to +74 %). The one gain is −2.0 % on the BarScene 1080p low-rate ladder, and the same ladder on TreesAndGrass loses +5.4 %. A global boost (the control) loses far more (+52.2 % to +248.1 %). The gate fails, so nothing is built.

## Question

QE-02 asked whether high-frequency detail added before the encode survives NVENC's deadzone and pays for its bits. The maintainer notes behind the inventory row record that a global `cas=0.8` restores HF energy but raises the bitrate at the same encoder settings. The open question was the net result at iso-quality, measured by the #131 method with at least four rate points. The maintainer chose SSIMULACRA2 as the deciding metric, with VMAF and CAMBI as secondary readings and no VMAF-NEG (decision `pelorus-qe02-metric`, paraphrased).

## Method

1. **Prefilter once on the CPU** with stock FFmpeg, luma only, written to raw YUV:
   - **gated**: `cas=strength=0.8` mixed into the source through `maskedmerge`. The mask is a `sobel` edge map passed through a ramp (0 below 4, full from 12 to 48, fading to 0 at 96) and `gblur=sigma=1.5`, so flat areas and the strongest edges keep the source and textured mid-gradients take the boost.
   - **cas** (control): `cas=strength=0.8` on the whole frame.
2. **Encode and score** with `run-bench.py --four-arm --pelorus-src-file <prefiltered>`. The record's fourth arm (named `pelorus-denoise+nvenc-tuned` in the schema) here encodes the prefiltered file with the `nvenc-tuned` settings. Every arm is scored against the unfiltered source, which is the encoder input (`scoring_reference: encoder-input`; the record's `reference` field is the fixed schema value `noisy-source`).
3. **Read the filter effect from the `nvenc-tuned` column**: that arm uses the same encoder settings as the prefiltered arm, so the difference is the prefilter alone. The `nvenc-tflevel` and `nvenc-uhq` columns add the difference between NVENC settings, which varies a lot by clip.

Clips:

| clip | source | size, fps | licence |
| --- | --- | --- | --- |
| `bbb` | `scripts/bench/corpus.lock` | 640x360, 30 | CC BY 3.0 |
| `netflix-bar` | `scripts/bench/corpus.lock` | 640x360, 60 | CC BY-NC-ND 4.0 ([ADR-0228](../adr/0228-bench-corpus-categories.md)); measured locally, frames not redistributed |
| BarScene 1080p | same pinned webm (sha256 `15b3a0ca…`), `-ss 6`, bicubic to 1920x1080 | 1920x1080, 60 | as above |
| TreesAndGrass | AOM CTC a2_2k (sha256 `0cc6405e…`), `-ss 1` | 1920x1080, 30 | CC BY-SA 4.0 |
| WalkingInStreet | AOM CTC a2_2k (sha256 `4ff5db10…`), `-ss 1` | 1080x1920, 30 | CC BY-SA 4.0 |

The two AOM CTC clips are the downloads of the [#413 survey](0413-noisy-candidates.md).

## Results

SSIMULACRA2 BD-rate of the prefiltered arm; positive means more bits for the same score.

| clip | arm | ladder | vs `nvenc-tuned` | vs `nvenc-tflevel` | vs `nvenc-uhq` | VMAF BD-rate |
| --- | --- | --- | --- | --- | --- | --- |
| `bbb` 360p | gated | CQ 22–34 | +74.0 % | +25.5 % | +104.0 % | +61.5 % |
| `bbb` 360p | cas | CQ 22–34 | +248.1 % | +117.4 % | no overlap | +384.3 % |
| `netflix-bar` 360p | gated | CQ 22–34 | +22.9 % | +26.0 % | +17.0 % | +42.6 % |
| `netflix-bar` 360p | cas | CQ 22–34 | +105.8 % | +104.8 % | +194.9 % | +124.5 % |
| BarScene 1080p | gated | CQ 22–34 | +13.2 % | +10.8 % | +1.8 % | +9.0 % |
| BarScene 1080p | cas | CQ 22–34 | +52.2 % | +46.8 % | +45.9 % | +45.3 % |
| BarScene 1080p | gated | CQ 30–42 | −2.0 % | −2.9 % | −12.9 % | +1.5 % |
| TreesAndGrass | gated | CQ 30–42 | +5.4 % | −11.9 % | −20.7 % | +4.6 % |
| WalkingInStreet | gated | CQ 30–42 | +15.3 % | +12.1 % | +3.3 % | +11.3 % |

Ladders without a BD-rate:

- **TreesAndGrass, CQ 22–34:** NVENC saturates near 15.6 Mbps at CQ 22 and 26 (`nvenc-tuned` 15657 and 15606 kbps), so the curves are not monotonic.
- **WalkingInStreet, CQ 22–34:** the same saturation near 15.0 Mbps; `nvenc-uhq` gives an identical bitrate at CQ 22 and 26, and the record validator refuses the run (`rule:monotonic_bitrate`). At every CQ the gated arm scores below `nvenc-tuned` in SSIMULACRA2. At CQ 22 and 26 both arms sit at the saturation bitrate (within 0.1 %), and at CQ 30 and 34 the gated arm spends more bits (CQ 30: 11225 against 9495 kbps, 34.25 against 36.39).

CAMBI separates the arms only on BarScene, the one clip with banding. There the gated arm stays inside the range of the NVENC arms (1080p CQ 22–34: 1.78 to 2.22 against 1.79 to 2.32), and the global `cas` control lowers it (360p 0.31 to 0.91 against 0.77 to 1.13; 1080p 1.10 to 1.88 against 1.79 to 2.32). The `cas` arm pays for that with +52.2 % or more in SSIMULACRA2 BD-rate against `nvenc-tuned`. On `bbb`, TreesAndGrass and WalkingInStreet every arm reads 0.18 or less.

## Findings

- **The gate helps but does not reach break-even.** Gating cuts the loss of the global boost to a third or less at every size, and the loss shrinks with resolution (BarScene +22.9 % at 360p, +13.2 % at 1080p). At matched CQ the gated arm spends more bits for a lower SSIMULACRA2 score than `nvenc-tuned` on 19 of its 32 points.
- **The low-rate gain does not replicate.** BarScene's −2.0 % sits inside the spread between clips: TreesAndGrass +5.4 % and WalkingInStreet +15.3 % on the same ladder.
- **The wins against `nvenc-tflevel` and `nvenc-uhq` are encoder settings, not the prefilter.** On TreesAndGrass both arms spend far fewer bits than `nvenc-tuned` for a much lower score (CQ 34: `nvenc-uhq` 3830 kbps at SSIMULACRA2 −23.65 against `nvenc-tuned` 7298 kbps at 17.95), so any arm built on `nvenc-tuned` beats them there.
- **SSIMULACRA2 and VMAF agree in sign** on every ladder except BarScene low-rate, where VMAF reads +1.5 % against SSIMULACRA2's −2.0 %.
- **First WalkingInStreet run discarded:** it read the portrait clip as 1920x1080. The record is kept as `INVALID-WalkingInStreet-hi-wrongdims` and is not in the table.

## Consequence

The measurement confirms the additive law of ADR-0142 for a gated boost too: adding HF ahead of NVENC does not pay at iso-quality on this corpus. QE-02 closes without a build. A future attempt would need a different mechanism, not a different mask: for example, encoder-side deadzone or AQ control, which ADR-0142 already rules out as source-side rate control, or a codec with a software path.
