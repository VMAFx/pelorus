<!-- markdownlint-disable MD013 MD060 -->
# Research 0111: FFmpeg QUALITY_STATS writers

Checked on 2026-10-15 against an FFmpeg n9.0.1 source tree. This digest records why
`pel_tlm_parse_ffmpeg_quality_stats` reads the side data the way it does; the
caller contract lives in [the API page](../api/encoder-telemetry.md).

## Layout as written

`ff_encode_add_stats_side_data` (`libavcodec/encode.c:972`) writes the
little-endian `quality` (QP times `FF_QP2LAMBDA` = 118), the picture type,
the error count and then one 64-bit SSE per entry.

- It never writes bytes 6 and 7. They read as zero only because a fresh
  buffer comes from `av_packet_new_side_data`, which zero-fills.
- It reuses side data already on the packet and only requires
  `size >= 4 + 4 + 8 * error_count`, so a buffer may be longer than the
  entries it carries.

The parser therefore ignores bytes 6 and 7 and any trailing bytes, and refuses
only a buffer shorter than the entries it announces.

## What each encoder writes

| Encoder | Call site | `quality` | Consequence for Pelorus |
| --- | --- | --- | --- |
| `nvenc` | `nvenc.c:2646` | `(frameAvgQP - 1) * 118` | parsed QP is one below NVENC `frameAvgQP` |
| `libsvtav1` | `libsvtav1.c:761` | `headerPtr->qp * 118` (picture QP 0..63) | caller passes `AV1_ENCODER_QP` |
| `libaomenc` | `libaomenc.c:1143` | 0, plus three SSE values when `have_sse` | QP not reported; PSNR from SSE |
| `libvpxenc` | `libvpxenc.c:1428` | `VP8E_GET_LAST_QUANTIZER_64 * 118` (0..63) | no Pelorus scale fits; VP9 QP ignored |
| `qsvenc` | `qsvenc.c:2694` | H.264 only | other codecs carry no side data |

## Limits

- Quality 0 means "not reported" for `libaomenc`, but a frame coded at QP 0
  writes the same value; such a frame reports no QP.
- Raw AV1 qindex has no normalisation in `pel_qp_normalize`, so the parser
  refuses `PEL_QP_SCALE_AV1_QINDEX`.
- Other writers (`amfenc`, `dnxhd`, `kvazaar`, `xavs`, `xvid`,
  `mpegvideo`, `snow`) were not mapped in this round.
