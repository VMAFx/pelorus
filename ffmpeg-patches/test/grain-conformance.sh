#!/usr/bin/env bash
#
# grain-conformance.sh — conformance against reference decoders' film-grain
# synthesisers (AV1 ff_aom_apply_film_grain and H.274 ff_h274_apply_film_grain)
# on converted parameters (issue #82, ADR-0211).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# Env:
#   FFMPEG_TREE    built FFmpeg tree root (required, absolute)
#   PELORUS_BUILD  meson build dir with libpelorus, or empty for pkg-config
#   OUTPUT_ROOT    evidence directory (required, absolute or relative)
#   CC             C compiler (default: cc)
# Exit: 0 pass, 1 fail, 77 skip (reason printed on stdout).

set -euo pipefail

PEL_FFMPEG_TREE="${FFMPEG_TREE:?FFMPEG_TREE must name the built FFmpeg tree}"
PEL_OUT="${OUTPUT_ROOT:?OUTPUT_ROOT is required}"

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

if [[ "$PEL_FFMPEG_TREE" != /* ]]; then
    pel_fail "FFMPEG_TREE must be an absolute path: $PEL_FFMPEG_TREE"
fi

if [[ ! -d "$PEL_FFMPEG_TREE" ]]; then
    pel_skip "FFMPEG_TREE directory does not exist: $PEL_FFMPEG_TREE"
fi

LIBAVCODEC=""
if [[ -f "$PEL_FFMPEG_TREE/libavcodec/libavcodec.a" ]]; then
    LIBAVCODEC="$PEL_FFMPEG_TREE/libavcodec/libavcodec.a"
elif [[ -f "$PEL_FFMPEG_TREE/lib/libavcodec.a" ]]; then
    LIBAVCODEC="$PEL_FFMPEG_TREE/lib/libavcodec.a"
else
    pel_skip "missing libavcodec.a in $PEL_FFMPEG_TREE"
fi

LIBAVUTIL=""
if [[ -f "$PEL_FFMPEG_TREE/libavutil/libavutil.a" ]]; then
    LIBAVUTIL="$PEL_FFMPEG_TREE/libavutil/libavutil.a"
elif [[ -f "$PEL_FFMPEG_TREE/lib/libavutil.a" ]]; then
    LIBAVUTIL="$PEL_FFMPEG_TREE/lib/libavutil.a"
else
    pel_skip "missing libavutil.a in $PEL_FFMPEG_TREE"
fi

if ! command -v nm >/dev/null 2>&1; then
    pel_skip "nm tool not available on host"
fi

if ! nm "$LIBAVCODEC" | grep -E " T ff_aom_apply_film_grain$" >/dev/null; then
    pel_skip "ff_aom_apply_film_grain symbol missing in $LIBAVCODEC"
fi

if ! nm "$LIBAVCODEC" | grep -E " T ff_h274_apply_film_grain$" >/dev/null; then
    pel_skip "ff_h274_apply_film_grain symbol missing in $LIBAVCODEC"
fi

# The static libraries need the external libraries FFmpeg was configured
# with (drm, VA, VPL, Vulkan, ...). configure records them per library in
# ffbuild/config.mak; read the two lines as data, never source the file.
PEL_CONFIG_MAK="$PEL_FFMPEG_TREE/ffbuild/config.mak"
if [[ ! -f "$PEL_CONFIG_MAK" ]]; then
    pel_skip "missing ffbuild/config.mak in $PEL_FFMPEG_TREE"
fi
PEL_EXTRALIBS=()
for pel_lib in avcodec avutil; do
    pel_grep_status=0
    pel_line="$(grep -E "^EXTRALIBS-$pel_lib=" "$PEL_CONFIG_MAK")" || pel_grep_status=$?
    if (( pel_grep_status != 0 )); then
        pel_fail "EXTRALIBS-$pel_lib not readable in $PEL_CONFIG_MAK (grep exit $pel_grep_status)"
    fi
    read -r -a pel_words <<< "${pel_line#*=}"
    PEL_EXTRALIBS+=("${pel_words[@]}")
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

mkdir -p "$PEL_OUT"
BIN="$PEL_OUT/grain_conformance"

PEL_CFLAGS=()
PEL_LDFLAGS=()

if [[ -n "${PELORUS_BUILD:-}" ]]; then
    if [[ ! -d "$PELORUS_BUILD" ]]; then
        pel_fail "PELORUS_BUILD directory does not exist: $PELORUS_BUILD"
    fi
    PEL_CFLAGS+=("-I$ROOT/libpelorus/include")
    PEL_LDFLAGS+=(
        "-L$PELORUS_BUILD/libpelorus"
        "-Wl,-rpath,$PELORUS_BUILD/libpelorus"
        "-lpelorus"
    )
elif pkg-config --exists libpelorus; then
    read -r -a PC_CFLAGS <<< "$(pkg-config --cflags libpelorus)"
    read -r -a PC_LIBS <<< "$(pkg-config --libs libpelorus)"
    PEL_CFLAGS+=("${PC_CFLAGS[@]}")
    PEL_LDFLAGS+=("${PC_LIBS[@]}")
else
    pel_fail "neither PELORUS_BUILD nor pkg-config libpelorus available"
fi

COMPILE_CMD=(
    "${CC:-cc}"
    -std=c11
    -O2
    -Wall
    -Wextra
    -Werror
    "-I$PEL_FFMPEG_TREE"
    "${PEL_CFLAGS[@]}"
    "$HERE/grain_conformance.c"
    "${PEL_LDFLAGS[@]}"
    "$LIBAVCODEC"
    "$LIBAVUTIL"
    "${PEL_EXTRALIBS[@]}"
    -lm
    -lpthread
    -o
    "$BIN"
)

if ! "${COMPILE_CMD[@]}" >"$PEL_OUT/compile.log" 2>&1; then
    cat "$PEL_OUT/compile.log" >&2
    pel_fail "compilation of grain_conformance.c failed"
fi

unplanted_status=0
"$BIN" >"$PEL_OUT/unplanted.log" 2>&1 || unplanted_status=$?
if (( unplanted_status != 0 )); then
    cat "$PEL_OUT/unplanted.log" >&2
    pel_fail "unplanted grain conformance failed (exit $unplanted_status)"
fi

PLANTS=(scale cutoff shift)
for plant in "${PLANTS[@]}"; do
    plant_status=0
    "$BIN" --plant "$plant" >"$PEL_OUT/plant-$plant.log" 2>&1 || plant_status=$?
    if (( plant_status != 1 )); then
        cat "$PEL_OUT/plant-$plant.log" >&2
        pel_fail "planted run --plant $plant expected exit 1, got exit $plant_status"
    fi
    if ! grep -q "\[FAIL\]" "$PEL_OUT/plant-$plant.log"; then
        cat "$PEL_OUT/plant-$plant.log" >&2
        pel_fail "planted run --plant $plant did not report [FAIL]"
    fi
done

echo "OK: grain conformance passed (clean and all plants verified)"
exit 0
