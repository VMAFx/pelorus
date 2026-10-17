<!-- markdownlint-disable MD013 -->
# Encoder telemetry fixtures

These are unedited stat files recorded from the distribution binaries below on 2026-10-15 (x264 on 2026-10-17). The x265, SVT-AV1 and matched x264 encodes use one ten-frame 176x144 8-bit YUV 4:2:0 `testsrc2` clip. The fixed-offset options make the reported QP constant across picture types; x265 QP 32, x264 QP 32 and SVT-AV1 QP 34 have matching luma AC quantiser steps after normalisation; `test_matched_qp` checks all three within one step (#111).

| Fixture | Encoder |
| --- | --- |
| `x265-4.3-qp32.csv` | x265 4.3, GCC 16.1.1, 8/10/12-bit build |
| `svt-av1-4.2.0-qp34.stat` | SVT-AV1 v4.2.0 (release), library v4.2.0 |
| `vvenc-1.14.0-qp32.log` | VVenC v1.14.0, release-static |
| `x264-0.165-qp32.stats` | x264 0.165.3222 b35605a, GCC 16.2.1, recorded 2026-10-16; 30 frames, 320x240, 8-bit |
| `x264-0.165-qp32-matched.stats` | x264 0.165.3222 b35605a, GCC 16.2.1, recorded 2026-10-17; the 176x144 ten-frame clip above |

The files were recorded from the repository root with these commands. The output paths were absent before the encoder commands because x265 appends to an existing CSV.

```console
mkdir -p <build-dir>/fixture-recording
/usr/bin/ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=176x144:rate=25 -frames:v 10 -pix_fmt yuv420p -f rawvideo -y <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv
/usr/bin/x265 --input <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv --input-res 176x144 --fps 25 --frames 10 --qp 32 --ipratio 1 --pbratio 1 --aq-mode 0 --csv <build-dir>/fixture-recording/x265-qp32.csv --csv-log-level 1 --output <build-dir>/fixture-recording/x265-qp32.hevc --pools 1 --frame-threads 1 --no-progress
/usr/bin/x264 --input-res 176x144 --fps 25 --frames 10 --qp 32 --ipratio 1 --pbratio 1 --aq-mode 0 --no-mbtree --threads 1 --pass 1 --stats <build-dir>/fixture-recording/x264-qp32.stats -o <build-dir>/fixture-recording/x264-qp32.264 <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv
/usr/bin/SvtAv1EncApp --input <build-dir>/fixture-recording/testsrc2-176x144-25-10.yuv --width 176 --height 144 --fps-num 25 --fps-denom 1 --frames 10 --qp 34 --aq-mode 0 --use-fixed-qindex-offsets 1 --key-frame-qindex-offset 0 --key-frame-chroma-qindex-offset 0 --qindex-offsets 0,0,0,0,0,0 --chroma-qindex-offsets 0,0,0,0,0,0 --enable-stat-report 1 --stat-file <build-dir>/fixture-recording/svt-av1-qp34.stat --output <build-dir>/fixture-recording/svt-av1-qp34.ivf --lp 1 --progress 0
vvencapp --threads 1 -v 4 -q 32 --preset fast -i synth.y4m -o /dev/null > <build-dir>/fixture-recording/vvenc-1.14.0-qp32.log
cp <build-dir>/fixture-recording/x265-qp32.csv libpelorus/test/fixtures/telemetry/x265-4.3-qp32.csv
cp <build-dir>/fixture-recording/svt-av1-qp34.stat libpelorus/test/fixtures/telemetry/svt-av1-4.2.0-qp34.stat
cp <build-dir>/fixture-recording/vvenc-1.14.0-qp32.log libpelorus/test/fixtures/telemetry/vvenc-1.14.0-qp32.log
cp <build-dir>/fixture-recording/x264-qp32.stats libpelorus/test/fixtures/telemetry/x264-0.165-qp32-matched.stats
```

The parser test reads the recorded text directly; the encoded bitstreams and raw input are not fixtures.

The x264 fixture uses a separate 30-frame clip and the unmodified CQP pass-1 output. Its `#options:` line records the encoder configuration; the tests also plant distinct pre/post-AQ QPs because CQP disables AQ. Reproduce it from the repository root (temporary output paths must be absent):

```console
ffmpeg -loglevel error -f lavfi -i testsrc2=size=320x240:rate=25 -frames:v 30 -pix_fmt yuv420p /tmp/x.y4m
x264 --qp 32 --pass 1 --stats libpelorus/test/fixtures/telemetry/x264-0.165-qp32.stats -o /tmp/x.264 /tmp/x.y4m
```

No `.mbtree` sidecar is retained; this command disables mbtree (`mbtree=0`). The fixture's copyright and EUPL-1.2 coverage are recorded in `REUSE.toml`, as for the other recorded telemetry data.

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
