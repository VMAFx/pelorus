<!-- markdownlint-disable MD013 -->
# ADR-0214: Quality Window Section, Rational PTS Alignment, and Encoder Zone Emitters (Interop ABI 1.6)

- **Status**: Accepted
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: interop, abi, quality, zones, vmafx, ratecontrol
- **Related**: [ADR-0103](0103-interop-sidedata-abi.md), [ADR-0132](0132-source-side-complexity-scalar.md), [ADR-0142](0142-no-source-side-qp-maps.md), [ADR-0174](0174-encoder-telemetry-abi-1-4.md), [ADR-0183](0183-zero-free-carrier.md)

## Context

Issue #83 addresses the quality-feedback loop between Pelorus pre-encode filtering, the external quality oracle (VMAFx), and the downstream encoder.

While Pelorus performs zero-copy GPU pre-filtering (deband, denoise, grain synthesis, motion estimation), modern fixed-function and software encoders (x265, SVT-AV1, libaom, NVENC, QSV) possess sophisticated internal rate-control models (lookahead, tree-based trellis, temporal cutree). Previous explorations demonstrated that injecting per-block QP maps directly from pre-encode analysis disrupts encoder rate control, causes rate-distortion inefficiency, and produces harsh block boundary artifacts. Consequently, [ADR-0132](0132-source-side-complexity-scalar.md) rejected source-side QP maps and [ADR-0142](0142-no-source-side-qp-maps.md) prohibited source-side QP steering.

Instead, quality-guided rate allocation must operate at the shot or temporal window granularity using high-level rate controls (CRF or bitrate multiplier `b=`). An external oracle such as VMAFx evaluates coded windows against reference sources to compute metric scores (e.g., VMAF, PSNR, SSIM, CAMBI). To close the feedback loop:

1. A standardized side-data carrier is required to convey time-aligned quality scores associated with temporal windows back into the pipeline.
2. Window timestamps and encoder telemetry records must be aligned reliably without precision loss or rounding drift across different timebases (e.g., millisecond 1/1000 vs MPEG-TS 1/90000).
3. The resulting per-shot rate targets must be emitted into encoder-native control surfaces that encoders actually support and honour.

## Decision

We introduce interop ABI 1.6 with the new section `PEL_SEC_QUALITY`, an exact-rational alignment helper, and zone emitters targeting x265, SVT-AV1, and libaom.

### 1. Section Definition (Interop ABI 1.6)

We mint section bit `PEL_SEC_QUALITY = 1u << 10` (k) in `libpelorus/include/pelorus/interop.h` and increment `PELORUS_ABI_MINOR` from 5 to 6.

The section is represented by `PelorusQualitySection` (96 bytes, flat POD, 8-byte aligned, explicit byte offsets locked via `_Static_assert`):

| Offset | Type | Member | Purpose |
| :--- | :--- | :--- | :--- |
| 0 | `uint64_t` | `window_id` | Producer-unique monotonic window identifier |
| 8 | `int64_t` | `pts_start` | Window start presentation timestamp (inclusive) |
| 16 | `int64_t` | `pts_end` | Window end presentation timestamp (exclusive; `pts_end > pts_start`) |
| 24 | `uint32_t` | `tb_num` | Timebase numerator (`tb_num > 0`) |
| 28 | `uint32_t` | `tb_den` | Timebase denominator (`tb_den > 0`) |
| 32 | `uint8_t[32]` | `model_hash` | Raw SHA-256 digest of metric model file (all zeroes if unmodelled) |
| 64 | `uint64_t` | `provenance_id` | Oracle scoring run / record identifier (`0` if unknown) |
| 72 | `double` | `score` | Metric score value (must be finite) |
| 80 | `uint32_t` | `metric_id` | Metric identifier from `enum pel_quality_metric` |
| 84 | `uint32_t` | `latency_frames` | Coded frames elapsed between window end and score availability |
| 88 | `uint32_t` | `frame_count` | Number of coded frames evaluated in window (`>= 1`) |
| 92 | `uint32_t` | `_pad` | Reserved zero padding |

Metric identifiers in `enum pel_quality_metric` are append-only:

