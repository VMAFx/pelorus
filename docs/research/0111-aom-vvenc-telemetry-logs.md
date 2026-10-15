<!--
SPDX-FileCopyrightText: 2026 Lusoris
SPDX-License-Identifier: EUPL-1.2
-->
<!-- markdownlint-disable MD013 MD060 -->
# Research 0111: telemetry extraction for libaom and VVenC adapters

Evidence for issue #111 adapters (libaom and VVenC). Evaluated on 2026-10-15.

Existing reference adapters:

- [`libpelorus/src/telemetry_adapters.c`](../../libpelorus/src/telemetry_adapters.c) (x265 CSV, SVT-AV1 stat file, FFmpeg `QUALITY_STATS`)
- [`docs/api/encoder-telemetry.md`](../api/encoder-telemetry.md)
- `PelorusEncTelemetryInput` in [`libpelorus/include/pelorus/telemetry.h`](../../libpelorus/include/pelorus/telemetry.h)
- `PelorusEncTelemetrySection` in [`libpelorus/include/pelorus/interop.h`](../../libpelorus/include/pelorus/interop.h)

---

## 1. aomenc and libaom per-frame telemetry analysis

### 1.1 Evaluated binaries and source pins

- Installed binary: `/usr/bin/aomenc` (`AOMedia Project AV1 Encoder v3.15.2`).
- Upstream repository: [`https://aomedia.googlesource.com/aom`](https://aomedia.googlesource.com/aom)
- Release tag: `v3.15.2` (peeled commit `af3dc9aadc793c1b00edc003e3fc19a44fdeb574` verified via `git ls-remote --tags https://aomedia.googlesource.com/aom refs/tags/v3.15.2^{}`).

### 1.2 Evaluation of candidate CLI flags and build options

| Candidate | CLI Flag / Option | Source location at tag `v3.15.2` | Per-frame metrics output | Build requirement |
|---|---|---|---|---|
| `--psnr` | `--psnr=1` / `--psnr=2` | [`apps/aomenc.c:1523`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1523), [`apps/aomenc.c:1767`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1767), [`apps/aomenc.c:1820-1833`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1820) | Overall, Y, U, V PSNR and coded frame size | Standard / default build |
| `--test-decode` | `--test-decode=fatal` / `warn` | [`apps/aomenc.c:1801-1812`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1801), [`apps/aomenc.c:1904-1940`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1904) | None (recon comparison only; silent unless mismatch) | Standard / default build |
| First-pass stats | `--fpf=<file>` | [`apps/aomenc.c:1490-1494`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1490), [`aom/aom_ext_ratectrl.h:235-366`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/aom/aom_ext_ratectrl.h#235) | Pre-encode motion/intra error binary dump; NO encoded QP, NO coded bits, NO reconstruction PSNR | Standard / default build |
| Rate-control histogram | `--q-hist=<n>` | [`apps/aomenc.c:1741-1750`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1741), [`apps/aomenc.c:2678-2681`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#2678) | End-of-stream summary histogram only (0..63 scale), NO per-frame line | Standard / default build |
| `AOM_CTRL` quantizer queries | `AOME_GET_LAST_QUANTIZER` / `AOME_GET_LAST_QUANTIZER_64` | [`aom/aomcx.h:264-272`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/aom/aomcx.h#264), [`av1/av1_cx_iface.c:3257`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/av1_cx_iface.c#3257) | C API only: returns `base_q_idx` (0..255) or encoder QP (0..63). Not exposed as CLI per-frame output | Standard C API |
| Internal stats | `CONFIG_INTERNAL_STATS` | [`cmake/aom_config_defaults.cmake:125`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/cmake/aom_config_defaults.cmake#125), [`av1/encoder/encoder.c:4880-5114`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/encoder/encoder.c#4880) | Stream-level summary in `opsnr.stt`. Per-frame `q_used.stt` is dead code disabled under `#if 0` at line 4949 | **Non-default build flag**: `-DCONFIG_INTERNAL_STATS=1` |
| Output frame size | `CONFIG_OUTPUT_FRAME_SIZE` | [`cmake/aom_config_defaults.cmake:181`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/cmake/aom_config_defaults.cmake#181), [`av1/encoder/encoder.c:5295-5300`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/encoder/encoder.c#5295) | Writes `<frame_bits>,<base_qindex>\n` to `frame_sizes.csv` for each coded frame | **Non-default build flag**: `-DCONFIG_OUTPUT_FRAME_SIZE=1` |
| Rate-control log | `CONFIG_RATECTRL_LOG` | [`cmake/aom_config_defaults.cmake:168`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/cmake/aom_config_defaults.cmake#168), [`av1/encoder/tpl_model.h:717-794`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/encoder/tpl_model.h#717), [`av1/encoder/encoder.c:1801-1805`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/av1/encoder/encoder.c#1801) | End-of-stream stdout dump in 3-pass mode (`rc_log_show()`): `coding_idx`, `q` (0..255), `qstep_ratio`, `est_coeff_rate`, `act_rate` | **Non-default build flags**: `-DCONFIG_RATECTRL_LOG=1` + `-DCONFIG_THREE_PASS=1` + `-DCONFIG_BITRATE_ACCURACY=1` |

### 1.3 Synthetic clip test and captured output

A 10-frame synthetic clip was generated:

```console
ffmpeg -y -f lavfi -i testsrc2=size=176x144:rate=10 -frames 10 -pix_fmt yuv420p .workingdir/synth.y4m
```

#### 1.3.1 Running `aomenc --psnr=1`

Command run:

```console
/usr/bin/aomenc --threads=4 --passes=1 --psnr=1 .workingdir/synth.y4m -o .workingdir/test_psnr.ivf 2>&1
```

Captured stderr output (interactive carriage-return updates collapsed):

```text
Pass 1/1 frame   10/0          0B   16457 us 607.64 fps [ETA  unknown] 66.311 70.496 62.764 62.865    2593FPass 1/1 frame   10/1       2593B  254579 us 39.28 fps [ETA  unknown] 59.210 60.482 57.222 57.642    6169FPass 1/1 frame   10/2       8762B 1228203 us 8.14 fps [ETA  unknown] 60.225 62.161 57.729 58.020       5FPass 1/1 frame   10/3       8767B 1228233 us 8.14 fps [ETA  unknown] 59.090 60.476 57.075 57.324    1198FPass 1/1 frame   10/4       9965B 1548914 us 6.46 fps [ETA  unknown] 61.109 63.122 58.477 58.931       5FPass 1/1 frame   10/5       9970B 1548944 us 6.46 fps [ETA  unknown] 59.752 61.249 57.805 57.710    2093FPass 1/1 frame   10/6      12063B 2168406 us 4.61 fps [ETA  unknown] 61.468 63.288 59.142 59.265       5FPass 1/1 frame   10/7      12068B 2168438 us 4.61 fps [ETA  unknown] 59.779 61.211 57.307 58.455     946FPass 1/1 frame   10/8      13014B 2504310 us 3.99 fps [ETA  unknown] 59.968 61.791 57.649 57.754     913FPass 1/1 frame   10/9      13927B 2821790 us 3.54 fps [ETA  unknown] 62.698 65.206 59.916 60.011      23FPass 1/1 frame   10/10     13950B 2831119 us 3.53 fps [ETA  unknown] Pass 1/1 frame   10/10     13950B   11160b/f  111600b/s 2831117 us (3.53 fps)
Stream 0 PSNR (Overall/Avg/Y/U/V) 60.575 60.961 62.948 58.509 58.798  111600 bps    2831 ms
```

Format explanation from [`apps/aomenc.c:1767`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1767) and [`1827-1830`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/apps/aomenc.c#1827):

- Frame packet (`AOM_CODEC_CX_FRAME_PKT`): `fprintf(stderr, " %6luF", pkt->data.frame.sz);`
- PSNR packet (`AOM_CODEC_PSNR_PKT`): `fprintf(stderr, "%.3f ", pkt->data.psnr.psnr[i]);` (0: overall, 1: Y, 2: U, 3: V).
- These tokens are emitted on stderr inline within the interactive carriage-return (`\r`) progress line.
- Missing: QP, picture type, keyframe/reference flags, PTS, header/residual bit breakdown.

#### 1.3.2 Running `aomenc --fpf`

Command run:

```console
/usr/bin/aomenc --threads=4 --passes=2 --fpf=.workingdir/stats.fpf .workingdir/synth.y4m -o .workingdir/test_fpf.ivf 2>&1
```

- Captured file: `.workingdir/stats.fpf` (2,640 bytes).
- File structure: 11 binary records of 240 bytes each (10 frames + 1 summary record).
- Record layout: `aom_rc_frame_stats_t` defined in [`aom/aom_ext_ratectrl.h:235-366`](https://aomedia.googlesource.com/aom/+/af3dc9aadc793c1b00edc003e3fc19a44fdeb574/aom/aom_ext_ratectrl.h#235). Contains 29 `double`s and 1 `int64_t` (`is_flash`). This file provides first-pass rate-control planning data, not reconstructed telemetry.

---

## 2. VVenC per-frame log line format

### 2.1 Evaluated release and source pins

- Repository: [`https://github.com/fraunhoferhhi/vvenc`](https://github.com/fraunhoferhhi/vvenc)
- Release tag: [`v1.14.0`](https://github.com/fraunhoferhhi/vvenc/releases/tag/v1.14.0) (commit `9428ea8636ae7f443ecde89999d16b2dfc421524`).
- Primary source locations:
  - Line formatting: [`source/Lib/EncoderLib/EncGOP.cpp:2956-3023`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/EncoderLib/EncGOP.cpp#L2956-L3023)
  - Log level emission: [`source/Lib/EncoderLib/EncGOP.cpp:3100-3107`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/EncoderLib/EncGOP.cpp#L3100-L3107)
  - NAL type strings: [`source/Lib/CommonLib/Nal.h:210-237`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/CommonLib/Nal.h#L210-L237)
  - Slice type character: [`source/Lib/EncoderLib/EncGOP.cpp:2931-2935`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/EncoderLib/EncGOP.cpp#L2931-L2935)
  - Verbosity enumeration: [`include/vvenc/vvencCfg.h:98-107`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/include/vvenc/vvencCfg.h#L98-L107)
  - CLI verbosity option: [`source/Lib/apputils/VVEncAppCfg.h:97-113, 614`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/apputils/VVEncAppCfg.h#L97-L113)

### 2.2 Default vs verbose levels in `vvencapp` and `vvencFFapp`

`vvencMsgLevel` values:
`0: silent`, `1: error`, `2: warning`, `3: info`, `4: notice`, `5: verbose`, `6: details`.

- **Default verbosity (`-v 3` / `info`):**
  `c->m_verbosity` defaults to `VVENC_INFO` ([`source/Lib/vvenc/vvencCfg.cpp:380`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/vvenc/vvencCfg.cpp#L380)).
  In [`EncGOP.cpp:3100`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/EncoderLib/EncGOP.cpp#L3100):
  `if (!accessUnit.InfoString.empty() && m_pcEncCfg->m_verbosity >= VVENC_NOTICE)`
  Because `VVENC_INFO` (3) is less than `VVENC_NOTICE` (4), **no per-frame log lines are emitted at default verbosity**. `vvencapp` and `vvencFFapp` output only rolling status lines via `cStats.getInfoString()` (`stats: 100.0% frame= 10/10 fps= ...`) and summary statistics.
- **Verbose levels (`-v 4` / `notice`, `-v 5` / `verbose`, `-v 6` / `details`):**
  Per-frame lines are emitted to stdout as each frame completes encoding.

### 2.3 Per-frame format specification

Each line has the exact anatomy:

```text
POC %5d TId: %1d (%10s, %c-SLICE, QP %d%s %10d bits [Y %6.4lf dB    U %6.4lf dB    V %6.4lf dB] [ET %5d ] [L0 ...] [L1 ...]
```

1. **POC (`slice->poc`):** `%5d` — 0-based presentation order count.
2. **TId (`slice->TLayer`):** `%1d` — temporal layer index (0..5).
3. **NAL unit type (`nalUnitTypeToString(slice->nalUnitType)`):** `%10s` — 10-char right-aligned string:
   `IDR_W_RADL`, `IDR_N_LP`, `CRA`, `GDR`, `TRAIL`, `STSA`, `RADL`, `RASL` (padded with leading spaces to width 10).
4. **Slice type (`c`):** `%c-SLICE`:
   - `'I'`, `'P'`, `'B'` for referenced frames (`pic->isReferenced`).
   - `'i'`, `'p'`, `'b'` (`c += 32`) for unreferenced frames ([`EncGOP.cpp:2934`](https://github.com/fraunhoferhhi/vvenc/blob/v1.14.0/source/Lib/EncoderLib/EncGOP.cpp#L2934)).
5. **QP (`slice->sliceQp`):** `QP %d` — native VVC slice QP.
6. **MCTF indicator (`sMctf`):**
   - If MCTF filtered: `, TF <idx>)` (e.g. `, TF 2)`).
   - If not MCTF filtered: `)` followed by 6 spaces.
7. **Coded bits (`uibits`):** `%10d bits` — access unit bit count.
8. **PSNR planes (`dPSNR`):**
   - Format: `[Y <psnr> dB    U <psnr> dB    V <psnr> dB]` formatted as `%6.4lf` (or `inf` if lossless `MAX_DOUBLE`).
9. **Elapsed time (`pic->encTime`):** `[ET %5d ]` — cumulative encode time in seconds.
10. **Reference picture lists:** `[L0 <pocs>] [L1 <pocs>]`.
11. **Optional hash:** `[%s:%s]` if decoded picture hash SEI is enabled.

### 2.4 Three verbatim example lines from real encoding

Captured from `vvencapp --threads 4 -v 4 -i .workingdir/synth.y4m -o .workingdir/test_vvenc_v4.vvc`:

```text
POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      13624 bits [Y 46.2068 dB    U 41.1604 dB    V 39.9543 dB] [ET     0 ] [L0 ] [L1 ]
POC     3 TId: 3 (      RADL, B-SLICE, QP 31)             3776 bits [Y 41.2902 dB    U 36.9165 dB    V 35.4065 dB] [ET     0 ] [L0 7 ] [L1 7 ]
POC     9 TId: 4 (      STSA, B-SLICE, QP 35)             3192 bits [Y 40.3191 dB    U 34.8735 dB    V 33.7429 dB] [ET     0 ] [L0 7 ] [L1 7 ]
```

---

## 3. Field mapping to PelorusEncTelemetryInput and QP scale semantics

### 3.1 Field-by-field crosswalk

| Telemetry field | Presence bit | VVenC log line (`-v 4`) | libaom C API (`libaom_stats`) | aomenc CLI (`--psnr=1`) |
|---|---|---|---|---|
| `display_index` | 0 | `POC %5d` | Caller-derived display order (PTS is timebase units) | Not reported |
| `decode_index` | 1 | Emission order (0, 1, 2, ...) | Packet sequence (0, 1, 2, ...) | Not reported |
| `frame_bytes` | 2 | `bits / 8` (integer division) | `pkt->data.frame.sz` | `<size>F` token |
| `header_bits` | 3 | Not reported | Not reported | Not reported |
| `residual_bits` | 4 | Not reported | Not reported | Not reported |
| `avg_qp` | 5 | `QP %d` (`slice->sliceQp`) | `AOME_GET_LAST_QUANTIZER_64` (0..63) | Not reported |
| `avg_qp_norm` | 6 | `avg_qp` (identity for `slice_qp`) | Mapped via `av1_encoder_ac_qtx` table | Not reported |
| `psnr_y` | 7 | `Y %lf dB` | `pkt->data.psnr.psnr[1]` | 2nd float in progress line |
| `psnr_u` | 8 | `U %lf dB` | `pkt->data.psnr.psnr[2]` | 3rd float in progress line |
| `psnr_v` | 9 | `V %lf dB` | `pkt->data.psnr.psnr[3]` | 4th float in progress line |
| `ssim_y` | 10 | Not reported | Not reported | Not reported |
| `intra_fraction` | 11 | Not reported | Not reported | Not reported |
| `inter_fraction` | 12 | Not reported | Not reported | Not reported |
| `skip_fraction` | 13 | Not reported | Not reported | Not reported |
| `picture_type` | 14 | `I`/`i` -> `PEL_PICTURE_I`<br>`P`/`p` -> `PEL_PICTURE_P`<br>`B`/`b` -> `PEL_PICTURE_B` | Key/Intra -> `PEL_PICTURE_I`<br>All others -> `PEL_PICTURE_P` | Not reported |
| `key_frame` (flag) | 15 | True if NAL is `IDR_*`, `CRA`, `GDR` | `flags & AOM_FRAME_IS_KEY` | Not reported |
| `reference` (flag) | 16 | True if slice char is uppercase (`I`,`P`,`B`) | Not reported (inverse not established for all packets) | Not reported |
| `shown` (flag) | 17 | True for all logged frames | Not reported by the mirrored flags | Not reported |
| `scene_cut` (flag) | 18 | Not reported | Not reported | Not reported |
| `qp_map` | 19 | Not reported | Not reported | Not reported |
| `bits_map` | 20 | Not reported | Not reported | Not reported |
| `mode_map` | 21 | Not reported | Not reported | Not reported |
| `coded_bit_depth` | 22 | Supplied by parser caller (default 10) | Stream bit depth (8/10/12) | Not reported |
| `codec` | - | `PEL_TLM_CODEC_VVC` (3) | `PEL_TLM_CODEC_AV1` (4) | `PEL_TLM_CODEC_AV1` (4) |
| `qp_scale` | - | `PEL_QP_SCALE_SLICE_QP` (1) | `PEL_QP_SCALE_AV1_ENCODER_QP` (4) | Not reported |
| `granularity` | - | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) |
| `metric_source` | - | `PEL_TLM_METRIC_ENCODER` (1) | `PEL_TLM_METRIC_ENCODER` (1) | `PEL_TLM_METRIC_ENCODER` (1) |
| `adapter` | - | `PEL_TLM_ADAPTER_VVENC_LOG` (5) | `PEL_TLM_ADAPTER_LIBAOM_STATS` (4) | `PEL_TLM_ADAPTER_EXTERNAL` (10) |

### 3.2 QP scale semantics

1. **VVenC (VVC / H.266):**
   - Scale: `PEL_QP_SCALE_SLICE_QP` (1).
   - Valid range: `-6 * (coded_bit_depth - 8)` to 63 (for 10-bit VVC: -12 to 63).
   - Normalization: `avg_qp_norm = avg_qp` (identity mapping).
2. **libaom (AV1):**
   - Scale: `PEL_QP_SCALE_AV1_ENCODER_QP` (4).
   - Valid range: `0.0f` to `63.0f`.
   - Normalization: [`libpelorus/src/telemetry.c:16-36`](../../libpelorus/src/telemetry.c) maps the 0..63 encoder QP through the pinned 192-entry luma AC table `av1_encoder_ac_qtx` ([Research 0174 E3](0174-e3-av1-qp-scale.md)):
     `qstep_8bit = ac_q / (8 << (coded_bit_depth - 8))`;
     `avg_qp_norm = 6 * log2(qstep_8bit / 0.625)`.
   - Raw `base_q_idx` (`PEL_QP_SCALE_AV1_QINDEX` 0..255): `pel_qp_normalize()` explicitly returns `PEL_ERR_UNSUPPORTED` for raw qindex.

---

## 4. Fixture plan and negative test cases

### 4.1 Smallest real fixtures

Following the precedent in [`libpelorus/test/fixtures/telemetry/README.md`](../../libpelorus/test/fixtures/telemetry/README.md):

- Common test clip: 10-frame 176x144 8-bit YUV 4:2:0 `testsrc2` clip at 25 fps.
- Generation script:

```console
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=176x144:rate=25 -frames:v 10 -pix_fmt yuv420p -f rawvideo -y .workingdir/testsrc2-176x144-25-10.yuv
```

#### VVenC fixture: `vvenc-1.14.0-qp32.log`

```console
vvencapp --threads 1 -v 4 -q 32 --preset fast -i .workingdir/testsrc2-176x144-25-10.yuv -o /dev/null > libpelorus/test/fixtures/telemetry/vvenc-1.14.0-qp32.log
```

The resulting file contains startup configuration lines, exactly 10 per-frame `POC` lines, and the summary block.

### 4.2 Negative test cases for VVenC log parser

1. **Truncated row:**
   - Cut line before `bits`:
     `POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      13624\n` -> returns `PEL_ERR_TRUNCATED`.
   - Cut line inside PSNR brackets:
     `POC     7 TId: 0 (IDR_W_RADL, I-SLICE, QP 23, TF 2)      13624 bits [Y 46.2068 dB\n` -> returns `PEL_ERR_TRUNCATED`.
2. **Field-shifted / malformed grammar:**
   - Unknown slice type: `POC 7 TId: 0 (IDR_W_RADL, X-SLICE, QP 23) 13624 bits ...` -> returns `PEL_ERR_INVALID`.
   - Inverted tokens: `TId: 0 POC 7 (IDR_W_RADL, I-SLICE, QP 23) ...` -> returns `PEL_ERR_INVALID`.
   - Non-numeric QP: `QP ab` -> returns `PEL_ERR_INVALID`.
   - Bits not divisible by 8: `13625 bits` -> returns `PEL_ERR_INVALID`.
   - Missing closing brackets: `[Y 46.2068 dB U 41.1604 dB V 39.9543 dB` (missing `]`) -> returns `PEL_ERR_TRUNCATED`.
3. **Out-of-range values:**
   - QP above 63: `QP 64` (VVC slice QP maximum is 63) -> returns `PEL_ERR_RANGE`.
   - Negative bits: `-100 bits` -> returns `PEL_ERR_INVALID`.
   - Non-finite PSNR: `[Y nan dB ...]` -> returns `PEL_ERR_INVALID`.
4. **Buffer boundaries:**
   - Input exceeding 1,048,576 bytes -> returns `PEL_ERR_RANGE`.
   - Line exceeding 4,096 bytes -> returns `PEL_ERR_RANGE`.
   - Caller capacity smaller than record count -> returns `PEL_ERR_RANGE` with `*out_count` holding required count.

### 4.3 Architectural recommendation for libaom adapter

Because `aomenc` CLI does not offer structured per-frame telemetry output:

1. **Scraping `aomenc` stderr progress lines is NOT recommended:** the stderr progress line mixes `\r`, lacks QP, lacks picture type, lacks frame flags, and drops display order.
2. **Preferred integration for issue #111:**
   - **Option A (In-process C adapter):** Use `libaom` C API (`aom_codec_encode` + `AOME_GET_LAST_QUANTIZER_64` + `AOM_CODEC_PSNR_PKT`) to build `PelorusEncTelemetryInput` records directly.
   - **Option B (FFmpeg quality stats filter):** Use FFmpeg's `libaom-av1` wrapper combined with `AV_PKT_DATA_QUALITY_STATS` export.
   - **Option C (CLI patch / CMake flag):** If CLI file parsing is required, enable `-DCONFIG_OUTPUT_FRAME_SIZE=1` to produce `frame_sizes.csv` (`<bits>,<base_qindex>`), or propose an upstream/local `--stat-file` patch to `apps/aomenc.c`.

### 4.4 Implemented libaom input contract

`pel_tlm_from_libaom` implements option A without linking libaom. Its plain-C
input contains caller-associated flags, 64-bit bytes, last quantizer, optional
PSNR and explicit display/decode indices. PTS remains blob metadata, not an
unscaled display index. The frozen telemetry picture enum defines `p` as every
AV1 frame that is neither KEY nor INTRAONLY (`interop.h`), so no P/B inference
is needed. The earlier reference/shown proposals in the crosswalk are not
implemented: the installed flag declarations alone do not establish the inverse
DROPPABLE semantics across all packets. Those presence bits stay clear.

Zero bytes is retained as a reported zero without inferring decodability or
DROPPABLE status. Counts above the section's `uint32_t` limit are rejected.
The API page specifies optional-QP, PSNR capping and error atomicity; the
standalone `libaom-record.c` fixture recorder deliberately enforces a lag-zero,
no-altref single-frame association. Its source is not part of Meson, keeping
the core dependency-free.
