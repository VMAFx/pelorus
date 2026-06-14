#!/usr/bin/env bash
#
# generate.sh — regenerate the Pelorus FFmpeg patch stack from files/.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent for the patches authored here;
# the linked FFmpeg binary's distribution is governed by FFmpeg's LGPL/GPL.
#
# Mirrors vmafx's /ffmpeg-build-patches workflow: check out a pristine FFmpeg
# base in an isolated git worktree, drop in the canonical filter sources from
# files/, wire the registration hunks, commit, and `git format-patch`. The
# committed patches in this directory are the artifact; files/ is the source of
# truth a maintainer edits.
#
# Usage:
#   FFMPEG_REPO=/path/to/ffmpeg BASE_TAG=n8.1.1 ./generate.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FFMPEG_REPO="${FFMPEG_REPO:-/home/kilian/dev/ffmpeg-8}"
BASE_TAG="${BASE_TAG:-n8.1.1}"
WORKTREE="${WORKTREE:-/tmp/pelorus-ffmpeg-gen}"
FILES_DIR="$HERE/files"

git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE" 2>/dev/null || true
git -C "$FFMPEG_REPO" worktree add --detach "$WORKTREE" "$BASE_TAG"

cp "$FILES_DIR/vf_pelorus_deband_vulkan.c" "$WORKTREE/libavfilter/"

python3 - "$WORKTREE" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])

def insert_before(path, anchor, line):
    p = root / path
    text = p.read_text()
    assert anchor in text, f"anchor not found in {path}: {anchor!r}"
    assert line.strip() not in text, f"already present in {path}"
    p.write_text(text.replace(anchor, line + anchor, 1))

def insert_after(path, anchor, line):
    p = root / path
    text = p.read_text()
    assert anchor in text, f"anchor not found in {path}: {anchor!r}"
    assert line.strip() not in text, f"already present in {path}"
    p.write_text(text.replace(anchor, anchor + line, 1))

# libavfilter/allfilters.c — alphabetical: pelorus < perms
insert_before("libavfilter/allfilters.c",
              "extern const FFFilter ff_vf_perms;\n",
              "extern const FFFilter ff_vf_pelorus_deband_vulkan;\n")

# libavfilter/Makefile — pelorus < perms
insert_before("libavfilter/Makefile",
              "OBJS-$(CONFIG_PERMS_FILTER)                  += f_perms.o\n",
              "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n")

# configure — filter deps (after the alphabetically-prior overlay_vulkan)
insert_after("configure",
             'overlay_vulkan_filter_deps="vulkan spirv_library"\n',
             'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n')

# configure — pkg-config requirement, beside libvmaf's
insert_after("configure",
             'enabled libvmaf           && check_pkg_config libvmaf_cuda "libvmaf >= 2.0.0" libvmaf_cuda.h vmaf_cuda_state_init\n',
             'enabled pelorus_deband_vulkan_filter && require_pkg_config libpelorus "libpelorus >= 0.1.0" pelorus/interop.h pel_blob_pack\n')
print("registration hunks applied")
PY

git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-0001.txt"

git -C "$WORKTREE" format-patch -1 --zero-commit \
    --start-number=1 -o "$HERE" "$BASE_TAG..HEAD"

git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE"
echo "patch(es) regenerated in $HERE"
