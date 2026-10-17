<!--
SPDX-FileCopyrightText: 2026 Lusoris
SPDX-License-Identifier: EUPL-1.2
-->
<!-- markdownlint-disable MD013 MD060 -->
# Research 0111: telemetry extraction for x264 and libvpx-vp9 adapters

Evidence for issue #111 adapters (x264 / libx264 and libvpx / VP9). Evaluated on 2026-10-16.

Existing reference adapters:

- [`libpelorus/src/telemetry_adapters.c`](../../libpelorus/src/telemetry_adapters.c) (x265 CSV, SVT-AV1 stat file, FFmpeg `QUALITY_STATS`, VVenC log)
- [`docs/api/encoder-telemetry.md`](../api/encoder-telemetry.md)
- [`docs/research/0111-aom-vvenc-telemetry-logs.md`](0111-aom-vvenc-telemetry-logs.md) (sister digest for libaom and VVenC)
- [`docs/research/0111-ffmpeg-quality-stats-writers.md`](0111-ffmpeg-quality-stats-writers.md) (mapping FFmpeg side data writers)
- `PelorusEncTelemetryInput` in [`libpelorus/include/pelorus/telemetry.h`](../../libpelorus/include/pelorus/telemetry.h)
- `PelorusEncTelemetrySection` in [`libpelorus/include/pelorus/interop.h`](../../libpelorus/include/pelorus/interop.h)

---

## 1. x264 and libx264 per-frame telemetry analysis

### 1.1 Evaluated binaries and source pins

