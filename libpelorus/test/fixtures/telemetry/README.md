<!-- markdownlint-disable MD013 -->
# Encoder telemetry fixtures

These are unedited stat files recorded from the distribution binaries below on 2026-10-15. Both encodes use one ten-frame 176x144 8-bit YUV 4:2:0 `testsrc2` clip. The fixed-offset options make the reported QP constant across picture types; x265 QP 32 and SVT-AV1 QP 34 have matching luma AC quantiser steps after normalisation.

| Fixture | Encoder |
| --- | --- |
| `x265-4.3-qp32.csv` | x265 4.3, GCC 16.1.1, 8/10/12-bit build |
| `svt-av1-4.2.0-qp34.stat` | SVT-AV1 v4.2.0 (release), library v4.2.0 |
| `vvenc-1.14.0-qp32.log` | VVenC v1.14.0, release-static |

The files were recorded from the repository root with these commands. The output paths were absent before the encoder commands because x265 appends to an existing CSV.

```console
mkdir -p <build-dir>/fixture-recording
/usr/bin/ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=176x144:rate=25 -frames:v 10 -pix_fmt yuv420p -f rawvideo -y <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv
/usr/bin/x265 --input <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv --input-res 176x144 --fps 25 --frames 10 --qp 32 --ipratio 1 --pbratio 1 --aq-mode 0 --csv <build-dir>/fixture-recording/x265-qp32.csv --csv-log-level 1 --output <build-dir>/fixture-recording/x265-qp32.hevc --pools 1 --frame-threads 1 --no-progress
/usr/bin/SvtAv1EncApp --input <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv --width 176 --height 144 --fps-num 25 --fps-denom 1 --frames 10 --qp 34 --aq-mode 0 --use-fixed-qindex-offsets 1 --key-frame-qindex-offset 0 --key-frame-chroma-qindex-offset 0 --qindex-offsets 0,0,0,0,0,0 --chroma-qindex-offsets 0,0,0,0,0,0 --enable-stat-report 1 --stat-file <build-dir>/fixture-recording/svt-av1-qp34.stat --output <build-dir>/fixture-recording/svt-av1-qp34.ivf --lp 1 --progress 0
vvencapp --threads 1 -v 4 -q 32 --preset fast -i synth.y4m -o /dev/null > <build-dir>/fixture-recording/vvenc-1.14.0-qp32.log
cp <build-dir>/fixture-recording/x265-qp32.csv libpelorus/test/fixtures/telemetry/x265-4.3-qp32.csv
cp <build-dir>/fixture-recording/svt-av1-qp34.stat libpelorus/test/fixtures/telemetry/svt-av1-4.2.0-qp34.stat
cp <build-dir>/fixture-recording/vvenc-1.14.0-qp32.log libpelorus/test/fixtures/telemetry/vvenc-1.14.0-qp32.log
```

The parser test reads the recorded text directly; the encoded bitstreams and raw input are not fixtures.

## libaom C-value fixture

`libaom-3.15.2-cq32.txt` was recorded by `libaom-record.c` from ten synthetic
64x64 8-bit I420 frames with libaom 3.15.2, AOM_Q, CQ level 32 (min/max
quantizer 32), lag zero, no altref or AQ, and one thread. The recorder is
intentionally outside Meson. Its header gives the exact
build and run commands. Columns in `libaom-3.15.2-cq32.txt` are display index,
decode index, flags (decimal), bytes, quantizer_64, then overall/Y/U/V PSNR.
The recorder checks packet counts and PTS before associating values and queries
QP before submitting another frame. `telemetry_adapter_test.c` replays the
recorded rows directly; libaom is required only to regenerate this fixture.
Frame 0 has flags `2031633` (`0x1F0011`): public KEY and INTRAONLY plus
encoder-private upper bits. Replay checks I + KEY for frame 0, P without KEY
for frames 1-9, QP 32, and each row's bytes and per-plane PSNR.
