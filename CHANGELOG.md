<!-- markdownlint-disable MD013 MD024 -->
# Changelog

All notable changes to Pelorus are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[SemVer](https://semver.org/). The `[Unreleased]` block is **rendered** from
`changelog.d/` fragments by `scripts/release/concat-changelog-fragments.sh` —
do not edit it by hand (see [changelog.d/README.md](changelog.d/README.md)).

## [Unreleased]

### Added

- **`libpelorus`** (`libpelorus/`): the shared core library — the Pelorus⇄vmafx
  side-data interop ABI (`pelorus/interop.h`, `pelorus/interop.c`) and the
  smart-deband parameter contract (`pelorus/deband.h`). The `PelorusSideData`
  blob is a versioned, UUID-keyed, append-only AVFrame side-data format both
  `vf_pelorus_*` and vmafx's `vf_libvmaf*` read/write. Ships a shared
  conformance fixture (`libpelorus/test/interop_test.c`). ADR-0103, ADR-0105.
- **`vf_pelorus_deband_vulkan`**: the flagship Vulkan compute smart-deband
  filter — f3kdb-style flat-test + TPDF/blue-noise grain + a local-variance
  detail-protection mask, zero-copy in VRAM. Shipped as
  `ffmpeg-patches/0001-add-vf_pelorus_deband_vulkan.patch` against n8.1.1.
  ADR-0102, ADR-0104.
- **Reference shader** (`libpelorus/shaders/pelorus_deband.comp`): standalone
  f3kdb deband, CI-compiled to SPIR-V.
- **Project scaffold**: principles, ADRs (0001/0100/0102/0103/0104/0105/0106/0108),
  per-surface docs, the FFmpeg patch tooling (`generate.sh`,
  `test/build-and-run.sh`), and CI-style local gate.

[Unreleased]: https://github.com/vmafx/pelorus/commits/master