- `PEL_QM_NONE = 0` (invalid / unset)
- `PEL_QM_VMAF = 1` (Netflix VMAF default model)
- `PEL_QM_VMAF_NEG = 2` (VMAF NEG)
- `PEL_QM_PSNR_Y = 3` (Luma PSNR)
- `PEL_QM_SSIM_Y = 4` (Luma SSIM)
- `PEL_QM_MS_SSIM = 5` (Multi-scale SSIM)
- `PEL_QM_XPSNR = 6` (Extended perceptual PSNR)
- `PEL_QM_CAMBI = 7` (Contrast-aware multiscale banding index)

### 2. Exact-Rational PTS Alignment Helper

Timestamp alignment between quality windows and encoder telemetry records is implemented in `pel_quality_align()`.

- **Interval Rule**: A record at `pts` matches a window iff its timestamp falls within the half-open interval `[pts_start, pts_end)` in the window's timebase.
- **Exact Cross-Multiplication**: Timebase conversion uses 64-bit integer cross-multiplication reduced by GCD ($K_w = \text{tb\_num}_w \cdot \text{tb\_den}_r$, $K_r = \text{tb\_num}_r \cdot \text{tb\_den}_w$, divided by $\gcd(K_w, K_r)$). Floating-point conversions and 128-bit integer types are avoided. Any signed 64-bit integer overflow immediately returns `PEL_ERR_RANGE`.
- **No Nearest Neighbour**: Nearest-neighbour matching is strictly prohibited. If no record falls in `[pts_start, pts_end)`, the window yields `matched = 0`, `record_count = 0`, and `first_record = UINT32_MAX`, even if a record exists immediately adjacent (e.g. 1 PTS past `pts_end`).
- **Preconditions**: Records must be pre-sorted in ascending PTS order (unsorted input returns `PEL_ERR_INVALID`). Maximum array bounds are enforced by `PEL_QUALITY_ALIGN_MAX` ($2^{20}$).

### 3. Zone Emitters to Supported Encoder Controls

Per-shot targets (`PelShotTarget`: `first_frame`, `last_frame` inclusive, target `crf` in `[0, 63]`) are converted into concrete encoder configuration formats.

All emitters enforce that shots cover the stream starting from frame 0 (`shots[0].first_frame == 0`) and are contiguous and non-overlapping (`shots[i].first_frame == shots[i-1].last_frame + 1`). Any gap, overlap, or first shot not starting at 0 returns `PEL_ERR_INVALID`. Format functions follow `snprintf` buffer sizing conventions, returning `PEL_OK` and setting `*need` (including trailing NUL) when queried with `buf = NULL` and `cap = 0`. If `cap < *need`, `PEL_ERR_RANGE` is returned with `*need` set. If `buf == NULL` and `cap > 0`, `PEL_ERR_INVALID` is returned with `*need = 0`. On validation failure, `PEL_ERR_INVALID` is returned with `*need = 0`. Decimal formatting is performed via integer division and modulo (`%u.%02u`, `%llu.%04llu`) to guarantee locale independence.

#### A. x265 `--zonefile` (`pel_zones_x265_zonefile`)

Emits one line per shot:

```text
<first_frame> --crf <crf %u.%02u>
```

Each line reconfigures `--crf` at `<first_frame>` (0-based index). In x265, `rateControlStart` matches `zones[i].startFrame == curFrame->m_encodeOrder` (0-based) and x265 starts a new IDR keyframe there (measured with x265 4.3: POC resets to 0 at encode order 30 for zonefile line `30 --crf 20`). Because a closed GOP precedes the IDR, zonefile boundaries are 0-based and frame-exact in display order.

**x265 `--aq-mode 0` Workaround**: In x265 4.3, invoking `--zonefile` with default adaptive quantization (AQ on) causes x265 to report per-frame QP `inf` for frames after the reconfigure (Avg QP:inf). `--no-cutree` does not avoid it; setting `--aq-mode 0` avoids it. The underlying cause is not established, and only the reported QP was measured, not the encoded picture. The honour test (`scripts/quality/x265-zone-honour.sh`) passes `--aq-mode 0` so the per-frame QP it compares stays finite; whether the AQ symptom changes the encoded result was not measured.

