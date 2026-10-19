<!-- markdownlint-disable MD013 MD060 -->
# Real-noise candidates for the bench `noisy` category

**Date:** 2026-10-19

**Decision:** [ADR-0232](../adr/0232-bench-corpus-parked-category.md), issue [#413](https://github.com/VMAFx/pelorus/issues/413).

**Scope:** `vf_pelorus_grain_estimate_vulkan` (median per-frame `sigma` over a 2 s window from `-ss 1`) on the FFmpeg `n9.0.2` build with the Pelorus stack, FFmpeg's default Vulkan device. Every candidate was downloaded and measured locally; no frame, crop or encode of any clip is committed.

Raw logs and the harness (local, not committed): `.workingdir/evidence/413-noisy/` (`measure.sh`, `temporal.sh`, `crop.sh`, `crop-tdn.sh`, `posctl.sh`, `median.py`, the SVT scripts and every `.meta` / `.err` file).

## Verdict

No permissive candidate carries temporal noise that survives into the bench workload. The best drop under a temporal denoiser is −23 % (SVT 2006 `in_to_tree`, 640x360 crop), against −38 % for the noisy control and the 30 % bar set for a real-noise set. The `noisy` category is parked on #413 until a source passes.

## Method

High `sigma` alone does not separate noise from fine texture: both raise the estimator's flat-area residual. Real sensor or film noise is uncorrelated from frame to frame, so a light temporal denoiser removes it while texture stays. Each clip runs four ways: scaled to 640x360 (the bench workload), the same with `atadenoise=0a=0.1:0b=0.2:1a=0.1:1b=0.2:2a=0.1:2b=0.2` before the estimator, a 640x360 crop at native resolution (no scaling, so noise is not averaged away), and the crop with the denoiser. A drop of at least 30 % counts as real noise.

Controls: `bbb` (clean animation) drops from 0.0131 to 0.0128 (−2 %); `bbb` with synthetic temporal noise drops from 0.0183 to 0.0114 (−38 %).

## Results

| set | clip | licence | scale | +denoise | crop | +denoise | best drop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AOM CTC a4 | SnowMountain | NTIA, research use only | 0.0163 | 0.0169 | 0.0163 | 0.0169 | +4 % |
| AOM CTC a4 | RedKayak | NTIA, research use only | 0.0153 | 0.0150 | 0.0153 | 0.0150 | −2 % |
| AOM CTC a4 | SpeedBag | NTIA, research use only | 0.0083 | 0.0078 | 0.0083 | 0.0078 | −6 % |
| AOM CTC a4 | TouchdownPass | NTIA, research use only | 0.0137 | 0.0128 | 0.0137 | 0.0128 | −7 % |
| AOM CTC a3 | ControlledBurn | NTIA, research use only | 0.0107 | 0.0106 | 0.0091 | 0.0080 | −12 % |
| AOM CTC a3 | WestWindEasy | NTIA, research use only | 0.0129 | 0.0121 | 0.0128 | 0.0134 | −6 % |
| AOM CTC a2 | TreesAndGrass | CC BY-SA 4.0 | 0.0150 | 0.0151 | 0.0136 | 0.0146 | +1 % |
| AOM CTC a2 | WalkingInStreet | CC BY-SA 4.0 | 0.0114 | 0.0105 | 0.0103 | 0.0103 | −8 % |
| SVT 2006 (65 mm film scan) | park_joy 720p50 | free distribution, standards work only | 0.0161 | 0.0127 | 0.0174 | 0.0143 | −21 % |
| SVT 2006 | ducks_take_off 720p50 | same | 0.0141 | 0.0138 | 0.0154 | 0.0142 | −8 % |
| SVT 2006 | in_to_tree 720p50 | same | 0.0110 | 0.0089 | 0.0134 | 0.0103 | −23 % |
| SVT 2006 | old_town_cross 720p50 | same | 0.0122 | 0.0105 | 0.0124 | 0.0098 | −21 % |
| SVT 2006 | crowd_run 1080p50 | same | 0.0133 | 0.0115 | 0.0134 | 0.0120 | −14 % |
| SVT 2022 (low light, lossless 12-bit master) | smoke_sauna | CC BY 4.0 | 0.0122 | 0.0133 | 0.0021 | 0.0017 | −19 % |

The licence column quotes the terms read on 2026-10-19: the copyright file next to each AOM CTC download, the SVT 2006 terms page (svt.github.io), and the SVT Open Content Video Test Suite 2022 PDF. `smoke_sauna` was measured on the first 4 GB of its 42.7 GB master; its crop has almost no flat-area residual, so the noise in the scaled run is texture brought into flat areas by scaling. The AOM CTC noise classes (`Noise_Ocean`, `Noise_Soccer`, `Noise_Animation*`) were left out: they are game captures and animation, not camera noise.

Even a passing SVT 2006 clip could not be pinned: its terms limit use to developing and testing standards. Netflix Open Content masters (CC BY 4.0, Sparks low light) were not measured: each is hundreds of gigabytes, and Nocturne's grain is added in post, not captured.

## Reproduce

```bash
bash scripts/bench/fetch-corpus.sh --check-categories   # prints PARKED noisy, exits 0
python3 -I scripts/test-fetch-corpus.py
```

The measurements need the local harness above and a Vulkan device; the clips download from `media.xiph.org/video/aomctc/test_set`, `media.xiph.org/video/derf/y4m` and `svtopencontent.svt.se`.
