<!-- markdownlint-disable MD013 MD060 -->
# ADR-0213: Windows replay of FFmpeg patch stack and tester zip artifact

- **Status**: Accepted
- **Implementation**: landed (#168)
- **Date**: 2026-10-18
- **Deciders**: Lusoris
- **Tags**: windows, msys2, ucrt64, tester, ffmpeg, ci
- **Related**: [ADR-0149](0149-windows-utf8-paths.md), [ADR-0173](0173-tester-programme.md), [ADR-0185](0185-shared-ffmpeg-fix-series.md), [ADR-0189](0189-named-nvenc-smoke-skip.md)

## Context

Issue #168 asks for Windows replay of the Pelorus FFmpeg patch stack in CI and packaging of a standalone Windows x64 tester zip artifact.
Previous Linux CI lanes replay the patch stack on Ubuntu runners (`ci.yml`), but Windows coverage was limited to the fast unit test suite without building FFmpeg or packaging runtime bundles.
External testers and issue reporters running Windows require an easy way to evaluate Pelorus filters and the bitstream filter without needing to maintain an MSYS2 development toolchain or compile FFmpeg from source.

Replaying and packaging FFmpeg on Windows introduces specific operational constraints:

1. **Hosted runner budget**: Windows runner minutes are more expensive and limited than Linux minutes (T16). Replaying the FFmpeg build on every commit would burn runner minutes unnecessarily.
2. **Toolchain ecosystem**: FFmpeg and Vulkan compute shaders compile naturally with GCC and MinGW-w64 under MSYS2 UCRT64, aligning with our existing Windows test matrix.
3. **Runtime dependencies**: Windows binaries dynamically link runtime DLLs. An unpackaged binary will fail on machines without MSYS2 installed unless required MinGW runtime DLLs are bundled.
4. **Vulkan loader boundary**: The Vulkan loader (`vulkan-1.dll`) must come from the system or GPU vendor's graphics driver. Shipping a bundled Vulkan loader can shadow system drivers or cause ICD discovery failures.
5. **Security and release governance**: A tester zip built in CI for testing must not be confused with an official release. Official releases require signing, SBOM generation, SLSA provenance attestations, and explicit operator authorization.

## Decision

1. **MSYS2 UCRT64 toolchain**: We build and replay the FFmpeg patch stack using GCC and Meson in the MSYS2 UCRT64 environment on GitHub Actions `windows-2025` runners. MSVC is not used.
2. **Dedicated path-filtered workflow**: Replay and zip packaging run in `.github/workflows/windows-tester.yml`. The workflow triggers on pull requests and pushes to `master` only when changes touch relevant inputs (`ffmpeg-patches/**`, `libpelorus/**`, `meson.build`, `meson_options.txt`, `build-config.env`, `scripts/fetch-ffmpeg-series.sh`, `scripts/windows/**`, or the workflow file itself).
3. **Hardware smoke skips**: Hardware-dependent smoke tests in `ffmpeg-patches/test/build-and-run.sh` use the existing named-skips harness ([ADR-0189](0189-named-nvenc-smoke-skip.md)) with explicit environment variables (`NVENC_SMOKE_SKIP`, `NVENC_HOP_SMOKE_SKIP`, `KEYFRAME_SMOKE_SKIP`, `FGS_SMOKE_SKIP`, `GRAIN_SCORE_AV1_SMOKE_SKIP`).
4. **PE import verification and DLL allow-list**: `scripts/windows/check-pe-imports.py` inspects portable executable imports using `objdump -p` against `scripts/windows/tester-zip-dlls.txt`. It enforces that:
   - System DLLs belong to the reviewed `[system]` allow-list (case-insensitive).
   - Bundled MinGW runtime DLLs belong to the `[bundle]` list with declared package names and SPDX licenses.
   - `vulkan-1.dll` is never statically imported by the bundle binaries.
5. **No bundled Vulkan loader**: `vulkan-1.dll` is excluded from the zip. The `run.cmd` launcher verifies that `vulkan-1.dll` is discoverable via `where.exe /q` before invoking `ffmpeg.exe`, returning exit code 3 with a clear diagnostic if missing.
6. **Workflow artifact only**: The resulting archive `pelorus-tester-windows-x64-<sha8>.zip` is published exclusively as a GitHub Actions workflow artifact with 14-day retention. No git tags, releases, cosign signatures, or build attestations are minted.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| MSYS2 UCRT64 replay and zip packaging (chosen) | Native FFmpeg build; matches existing Windows test suite; straightforward DLL closure | Requires MSYS2 environment in CI | Chosen |
| MSVC toolchain build | Native Visual Studio output | Requires extensive patch adaptations for MSVC; FFmpeg Vulkan integration is designed around GCC/Clang | Rejected: high maintenance burden and divergence from Linux codebase |
| Ship `vulkan-1.dll` in the zip | Works if system has no driver | Breaks driver ICD dispatch; violates Vulkan loader architecture | Rejected: causes runtime conflicts with GPU vendor drivers |
| Integrate into `ci.yml` `windows` job | Single workflow file | Runs on every PR regardless of touched files; consumes excessive runner minutes | Rejected: costly on hosted runners |
| Mint release candidate assets from Windows replay | Immediate public download | Mixes unreviewed test bundles with release governance; bypasses operator release decision | Rejected: zip remains a workflow artifact only |

## Consequences

- **Positive**: Automated validation of the FFmpeg patch stack on Windows in CI; reproducible tester zip with verified dependencies and license manifests.
- **Negative**: Adds up to 150 minutes of runner execution time on qualifying pull requests that modify patch inputs.
- **Neutral**: Does not alter required checks in the branch ruleset; zip artifacts expire automatically after 14 days.

## References

- Issue [#168](https://github.com/VMAFx/pelorus/issues/168): Windows replay of FFmpeg patch stack in CI.
- [ADR-0149](0149-windows-utf8-paths.md): native Windows CI leg and UTF-8 paths.
- [ADR-0173](0173-tester-programme.md): Tester programme and packaging.
- [ADR-0185](0185-shared-ffmpeg-fix-series.md): Shared FFmpeg fix series.
- [ADR-0189](0189-named-nvenc-smoke-skip.md): Named skips for hardware-gated smokes.
