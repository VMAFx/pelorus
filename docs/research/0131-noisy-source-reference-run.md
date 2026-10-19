<!-- markdownlint-disable MD013 MD060 -->
# Noisy-source reference run: Pelorus denoise against three tuned NVENC arms

**Date:** 2026-10-18

**Decision:** [ADR-0223](../adr/0223-noisy-source-bench-method.md), issue [#131](https://github.com/VMAFx/pelorus/issues/131).

**Scope:** FFmpeg `n9.0.2` plus shared series `v0.3.0` plus the Pelorus stack, `hevc_nvenc` on an NVIDIA GeForce RTX 4090 (driver 615.78.08), vmafx `tools/vmaf` at `v3.1.0-2519-g19c8a4be1` (model `vmaf_v1.0.16_3d0h`, features `ssimulacra2` and `cambi`). Four CQ points per arm, 48 frames.

Raw logs and records (local, not committed): `.workingdir/evidence/131/` (`ref-r1.log` to `ref-r5.log`, `ref-r*/result.json`, `ref-r*/record.json`, `ref-r5.src.sha256`).

## Verdict

On the noisy clip, `pelorus_denoise_vulkan` in front of the tuned NVENC baseline saves 12.65 % SSIMULACRA2 BD-rate against that baseline, but costs 7.96 % against NVENC's own temporal filter (`-tf_level 4`) and 4.65 % against `-tune uhq`. Under ADR-0223 the comparison that decides is the one against the best NVENC arm, so this run shows no Pelorus denoise win on this clip. The VMAF cross-check (-20.34 % against the tuned baseline) agrees in sign with the SSIMULACRA2 result against the same arm.

The clip is a local file, not a pinned corpus entry, so the numbers are a method check, not a published claim.

## Method

1. `scripts/bench/run-bench.py --four-arm --cambi` encodes each arm at CQ 22, 26, 30 and 34, decodes, scores against the noisy source, writes `result.json` and `record.json`, and validates the record against `scripts/bench/record.schema.json`.
2. Source: `.bench-corpus/bbb_noisy.yuv`, 640x360, yuv420p, 48 frames, sha256 `6b6eb33e1794361a24c71fec7b2344399481ae921db29ebbd13992e7eb68b61d`. It is not listed in `scripts/bench/corpus.lock`.
3. SSIMULACRA2 BD-rate: `scripts/bench/bd_rate.py`, Pelorus arm against each NVENC arm.

## Results

Run `ref-r5`:

| Arm | CQ 22 kbps / SS2 | CQ 26 | CQ 30 | CQ 34 |
| --- | --- | --- | --- | --- |
| `nvenc-tuned` | 3237.4 / 32.87 | 1787.6 / 25.75 | 792.9 / 19.71 | 327.5 / 15.72 |
| `nvenc-tflevel` | 2180.1 / 29.56 | 1228.5 / 24.18 | 662.3 / 20.44 | 317.0 / 16.78 |
| `nvenc-uhq` | 2261.0 / 28.72 | 1036.5 / 22.73 | 462.2 / 19.20 | 212.5 / 14.52 |
| `pelorus-denoise+nvenc-tuned` | 3198.6 / 34.13 | 1731.5 / 26.38 | 734.7 / 20.51 | 329.9 / 16.60 |

| Pelorus against | SSIMULACRA2 BD-rate |
| --- | --- |
| `nvenc-tuned` | -12.65 % |
| `nvenc-tflevel` | +7.96 % |
| `nvenc-uhq` | +4.65 % |

CAMBI stayed at 0.0000 on every Pelorus and tuned point; the clip has no banding.

## Findings during the run

- `ref-r1`: the `nvenc-tflevel` arm did not open. FFmpeg `n9.0.2` `libavcodec/nvenc.c:1497` requires at least 4 B-frames for temporal filtering. Every NVENC arm now passes `-bf 4`.
- `ref-r1`: CAMBI read NaN because vmafx names the feature with its options (`cambi_hrs_1080_...`). The reader now takes the first `cambi` key.
- `ref-r3` (lavfi `--synth noise`, 1280x720, CQ 22 to 34): the record validated, but every SSIMULACRA2 curve was non-monotonic near the score floor (about 19), so no BD-rate is defined. Synthetic noise at that size is not a usable reference clip.
- `ref-r4` (same source, CQ 16 to 28): the tuned and Pelorus arms gave the same bitrate at CQ 16 and 20. The validator refused the record (`rule:monotonic_bitrate`), and `run-bench.py` now exits 1 when the record fails validation; before this run it printed the errors and exited 0.

## Consequence

The method and its validator ran end to end on hardware. A competitive claim still needs a pinned noisy clip in `scripts/bench/corpus.lock`; until then ADR-0223 stays partial. The QE-02 deadzone run (ADR-0142 follow-up) uses this method.
