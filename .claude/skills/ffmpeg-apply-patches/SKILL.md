---
name: ffmpeg-apply-patches
description: Use when verifying FFmpeg integration, cumulative patch replay, final linking, or filter and BSF registration.
---

# Verify the FFmpeg patch stack

Run the supported gate from the repository root:

```sh
FFMPEG_REPO=/absolute/path/to/ffmpeg ffmpeg-patches/test/build-and-run.sh
```

Root `build-config.env` owns the qualified tag and peeled commit;
`FFMPEG_REPO` is required and `BASE_TAG` is unsupported. The gate creates a
hook-neutral run-owned worktree, privately builds/tests/installs this libpelorus
tree, applies `series.txt` with `git am --3way`, configures FFmpeg, links the
final binary, and verifies all Pelorus filters plus `pelorus_fgs` register.

For QSV ROI changes, also run:

```sh
FFMPEG_REPO=/absolute/path/to/ffmpeg \
  ffmpeg-patches/test/qsv-roi-regression.sh
```

## Completion boundary

- Per-patch `git apply --check`, object-only compilation, and filter help from an
  unlinked tree do not replace the full gate.
- A successful replay proves apply/build/link/registration, not Vulkan runtime
  correctness. Run the relevant on-device format and plane-mask matrix and
  report unavailable hardware rows as unexecuted.
- Do not hand-apply into a fixed `/tmp` path or a caller-owned worktree; the
  supported scripts own setup and cleanup.
