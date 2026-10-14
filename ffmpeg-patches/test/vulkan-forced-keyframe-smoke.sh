#!/usr/bin/env bash
#
# vulkan-forced-keyframe-smoke.sh — Vulkan HEVC encode with -force_key_frames
# succeeds without intermittent hang (issue #309).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# GPU test. With -force_key_frames 2 -c:v hevc_vulkan, unpatched FFmpeg (n9.0.2
# plus shared series 0001-0007) intermittently hangs at frame 61 (~1 in 5 runs),
# killed by timeout (rc 124/137). Upstream FFmpeg commit d0d7d81317 (series
# 0008 backport) resolves the hang.
#
# Input: a 1280x720 HEVC clip of 120 frames whose only keyframe is frame 0.
# Without INPUT the binary generates one (lavfi testsrc2 + smptehdbars, encoded
# with hevc_vulkan -g 1000 -bf 0); a clip made this way reproduced the hang with
# an unpatched build. A binary that cannot generate it skips with the reason.
#
# Modes:
#   Normal:        runs RUNS times (default 60). Passes (exit 0) when all runs
#                  encode all frames with rc 0. Fails (exit 1) on any hang or
#                  unexpected error.
#   --self-test:   runs RUNS times on FFMPEG_BIN. Passes (exit 0) only when at
#                  least one run hangs (reproducing the bug). Fails (exit 1)
#                  when all runs finish clean ("no hang seen: binary carries
#                  the fix or RUNS too low").
#
# Env:
#   FFMPEG_BIN     patched or test ffmpeg binary (required)
#   INPUT          HEVC input clip, keyframe only at frame 0 (default: generated)
#   RUNS           iteration count (default 60, integer 1..1000)
#   OUTPUT_ROOT    evidence directory (default: new directory under TMPDIR)
#
# Exit: 0 pass, 1 fail, 77 skip (the reason is printed on stdout).
set -euo pipefail

SELF_TEST=0
if [[ "${1:-}" == "--self-test" ]]; then
    SELF_TEST=1
fi

PEL_FFMPEG="${FFMPEG_BIN:-}"
PEL_INPUT="${INPUT:-}"
RUNS="${RUNS:-60}"

pel_skip()
{
    echo "SKIP: $*"
    exit 77
}

pel_fail()
{
    echo "FAIL: $*" >&2
    if [[ -n "${PEL_OUT:-}" && -d "${PEL_OUT:-}" ]]; then
        echo "Evidence: $PEL_OUT" >&2
    fi
    exit 1
}

pel_first_error()
{
    awk 'tolower($0) ~ /error|failed/ { print; exit }' "$1"
}

pel_last_frame()
{
    sed -E -n 's/.*frame=[[:space:]]*([0-9]+).*/\1/p' "$1" | tail -n 1
}

pel_device()
{
    awk '/Device [0-9]+ selected: / { sub(/.*selected: /, ""); print; exit }' "$1"
}

[[ -n "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN must name the ffmpeg binary"
[[ -x "$PEL_FFMPEG" ]] || pel_fail "FFMPEG_BIN is not executable: $PEL_FFMPEG"

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || ((RUNS < 1 || RUNS > 1000)); then
    pel_fail "RUNS must be an integer between 1 and 1000: '$RUNS'"
fi

PEL_OUT="${OUTPUT_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/pelorus-vk-kf-smoke.XXXXXX")}"
mkdir -p "$PEL_OUT"

timeout -k 5 30 "$PEL_FFMPEG" -hide_banner -encoders >"$PEL_OUT/encoders.txt" 2>&1 < /dev/null ||
    pel_fail "ffmpeg -encoders failed"
timeout -k 5 30 "$PEL_FFMPEG" -hide_banner -decoders >"$PEL_OUT/decoders.txt" 2>&1 < /dev/null ||
    pel_fail "ffmpeg -decoders failed"

grep -Eq '^[[:space:]]*V[^[:space:]]*[[:space:]]+hevc_vulkan[[:space:]]' "$PEL_OUT/encoders.txt" ||
    pel_skip "hevc_vulkan encoder not found in $PEL_FFMPEG"

grep -Eq '^[[:space:]]*V[^[:space:]]*[[:space:]]+hevc[[:space:]]' "$PEL_OUT/decoders.txt" ||
    pel_skip "hevc decoder not found in $PEL_FFMPEG"