#### B. x265 `--zones` / `x265-params zones=` (`pel_zones_x265_param`)

Emits zone definitions joined by `/`:

```text
<start>,<end>,b=<mult>
```

where `mult = 2^((base_crf - crf) / 6.0)` formatted to 4 decimal places via integer arithmetic (`%llu.%04llu`). In adherence to [ADR-0142](0142-no-source-side-qp-maps.md), `q=` (forced QP) is never emitted.

**x265 `--zones` Boundary Semantics and Ordinal Conversion**:
In upstream x265 master (`source/encoder/ratecontrol.cpp`, `RateControl::getZone()`, read 2026-10-18):

```cpp
if (m_framesDone + 1 >= z->startFrame && m_framesDone < z->endFrame) return z;
```

(Zones are scanned last to first; the later zone wins).
With `m_framesDone` counting frames already encoded from 0, `<start>` and `<end>` passed to `--zones` are 1-based frame ordinals, both inclusive, counted in encode order (`m_framesDone`). This was verified by local measurement with x265 4.3 (`--zones 30,40,q=10 --bframes 0 --frame-threads 1 --aq-mode 0`): frames with 0-based index 29..39 get the zone.
Accordingly, `pel_zones_x265_param` emits `<first_frame+1>,<last_frame+1>,b=<mult>`, converting 0-based shot indices to 1-based x265 ordinals.

**Encode-Order Caveat**:
With B-frames enabled (x265 default), encode order diverges from display order. Because `--zones` evaluates boundaries in encode order (`m_framesDone`), display frames near a boundary can land on the wrong side by up to one mini-GOP (measured with x265 4.3 default bframes/frame-threads: zone effect shifted to encode order 31..41).

#### C. Chunked Segment Plan CSV (`pel_zones_segment_plan`)

For encoders lacking runtime CLI zone controls, a segment plan CSV is generated:

```csv
first_frame,last_frame,crf
0,29,23.50
30,59,18.00
```

**Verification of SVT-AV1 and libaom Support**:
Local inspection of `SvtAv1EncApp --help` and `aomenc --help` confirms that neither encoder CLI provides zone options for CRF modulation:

- `SvtAv1EncApp --help`: returns no occurrences of `zone` or regional CRF configuration.
- `aomenc --help`: returns no occurrences of `zone` or regional CRF configuration.

Accordingly, SVT-AV1 and libaom require chunked/segment-based encoding plans where each segment is encoded as an independent invocation at its designated CRF.

### 4. FFmpeg Runtime Encoder Reconfiguration Deferred

Direct runtime encoder reconfiguration across arbitrary frame boundaries within the FFmpeg CLI is deferred:

- FFmpeg's CLI and `avcodec` APIs do not expose a uniform runtime mechanism to alter encoder CRF or bitrate mid-stream across all codecs without tearing down the codec context or inserting keyframe splits.
- Standalone encoder invocation (`x265 --zonefile`, `x265 --zones`) and segment-based chunking provide immediate, reliable rate steering without requiring intrusive FFmpeg core modifications.

## Alternatives Considered

- **Floating-point timestamps**: Computing PTS alignment using double-precision seconds (`pts * tb_num / tb_den`). Rejected due to accumulated rounding error and drift across mismatched clock domains (e.g., 90 kHz MPEG-TS vs 1 kHz timescale).
- **Nearest-neighbour matching**: Mapping orphaned records to the closest quality window. Rejected because attributing quality fluctuations to unrelated adjacent shots distorts the optimization feedback loop.
- **Fixed QP zones (`q=`) in x265**: Using `--zones start,end,q=N`. Rejected under ADR-0142: overriding adaptive rate control degrades compression efficiency.
- **Source-side QP maps**: Generating macroblock/CU QP matrices. Rejected under ADR-0132 and ADR-0142.

## Consequences

- Public headers install `pelorus/quality.h`.
- Conformance fixture in `interop_test.c` asserts round-trip packing and R4 backward compatibility.
- Test suite validates alignment boundary conditions, refusal of nearest matching, and exact emitter formatting.
- `scripts/quality/x265-zone-honour.sh` verifies that x265 honours `--zonefile` and `--zones b=` rate adjustments with mean QP shifts $\ge 1.0$.
