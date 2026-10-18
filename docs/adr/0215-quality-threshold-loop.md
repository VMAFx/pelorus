<!-- markdownlint-disable MD013 -->
# ADR-0215: Quality-Threshold Closed-Loop Controller and Reference Harness

- **Status**: Accepted
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: quality, qloop, controller, closed-loop, vmafx, ratecontrol, adr-0142
- **Related**: [ADR-0132](0132-source-side-complexity-scalar.md), [ADR-0142](0142-no-source-side-qp-maps.md), [ADR-0196](0196-filter-knob-contracts.md), [ADR-0214](0214-quality-window-section.md), [Research 0172](../research/0172-competitive-audit.md)

## Context

Issue #166 addresses closed-loop quality-threshold encoding (CABR-style) on top of the `PEL_SEC_QUALITY` side-data carrier and encoder zone emitters established in [ADR-0214](0214-quality-window-section.md) (#83).

In competitive research ([Research 0172](../research/0172-competitive-audit.md)), item C5 identified quality-threshold encoding as an industry standard capability offered by commercial solutions (Beamr CABR, Harmonic, Netflix Dynamic Optimizer), while BEAT 7 targeted reducing chunk probe costs relative to tools like Av1an, whose Target Quality mode uses a rigid default of four full probe encodes per chunk.

Furthermore, [ADR-0132](0132-source-side-complexity-scalar.md) and [ADR-0142](0142-no-source-side-qp-maps.md) strictly forbid source-side per-block QP maps and runtime QP steering, because overriding macroblock/CTU QP decisions disrupts encoder lookahead, tree-based rate distortion optimization (trellis/cutree), and buffer verification. Quality steering must operate at the shot or temporal window boundary by tuning encoder rate-control targets (such as CRF or bitrate multiplier `b=`), or by modulating pre-encode filter strengths according to declared filter knob contracts ([ADR-0196](0196-filter-knob-contracts.md)).

To enable closed-loop threshold encoding across distributed pipelines, an embeddable, deterministic controller is required.

## Decision

We introduce the quality loop controller API (`libpelorus/include/pelorus/qloop.h` and `libpelorus/src/qloop.c`), the demonstrator CLI `pelorus_qloop` (`tools/pelorus_qloop.c`), and a reference closed-loop verification suite (`scripts/quality/qloop-x265-reference.sh`).

### 1. Pure, Stateless Controller API

The controller is implemented as a pure, non-allocating, deterministic state machine exposed through:

```c
pel_result pel_qloop_validate(const PelQloopConfig *c);
pel_result pel_qloop_next(const PelQloopConfig *c,
                          const PelQloopObs *hist,
                          size_t n_hist,
                          PelQloopStep *out);
```

Configuration `PelQloopConfig` defines the target metric score, tolerance band, knob range `[knob_min, knob_max]`, starting knob `knob_start`, quantization resolution `resolution`, iteration budget `max_iter` ($\le 16$), and direction:

- `direction = -1`: metric score decreases as knob increases (e.g. CRF, where higher CRF implies lower visual quality).
- `direction = +1`: metric score increases as knob increases (e.g. bitrate multiplier or filter strength).

#### Stateless Replay Invariant

The controller holds no internal mutable state or memory heap allocations. At each step, the caller passes the full observation history `hist` as an array of `PelQloopObs { double knob; double score; }`. This design guarantees:

1. Crash-resilience: If an encoder worker crashes during a probe, the controller state is fully recovered from the persisted history log.
2. Auditable determinism: Any controller sequence can be replayed and verified offline from plaintext logs.
3. Thread safety: The controller is reentrant and thread-safe without locks.

### 2. Search Strategy: Bracketed Regula Falsi with Illinois Modification

The controller progresses through three phases:

1. **Initial Probing**: When `n_hist == 0`, the controller outputs `PEL_QLOOP_PROBE` at `knob_start` (iteration 0). If `knob_start` already achieves a score within `[target, target + tolerance]`, the controller immediately yields `PEL_QLOOP_DONE` at iteration 0.
2. **Bracketing**: Before a valid bracket $(A, B)$ with $f(A) \ge 0$ and $f(B) < 0$ is established (where $f(k) = score(k) - target$), the controller steps toward the target using a secant extrapolation from the two most recent observations, or steps by $(knob\_max - knob\_min)/4$ from a single observation, clamped to $[knob\_min, knob\_max]$.
3. **Refinement**: Once a bracket exists:
   - The root is approximated via regula falsi with the Illinois damping modification on stagnant bracket ends ($f_{opp} \leftarrow f_{opp} / 2$).
   - The next probe is clamped strictly inside $(knob_L, knob_R)$ and at least $resolution / 2$ away from either endpoint, falling back to the bracket midpoint if necessary.
   - Every candidate knob is rounded to an integer multiple of `resolution` relative to `knob_min`.

