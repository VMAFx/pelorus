<!-- markdownlint-disable MD013 MD060 -->
# ADR-0218: The SEI Fit Helper Includes the Series nvenc_sei.h

- **Status**: Accepted
- **Implementation**: landed (#386)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: encoders, nvenc, sei, ffmpeg-patches, shared-series, fast-suite
- **Related**: [ADR-0181](0181-hevc-nvenc-sei-header-budget.md), [ADR-0183](0183-sidedata-zero-free-carrier.md), [ADR-0185](0185-shared-ffmpeg-fix-series.md), [ADR-0188](0188-named-test-skips.md)

## Context

`ffmpeg-patches/files/pelorus_sei_fit.h` decides whether a Pelorus payload fits the 1024-byte non-VCL limit of `hevc_nvenc` (issue #267, ADR-0181). It carried its own copy of the rules FFmpeg's `nvenc.c` applies: the 1024-byte cap, the 256-byte reserve and the emulation-prevention scan that sizes a SEI NAL unit.

Shared FFmpeg fix series `v0.3.0` (VMAFx/ffmpeg-patches, patch 0027) moves those rules out of `nvenc.c` into a new plain-C header, `libavcodec/nvenc_sei.h`: `NVENC_HEVC_NON_VCL_LIMIT`, `NVENC_HEVC_SEI_RESERVE`, `NVENC_HEVC_SEI_BUDGET`, `nvenc_sei_epb_count()` and `nvenc_sei_nal_size()`. With two copies, a series change to the rules would leave Pelorus deciding with the old ones and no test would notice.

The header exists only inside an FFmpeg tree with the series applied. The fast suite builds `sei_fit_test.c` without an FFmpeg tree, so it needs the header from somewhere.

## Decision

1. **Pin series `v0.3.0` and include its header.** `build-config.env` moves `FFMPEG_SERIES_TAG` to `v0.3.0`. `pelorus_sei_fit.h` includes `nvenc_sei.h` and drops its own cap, reserve and scan: `PEL_SEI_HEVC_USER_BUDGET` is `NVENC_HEVC_SEI_BUDGET` and `pel_sei_nal_bytes()` calls `nvenc_sei_nal_size()`. `pelorus_sei_fit_qsv.h` uses `nvenc_sei_epb_count()`. Patch 0022 logs the limit from `NVENC_HEVC_NON_VCL_LIMIT`.

2. **Same fit decisions, checked against a frozen oracle.** `sei_fit_test.c` keeps the pre-0218 `pel_sei_nal_bytes()` as `frozen_nal_bytes()` and compares both for every payload length up to 1100 bytes, four byte patterns and the limits 0, the budget and `SIZE_MAX`. The two differ in one place: for a payload over the limit, the series compares the NAL size with the limit while scanning, the frozen copy compared the payload size before scanning. Both then return a value over the limit, so the decision is the same, but the value differs (36 such cases, none of them fits). The test therefore requires equal values only where the payload fits and the same decision everywhere.

3. **The fast suite reads the header from the fetched series, never from a copy.** Meson option `ffmpeg_series_dir` names the directory `scripts/fetch-ffmpeg-series.sh` printed. `scripts/extract-series-file.py` writes `nvenc_sei.h` into the build directory from the one series patch that creates it, and refuses when no patch or more than one patch touches the path, or when the patch is anything but one new-file hunk of exactly the stated length.

4. **Without the option, a named skip.** `sei-fit` is then a test that prints `SKIP: ffmpeg_series_dir unset ...` and exits 77, listed in `.config/test-skips.json` (ADR-0188). `make` passes `FFMPEG_SERIES_DIR` through, and `make tidy` names the clang-tidy unit it leaves out when that is empty.

5. **CI requires the test to run, and a planted divergence must fail.** The `core`, `sanitizers` and `windows` jobs fetch and verify the series, configure with `-Dffmpeg_series_dir`, and run `check-test-skips.py --require pelorus:sei-fit --require pelorus:sei-fit-planted`: a skip, a failure or a missing test fails the job. `sei-fit-planted` builds the same test against the header with `NVENC_HEVC_SEI_RESERVE` planted from 256 to 255 and is registered `should_fail`, so the suite turns red if the test stops noticing a divergence.

## Alternatives considered

| Option | Verdict | Reason |
| :--- | :--- | :--- |
| Keep the local copy of the rules | Rejected | Two copies of the same budget and size formula drift; the point of patch 0027 is one copy. |
| Commit a copy of `nvenc_sei.h` under `ffmpeg-patches/files/` | Rejected | A copy is a second source that can go stale against the pinned series; a check comparing it with the series would still need the series in CI. |
| Make the fast suite fetch the series itself | Rejected | The fast suite must run offline; network access inside `meson test` would turn a missing network into a test failure or a silent skip. |
| Skip `sei-fit` without a series and accept the skip in CI | Rejected | A listed skip passes everywhere; CI would never run the fit checks. `--require` makes CI fail on the skip. |

## Consequences

- A local build without `FFMPEG_SERIES_DIR` no longer runs the SEI fit checks; it prints the named skip. `make FFMPEG_SERIES_DIR=<dir> verify-native` runs them, with `<dir>` from `scripts/fetch-ffmpeg-series.sh`.
- The `core`, `sanitizers` and `windows` CI jobs now need network access, `gh` with a token and `cosign`, as the `ffmpeg-stack` job already did.
- The release build (`.github/workflows/release-build.yml`) runs the fast suite without the series, so `sei-fit` is the named skip there; the `core` CI job runs it on every pull request into `master`. Running it in the release build is follow-up [#388](https://github.com/VMAFx/pelorus/issues/388).
- A rebuilt header follows every patch that `series.txt` lists, and a relative `ffmpeg_series_dir` fails at configure time.
- A series release that edits `nvenc_sei.h` in a later patch makes the extractor refuse until it reads the final state; that change comes with this repository's next series bump.

## References

- Issue [#386](https://github.com/VMAFx/pelorus/issues/386).
- Shared series `v0.3.0` patch `0027-avcodec-nvenc-move-the-SEI-size-rules-into-nvenc_sei.patch`.
- Fast-suite header source chosen by the maintainer through the fleet decision ledger, 2026-10-18 (paraphrased): an option names the fetched series, CI fetches it and requires the test, a local run without it is a named skip, no copy in the repository.