- Installed CLI binary: `/usr/bin/x264` (`x264 0.165.3222 b35605a`, built on 2026-09-27 with GCC 16.2.1).
- Installed library header: `/usr/include/x264.h` (`X264_BUILD 165`).
- Installed FFmpeg: `/usr/bin/ffmpeg` (`n9.0.2`, GCC 16, `--enable-libx264 --enable-libvpx`).
- Upstream repository: [`https://code.videolan.org/videolan/x264.git`](https://code.videolan.org/videolan/x264.git)
- Pinned commit: `b35605ace3ddf7c1a5d67a2eb553f034aef41d55` (`refs/heads/stable`, commit prefix `b35605a`, revision 3222).

### 1.2 Evaluation of candidate CLI flags and output mechanisms

| Candidate | CLI Flag / Configuration | Source location at pinned commit | Output mechanism | Suitability for telemetry adapter |
|---|---|---|---|---|
| 2-pass stats file | `--pass 1 --stats <file>` / `rc.b_stat_write` | [`encoder/ratecontrol.c:1857-1901`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/ratecontrol.c#L1857-1901) | Text lines in `<file>` (`in: out: type: q: aq: tex: mv: misc: imb: pmb: smb: d: ref:;`) | **High**: structured text, stable syntax, reports display/decode index, slice type, QP before/after AQ, exact bit split, MB partition shares |
| Macroblock tree file | `--pass 1 --stats <file>` (auto `<file>.mbtree`) | [`encoder/ratecontrol.c:1904-1912`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/ratecontrol.c#L1904-1912), [`common/mc.c:600-605`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/common/mc.c#L600-605) | Binary sidecar file (`uint8_t i_type` + `uint16_t` big-endian Q8.8 QP offsets per MB) | **Medium**: provides MB-level QP delta offsets, but written only for reference frames (`b_kept_as_ref`), omits unreferenced `b` frames |
| Verbose debug log | `-v` / `--verbose` / `--log-level debug` | [`x264.c:1502`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/x264.c#L1502), [`encoder/encoder.c:4130-4142`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/encoder.c#L4130-4142) | Emitted to `stderr` (`x264 [debug]: frame=%d QP=%.2f NAL=%d Slice:%c Poc:%d I:%d P:%d SKIP:%d size=%d bytes`) | **Medium**: single pass supported; disables interactive progress bar; includes PSNR/SSIM when enabled, but requires parsing stderr |
| PSNR computation | `--psnr` | [`encoder/encoder.c:4096-4120`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/encoder.c#L4096-4120) | Appends `PSNR Y:%5.2f U:%5.2f V:%5.2f` to the `-v` stderr debug line | **High** for distortion metrics; absent from `.stats` file |
| SSIM computation | `--ssim` | [`encoder/encoder.c:4122-4128`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/encoder.c#L4122-4128) | Appends `SSIM Y:%.5f` to the `-v` stderr debug line | **High** for distortion metrics; absent from `.stats` file |
| Reconstructed YUV dump | `--dump-yuv <file>` | [`common/base.c:1240`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/common/base.c#L1240), [`encoder/encoder.c:4175-4176`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/encoder.c#L4175-4176) | Raw planar uncompressed YUV 4:2:0 bytes written to disk | **None**: outputs raw pixel samples (`width * height * 1.5 * frames`), zero metadata or telemetry |
| FFmpeg `libx264` 2-pass stats | `-pass 1 -passlogfile <pfx>` or `-x264opts stats=<file>` | [`libavcodec/libx264.c:1119-1122, 1200, 1357-1368`](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/libavcodec/libx264.c#L1119) | Writes standard x264 `.stats` text file (and `.mbtree`) via libx264 rate-control | **High**: identical syntax to standalone x264 CLI stats file |
| FFmpeg `libx264` quality stats | Internal `AV_PKT_DATA_QUALITY_STATS` | [`libavcodec/libx264.c:731-732`](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/libavcodec/libx264.c#L731-732) | Little-endian packet side data: `quality = (pic_out.i_qpplus1 - 1) * 118`, `pict_type`, 3 SSE planes when `-flags +psnr` | **High**: direct in-memory packet side data parsed by `pel_tlm_parse_ffmpeg_quality_stats` |
| FFmpeg `-vstats_file` | `-vstats_file <file>` | [`fftools/ffmpeg_mux.c`](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/fftools/ffmpeg_mux.c) | Text lines (`frame= %d q= %.1f f_size= %d ... type= %c`) | **Low**: FFmpeg CLI multiplexer log, contains single average QP and coded frame size; lacks bit breakdown and plane PSNR |

### 1.3 Synthetic clip test and captured output

Commands were executed on a 10-frame synthetic clip generated via FFmpeg:

```console
ffmpeg -y -f lavfi -i testsrc2=size=320x240:rate=25 -frames:v 10 -pix_fmt yuv420p /tmp/pelorus-test-111/synth_320x240_10.y4m
```

#### 1.3.1 x264 CLI `--pass 1 --stats`

Command run:

```console
/usr/bin/x264 --bitrate 500 --pass 1 --stats /tmp/pelorus-test-111/x264_pass1.stats -o /tmp/pelorus-test-111/x264_pass1.264 /tmp/pelorus-test-111/synth_320x240_10.y4m
```

The resulting file `/tmp/pelorus-test-111/x264_pass1.stats` (1,691 bytes) contains one `#options:` configuration header followed by 10 per-frame lines. Three verbatim output lines:

```text
in:0 out:0 type:I dur:2 cpbdur:2 q:29.01 aq:15.76 tex:28138 mv:3007 misc:5975 imb:300 pmb:0 smb:0 d:- ref:;
in:4 out:1 type:P dur:2 cpbdur:2 q:28.99 aq:24.17 tex:17052 mv:835 misc:249 imb:34 pmb:74 smb:192 d:- ref:0 ;
in:2 out:2 type:B dur:2 cpbdur:2 q:28.99 aq:30.33 tex:11263 mv:942 misc:251 imb:7 pmb:90 smb:203 d:- ref:0 ;
```

Field breakdown from [`encoder/ratecontrol.c:1857-1901`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/ratecontrol.c#L1857-1901):

- `in:%d`: display order / presentation frame count (`h->fenc->i_frame`).
- `out:%d`: decode / coding order (`h->i_frame`).
- `type:%c`: slice type:
  - `'I'`: IDR keyframe (`h->sh.i_type == SLICE_TYPE_I && h->fenc->i_poc == 0`).
  - `'i'`: non-IDR intra frame (`h->sh.i_type == SLICE_TYPE_I && h->fenc->i_poc != 0`).
  - `'P'`: predicted frame (`h->sh.i_type == SLICE_TYPE_P`).
  - `'B'`: referenced bi-predictive frame (`h->fenc->b_kept_as_ref == 1`).
  - `'b'`: non-referenced bi-predictive frame (`h->fenc->b_kept_as_ref == 0`).
- `dur:%"PRId64"`: frame duration in timebase ticks (`h->fenc->i_duration`).
- `cpbdur:%"PRId64"`: CPB removal duration (`h->fenc->i_cpb_duration`).
- `q:%.2f`: rate-control frame QP before adaptive quantization (`rc->qpa_rc`).
- `aq:%.2f`: average frame QP after adaptive quantization (`h->fdec->f_qp_avg_aq`).
- `tex:%d`: texture / transform residual bits (`h->stat.frame.i_tex_bits`).
- `mv:%d`: motion vector bits (`h->stat.frame.i_mv_bits`).
- `misc:%d`: syntax and header bits (`h->stat.frame.i_misc_bits`).
  - Total frame bits: `tex + mv + misc`.
- `imb:%d`: intra macroblock count (`h->stat.frame.i_mb_count_i`).
- `pmb:%d`: inter macroblock count (`h->stat.frame.i_mb_count_p`).
- `smb:%d`: skipped macroblock count (`h->stat.frame.i_mb_count_skip`).
  - Total macroblocks: `imb + pmb + smb` (`h->mb.i_mb_count`).
- `d:%c`: direct mode selection (`'s'` for spatial, `'t'` for temporal, `'-'` for none/disabled).
- `ref:`: space-delimited list of reference usage counts per reference frame index.
- Optional `w:` after the reference counts: 3 signed weight values (luma) or 8 (luma plus chroma), comma-separated. Verified 2026-10-16 against [`x264_ratecontrol_end` at b35605a](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/ratecontrol.c#L1884-1897); these are syntax-checked, not telemetry fields.
- `;`: statement terminator at the end of each frame line.

#### 1.3.2 x264 CLI `-v --psnr --ssim`

Command run:

```console
/usr/bin/x264 -v --psnr --ssim -o /tmp/pelorus-test-111/x264_verbose.264 /tmp/pelorus-test-111/synth_320x240_10.y4m 2>&1
```

Three verbatim lines captured from stderr:

```text
x264 [debug]: frame=   0 QP=21.74 NAL=3 Slice:I Poc:0   I:300  P:0    SKIP:0    size=5049 bytes PSNR Y:45.33 U:43.93 V:44.36 SSIM Y:0.99463
x264 [debug]: frame=   1 QP=27.32 NAL=2 Slice:P Poc:8   I:38   P:89   SKIP:173  size=2277 bytes PSNR Y:43.85 U:42.45 V:42.63 SSIM Y:0.99333
x264 [debug]: frame=   2 QP=30.11 NAL=2 Slice:B Poc:4   I:2    P:110  SKIP:188  size=1365 bytes PSNR Y:42.88 U:41.82 V:42.19 SSIM Y:0.99236
```

Format explanation from [`encoder/encoder.c:4130-4142`](https://code.videolan.org/videolan/x264/-/blob/b35605ace3ddf7c1a5d67a2eb553f034aef41d55/encoder/encoder.c#L4130-4142):

- `frame=%4d`: coding order index (`h->i_frame`).
- `QP=%.2f`: post-AQ average QP (`h->fdec->f_qp_avg_aq`).
- `NAL=%d`: NAL reference IDC (`h->i_nal_ref_idc`: 0 = unreferenced disposable frame; 1..3 = reference frame; 3 = IDR).
- `Slice:%c`: slice type (`'I'`, `'P'`, `'B'`).
- `Poc:%-3d`: picture order count (`h->fdec->i_poc`). In progressive frame encoding, `display_index = Poc / 2`.
- `I:%-4d`: intra MB count.
- `P:%-4d`: inter MB count.
- `SKIP:%-4d`: skip MB count.
- `size=%d bytes`: coded frame size in bytes (`frame_size`).
- `PSNR Y:%.2f U:%.2f V:%.2f`: peak signal-to-noise ratio in dB for Y, U, and V planes (from `--psnr`).
- `SSIM Y:%.5f`: luma structural similarity index (from `--ssim`).

#### 1.3.3 x264 `.mbtree` binary file

Command run: generated automatically alongside `--pass 1 --stats` when MB-tree is enabled (default `mbtree=1`).

- Captured file: `/tmp/pelorus-test-111/x264_pass1.stats.mbtree` (3,606 bytes).
- File structure:
  - Resolution: 320x240 -> $20 \times 15 = 300$ macroblocks per frame.
  - Per reference frame: 1 byte `uint8_t i_type` (`h->sh.i_type`) + 300 entries of `uint16_t` big-endian Q8.8 fixed-point values ($1 + 300 \times 2 = 601$ bytes per frame).
  - 10-frame clip produced 6 reference frames ($6 \times 601 = 3,606$ bytes). Non-reference frames (`'b'`, frames 1, 3, 5, 7) are omitted from the `.mbtree` stream.
  - Value encoding: `mbtree_fix8_pack` converts `float f_qp_offset` via `(int16_t)(src[i] * 256.0f)` stored in big-endian byte order (`common/mc.c:604`).
  - Semantic: relative QP offset per macroblock calculated by the MB-tree lookahead propagation model; not an absolute slice QP.

#### 1.3.4 FFmpeg `libx264` `-x264opts stats=...`

Command run:

```console
ffmpeg -y -i /tmp/pelorus-test-111/synth_320x240_10.y4m -c:v libx264 -b:v 500k -pass 1 -x264opts stats=/tmp/pelorus-test-111/custom_x264.log -f mp4 /tmp/pelorus-test-111/ff_custom.mp4
```

- Captured file: `/tmp/pelorus-test-111/custom_x264.log` (1,691 bytes).
- Content: identical line syntax and values to standalone `x264 --stats`.
- If `-passlogfile <prefix>` is used instead of `-x264opts stats=...`, FFmpeg appends `-0.log` (e.g. `/tmp/pelorus-test-111/ff_x264-0.log`).

---

## 2. vpxenc and libvpx-vp9 per-frame telemetry analysis

### 2.1 Evaluated binaries and source pins

- Installed CLI binary: `/usr/bin/vpxenc` (`WebM Project VP9 Encoder v1.17.0`).
- Installed library headers: `/usr/include/vpx/` (`vpx_encoder.h`, `vpx_codec.h`, `vp8cx.h`).
- Upstream repository: [`https://chromium.googlesource.com/webm/libvpx`](https://chromium.googlesource.com/webm/libvpx)
- Release tag: `v1.17.0` (tag object `82615ff0733cd4063838c352bc2e2ab225f37ade`, peeled commit `6df3ec34557879fff673706f4a1d9fbd0f3a6f0e`).

### 2.2 Evaluation of candidate CLI flags and output mechanisms

| Candidate | CLI Flag / Configuration | Source location at tag `v1.17.0` | Output mechanism | Suitability for telemetry adapter |
|---|---|---|---|---|
| First-pass stats | `--fpf=<file>` | [`vpxenc.c:1543-1548`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vpxenc.c#1543), [`vp9/encoder/vp9_firstpass_stats.h:24-48`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vp9/encoder/vp9_firstpass_stats.h#24) | Binary dump of `FIRSTPASS_STATS` records (216 bytes each: 26 doubles + 1 int64_t; $N+1$ records) | **Unsuitable**: pre-encode analysis data only; contains NO coded frame bits, NO reconstructed QP/qindex, NO reconstructed PSNR |
| PSNR status line | `--psnr` | [`vpxenc.c:1551-1563, 1903-1920`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vpxenc.c#1551) | Interactive `\r` progress line on `stderr`: emits `%.3f` (overall, Y, U, V) and `<size>F` | **Low**: lacks QP, lacks picture type, lacks frame flags, lacks decode index; carriage-return updates collapse across frames |
| Verbose flag | `-v` / `--verbose` | [`vpxenc.c:1854-1855`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vpxenc.c#1854) | Prints static encoder configuration at startup to `stderr` | **None**: outputs zero per-frame information |
| Spatial resampling | `--resize-allowed=1` | [`vpxenc.c:996-1006`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vpxenc.c#996) | Dynamic on-the-fly resolution switching | **None**: algorithmic encoder feature; provides zero telemetry output |
| libvpx C API controls | `VP8E_GET_LAST_QUANTIZER` / `VP8E_GET_LAST_QUANTIZER_64` | [`vp9/vp9_cx_iface.c:929-943, 2259-2260`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vp9/vp9_cx_iface.c#929) | C API queries: `VP8E_GET_LAST_QUANTIZER` returns native `base_qindex` (0..255); `VP8E_GET_LAST_QUANTIZER_64` returns mapped quantizer (0..63) | **High**: accurate per-frame quantizer from encoder state; C API only (not exposed in CLI) |
| FFmpeg `libvpx-vp9` quality stats | `AV_PKT_DATA_QUALITY_STATS` | [`libavcodec/libvpxenc.c:1425-1430`](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/libavcodec/libvpxenc.c#L1425-1430) | Packet side data: `quality = VP8E_GET_LAST_QUANTIZER_64 * 118`, `pict_type` (I or P), 3 SSE planes when `-flags +psnr` | **High**: in-memory packet side data; QP is 0..63 quantizer scale multiplied by 118 (ignored by Pelorus when this digest was written; mapped to qindex since) |
| FFmpeg `-vstats_file` | `-vstats_file <file>` | [`fftools/ffmpeg_mux.c`](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/fftools/ffmpeg_mux.c) | Text lines (`frame= %d q= 32.0 f_size= %d ... type= %c`) | **Low**: reports quantizer (0..63 scale) and frame size; lacks bit breakdown and plane PSNR |

### 2.3 Synthetic clip test and captured output

#### 2.3.1 `vpxenc --fpf` (binary first-pass stats)

Command run:

```console
/usr/bin/vpxenc --codec=vp9 --passes=2 --pass=1 --fpf=/tmp/pelorus-test-111/vpx_stats.fpf -o /tmp/pelorus-test-111/vpx_p1.ivf /tmp/pelorus-test-111/synth_320x240_10.y4m
```

- Output file: `/tmp/pelorus-test-111/vpx_stats.fpf` (2,376 bytes).
- Structure: exactly 11 binary records of 216 bytes each (10 individual frame records + 1 summary total record).
- Record unpack format (`<26dq`):

```c
typedef struct {
    double frame;
    double weight;
    double intra_error;
    double coded_error;
    double sr_coded_error;
    double frame_noise_energy;
    double pcnt_inter;
    double pcnt_motion;
    double pcnt_second_ref;
    double pcnt_neutral;
    double pcnt_intra_low;
    double pcnt_intra_high;
    double intra_skip_pct;
    double intra_smooth_pct;
    double inactive_zone_rows;
    double inactive_zone_cols;
    double MVr;
    double mvr_abs;
    double MVc;
    double mvc_abs;
    double MVrv;
    double MVcv;
    double mv_in_out_count;
    double duration;
    double count;
    double new_mv_count;
    int64_t spatial_layer_id;
} FIRSTPASS_STATS; /* 26 * 8 + 8 = 216 bytes */
```

Three unpacked records from `/tmp/pelorus-test-111/vpx_stats.fpf`:

```text
Record 0: frame=0.0 weight=1.295377 intra_err=410.80 coded_err=410.80 pcnt_inter=0.0000 dur=400000.0
Record 1: frame=1.0 weight=1.290728 intra_err=410.25 coded_err=42.62 pcnt_inter=0.9900 dur=400000.0
Record 2: frame=2.0 weight=1.289925 intra_err=413.20 coded_err=47.92 pcnt_inter=0.9733 dur=400000.0
```

11th summary record:

```text
Summary record (10): frame=45.0 weight=12.829451 intra_err=4232.74 coded_err=817.73 count=10.0
```

#### 2.3.2 `vpxenc --psnr` progress line

Command run:

```console
/usr/bin/vpxenc --codec=vp9 --passes=1 --lag-in-frames=0 --psnr -o /tmp/pelorus-test-111/vpx_psnr_lag0.ivf /tmp/pelorus-test-111/synth_320x240_10.y4m 2>&1
```

Three verbatim captured progress segments (from carriage-return stderr stream):

```text
Pass 1/1 frame    1/0          0B       0 us 0.00 fpm [ETA  unknown] 54.686 56.438 52.457 52.491    5967F
Pass 1/1 frame    2/1       5967B   13945 us 143.42 fps [ETA  0:00:00] 50.446 51.750 48.568 48.696    2042F
Pass 1/1 frame    3/2       8009B   26051 us 115.16 fps [ETA  0:00:00] 40.283 40.794 39.776 39.074     603F
```

Tokens emitted by [`vpxenc.c:1558`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vpxenc.c#1558):

- `54.686`: Overall PSNR in dB (`pkt->data.psnr.psnr[0]`).
- `56.438`: Luma Y PSNR in dB (`pkt->data.psnr.psnr[1]`).
- `52.457`: Chroma U PSNR in dB (`pkt->data.psnr.psnr[2]`).
- `52.491`: Chroma V PSNR in dB (`pkt->data.psnr.psnr[3]`).
- `5967F`: Coded frame size in bytes with trailing `F` token (`pkt->data.frame.sz`).

#### 2.3.3 FFmpeg `libvpx-vp9 -vstats_file`

Command run:

```console
ffmpeg -y -i /tmp/pelorus-test-111/synth_320x240_10.y4m -c:v libvpx-vp9 -b:v 500k -vstats_file /tmp/pelorus-test-111/vstats_vp9.log -f null -
```

Three verbatim lines from `/tmp/pelorus-test-111/vstats_vp9.log`:

```text
out=  0 st=  0 frame=     0 q= 32.0 f_size=   5964 s_size=        0KiB time= 0.010 br=  1192.8kbits/s avg_br=     0.0kbits/s type= I
out=  0 st=  0 frame=     1 q= 32.0 f_size=   2070 s_size=        6KiB time= 0.040 br=   414.0kbits/s avg_br=  1192.8kbits/s type= P
out=  0 st=  0 frame=     2 q= 32.0 f_size=    879 s_size=        8KiB time= 0.080 br=   175.8kbits/s avg_br=   803.4kbits/s type= P
```

---

## 3. Field mapping to PelorusEncTelemetryInput and QP scale semantics

### 3.1 Field-by-field crosswalk

| Telemetry field | Bit | x264 `.stats` line | x264 `-v` debug line | FFmpeg `libx264` side data | `vpxenc --fpf` | `vpxenc --psnr` | FFmpeg `libvpx-vp9` side data |
|---|---|---|---|---|---|---|---|
| `display_index` | 0 | `in:%d` | `Poc:%d / 2` | Not reported | `(uint32_t)frame` | Not reported | Not reported |
| `decode_index` | 1 | `out:%d` | `frame=%d` | Packet arrival order | Record index (0..N-1) | Frame out count | Packet arrival order |
| `frame_bytes` | 2 | `(tex+mv+misc)/8` | `size=%d` | `pkt->size` | Not reported | `<size>F` token | `pkt->size` |
| `header_bits` | 3 | `misc:%d` | Not reported | Not reported | Not reported | Not reported | Not reported |
| `residual_bits` | 4 | `tex:%d` | Not reported | Not reported | Not reported | Not reported | Not reported |
| `avg_qp` | 5 | `aq:%.2f` (or `q:%.2f`) | `QP=%.2f` | `quality / 118` | Not reported | Not reported | `quality / 118` (quantizer 0..63) |
| `avg_qp_norm` | 6 | Identity mapping | Identity mapping | Identity mapping | Not reported | Not reported | Not mapped (see §3.2) |
| `psnr_y` | 7 | Not reported | `PSNR Y:%5.2f` | Derived from SSE[0] | Not reported | 2nd float (`psnr[1]`) | Derived from SSE[0] |
| `psnr_u` | 8 | Not reported | `PSNR U:%5.2f` | Derived from SSE[1] | Not reported | 3rd float (`psnr[2]`) | Derived from SSE[1] |
| `psnr_v` | 9 | Not reported | `PSNR V:%5.2f` | Derived from SSE[2] | Not reported | 4th float (`psnr[3]`) | Derived from SSE[2] |
| `ssim_y` | 10 | Not reported | `SSIM Y:%.5f` | Not reported | Not reported | Not reported | Not reported |
| `intra_fraction` | 11 | `imb / total_mb` | `I / total_mb` | Not reported | `1.0 - pcnt_inter` | Not reported | Not reported |
| `inter_fraction` | 12 | `pmb / total_mb` | `P / total_mb` | Not reported | `pcnt_inter` | Not reported | Not reported |
| `skip_fraction` | 13 | `smb / total_mb` | `SKIP / total_mb` | Not reported | `intra_skip_pct` | Not reported | Not reported |
| `picture_type` | 14 | `I`/`i` -> `I`<br>`P` -> `P`<br>`B`/`b` -> `B` | `Slice:I` -> `I`<br>`Slice:P` -> `P`<br>`Slice:B` -> `B` | `pict_type` (1=I, 2=P, 3=B) | Not reported | Not reported | `pict_type` (1=I, 2=P) |
| `key_frame` (flag) | 15 | `type == 'I'` | `Slice == 'I' && Poc == 0` | `pict_type == I` | Inferred (`pcnt_inter == 0`) | Not reported | `pict_type == I` |
| `reference` (flag) | 16 | `type != 'b'` | `NAL > 0` | Not reported | Not reported | Not reported | Not reported |
| `shown` (flag) | 17 | True for all frames | True for all frames | True for all frames | True for all frames | True for all frames | True for all frames |
| `scene_cut` (flag) | 18 | `type == 'I' && in > 0` | Not reported | Not reported | Not reported | Not reported | Not reported |
| `qp_map` | 19 | In `.mbtree` (ref only) | Not reported | Not reported | Not reported | Not reported | Not reported |
| `bits_map` | 20 | Not reported | Not reported | Not reported | Not reported | Not reported | Not reported |
| `mode_map` | 21 | Not reported | Not reported | Not reported | Not reported | Not reported | Not reported |
| `coded_bit_depth` | 22 | `#options:` `bitdepth` | Stream depth (default 8) | Caller depth (default 8) | 8 | Stream depth (default 8) | Caller depth (default 8) |
| `codec` | - | `PEL_TLM_CODEC_H264` (1) | `PEL_TLM_CODEC_H264` (1) | `PEL_TLM_CODEC_H264` (1) | `PEL_TLM_CODEC_VP9` (5) | `PEL_TLM_CODEC_VP9` (5) | `PEL_TLM_CODEC_VP9` (5) |
| `qp_scale` | - | `PEL_QP_SCALE_SLICE_QP` (1) | `PEL_QP_SCALE_SLICE_QP` (1) | `PEL_QP_SCALE_SLICE_QP` (1) | Not reported | Not reported | `PEL_QP_SCALE_VP9_QINDEX` (3) / 0..63 |
| `granularity` | - | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) | `PEL_TLM_GRAN_FRAME` (1) |
| `metric_source` | - | Not reported | `PEL_TLM_METRIC_ENCODER` (1) | `PEL_TLM_METRIC_ADAPTER_SSE` (2) | Not reported | `PEL_TLM_METRIC_ENCODER` (1) | `PEL_TLM_METRIC_ADAPTER_SSE` (2) |
| `adapter` | - | `PEL_TLM_ADAPTER_EXTERNAL` (10) | `PEL_TLM_ADAPTER_EXTERNAL` (10) | `PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS` (1) | `PEL_TLM_ADAPTER_EXTERNAL` (10) | `PEL_TLM_ADAPTER_EXTERNAL` (10) | `PEL_TLM_ADAPTER_FFMPEG_QUALITY_STATS` (1) |

### 3.2 QP scale semantics and normalisation

#### 3.2.1 x264 (H.264 / AVC)

- Scale: `PEL_QP_SCALE_SLICE_QP` (1).
- Native valid range: `0.0f` to `51.0f` for 8-bit H.264 (up to `69.0f` for 10-bit H.264 where max QP is $51 + 6 \times (10 - 8) = 63$, and x264 configuration defaults allow `qpmax=69`).
- Normalization: `avg_qp_norm = avg_qp` (identity mapping, defined in [`libpelorus/src/telemetry.c:656-659`](../../libpelorus/src/telemetry.c)).
- Rate-control QP (`q`) vs Adaptive Quantization QP (`aq`):
  - In `encoder/ratecontrol.c:1858`, x264 writes both `q:%.2f` and `aq:%.2f`.
  - `q` is the macroblock-averaged frame quantizer computed by the rate-control model (`rc->qpa_rc`).
  - `aq` is the post-AQ macroblock-averaged frame quantizer (`h->fdec->f_qp_avg_aq`), which reflects the actual quantization applied across macroblocks after spatial complexity bias.
  - Telemetry adapters should map `aq` to `avg_qp` as it accurately reflects reconstruction quality.

#### 3.2.2 libvpx (VP9)

VP9 operates internally on a 0..255 `base_qindex` scale (`PEL_QP_SCALE_VP9_QINDEX` = 3), but libvpx and FFmpeg expose a 0..63 quantizer scale:

1. **Mapping between 0..63 quantizer and 0..255 qindex in libvpx:**

   In [`vp9/encoder/vp9_quantize.c:313-321`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vp9/encoder/vp9_quantize.c#L313):

   ```c
   static const int quantizer_to_qindex[] = {
     0,   4,   8,   12,  16,  20,  24,  28,  32,  36,  40,  44,  48,
     52,  56,  60,  64,  68,  72,  76,  80,  84,  88,  92,  96,  100,
     104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 148, 152,
     156, 160, 164, 168, 172, 176, 180, 184, 188, 192, 196, 200, 204,
     208, 212, 216, 220, 224, 228, 232, 236, 240, 244, 249, 255,
   };
   ```

   From index 0 to 60, `qindex = quantizer * 4`. Indices 61, 62, 63 map to 244, 249, 255.

   Inverse mapping [`vp9/encoder/vp9_quantize.c:328-335`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vp9/encoder/vp9_quantize.c#L328):

   ```c
   int vp9_qindex_to_quantizer(int qindex) {
     for (int quantizer = 0; quantizer < 64; ++quantizer)
       if (quantizer_to_qindex[quantizer] >= qindex) return quantizer;
     return 63;
   }
   ```

2. **FFmpeg `libvpxenc.c` behavior:**

   In [`libavcodec/libvpxenc.c:1428`](https://github.com/FFmpeg/FFmpeg/blob/n9.0.2/libavcodec/libvpxenc.c#L1428):
   `vpx_codec_control(&ctx->encoder, VP8E_GET_LAST_QUANTIZER_64, &quality);`
   `ff_encode_add_stats_side_data(pkt, quality * FF_QP2LAMBDA, ...);`

   `VP8E_GET_LAST_QUANTIZER_64` invokes `ctrl_get_quantizer64()` ([`vp9/vp9_cx_iface.c:937-943`](https://chromium.googlesource.com/webm/libvpx/+/6df3ec34557879fff673706f4a1d9fbd0f3a6f0e/vp9/vp9_cx_iface.c#937)), which executes `vp9_qindex_to_quantizer(vp9_get_quantizer(cpi))` and returns a quantizer in the range **0..63**.
   FFmpeg multiplies this 0..63 value by `FF_QP2LAMBDA` (118) and writes it into the side data `quality` field.

3. **Consequence for Pelorus:**

   As documented in [`docs/research/0111-ffmpeg-quality-stats-writers.md`](0111-ffmpeg-quality-stats-writers.md) and [`libpelorus/src/telemetry_adapters.c:1222-1225`](../../libpelorus/src/telemetry_adapters.c):

   ```c
   if (codec != PEL_TLM_CODEC_VP9) {
       const uint32_t quality = ffmpeg_read_u32le(buf);
       rc = ffmpeg_qstats_parse_qp(quality, qp_scale, coded_bit_depth, &rec);
   }
   ```

   Pelorus currently ignores VP9 QP from `AV_PKT_DATA_QUALITY_STATS` because `PEL_QP_SCALE_VP9_QINDEX` (3) specifies the native 0..255 qindex range (`tlm_qp_range` defines `lo=0, hi=255`), whereas `libvpxenc` writes a 0..63 value. Passing 0..63 under `VP9_QINDEX` produces mismatched semantics, and `pel_qp_normalize()` currently returns `PEL_ERR_UNSUPPORTED` for `PEL_QP_SCALE_VP9_QINDEX`.
   Issue #111 resolution: either mint an enum `PEL_QP_SCALE_VP9_ENCODER_QP` (0..63, analogous to `PEL_QP_SCALE_AV1_ENCODER_QP`) or expand `pel_tlm_parse_ffmpeg_quality_stats` to map the 0..63 quantizer to 0..255 `qindex` via `quantizer_to_qindex[quality / 118]`.

---

## 4. Recommendations and architectural pitfalls

### 4.1 Recommended formats for each adapter

#### 4.1.1 x264 adapter

1. **Option A: Text parser for x264 `.stats` file (Recommended for multi-pass / file workflows):**
   - Syntax is strictly structured and stable across x264 revisions: `#options:` header followed by `in:%d out:%d type:%c dur:%d cpbdur:%d q:%.2f aq:%.2f tex:%d mv:%d misc:%d imb:%d pmb:%d smb:%d d:%c ref:...;\n`.
   - Exposes display index, decode index, frame types (`I`, `i`, `P`, `B`, `b`), reference flags, pre/post AQ QP, and exact bit distributions.
   - Built natively by both standalone `x264 --pass 1 --stats` and `ffmpeg -pass 1 -x264opts stats=...`.
   - Does not require recompiling x264 or linking libx264.
2. **Option B: Parsing x264 `-v` stderr log stream (Recommended when PSNR/SSIM is required):**
   - Enables capturing distortion metrics (`PSNR Y`, `U`, `V` and `SSIM Y`) in a single pass without generating a multi-pass stats file.
   - Parsing regex/grammar: `x264 [debug]: frame=%d QP=%f NAL=%d Slice:%c Poc:%d I:%d P:%d SKIP:%d size=%d bytes[ PSNR Y:%f U:%f V:%f][ SSIM Y:%f]`.
3. **Option C: FFmpeg `QUALITY_STATS` side data (In-memory pipeline):**
   - For real-time or filter pipelines, FFmpeg's `libx264` already writes `AV_PKT_DATA_QUALITY_STATS` containing `quality / 118 = slice_qp`, `pict_type`, and SSE distortion planes (when `-flags +psnr` is set). Fully supported by `pel_tlm_parse_ffmpeg_quality_stats`.

#### 4.1.2 libvpx-vp9 adapter

1. **Scraping `vpxenc --fpf` is REJECTED:**
   - `--fpf` is a pre-encode analysis cache containing 216-byte raw C struct binary dumps (`FIRSTPASS_STATS`).
   - It contains motion estimation and variance statistics for rate-control planning; it contains **no coded frame sizes, no encoded qindex/QP, and no reconstructed PSNR**.
2. **Scraping `vpxenc` stderr progress stream is REJECTED:**
   - Interactive carriage returns (`\r`) overwrite progress in place; tokens lack QP, lack picture type, and lack keyframe flags.
3. **Option A: In-process C adapter (Recommended for issue #111):**
   - Modeled after `pel_tlm_from_libaom`: create an in-process adapter `pel_tlm_from_libvpx()` reading `vpx_codec_cx_pkt_t` packets.
   - After each frame encode, query `vpx_codec_control(&ctx, VP8E_GET_LAST_QUANTIZER, &qindex)` to obtain the exact 0..255 `qindex`.
   - Read coded frame bytes from `pkt->data.frame.sz` and PSNR from `pkt->data.psnr`.
4. **Option B: FFmpeg `QUALITY_STATS` side data with VP9 quantizer mapping:**
   - When running under FFmpeg, `libvpxenc` attaches `AV_PKT_DATA_QUALITY_STATS`.
   - Enhance Pelorus to accept `quality / 118` by mapping the 0..63 quantizer to native `qindex` (via `quantizer_to_qindex`) or by defining normalisation for VP9 0..63 quantizer.

### 4.2 Pitfalls and failure modes

1. **Binary struct drift in `--fpf`:**
   - `FIRSTPASS_STATS` in libvpx is not a frozen serialization format: it is a raw internal C struct subject to field additions (e.g. `spatial_layer_id` added in recent revisions). It has no magic header, no format version field, and depends on compiler alignment and architecture endianness. An external parser reading `--fpf` across different libvpx versions risks severe field offset misalignment.
2. **Binary struct layout in `.mbtree`:**
   - The `.mbtree` sidecar file consists of uncompressed raw macroblock delta QPs in big-endian Q8.8 fixed-point format. Crucially, x264 **only writes records for reference frames** (`h->fenc->b_kept_as_ref`), silently omitting non-reference B-frames (`'b'`). An adapter parsing `.mbtree` cannot assume 1:1 record-to-frame alignment without correlating frame types against the `.stats` text log.
3. **Multi-pass dependencies:**
   - Both x264 `.stats` and vpxenc `--fpf` are only generated during pass 1 of a multi-pass encode. In 1-pass CRF/CBR/ABR encodes, these files are not created unless explicitly forced.
4. **Progress bar interference on stderr:**
   - Scraping stderr from `x264 -v` requires suppressing or ignoring terminal escape codes and carriage returns. Fortunately, x264 explicitly disables `b_progress` when `i_log_level >= X264_LOG_DEBUG` (`x264.c:1947`), ensuring clean newline-delimited lines. In contrast, `vpxenc` mixes interactive progress updates and PSNR tokens on the same carriage-return line.
5. **QP scale ambiguity in VP9:**
   - Callers frequently conflate VP9's 0..63 CLI quantizer with the 0..255 native `qindex`. Standardizing on `PEL_QP_SCALE_VP9_QINDEX` (0..255) requires explicit conversion of `VP8E_GET_LAST_QUANTIZER_64` (multiplication by 4 for $Q \le 60$, with non-linear steps 244, 249, 255 for $Q \in \{61, 62, 63\}$).
