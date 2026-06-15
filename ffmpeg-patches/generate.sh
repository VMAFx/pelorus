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
for filter in deband analyze denoise; do
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

# libpelorus wiring mirrors vmafx's vf_vmaf_pre: the filter depends only on
# FFmpeg-KNOWN components (vulkan spirv_library) — `libpelorus` is NOT a known
# config name, so it must NOT appear in _deps (an unknown dep silently disables
# the filter). The extra lib is gated by `enabled <filter> && require_pkg_config
# libpelorus ...`, which adds its cflags/libs and hard-errors if it is missing.
LVMAF_CUDA = ('enabled libvmaf           && check_pkg_config libvmaf_cuda '
              '"libvmaf >= 2.0.0" libvmaf_cuda.h vmaf_cuda_state_init\n')
# require_pkg_config adds the header cflags but NOT the link libs, so append
# add_extralibs to actually link -lpelorus (otherwise ffmpeg fails at LINK time
# with undefined references, which a compile-only check never catches).
REQ = ('enabled %s_filter && require_pkg_config libpelorus '
       '"libpelorus >= 0.1.0" pelorus/interop.h pel_blob_pack '
       '&& add_extralibs $libpelorus_extralibs\n')

if which == 'deband':
    ins_before("libavfilter/allfilters.c",
               "extern const FFFilter ff_vf_perms;\n",
               "extern const FFFilter ff_vf_pelorus_deband_vulkan;\n")
    ins_before("libavfilter/Makefile",
               "OBJS-$(CONFIG_PERMS_FILTER)                  += f_perms.o\n",
               "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n")
    ins_after("configure",
              'overlay_vulkan_filter_deps="vulkan spirv_library"\n',
              'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n')
    ins_after("configure", LVMAF_CUDA, REQ % "pelorus_deband_vulkan")
elif which == 'analyze':
    # analyze sorts before deband alphabetically; insert ahead of it.
    ins_before("libavfilter/allfilters.c",
               "extern const FFFilter ff_vf_pelorus_deband_vulkan;\n",
               "extern const FFFilter ff_vf_pelorus_analyze_vulkan;\n")
    ins_before("libavfilter/Makefile",
               "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n",
               "OBJS-$(CONFIG_PELORUS_ANALYZE_VULKAN_FILTER) += vf_pelorus_analyze_vulkan.o vulkan.o vulkan_filter.o\n")
    ins_before("configure",
               'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n',
               'pelorus_analyze_vulkan_filter_deps="vulkan spirv_library"\n')
    ins_after("configure", REQ % "pelorus_deband_vulkan", REQ % "pelorus_analyze_vulkan")
elif which == 'denoise':
    # denoise sorts after deband alphabetically (deban < denoi); insert after it.
    ins_before("libavfilter/allfilters.c",
               "extern const FFFilter ff_vf_perms;\n",
               "extern const FFFilter ff_vf_pelorus_denoise_vulkan;\n")
    ins_after("libavfilter/Makefile",
              "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n",
              "OBJS-$(CONFIG_PELORUS_DENOISE_VULKAN_FILTER) += vf_pelorus_denoise_vulkan.o vulkan.o vulkan_filter.o\n")
    ins_after("configure",
              'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n',
              'pelorus_denoise_vulkan_filter_deps="vulkan spirv_library"\n')
    ins_after("configure", REQ % "pelorus_analyze_vulkan", REQ % "pelorus_denoise_vulkan")
else:
    sys.exit("unknown filter: " + which)
print(f"registration applied: {which}")
PY
    git -C "$WORKTREE" add -A
    git -C "$WORKTREE" \
        -c user.name=Lusoris -c user.email=lusoris@pm.me \
        commit -q -F "$HERE/.commit-msg-${filter}.txt"
done

# Pelorus libavcodec edit (NOT a filter): make NVENC honor ROI side data via its
# delta-QP map. generate.sh's filter model (drop a new file + inject registration
# hunks) can't express an edit to an existing file, so this is a hand-maintained
# unified diff in files/, applied as its own commit (-> patch 0004 below).
git -C "$WORKTREE" apply "$FILES_DIR/nvenc-pelorus-roi.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-nvenc.txt"

# Pelorus libavcodec edit (NOT a filter): make Intel QSV honor ROI side data via
# its dense per-block delta-QP map (mfxExtMBQP). Same hand-maintained-diff model
# as the NVENC patch above; applied as its own commit (-> patch 0005 below).
git -C "$WORKTREE" apply "$FILES_DIR/qsv-pelorus-roi.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-qsv.txt"

