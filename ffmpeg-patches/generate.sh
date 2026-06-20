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
          "OBJS-$(CONFIG_PELORUS_GRAIN_ESTIMATE_VULKAN_FILTER) += vf_pelorus_grain_estimate_vulkan.o vulkan.o vulkan_filter.o\n",
          "OBJS-$(CONFIG_PELORUS_MC_VULKAN_FILTER)      += vf_pelorus_mc_vulkan.o vulkan.o vulkan_filter.o\n")
ins_after("configure",
          'pelorus_grain_estimate_vulkan_filter_deps="vulkan spirv_library"\n',
          'pelorus_mc_vulkan_filter_deps="vulkan spirv_library"\n')
ins_after("configure", REQ % "pelorus_grain_estimate_vulkan", REQ % "pelorus_mc_vulkan")
print("registration applied: mc")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-mc.txt"

# Pelorus libavcodec edit (NOT a filter): make NVENC honor the PEL_SEC_MOTION MV
# field (emitted by 0007's vf_pelorus_mc_vulkan) via its external-ME-hint input,
# so the ASIC seeds/short-circuits its own motion search (encode SPEED, not
# quality). Same hand-maintained-diff model as the NVENC/QSV ROI patches above;
# applied as its own commit (-> patch 0008 below). Must land after 0007 (the
# producer) and after 0004 (it shares the NvencContext block 0004 introduced).
git -C "$WORKTREE" apply "$FILES_DIR/nvenc-pelorus-me-hints.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-nvenc-me-hints.txt"

# Pelorus libavcodec edit (NOT a filter): make the native Vulkan-video encoders
# (h264_vulkan/hevc_vulkan/av1_vulkan) honor ROI side data via the cross-vendor
# VK_KHR_video_encode_quantization_map (delta map for CQP, emphasis map for
# CBR/VBR). Same hand-maintained-diff model as the NVENC/QSV patches above;
# applied as its own commit (-> patch 0009 below). ADR-0114 Tier 2.
git -C "$WORKTREE" apply "$FILES_DIR/vulkan-pelorus-qpmap.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-vulkan.txt"

# pelorus_fgs bitstream filter (H.274 / HEVC FGC SEI inserter) lands as patch
# 0010 — the HEVC leg of the FGS round-trip the grain estimator (0006) started.
# A BSF is NOT an AVFilter: the source drops into libavcodec/bsf/ and the
# registration edits target bitstream_filters.c / bsf/Makefile / configure
# (not allfilters.c / libavfilter/Makefile). It is built on CBS (cbs_h265) and
# does NOT link libpelorus — it consumes the H.274 model via AVOptions, so no
# require_pkg_config hunk.
cp "$FILES_DIR/h265_pelorus_fgs_bsf.c" "$WORKTREE/libavcodec/bsf/pelorus_fgs.c"
python3 - "$WORKTREE" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
def ins_before(path, anchor, line):
    p = root / path; t = p.read_text()
    assert anchor in t, f"anchor missing in {path}: {anchor!r}"
    assert line.strip() not in t, f"already present in {path}: {line!r}"
    p.write_text(t.replace(anchor, line + anchor, 1))
# bitstream_filters.c: alphabetical extern list (pcm < pelorus < pgs).
ins_before("libavcodec/bitstream_filters.c",
           "extern const FFBitStreamFilter ff_pgs_frame_merge_bsf;\n",
           "extern const FFBitStreamFilter ff_pelorus_fgs_bsf;\n")
# bsf/Makefile: alphabetical OBJS list (pcm_rechunk < pelorus_fgs < pgs_frame_merge).
ins_before("libavcodec/bsf/Makefile",
           "OBJS-$(CONFIG_PGS_FRAME_MERGE_BSF)        += bsf/pgs_frame_merge.o\n",
           "OBJS-$(CONFIG_PELORUS_FGS_BSF)            += bsf/pelorus_fgs.o\n")
