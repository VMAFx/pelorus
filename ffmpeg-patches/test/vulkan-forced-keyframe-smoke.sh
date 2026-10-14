#!/usr/bin/env bash
#
# vulkan-forced-keyframe-smoke.sh — Vulkan HEVC encode with -force_key_frames
# succeeds without intermittent hang (issue #309).
#
# Copyright 2026 Lusoris
# SPDX-License-Identifier: EUPL-1.2
#
# GPU test. With -force_key_frames 2 -c:v hevc_vulkan, unpatched FFmpeg (n9.0.2
# plus shared series 0001-0007) intermittently hangs at frame 61 (25 of 60 runs
# on the release/9.0 tip), killed by timeout (rc 124/137). The input frame count
# comes from a stream copy; a copy that fails or counts no frame fails the smoke. Upstream FFmpeg commit d0d7d81317 (series
# 0008 backport) resolves the hang.
#
# Input: a 1280x720 HEVC clip of 120 frames whose only keyframe is frame 0.
# Without INPUT the binary generates one (lavfi testsrc2 + smptehdbars, encoded
# with hevc_vulkan -g 1000 -bf 0); a clip made this way reproduced the hang with
# an unpatched build. A binary that cannot generate it skips with the reason.
#
# Skip reasons (stdout "SKIP: ..."): "hevc_vulkan encoder not found",
# "hevc decoder not found", "cannot generate the input clip", and
# "no Vulkan device with HEVC video" (no device, or one without HEVC decode or
# encode, e.g. lavapipe). Generating the clip fails, never skips, once the
# device probe passed.
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

# pel_why LOG — first error line of LOG, else its last line.
pel_why()
{
    local e
    e="$(pel_first_error "$1")"
    echo "${e:-$(tail -n 1 "$1")}"
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

timeout -k 5 30 "$PEL_FFMPEG" -hide_banner -filters >"$PEL_OUT/filters.txt" 2>&1 < /dev/null ||
    pel_fail "ffmpeg -filters failed"
timeout -k 5 30 "$PEL_FFMPEG" -hide_banner -demuxers >"$PEL_OUT/demuxers.txt" 2>&1 < /dev/null ||
    pel_fail "ffmpeg -demuxers failed"

# pel_has LIST NAME — NAME is a component in LIST. The name sits in column 2,
# or 3 after the demuxers' device flag "d", and may be a comma-separated list.
pel_has()
{
    awk -v n="$2" '{
        k = split(($2 == "d" && NF >= 3) ? $3 : $2, a, ",")
        for (j = 1; j <= k; j++) if (a[j] == n) found = 1
    } END { exit !found }' "$PEL_OUT/$1"
}

# pel_vk_probe LOG ARGS... — encode one frame with hevc_vulkan; skip when the
# host has no Vulkan device that decodes and encodes HEVC.
pel_vk_probe()
{
    local log="$1"
    shift
    if ! timeout -k 5 30 "$PEL_FFMPEG" -nostdin -hide_banner -loglevel verbose -y \
        -init_hw_device vulkan=vk:0 -filter_hw_device vk "$@" \
        -frames:v 1 -c:v hevc_vulkan -qp 28 "$PEL_OUT/probe_vk.mkv" \
        >"$log" 2>&1 < /dev/null; then
        pel_skip "no Vulkan device with HEVC video: $(pel_why "$log")"
    fi
}

if [[ -z "$PEL_INPUT" ]]; then
    # Allow-list of what the generation command uses: lavfi demuxer, the
    # wrapped_avframe decoder it outputs, and every filter in the graph.
    for need in demuxers:lavfi decoders:wrapped_avframe filters:testsrc2 \
        filters:smptehdbars filters:concat filters:format filters:hwupload; do
        pel_has "${need%%:*}.txt" "${need#*:}" ||
            pel_skip "cannot generate the input clip: no ${need#*:} in $PEL_FFMPEG -${need%%:*}; set INPUT"
    done
    pel_vk_probe "$PEL_OUT/probe_vk_enc.log" \
        -f lavfi -i testsrc2=s=1280x720:r=30:d=0.1 -vf format=nv12,hwupload
    PEL_INPUT="$PEL_OUT/input.mkv"
    if ! timeout -k 5 120 "$PEL_FFMPEG" -nostdin -hide_banner -y \
        -init_hw_device vulkan=vk:0 -filter_hw_device vk \
        -f lavfi -i testsrc2=s=1280x720:r=30:d=2 \
        -f lavfi -i smptehdbars=s=1280x720:r=30:d=2 \
        -filter_complex '[0:v][1:v]concat=n=2:v=1:a=0,format=nv12,hwupload[v]' \
        -map '[v]' -c:v hevc_vulkan -g 1000 -bf 0 -qp 20 \
        "$PEL_INPUT" >"$PEL_OUT/input.log" 2>&1 < /dev/null; then
        pel_fail "generating the input clip failed: $(pel_why "$PEL_OUT/input.log")"
    fi
fi
[[ -f "$PEL_INPUT" ]] || pel_fail "input clip does not exist: $PEL_INPUT"

pel_vk_probe "$PEL_OUT/probe_vk.log" \
    -hwaccel vulkan -hwaccel_device vk -hwaccel_output_format vulkan -i "$PEL_INPUT"

vk_dev="$(pel_device "$PEL_OUT/probe_vk.log")"
if [[ -n "$vk_dev" ]]; then
    echo "Vulkan device: $vk_dev"
fi

timeout -k 5 60 "$PEL_FFMPEG" -nostdin -hide_banner -y -i "$PEL_INPUT" -c copy -f matroska \
    "$PEL_OUT/probe_count.mkv" >"$PEL_OUT/probe_count.log" 2>&1 < /dev/null \
    || pel_fail "counting the input frames failed: $(tail -n 1 "$PEL_OUT/probe_count.log")"
rm -f "$PEL_OUT/probe_count.mkv"
PEL_EXPECTED_FRAMES="$(pel_last_frame "$PEL_OUT/probe_count.log")"
[[ "${PEL_EXPECTED_FRAMES:-0}" -gt 0 ]] \
    || pel_fail "counting the input frames found no frame: $PEL_OUT/probe_count.log"

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
        pel_fail "run $i/$RUNS failed (rc $rc): $(pel_why "$log")"
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