# vf_pelorus_grain_estimate_vulkan (FGS parameter estimator) is committed after
# the encoder patches so it lands as patch 0006 (nvenc keeps 0004, qsv 0005, no
# renumber of a shipped artifact). Same per-filter registration model as the
# deband/analyze/denoise loop above.
cp "$FILES_DIR/vf_pelorus_grain_estimate_vulkan.c" "$WORKTREE/libavfilter/"
python3 - "$WORKTREE" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
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
REQ = ('enabled %s_filter && require_pkg_config libpelorus '
       '"libpelorus >= 0.1.0" pelorus/interop.h pel_blob_pack '
       '&& add_extralibs $libpelorus_extralibs\n')
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_perms;\n",
           "extern const FFFilter ff_vf_pelorus_grain_estimate_vulkan;\n")
ins_after("libavfilter/Makefile",
          "OBJS-$(CONFIG_PELORUS_DENOISE_VULKAN_FILTER) += vf_pelorus_denoise_vulkan.o vulkan.o vulkan_filter.o\n",
          "OBJS-$(CONFIG_PELORUS_GRAIN_ESTIMATE_VULKAN_FILTER) += vf_pelorus_grain_estimate_vulkan.o vulkan.o vulkan_filter.o\n")
ins_after("configure",
          'pelorus_denoise_vulkan_filter_deps="vulkan spirv_library"\n',
          'pelorus_grain_estimate_vulkan_filter_deps="vulkan spirv_library"\n')
ins_after("configure", REQ % "pelorus_denoise_vulkan", REQ % "pelorus_grain_estimate_vulkan")
print("registration applied: grain_estimate")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-grain_estimate.txt"

# vf_pelorus_mc_vulkan (motion estimator) is committed last so it lands as patch
# 0007. Same per-filter registration model as the loop above.
cp "$FILES_DIR/vf_pelorus_mc_vulkan.c" "$WORKTREE/libavfilter/"
python3 - "$WORKTREE" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
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
REQ = ('enabled %s_filter && require_pkg_config libpelorus '
       '"libpelorus >= 0.1.0" pelorus/interop.h pel_blob_pack '
       '&& add_extralibs $libpelorus_extralibs\n')
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_perms;\n",
           "extern const FFFilter ff_vf_pelorus_mc_vulkan;\n")
ins_after("libavfilter/Makefile",
          "OBJS-$(CONFIG_PELORUS_DENOISE_VULKAN_FILTER) += vf_pelorus_denoise_vulkan.o vulkan.o vulkan_filter.o\n",
          "OBJS-$(CONFIG_PELORUS_MC_VULKAN_FILTER)      += vf_pelorus_mc_vulkan.o vulkan.o vulkan_filter.o\n")
ins_after("configure",
          'pelorus_denoise_vulkan_filter_deps="vulkan spirv_library"\n',
          'pelorus_mc_vulkan_filter_deps="vulkan spirv_library"\n')
ins_after("configure", REQ % "pelorus_denoise_vulkan", REQ % "pelorus_mc_vulkan")
print("registration applied: mc")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-mc.txt"

# Clean stale patches, regenerate the whole range.
rm -f "$HERE"/0*.patch
git -C "$WORKTREE" format-patch --zero-commit --start-number=1 \
    -o "$HERE" "$BASE_TAG..HEAD"

# Normalize auto-generated filenames to the series.txt names.
mv "$HERE"/0001-*.patch "$HERE/0001-add-vf_pelorus_deband_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0002-*.patch "$HERE/0002-add-vf_pelorus_analyze_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0003-*.patch "$HERE/0003-add-vf_pelorus_denoise_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0004-*.patch "$HERE/0004-nvenc-pelorus-roi.patch" 2>/dev/null || true
mv "$HERE"/0005-*.patch "$HERE/0005-qsv-pelorus-roi.patch" 2>/dev/null || true
mv "$HERE"/0006-*.patch "$HERE/0006-add-vf_pelorus_grain_estimate_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0007-*.patch "$HERE/0007-add-vf_pelorus_mc_vulkan.patch" 2>/dev/null || true

git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE"
echo "patch(es) regenerated in $HERE:"
ls "$HERE"/0*.patch