# configure: alphabetical *_bsf_select list (mpeg2 < pelorus < smpte436m); the
# BSF itself is auto-discovered by find_things scanning bitstream_filters.c.
ins_before("configure",
           'smpte436m_to_eia608_bsf_select="smpte_436m"\n',
           'pelorus_fgs_bsf_select="cbs_h265"\n')
print("registration applied: pelorus_fgs (bsf)")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-fgs-bsf.txt"

# Pelorus libavcodec edit (NOT a filter): make av1_nvenc carry the Pelorus AV1
# film-grain estimate into NVENC's hardware AV1 film-grain config so the grain
# is re-synthesized at decode instead of coded as residual — the HW-AV1 leg of
# the FGS round-trip (0010's BSF is the HEVC/H.274 leg; the AV1 software path
# round-trips via native side data). Same hand-maintained-diff model as the
# NVENC/QSV ROI + ME-hint patches above; applied as its own commit (-> patch
# 0011 below). Must land after 0006 (the grain producer) and after 0004 (it
# shares the NvencContext block 0004 introduced).
git -C "$WORKTREE" apply "$FILES_DIR/nvenc-pelorus-film-grain.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-nvenc-film-grain.txt"

# Pelorus libavcodec edit (NOT a filter): make libaom-av1 honor ROI side data via
# its segment-based AOME_SET_ROI_MAP (aom_roi_map_t) — the SW-AV1 ROI leg next to
# the NVENC/QSV dense delta-QP maps (ADR-0114 Tier 1). libaom has no dense
# per-block QP map: the bridge quantizes each frame's ROI qoffsets into <= 8
# AV1 segments (4x4-cell grid) and pushes the map per frame. Same hand-maintained
# -diff model as the NVENC/QSV ROI patches above; applied as its own commit
# (-> patch 0012 below). Independent of the encoder-specific patches, so it lands
# last to avoid renumbering any shipped artifact.
git -C "$WORKTREE" apply "$FILES_DIR/libaom-pelorus-roi.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-libaom.txt"

# Pelorus libavcodec edit (NOT a filter): make libsvtav1 (av1_svt) honor ROI side
# data via SVT-AV1's native per-superblock ROI segment map (SvtAv1RoiMapEvt /
# enable_roi_map / the ROI_MAP_EVENT priv-data node). Same hand-maintained-diff
# model as the NVENC/QSV ROI patches above; applied as its own commit (-> patch
# 0013 below). Must land after 0002 (the vf_pelorus_analyze producer); it touches
# libsvtav1.c only, so it has no dependency on the other encoder patches. The
# whole path is compile-guarded by SVT_AV1_CHECK_VERSION(1, 6, 0). ADR-0121.
git -C "$WORKTREE" apply "$FILES_DIR/svtav1-pelorus-roi.patch"
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-svtav1.txt"

# vf_pelorus_dehalo_vulkan (anime dehalo + dering) is a PURE pixel transform that
# does NOT link libpelorus (no interop emit), so its registration omits the
# require_pkg_config hunk entirely — just the deps line. Committed last so it
# lands as patch 0014. Same per-filter registration model as the deband loop.
cp "$FILES_DIR/vf_pelorus_dehalo_vulkan.c" "$WORKTREE/libavfilter/"
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
# allfilters.c: alphabetical (deban < dehal < denoi) -> insert before denoise.
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_pelorus_denoise_vulkan;\n",
           "extern const FFFilter ff_vf_pelorus_dehalo_vulkan;\n")
# Makefile: insert after deband (deband < dehalo).
ins_after("libavfilter/Makefile",
          "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n",
          "OBJS-$(CONFIG_PELORUS_DEHALO_VULKAN_FILTER)  += vf_pelorus_dehalo_vulkan.o vulkan.o vulkan_filter.o\n")
# configure: deps ONLY — dehalo does not link libpelorus (no require_pkg_config).
ins_after("configure",
          'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n',
          'pelorus_dehalo_vulkan_filter_deps="vulkan spirv_library"\n')
