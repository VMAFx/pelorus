---
name: ffmpeg-apply-patches
description: Use when verifying or building the FFmpeg integration — apply the patch stack onto a pristine n8.1.1 via git am --3way, configure, and compile the vf_pelorus_* filter objects. The patch-stack gate.
---

# /ffmpeg-apply-patches

The end-to-end gate for the FFmpeg side. Needs libpelorus installed (pkg-config)
plus a Vulkan loader + SPIR-V compiler (libshaderc/libglslang).

## Full gate (apply + build + smoke)

```sh
ffmpeg-patches/test/build-and-run.sh
```

This worktrees a pristine n8.1.1, `git am --3way`s every patch in `series.txt`,
configures `--enable-vulkan`, builds the filter objects, and smoke-tests
`-h filter=pelorus_*`.

## Quick apply-only check (no build deps)

```sh
git -C /home/kilian/dev/ffmpeg-8 worktree add --detach /tmp/ff n8.1.1
while read -r p; do case "$p" in ''|\#*) continue;; esac
  git -C /tmp/ff am --3way "$PWD/ffmpeg-patches/$p"; done < ffmpeg-patches/series.txt
git -C /home/kilian/dev/ffmpeg-8 worktree remove --force /tmp/ff
```

## Rules

- Verify with a **full series replay** (`git am --3way` of the whole stack), NOT
  per-patch `git apply --check` — patches are cumulative (ADR-0104).
- If a patch fails to apply after an upstream bump, regenerate with
  `/ffmpeg-build-patches` against the new base tag rather than hand-resolving.
