<!-- markdownlint-disable MD013 MD060 -->
# The per-picture SEI space behind QSV's udu_sei

**Date:** 2026-10-10

**Decision:** patch 0023 (`ffmpeg-patches/0023-qsv-pelorus-udu-sei-budget.patch`), issue
[#286](https://github.com/VMAFx/pelorus/issues/286); extends
[ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md) (same strip-then-drop rule, QSV numbers).

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`) with the 22-patch stack of `master` at `847853d`, then with
patch 0023. Intel Arc A380, `iHD` 26.3.5, `vpl-gpu-rt` 26.3.5, `libvpl` 2.17.0, render node
`renderD130`. The host ran other jobs, so no timing is reported.

## Verdict

With `-udu_sei 1`, `hevc_qsv` and `h264_qsv` hand the oneVPL runtime every `SEI_UNREGISTERED` side-data
entry as an `mfxPayload`. The QSV runtime accepts a limited amount of user data SEI per picture. The
budget is per picture: it is the sum of the `mfxPayload.BufSize` values (SEI message type byte, size
bytes and payload, as the patch builds them) in one `mfxEncodeCtrl`, plus on `h264_qsv` the emulation
prevention bytes of the payloads. Patch 0023 keeps 4 040 and 40 960 bytes, writes a Pelorus
blob that does not fit without its per-cell maps, and does not write anything else that does not fit.

## Method

`carrierprobe` (an FFmpeg API program, not in the tree) encodes four gray frames with the encoder under
test, `udu_sei=1`, one record of payloads per frame, the payload bytes exactly as given. Each run is one
process under the shared GPU lock, with `HWDEV=qsv:/dev/dri/renderD130` and `LIBVA_DRIVER_NAME=iHD`.
The stream is parsed for complete SEI messages and decoded with stock FFmpeg `n9.0.2`. Payloads are
`0x61`-filled (no zero byte, so no emulation prevention) unless the row says zeros.

## Per payload or per picture

The budget counts the sum over the SEI messages of one picture, not the size of one payload and not
the sum of the side-data sizes, at 640x360, 1080p and 3840x2160 alike. Emulation prevention bytes do
not count on `hevc_qsv`; on `h264_qsv` they do, so zero-heavy payloads cost more there. Only two
payloads per picture are queued (`QSV_MAX_ENC_PAYLOAD`, the warning of patch 0019).

## Budgets

| Encoder | Budget per picture | Counted |
| --- | --- | --- |
| `hevc_qsv` | 4 040 bytes | `BufSize` |
| `h264_qsv` | 40 960 bytes | `BufSize` plus emulation prevention |

The margin covers SEI the runtime writes on its own (picture timing, buffering period) in case it shares
the space. The payloads the stock A/53 callback
already queued are charged first.

## After patch 0023

48 real analyze blobs (320x180 to 1080p, flat, gradient and detailed, `maps` on and off, cell 16 to 64;
sizes 184 to 49 144 bytes), the first four frames of each, one payload per frame:

| Encoder | Written whole | Written without maps | Dropped |
| --- | --- | --- | --- |
| `hevc_qsv` | 33 | 15 | 0 |
| `h264_qsv` | 44 | 3 | 0 (one run wrote no output) |

Every written blob equals the input or its maps-stripped form (`pelorus_sei_fit.h`), and every stream
decodes four pictures. Synthetic payloads on `hevc_qsv`: 4 023 bytes (the largest that fits the budget)
written whole; larger non-Pelorus payloads not written, with the warning; two payloads of 2 000 bytes
both written; two of 2 023 bytes (4 066 bytes) write the first only. On `h264_qsv`, 12 424 and 27 000
zero bytes are written whole; payloads past the budget, 28 000 zero bytes among them, are not written.

The tester stage `sidedata_roundtrip`, `hevc_qsv` and `h264_qsv`, five cases each (tool 0.4.8):

| Build | `hevc_qsv` | `h264_qsv` |
| --- | --- | --- |
| `master` (22 patches), tool 0.4.7 | 4 of 5: `flat-1080p-maps1-cell32` fails | 5 of 5 |
| patch 0023, tool 0.4.8 | 5 of 5: `flat-1080p-maps1-cell32` passes with the maps stripped on all four pictures | 5 of 5 |

## Limits of this evidence

One Arc A380 on one runtime build; another runtime or GPU needs a new measurement
(`tools/carrierprobe.c` is not in the tree; the method above is enough to repeat it). The budgets are
constants in `pelorus_sei_fit_qsv.h`; `av1_qsv` has no `udu_sei`.
