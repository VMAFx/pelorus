#!/usr/bin/env bash
#
# vulkan-drm-map-smoke.sh — a Pelorus filter's output maps to VAAPI (and to
# QSV through VAAPI) without hwdownload when the filter allocates its pool
# with tiling=drm (issue #103, ADR-0184).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# GPU test. pelorus_deband_vulkan feeds hwmap=derive_device=vaapi. Two checks
# per case:
#   readback  the mapped VAAPI surface, read back with hwdownload, has the
#             same per-frame MD5 as the Vulkan frame read back directly: the
#             map carries exactly the filter's pixels
#   encode    the encoder consumes the mapped surfaces; its stream is compared
#             with the same encoder fed through hwdownload and hwupload, and
#             must be bit-identical or reach MIN_PSNR (the iHD encoder codes a
#             LINEAR surface slightly differently from the tiled one it
#             allocates itself, from identical pixels)
# Cases:
#   nv12-640x360   tiling=drm, h264_vaapi constant QP
#   nv12-641x361   odd frame size
#   p010-640x360   10-bit, hevc_vaapi Main10 (see P010 below)
#   p010-drm       10-bit pool exported as DRM PRIME (no VAAPI import)
#   negative       drm_modifiers names only NEGATIVE_MODIFIER, which the GPU
#                  does not offer: the filter logs the substitution of
#                  DRM_FORMAT_MOD_LINEAR by name, and the LINEAR pool maps
#   bad-list       a malformed drm_modifiers list fails the filter
#   qsv-nv12       (QSV=1) hevc_qsv constant QP through hwmap vaapi -> qsv
# Stock FFmpeg n9.0.2 exports a P010 pool as layers R16 + GR1616 and its VAAPI
# import accepts only R16 + RG1616 (hwcontext_vaapi.c vaapi_drm_format_map):
# p010-640x360 then reports KNOWN-STOCK and does not fail, unless P010=1
# demands it (FFmpeg carrying the GR1616 rows).
#
# Env:
#   FFMPEG_BIN         patched ffmpeg with Vulkan, VAAPI and libdrm (required)
#   RENDER_NODE        render node of the GPU under test (default
#                      /dev/dri/renderD128); Vulkan is derived from its VAAPI
#                      device, so both run on the same GPU
#   EXPECT_DEVICE      extended regex the selected Vulkan device must match
#   QSV                1: run the hevc_qsv case (Intel)
#   P010               1: p010-640x360 must pass
#   NEGATIVE_MODIFIER  modifier the GPU does not offer for nv12 (default
#                      0x0c00000000000001, DRM_FORMAT_MOD_APPLE_GPU_TILED)
#   MIN_PSNR           encode check floor in dB when not bit-exact (default 50)
#   OUTPUT_ROOT        evidence directory (default: new directory under TMPDIR)
#   FRAMES             frames per case (default 10)
# Options:
#   --self-test        plant defects into the checks; needs no GPU
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).
set -euo pipefail

PEL_OUT=""
PEL_FRAMES="${FRAMES:-10}"
PEL_NODE="${RENDER_NODE:-/dev/dri/renderD128}"
PEL_NEG="${NEGATIVE_MODIFIER:-0x0c00000000000001}"
PEL_MIN_PSNR="${MIN_PSNR:-50}"

pel_skip()
{
    echo "SKIP: $*"
    exit 77
}

pel_fail()
{
    echo "FAIL: $*" >&2
    [[ -z "$PEL_OUT" ]] || echo "Evidence: $PEL_OUT" >&2
    exit 1
}

# Lines of a log that contain a fixed string.
pel_count()
{
    awk -v needle="$2" 'index($0, needle) { n++ } END { print n + 0 }' "$1"
}

# First error line of a log (empty when there is none).
pel_first_error()
{
    awk 'tolower($0) ~ /error|failed/ { print; exit }' "$1"
}

# The Vulkan device FFmpeg selected, from a verbose log.
pel_device()
{
    awk '/Device [0-9]+ selected: / { sub(/.*selected: /, ""); print; exit }' "$1"
}

