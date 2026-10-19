<!-- markdownlint-disable MD013 MD041 -->
# ADR-0224: FFmpeg-tree clang-tidy lane in patch stack replay

- **Status**: Accepted
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: `ffmpeg`, `build`, `ci`, `governance`, `static-analysis`

## Context

[ADR-0168](0168-praetor-engine-492a00f.md) declared the `libpelorus` `clang-tidy` lane (`.config/clang-tidy/lane-files.txt`) for Meson-built translation units and placed the FFmpeg-tree C units under `ffmpeg-patches/files/` and `ffmpeg-patches/test/` on dated `clang-tidy-coverage` exceptions in `.standards.yaml` ([#94](https://github.com/VMAFx/pelorus/issues/94)). Those 14 units (`h265_pelorus_fgs_bsf.c`, nine `vf_pelorus_*_vulkan.c` filters, `vf_pelorus_scenecut.c`, `test/grain_conformance.c`, `test/qsv-roi-regression.c`, and `test/static-libavfilter-consumer.c`) include internal FFmpeg headers (`libavfilter/vulkan_filter.h`, `libavcodec/bsf_internal.h`, `libavcodec/qsvenc.c`, generated `config_components.h`, and build-time SPIR-V C arrays) that exist only after FFmpeg is configured and built in the patch-stack replay (`ffmpeg-patches/test/build-and-run.sh`).

At the same time, FFmpeg filter and bitstream-filter translation units follow upstream FFmpeg conventions (`av_mallocz`/`av_calloc`/`av_free` wrappers, single-label `goto fail` cleanup, `FFERROR_*`/`AVERROR` macros, `AVOption` `OFFSET` macros, and FFmpeg callback signatures) that trigger style and raw-memory diagnostics under the root `libpelorus` `.clang-tidy` profile, while real bugprone and CERT defects (implicit multiplication widening, unchecked narrowing conversions, unused callback parameters, unchecked `snprintf`/`fprintf` returns) still need to fail closed without loosening root `.clang-tidy`.

## Decision

We will declare a second `clang-tidy` lane, `ffmpeg-tree`, backed by `.config/clang-tidy/ffmpeg-tree-files.txt`, run it inside `ffmpeg-patches/test/build-and-run.sh` via `ffmpeg-patches/test/tidy-ffmpeg-tree.sh` with `--config-file=.config/clang-tidy/ffmpeg-tree.yaml`, and remove the 14 FFmpeg-tree `clang-tidy-coverage` exceptions from `.standards.yaml`. For each `ffmpeg-patches/files/` unit, `tidy-ffmpeg-tree.sh` extracts the exact compiler invocation from `make -n -B V=1 <obj>` in the configured FFmpeg worktree; for each `ffmpeg-patches/test/` unit, it synthesizes the compile command from `${CC:-cc}`, `pkg-config --static --cflags libavfilter libpelorus`, `pkg-config --cflags vpl`, and the FFmpeg tree include paths. It writes `compile_commands.json` into the replay log directory, verifies that every listed unit has a non-empty compile command (failing closed on any missing unit or finding), and runs `clang-tidy` with `.config/clang-tidy/ffmpeg-tree.yaml`, where every disabled FFmpeg-idiom check carries an inline rationale comment. In `build-and-run.sh`, the step runs after FFmpeg build and static consumer verification and honors `FFMPEG_TIDY_SKIP` via `named-skip.sh` ([ADR-0189](0189-named-nvenc-smoke-skip.md)), defaulting to a named skip on Windows MSYS2 UCRT64 replay.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Extract compile commands from `make -n -B` and `pkg-config` in `tidy-ffmpeg-tree.sh` with `.config/clang-tidy/ffmpeg-tree.yaml` | Zero new dependencies; covers both `files/` and out-of-tree `test/` units; keeps root `.clang-tidy` untouched | Requires mapping `files/` units to their FFmpeg object targets | **Chosen** |
| Intercept the FFmpeg `make` build with `bear` | Automatic capture for `make`-built objects | Adds a new external dependency requiring an ADR; misses standalone `ffmpeg-patches/test/*.c` units not built by FFmpeg's `Makefile` | Unnecessary third-party dependency and incomplete `test/` coverage |
| Loosen root `.clang-tidy` or scatter `// NOLINT` across `ffmpeg-patches/` | Single `.clang-tidy` configuration file | Weakens static analysis for `libpelorus/` or clutters shipped FFmpeg patches with suppression comments | Violates hard rule 6 |
| Renew the 90-day `clang-tidy-coverage` exceptions in `.standards.yaml` | No replay script changes | Leaves 14 FFmpeg-tree units unanalyzed and requires recurring exception maintenance | Fails to catch real narrowing, widening, and CERT defects |

## Consequences

- **Positive**: All 14 FFmpeg-tree C translation units are checked by `clang-tidy` in the `ffmpeg-stack` replay and covered by the `ffmpeg-tree` lane in `praetorctl audit`, eliminating 14 `clang-tidy-coverage` exceptions from `.standards.yaml`. Real findings in `vf_pelorus_analyze_vulkan.c`, `vf_pelorus_deband_vulkan.c`, `vf_pelorus_denoise_vulkan.c`, `vf_pelorus_grain_estimate_vulkan.c`, `vf_pelorus_mc_vulkan.c`, and `qsv-roi-regression.c` are fixed at the source.
- **Negative**: Replay runs that execute the lane add ~15 seconds of `clang-tidy` analysis across the 14 units; CI replay lanes without the oneVPL development package (`lavapipe`, `arm64`) name the oneVPL skip via `FFMPEG_TIDY_SKIP`, and Windows MSYS2 UCRT64 skips by name. The script checks oneVPL before `clang-tidy`, so a job without oneVPL prints the same reason whether or not its runner image ships `clang-tidy`.
- **Neutral / follow-ups**: `.config/clang-tidy/ffmpeg-tree-files.txt` includes a commented hook for `ffmpeg-patches/test/vulkan-export-contract.c` so the companion PR can enable it in the lane when that unit lands.

## References

- [ADR-0168](0168-praetor-engine-492a00f.md), [ADR-0189](0189-named-nvenc-smoke-skip.md), and [#94](https://github.com/VMAFx/pelorus/issues/94).
- `.config/clang-tidy/ffmpeg-tree-files.txt`, `.config/clang-tidy/ffmpeg-tree.yaml`, and `ffmpeg-patches/test/tidy-ffmpeg-tree.sh`.
- Source: issue [#94](https://github.com/VMAFx/pelorus/issues/94).