print("registration applied: dehalo")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-dehalo.txt"

# vf_pelorus_aa_vulkan (anime warp-AA + line-darkening) is a PURE pixel transform
# that does NOT link libpelorus (no interop emit) -> registration is deps-only (no
# require_pkg_config). "aa" sorts first among the pelorus filters (aa < analyze),
# so it inserts ahead of analyze. Committed last -> patch 0015.
cp "$FILES_DIR/vf_pelorus_aa_vulkan.c" "$WORKTREE/libavfilter/"
python3 - "$WORKTREE" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
def ins_before(path, anchor, line):
    p = root / path; t = p.read_text()
    assert anchor in t, f"anchor missing in {path}: {anchor!r}"
    assert line.strip() not in t, f"already present in {path}: {line!r}"
    p.write_text(t.replace(anchor, line + anchor, 1))
# allfilters.c: aa < analyze -> insert before analyze.
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_pelorus_analyze_vulkan;\n",
           "extern const FFFilter ff_vf_pelorus_aa_vulkan;\n")
# Makefile: aa < analyze -> insert before analyze.
ins_before("libavfilter/Makefile",
           "OBJS-$(CONFIG_PELORUS_ANALYZE_VULKAN_FILTER) += vf_pelorus_analyze_vulkan.o vulkan.o vulkan_filter.o\n",
           "OBJS-$(CONFIG_PELORUS_AA_VULKAN_FILTER)      += vf_pelorus_aa_vulkan.o vulkan.o vulkan_filter.o\n")
# configure: deps ONLY (aa does not link libpelorus); insert before analyze.
ins_before("configure",
           'pelorus_analyze_vulkan_filter_deps="vulkan spirv_library"\n',
           'pelorus_aa_vulkan_filter_deps="vulkan spirv_library"\n')
print("registration applied: aa")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-aa.txt"

# vf_pelorus_scenecut (scene-cut -> forced IDR) is a metadata-only consumer of
# PEL_SEC_MOTION — NOT a Vulkan filter (no shader, no _deps), but it LINKS
# libpelorus to parse the blob, so it carries the require_pkg_config hunk and a
# plain (no vulkan.o) OBJS line. Lands as patch 0016.
cp "$FILES_DIR/vf_pelorus_scenecut.c" "$WORKTREE/libavfilter/"
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
# allfilters.c: pelorus_scenecut sorts after pelorus_mc -> insert before perms.
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_perms;\n",
           "extern const FFFilter ff_vf_pelorus_scenecut;\n")
# Makefile: metadata-only -> plain .o (no vulkan.o), after mc.
ins_after("libavfilter/Makefile",
          "OBJS-$(CONFIG_PELORUS_MC_VULKAN_FILTER)      += vf_pelorus_mc_vulkan.o vulkan.o vulkan_filter.o\n",
          "OBJS-$(CONFIG_PELORUS_SCENECUT_FILTER)       += vf_pelorus_scenecut.o\n")
# configure: NO _deps (not a Vulkan filter); just the require_pkg_config link.
ins_after("configure", REQ % "pelorus_mc_vulkan", REQ % "pelorus_scenecut")
print("registration applied: scenecut")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-scenecut.txt"

# vf_pelorus_deblock_vulkan (re-encode deblock/dering) is a PURE pixel transform
# that does NOT link libpelorus (deps-only registration). "deblock" sorts after
# deband, before dehalo. Committed last -> patch 0017.
cp "$FILES_DIR/vf_pelorus_deblock_vulkan.c" "$WORKTREE/libavfilter/"
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
# allfilters.c: deband < deblock < dehalo -> insert before dehalo.
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_pelorus_dehalo_vulkan;\n",
           "extern const FFFilter ff_vf_pelorus_deblock_vulkan;\n")
