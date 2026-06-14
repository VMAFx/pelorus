#!/usr/bin/env bash
#
# build-and-run.sh — apply the Pelorus patch stack to a pristine FFmpeg n8.1.1,
# build, and smoke-test the filters. The CI gate for ffmpeg-patches/.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent.
#
# Requires: a FFmpeg git checkout, libpelorus installed (pkg-config), a Vulkan
# loader + headers, and a SPIR-V compiler (libshaderc or libglslang).
#
# Env:
#   FFMPEG_REPO  path to a FFmpeg git checkout      (default /home/kilian/dev/ffmpeg-8)
#   BASE_TAG     base tag to apply onto             (default n8.1.1)
#   WORKTREE     scratch worktree path              (default /tmp/pelorus-ffmpeg-test)
#   JOBS         make -j value                      (default nproc)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PATCHDIR="$(dirname "$HERE")"
FFMPEG_REPO="${FFMPEG_REPO:-/home/kilian/dev/ffmpeg-8}"
BASE_TAG="${BASE_TAG:-n8.1.1}"
WORKTREE="${WORKTREE:-/tmp/pelorus-ffmpeg-test}"
JOBS="${JOBS:-$(nproc)}"

echo "== libpelorus pkg-config =="
pkg-config --exists libpelorus || {
    echo "ERROR: libpelorus not found by pkg-config. Build+install it first:"
    echo "  meson setup build && ninja -C build && ninja -C build install"
    exit 1
}
pkg-config --modversion libpelorus

echo "== applying patch stack onto $BASE_TAG =="
git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE" 2>/dev/null || true
git -C "$FFMPEG_REPO" worktree add --detach "$WORKTREE" "$BASE_TAG"
while read -r p; do
    case "$p" in ''|\#*) continue;; esac
    echo "  am: $p"
    git -C "$WORKTREE" am --3way "$PATCHDIR/$p"
done < "$PATCHDIR/series.txt"

echo "== configure =="
( cd "$WORKTREE" && ./configure \
    --enable-vulkan --enable-libshaderc \
    --disable-doc --disable-programs --enable-ffmpeg \
    >/tmp/pelorus-ff-configure.log 2>&1 ) || {
    echo "configure failed; tail:"; tail -30 /tmp/pelorus-ff-configure.log; exit 1; }

echo "== single-TU compile (filter only) =="
make -C "$WORKTREE" -j"$JOBS" libavfilter/vf_pelorus_deband_vulkan.o

echo "== full build =="
make -C "$WORKTREE" -j"$JOBS"

echo "== smoke test: filter is registered =="
"$WORKTREE/ffmpeg" -hide_banner -filters 2>/dev/null | grep pelorus_deband_vulkan
"$WORKTREE/ffmpeg" -hide_banner -h filter=pelorus_deband_vulkan 2>/dev/null | head -40

git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE"
echo "OK"
