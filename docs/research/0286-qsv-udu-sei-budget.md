<!-- markdownlint-disable MD013 MD060 -->
# The per-picture SEI space behind QSV's udu_sei

**Date:** 2026-10-10

**Decision:** patch 0023 (`ffmpeg-patches/0023-qsv-pelorus-udu-sei-budget.patch`), issue
[#286](https://github.com/VMAFx/pelorus/issues/286); extends
[ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md) (same strip-then-drop rule, QSV numbers).

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`) with the 22-patch stack of `master` at `86c1236`, then with
patch 0023. Intel Arc A380, `iHD` 26.3.5, `vpl-gpu-rt` 26.3.5, `libvpl` 2.17.0, render node
`renderD130`. The host ran other jobs, so no timing is reported.

## Verdict

With `-udu_sei 1`, `hevc_qsv` and `h264_qsv` hand the oneVPL runtime every `SEI_UNREGISTERED` side-data
entry as an `mfxPayload`. Larger payloads damage the encoded access unit. The limit is per picture: it
is the sum of the `mfxPayload.BufSize` values (SEI message type byte, size bytes and payload, as the
patch builds them) in one `mfxEncodeCtrl`, not the size of one payload and not the sum of the side-data
sizes. `hevc_qsv` tolerates 4 107 bytes, `h264_qsv` about 42 420 bytes, the latter counted with the
emulation prevention bytes of the payloads. Patch 0023 keeps 4 040 and 40 960 bytes, writes a Pelorus
blob that does not fit without its per-cell maps, and does not write anything else that does not fit.

## Method

`carrierprobe` (an FFmpeg API program, not in the tree) encodes four gray frames with the encoder under
test, `udu_sei=1`, one record of payloads per frame, the payload bytes exactly as given. Each run is one
process under the shared GPU lock, with `HWDEV=qsv:/dev/dri/renderD130` and `LIBVA_DRIVER_NAME=iHD`.
The stream is parsed for complete SEI messages and decoded with stock FFmpeg `n9.0.2`. Payloads are
`0x61`-filled (no zero byte, so no emulation prevention) unless the row says zeros.

## Per payload or per picture (`hevc_qsv`, 1080p)

| Payloads in the picture (UUID + data bytes) | Sum of `BufSize` | Result |
| --- | --- | --- |
| 4 089 | 4 107 | complete, decodes |
| 4 090 | 4 108 | access unit damaged |
| 4 091 | 4 109 | access unit damaged, no picture decodes |
| 2 000 + 2 000 | 4 018 | both complete, decodes |
| 2 040 + 2 040 | 4 100 | both complete, decodes |
| 2 043 + 2 045 | 4 108 | access unit damaged |
| 2 044 + 2 045 | 4 110 | access unit damaged, no picture decodes |
| 2 100 + 2 100 | 4 218 | access unit damaged, no picture decodes |
| 1 500 + 1 500 + 1 500 | | third payload not queued: `QSV_MAX_ENC_PAYLOAD` is 2 (the warning of patch 0019) |

Two payloads whose side-data sizes sum to 4 088 bytes are damaged while one of 4 089 is not, so the sum
over side-data sizes is not the quantity. The sum of `BufSize` reaches 4 108 in both damaged
two-payload cases and in the single 4 090-byte case, and stays at or below 4 107 in every clean one.
The same boundary holds at 640x360 and 3840x2160. Emulation prevention bytes do not count on
`hevc_qsv`: a payload of 4 089 zero bytes (about 2 000 of them) is complete and decodes.

## `h264_qsv`

The encode fails (`Invalid FrameType:0`, or it crashes) once the SEI of a picture passes a
limit between 42 255 and 42 260 bytes of one payload (`BufSize` 42 422 to 42 427), at 640x360, 1080p
and 2160p alike. Two payloads of 21 125 bytes (`BufSize` 42 418) encode, two of 21 128 (42 424) do not,
so this limit is per picture as well. Zero-heavy payloads reach it earlier: 28 000 zero bytes encode and
28 400 do not, which is the same limit once the emulation prevention bytes (one per two zeros) are
counted.

### Intermittent crashes (not a size limit)

`h264_qsv` with payloads can also crash, producing no output, in a share of runs that grows with the
payload size. It reproduces with stock FFmpeg's own A/53 caption path, so it is not specific to Pelorus
or to patch 0019. Runs of the same input, 40 per size unless noted:

| Payload bytes | Crashes | Notes |
| --- | --- | --- |
| none (control) | 0 of 60 | |
| 100 | 0 of 16 | |
| 1 000 | 1 of 60 | one more run lost a payload in one picture |
| 2 000 | 1 of 40 | 0 of 16 in an earlier batch |
| 3 000, 4 089 | 0 of 30 each | |
| 4 000, 4 500, 5 000 | 1 of 30, 1 of 30, 0 of 30 | 4 000 and 5 000 each lost one payload once |
| 8 000 | 3 of 56 | |
| 12 424 | 1 of 8 | |
| 16 000 | 7 of 40 (stock patch 0019), 6 of 40 (patch 0023) | |
| 30 000 | 3 of 8 | |
| 40 000 | 2 of 8 | |

A second series of 80 runs per size, with the same probe, gave 0 of 80 at 2 KB, about 12 to 15 of 80 at
16 KB and 11 to 30 of 80 at 30 KB. `hevc_qsv` had none in 80 runs (1 000 and 4 000 bytes). The crash
happens inside Intel's QSV runtime and reproduces with stock FFmpeg, so Pelorus does not cause it.

This is a known issue, documented and not fixed in Pelorus. The crash is not tied to a size boundary the
budget can enforce (a smaller `h264_qsv` budget would lower the rate, not remove it), so `h264_qsv` with
`udu_sei` is documented as not crash-free. A fix is being worked on outside Pelorus. Evidence (local, git-ignored):
`.workingdir/evidence/qsv-udu-budget/logs/h264-flaky-*.txt` and `h264-rate-1.txt`.

## Budgets

| Encoder | Measured limit | Budget | Margin |
| --- | --- | --- | --- |
| `hevc_qsv` | 4 107 bytes of `BufSize` | 4 040 | 67 bytes (1.6 %) |
| `h264_qsv` | about 42 420 bytes of `BufSize` plus emulation prevention | 40 960 | about 1 460 bytes (3.4 %) |

The margin covers SEI the runtime writes on its own (picture timing, buffering period) in case it shares
the space, and the spread between runs near the `h264_qsv` limit. The payloads the stock A/53 callback
already queued are charged first.

## After patch 0023

48 real analyze blobs (320x180 to 1080p, flat, gradient and detailed, `maps` on and off, cell 16 to 64;
sizes 184 to 49 144 bytes), the first four frames of each, one payload per frame:

| Encoder | Written whole | Written without maps | Dropped | Damaged access unit | Runtime crash |
| --- | --- | --- | --- | --- | --- |
| `hevc_qsv` | 33 | 15 | 0 | 0 | 0 |
| `h264_qsv` | 44 | 3 | 0 | 0 | 1 (a 12 424-byte blob kept whole) |

Every written blob equals the input or its maps-stripped form (`pelorus_sei_fit.h`), and every stream
starts with its first NAL unit intact and decodes four pictures. Synthetic payloads on `hevc_qsv`:
4 023 bytes (the largest that fits the budget) written whole; 4 024, 4 089, 4 091, 12 424 and 49 144
bytes of a non-Pelorus payload not written, with the warning; two payloads of 2 000 bytes both written;
two of 2 023 bytes (4 066 bytes) write the first only. On `h264_qsv`, 12 424 and 27 000 zero bytes
are written whole; 40 800, 42 255 and 49 144 bytes and 28 000 zero bytes are not written.

The tester stage `sidedata_roundtrip`, `hevc_qsv` and `h264_qsv`, five cases each (tool 0.4.8):

| Build | `hevc_qsv` | `h264_qsv` |
| --- | --- | --- |
| `master` (22 patches), tool 0.4.7 | 4 of 5: `flat-1080p-maps1-cell32` fails (`Invalid FrameType:0`) | 5 of 5 |
| patch 0023, tool 0.4.8 | 5 of 5: `flat-1080p-maps1-cell32` passes with the maps stripped on all four pictures | 5 of 5 |

## Limits of this evidence

One Arc A380 on one runtime build; another runtime or GPU needs a new measurement
(`tools/carrierprobe.c` is not in the tree; the method above is enough to repeat it). The `h264_qsv`
limit sits among intermittent crashes, so its boundary is a two-run observation at each size, not a
guarantee. The budgets are constants in `pelorus_sei_fit_qsv.h`; `av1_qsv` has no `udu_sei`.
