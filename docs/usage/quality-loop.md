<!-- markdownlint-disable MD013 -->
# Quality-threshold loop

Closed-loop quality-threshold encoding (CABR-style) modulates encoder rate controls per temporal window or shot to achieve a target visual quality score (e.g. VMAF $\ge 90.0$) at minimum bitrate cost ([ADR-0215](../adr/0215-quality-threshold-loop.md), [#166](https://github.com/VMAFx/pelorus/issues/166)).

Under [ADR-0132](../adr/0132-source-side-complexity-scalar.md) and [ADR-0142](../adr/0142-no-source-side-qp-maps.md), Pelorus prohibits source-side per-block QP maps and runtime macroblock QP steering. Instead, quality steering operates at the window level using high-level rate controls (such as CRF or bitrate multiplier `b=`), letting the encoder retain full control over frame-level lookahead, temporal references, and trellis optimization.

## Workflow overview

The closed-loop encoding pipeline proceeds in three stages:

1. **Per-shot iterative search**: For each shot, the pure stateless controller `pelorus_qloop` guides successive probe encodes using bracketed regula falsi with the Illinois modification until the chosen knob satisfies the quality target and tolerance.
2. **Zone parameter compilation**: The chosen per-shot knobs are converted into encoder-native zone directives using `pelorus_zones` ([ADR-0214](../adr/0214-quality-window-section.md)).
3. **Full-stream single-pass encode**: The entire clip is encoded in one continuous pass using the generated `--zonefile` (for x265) or chunked segment plan (for SVT-AV1 / libaom).

## Recipe

### 1. Initialize per-shot search with `pelorus_qloop`

The CLI tool `pelorus_qloop` reads the history of prior observations (`<knob> <score>`) from standard input and prints the next action:

- `probe <knob>`: Execute a probe encode at this knob value.
- `done <knob>`: Convergence achieved; use this knob for the final encode.
- `unreachable`: Target cannot be achieved even at maximum quality.
- `exhausted <knob>`: Iteration budget reached without full convergence; outputs best available setting.

```bash
# Query initial probe (history is empty)
pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 \
  --start 28.0 --resolution 0.5 --max-iter 8 --direction -1 < /dev/null
# Output: probe 28.0000
```

### 2. Encode probe and measure score

Encode the shot frames with the proposed CRF, decode to Y4M, and score using `vmaf`:

```bash
x265 --input shot0.y4m --crf 28.0 --preset ultrafast --aq-mode 0 \
  --frame-threads 1 -o probe0.265
ffmpeg -v error -i probe0.265 -y probe0_dec.y4m
vmaf -r shot0.y4m -d probe0_dec.y4m --quiet --json -o vmaf0.json
```

Extract the pooled mean VMAF score and append the observation to the shot history file:

```bash
echo "28.0000 94.1200" >> shot0_hist.txt  # illustrative score, not a measured value
```

### 3. Loop until convergence

Pass the updated history back into `pelorus_qloop`:

```bash
pelorus_qloop --target 90.0 --tolerance 1.0 --min 10.0 --max 45.0 \
  --start 28.0 --resolution 0.5 --max-iter 8 --direction -1 < shot0_hist.txt
```

Repeat until the tool prints `done <crf>`.

### 4. Emit zonefile and execute full encode

Combine per-shot converged CRFs into shot definitions (`first_frame,last_frame,crf`) and generate the zonefile:

```bash
cat << 'EOF' > shots.csv
0,29,32.0000
30,59,26.5000
EOF

pelorus_zones --format zonefile < shots.csv > zonefile.txt
x265 --input full_input.y4m --crf 28.0 --aq-mode 0 --preset ultrafast \
  --frame-threads 1 --zonefile zonefile.txt -o full.265
```

## Measured reference results

The reference test suite (`scripts/quality/qloop-x265-reference.sh`, part of `meson test --suite=slow`) executes this complete loop against a 60-frame clip containing two contrasting shots (frames 0..29 from `testsrc`, frames 30..59 from `testsrc2`).

The table below records the actual measured values obtained from the verified run on 2026-10-18:

| Shot | Frame range | Content source | Iterations | Converged CRF | Loop VMAF | Full-encode VMAF | Cross-encode drift |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 0 | 0..29 | `testsrc` | 4 | 32.0000 | 90.3830 | 90.3830 | 0.0000 |
| 1 | 30..59 | `testsrc2` | 3 | 26.5000 | 90.7760 | 91.0889 | +0.3129 |

### Observations and cross-encode drift

1. **Fast convergence**: Both shots converged in $\le 4$ iterations (shot 0 in 4 iterations, shot 1 in 3 iterations), well under the `max-iter 8` limit.
2. **Cross-encode drift**: Encoding the entire clip with `--zonefile` introduces small cross-shot drift due to encoder lookahead and reference buffering across shot transitions. For shot 0, the full-encode mean matched the per-shot loop exactly (90.3830 vs 90.3830). For shot 1, the full-encode mean shifted slightly higher (+0.3129 VMAF, 91.0889 vs 90.7760). Both comfortably met the threshold assertion ($VMAF \ge target - 0.5$, or $\ge 89.5000$).
3. **Planted negative verification**: A planted test targeting VMAF 101.0 correctly terminated with `unreachable` at iteration 2 after probing maximum quality (CRF 10.0, measured VMAF 98.7180).

### Pre-encode filter knobs note

While the reference script tests encoder rate-control steering (CRF), the controller API accepts any monotonic scalar knob. Tuning Pelorus Vulkan filter parameters (such as `deband_threshold` or `denoise_strength` under [ADR-0196](../adr/0196-filter-knob-contracts.md)) requires the patched FFmpeg filter pipeline and is not measured in this standalone test suite.