# Makefile: insert after deband (deband < deblock).
ins_after("libavfilter/Makefile",
          "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n",
          "OBJS-$(CONFIG_PELORUS_DEBLOCK_VULKAN_FILTER) += vf_pelorus_deblock_vulkan.o vulkan.o vulkan_filter.o\n")
# configure: deps ONLY (no libpelorus); insert after deband.
ins_after("configure",
          'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n',
          'pelorus_deblock_vulkan_filter_deps="vulkan spirv_library"\n')
print("registration applied: deblock")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-deblock.txt"

# vf_pelorus_borderfix_vulkan (dirty-line/border repair) is a PURE pixel transform
# that does NOT link libpelorus (deps-only registration). "borderfix" sorts after
# analyze, before deband. Committed last -> patch 0018.
cp "$FILES_DIR/vf_pelorus_borderfix_vulkan.c" "$WORKTREE/libavfilter/"
python3 - "$WORKTREE" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
def ins_before(path, anchor, line):
    p = root / path; t = p.read_text()
    assert anchor in t, f"anchor missing in {path}: {anchor!r}"
    assert line.strip() not in t, f"already present in {path}: {line!r}"
    p.write_text(t.replace(anchor, line + anchor, 1))
# allfilters.c: analyze < borderfix < deband -> insert before deband.
ins_before("libavfilter/allfilters.c",
           "extern const FFFilter ff_vf_pelorus_deband_vulkan;\n",
           "extern const FFFilter ff_vf_pelorus_borderfix_vulkan;\n")
# Makefile: insert before deband.
ins_before("libavfilter/Makefile",
           "OBJS-$(CONFIG_PELORUS_DEBAND_VULKAN_FILTER)  += vf_pelorus_deband_vulkan.o vulkan.o vulkan_filter.o\n",
           "OBJS-$(CONFIG_PELORUS_BORDERFIX_VULKAN_FILTER) += vf_pelorus_borderfix_vulkan.o vulkan.o vulkan_filter.o\n")
# configure: deps ONLY (no libpelorus); insert before deband.
ins_before("configure",
           'pelorus_deband_vulkan_filter_deps="vulkan spirv_library"\n',
           'pelorus_borderfix_vulkan_filter_deps="vulkan spirv_library"\n')
print("registration applied: borderfix")
PY
git -C "$WORKTREE" add -A
git -C "$WORKTREE" \
    -c user.name=Lusoris -c user.email=lusoris@pm.me \
    commit -q -F "$HERE/.commit-msg-borderfix.txt"

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
mv "$HERE"/0008-*.patch "$HERE/0008-nvenc-pelorus-me-hints.patch" 2>/dev/null || true
mv "$HERE"/0009-*.patch "$HERE/0009-vulkan-pelorus-qpmap.patch" 2>/dev/null || true
mv "$HERE"/0010-*.patch "$HERE/0010-add-pelorus_fgs_bsf.patch" 2>/dev/null || true
mv "$HERE"/0011-*.patch "$HERE/0011-nvenc-pelorus-film-grain.patch" 2>/dev/null || true
mv "$HERE"/0012-*.patch "$HERE/0012-libaom-pelorus-roi.patch" 2>/dev/null || true
mv "$HERE"/0013-*.patch "$HERE/0013-svtav1-pelorus-roi.patch" 2>/dev/null || true
mv "$HERE"/0014-*.patch "$HERE/0014-add-vf_pelorus_dehalo_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0015-*.patch "$HERE/0015-add-vf_pelorus_aa_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0016-*.patch "$HERE/0016-add-vf_pelorus_scenecut.patch" 2>/dev/null || true
mv "$HERE"/0017-*.patch "$HERE/0017-add-vf_pelorus_deblock_vulkan.patch" 2>/dev/null || true
mv "$HERE"/0018-*.patch "$HERE/0018-add-vf_pelorus_borderfix_vulkan.patch" 2>/dev/null || true

git -C "$FFMPEG_REPO" worktree remove --force "$WORKTREE"
echo "patch(es) regenerated in $HERE:"
ls "$HERE"/0*.patch
