<!-- markdownlint-disable MD013 MD060 -->
# The 1024-byte header limit behind hevc_nvenc's udu_sei ENOMEM

**Date:** 2026-10-09

**Decision:** [ADR-0181](../adr/0181-hevc-nvenc-sei-header-budget.md), issue
[#267](https://github.com/VMAFx/pelorus/issues/267)

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`) with the 21-patch stack of
`v0.4.0-rc.1`, then with patch 0022; nv-codec-headers `13.1.15.0.1`; NVIDIA
RTX 4090, driver 615.71.09. The tester image (nv-codec-headers `n12.1.14.0`,
same driver) failed the same way. The workstation ran other jobs, so no timing
is reported.

## Verdict

NVENC's HEVC encoder refuses a picture whose non-VCL NAL units add up to more
than 1024 bytes: VPS, SPS, PPS, access unit delimiter and every SEI NAL unit,
counted in Annex B form with start codes and emulation prevention bytes.
`nvEncLockBitstream()` then returns `NV_ENC_ERR_OUT_OF_MEMORY`, which FFmpeg
reports as `AVERROR(ENOMEM)`. The analyze blob with maps (12 424 bytes at 1080p,
`cell=32`) is twelve times that. `h264_nvenc` has no such limit up to 64 KiB.
Neither header version nor the NVENC programming guide 13.1 documents it.

## Reproduction

```text
$ ffmpeg -init_hw_device vulkan=vk:0 -filter_hw_device vk \
    -f lavfi -i testsrc2=size=1920x1080:rate=25 -frames:v 4 \
    -vf "format=nv12,hwupload,pelorus_analyze_vulkan=maps=1:cell=32,hwdownload,format=nv12" \
    -c:v hevc_nvenc -udu_sei 1 -f hevc out.hevc
[hevc_nvenc] Failed locking bitstream buffer: out of memory (10):
[enc:hevc_nvenc] Error submitting video frame to the encoder
[vost#0:0/hevc_nvenc] Terminating thread with return code -12 (Cannot allocate memory)
```

`maps=0` (184-byte blob) encodes, and so do `h264_nvenc` with `maps=1`. Even
`cell=64` (3 246 bytes) fails. The message comes from `process_output_surface()`
in stock `libavcodec/nvenc.c`; the Pelorus NVENC patches (0004, 0008, 0011) do
not touch the SEI or bitstream path.

## Method

A small libavcodec program (not in the tree) encoded gray NV12 frames with
`hevc_nvenc` and `udu_sei=1`, attached N-byte `AV_FRAME_DATA_SEI_UNREGISTERED`
entries to chosen frames, required one packet per frame, and bisected N. A
second bisect inserted the SEI with `h264_metadata=sei_user_data`, decoded it
back into frame side data and re-encoded. The non-VCL bytes per access unit
were summed from the Annex B output.

## Measurements

| Case (1920x1080 unless named) | Largest entry that encodes | Non-VCL bytes of that picture |
| --- | --- | --- |
| IDR picture, one entry | 898 | 1024: VPS 28, SPS 62, PPS 11, NVENC picture timing SEI 13, user SEI 910 |
| P picture, one entry | 999 | 1024: picture timing SEI 13, user SEI 1011 |
| P picture, two entries | 495 each | 1023: 13 + 505 + 505 |
| P picture, entry of zero bytes | 747 | 1023: the user SEI NAL is 1010 bytes with emulation prevention |
| IDR, 256x64 / 4096x128 / 3840x2160 | 901 / 900 / 898 | the SPS size changes with the picture size |
| IDR, `-preset p7 -bf 3 -aud 1` | 891 | the access unit delimiter takes 7 bytes |
| IDR, `-preset p1 -tune ll` / `-preset p4 -multipass fullres` with AQ | 898 / 899 | |
| `h264_nvenc`, every picture | more than 65 536 | |

NVENC's own non-VCL bytes: 114 on an IDR picture and 13 on a P picture at
1080p; 128 and 19 at 7680x4320 Main10 with BT.2020 PQ VUI and `-aud 1`. The
1024-byte limit holds at every preset tried and does not grow with the
picture size.

At the end of a stream the failure is silent: a single over-limit frame gives
no packet and the encode returns success; with two frames, the over-limit IDR
picture is missing from the output. Only the `Failed locking bitstream buffer`
line shows it.

## After patch 0022

The same command encodes all pictures. The encoder names the change once and
totals it at close:

```text
[hevc_nvenc] udu_sei: frame pts 0: the 12424-byte Pelorus side data is written without its per-cell maps (184 bytes): hevc_nvenc writes at most 1024 bytes of parameter sets and SEI per picture.
[hevc_nvenc] udu_sei: 4 frame(s) carried Pelorus side data without its per-cell maps and 0 user data unregistered SEI payload(s) were not written, to stay within hevc_nvenc's 1024 bytes of parameter sets and SEI per picture.
```

The output (128 235 bytes) is byte-identical in size to the `maps=0` encode,
because the stripped blob equals the `maps=0` blob (`sei_fit_test.c` checks
this byte for byte). `ffmpeg-patches/test/nvenc-udu-sei-smoke.sh` on the
RTX 4090:

| Case | Before (21 patches) | After (patch 0022) |
| --- | --- | --- |
| `maps-1080p` | FAIL: `Failed locking bitstream buffer: out of memory (10)` | PASS: 8 pictures, scalar sections in the stream |
| `grid-1x1` | not reached | PASS: the 201-byte blob keeps its 1x1 maps |
| `grid-2^20` (8192x8192, `cell=8`, 6 MiB blob) | not reached | PASS: scalar sections in the stream |
| `foreign` (2 017-byte non-Pelorus SEI) | not reached | PASS: not written, logged, 8 pictures |

With `CUDA_VISIBLE_DEVICES=` the script exits 77 with
`SKIP: hevc_nvenc cannot encode on this host: ... CUDA_ERROR_NO_DEVICE`.

## References

- `/usr/include/ffnvcodec/nvEncodeAPI.h` (13.1.15.0.1) lines 2396-2404 and
  nv-codec-headers `n12.1.14.0` lines 2188-2196: `NV_ENC_SEI_PAYLOAD`, no size
  limit stated.
- NVENC Video Encoder API Programming Guide 13.1, section 8.25 (read
  2026-10-09): `seiPayloadArray` for user SEI, no size limit stated.
- Evidence (local): `.workingdir/evidence/267-hevc-nvenc-sei/`.
