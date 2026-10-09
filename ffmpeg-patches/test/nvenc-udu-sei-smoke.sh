#!/usr/bin/env bash
#
# nvenc-udu-sei-smoke.sh — hevc_nvenc carries pelorus_analyze_vulkan's side
# data with -udu_sei 1 (issue #267, ADR-0181).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# GPU test. Before patch 0022, the 1080p case stopped the encode with
# "Failed locking bitstream buffer: out of memory" (AVERROR(ENOMEM)). Cases:
#   maps-1080p   analyze maps=1 at 1920x1080, cell 32 (12 424-byte blob): the
#                encode succeeds, the encoder log names the removed maps, and
#                every picture carries the scalar sections without maps.
#   grid-1x1     a frame smaller than one cell (1x1 grid, 201-byte blob): the
#                maps fit and stay in the stream.
#   grid-2^20    8192x8192 at cell 8, the largest grid (6 MiB blob): carried
#                without maps.
#   foreign      a 2 017-byte user data unregistered SEI from another producer:
#                not written, named in the log, and the encode succeeds.
#
# Env:
#   FFMPEG_BIN     patched ffmpeg binary (required)
#   VULKAN_DEVICE  FFmpeg Vulkan device index for the analyze filter (optional)
#   OUTPUT_ROOT    evidence directory (default: new directory under TMPDIR)
#   FRAMES         frames per case (default 8)
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).
set -euo pipefail

PEL_FFMPEG="${FFMPEG_BIN:?FFMPEG_BIN must name the patched ffmpeg binary}"
PEL_FRAMES="${FRAMES:-8}"
PEL_DEVICE="vulkan=pel_vk"
if [[ -n "${VULKAN_DEVICE:-}" ]]; then
    PEL_DEVICE+=":${VULKAN_DEVICE}"
fi
PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-nvenc-udu-sei.XXXXXX")}"
mkdir -p "$PEL_OUT"
PEL_CHECK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/analyze-maps-check.py"
PEL_UUID="e1d7c4a2-6b93-4f08-9a55-0f3c2db17e64"
PEL_FOREIGN_UUID="0f1e2d3c-4b5a-4968-8776-655443322110"

pel_skip()
{
    echo "SKIP: $*"
    exit 77
}

pel_fail()
{
    echo "FAIL: $*" >&2
    echo "Evidence: $PEL_OUT" >&2
    exit 1
}

# First error line of a log (empty when there is none).
pel_first_error()
{
    awk 'tolower($0) ~ /error|failed/ { print; exit }' "$1"
}

# Decoded pictures in a showinfo log.
pel_pictures()
{
    awk '/\] n: *[0-9]+ / { n++ } END { print n + 0 }' "$1"
}

# Lines of a log that contain a fixed string.
pel_count()
{
    awk -v needle="$2" 'index($0, needle) { n++ } END { print n + 0 }' "$1"
}