# check_frames NAME ZERO_COPY HOST FRAMES: two framemd5 files list FRAMES
# frames and the same MD5 for each.
pel_check_frames()
{
    local name="$1" zc="$2" host="$3" frames="$4" n

    n="$(awk '!/^#/ { n++ } END { print n + 0 }' "$host")"
    [[ "$n" == "$frames" ]] || { echo "$name: $n of $frames frames read back"; return 1; }
    # Plain string comparison: the MSYS2 CI image has no diff(1).
    [[ "$(grep -v '^#' "$zc")" == "$(grep -v '^#' "$host")" ]] ||
        { echo "$name: mapped surface differs from the filter's frame"; return 1; }
}

# check_stream NAME PSNR: the encode is bit-exact ("inf") or reaches MIN_PSNR.
pel_check_stream()
{
    local name="$1" psnr="$2"

    [[ "$psnr" == inf ]] && return 0
    [[ "$psnr" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "$name: no PSNR measured"; return 1; }
    awk -v p="$psnr" -v m="$PEL_MIN_PSNR" 'BEGIN { exit !(p >= m) }' ||
        { echo "$name: stream PSNR $psnr dB below $PEL_MIN_PSNR dB"; return 1; }
}

# check_pool NAME LOG MODIFIER_NAME: the filter named the pool modifier, and
# when MODIFIER_NAME is set, it is that modifier and was substituted.
pel_check_pool()
{
    local name="$1" log="$2" want="${3:-}"

    [[ "$(pel_count "$log" "pool uses DRM format modifier")" -ge 1 ]] ||
        { echo "$name: the filter did not name its pool modifier"; return 1; }
    [[ -z "$want" ]] && return 0
    [[ "$(pel_count "$log" "substituting $want")" -ge 1 ]] ||
        { echo "$name: no substitution of $want logged"; return 1; }
    [[ "$(pel_count "$log" "pool uses DRM format modifier 0x0000000000000000 ($want)")" -ge 1 ]] ||
        { echo "$name: the pool does not use $want"; return 1; }
}

# check_device LOG: a Vulkan device was selected and matches EXPECT_DEVICE.
pel_check_device()
{
    local dev

    dev="$(pel_device "$1")"
    [[ -n "$dev" ]] || { echo "no Vulkan device line in $1"; return 1; }
    [[ -z "${EXPECT_DEVICE:-}" || "$dev" =~ $EXPECT_DEVICE ]] ||
        { echo "Vulkan device '$dev' does not match '$EXPECT_DEVICE'"; return 1; }
}

pel_self_test()
{
    local dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/pelorus-drm-map-self.XXXXXX")"
    printf '#format\n0, 0, 0, 1, 1, aaa\n0, 1, 1, 1, 1, bbb\n' >"$dir/f1"
    printf '#other header\n0, 0, 0, 1, 1, aaa\n0, 1, 1, 1, 1, bbb\n' >"$dir/f1same"
    printf '#format\n0, 0, 0, 1, 1, aaa\n0, 1, 1, 1, 1, ccc\n' >"$dir/f2"
    printf '#format\n0, 0, 0, 1, 1, aaa\n' >"$dir/short"
    printf '%s\n' '[Vulkan] Device 1 selected: Intel(R) Arc(tm) A380 Graphics (DG2)' \
        '[f] tiling=drm: nv12 64x64 pool uses DRM format modifier 0x0100000000000009 (I915_FORMAT_MOD_4_TILED), chosen by the driver from 3' \
        >"$dir/ok.log"
    printf '%s\n' '[Vulkan] Device 0 selected: llvmpipe (LLVM 21.1.0, 256 bits)' >"$dir/cpu.log"
    printf '%s\n' '[f] tiling=drm: no modifier in drm_modifiers supports storage images and DMA-BUF export for nv12; substituting DRM_FORMAT_MOD_LINEAR (0x0000000000000000)' \
        '[f] tiling=drm: nv12 64x64 pool uses DRM format modifier 0x0000000000000000 (DRM_FORMAT_MOD_LINEAR), chosen by the driver from 1' \
        >"$dir/fallback.log"
    pel_check_frames t "$dir/f1" "$dir/f1same" 2 >/dev/null || pel_fail "self-test: equal frames rejected"
    ! pel_check_frames t "$dir/f2" "$dir/f1" 2 >/dev/null || pel_fail "self-test: different frame accepted"
    ! pel_check_frames t "$dir/short" "$dir/short" 2 >/dev/null || pel_fail "self-test: lost frame accepted"
    pel_check_stream t inf >/dev/null || pel_fail "self-test: bit-exact stream rejected"
    pel_check_stream t 78.14 >/dev/null || pel_fail "self-test: 78 dB stream rejected"
    ! pel_check_stream t 31.2 >/dev/null || pel_fail "self-test: 31 dB stream accepted"
    ! pel_check_stream t "" >/dev/null || pel_fail "self-test: missing PSNR accepted"
    pel_check_pool t "$dir/ok.log" >/dev/null || pel_fail "self-test: named pool rejected"
    ! pel_check_pool t "$dir/cpu.log" >/dev/null || pel_fail "self-test: unnamed pool accepted"
    pel_check_pool t "$dir/fallback.log" DRM_FORMAT_MOD_LINEAR >/dev/null ||
        pel_fail "self-test: logged substitution rejected"
    ! pel_check_pool t "$dir/ok.log" DRM_FORMAT_MOD_LINEAR >/dev/null ||
        pel_fail "self-test: missing substitution accepted"
    EXPECT_DEVICE='Arc' pel_check_device "$dir/ok.log" >/dev/null || pel_fail "self-test: right device rejected"
    ! EXPECT_DEVICE='Arc' pel_check_device "$dir/cpu.log" >/dev/null ||
        pel_fail "self-test: wrong device accepted"
    ! pel_check_device "$dir/fallback.log" >/dev/null || pel_fail "self-test: missing device line accepted"
    rm -rf -- "$dir"
    echo "PASS: self-test (8 planted defects rejected, 6 controls accepted)"
}

if [[ "${1:-}" == "--self-test" ]]; then
    pel_self_test
    exit 0
fi

[[ -n "${FFMPEG_BIN:-}" ]] || pel_skip "FFMPEG_BIN unset (needs a patched ffmpeg and a VAAPI-capable GPU)"
[[ -x "$FFMPEG_BIN" ]] || pel_fail "FFMPEG_BIN is not executable: $FFMPEG_BIN"
[[ -e "$PEL_NODE" ]] || pel_skip "render node $PEL_NODE does not exist"
if ! [[ "$PEL_FRAMES" =~ ^[0-9]+$ ]] || ((PEL_FRAMES < 2 || PEL_FRAMES > 120)); then
    pel_fail "FRAMES must be 2..120"
fi
PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-drm-map.XXXXXX")}"
mkdir -p "$PEL_OUT"

"$FFMPEG_BIN" -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 || pel_fail "ffmpeg -filters failed"
grep -Eq ' pelorus_deband_vulkan ' "$PEL_OUT/filters.txt" ||
    pel_skip "pelorus_deband_vulkan is not built into $FFMPEG_BIN"
PEL_DEVS=(-init_hw_device "drm=pel_dr:$PEL_NODE" -init_hw_device vaapi=pel_va@pel_dr
    -init_hw_device vulkan=pel_vk@pel_va -filter_hw_device pel_vk)
if ! "$FFMPEG_BIN" -hide_banner -v verbose "${PEL_DEVS[@]}" -f lavfi -i testsrc2=s=64x64 \
    -frames:v 1 -vf format=nv12,hwupload -f null - >"$PEL_OUT/probe.log" 2>&1; then
    pel_skip "no VAAPI + Vulkan pair on $PEL_NODE: $(pel_first_error "$PEL_OUT/probe.log")"
fi
pel_check_device "$PEL_OUT/probe.log" || pel_fail "device check failed"
echo "Vulkan device: $(pel_device "$PEL_OUT/probe.log")"
echo "VAAPI driver: $(awk '/VAAPI driver: / { sub(/.*VAAPI driver: /, ""); print; exit }' "$PEL_OUT/probe.log")"

# run NAME SIZE SWFMT TAIL OUTPUT_ARGS...: testsrc2 -> SWFMT -> hwupload -> TAIL
# into NAME.out; the log is NAME.log.
pel_run()
{
    local name="$1" size="$2" fmt="$3" tail="$4"
    shift 4

    "$FFMPEG_BIN" -hide_banner -v verbose -y "${PEL_DEVS[@]}" \
        -f lavfi -i "testsrc2=s=$size:r=30" -frames:v "$PEL_FRAMES" \
        -vf "format=$fmt,hwupload,$tail" "$@" "$PEL_OUT/$name.out" >"$PEL_OUT/$name.log" 2>&1
}

# psnr A B: average PSNR of two streams ("inf" when identical).
pel_psnr()
{
    "$FFMPEG_BIN" -hide_banner -i "$1" -i "$2" -lavfi psnr -f null - 2>&1 |
        awk '/PSNR .* average:/ { sub(/.*average:/, ""); sub(/ .*/, ""); print; exit }'
}

# readback NAME SIZE SWFMT ARGS: the VAAPI-mapped surface reads back as the
# Vulkan frame does. Returns 1 when the zero-copy run itself fails.
pel_readback()
{
    local name="$1" size="$2" fmt="$3" args="$4"

    pel_run "$name-rb-host" "$size" "$fmt" "pelorus_deband_vulkan,hwdownload,format=$fmt" \
        -f framemd5 || pel_fail "$name: Vulkan readback failed: $(pel_first_error "$PEL_OUT/$name-rb-host.log")"
    pel_run "$name-rb" "$size" "$fmt" \
        "pelorus_deband_vulkan=$args,hwmap=derive_device=vaapi,format=vaapi,hwdownload,format=$fmt" \
        -f framemd5 || return 1
    pel_check_device "$PEL_OUT/$name-rb.log" || pel_fail "$name: device check failed"
    pel_check_frames "$name" "$PEL_OUT/$name-rb.out" "$PEL_OUT/$name-rb-host.out" "$PEL_FRAMES" ||
        pel_fail "$name: readback check failed"
}

# encode NAME SIZE SWFMT ARGS ZC_TAIL HOST_TAIL ENCODER...: the zero-copy and
# the hwdownload stream of one case; prints the PSNR between them.
pel_encode()
{
    local name="$1" size="$2" fmt="$3" args="$4" zc="$5" host="$6" psnr
    shift 6

    pel_run "$name-host" "$size" "$fmt" "pelorus_deband_vulkan,hwdownload,format=$fmt,$host" "$@" ||
        pel_fail "$name: hwdownload path failed: $(pel_first_error "$PEL_OUT/$name-host.log")"
    pel_run "$name" "$size" "$fmt" "pelorus_deband_vulkan=$args,$zc" "$@" ||
        pel_fail "$name: zero-copy encode failed: $(pel_first_error "$PEL_OUT/$name.log")"
    pel_check_device "$PEL_OUT/$name.log" || pel_fail "$name: device check failed"
    psnr="$(pel_psnr "$PEL_OUT/$name.out" "$PEL_OUT/$name-host.out")"
    pel_check_stream "$name" "$psnr" || pel_fail "$name: encode check failed"
    echo "$psnr"
}

PEL_VA_ZC="hwmap=derive_device=vaapi"
PEL_VA_HOST="hwupload=derive_device=vaapi"
PEL_H264=(-c:v h264_vaapi -rc_mode CQP -qp 20 -f h264)

for c in nv12-640x360:640x360 nv12-641x361:641x361; do
    name="${c%%:*}"
    pel_readback "$name" "${c#*:}" nv12 tiling=drm ||
        pel_fail "$name: $(pel_first_error "$PEL_OUT/$name-rb.log")"
    pel_check_pool "$name" "$PEL_OUT/$name-rb.log" || pel_fail "pool check failed"
    psnr="$(pel_encode "$name" "${c#*:}" nv12 tiling=drm "$PEL_VA_ZC" "$PEL_VA_HOST" "${PEL_H264[@]}")"
    echo "PASS: $name (readback bit-exact; h264_vaapi PSNR $psnr dB vs hwdownload)"
done

if pel_readback p010-640x360 640x360 p010le tiling=drm; then
    psnr="$(pel_encode p010-640x360 640x360 p010le tiling=drm "$PEL_VA_ZC" "$PEL_VA_HOST" \
        -c:v hevc_vaapi -profile:v main10 -rc_mode CQP -qp 24 -f hevc)"
    echo "PASS: p010-640x360 (readback bit-exact; hevc_vaapi PSNR $psnr dB vs hwdownload)"
elif [[ "${P010:-0}" != 1 && "$(pel_count "$PEL_OUT/p010-640x360-rb.log" "DRM format not supported by VAAPI")" -ge 1 ]]; then
    echo "KNOWN-STOCK: p010-640x360: VAAPI does not import the R16 + GR1616 layers stock FFmpeg exports"
else
    pel_fail "p010-640x360: $(pel_first_error "$PEL_OUT/p010-640x360-rb.log")"
fi
pel_run p010-drm 640x360 p010le "pelorus_deband_vulkan=tiling=drm,hwmap=derive_device=drm,format=drm_prime" \
    -f null || pel_fail "p010-drm: $(pel_first_error "$PEL_OUT/p010-drm.log")"
pel_check_pool p010-drm "$PEL_OUT/p010-drm.log" || pel_fail "pool check failed"
# FFmpeg folds repeated log lines, so count the frames from the progress line.
[[ "$(pel_count "$PEL_OUT/p010-drm.log" "Mapped AVVkFrame to a DRM object")" -ge 1 ]] ||
    pel_fail "p010-drm: no frame exported as a DRM object"
[[ "$(awk -F'frame= *' '/frame= *[0-9]+ / { split($2, a, " "); n = a[1] } END { print n + 0 }' \
    "$PEL_OUT/p010-drm.log")" == "$PEL_FRAMES" ]] || pel_fail "p010-drm: not every frame passed the map"
echo "PASS: p010-drm"

pel_readback negative 640x360 nv12 "tiling=drm:drm_modifiers=$PEL_NEG" ||
    pel_fail "negative: $(pel_first_error "$PEL_OUT/negative-rb.log")"
pel_check_pool negative "$PEL_OUT/negative-rb.log" DRM_FORMAT_MOD_LINEAR || pel_fail "pool check failed"
psnr="$(pel_encode negative 640x360 nv12 "tiling=drm:drm_modifiers=$PEL_NEG" "$PEL_VA_ZC" \
    "$PEL_VA_HOST" "${PEL_H264[@]}")"
echo "PASS: negative ($PEL_NEG not offered; DRM_FORMAT_MOD_LINEAR substituted and logged; readback bit-exact; PSNR $psnr dB)"

if pel_run bad-list 64x64 nv12 "pelorus_deband_vulkan=tiling=drm:drm_modifiers=zz,$PEL_VA_ZC" \
    "${PEL_H264[@]}"; then
    pel_fail "bad-list: a malformed drm_modifiers list was accepted"
fi
[[ "$(pel_count "$PEL_OUT/bad-list.log" "drm_modifiers: cannot parse")" -ge 1 ]] ||
    pel_fail "bad-list: the filter did not name the malformed list"
echo "PASS: bad-list"

if [[ "${QSV:-0}" == 1 ]]; then
    psnr="$(pel_encode qsv-nv12 640x360 nv12 tiling=drm "$PEL_VA_ZC,format=vaapi,hwmap=derive_device=qsv,format=qsv" \
        "hwupload=derive_device=qsv:extra_hw_frames=16" -c:v hevc_qsv -q:v 24 -f hevc)"
    pel_check_pool qsv-nv12 "$PEL_OUT/qsv-nv12.log" || pel_fail "pool check failed"
    echo "PASS: qsv-nv12 (hevc_qsv PSNR $psnr dB vs hwdownload)"
fi
echo "Evidence: $PEL_OUT"
