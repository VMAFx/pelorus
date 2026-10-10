<!-- markdownlint-disable MD013 MD060 -->
# A zero-free carrier for Pelorus side data through NVENC

**Date:** 2026-10-10

**Decision:** [ADR-0183](../adr/0183-sidedata-zero-free-carrier.md), issue
[#284](https://github.com/VMAFx/pelorus/issues/284)

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`). Before the fix: stock `n9.0.2`
NVENC through a probe that attaches recorded payloads, and the `v0.4.0-rc.2`
candidate build (22 patches) for the blobs and for QSV. After the fix: this
branch's 22-patch stack, replayed by `ffmpeg-patches/test/build-and-run.sh`.
NVIDIA RTX 4090, driver 615.78.08, NVENC 13.1; Intel Arc A380 (`xe`), iHD
26.3.5, vpl-gpu-rt 26.3.5, libvpl 2.17.0; AMD Raphael iGPU, RADV. The
workstation ran other jobs (load 30 to 60), so timings are upper bounds.

## Verdict

NVENC writes a user data unregistered SEI truncated, and the decoder drops it,
when its emulation prevention bytes exceed `ceil(P / 3) + 3` for a `P`-byte
payload (the rule a separate investigation pinned on the same GPU, with 0
mismatches over about 77 000 payloads). Real Pelorus blobs hit it: 11 of 48
analyze cases lose every picture's side data on `h264_nvenc`. A COBS carrier
under its own UUID has no zero byte and therefore no emulation prevention, so
it cannot hit the rule. Written by patch 0022 on both NVENC encoders, it
brings every one of the 48 cases through intact: 384 of 384 pictures on
`h264_nvenc`, and on `hevc_nvenc` 168 pictures whole plus 216 with patch
0022's maps stripped, with no picture lost. The carrier costs 1 to 70 bytes
per blob.

## The NVENC rule (pinned separately)

A separate investigation on the same RTX 4090 and driver (local evidence
`.workingdir/evidence/nvenc-sei-zero-run-rule/`, not in the repository) pinned
the trigger: for one user data unregistered entry of `P` bytes (UUID
included) and `epb` emulation prevention bytes the spec requires for its RBSP,
the SEI is truncated if and only if `epb > ceil(P / 3) + 3`. With several entries
in one picture the limit follows the largest. It holds for `h264_nvenc` and
`hevc_nvenc`, IDR and P pictures, 320x180 and 1920x1080, with 0 mismatches over
about 77 000 payloads, a separator grid of 17 836 cases included. The simpler
candidates fail against the same data: "a zero run of 63 or more bytes" misses
7 314 and falsely flags 19 874 of 76 870 cases, "more than 30 emulation
prevention bytes" misses 67 and falsely flags 26 651. A payload without a zero
byte has `epb = 0`, so the rule cannot apply to it.

## Method

1. **Blobs.** `pelorus_analyze_vulkan` on the RTX 4090 for 4 sizes (320x180,
   640x360, 1280x720, 1920x1080) x 3 contents (flat gray, horizontal
   gradient, `testsrc2` with temporal noise) x 4 settings (`maps=0` at cell
   32, `maps=1` at cells 16, 32 and 64), 8 frames each, read back from
   `showinfo` (`gen_blobs.py`).
2. **Candidate forms** of each blob (`alt_measure.py`): the blob as written
   today; (a) COBS under the carrier UUID; (b) XOR with an xorshift32
   sequence; (c) zlib level 9; (d) the image cut into four SEI payloads.
   Offline: size, zero bytes, longest zero run, emulation prevention bytes.
3. **Through NVENC** (`carrierprobe.c`, linked against stock `n9.0.2`): one
   gray NV12 frame per recorded blob, `-udu_sei 1`, no B frames. Every SEI NAL
   of the stream is parsed (complete payload, trailing bits), and FFmpeg's
   decoder output is compared byte for byte. `hevc_nvenc` only where the
   payload fits its 1024-byte picture limit
   ([ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md)).
4. **The rule check** (`pred_margins.py`): the pinned predicate against every
   form of every blob, compared with the GPU outcome.
5. **After the fix** (`post_matrix.py`): the real pipeline, analyze on Vulkan
   into `h264_nvenc` and `hevc_nvenc` with `-udu_sei 1`, built from this
   branch. The written blob comes from `showinfo` before the encoder and is
   matched to the decoded picture by its `frame_pts`; the decoded payload is
   unwrapped with a strict COBS decoder.
6. **QSV and Vulkan.** The probe on the Arc A380 for size and zero-run
   payloads; and a re-encode of an `h264_nvenc` carrier stream with
   `h264_qsv` (A380) and `h264_vulkan` (RADV), where the decoder hands the
   carrier to the next encoder as frame side data.

Every GPU command ran under `flock /var/tmp/claude-1000/gpu-4090.lock`; the
device lines are in each log (`Device 0 selected: NVIDIA GeForce RTX 4090`,
`Loaded Nvenc version 13.1`, `VAAPI driver: Intel iHD driver ... 26.3.5`,
`Device 0 selected: ... (RADV RAPHAEL_MENDOCINO)`).

## Measurements

### Candidate forms through NVENC (before the fix)

| Form | Pictures intact, `h264_nvenc` | Pictures intact, `hevc_nvenc` | Size change | Zero bytes left | Longest zero run | Rule margin, worst case |
| --- | --- | --- | --- | --- | --- | --- |
| Blob (today) | 296/384 (11 cases lose all 8) | 160/168 (21 cases) | 0 | up to 40 328 | 40 328 | -7 822 (truncated) |
| (a) COBS carrier | 384/384 | 168/168 | +1 to +70 bytes (at most 0.54 %) | 0 | 0 | +65, and `epb = 0` always |
| (b) XOR | 384/384 | 168/168 | 0 | up to 211 | 2 | +65 |
| (c) zlib | 384/384 | 304/304 (38 cases) | -99.5 % to -20 % | present | 39 | +34 |
| (d) four payloads | 227/384 | 152/168 | +48 bytes | as the blob | 12 283 | -2 038 (truncated) |

The predicate agrees with the GPU on all 48 cases for every form. Stress
payloads as carriers through `h264_nvenc`: 60 000 zero bytes (a 60 017-byte
carrier of `0x01` bytes) and 60 000 random bytes, 4 of 4 pictures each; the
zero-heavy blobs as written today (1 624 to 49 144 bytes) and a UUID followed by
700 or 2 000 zero bytes: 0 of 4 each. Without emulation prevention the carrier's
NAL unit is smaller than the blob's in all 48 cases (320x180 flat, cell 32:
747 to 560 bytes), which is what `hevc_nvenc`'s budget counts.

### After the fix: the 48 cases through patch 0022

| Encoder | Pictures | Whole | Maps stripped (patch 0022) | Lost | SEI NALs complete | Form |
| --- | --- | --- | --- | --- | --- | --- |
| `h264_nvenc` | 384 | 384 | 0 | 0 | 384/384 | carrier on every picture |
| `hevc_nvenc` | 384 | 168 | 216 | 0 | 384/384 | carrier on every picture |

`maps=0` blobs stay 184 bytes and become 185-byte carriers on both encoders.
`hevc_nvenc` keeps the maps on 9 of the 36 `maps=1` cases (all 320x180 at
cells 32 and 64, and 640x360 at cell 64, every content); the rest go out
without maps as ADR-0181 designed. One `hevc_nvenc` case had one picture whose
written blob could not be read back from the log (another thread's log line
cut the `showinfo` line); its rerun matched all 8 pictures, and the table uses
the rerun. A first pass that matched pictures by position instead of
`frame_pts` reported 4 cases short for the same reason; matching by
`frame_pts` removed every difference.

### Decode cost

`pel_blob_unwrap()` on a debug (`-O0`) build of libpelorus: 0.17 ms for the
49 144-byte flat blob, 0.07 ms for the detailed one, 0.4 µs for the 184-byte
`maps=0` blob (`bench_carrier.c`).

### QSV and Vulkan

The carrier passes both unchanged: re-encoding an `h264_nvenc` carrier stream
(640x360 flat, cell 32, 1 625-byte carriers) with `h264_qsv` on the A380 and
with `h264_vulkan` on RADV gives 8 of 8 pictures with the same unwrapped blob.
Through the probe, carriers of 1 625 and 5 705 bytes pass `h264_qsv`, and the
1 625-byte one passes `hevc_qsv`.

QSV fails on size, not on zero runs, and the carrier does not change that:

| Encoder (A380) | Payload | Result |
| --- | --- | --- |
| `hevc_qsv` | up to 4 089 bytes, zero-heavy or not | written, decodes byte-exact |
| `hevc_qsv` | 4 090 bytes | access unit damaged; still decodes |
| `hevc_qsv` | 4 091 to about 10 000 bytes | access unit damaged; no picture decodes |
| `hevc_qsv` | about 11 000 bytes and more | encode fails, "Invalid FrameType:0" |
| `h264_qsv` | 12 424 bytes, zero-heavy | written, decodes byte-exact |
| `h264_qsv` | 49 144 bytes | encode fails, "Invalid FrameType:0" |

That is a separate defect ([#286](https://github.com/VMAFx/pelorus/issues/286)); patch 0019 needs its own budget of at most
4 089 bytes for `hevc_qsv`, as patch 0022 has for `hevc_nvenc`.

## Per-case tables

Before the fix, intact pictures per form ("-": over `hevc_nvenc`'s limit, not
run):

| Case | Blob bytes | Longest zero run | Carrier bytes | `h264_nvenc` blob / COBS / XOR / zlib / split | `hevc_nvenc` blob / COBS / XOR / zlib / split |
| --- | --- | --- | --- | --- | --- |
| 320x180-detailed-m0-c32 | 184 | 36 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-detailed-m1-c16 | 1624 | 248 | 1628 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 320x180-detailed-m1-c32 | 548 | 72 | 550 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-detailed-m1-c64 | 279 | 24 | 280 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-flat-m0-c32 | 184 | 20 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-flat-m1-c16 | 1624 | 1128 | 1625 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 320x180-flat-m1-c32 | 548 | 272 | 549 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 |
| 320x180-flat-m1-c64 | 279 | 46 | 280 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-gradient-m0-c32 | 184 | 20 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-gradient-m1-c16 | 1624 | 20 | 1628 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |
| 320x180-gradient-m1-c32 | 548 | 14 | 550 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 320x180-gradient-m1-c64 | 279 | 11 | 280 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 640x360-detailed-m0-c32 | 184 | 36 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 640x360-detailed-m1-c16 | 5704 | 702 | 5714 | 8/8 / 8/8 / 8/8 / 8/8 / 3/8 | - / - / - / - / - |
| 640x360-detailed-m1-c32 | 1624 | 248 | 1628 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 640x360-detailed-m1-c64 | 548 | 72 | 550 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 640x360-flat-m0-c32 | 184 | 20 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 640x360-flat-m1-c16 | 5704 | 4448 | 5705 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 640x360-flat-m1-c32 | 1624 | 1128 | 1625 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 640x360-flat-m1-c64 | 548 | 214 | 549 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 |
| 640x360-gradient-m0-c32 | 184 | 20 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 640x360-gradient-m1-c16 | 5704 | 880 | 5722 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |
| 640x360-gradient-m1-c32 | 1624 | 220 | 1628 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |
| 640x360-gradient-m1-c64 | 548 | 50 | 549 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1280x720-detailed-m0-c32 | 184 | 36 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1280x720-detailed-m1-c16 | 21784 | 1583 | 21815 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / - / - |
| 1280x720-detailed-m1-c32 | 5704 | 928 | 5711 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 1280x720-detailed-m1-c64 | 1624 | 248 | 1626 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 1280x720-flat-m0-c32 | 184 | 37 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1280x720-flat-m1-c16 | 21784 | 21626 | 21785 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1280x720-flat-m1-c32 | 5704 | 5546 | 5705 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1280x720-flat-m1-c64 | 1624 | 1467 | 1625 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1280x720-gradient-m0-c32 | 184 | 22 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1280x720-gradient-m1-c16 | 21784 | 3604 | 21785 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1280x720-gradient-m1-c32 | 5704 | 920 | 5723 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |
| 1280x720-gradient-m1-c64 | 1624 | 240 | 1629 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |
| 1920x1080-detailed-m0-c32 | 184 | 36 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1920x1080-detailed-m1-c16 | 49144 | 902 | 49214 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / - / - |
| 1920x1080-detailed-m1-c32 | 12424 | 2048 | 12440 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 1920x1080-detailed-m1-c64 | 3246 | 520 | 3249 | 8/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 1920x1080-flat-m0-c32 | 184 | 20 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1920x1080-flat-m1-c16 | 49144 | 40328 | 49146 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1920x1080-flat-m1-c32 | 12424 | 9968 | 12425 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1920x1080-flat-m1-c64 | 3246 | 1952 | 3247 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / 8/8 / - |
| 1920x1080-gradient-m0-c32 | 184 | 20 | 185 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 |
| 1920x1080-gradient-m1-c16 | 49144 | 8040 | 49145 | 0/8 / 8/8 / 8/8 / 8/8 / 0/8 | - / - / - / - / - |
| 1920x1080-gradient-m1-c32 | 12424 | 1980 | 12432 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |
| 1920x1080-gradient-m1-c64 | 3246 | 480 | 3256 | 8/8 / 8/8 / 8/8 / 8/8 / 8/8 | - / - / - / 8/8 / - |

After the fix:

| Case | `h264_nvenc`: pictures whole, complete carrier NALs | `hevc_nvenc`: pictures whole + maps stripped, complete carrier NALs |
| --- | --- | --- |
| 320x180-detailed-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-detailed-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 320x180-detailed-m1-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-detailed-m1-c64 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-flat-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-flat-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 320x180-flat-m1-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-flat-m1-c64 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-gradient-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-gradient-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 320x180-gradient-m1-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 320x180-gradient-m1-c64 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 640x360-detailed-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 640x360-detailed-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 640x360-detailed-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 640x360-detailed-m1-c64 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 640x360-flat-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 640x360-flat-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 640x360-flat-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 640x360-flat-m1-c64 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 640x360-gradient-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 640x360-gradient-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 640x360-gradient-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 640x360-gradient-m1-c64 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1280x720-detailed-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1280x720-detailed-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-detailed-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-detailed-m1-c64 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-flat-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1280x720-flat-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-flat-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-flat-m1-c64 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-gradient-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1280x720-gradient-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-gradient-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1280x720-gradient-m1-c64 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-detailed-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1920x1080-detailed-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-detailed-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-detailed-m1-c64 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-flat-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1920x1080-flat-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-flat-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-flat-m1-c64 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-gradient-m0-c32 | 8/8, 8/8 | 8 + 0 of 8, 8/8 |
| 1920x1080-gradient-m1-c16 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-gradient-m1-c32 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |
| 1920x1080-gradient-m1-c64 | 8/8, 8/8 | 0 + 8 of 8, 8/8 |

## Reproduction

The scripts are local evidence under
`.workingdir/evidence/sidedata-carrier-abi15/tools/`; the shipped checks are:

```sh
meson test -C build --suite=fast --print-errorlogs interop-abi sei-fit analyze-maps-check-self-test
FFMPEG_REPO=/abs/ffmpeg JOBS=4 KEEP_DIR=/abs/keep ffmpeg-patches/test/build-and-run.sh
FFMPEG_BIN=/abs/keep/ffmpeg LD_LIBRARY_PATH=/abs/keep/lib ffmpeg-patches/test/nvenc-udu-sei-smoke.sh
```

`nvenc-udu-sei-smoke.sh` passes on this stack (six cases, `flat-h264` and
`flat-hevc` new) and fails on the rc.2 build with "8 Pelorus blobs not in the
carrier form".

## References

- Issue [#284](https://github.com/VMAFx/pelorus/issues/284); NVENC rule: `.workingdir/evidence/nvenc-sei-zero-run-rule/results.md` (local evidence, not in the repository; key numbers above); QSV size defect: [#286](https://github.com/VMAFx/pelorus/issues/286).
- [ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md), [research 0181](0181-hevc-nvenc-sei-header-budget.md).
- S. Cheshire and M. Baker, "Consistent Overhead Byte Stuffing", IEEE/ACM Transactions on Networking 7(2), 1999.
- FFmpeg `n9.0.2`: `libavcodec/hevc/ps.c:1255` ("VPS %d does not exist"), `libavcodec/hevc/hevcdec.c:3678` ("Skipping invalid undecodable NALU").