[[ -x "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN is not executable: $PEL_FFMPEG"
if ! [[ "$PEL_FRAMES" =~ ^[0-9]+$ ]] || ((PEL_FRAMES < 2 || PEL_FRAMES > 64)); then
    pel_fail "FRAMES must be 2..64 (one frame ends in the flush path, which hides a lost picture)"
fi
command -v python3 >/dev/null 2>&1 || pel_fail "python3 is required"

"$PEL_FFMPEG" -hide_banner -encoders >"$PEL_OUT/encoders.txt" 2>&1 ||
    pel_fail "ffmpeg -encoders failed"
"$PEL_FFMPEG" -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 ||
    pel_fail "ffmpeg -filters failed"
grep -Eq '^ V[^ ]* hevc_nvenc ' "$PEL_OUT/encoders.txt" ||
    pel_skip "hevc_nvenc is not built into $PEL_FFMPEG (no ffnvcodec)"
grep -Eq '^ V[^ ]* h264_nvenc ' "$PEL_OUT/encoders.txt" ||
    pel_skip "h264_nvenc is not built into $PEL_FFMPEG (no ffnvcodec)"
grep -Eq ' pelorus_analyze_vulkan ' "$PEL_OUT/filters.txt" ||
    pel_skip "pelorus_analyze_vulkan is not built into $PEL_FFMPEG (no Vulkan SPIR-V compiler)"
if ! "$PEL_FFMPEG" -hide_banner -loglevel error -f lavfi -i testsrc2=size=256x64 \
    -frames:v 1 -c:v hevc_nvenc -f null - >"$PEL_OUT/probe-nvenc.log" 2>&1; then
    pel_skip "hevc_nvenc cannot encode on this host: $(head -c 160 "$PEL_OUT/probe-nvenc.log" | head -n 1)"
fi
if ! "$PEL_FFMPEG" -hide_banner -loglevel error -init_hw_device "$PEL_DEVICE" \
    -f lavfi -i testsrc2=size=64x64 -frames:v 1 -f null - >"$PEL_OUT/probe-vulkan.log" 2>&1; then
    pel_skip "no usable Vulkan device ($PEL_DEVICE): $(head -c 160 "$PEL_OUT/probe-vulkan.log" | head -n 1)"
fi

# encode NAME SOURCE FRAMES FILTERS: analyze on Vulkan, then hevc_nvenc -udu_sei 1.
pel_encode()
{
    local name="$1" source="$2" frames="$3" filters="$4"

    "$PEL_FFMPEG" -hide_banner -loglevel info -y -init_hw_device "$PEL_DEVICE" \
        -filter_hw_device pel_vk -f lavfi -i "$source" -frames:v "$frames" \
        -vf "$filters" -c:v hevc_nvenc -udu_sei 1 -f hevc "$PEL_OUT/$name.hevc" \
        >"$PEL_OUT/$name-encode.log" 2>&1 ||
        pel_fail "$name: hevc_nvenc -udu_sei 1 failed: $(pel_first_error "$PEL_OUT/$name-encode.log")"
}

# tap NAME FRAMES: decode the stream; every picture carries one Pelorus blob.
pel_tap()
{
    local name="$1" frames="$2" pictures blobs

    "$PEL_FFMPEG" -hide_banner -loglevel info -i "$PEL_OUT/$name.hevc" -vf showinfo \
        -f null - >"$PEL_OUT/$name-tap.log" 2>&1 || pel_fail "$name: decode of the stream failed"
    pictures="$(pel_pictures "$PEL_OUT/$name-tap.log")"
    blobs="$(pel_count "$PEL_OUT/$name-tap.log" "UUID=$PEL_UUID")"
    [[ "$pictures" == "$frames" ]] || pel_fail "$name: $pictures of $frames pictures decoded"
    [[ "$blobs" == "$frames" ]] || pel_fail "$name: $blobs Pelorus blobs for $frames pictures"
}

pel_maps_check()
{
    local name="$1"
    shift
    python3 -I "$PEL_CHECK" "$@" >"$PEL_OUT/$name-check.log" 2>&1 ||
        pel_fail "$name: analyze-maps-check.py $*: $(tail -n 3 "$PEL_OUT/$name-check.log")"
}

PEL_HEAD="format=nv12,hwupload"
PEL_TAIL="hwdownload,format=nv12"

# maps-1080p: the issue #267 reproducer.
pel_encode maps-1080p "testsrc2=size=1920x1080:rate=25" "$PEL_FRAMES" \
    "$PEL_HEAD,pelorus_analyze_vulkan=maps=1:cell=32,$PEL_TAIL"
grep -q 'written without its per-cell maps' "$PEL_OUT/maps-1080p-encode.log" ||
    pel_fail "maps-1080p: the encoder log does not name the removed maps"
pel_tap maps-1080p "$PEL_FRAMES"
pel_maps_check maps-1080p no-maps "$PEL_OUT/maps-1080p-tap.log" --grid 60x34
echo "PASS maps-1080p: $PEL_FRAMES pictures, scalar sections in the stream, maps removal logged"

# grid-1x1: analyze a 64x64 frame (one 64-pixel cell), scale up for NVENC.
pel_encode grid-1x1 "testsrc2=size=64x64:rate=25" "$PEL_FRAMES" \
    "$PEL_HEAD,pelorus_analyze_vulkan=maps=1:cell=64,$PEL_TAIL,scale=256:64"
if grep -q 'written without its per-cell maps' "$PEL_OUT/grid-1x1-encode.log"; then
    pel_fail "grid-1x1: maps removed although the blob fits"
fi
pel_tap grid-1x1 "$PEL_FRAMES"
pel_maps_check grid-1x1 maps "$PEL_OUT/grid-1x1-tap.log" --grid 1x1
echo "PASS grid-1x1: $PEL_FRAMES pictures, the 1x1 maps stay in the stream"

# grid-2^20: 8192x8192 at cell 8 is 1024x1024 cells, PEL_AN_MAX_CELLS.
pel_encode grid-max "color=c=gray:size=8192x8192:rate=25" 2 \
    "$PEL_HEAD,pelorus_analyze_vulkan=maps=1:cell=8,$PEL_TAIL"
pel_tap grid-max 2
pel_maps_check grid-max no-maps "$PEL_OUT/grid-max-tap.log" --grid 1024x1024
echo "PASS grid-2^20: 2 pictures, scalar sections in the stream"

# foreign: h264_metadata adds a 2 017-byte SEI (UUID + 2 000 bytes + NUL) to the
# first picture, the decoder exports it as frame side data.
pel_text="$(printf '%2000s' '' | tr ' ' 'x')"
"$PEL_FFMPEG" -hide_banner -loglevel error -y -f lavfi -i "testsrc2=size=256x64:rate=25" \
    -frames:v "$PEL_FRAMES" -c:v h264_nvenc \
    -bsf:v "h264_metadata=sei_user_data=${PEL_FOREIGN_UUID}+${pel_text}" -f h264 \
    "$PEL_OUT/foreign-src.h264" >"$PEL_OUT/foreign-src.log" 2>&1 ||
    pel_fail "foreign: could not build the source stream"
"$PEL_FFMPEG" -hide_banner -loglevel info -y -i "$PEL_OUT/foreign-src.h264" -c:v hevc_nvenc \
    -udu_sei 1 -f hevc "$PEL_OUT/foreign.hevc" >"$PEL_OUT/foreign-encode.log" 2>&1 ||
    pel_fail "foreign: hevc_nvenc -udu_sei 1 failed: $(pel_first_error "$PEL_OUT/foreign-encode.log")"
grep -q 'user data unregistered SEI is not written' "$PEL_OUT/foreign-encode.log" ||
    pel_fail "foreign: the encoder log does not name the dropped SEI"
"$PEL_FFMPEG" -hide_banner -loglevel info -i "$PEL_OUT/foreign.hevc" -vf showinfo -f null - \
    >"$PEL_OUT/foreign-tap.log" 2>&1 || pel_fail "foreign: decode of the stream failed"
if grep -q "UUID=$PEL_FOREIGN_UUID" "$PEL_OUT/foreign-tap.log"; then
    pel_fail "foreign: the over-limit SEI is in the stream"
fi
[[ "$(pel_pictures "$PEL_OUT/foreign-tap.log")" == "$PEL_FRAMES" ]] ||
    pel_fail "foreign: pictures lost"
echo "PASS foreign: over-limit SEI not written and logged, $PEL_FRAMES pictures"

echo "OK: hevc_nvenc udu_sei smoke ($PEL_OUT)"