### 3. Termination and Boundary Conditions

- `PEL_QLOOP_DONE`: Returned when an observation falls within $[target, target + tolerance]$, or when the closest passing knob ($score \ge target$) and closest failing knob ($score < target$) are within `resolution` (in which case the passing knob is selected).
- `PEL_QLOOP_UNREACHABLE`: Returned when the highest-quality boundary knob has been evaluated and fails to achieve the target ($score < target$). Note: if the lowest-quality boundary knob scores above target, the controller terminates with `PEL_QLOOP_DONE` at that boundary (the cheapest permissible setting).
- `PEL_QLOOP_EXHAUSTED`: Returned when $n\_hist \ge max\_iter$ without reaching `DONE`. It outputs the cheapest passing knob observed if any passed, or the knob yielding the highest observed score.
- Monotonicity enforcement: If observation history exhibits non-monotonicity (where a higher-quality knob yields a score lower by more than `tolerance` than a lower-quality knob), `pel_qloop_next` fails closed with `PEL_ERR_INVALID`. Duplicate knobs or NaN/inf values similarly fail with `PEL_ERR_INVALID`.

### 4. Reference Verification and Measured Drift

The reference loop script `scripts/quality/qloop-x265-reference.sh` drives a 2-shot 60-frame clip (320x180 YUV420p, 25 fps; shot 0 frames 0..29 from `testsrc`, shot 1 frames 30..59 from `testsrc2`) using `pelorus_qloop`, x265 4.3, and the official `vmaf` 3.2.0 CLI:

- Target VMAF: 90.0
- Tolerance: 1.0
- CRF range: 10.0 to 45.0 (resolution 0.5, start 28.0)
- Maximum iterations: 8

After per-shot loops determine individual shot CRFs, an x265 zonefile is emitted via `pelorus_zones --format zonefile` and the full clip is encoded in a single pass.

#### Measured Reference Results (Recorded 2026-10-18)

| Shot | Content | Iterations | Converged CRF | Loop VMAF | Full-Encode VMAF | Cross-Encode Drift |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 0 | `testsrc` (frames 0..29) | 4 | 32.0000 | 90.3830 | 90.3830 | 0.0000 |
| 1 | `testsrc2` (frames 30..59) | 3 | 26.5000 | 90.7760 | 91.0889 | +0.3129 |

- Planted negative test: Target VMAF 101.0 correctly terminated with `unreachable` at probe iteration 2 after observing CRF 10.0 with score 98.7180.
- Cross-encode drift: The full-encode VMAF scores remained within $+0.3129$ of the per-shot loop scores and comfortably satisfied the $VMAF \ge target - 0.5$ (89.5000) assertion.

#### Scope Note on Pre-Encode Filter Knobs

The controller design supports any scalar monotonic knob, including Pelorus Vulkan filter parameters (such as `deband_threshold` or `denoise_strength` under [ADR-0196](0196-filter-knob-contracts.md)). Because filter tuning requires the full patched FFmpeg filter pipeline, filter-knob runs are not measured in this reference script; the reference suite isolates and validates the encoder rate-control path using stock x265 and libvmaf.

## Alternatives Considered

1. **Av1an Fixed 4-Probe Scheme**: Av1an's Target Quality mode encodes four fixed probes per chunk. This is inflexible and wasteful: smooth shots converge in 2-3 iterations (as measured above), while difficult non-linear boundaries require adaptive bracketing rather than evenly spaced points.
2. **Pure Bisection**: Bisection ignores metric distance and takes $\lceil \log_2((max-min)/resolution) \rceil$ iterations regardless of proximity to the target. Regula falsi with secant extrapolation converges significantly faster when the quality-knob curve is smooth.
3. **Stateful Controller Object / Daemon**: Storing step state in an allocated struct or background daemon creates vulnerability to worker crashes and requires state serialization. The pure stateless functional API allows log replay and zero-copy operation.
4. **Source-Side QP Steering**: Rejected under [ADR-0132](0132-source-side-complexity-scalar.md) and [ADR-0142](0142-no-source-side-qp-maps.md).
