<!-- markdownlint-disable MD013 MD060 -->
# Quality window section (interop ABI 1.6)

> [!IMPORTANT]
> **Status: Implemented in ABI 1.6 (`PELORUS_ABI_MINOR` 6).**
> The section `PEL_SEC_QUALITY` (bit 10, 96 bytes) in [`interop.h`](../../libpelorus/include/pelorus/interop.h),
> the rational alignment helper, and encoder zone emitters in [`pelorus/quality.h`](../../libpelorus/include/pelorus/quality.h)
> implement the oracle quality-feedback contract for issue [#83](https://github.com/VMAFx/pelorus/issues/83)
> ([ADR-0214](../adr/0214-quality-window-section.md)).

## In one paragraph

Pelorus applies zero-copy GPU pre-encoding filters (deband, denoise, grain synthesis, motion hints), while an external quality oracle (VMAFx) evaluates coded video quality over temporal windows or shots. ABI 1.6 adds `PEL_SEC_QUALITY` (bit 10) to transport time-aligned window scores across stream boundaries, provides an exact-rational PTS alignment helper (`pel_quality_align`) to correlate quality windows with encoder telemetry records without precision loss, and provides emitters (`pel_zones_*`) to translate per-shot quality targets into encoder-supported rate controls (x265 zonefile, x265 `--zones b=`, and chunked segment plans for SVT-AV1 and libaom).

## Section layout

`PelorusQualitySection`, 96 bytes, flat plain-old-data (POD), 8-byte aligned (first member `uint64_t`), no embedded pointers or dynamic allocations. Member offsets and sizes are locked via `_Static_assert`. Append-only after `_pad`.

| Offset | Type | Member | Unit and description |
|---:|---|---|---|
| 0 | `uint64_t` | `window_id` | Producer-unique, monotonic window identifier |
| 8 | `int64_t` | `pts_start` | First PTS of window, inclusive, in timebase units |
| 16 | `int64_t` | `pts_end` | End PTS of window, exclusive (`pts_end > pts_start`) |
| 24 | `uint32_t` | `tb_num` | Timebase numerator (`tb_num > 0`) |
| 28 | `uint32_t` | `tb_den` | Timebase denominator (`tb_den > 0`) |
| 32 | `uint8_t[32]` | `model_hash` | Raw SHA-256 digest of the metric model file (all zeroes if unmodelled, e.g. PSNR) |
| 64 | `uint64_t` | `provenance_id` | Identifier of scoring run/record in oracle ledger, `0` if unknown |
| 72 | `double` | `score` | Quality score scalar (finite, no NaN or Inf) |
| 80 | `uint32_t` | `metric_id` | Metric identifier from `enum pel_quality_metric` |
| 84 | `uint32_t` | `latency_frames` | Coded frames elapsed between window end and score availability |
| 88 | `uint32_t` | `frame_count` | Coded frames scored in this window (`frame_count >= 1`) |
| 92 | `uint32_t` | `_pad` | Reserved zero padding |

### Metric identifiers

`enum pel_quality_metric` defines the metric identifier carried in `metric_id`. Identifiers are append-only; `0` is invalid:

| Identifier | Value | Description |
|:---|:---|:---|
| `PEL_QM_NONE` | 0 | Invalid / unset |
| `PEL_QM_VMAF` | 1 | Netflix VMAF v0.6.1 / default model |
| `PEL_QM_VMAF_NEG` | 2 | VMAF NEG (no enhancement gain) |
| `PEL_QM_PSNR_Y` | 3 | Peak signal-to-noise ratio, luma |
| `PEL_QM_SSIM_Y` | 4 | Structural similarity index, luma |
| `PEL_QM_MS_SSIM` | 5 | Multi-scale structural similarity |
| `PEL_QM_XPSNR` | 6 | Extended perceptual PSNR |
| `PEL_QM_CAMBI` | 7 | Contrast-aware multiscale banding index |

## Timestamp alignment helper

Temporal alignment between quality windows and encoder telemetry records (e.g. from `PEL_SEC_ENC_TELEMETRY`, stats files, or frame packets) is implemented by `pel_quality_align()`:

```c
pel_result pel_quality_align(const PelorusQualitySection *windows, size_t n_windows,
                             const PelQualityEncRecord *records, size_t n_records,
                             uint32_t rec_tb_num, uint32_t rec_tb_den,
                             PelQualityMatch *out, size_t out_cap);
```

### Exact rational matching rule

- **Half-open interval**: A record matches a window iff its presentation timestamp `pts`, rescaled to the window timebase, lies within `[pts_start, pts_end)`.
- **Exact cross-multiplication**: Comparison performs 64-bit integer cross-multiplication:
  $$K_w = \text{tb\_num}_w \cdot \text{rec\_tb\_den}, \quad K_r = \text{rec\_tb\_num} \cdot \text{tb\_den}_w$$
  reduced by $g = \gcd(K_w, K_r)$. Multiplication overflow is checked explicitly and returns `PEL_ERR_RANGE`. No floating-point or 128-bit types are used.
- **Strictly no nearest neighbour**: A window containing zero matching records receives `matched = 0`, `record_count = 0`, and `first_record = UINT32_MAX`. Even if a record sits 1 PTS adjacent to `pts_end` or `pts_start`, it is never matched to that window.
- **Sorted invariant**: `records` must be pre-sorted in ascending PTS order (`records[i].pts >= records[i-1].pts`), else `PEL_ERR_INVALID` is returned.
- **Array caps**: Inputs are bounded by `PEL_QUALITY_ALIGN_MAX` ($2^{20} = 1{,}048{,}576$ elements).

## Zone emitters

Per-shot targets (`PelShotTarget`: `first_frame`, `last_frame` inclusive, target `crf` in `[0, 63]`) are formatted into encoder-supported rate control directives.

All emitters enforce that shots cover the stream starting from frame 0 (`shots[0].first_frame == 0`) and are contiguous and non-overlapping (`shots[i].first_frame == shots[i-1].last_frame + 1`). Any gap, overlap, or first shot not starting at 0 returns `PEL_ERR_INVALID`. Formatters follow `snprintf` buffer sizing conventions: querying with `buf = NULL` and `cap = 0` populates `*need` with bytes required (including trailing NUL) and returns `PEL_OK`. If `cap < *need`, `PEL_ERR_RANGE` is returned with `*need` set. If `buf == NULL` and `cap > 0`, `PEL_ERR_INVALID` is returned with `*need = 0`. On validation failure, `PEL_ERR_INVALID` is returned with `*need = 0`. Decimals are formatted via integer arithmetic (`%u.%02u`, `%llu.%04llu`) to maintain locale independence.

### 1. x265 `--zonefile` (`pel_zones_x265_zonefile`)

Generates lines of `<first_frame> --crf <crf %u.%02u>\n`:

```text
0 --crf 28.00
30 --crf 20.00
```

Each line reconfigures `--crf` at `<first_frame>` (0-based frame index). In x265, `rateControlStart` matches `zones[i].startFrame == curFrame->m_encodeOrder` (0-based) and x265 starts a new IDR keyframe there (measured with x265 4.3: POC resets to 0 at encode order 30 for zonefile line `30 --crf 20`). Because a closed GOP precedes the IDR, zonefile boundaries are 0-based and frame-exact in display order.

> [!NOTE]
> **x265 `--aq-mode 0` Workaround**: In x265 4.3, invoking `--zonefile` with default adaptive quantization (AQ on) causes x265 to report per-frame QP `inf` for frames after the reconfigure (Avg QP:inf). `--no-cutree` does not avoid it; setting `--aq-mode 0` avoids it. The underlying cause is not established, and only the reported QP was measured, not the encoded picture. The honour test (`scripts/quality/x265-zone-honour.sh`) passes `--aq-mode 0` so the per-frame QP it compares stays finite; whether the AQ symptom changes the encoded result was not measured.

### 2. x265 `--zones` / `x265-params zones=` (`pel_zones_x265_param`)

Generates `<start>,<end>,b=<mult>` joined by `/`:

```text
1,30,b=1.0000/31,60,b=2.5198
```

where $\text{mult} = 2^{(\text{base\_crf} - \text{crf}) / 6.0}$ formatted to 4 decimal places via integer arithmetic (`%llu.%04llu`). In adherence to [ADR-0142](../adr/0142-no-source-side-qp-maps.md), `q=` (forced QP) is never emitted.

**x265 `--zones` Boundary Semantics and Ordinal Conversion**:
In upstream x265 master (`source/encoder/ratecontrol.cpp`, `RateControl::getZone()`, read 2026-10-18):

```cpp
if (m_framesDone + 1 >= z->startFrame && m_framesDone < z->endFrame) return z;
```

(Zones are scanned last to first; the later zone wins).
With `m_framesDone` counting frames already encoded from 0, `<start>` and `<end>` passed to `--zones` are 1-based frame ordinals, both inclusive, counted in encode order (`m_framesDone`). This was verified by local measurement with x265 4.3 (`--zones 30,40,q=10 --bframes 0 --frame-threads 1 --aq-mode 0`): frames with 0-based index 29..39 receive the zone.
Accordingly, `pel_zones_x265_param` emits `<first_frame+1>,<last_frame+1>,b=<mult>`, converting 0-based shot indices to 1-based x265 ordinals.

**Encode-Order Caveat**:
With B-frames enabled (x265 default), encode order diverges from display order. Because `--zones` evaluates boundaries in encode order (`m_framesDone`), display frames near a boundary can land on the wrong side by up to one mini-GOP (measured with x265 4.3 default bframes/frame-threads: zone effect shifted to encode order 31..41).

### 3. Segment plan CSV (`pel_zones_segment_plan`)

Generates a CSV plan for encoders that do not support runtime CLI zone parameters:

```csv
first_frame,last_frame,crf
0,29,23.50
30,59,18.00
```

Inspection of `SvtAv1EncApp --help` and `aomenc --help` confirms that neither SVT-AV1 nor libaom provides zone options for CRF modulation in their CLI. Chunked/segment-based encoding plans encode each chunk as an independent process at its designated CRF.

## ADR-0142 constraint and difference

[ADR-0142](../adr/0142-no-source-side-qp-maps.md) prohibited source-side QP maps and source-side macroblock/CU QP steering. Pre-encode filters do not possess knowledge of the downstream encoder's internal lookahead buffers, B-frame pyramids, temporal dependencies, or rate-distortion trellis optimization. Overriding encoder QP decisions at the block level creates blocking artifacts and degrades rate-distortion performance.

The quality window section and zone emitters respect this boundary:

- **No QP maps**: Pelorus emits no per-block or per-frame QP steering directives.
- **Encoder-native control**: Quality oracle scores inform shot-level CRF or bitrate multipliers (`b=`). The encoder retains full autonomy over intra-shot frame-type decisions, temporal reference hierarchies, spatial adaptive quantization, and rate allocation.

## Reference loop integration

The quality window section provides the wire format and emitter infrastructure for the closed-loop autotune control plane ([#166](https://github.com/VMAFx/pelorus/issues/166)). In this loop, VMAFx reads encoded stream telemetry, scores decoded windows against pristine reference frames, and adjusts filter parameters and encoder shot targets dynamically.
