<!-- markdownlint-disable MD013 MD060 -->
# ADR-0236: The SEI Fit Helper Keeps Its Own Rules on FFmpeg's Merged Form

- **Status**: Accepted
- **Implementation**: this pull request
- **Date**: 2026-10-25
- **Deciders**: Lusoris
- **Tags**: encoders, nvenc, sei, ffmpeg-patches, shared-series, fast-suite
- **Related**: [ADR-0181](0181-hevc-nvenc-sei-header-budget.md), [ADR-0183](0183-sidedata-zero-free-carrier.md), [ADR-0185](0185-shared-ffmpeg-fix-series.md), [ADR-0218](0218-sei-fit-includes-nvenc-sei.md) (superseded)

## Context

ADR-0218 made `ffmpeg-patches/files/pelorus_sei_fit.h` include `libavcodec/nvenc_sei.h`, which shared FFmpeg fix series `v0.3.0` creates (patch 0027), and use its `NVENC_HEVC_SEI_BUDGET`, `nvenc_sei_nal_size()` and `nvenc_sei_epb_count()`. The fast suite extracted that header from the fetched series.

FFmpeg commit `96b7f8281a` merged a different form of the NVENC user-data SEI rules. It defines `NVENC_HEVC_SEI_MAX_PAYLOAD` (512) in `nvenc.c` and adds `nvenc_hevc_sei_size_check()`, which on `hevc_nvenc` walks the SEI of a picture in order, keeps each one while the payload total stays at or under 512 bytes, and frees the rest. There is no `nvenc_sei.h`. The series drops its patches 0003, 0004 and 0028 for that backport (plan `ffmpeg-patches-drop-24994-plan`), so the names ADR-0218 calls stop existing.

Two limits then apply on `hevc_nvenc`: the 1024-byte non-VCL limit of the encoder that the series rules modelled (768 bytes for SEI after the 256-byte reserve), and FFmpeg's 512-byte payload limit. A Pelorus blob that passes the first and fails the second is freed by FFmpeg without a word, and its per-cell maps are not stripped first.

## Decision

1. **Pelorus keeps its own rules in `pelorus_sei_fit.h`.** `PEL_SEI_HEVC_NON_VCL_LIMIT` (1024), `PEL_SEI_HEVC_RESERVE` (256), `PEL_SEI_HEVC_USER_BUDGET` (768), `PEL_SEI_HEVC_PAYLOAD_LIMIT` (512), `pel_sei_epb_count()`, `pel_sei_nal_size()` and `pel_sei_udu_truncated()` are the `v0.3.0` rules, frozen. No name from the series or from `nvenc.c` is called. `pelorus_sei_fit_qsv.h` uses `pel_sei_epb_count()`.

2. **The budget covers both limits.** `PelSeiHevcRoom` tracks the NAL bytes and the payload bytes the SEI before a blob leave. `pel_sei_hevc_charge()` always charges a SEI's NAL bytes and charges its payload bytes only when it fits the payload room left, which mirrors `nvenc_hevc_sei_size_check()`: a SEI FFmpeg frees does not count. `pel_sei_hevc_budget()` returns the NAL room, capped by the NAL size of a payload that fills the payload room (524 bytes on an empty picture).

3. **Patch 0022 runs its hook right after the copy and drops what does not fit.** `pelorus_udu_carrier()` is called after `av_memdup()` in `prepare_sei_data_array()`, which applies with and without the series patches. A blob over the budget loses its per-cell maps first (ADR-0181). A carrier that still does not fit is freed and skipped, with a warning for the first and a count at close, instead of being left to FFmpeg's silent drop.

4. **The fast suite runs without the series.** `sei_fit_test.c` checks the header against frozen copies of the `v0.3.0` rules, pins the room arithmetic, models a picture with the FFmpeg size check, and checks the hook's keep, strip and drop decisions. `meson_options.txt` loses `ffmpeg_series_dir`, `scripts/extract-series-file.py` and `ffmpeg-patches/test/series/` go, and `sei-fit` is no named skip any more.

5. **Each rule has a planted twin that must fail.** `scripts/plant-header.py` writes the header with one rule changed (reserve, payload limit, emulation-prevention scan, truncation rule, payload charge). Meson builds `sei-fit-planted-<rule>` against each copy, registered `should_fail`. CI requires `sei-fit` and all five planted tests through `check-test-skips.py --require`.

## Alternatives considered

| Option | Verdict | Reason |
| :--- | :--- | :--- |
| Call `nvenc_hevc_sei_size_check()` or `NVENC_HEVC_SEI_MAX_PAYLOAD` from `nvenc.c` | Rejected | Both are file-local to `nvenc.c`; the fast suite builds without an FFmpeg tree, and the 1024-byte encoder limit is not in the merged form at all. |
| Keep ADR-0218 and carry `nvenc_sei.h` as a Pelorus patch | Rejected | It keeps a second copy of rules upstream no longer has, and every series bump would need the extractor to follow it. |
| Leave over-budget blobs to FFmpeg's size check | Rejected | FFmpeg frees them without a log line and without the map strip that would have let them fit. |
| Charge every earlier SEI's payload in full | Rejected | FFmpeg frees a SEI that does not fit and does not count it; charging it would drop Pelorus blobs that FFmpeg keeps. |

## Consequences

- The fast suite runs the SEI fit checks everywhere, offline. The `core`, `sanitizers` and `windows` CI jobs no longer fetch the series, so they need no token and no `cosign` for this.
- The release build (`.github/workflows/release-build.yml`) now runs `sei-fit` instead of a named skip, which closes follow-up [#388](https://github.com/VMAFx/pelorus/issues/388).
- On `hevc_nvenc` a Pelorus blob gets at most 524 bytes of NAL room on an empty picture instead of 768. Maps are stripped earlier; `sei_fit_test.c` checks which blobs keep, lose their maps or drop. The `flat-hevc` case of `ffmpeg-patches/test/nvenc-udu-sei-smoke.sh` (320x180 flat, cell 32, a 560-byte carrier) now loses its maps, and its `foreign` case accepts the drop message of either form.
- Pelorus now carries the `v0.3.0` rules itself. A future encoder or FFmpeg change to those limits needs a Pelorus change; the frozen-oracle and planted tests mark the place.
- The `consumer_stacks` job of the series replays this stack on the drop branch; the pin bump to the series release without 0003, 0004 and 0028 is a separate unit.

## References

- FFmpeg commit `96b7f8281a` (`nvenc_hevc_sei_size_check()`, `NVENC_HEVC_SEI_MAX_PAYLOAD`).
- Shared series `v0.3.0` patch `0027-avcodec-nvenc-move-the-SEI-size-rules-into-nvenc_sei.patch`.
- Fleet order `pelorus-sei-fit-merged-form-20261025`, 2026-10-25 (paraphrased): move patches 0022 and 0023 and the fit header onto the merged form, keep Pelorus's own budget, NAL size, emulation-prevention count and truncation rule, and prove it with the series consumer replay and the planted tests.
