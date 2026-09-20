- Updated the supported FFmpeg patch baseline to immutable n9.0.2, moved CI to
  Ubuntu 26.04's native Vulkan toolchain, and made generation, replay, QSV, and
  release jobs consume the shared pin deterministically; the replay now compiles
  every available encoder consumer and proves static `libavfilter` pkg-config
  linkage from an external program ([ADR-0144](docs/adr/0144-ffmpeg-pin-and-ci-runner-policy.md)).
