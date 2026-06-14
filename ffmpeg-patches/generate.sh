#!/usr/bin/env bash
#
# generate.sh — regenerate the Pelorus FFmpeg patch stack from files/.
#
# Copyright 2026 Lusoris. BSD-2-Clause-Patent for the patches authored here;
# the linked FFmpeg binary's distribution is governed by FFmpeg's LGPL/GPL.
#
# Mirrors vmafx's /ffmpeg-build-patches workflow: check out a pristine FFmpeg
# base in an isolated git worktree, drop in the canonical filter sources from
# files/, wire each filter's registration hunks as its own commit, and
# `git format-patch` the whole range. The cumulative stack (0001 deband, then
# 0002 analyze on top) is the artifact; files/ is the source of truth.
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

# One commit per filter, in stack order, so format-patch yields 0001, 0002, ...
# Each filter's source is dropped in *inside* its own iteration so the commit's
# `git add -A` captures only that filter's file (not later filters' sources).
for filter in deband analyze; do
    cp "$FILES_DIR/vf_pelorus_${filter}_vulkan.c" "$WORKTREE/libavfilter/"
    python3 - "$WORKTREE" "$filter" <<'PY'
import sys, pathlib
root, which = pathlib.Path(sys.argv[1]), sys.argv[2]

def ins_before(path, anchor, line):
    p = root / path; t = p.read_text()
    assert anchor in t, f"anchor missing in {path}: {anchor!r}"
    assert line.strip() not in t, f"already present in {path}: {line!r}"
    p.write_text(t.replace(anchor, line + anchor, 1))

def ins_after(path, anchor, line):
    p = root / path; t = p.read_text()
    assert anchor in t, f"anchor missing in {path}: {anchor!r}"
    assert line.strip() not in t, f"already present in {path}: {line!r}"
    p.write_text(t.replace(anchor, anchor + line, 1))

# libpelorus is wired the idiomatic FFmpeg way (cf. libvmaf_cuda): a soft
# check_pkg_config probe sets the `libpelorus` config item, and each filter
# lists `libpelorus` in its _deps so it auto-enables iff libpelorus is found.
LVMAF_CUDA = ('enabled libvmaf           && check_pkg_config libvmaf_cuda '
              '"libvmaf >= 2.0.0" libvmaf_cuda.h vmaf_cuda_state_init\n')
CHECK = ('check_pkg_config libpelorus "libpelorus >= 0.1.0" '
         'pelorus/interop.h pel_blob_pack\n')

if which == 'deband':
    ins_before("libavfilter/allfilters.c",
               "extern const FFFilter ff_vf_perms;\n",
               "extern const FFFilter ff_vf_pelorus_deband_vulkan;\n")
    ins_before("libavfilter/Makefile",
               "OBJS-$(CONFIG_PERMS_FILTER)                  += f_perms.o\n",
               "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n")
    ins_after("configure",
              'overlay_vulkan_filter_deps="vulkan spirv_library"\n',
              'pelorus_deband_vulkan_filter_deps="vulkan spirv_library libpelorus"\n')
    ins_after("configure", LVMAF_CUDA, CHECK)
elif which == 'analyze':
    # analyze sorts before deband alphabetically; insert ahead of it.
    ins_before("libavfilter/allfilters.c",
               "extern const FFFilter ff_vf_pelorus_deband_vulkan;\n",
               "extern const FFFilter ff_vf_pelorus_analyze_vulkan;\n")
    ins_before("libavfilter/Makefile",
               "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n",
               "OBJS-$(CONFIG_PELORUS_ANALYZE_VULKAN_FILTER) += vf_pelorus_analyze_vulkan.o vulkan.o vulkan_filter.o\n")
    ins_before("configure",
               'pelorus_deband_vulkan_filter_deps="vulkan spirv_library libpelorus"\n',
               'pelorus_analyze_vulkan_filter_deps="vulkan spirv_library libpelorus"\n')
    # libpelorus check_pkg_config is already added by the deband patch (0001).
else:
    sys.exit("unknown filter: " + which)
print(f"registration applied: {which}")
PY
    git -C "$WORKTREE" add -A
    git -C "$WORKTREE" \
        -c user.name=Lusoris -c user.email=lusoris@pm.me \
        commit -q -F "$HERE/.commit-msg-${filter}.txt"
done

# Clean stale patches, regenerate the whole range.
rm -f "$HERE"/0*.patch
git -C "$WORKTREE" format-patch --zero-commit --start-number=1 \
    -o "$HERE" "$BASE_TAG..HEAD"

# Normalize auto-generated filenames to the series.txt names.
mv "$HERE"/0001-*.patch "$HERE/0001-add-vf_pelorus_deband_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0002-*.patch "$HERE/0002-add-vf_pelorus_analyze_vulkan.patch" 2>/dev/null || true

git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE"
echo "patch(es) regenerated in $HERE:"
ls "$HERE"/0*.patch
