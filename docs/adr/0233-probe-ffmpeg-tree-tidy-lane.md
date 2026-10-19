<!-- markdownlint-disable MD013 MD041 MD060 -->
# ADR-0233: avoption-probe joins the FFmpeg-tree clang-tidy lane

- **Status**: Accepted
- **Date**: 2026-10-19
- **Deciders**: Lusoris
- **Tags**: `ffmpeg`, `static-analysis`, `build`, `ci`, `governance`
- **Related**: [ADR-0200](0200-ffmpeg-avoptions-in-encode-record.md), [ADR-0224](0224-ffmpeg-tree-tidy-lane.md)

## Context

[ADR-0200](0200-ffmpeg-avoptions-in-encode-record.md) introduced `scripts/encode-options/avoption-probe.c` to dump AVOptions from `AVCodecContext` and private encoder classes for table generation in `libpelorus/src/encode_avoptions_tables.c` ([#352](https://github.com/VMAFx/pelorus/issues/352)). The probe compiles against FFmpeg's `libavcodec` and `libavutil` headers. Because the Meson build does not depend on FFmpeg dev packages (the CI `core` job runs without FFmpeg packages installed), `avoption-probe.c` could not join the Meson-backed `libpelorus` `clang-tidy` lane (`.config/clang-tidy/lane-files.txt`), and was covered by a 90-day Category D `clang-tidy-coverage` exception in `.standards.yaml` expiring 2027-01-15.

[ADR-0224](0224-ffmpeg-tree-tidy-lane.md) declared the `ffmpeg-tree` `clang-tidy` lane (`.config/clang-tidy/ffmpeg-tree-files.txt`, `ffmpeg-patches/test/tidy-ffmpeg-tree.sh`, `.config/clang-tidy/ffmpeg-tree.yaml`), which runs during the FFmpeg patch-stack replay against the pinned static n9.0.2 FFmpeg prefix. Placing `avoption-probe.c` in this lane allows checking it against the exact pinned `libavcodec` headers rather than an arbitrary host FFmpeg or leaving it unanalyzed.

## Decision

1. Add `scripts/encode-options/avoption-probe.c` to the `ffmpeg-tree` `clang-tidy` lane in `.config/clang-tidy/ffmpeg-tree-files.txt` and retire its `clang-tidy-coverage` exception in `.standards.yaml`.
2. In `ffmpeg-patches/test/tidy-ffmpeg-tree.sh`, add a dedicated compile database generation branch for `scripts/encode-options/avoption-probe.c`: command = `${CC:-cc}` + `static_cflags` (which already carries include paths from `pkg-config --static --cflags libavfilter libpelorus`) + `-c scripts/encode-options/avoption-probe.c -o <output_root>/avoption-probe.o -Wno-unknown-warning-option`, with working directory `source_root`.
3. Check the probe against the pinned static n9.0.2 prefix during replay using `.config/clang-tidy/ffmpeg-tree.yaml`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Put `avoption-probe.c` into `ffmpeg-tree` lane in replay (chosen) | Checks probe against pinned FFmpeg n9.0.2 headers without host dependencies in `core` job; retires exception | Probe is linted only where the replay runs (Linux `ffmpeg-stack` job), not in `make tidy` | **Chosen** |
| Optional Meson target behind `dependency('libavcodec')` | Probe built and linted during `ninja -C build` when host has FFmpeg dev packages | CI core job has no FFmpeg dev package; host FFmpeg differs from pin n9.0.2 | Rejected: CI core job has no FFmpeg dev package, host FFmpeg differs from pin n9.0.2 |
| Keep `clang-tidy-coverage` exception in `.standards.yaml` | No replay script or lane changes | Exception expires 2027-01-15; leaves probe unanalyzed | Rejected: expires 2027-01-15, never checked |

## Consequences

- **Positive**: All 16 FFmpeg-tree translation units are checked by `clang-tidy` against the pinned FFmpeg n9.0.2 prefix during replay; the `clang-tidy-coverage` exception for `avoption-probe.c` is retired from `.standards.yaml`.
- **Negative**: The probe is linted only in the replay, so a local `make tidy` does not cover it; the replay skips the lane on Windows MSYS2 like the other FFmpeg-tree units.
- **Verification**: a local replay lints all 16 units clean; with a planted `if (x = 1)` in the probe, the lane fails at the probe with `bugprone-assignment-in-selection-statement`.
- **Neutral / follow-ups**: The probe remains an out-of-tree tool run during table regeneration, but its source is now continuously verified in CI against pinned FFmpeg headers.

## References

- Issues: [#352](https://github.com/VMAFx/pelorus/issues/352), [#94](https://github.com/VMAFx/pelorus/issues/94).
- Architecture decision records: [ADR-0200](0200-ffmpeg-avoptions-in-encode-record.md), [ADR-0224](0224-ffmpeg-tree-tidy-lane.md).
- Scripts and configs: `.config/clang-tidy/ffmpeg-tree-files.txt`, `ffmpeg-patches/test/tidy-ffmpeg-tree.sh`, `.standards.yaml`.
