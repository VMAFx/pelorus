- Updated the supported FFmpeg patch baseline to immutable n9.0.2, moved CI to
  Ubuntu 26.04's native Vulkan toolchain, and made generation, replay, QSV, and
  release jobs consume the shared pin deterministically. A manual release
  dispatch now runs a non-publishing rehearsal while only a `v*` tag push can
  publish; replay compiles every available encoder consumer and proves static
  `libavfilter` pkg-config linkage from an external program. Patch replay also
  supplies its own committer identity and disables signing so clean CI runners
  do not depend on ambient Git configuration. The SVT-AV1 ROI consumer now
  uses an SDK-neutral boolean assignment, preserving compilation with Ubuntu
  26.04's SVT-AV1 2.3 headers as well as newer SDKs
  ([ADR-0144](docs/adr/0144-ffmpeg-pin-and-ci-runner-policy.md)).
