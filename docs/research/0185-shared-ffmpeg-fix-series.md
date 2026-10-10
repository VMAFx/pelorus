<!-- markdownlint-disable MD013 MD060 -->
# Shared FFmpeg fix series: switching the Pelorus stack onto VMAFx/ffmpeg-patches v0.1.0-rc.1

**Date:** 2026-10-10

**Decision:** [ADR-0185](../adr/0185-shared-ffmpeg-fix-series.md).

**Scope:** FFmpeg `n9.0.2` (pin `946fcce0`), shared series release
[`v0.1.0-rc.1`](https://github.com/VMAFx/ffmpeg-patches/releases/tag/v0.1.0-rc.1)
(four patches, tag commit `ea78aa5d`), then the 22-patch Pelorus stack of this
change. Host: Linux workstation with an NVIDIA RTX 4090 (driver 615.78.08), an
Intel Arc A380 (`xe`, `renderD130`, `iHD` 26.3.5, `vpl-gpu-rt` 26.3.5,
`libvpl` 2.17.0), gcc 16.2.1, clang 23.1.1, cosign v2.6.3, gh 2.102.0. The
host ran other jobs, so no timing is reported. Raw logs:
`.workingdir/evidence/shared-series-switch/` (local).

## Verdict

- The release verifies: sha256 pin, `SHA256SUMS`, cosign signature and build
  attestation all pass, and the verifier refuses five planted defects, each at
  the check meant for it.
- Series patch 0001 and Pelorus patch 0021 are the same diff, so 0021 leaves
  the stack. The other 22 patches apply on the series tip; `generate.sh`
  reproduces them byte for byte on a second run.
- The full replay builds and links FFmpeg, and the NVENC smoke passes all six
  cases on the RTX 4090.
- The tester's `sidedata_roundtrip` passes on `hevc_nvenc` and `h264_nvenc`
  (5 of 5 cases each); `hevc_qsv` and `h264_qsv` carry the blob on the Arc A380.

## What the series replaces

| Series patch | Pelorus before | Check |
| --- | --- | --- |
| 0001 queue family in `ff_vk_frame_barrier()` | patch 0021 | diff bodies identical (`diff` of both patches from the first `diff --git` line: no output); only the commit message differs |
| 0002 GCC 14 and 16 diagnostics, 50 files | none | no file in common with any Pelorus patch (`comm -12` of both file lists: empty); `libavcodec/h274.c`, which `scripts/gen-h274-grain-calibration.py` reads, is not among them |
| 0003 `hevc_nvenc` SEI budget | generic part of patch 0022 | same size formula as `pel_sei_nal_bytes()`: `n + n / 255 + 10` plus the emulation prevention bytes, with the same scan |
| 0004 drop of SEI that NVENC would truncate | none | a carrier holds no zero byte, so its count of emulation prevention bytes is 0 and `P + 3 > floor(4 * (Pmax + 5) / 3)` cannot hold for `Pmax >= P` |

Patch 0022's change to `nvenc.c` went from 145 added and 5 removed lines to 72
added lines: the stat block after `---` in
`ffmpeg-patches/0022-nvenc-pelorus-udu-sei.patch`, at `7b50240` (`git show
7b50240:ffmpeg-patches/0022-nvenc-pelorus-udu-sei.patch`) and in this change. Patches 0004, 0008 and
0011 changed in line numbers and blob ids only; their source diffs under
`ffmpeg-patches/files/` are untouched and apply with offsets.

## Verifier: positive and negative cases

`scripts/fetch-ffmpeg-series.sh --self-test`, on the genuine release files:

| Case | Planted defect | Refused at |
| --- | --- | --- |
| genuine release | none | accepted, unpacked |
| flipped byte | byte 4096 of the tarball plus one | sha256 pin |
| flipped byte, pin moved to it | the same tarball with its own sha256 as the pin | `sha256sum --check SHA256SUMS` |
| wrong pin | every hex digit of the pin shifted | sha256 pin |
| tampered checksums | a line added to `SHA256SUMS` that still matches a file | `cosign verify-blob` |
| wrong commit | every hex digit of the commit pin shifted | `gh attestation verify` |

Each check was then switched off once in a copy of the script
(`fetch-mutations.txt`). The self-test failed every time: without the pin
check the flipped byte is caught one check later than declared, without the
`SHA256SUMS` check the moved pin is caught by the attestation instead, and
without cosign or without the attestation the planted defect is accepted.
With no `cosign` on `PATH` the script exits 1 and removes its output directory.

The unpack checks need no network and run in the fast suite
(`fetch-ffmpeg-series-fail-closed`, `scripts/test-fetch-ffmpeg-series.py`): a
tarball that is not the pinned one, a truncated one, a series made for another
FFmpeg commit or tag, a `base.env` that names two commits, a listed patch that
is missing or leaves `patches/`, an empty series, a member outside the
top-level directory, a parent-directory member, a symlink member, an existing
output directory and a full run on a host without `cosign` are each refused,
and a well-formed series unpacks.
With each of those checks switched off in a copy of the script, the test
fails (`unpack-mutations.txt`). The first mutation run showed a defect, since
fixed: with the existing-directory check off, a failed `mkdir` of the output
directory did not stop the script (exit 0), because a command substitution
does not inherit `errexit`. Reading the member checks for the same reason
showed a second one, not reproduced with the small fixture and fixed as well:
a `grep -q` at the end of a `tar` pipe can end `tar` early, and `pipefail`
then reports the pipe as failed and hides the match. The script now reads each
listing whole before matching and sets `inherit_errexit`.

The new rules of `scripts/check-build-config.py` were switched off the same way
(`checker-mutations.txt`): series applied first, format range from the series
tip, sha256 shape, repository name and the hermetic `git am` in `generate.sh`.
`--self-test` failed for each.

## Replay and regeneration

| Step | Command | Result |
| --- | --- | --- |
| Regenerate, twice | `FFMPEG_REPO=... ffmpeg-patches/generate.sh` | exit 0 both times; the 22 patches of the second run equal the first (`diff -r`: no output) |
| Full replay | `JOBS=4 FFMPEG_REPO=... KEEP_DIR=... ffmpeg-patches/test/build-and-run.sh` | exit 0: fast suite green, `4 shared series and 22 Pelorus patches applied`, FFmpeg linked, ten filters and `pelorus_fgs` registered, encoder options present (QSV, libaom, SVT-AV1, NVENC, Vulkan) |
| NVENC smoke (inside the replay) | `test/nvenc-udu-sei-smoke.sh` | 6 of 6 pass: `maps-1080p`, `grid-1x1`, `grid-2^20`, `flat-h264`, `flat-hevc`, `foreign` |
| QSV ROI gate | `JOBS=4 FFMPEG_REPO=... ffmpeg-patches/test/qsv-roi-regression.sh` | exit 0, `QSV ROI regression: PASS` (series, Pelorus 0001 to 0004, the 0005 source diff; ASan and UBSan build, MBQP-absent build) |
| Tester image `build` stage | `docker build --target build` with the release in `.ffmpeg-series/` | exit 0: the pin is checked again in the image, 26 patches applied |
| `--unpack` with a tampered tarball | one byte changed | exit 1, no output directory |
| By-hand apply, as in the build guide | `xargs git am --3way` of both lists | 26 commits on the base |
| Diagnostics | `nvenc.o` with gcc and clang, `-Wall -Wextra` | 0 in `nvenc.c` and `pelorus_sei_fit.h` |

The `foreign` case now matches the series' log line (`Not writing a 2017-byte
user data unregistered SEI at pts ...: hevc_nvenc fails a picture ...`), since
the drop is series patch 0003.

The smoke reaches three paths of the patch 0022 hook from a filter graph: a
blob over the budget that is stripped and written (`maps-1080p`, `grid-2^20`),
a blob that fits whole (`grid-1x1`, `flat-hevc`) and the `h264_nvenc` path
without a budget (`flat-h264`). No filter emits a blob the hook cannot strip,
so that path is covered in the fast test `sei-fit`, whose `carry()` helper
follows the hook step by step: such a blob over the budget is dropped whole,
never cut, and without a budget it goes out whole as a carrier. With the
section-mask rule of `pelorus_sei_fit.h` switched off, that test fails
(`seifit-mutation.txt`).

## GPU results on the switched build

| Encoder | Check | Result |
| --- | --- | --- |
| `hevc_nvenc` (RTX 4090) | tester `sidedata_roundtrip`, tool 0.4.9 | pass, 5 of 5 cases; blobs in the stream and at the decode tap equal the written blob as zero-free carriers, maps stripped where the budget requires |
| `h264_nvenc` (RTX 4090) | same | pass, 5 of 5 cases, as zero-free carriers |
| `hevc_vulkan`, `h264_vulkan` (RTX 4090) | same | pass, 5 of 5 cases each |
| `hevc_qsv`, `h264_qsv` in the tester | same | `not_run`: the tester opened no QSV session on this three-GPU host (`Error creating a MFX session: -9`) |
| `hevc_qsv`, `h264_qsv` (Arc A380) | `pelorus_analyze_vulkan=maps=1:cell=32` at 640x360 into `-udu_sei 1`, VAAPI device `renderD130`, then a decode | 8 of 8 pictures carry the Pelorus blob on each encoder |

## Not executed here

- The three tester image kits were not built locally; only the shared `build`
  stage of `tools/tester/Containerfile` was. The pull request's `Tester
  publish` build job builds every kit.
- The `lavapipe` lane and the Windows job run in CI only.
- No AMD device was used: the change touches no Vulkan filter code.

## Sources

- Shared series release notes and README (apply order, release files, verification commands), read from the verified tarball.
- `cosign verify-blob --help` (v2.6.3) and `gh attestation verify --help` (2.102.0) for the flags the verifier passes.
- FFmpeg `n9.0.2` `libavcodec/nvenc.c` with series patches 0003 and 0004 applied (`prepare_sei_data_array()`, `nvenc_hevc_sei_fits()`, `nvenc_drop_truncated_udu_sei()`).