if [[ -z "$PEL_INPUT" ]]; then
    PEL_INPUT="$PEL_OUT/input.mkv"
    if ! timeout -k 5 120 "$PEL_FFMPEG" -nostdin -hide_banner -y \
        -init_hw_device vulkan=vk:0 -filter_hw_device vk \
        -f lavfi -i testsrc2=s=1280x720:r=30:d=2 \
        -f lavfi -i smptehdbars=s=1280x720:r=30:d=2 \
        -filter_complex '[0:v][1:v]concat=n=2:v=1:a=0,format=nv12,hwupload[v]' \
        -map '[v]' -c:v hevc_vulkan -g 1000 -bf 0 -qp 20 \
        "$PEL_INPUT" >"$PEL_OUT/input.log" 2>&1 < /dev/null; then
        pel_skip "cannot generate the input clip, set INPUT: $(pel_first_error "$PEL_OUT/input.log")"
    fi
fi
[[ -f "$PEL_INPUT" ]] || pel_fail "input clip does not exist: $PEL_INPUT"

if ! timeout -k 5 30 "$PEL_FFMPEG" -nostdin -hide_banner -loglevel verbose -y \
    -init_hw_device vulkan=vk:0 -filter_hw_device vk \
    -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan \
    -i "$PEL_INPUT" -frames:v 1 -c:v hevc_vulkan -qp 28 \
    "$PEL_OUT/probe_vk.mkv" >"$PEL_OUT/probe_vk.log" 2>&1 < /dev/null; then
    pel_skip "no usable Vulkan device: $(pel_first_error "$PEL_OUT/probe_vk.log")"
fi

vk_dev="$(pel_device "$PEL_OUT/probe_vk.log")"
if [[ -n "$vk_dev" ]]; then
    echo "Vulkan device: $vk_dev"
fi

PEL_EXPECTED_FRAMES=120
if timeout -k 5 60 "$PEL_FFMPEG" -nostdin -hide_banner -y -i "$PEL_INPUT" -c copy -f matroska \
    "$PEL_OUT/probe_count.mkv" >"$PEL_OUT/probe_count.log" 2>&1 < /dev/null; then
    detected_frames="$(pel_last_frame "$PEL_OUT/probe_count.log")"
    if [[ -n "$detected_frames" && "$detected_frames" -gt 0 ]]; then
        PEL_EXPECTED_FRAMES="$detected_frames"
    fi
    rm -f "$PEL_OUT/probe_count.mkv"
fi

out_mkv="$PEL_OUT/run.mkv"

for ((i = 1; i <= RUNS; i++)); do
    rm -f "$out_mkv"
    log="$PEL_OUT/run_${i}.log"
    rc=0

    timeout -k 5 30 "$PEL_FFMPEG" -hide_banner -nostdin -y \
        -init_hw_device vulkan=vk:0 -filter_hw_device vk \
        -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan \
        -i "$PEL_INPUT" -force_key_frames 2 -c:v hevc_vulkan -qp 28 \
        "$out_mkv" >"$log" 2>&1 < /dev/null || rc=$?

    fr="$(pel_last_frame "$log")"

    if (( rc == 124 || rc == 137 )); then
        if (( SELF_TEST == 1 )); then
            echo "PASS: self-test observed expected hang at run $i/$RUNS (rc $rc, last frame ${fr:-0})"
            exit 0
        else
            pel_fail "run $i/$RUNS hung (rc $rc, last frame ${fr:-0})"
        fi
    fi

    if (( rc != 0 )); then
        first_err="$(pel_first_error "$log")"
        pel_fail "run $i/$RUNS failed (rc $rc): ${first_err:-$(head -n 1 "$log")}"
    fi

    if [[ "${fr:-0}" != "$PEL_EXPECTED_FRAMES" ]]; then
        pel_fail "run $i/$RUNS encoded ${fr:-0} of $PEL_EXPECTED_FRAMES frames"
    fi
done

if (( SELF_TEST == 1 )); then
    echo "FAIL: no hang seen: binary carries the fix or RUNS too low ($RUNS runs completed cleanly)" >&2
    exit 1
fi

echo "OK: $RUNS/$RUNS runs completed successfully ($PEL_EXPECTED_FRAMES frames)"
exit 0
