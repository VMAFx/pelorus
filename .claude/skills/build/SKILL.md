---
name: build
description: Use when building or testing libpelorus — configure with Meson, compile with Ninja, run the fast test suite (interop ABI + shader compile). The local gate before any push.
---

# /build

The libpelorus build + test loop. Run this as the local gate before pushing.

## Steps

```sh
meson setup build              # first time (re-run after meson.build changes)
ninja -C build                 # compile lib + interop test; compile shaders to SPIR-V
meson test -C build --suite=fast --print-errorlogs
```

`--suite=fast` is the pre-push gate: the interop ABI conformance fixture
(`libpelorus/test/interop_test.c`) plus a glslang compile of every reference
shader (`libpelorus/shaders/*.comp`).

To make the FFmpeg filters available, install the library so pkg-config sees it:

```sh
ninja -C build install         # or: meson setup build --prefix=/tmp/p && ninja -C build install
```

## Notes

- A red static-assert in `interop.h` means the ABI struct sizes changed — that's
  an ABI change, see `/bump-abi`, never "fix" the assert.
- The full FFmpeg filter build needs a SPIR-V toolchain + the patch stack; use
  `/ffmpeg-apply-patches` (or CI's `ffmpeg-stack` job).
- Don't `git clean -xf` the build trees blindly (the bash guard blocks it).
