<!-- markdownlint-disable MD013 MD060 -->
# ADR-0203: Native arm64 CI lane for fast suite, patch replay, and lavapipe functional validation

- **Status**: Accepted
- **Implementation**: landed (#170)
- **Date**: 2026-10-17
- **Deciders**: Lusoris
- **Tags**: ci, arm64, aarch64, lavapipe, interop-abi, tester
- **Related**: [ADR-0149](0149-windows-utf8-paths.md), [ADR-0173](0173-tester-programme.md), [ADR-0180](0180-tester-vendor-images.md), [ADR-0183](0183-sidedata-zero-free-carrier.md), [ADR-0185](0185-shared-ffmpeg-fix-series.md)

## Context

Pelorus targets cross-architecture deployments, with tester image and runtime contracts spanning x86-64 and arm64 (`aarch64`) environments (issue #170, [ADR-0173](0173-tester-programme.md)). While initial CI coverage ran on x86-64 Linux and Windows runners, arm64 verification was previously unexecuted in hosted CI.

GitHub-hosted `ubuntu-26.04-arm` runners became generally available on 2026-09-17, providing native arm64 execution without emulation overhead or cross-compilation drift.

Key architectural and validation invariants require explicit arm64 proof:

1. **Interop ABI and side-data serialization**: `libpelorus/test/interop_test.c` contains known-answer test byte vectors (`kat_blob`, `kat_carrier`) produced on x86-64. Executing the libpelorus fast suite on native aarch64 proves little-endian wire integrity, alignment, and round-trip fidelity between architectures ([ADR-0183](0183-sidedata-zero-free-carrier.md), [interop ABI](../api/interop-abi.md#stability-rules-normative--see-interoph)).
2. **Patch stack replay and shader compilation**: The FFmpeg patch stack (22 patches applied on top of shared series v0.2.0, [ADR-0185](0185-shared-ffmpeg-fix-series.md)) must replay cleanly, build with native toolchains on aarch64 without x86 assemblers (`nasm`/`yasm`), link `libpelorus`, and register all ten Vulkan filters and the `pelorus_fgs` bitstream filter.
3. **Mesa lavapipe functional validation**: Software Vulkan execution on lavapipe provides functional evidence with the Khronos validation layer enabled across all ten filters and the format matrix. Pelorus contains no architecture-specific NEON SIMD kernels, and hosted arm64 runners provide no GPU hardware; hence the lane produces functional evidence only, never hardware GPU claims ([ADR-0173](0173-tester-programme.md)).
4. **Missing-device non-failure contract**: When no Vulkan device is visible, the tester report program (`tools/tester/pelorus_tester_report.py`) must report `no_device` for GPU stages and exit 0 (verdict `pass`), matching the documented contract.

## Decision

1. **Dedicated CI job `arm64`**: Add a new job named `arm64 (native · fast suite · patch replay · lavapipe)` to `.github/workflows/ci.yml` running on `ubuntu-26.04-arm` with a 45-minute timeout.
2. **Toolchain installation**: Install the union of `core` and `lavapipe` build dependencies available on arm64, omitting x86-only assemblers (`nasm`, `yasm`). Configure and build steps detect aarch64 natively.
3. **Runner architecture verification**: The receipt step checks `arch=$(uname -m)` and fails immediately unless `arch` equals `aarch64`.
4. **Fast suite execution**: Build `libpelorus` natively (`meson setup --werror build && ninja -C build`) and run `meson test --suite=fast` followed by `scripts/check-test-skips.py`.
5. **Patch stack replay and lavapipe validation**: Fetch and verify the pinned FFmpeg release and shared fix series via `scripts/fetch-ffmpeg-series.sh`, replay all patches via `ffmpeg-patches/test/build-and-run.sh`, select the lavapipe ICD, run the device guard self-tests, verify all ten filters under Khronos validation, execute the format matrix, and produce the functional software Vulkan tester report.
6. **Negative missing-device verification**: Run `tools/tester/pelorus_tester_report.py run` with `VK_DRIVER_FILES=/nonexistent.json` and assert that all GPU stages report `no_device` with exit code 0.
7. **Endianness scope**: Explicitly document that big-endian architectures are unsupported. Pelorus interop ABI wire format is strictly little-endian (ADR-0183; interop ABI rule R5).
8. **Committed ruleset lists the check**: The new job runs on every push and pull request, and its context is added to the committed `.github/rulesets/main.json` (10 checks), as the lavapipe job did in #269; the engine audit refuses a CI context the committed ruleset lacks. The live ruleset changes only when the maintainer runs `praetorctl sync --remote`.

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
| --- | --- | --- | --- |
| Dedicated native `arm64` job on `ubuntu-26.04-arm` (chosen) | Fast, native execution; no emulation anomalies; preserves existing required check names in `.github/rulesets/main.json` | Additional CI runner minutes | Chosen |
| Matrix on existing `lavapipe` job (`runs-on: [ubuntu-26.04, ubuntu-26.04-arm]`) | Reuses job definition without duplication | Renames the GitHub Actions check context (e.g. `lavapipe (ubuntu-26.04)`), breaking required checks in `main.json` and blocking PR merges | Rejected: breaks ruleset check names |
| QEMU user or system emulation on x86 runner | Runs on standard x86 runner pool | Emulated compile of FFmpeg is much slower than native (not measured here) and risks the job timeout; emulation quirks may mask real architectural issues | Rejected: too slow and fragile |
| Leave `arm64` out of the committed `.github/rulesets/main.json` | Live and committed rulesets stay identical until sync | The engine audit fails: the committed ruleset must list every hosted CI context | Rejected: the audit gate refuses it |

## Consequences

- **Positive**: Proves libpelorus and FFmpeg patch stack compatibility on aarch64; validates little-endian wire serialization against x86-64 KAT byte vectors; confirms Mesa lavapipe functional behavior on native arm64 Linux.
- **Negative**: Consumes GitHub-hosted arm64 runner quota.
- **Neutral**: Does not provide GPU performance or hardware encoder evidence (no physical GPU present on hosted runner). The live branch ruleset requires the new check only after maintainer sync.

## References

- Issue [#170](https://github.com/VMAFx/pelorus/issues/170): tester: arm64 image.
- GitHub Changelog (2026-09-17): GitHub-hosted arm64 runners (`ubuntu-26.04-arm`) general availability.
- [ADR-0173](0173-tester-programme.md): tester programme and `no_device` non-failure contract.
- [ADR-0180](0180-tester-vendor-images.md): tester vendor images.
- [ADR-0183](0183-sidedata-zero-free-carrier.md): interop ABI 1.5 little-endian carrier specification.
- [ADR-0185](0185-shared-ffmpeg-fix-series.md): shared FFmpeg fix series verification.
